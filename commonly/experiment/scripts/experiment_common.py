"""Private metadata, source fingerprints and the existing Commonly campaign lock."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redteam.common import configure, run_lock, scenario_data, write_json, reset_budget, reset_time_remaining
configure()

EXPERIMENT = ROOT / "experiment"
RESULTS = EXPERIMENT / "results"
BASELINES = EXPERIMENT / "baselines"
ORACLE = ROOT / ".run/oracle"
PYTHON = ROOT / "redteam/.venv/bin/python"
FRAMEWORKS = ("promptfoo", "deepteam", "pyrit", "dreadnode")
SCENARIOS = ("private-pod-leak", "task-exfil", "forged-invite", "forged-kick", "forged-dissolve", "forged-delete")
DEFAULTS = {"run_timeout": 14400, "reset_timeout": 1200, "oracle_wait": 60,
            "collection_timeout": 180, "max_steps": 40}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text())


def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("Expected a simple name without path separators")
    return value


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Expected a finite positive number")
    return number


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def configuration(target_only=False):
    directories = ["backend", "commonly-mcp", "docker", "oracle", "benchmark"]
    if not target_only:
        directories += ["redteam", "experiment/scripts", "experiment/tests"]
    excluded = {"node_modules", ".venv", "__pycache__", ".pytest_cache", "dist", "build", ".git"}
    suffixes = {".py", ".ts", ".js", ".mjs", ".json", ".md", ".sh", ".yaml", ".yml", ".toml", ".txt", ".lock"}
    paths = {ROOT / "docker-compose.stack.yml", ROOT / "experiment/run_all.sh",
             ROOT.parent / "scripts/oracle_result.py"}
    if target_only:
        paths.discard(ROOT / "experiment/run_all.sh")
    for directory in directories:
        for parent, children, files in os.walk(ROOT / directory):
            children[:] = [c for c in children if c not in excluded]
            paths.update(Path(parent) / name for name in files
                         if Path(name).suffix in suffixes or name == "Dockerfile")
    values = {os.path.relpath(p, ROOT): digest(p) for p in sorted(paths) if p.is_file()}
    if (ROOT / ".env").is_file():
        values[".env"] = digest(ROOT / ".env")
    operational = {"COMMONLY_CAMPAIGN_LOCK_FD", "COMMONLY_EXPERIMENT_RUN_ID", "COMMONLY_STATE_FILE",
                   "COMMONLY_USAGE_FILE", "COMMONLY_BASELINE_RECEIPT", "COMMONLY_RUN_DIR",
                   "COMMONLY_RUN_TIMEOUT", "COMMONLY_RESET_DEADLINE", "GREYZONE_RUN_ID", "GREYZONE_REVISION", "GREYZONE_ATTACKER_MODEL"}
    for key, value in sorted(os.environ.items()):
        if key.startswith(("OPENAI_", "COMMONLY_", "ORACLE_", "SEED_", "EPISODE_")) and key not in operational:
            values["env/" + key] = hashlib.sha256(value.encode()).hexdigest()
    return values


def child_env(descriptor, **values):
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **{k: str(v) for k, v in values.items()}}
    if descriptor is not None:
        env["COMMONLY_CAMPAIGN_LOCK_FD"] = str(descriptor)
    return env


def interpreter(framework):
    return ROOT / "redteam" / framework / ".venv/bin/python" if framework in {"pyrit", "deepteam"} else PYTHON


def options(args, saved=None):
    values = dict(saved or DEFAULTS)
    for key in DEFAULTS:
        requested = getattr(args, key, None)
        if requested is not None:
            positive(requested)
            if saved is not None and requested != saved[key]:
                raise ValueError(f"Resume cannot change {key}")
            values[key] = requested
        setattr(args, key, values[key])
    if not 1 <= values["oracle_wait"] <= 1800 or not 1 <= values["max_steps"] <= 1000:
        raise ValueError("oracle-wait must be 1..1800; max-steps must be 1..1000")
    if values["collection_timeout"] <= values["oracle_wait"]:
        raise ValueError("collection-timeout must exceed oracle-wait")
    return values
