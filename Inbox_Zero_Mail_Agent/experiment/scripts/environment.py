"""Create a pristine schema baseline and rebuild ephemeral mail state per attempt."""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from uuid import uuid4

from common import APP, ROOT, EXPERIMENT, app_env, now, read_json, target_configuration, write_json


def command(args, *, env=None, cwd=ROOT, input=None, log=None):
    result = subprocess.run(args, cwd=cwd, env=env, input=input, capture_output=True)
    if log:
        with log.open("ab") as stream:
            stream.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}; inspect environment.log")
    return result.stdout


def compose(*args, **kwargs):
    return command(["docker", "compose", "--env-file", str(APP / ".env"), *args], **kwargs)


def sql(query, *, log=None):
    return compose("exec", "-T", "db", "psql", "-U", "postgres", "-d", "postgres",
                   "-v", "ON_ERROR_STOP=1", "-tAc", query, log=log)


def assert_no_watcher():
    # Never kill a manually started watcher or allow concurrent writers.
    result = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "comm=,args="],
                            capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        comm, _, args = line.strip().partition(" ")
        if comm in ("node", "pnpm", "tsx") and "scripts/oracle-watch.ts" in args:
            raise RuntimeError("An Oracle watcher is already running; stop it before starting an experiment")


def save_baseline(name):
    """Migrate a temporary empty database, without copying contaminated live data."""
    path = EXPERIMENT / "baselines" / name
    if path.exists():
        raise ValueError("Baseline already exists; choose a new name")
    path.mkdir(parents=True, mode=0o700)
    log = path / "environment.log"
    database = "inbox_exp_" + uuid4().hex
    created = False
    try:
        sql(f'CREATE DATABASE "{database}";', log=log)
        created = True
        values = app_env()
        from urllib.parse import quote
        url = f"postgresql://postgres:{quote(values['POSTGRES_PASSWORD'], safe='')}@127.0.0.1:5433/{database}?schema=public"
        env = {**os.environ, **values, "DATABASE_URL": url, "DIRECT_URL": url}
        for key in ("PREVIEW_DATABASE_URL", "PREVIEW_DATABASE_URL_UNPOOLED", "DATABASE_URL_UNPOOLED"):
            env.pop(key, None)
        command(["pnpm", "exec", "prisma", "migrate", "deploy"], env=env, cwd=APP, log=log)
        dump = compose("exec", "-T", "db", "pg_dump", "-U", "postgres", "-Fc", database)
        (path / "database.dump").write_bytes(dump)
        write_json(path / "manifest.json", {"created_at": now(), "kind": "pristine-schema-v1",
                   "target_configuration": target_configuration(), "seed_policy": "fresh-canaries-per-attempt"})
    finally:
        if created:
            sql(f'DROP DATABASE "{database}" WITH (FORCE);', log=log)
    return path


def backup_and_reset(baseline, destination, progress):
    assert_no_watcher()
    if read_json(baseline / "manifest.json")["target_configuration"] != target_configuration():
        raise ValueError("Target configuration changed; create a new baseline")
    log = destination / "environment.log"
    backup = EXPERIMENT / "backups" / (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    write_json(destination / "previous-state.json", {"backup": str(backup)})
    # Export in-memory mail before stopping the emulator. This is forensic
    # evidence, not a restorable emulator snapshot (OAuth state is ephemeral).
    messages = []
    token = (APP / ".tmp/customer-mail/.transport-token").read_text().strip()
    def emulator_get(endpoint):
        request = urllib.request.Request("http://127.0.0.1:4002" + endpoint,
                                         headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    try:
        page = emulator_get("/gmail/v1/users/me/messages?maxResults=1000")
        for entry in page.get("messages", []):
            messages.append(emulator_get(f"/gmail/v1/users/me/messages/{entry['id']}?format=raw"))
        if page.get("nextPageToken"):
            raise RuntimeError("Mailbox export exceeds 1000 messages; archive it before resetting")
        write_json(backup / "mailbox.json", messages)
    except OSError as exc:
        raise RuntimeError("Cannot archive the live emulator; start the mail stack before running experiments") from exc
    progress.stage("resetting", "Stopping the app and mail services; preserving the previous state")
    command(["bash", "scripts/mail-loop.sh", "down"], log=log)
    compose("up", "-d", "db", "redis", "serverless-redis-http", log=log)
    for _ in range(60):
        try:
            sql("SELECT 1;", log=log)
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise RuntimeError("Postgres did not become ready")
    (backup / "database.dump").write_bytes(compose("exec", "-T", "db", "pg_dump", "-U", "postgres", "-Fc", "inboxzero"))
    compose("exec", "-T", "redis", "redis-cli", "SAVE", log=log)
    (backup / "redis.rdb").write_bytes(compose("exec", "-T", "redis", "cat", "/data/dump.rdb"))
    for relative in (".tmp/customer-mail", ".tmp/oracle"):
        original = APP / relative
        if original.exists():
            shutil.move(str(original), str(backup / original.name))
    customer = APP / ".tmp/customer-mail"
    customer.mkdir(parents=True, mode=0o700)
    token = backup / "customer-mail/.transport-token"
    if token.exists():
        shutil.copy2(token, customer / ".transport-token")
    else:
        raise RuntimeError("Transport token is missing; run mail-loop-init.sh first")
    sql('DROP DATABASE "inboxzero" WITH (FORCE);', log=log)
    sql('CREATE DATABASE "inboxzero";', log=log)
    compose("exec", "-T", "db", "pg_restore", "-U", "postgres", "-d", "inboxzero", "--exit-on-error",
            input=(baseline / "database.dump").read_bytes(), log=log)
    # This Redis service belongs exclusively to the local mail experiment stack.
    compose("exec", "-T", "redis", "redis-cli", "FLUSHALL", log=log)
    values = app_env()
    mail_args = ["docker", "compose", "--env-file", str(APP / ".env"), "--profile", "mail-loop",
                 "-f", "docker-compose.mail-loop.yml"]
    state = command([*mail_args, "run", "--rm", "--no-deps", "--entrypoint", "node", "mail-backbone", "-e",
             "const fs=require('fs');const p='/app/state/state.json';if(fs.existsSync(p)){process.stdout.write(fs.readFileSync(p));fs.rmSync(p);}"],
            env={**os.environ, **values})
    (backup / "backbone-state.json").write_bytes(state)
    progress.stage("bootstrapping", "Starting services, login, fixtures and mail watch")
    command(["bash", "scripts/mail-loop.sh", "up"], env={**os.environ, **values}, log=log)
    shutil.copy2(APP / ".tmp/oracle/canaries.json", destination / "canaries.json")
    for url in ("http://127.0.0.1:3000/login", "http://127.0.0.1:4005/.well-known/openid-configuration"):
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Service health check failed")
