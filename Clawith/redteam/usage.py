"""Experiment-only SDK usage journal; no prompts, credentials or target calls."""
import json
import os
import uuid
from functools import wraps


def install_usage_hook():
    filename = os.environ.get("CLAWITH_EXPERIMENT_USAGE")
    if not filename:
        return
    from openai._base_client import AsyncAPIClient, SyncAPIClient
    if all(getattr(cls.request, "_clawith_usage", False) for cls in (AsyncAPIClient, SyncAPIClient)):
        return

    def write(row):
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            stream.write(json.dumps(row) + "\n")

    write({"kind": "usage_start", "source": "openai.request"})

    def record(request_id, response=None, error=False):
        usage = getattr(response, "usage", None)
        data = usage.model_dump() if hasattr(usage, "model_dump") else usage or {}
        inputs = data.get("prompt_tokens", data.get("input_tokens"))
        outputs = data.get("completion_tokens", data.get("output_tokens"))
        total = data.get("total_tokens")
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs
        row = {"kind": "response", "request_id": request_id, "id": getattr(response, "id", None),
               "model": getattr(response, "model", None), "input_tokens": inputs,
               "output_tokens": outputs, "total_tokens": total, "request_error": error}
        write(row)

    for cls in (AsyncAPIClient, SyncAPIClient):
        original = cls.request
        if getattr(original, "_clawith_usage", False):
            continue
        if cls is AsyncAPIClient:
            def wrap(original):
                @wraps(original)
                async def capture(self, *args, **kwargs):
                    request_id = str(uuid.uuid4())
                    write({"kind": "request_start", "request_id": request_id})
                    try:
                        response = await original(self, *args, **kwargs)
                    except Exception:
                        record(request_id, error=True)
                        raise
                    record(request_id, response)
                    return response
                return capture
        else:
            def wrap(original):
                @wraps(original)
                def capture(self, *args, **kwargs):
                    request_id = str(uuid.uuid4())
                    write({"kind": "request_start", "request_id": request_id})
                    try:
                        response = original(self, *args, **kwargs)
                    except Exception:
                        record(request_id, error=True)
                        raise
                    record(request_id, response)
                    return response
                return capture
        cls.request = wrap(original)
        cls.request._clawith_usage = True
