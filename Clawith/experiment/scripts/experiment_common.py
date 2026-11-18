"""Experiment paths, private metadata and reproducibility fingerprints."""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiment/results"
SCENARIOS = ROOT / "benchmark/scenarios"
FRAMEWORKS = ("promptfoo", "deepteam", "pyrit", "dreadnode")
PYTHON = sys.executable
sys.path.insert(0, str(ROOT))
from redteam.campaign import campaign_lock
from redteam.dreadnode.run import env_value, scenario_data


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("Expected a simple name without path separators")
    return value


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def configuration(target_only=False):
    paths = set()
    directories = ["backend", "redteam-mcp", "oracle", "benchmark"]
    if not target_only:
        directories += ["redteam", "experiment/scripts"]
    for directory in directories:
        for parent, children, files in os.walk(ROOT / directory):
            children[:] = [c for c in children if c not in
                           {"node_modules", ".venv", "__pycache__", "agent_data", ".pytest_cache"}]
            paths.update(p for name in files if (p := Path(parent) / name).name == "Dockerfile"
                         or p.suffix in {".py", ".mjs", ".json", ".md", ".sh", ".yml", ".yaml", ".toml", ".txt", ".lock"})
    paths.update(ROOT / name for name in ("docker-compose.yml", "start.sh", "ss-nodes.json"))
    paths.add(ROOT.parent / "scripts/oracle_result.py")
    hashes = {str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else "../scripts/oracle_result.py"):
              hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}
    # Bootstrap rewrites these identities on every hard reset. All other dotenv
    # settings, including credentials, are fingerprinted without exporting values.
    runtime = {"ALICE_USER_ID", "ALICE_TENANT_ID"}
    lines = [line for line in (ROOT / ".env").read_text().splitlines()
             if line.partition("=")[0].strip() not in runtime
             and not (target_only and line.startswith("DREADNODE_"))]
    hashes[".env"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    prefixes = ("OPENAI_", "DREADNODE_", "CLAWITH_", "MCP_", "ORACLE_", "SANDBOX_", "AGENT_")
    operational = {"CLAWITH_CAMPAIGN_LOCK_FD", "CLAWITH_SKIP_BUILD", "CLAWITH_PREPARED_BASELINE",
                   "CLAWITH_EXPERIMENT_EVENTS", "CLAWITH_EXPERIMENT_USAGE", "CLAWITH_REPLY_TIMEOUT",
                   "CLAWITH_SETTLE_TIMEOUT", "CLAWITH_ORACLE_TIMEOUT", "CLAWITH_MAX_TURNS"}
    for key, value in os.environ.items():
        if (key.startswith(prefixes) and key not in runtime | operational
                and not (target_only and (key.startswith("DREADNODE_") or key == "CLAWITH_PYTHON"))):
            hashes["shell/" + key] = hashlib.sha256(value.encode()).hexdigest()
    return hashes


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
