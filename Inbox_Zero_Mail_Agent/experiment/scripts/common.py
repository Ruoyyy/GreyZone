"""Small standard-library helpers for Inbox Zero experiments."""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiment"
RESULTS = EXPERIMENT / "results"
SCENARIOS = ROOT / "benchmark/scenarios"
APP = ROOT / "apps/web"
FRAMEWORKS = ("promptfoo", "deepteam", "pyrit", "dreadnode")


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("Expected a simple name without path separators")
    return value


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def app_env():
    result = subprocess.run(["node", "--input-type=module", "-e",
        "import {parseEnv} from 'node:util'; import fs from 'node:fs'; "
        "process.stdout.write(JSON.stringify(parseEnv(fs.readFileSync(process.argv[1], 'utf8'))));",
        str(APP / ".env")], capture_output=True, check=True, text=True)
    return json.loads(result.stdout)


def configuration():
    paths = [APP / ".env", ROOT / "docker-compose.yml", ROOT / "docker-compose.mail-loop.yml",
             ROOT / "pnpm-lock.yaml", ROOT / "emulate.local.config.yaml"]
    for framework in FRAMEWORKS:
        paths.extend((ROOT / "redteam" / framework / name) for name in
                     (".env", "config.yaml", "promptfooconfig.yaml", "capability.yaml", "package-lock.json", "uv.lock"))
        directory = ROOT / "redteam" / framework
        for source in (directory, directory / "agents", directory / "tools"):
            paths += [p for p in source.glob("*") if p.suffix in (".py", ".mjs", ".sh", ".md", ".yaml")]
    paths += list((ROOT / "redteam").glob("*.py"))
    paths += list((ROOT / "redteam/shared").glob("*.sh"))
    paths += list((EXPERIMENT / "scripts").glob("*.py"))
    paths += list(SCENARIOS.glob("*/*"))
    for directory in (APP / "scripts", APP / "utils/llms", ROOT / "scripts"):
        paths += [p for p in directory.rglob("*") if p.suffix in (".ts", ".sh", ".py") and ".test." not in p.name]
    paths += [APP / "env.ts", APP / "prisma/schema.prisma"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(set(paths)) if p.is_file()}
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "DREADNODE_ATTACKER_MODEL", "TRANSPORT_URL"):
        if key in os.environ:
            hashes["shell/" + key] = hashlib.sha256(os.environ[key].encode()).hexdigest()
    return hashes


def target_configuration():
    return {k: v for k, v in configuration().items() if not k.startswith(("redteam/", "experiment/", "shell/"))}


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def scenario(name):
    return read_json(SCENARIOS / safe_name(name) / "scenario.json")


def campaign_lock():
    import sys
    sys.path.insert(0, str(ROOT / "redteam"))
    from run_locked import acquire
    return acquire()
