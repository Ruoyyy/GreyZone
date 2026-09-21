"""Local evidence, including partial runs. No claim of Oracle verification."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Report:
    def __init__(self, root: Path):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        self.path = root / self.run_id
        self.path.mkdir(parents=True, mode=0o700)
        self.secrets = sorted({v for k, v in os.environ.items()
            if len(v) >= 8 and any(x in k.upper() for x in ("KEY", "TOKEN", "PASSWORD", "SECRET"))},
            key=len, reverse=True)

    def _json(self, value):
        text = json.dumps(value, ensure_ascii=False, default=str)
        # Replace the JSON-escaped value too, so quoted/backslash-containing keys
        # do not bypass report redaction.
        for secret in self.secrets:
            text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        return text

    def write(self, name, value):
        target = self.path / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(self._json(value) + "\n")
        temporary.chmod(0o600)
        temporary.replace(target)

    def event(self, case_id, value):
        target = self.path / "events.jsonl"
        with target.open("a") as stream:
            stream.write(self._json({"time": now(), "case_id": case_id, **value}) + "\n")
        target.chmod(0o600)
