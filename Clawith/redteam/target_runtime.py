"""Read alice-visible target Run states and recognize backend failure messages."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

RETRYABLE_CONFLICTS = {"checkpoint_not_found", "inconsistent_checkpoint"}


class RunStateQueryError(RuntimeError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(json.dumps(detail))


def record_query(detail):
    if filename := os.environ.get("CLAWITH_EXPERIMENT_EVENTS"):
        path = Path(filename).with_name("state-queries.jsonl")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as stream:
            stream.write(json.dumps({"at": time.time(), **detail}) + "\n")

FAILURE = re.compile(
    r"\A\u4efb\u52a1(?:\u6267\u884c|\u89c4\u5212)\u672a\u5b8c\u6210\u3002\n\u9519\u8bef\uff1a[\s\S]*\n\u9519\u8bef\u7801\uff1a([A-Za-z0-9_.-]+)\nRun ID\uff1a([0-9a-fA-F-]{36})\s*\Z")


def failure_reply(reply):
    match = FAILURE.fullmatch(reply or "")
    if match:
        return {"code": match[1], "run_id": str(uuid.UUID(match[2]))}
    return None


def intake_run_ids(receipt):
    if receipt.get("error_code") or receipt.get("error"):
        raise RuntimeError(f"Target intake failed: {receipt.get('error_code') or receipt['error']}")
    ids = receipt.get("run_ids")
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("Target intake did not identify any dispatched Run")
    return list(dict.fromkeys(str(uuid.UUID(value)) for value in ids))


def read_states(lab, run_ids):
    """Use the regular alice account, with the same group/session scope as the turn."""
    base = os.environ.get("ORACLE_BASE_URL", "http://localhost:3008").rstrip("/") + "/api"

    def request(path, body=None, token=None, timeout=10):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=timeout) as response:
            return json.load(response)

    deadline = time.monotonic() + 25
    token = request("/auth/login", {"login_identifier": "alice@clawith.com", "password": "alice"})["access_token"]
    group, session = (str(uuid.UUID(lab[key])) for key in ("group_id", "session_id"))
    states = []
    for value in run_ids:
        run_id = str(uuid.UUID(value))
        path = f"/groups/{group}/sessions/{session}/runs/{run_id}"
        for attempt in range(1, 7):
            try:
                result = request(path, token=token, timeout=min(10, max(0.1, deadline-time.monotonic())))
                if attempt > 1:
                    record_query({"kind": "recovered", "run_id": run_id, "attempt": attempt})
                break
            except urllib.error.HTTPError as error:
                body = error.read(4096).decode(errors="replace")
                try:
                    detail = json.loads(body).get("detail", body)
                except (ValueError, AttributeError):
                    detail = body
                delay = min(4, 0.5 * 2 ** (attempt-1))
                retry = (error.code == 409 and isinstance(detail, str) and detail in RETRYABLE_CONFLICTS
                         and attempt < 6 and time.monotonic() + delay < deadline)
                evidence = {"kind": "http_error", "run_id": run_id, "http_status": error.code,
                            "detail": detail, "attempt": attempt, "retry": retry}
                record_query(evidence)
                if not retry:
                    raise RunStateQueryError(evidence) from error
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError) as error:
                evidence = {"kind": "transport_error", "run_id": run_id,
                            "detail": str(error), "attempt": attempt}
                record_query(evidence)
                raise RunStateQueryError(evidence) from error
        if result.get("run_id") != run_id or not isinstance(result.get("status"), str):
            raise RuntimeError("Target Run state identity is missing or mismatched")
        states.append({"run_id": run_id, "status": result["status"],
                       **{key: result[key] for key in ("source_message_id", "reply_message_ids", "delivery_status") if key in result}})
    return states


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    try:
        states = read_states(payload["lab"], intake_run_ids(payload["intake"]))
        print(json.dumps(states))
    except Exception as error:
        detail = getattr(error, "detail", {"detail": str(error), "type": type(error).__name__})
        print(json.dumps({"error": detail}))
        raise SystemExit(1)
