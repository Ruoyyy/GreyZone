"""Read-only stack inspection and framework prerequisites; never print secrets."""
import json
import os
import re
import shutil
import subprocess
import time

from experiment_common import ROOT, PYTHON, child_env, interpreter, reset_time_remaining

COMPOSE = ["docker", "compose", "-p", "commonly", "-f", "docker-compose.stack.yml"]
VOLUMES = ("mongo-data", "postgres-data", "core-secrets", "kai-secrets", "mcp-secrets", "kai-workspace", "kai-home")
SERVICES = ("mongo", "postgres", "litellm", "backend", "frontend", "agent-kai", "redteam-mcp")


def compose(*args, **kwargs):
    return subprocess.run([*COMPOSE, *args], cwd=ROOT, check=True, timeout=reset_time_remaining(), **kwargs)


def stack_config():
    return json.loads(compose("config", "--format", "json", capture_output=True, text=True).stdout)


def images():
    config = stack_config()
    result = {}
    for service, data in config["services"].items():
        name = data["image"]
        result[service] = subprocess.check_output(
            ["docker", "image", "inspect", name, "--format", "{{.Id}}"], text=True,
            timeout=reset_time_remaining(default=30)).strip()
    return result


def volumes():
    data = stack_config()["volumes"]
    if set(data) != set(VOLUMES):
        raise ValueError("Unexpected persistent volumes in Commonly stack")
    result = {}
    for logical in VOLUMES:
        spec = data[logical]
        if spec.get("external") or spec.get("name") != "commonly_" + logical:
            raise ValueError("Baseline restoration requires Commonly-owned volumes")
        result[logical] = spec["name"]
    return result


def assert_no_watcher():
    output = subprocess.check_output(["ps", "-u", str(os.getuid()), "-o", "comm=,args="], text=True)
    for line in output.splitlines():
        command, _, args = line.strip().partition(" ")
        if "python" in command and re.search(r"(?:-m\s+oracle\.watch\b|oracle/watch\.py\b)", args):
            if "--probe" not in args and "--close" not in args:
                raise ValueError("Stop standalone Oracle watchers before experiments")


def preflight(frameworks=(), descriptor=None):
    assert_no_watcher()
    if not shutil.which("docker") or not PYTHON.is_file():
        raise ValueError("Install Docker and redteam/.venv first")
    try:
        subprocess.run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=commonly", "--format", "{{.ID}}"],
                       capture_output=True, check=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker container queries did not respond within 20s; resolve the Docker daemon/container state before resetting Commonly") from exc
    from redteam.common import attacker_settings
    attacker_settings(os.environ.get("OPENAI_MODEL", ""))
    images()
    volumes()
    for framework in frameworks:
        if framework == "promptfoo":
            version = tuple(map(int, subprocess.check_output(["node", "-p", "process.versions.node"], text=True).strip().split(".")))
            if version < (22, 22, 0):
                raise ValueError("Node 22.22+ is required")
        if framework == "dreadnode":
            version = subprocess.check_output(["dn", "--version"], text=True).strip()
            if not re.search(r"(?<![\d.])2\.0\.46(?![\d.])", version):
                raise ValueError("Dreadnode 2.0.46 is required")
        subprocess.run([str(interpreter(framework)), f"redteam/{framework}/run.py", "--offline-check"],
                       cwd=ROOT, env=child_env(descriptor),
                       pass_fds=(descriptor,) if descriptor is not None else (), check=True, timeout=120)
    config = stack_config()
    if float(config["services"]["backend"]["environment"].get("COMMONLY_AUTO_APPROVE_MS", 0)) <= 0:
        raise ValueError("Unattended approval must be enabled for action scenarios")


def quiet():
    query = """print(JSON.stringify({
      events: db.agentevents.countDocuments({status: {$in: ['pending','delivered']}}),
      runs: db.agentruns.countDocuments({status: {$in: ['queued','running']}}),
      cards: db.approvalactions.countDocuments({$or: [{status:'flagged'},
        {decision:'approved', executedAt: null, executionError: null}]})
    }))"""
    raw = compose("exec", "-T", "mongo", "mongosh", "--quiet", "commonly", "--eval", query,
                  capture_output=True, text=True).stdout
    state = json.loads(raw)
    if set(state) != {"events", "runs", "cards"} or any(type(v) is not int or v < 0 for v in state.values()):
        raise ValueError("Invalid environment settlement response")
    return not any(state.values())


def wait_quiet(timeout=None):
    timeout = reset_time_remaining(default=120) if timeout is None else timeout
    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
        stable = stable + 1 if quiet() else 0
        if stable >= 2:
            return
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError("Commonly still has active events, runs or approvals")


def verify_substrate(descriptor=None):
    # prepare verifies canonical truth, identities, membership and audit substrate;
    # it does not seed, arm an episode or send an attack.
    code = ("import asyncio;from redteam.common import scenario_data;"
            "from redteam.runner import check;from oracle.run_episode import prepare;"
            "asyncio.run(check());asyncio.run(prepare(scenario_data('forged-delete')[0]))")
    subprocess.run([str(PYTHON), "-c", code], cwd=ROOT, env=child_env(descriptor),
                   pass_fds=(descriptor,) if descriptor is not None else (), check=True, timeout=reset_time_remaining())
