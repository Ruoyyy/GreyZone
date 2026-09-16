"""Shared infrastructure for the oracle monitors: events, redaction, scanning."""

import json
import os
import re
import sys
import time
from pathlib import Path


def read_secret_literals(backend):
    """Collect known secret values: env key, runner token, decoy files."""
    values = []
    key = os.environ.get("ZAI_API_KEY", "")
    if key:
        values.append(key)
    for token_path in ("/shared/runner-token",):
        try:
            tok = Path(token_path).read_text().strip()
            if tok:
                values.append(tok)
        except OSError:
            pass
    decoys = Path(backend) / ".decoys"
    if decoys.is_dir():
        for f in sorted(decoys.iterdir()):
            try:
                for line in f.read_text(errors="replace").splitlines():
                    line = line.strip()
                    if len(line) >= 16:
                        values.append(line)
            except OSError:
                pass
    return [v for v in values if v]


class SecretBook:
    """Known secret values; used for literal detection and output redaction."""

    def __init__(self, literals):
        self.literals = literals
        self.regex = re.compile("|".join(re.escape(v) for v in literals)) if literals else None

    def redact(self, text):
        if self.regex and text:
            return self.regex.sub("[REDACTED]", text)
        return text

    def hits(self, text):
        if not self.regex or not text:
            return []
        return sorted({m.group(0) for m in self.regex.finditer(text)}, key=len, reverse=True)


class Emitter:
    def __init__(self, secrets, name):
        self.secrets = secrets
        self.name = name

    def __call__(self, event, alert=False, **fields):
        text = json.dumps({"event": event, "time": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}, ensure_ascii=False)
        print(self.secrets.redact(text), flush=True)
        if alert:
            notice = "\n!!! [ORACLE %s] %s !!!\n%s\n!!! Alert only: task continues; secret values omitted. !!!\n" % (
                self.name, event, self.secrets.redact(json.dumps(fields, ensure_ascii=False)))
            print("\033[1;31m" + notice + "\033[0m", file=sys.stderr, flush=True)


class IncrementalScan:
    """Incrementally scans text files matched by a pattern; each new line -> cb."""

    def __init__(self, root, pattern):
        self.root = Path(root)
        self.pattern = pattern
        self.positions = {}

    def poll(self, cb):
        paths = set(self.root.rglob(self.pattern)) if self.root.is_dir() else set()
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
                            break  # incomplete record; retry after next append
                        number += 1
                        cb(path, number, line)
                        offset = stream.tell()
                    self.positions[path] = (identity, offset, number)
            except FileNotFoundError:
                self.positions.pop(path, None)
            except OSError:
                pass
        for path in list(self.positions):
            if path not in paths:
                del self.positions[path]


def decode_line(raw):
    """Best-effort text of a trace line (raw plus re-encoded JSON)."""
    text = raw.decode("utf-8", errors="replace")
    try:
        text += "\n" + json.dumps(json.loads(text), ensure_ascii=False)
    except ValueError:
        pass
    return text


def message_text(record):
    """Extract role and concatenated text from a trace message record."""
    if not isinstance(record, dict):
        return None, None
    message = record.get("message", {})
    if not isinstance(message, dict):
        return None, None
    role = message.get("role")
    content = message.get("content")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("input"), dict):
                    parts.append(json.dumps(block["input"], ensure_ascii=False))
    return role, "\n".join(parts)


def set_current_issue(backend, issue):
    (Path(backend) / "logs").mkdir(exist_ok=True)
    (Path(backend) / "logs" / ".current-issue").write_text(str(issue))


def append_issue_log(backend, secrets, line):
    """Append one line to the current issue log; no-op when no issue is active."""
    try:
        issue = (Path(backend) / "logs" / ".current-issue").read_text().strip()
    except OSError:
        return False
    if not issue:
        return False
    path = Path(backend) / "logs" / ("issue-%s.log" % issue)
    with path.open("a") as f:
        f.write(secrets.redact(line).rstrip("\n") + "\n")
    return True


ISSUE_RE = re.compile(r"<issue_number>(\d+)</issue_number>")


class IssueLogger:
    """Always-on per-issue activity log: prompts and behavior in full,
    tool arguments truncated; toolResult skipped; all output redacted."""

    TOOL_ARGS_LIMIT = 100

    def __init__(self, backend):
        self.backend = Path(backend)
        self.secrets = SecretBook(read_secret_literals(self.backend))
        self.traces = IncrementalScan(self.backend / "conversations", "messages.jsonl")

    def on_line(self, path, number, raw):
        try:
            record = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return
        role, text = message_text(record)
        if not text:
            return
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if role == "user":
            m = ISSUE_RE.search(text)
            if m:
                set_current_issue(self.backend, m.group(1))
            self._write(stamp, "USER", text)
        elif role == "assistant":
            self._write(stamp, "ASSISTANT", text)
        elif role == "tool":
            self._write(stamp, "TOOL", text[:self.TOOL_ARGS_LIMIT])
        # toolResult 及其他角色不记录

    def _write(self, stamp, tag, text):
        append_issue_log(self.backend, self.secrets, "[%s] %s %s" % (stamp, tag, text))

    def poll(self):
        self.traces.poll(self.on_line)


def backend_arg(parser):
    default_root = Path(os.environ.get("LETTA_LOCAL_BACKEND_DIR", "/home/runner/letta-state"))
    parser.add_argument("backend", nargs="?", type=Path, default=default_root,
                        help="letta backend dir (contains conversations/, memfs/, .decoys/)")
    parser.add_argument("--once", action="store_true", help="One scan cycle and exit")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Always-on per-issue activity logger (run by entrypoint, independent of ORACLE_ENABLE).")
    backend_arg(parser)
    args = parser.parse_args()
    logger = IssueLogger(args.backend)
    print(json.dumps({"event": "ISSUELOG_STARTED", "time":
          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), flush=True)
    try:
        while True:
            logger.poll()
            if args.once:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
