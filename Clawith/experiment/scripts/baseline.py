"""Save a freshly prepared lab; restore it without bootstrap or model calls."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid

from experiment_common import (ROOT, campaign_lock, configuration, now, read_json,
                               safe_name, write_json)
from environment import images

BASELINES = ROOT / "experiment/baselines"
ORACLE = ROOT / ".run/oracle"
APPLICATIONS = ("frontend", "redteam-mcp", "backend")
FILES = ("database.dump", "agent-data.tar", "prepared.json")
QUIET_SQL = ("SELECT (SELECT count(*) FROM agent_runs WHERE lane_held OR delivery_status='pending')"
             " + (SELECT count(*) FROM agent_run_commands WHERE status IN ('pending','claimed'))"
             " + (SELECT count(*) FROM agent_tool_executions WHERE status IN ('started','unknown'))")
IDENTITY_SQL = ("SELECT json_build_object('ALICE_USER_ID', u.id, 'ALICE_TENANT_ID', u.tenant_id) "
                "FROM users u JOIN identities i ON u.identity_id=i.id WHERE i.email='alice@clawith.com'")
MODELS_SQL = ("SELECT json_object_agg(a.name,json_build_object('primary',m.model,'fallback',f.model)) "
              "FROM agents a LEFT JOIN llm_models m ON m.id=a.primary_model_id "
              "LEFT JOIN llm_models f ON f.id=a.fallback_model_id WHERE a.name IN ('Morty','Meeseeks')")


def workspace_fingerprint():
    code = """import hashlib,json,os
from pathlib import Path
h=hashlib.sha256();count=0
for path in sorted(Path('/data/agents').rglob('*')):
    name=str(path.relative_to('/data/agents'))
    if path.is_symlink(): data=('symlink:'+os.readlink(path)).encode()
    elif path.is_file(): data=hashlib.sha256(path.read_bytes()).digest()
    else: continue
    h.update(name.encode()+b'\\0'+data+b'\\0');count+=1
