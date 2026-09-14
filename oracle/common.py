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


def backend_arg(parser):
    default_root = Path(os.environ.get("LETTA_LOCAL_BACKEND_DIR", "/home/runner/letta-state"))
    parser.add_argument("backend", nargs="?", type=Path, default=default_root,
                        help="letta backend dir (contains conversations/, memfs/, .decoys/)")
    parser.add_argument("--once", action="store_true", help="One scan cycle and exit")
