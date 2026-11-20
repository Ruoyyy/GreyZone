"""Configuration and artifact helpers shared by all four attack frameworks."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shlex
import time
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]


def reset_time_remaining(default=180):
    """Use one monotonic deadline across all reset/capture subprocesses."""
    value = os.environ.get("COMMONLY_RESET_DEADLINE")
    if value is None:
        return default
    deadline = float(value)
    if not math.isfinite(deadline):
        raise ValueError("Invalid Commonly reset deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Commonly reset/capture deadline expired")
    return remaining


@contextmanager
def reset_budget(seconds=1200):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Reset timeout must be finite and positive")
    inherited = "COMMONLY_RESET_DEADLINE" in os.environ
    if not inherited:
        os.environ["COMMONLY_RESET_DEADLINE"] = str(time.monotonic() + seconds)
    try:
        reset_time_remaining()
        yield
    finally:
        if not inherited:
            os.environ.pop("COMMONLY_RESET_DEADLINE", None)


def configure() -> None:
    """Shell settings take precedence over the local stack's environment file."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                parsed = shlex.split(value, comments=True)
                os.environ.setdefault(key.strip(), " ".join(parsed))
    os.environ.setdefault("XDG_DATA_HOME", str(ROOT / ".run/sdk-data"))
    os.environ.setdefault("ORACLE_BASE_URL", f"http://localhost:{os.environ.get('COMMONLY_BACKEND_PORT', '5000')}/api")


def scenario_data(identifier: str) -> tuple[dict, str]:
    from oracle.judge import ORACLE_SURFACES
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
        raise ValueError("invalid scenario id")
    directory = ROOT / "benchmark/scenarios" / identifier
    data = json.loads((directory / "scenario.json").read_text())
    turns = data.get("maxTurns")
    if type(turns) is not int or not 1 <= turns <= 20:
        raise ValueError("Commonly scenario maxTurns must be between 1 and 20")
    if (data.get("schemaVersion") != 1 or data.get("id") != identifier
            or data.get("environment") != "commonly" or data.get("attacker") != "alice"
            or data.get("attackChannel") != "shared_pod_message"
            or not isinstance(data.get("oracleSuccessSurfaces"), list)
            or len(data["oracleSuccessSurfaces"]) != 1
            or data["oracleSuccessSurfaces"][0] not in ORACLE_SURFACES
            or not data.get("policy", "").strip() or not data.get("attackerTurn", "").strip()
            or not re.fullmatch(r"[a-z0-9-]+\.md", data.get("objectiveFile", ""))):
        raise ValueError(f"invalid Commonly scenario: {identifier}")
    objective = (directory / data["objectiveFile"]).read_text().strip()
    if not objective:
        raise ValueError("scenario objective is empty")
    data["_dir"] = str(directory)
    return data, objective


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def attacker_settings(model: str) -> tuple[str, str, str]:
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or not model.strip() or not endpoint.startswith("https://"):
        raise ValueError("configure OPENAI_API_KEY, OPENAI_MODEL and an HTTPS OPENAI_BASE_URL")
    return endpoint, key, model


@contextmanager
def run_lock():
    """Serialize candidates and resets; retain the existing lock file and FD protocol."""
    path = ROOT / ".run/campaign.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.get("COMMONLY_CAMPAIGN_LOCK_FD")
    if inherited is not None:
        descriptor = int(inherited)
        if os.fstat(descriptor).st_ino != path.stat().st_ino:
            raise RuntimeError("Invalid inherited campaign lock")
        yield descriptor
        return
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Commonly run or reset is in progress") from None
        yield stream.fileno()
