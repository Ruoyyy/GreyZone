"""Restore the canonical shop and validate host configuration before attacks."""
import os
import shutil
import subprocess
import urllib.request

from experiment_common import ROOT, PYTHON, SCENARIOS, read_json, settings


def assert_no_watcher():
    rows = subprocess.check_output(["ps", "-u", str(os.getuid()), "-o", "comm=,args="], text=True)
    for line in rows.splitlines():
        comm, _, args = line.strip().partition(" ")
        if "python" in comm and "oracle/watch.py" in args:
            raise RuntimeError("Stop the standalone Oracle watcher before experiments")


def image_id():
    env = settings()
    return subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}",
        env.get("SHOPWARE_CONTAINER", "commerce-agents-shopware")], text=True).strip()


def preflight(framework):
    assert_no_watcher()
    required = [PYTHON, ROOT / "docker/.generated.env", ROOT / "secrets/ucp-agent-signing-key.pem",
                ROOT / "docker/snapshot/shopware.sql.gz", ROOT / "docker/snapshot/media.tar.gz", ROOT / "docker/snapshot/captured-at.txt"]
    if framework == "promptfoo":
        required.append(ROOT / "redteam/promptfoo/node_modules/.bin/promptfoo")
    elif framework in ("deepteam", "pyrit"):
        required.append(ROOT / "redteam" / framework / ".venv/bin/python")
    elif not shutil.which("dn"):
        raise ValueError("Dreadnode CLI is missing")
    for path in required:
        if not path.exists():
            raise ValueError(f"Missing prerequisite: {path.relative_to(ROOT)}")
    env = settings()
    if not env.get("ANTHROPIC_API_KEY") or not env.get("ANTHROPIC_MODEL"):
        raise ValueError("Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL for the victim hosts")
    if framework != "dreadnode" and not env.get("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY for the selected framework")
    from oracle.common import merchant_host_setting
    if merchant_host_setting("SHOPWARE_LOCAL_STORE") not in (None, "", "0", "false", "False"):
        raise ValueError("Experiments require the real Docker shop (SHOPWARE_LOCAL_STORE=0)")


def check_scenarios(scenarios):
    from oracle.common import oracle_surfaces, require_self_applied_changes
    for scenario in scenarios:
        spec = read_json(SCENARIOS / scenario / "scenario.json")
        require_self_applied_changes(spec)
        if not set(spec["oracleSurfaces"]) <= set(oracle_surfaces()):
            raise ValueError(f"ORACLE_SURFACES disables the selected scenario: {scenario}")


def reset(progress, execute):
    assert_no_watcher()
    progress.stage("resetting", "Restoring the shop snapshot and clearing host state")
    # Stop hosts before importing MySQL to prevent writes during restoration.
    execute(["bash", "oracle/reset.sh"], ROOT)
    progress.stage("checking-environment", "Checking accounts, UCP and integrations")
    execute(["bash", "docker/verify.sh"], ROOT)
    env = settings()
    for base, route in [(env.get("STOREFRONT_API_URL", "http://127.0.0.1:8004"), "/api/health"),
                        (env.get("MERCHANT_API_URL", "http://127.0.0.1:8005"), "/api/merchant/health")]:
        with urllib.request.urlopen(base.rstrip("/") + route, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Host health check failed")
