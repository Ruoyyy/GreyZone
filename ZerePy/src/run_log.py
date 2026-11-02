"""
Structured per-run log for tool-driven agents.

One file per run: logs/<agent>-<run_id>.log, one event per line:
    <UTC timestamp> <event> <JSON payload>
e.g.
    2026-11-02T06:30:01.123Z tool_result {"cycle": 3, "name": "twitter_read_timeline", ...}
Parse a line with: ts, event, payload = line.split(" ", 2); json.loads(payload)
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, agent_name: str, log_dir: str = "logs"):
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.path = Path(log_dir) / f"{agent_name}-{self.run_id}.log"

        self._logger = logging.getLogger(f"runlog.{agent_name}.{self.run_id}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False  # keep events out of the console output
        handler = logging.FileHandler(self.path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s.%(msecs)03dZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

    def event(self, name: str, /, **fields) -> None:
        self._logger.info(f"{name} {json.dumps(fields, ensure_ascii=False, default=str)}")
