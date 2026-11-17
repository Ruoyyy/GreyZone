"""Shared paths, private files and immutable batch configuration."""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiment"
RESULTS = EXPERIMENT / "results"
SCENARIOS = ROOT / "benchmark/scenarios"
PYTHON = ROOT / ".venv/bin/python"
FRAMEWORKS = ("promptfoo", "deepteam", "pyrit", "dreadnode")
sys.path.insert(0, str(ROOT))
from redteam.experiment_support import campaign_lock


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


def settings():
    code = "import json;from oracle.shop import load_env;print(json.dumps(load_env()))"
    result = subprocess.run([str(PYTHON), "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    return {**json.loads(result.stdout), **os.environ}


def configuration():
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    paths = {ROOT / p for p in tracked if Path(p).suffix in (".py", ".sh", ".ts", ".tsx", ".json", ".yaml", ".toml", ".txt", ".lock")
             and not p.startswith(("docs/", "plugins/"))}
    paths.update((EXPERIMENT / "scripts").glob("*"))
    paths.add(ROOT / "redteam/experiment_support.py")
    paths.add(ROOT / "docker/ports.sh")
    for directory in (ROOT, ROOT / "merchant", ROOT / "storefront", *(ROOT / "redteam" / fw for fw in FRAMEWORKS)):
        paths.add(directory / ".env")
    paths.update((ROOT / "docker/snapshot").iterdir())
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}
    shared = ROOT.parent / "scripts/oracle_result.py"
    hashes["../scripts/oracle_result.py"] = hashlib.sha256(shared.read_bytes()).hexdigest()
    prefixes = ("ANTHROPIC_", "OPENAI_", "SHOPWARE_", "MERCHANT_", "STOREFRONT_", "UCP_", "COMMERCE_", "BUYER_", "SELLER_", "DREADNODE_", "PROMPTFOO_", "ORACLE_")
    for key, value in os.environ.items():
        if key.startswith(prefixes) or key == "TASK_CATEGORY":
            hashes["shell/" + key] = hashlib.sha256(value.encode()).hexdigest()
    return hashes


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def attacker_model(framework):
    # Match launcher precedence without copying credentials to metadata.
    code = """import json,os
from pathlib import Path
from dotenv import dotenv_values
fw=os.environ['EXPERIMENT_FRAMEWORK']
env={**dotenv_values('.env'),**dotenv_values(Path('redteam')/fw/'.env'),**os.environ}
if fw=='dreadnode': model=os.environ.get('DREADNODE_ATTACKER_MODEL','anthropic/claude-sonnet-4-6')
else:
 model=env.get('OPENAI_MODEL')
print(json.dumps(model))"""
    result = subprocess.check_output([str(PYTHON), "-c", code], cwd=ROOT,
        env={**os.environ, "EXPERIMENT_FRAMEWORK": framework}, text=True)
    return json.loads(result)


def victim_model(role):
    # Resolve role-specific dotenv files in the same isolated process as the hosts.
    code = "from pathlib import Path;from demo_common import load_demo_env;import os;load_demo_env(Path(os.environ['EXPERIMENT_HOST']));print(os.environ.get('ANTHROPIC_MODEL',''))"
    return subprocess.check_output([str(PYTHON), "-c", code], cwd=ROOT,
        env={**os.environ, "EXPERIMENT_HOST": str(ROOT / ("storefront" if role == "buyer" else "merchant"))}, text=True).strip()
