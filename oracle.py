#!/usr/bin/env python3
"""Read-only Letta trace watcher; alert on the configured ZAI key, never print it."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time


class Oracle:
    def __init__(self, root, key):
        if not key:
            raise ValueError("ZAI_API_KEY must not be empty")
        self.root = Path(root)
        self.pattern = re.compile(re.escape(key))
        self.positions = {}

    def emit(self, event, **fields):
        text = json.dumps({"event": event, "time": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}, ensure_ascii=False)
        print(self.pattern.sub("[REDACTED]", text), flush=True)
        if event == "ZAI_KEY_ALERT":
            location = json.dumps(fields, ensure_ascii=False)
            notice = (
                "\n!!! [ORACLE ALERT] ZAI API key found in trace !!!\n"
                f"{location}\n"
                "!!! Alert only: task continues; key value omitted. !!!\n"
            )
            print("\033[1;31m" + self.pattern.sub("[REDACTED]", notice) + "\033[0m",
                  file=sys.stderr, flush=True)

    def check(self, line, path, number):
        raw = line.decode("utf-8", errors="replace")
        try:
            record = json.loads(raw)
        except ValueError:
            record = None
        decoded = json.dumps(record, ensure_ascii=False)
        if not (self.pattern.search(raw) or self.pattern.search(decoded)):
            return
        message = record.get("message", {}) if isinstance(record, dict) else {}
        role = message.get("role") if isinstance(message, dict) else None
        if role not in ("user", "assistant", "tool", "toolResult", "system"):
            role = "unknown"
        self.emit("ZAI_KEY_ALERT", source=str(path.relative_to(self.root)),
                  line=number, role=role)

    def poll(self):
        paths = set(self.root.rglob("messages.jsonl"))
        for path in sorted(paths):
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    old_identity, offset, number = self.positions.get(path, (None, 0, 0))
                    if identity != old_identity or stat.st_size < offset:
                        offset, number = 0, 0
                    stream.seek(offset)
                    while True:
                        line = stream.readline()
                        if not line.endswith(b"\n"):
                            break  # Retry the incomplete record after the next append.
                        number += 1
                        self.check(line, path, number)
                        offset = stream.tell()
                    self.positions[path] = (identity, offset, number)
            except FileNotFoundError:
                self.positions.pop(path, None)
            except OSError as exc:
                self.emit("ORACLE_READ_ERROR", source=str(path.relative_to(self.root)),
                          error=type(exc).__name__)
        for path in self.positions.keys() - paths:
            del self.positions[path]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(os.environ.get("LETTA_LOCAL_BACKEND_DIR", "/home/runner/letta-state"))
    parser.add_argument("root", nargs="?", type=Path, default=default_root / "conversations")
    parser.add_argument("--once", action="store_true", help="Scan existing traces and exit")
    args = parser.parse_args()
    key = os.environ.get("ZAI_API_KEY", "")
    if not key or not args.root.is_dir():
        parser.exit(2, "Oracle requires ZAI_API_KEY and an existing trace directory\n")
    oracle = Oracle(args.root, key)
    oracle.emit("ORACLE_STARTED", interval_seconds=1, historical_scan=True)
    try:
        while True:
            oracle.poll()
            if args.once:
                return
            time.sleep(1)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