print(json.dumps({'sha256':h.hexdigest(),'entries':count}))
"""
    result = compose("run", "--rm", "--no-deps", "-T", "--user", "0:0", "--entrypoint", "python",
                     "backend", "-c", code, capture_output=True, text=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compose(*args, env=None, **kwargs):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, env=env,
                          check=True, timeout=600, **kwargs)


def sql(query):
    result = compose("exec", "-T", "postgres", "psql", "-U", "clawith", "-d", "clawith",
                     "-v", "ON_ERROR_STOP=1", "-tAc", query, capture_output=True, text=True)
    return result.stdout.strip()


def wait_ready():
    for _ in range(120):
        try:
            compose("exec", "-T", "backend", "python", "-c",
                    "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health',timeout=3)",
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            compose("exec", "-T", "redteam-mcp", "python", "-c",
                    "import urllib.request,urllib.error,sys\n"
                    "try: urllib.request.urlopen(urllib.request.Request('http://localhost:3010/mcp',data=b'{}'),timeout=3)\n"
                    "except urllib.error.HTTPError as error: sys.exit(0 if error.code == 401 else 1)\n"
                    "else: sys.exit(1)", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except subprocess.CalledProcessError:
            time.sleep(2)
    raise RuntimeError("Backend did not become ready")


def wait_quiet():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if sql(QUIET_SQL) == "0":
            time.sleep(2)
            if sql(QUIET_SQL) == "0":
                return
        time.sleep(2)
    raise RuntimeError("Prepared lab still has active runs, deliveries or commands")


def verify(name, *, compatible=True):
    path = BASELINES / safe_name(name)
    if not (path / "manifest.json").is_file():
        raise ValueError(f"Missing baseline {name}; create it with --save-baseline {name}")
    manifest = read_json(path / "manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("name") != name:
        raise ValueError("Invalid baseline manifest")
    if set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Incomplete baseline")
    for filename in FILES:
        if digest(path / filename) != manifest["files"][filename]:
            raise ValueError(f"Baseline archive changed: {filename}")
    prepared = read_json(path / "prepared.json")
    if prepared.get("state") != "preparing" or not prepared.get("lab") or not prepared.get("truthIds"):
        raise ValueError("Baseline must contain a prepared, unarmed episode")
    if compatible and (manifest["target_configuration"] != configuration(target_only=True)
                       or manifest["images"] != images()):
        raise ValueError("Baseline target configuration or images changed; create a new baseline")
    return {"name": name, "manifest_sha256": digest(path / "manifest.json")}


def save(name):
    destination = BASELINES / safe_name(name)
    if destination.exists():
        raise ValueError(f"Baseline already exists: {name}")
    BASELINES.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = BASELINES / (".capture-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    print(f"[baseline] Creating {name}: hard reset, then one Oracle prepare", flush=True)
    subprocess.run(["bash", "oracle/reset.sh", "--hard"], cwd=ROOT,
                   env={**os.environ, "CLAWITH_SKIP_BUILD": "1"}, check=True)
    prepare_env = {k: v for k, v in os.environ.items() if k != "CLAWITH_PREPARED_BASELINE"}
    subprocess.run(["uv", "run", "--with-requirements", "oracle/requirements.txt", "python",
                    "-m", "oracle.run_episode", "--prepare"], cwd=ROOT, env=prepare_env, check=True)
    wait_quiet()
    prepared = read_json(ORACLE / "episode.json")
    if prepared.get("state") != "preparing":
        raise ValueError("Capture requires a prepared, unarmed episode")
    identity = json.loads(sql(IDENTITY_SQL))
    victim_models = json.loads(sql(MODELS_SQL))
    # No application process may write while database and files are captured.
    compose("stop", *APPLICATIONS)
    try:
        if sql(QUIET_SQL) != "0":
            raise RuntimeError("Activity appeared while stopping the lab; refusing an inconsistent snapshot")
        with (temporary / "database.dump").open("wb") as output:
            compose("exec", "-T", "postgres", "pg_dump", "-U", "clawith", "-d", "clawith",
                    "--format=custom", "--no-owner", stdout=output)
        with (temporary / "agent-data.tar").open("wb") as output:
            compose("run", "--rm", "--no-deps", "-T", "--user", "0:0", "--entrypoint", "tar",
                    "backend", "-cpf", "-", "-C", "/data/agents", ".", stdout=output)
        write_json(temporary / "prepared.json", prepared)
        manifest = {"schema_version": 1, "name": name, "created_at": now(),
                    "target_configuration": configuration(target_only=True), "images": images(),
                    "identity": identity, "victim_models": victim_models,
                    "workspace": workspace_fingerprint(), "files": {f: digest(temporary / f) for f in FILES}}
        write_json(temporary / "manifest.json", manifest)
        for path in temporary.iterdir():
            path.chmod(0o600)
        temporary.rename(destination)
    finally:
        compose("start", "backend", "frontend", "redteam-mcp")
    print(f"[baseline] Saved {destination}", flush=True)


def restore(name, run_id, expected_manifest=None):
    reference = verify(name)  # Must finish before any destructive command.
    if expected_manifest is not None and reference["manifest_sha256"] != expected_manifest:
        raise ValueError("Baseline differs from the batch manifest")
    path = BASELINES / name
    manifest = read_json(path / "manifest.json")
    environment = {**os.environ, **manifest["identity"]}
    print(f"[baseline] Restoring {name}", flush=True)
    (ORACLE / "restored-prepared.json").unlink(missing_ok=True)
    compose("stop", *APPLICATIONS)
    compose("rm", "-f", *APPLICATIONS)
    # Recreating backend also removes /root and other writable-layer mutations.
    compose("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "postgres", "redis")
    compose("exec", "-T", "postgres", "dropdb", "-U", "clawith", "--if-exists", "--force", "clawith")
    compose("exec", "-T", "postgres", "createdb", "-U", "clawith", "clawith")
    with (path / "database.dump").open("rb") as source:
        compose("exec", "-T", "postgres", "pg_restore", "-U", "clawith", "-d", "clawith",
                "--no-owner", "--exit-on-error", stdin=source)
    with (path / "agent-data.tar").open("rb") as source:
        compose("run", "--rm", "--no-deps", "-T", "--user", "0:0", "--entrypoint", "sh", "backend", "-c",
                "find /data/agents -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + "
                "&& tar -xpf - -C /data/agents", stdin=source)
    compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
    if manifest.get("workspace") and workspace_fingerprint() != manifest["workspace"]:
        raise ValueError("Restored workspace differs from the baseline")
    if json.loads(sql(IDENTITY_SQL)) != manifest["identity"]:
        raise ValueError("Restored database identities differ from the baseline")
    if manifest.get("victim_models") and json.loads(sql(MODELS_SQL)) != manifest["victim_models"]:
        raise ValueError("Restored victim models differ from the baseline")
    (ORACLE / "episode.json").unlink(missing_ok=True)
    if (ORACLE / "prepare-backup").exists():
        shutil.rmtree(ORACLE / "prepare-backup")
    compose("up", "-d", "--no-build", "--no-deps", "backend", "frontend", "redteam-mcp", env=environment)
    wait_ready()
    if sql(QUIET_SQL) != "0":
        raise RuntimeError("Restored lab has pending work")
    # The one-use receipt is bound to this attempt; prepare validates live lab IDs.
    write_json(ORACLE / "restored-prepared.json", {"baseline": reference, "run_id": run_id,
               "prepared": read_json(path / "prepared.json")})
    print(f"[baseline] Restored {name}; next prepare will reuse seeded state", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save", "restore", "check"))
    parser.add_argument("name", type=safe_name)
    parser.add_argument("--run-id")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    os.umask(0o077)
    with campaign_lock():
        if args.action == "save":
            save(args.name)
        elif args.action == "restore":
            if not args.run_id:
                parser.error("restore requires --run-id")
            restore(args.name, args.run_id, args.manifest_sha256)
        else:
            print(verify(args.name))
