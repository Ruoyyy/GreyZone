"""Small shared helpers for local experiment scripts."""
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiment"
RESULTS = EXPERIMENT / "results"
FRAMEWORKS = ("promptfoo", "deepteam", "pyrit", "dreadnode")
SCENARIOS = ROOT / "benchmark/scenarios"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text())


def framework_completed(path, run):
    """Promptfoo uses exit 100 for completed evaluations with failed assertions."""
    code = run.get("returncode")
    if code == 0:
        return True
    if run.get("framework") != "promptfoo" or code != 100:
        return False
    try:
        report = read_json(path / "framework" / f"{run['scenario']}.json")["results"]
        stats = report["stats"]
        return bool(report["results"]) and stats["errors"] == 0 and stats["failures"] > 0
    except (OSError, ValueError, KeyError, TypeError):
        return False


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def configuration_hashes():
    paths = [ROOT / ".env", ROOT / "docker-compose.yml", ROOT / "config.yaml"]
    paths += list((ROOT / "shared").rglob("*"))
    for framework in FRAMEWORKS:
        directory = ROOT / "redteam" / framework
        paths += [directory / name for name in (
            ".env", "config.yaml", "promptfooconfig.yaml", "capability.yaml",
            "instructions.md", "package-lock.json", "uv.lock")]
    paths += list(SCENARIOS.glob("*/*"))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths)) if p.is_file()}


def compose(*args, env=None, **kwargs):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, env=env,
                          check=True, **kwargs)


@contextlib.contextmanager
def campaign_lock():
    path = ROOT / "redteam/.campaign.lock"
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another campaign is active in this checkout") from None
        yield stream.fileno()


def safe_name(value):
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in value):
        raise ValueError("Names may contain only letters, digits, hyphens, and underscores")
    return value
