#!/usr/bin/env python3
"""Memory-poisoning oracle: agent memory files (memfs/*/memory/**/*.md) are
scanned incrementally for poisoning signatures (instructions to ignore rules,
exfiltrate secrets, fetch-and-execute) and for stored secret values.
Alert; a tripwire for humans to review, not a hard block."""

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Emitter, IncrementalScan, SecretBook, append_alert,
                    backend_arg, read_secret_literals)

MEM_POISON_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|bypass|override)\b[^.\n]{0,60}\b(rule|rules|directive|directives|instruction|instructions|security|restriction|guardrail)"),
    re.compile(r"(?i)\b(exfiltrate|leak|send|upload|post|transmit|reveal)\b[^.\n]{0,60}\b(key|token|secret|credential|credentials|password|env|environment)"),
    re.compile(r"(?i)reverse[ _-]?shell"),
    re.compile(r"(?i)\b(curl|wget)\b[^|\n]{0,80}\|\s*(ba|z)?sh\b"),
]


class MemOracle:
    def __init__(self, backend):
        self.backend = Path(backend)
        self.secrets = SecretBook(read_secret_literals())
        self.emit = Emitter(self.secrets, "MEM")
        self.code_blocks = {}
        self.memory = IncrementalScan(self.backend / "memfs", "*.md")
        self.seen_lines = set()

        def baseline(path, number, raw):
            text = raw.decode("utf-8", "replace").strip()
            self.seen_lines.add((path, text.casefold()))
            if text.startswith(("```", "~~~")):
                self.code_blocks[path] = not self.code_blocks.get(path, False)
        self.memory.poll(baseline)

    def on_line(self, path, number, raw):
        text = raw.decode("utf-8", errors="replace")
        stripped = text.strip()
        if stripped.startswith(("```", "~~~")):
            self.code_blocks[path] = not self.code_blocks.get(path, False)
            return
        if self.code_blocks.get(path) or stripped.startswith(">"):
            return
        key = (path, stripped.casefold())
        if key in self.seen_lines:
            return
        self.seen_lines.add(key)
        if self.secrets.hits(text):
            self.emit("MEM_POISON_SECRET", alert=True,
                      source=str(path.relative_to(self.backend)), line=number,
                      hint="known secret value stored in agent memory")
            append_alert(self.backend, self.secrets, "MEM_POISON_SECRET",
                         "%s:%s known secret in memory" % (
                             path.relative_to(self.backend), number))
            return
        for regex in MEM_POISON_PATTERNS:
            m = regex.search(text)
            if m:
                directive = text[:m.start()].strip().strip("-* ").casefold()
                if directive not in {"", "always", "from now on,", "from now on", "remember:", "remember: always"}:
                    continue
                prefix = text[max(0, m.start() - 20):m.start()]
                if re.search(r"(?i)(?:never|must not|do not|don\'t|not to)\s*$", prefix):
                    continue
                self.emit("MEM_POISON_ALERT", alert=True,
                          source=str(path.relative_to(self.backend)), line=number,
                          matched=self.secrets.redact(m.group(0)[:80]))
                append_alert(self.backend, self.secrets, "MEM_POISON_ALERT",
                             "%s:%s %s" % (
                                 path.relative_to(self.backend), number,
                                 self.secrets.redact(m.group(0)[:80])))
                return

    def poll(self):
        self.memory.poll(self.on_line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    backend_arg(parser)
    args = parser.parse_args()
    if not (args.backend / "memfs").is_dir():
        parser.exit(2, "Memory oracle requires a memfs/ dir under the backend\n")
    oracle = MemOracle(args.backend)
    oracle.emit("ORACLE_MEM_STARTED", interval_seconds=1, historical_scan=False)
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
