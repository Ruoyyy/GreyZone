"""Experiment-only SDK usage journal; no prompts, credentials or target calls."""
import json
import os
import uuid
from functools import wraps


def install_usage_hook():
    filename = os.environ.get("COMMONLY_USAGE_FILE")
    if not filename:
        return
    from openai._base_client import AsyncAPIClient, SyncAPIClient
    if all(getattr(cls.request, "_commonly_usage", False) for cls in (AsyncAPIClient, SyncAPIClient)):
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
        if getattr(original, "_commonly_usage", False):
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
        cls.request._commonly_usage = True


def summarize(directory, framework):
    """Keep SDK/native accounting separate from target costs; unknown stays null."""
    import re
    keys = ("input_tokens", "output_tokens", "total_tokens")
    records = []
    source = framework + ".native"
    if framework in {"deepteam", "pyrit"}:
        file = directory / "usage.jsonl"
        rows = [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []
        requests = {row["request_id"]: {} for row in rows if row["kind"] == "request_start"}
        requests.update({row["request_id"]: row for row in rows if row["kind"] == "response"})
        if not requests and any(r["kind"] == "usage_start" for r in rows):
            return {"status": "reported", "source": source, "calls": 0, **dict.fromkeys(keys, 0)}
        records = list(requests.values())
    elif framework == "promptfoo":
        file = directory / "promptfoo-results.json"
        usage = json.loads(file.read_text()).get("results", {}).get("stats", {}).get("tokenUsage", {}) if file.exists() else {}
        for bucket in ("attacker", "assertions", "generation"):
            if bucket in usage:
                item = usage[bucket]
                records.append(dict(zip(keys, (item.get("prompt"), item.get("completion"), item.get("total")))))
        if "generation" not in usage:
            text = (directory / "promptfoo.log").read_text(errors="replace")
            for total, inputs, outputs in re.findall(
                    r"Observed generation token usage:\s*([\d,]+) total \(([\d,]+) input, ([\d,]+) output\)", text):
                records.append(dict(zip(keys, (int(v.replace(",", "")) for v in (inputs, outputs, total)))))
            if not re.search(r"Observed generation token usage:", text):
                records.append({})
    else:
        text = (directory / "dreadnode.log").read_text(errors="replace")
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        matches = re.findall(r"(?:^|[\s·])([\d,.]+)([kKmM]?) tokens\b", text)
        if matches:
            value, suffix = matches[-1]
            return {"status": "approximate" if suffix else "reported-total-only", "source": source,
                    "total_tokens": round(float(value.replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[suffix.lower()]),
                    "input_tokens": None, "output_tokens": None}
    if not records:
        return {"status": "unavailable", "source": source, **dict.fromkeys(keys, None)}
    result = {key: sum(r[key] for r in records if type(r.get(key)) in {int, float})
              if any(type(r.get(key)) in {int, float} for r in records) else None for key in keys}
    return {"status": "reported" if all(all(type(r.get(k)) in {int, float} for k in keys) for r in records) else "partial",
            "source": source, "records": len(records), **result}
