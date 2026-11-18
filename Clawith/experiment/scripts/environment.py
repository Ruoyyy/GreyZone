"""Read-only prerequisites and complete reset using the existing lab scripts."""
import shutil
import subprocess
import os
import json
import re

from experiment_common import ROOT, env_value
from redteam.dreadnode.run import dreadnode_python


def images():
    output = subprocess.check_output(["docker", "compose", "config", "--images"], cwd=ROOT, text=True)
    return {name: subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name],
                                        cwd=ROOT, text=True).strip() for name in sorted(set(output.split()))}


def preflight(framework=None):
    processes = subprocess.check_output(["ps", "-u", str(os.getuid()), "-o", "comm=,args="], text=True)
    for line in processes.splitlines():
        executable, _, command = line.strip().partition(" ")
        if ("python" in executable and re.search(r"(?:-m\s+oracle\.watch\b|oracle/watch\.py\b)", command)
                and "--probe" not in command and "--close" not in command):
            raise ValueError("Stop the standalone Oracle watcher before experiments")
    for command in ("docker", "uv", "node" if framework == "promptfoo" else "python3"):
        if not shutil.which(command):
            raise ValueError(f"Missing command: {command}")
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "MCP_API_KEY", "JWT_SECRET_KEY", "SECRET_KEY"):
        value = env_value(key)
        if not value or value.startswith("change-me-"):
            raise ValueError(f"Configure {key} first; see Clawith/README.md")
    if framework == "promptfoo":
        if not (ROOT / "redteam/promptfoo/node_modules/.bin/promptfoo").exists():
            raise ValueError("Install Promptfoo dependencies first")
        installed = json.loads((ROOT / "redteam/promptfoo/node_modules/promptfoo/package.json").read_text())["version"]
        expected = json.loads((ROOT / "redteam/promptfoo/package.json").read_text())["dependencies"]["promptfoo"]
        if installed != expected:
            raise ValueError(f"Promptfoo {expected} is required; found {installed}")
        version = tuple(map(int, subprocess.check_output(["node", "-p", "process.versions.node"], text=True).strip().split(".")))
        if version < (22, 22, 0):
            raise ValueError("Node 22.22+ is required")
    elif framework in ("deepteam", "pyrit"):
        python = ROOT / "redteam" / framework / ".venv/bin/python"
        code = (f"import {framework}, httpx, websockets, fastmcp, mcp;"
                f"from redteam.{framework}.run import attacker_settings,check_version;"
                "from redteam.dreadnode.run import env_value;"
                "check_version();attacker_settings(env_value('OPENAI_MODEL'))")
        subprocess.run([str(python), "-c", code], cwd=ROOT, check=True)
    elif framework == "dreadnode":
        dn = shutil.which("dn")
        if not dn:
            raise ValueError("Install Dreadnode CLI first")
        subprocess.run([dreadnode_python(dn), "-c", "import dreadnode,httpx,websockets,fastmcp,mcp;"
                        "import importlib.metadata as m;assert m.version('dreadnode')=='2.0.46','Dreadnode 2.0.46 is required'"], check=True)
    images()


def reset(progress, execute, baseline=None, run_id=None, expected_manifest=None):
    if baseline is not None:
        from experiment_common import PYTHON
        progress.stage("restoring-baseline", baseline)
        command = [PYTHON, str(ROOT / "experiment/scripts/baseline.py"), "restore", baseline,
                   "--run-id", run_id]
        if expected_manifest:
            command += ["--manifest-sha256", expected_manifest]
        execute(command, ROOT)
        return
    progress.stage("resetting", "Removing lab state and recreating containers from existing images")
    execute(["bash", "oracle/reset.sh", "--hard"], ROOT)
