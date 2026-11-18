"""Freeze execution budgets and actual runtime versions before a series starts."""
import json
import subprocess
import sys
import platform

from experiment_common import ROOT, FRAMEWORKS, configuration, env_value, revision
from environment import images
from redteam.dreadnode.run import dreadnode_python
import shutil

DEFAULTS = {"run_timeout": 14400, "reset_timeout": 1200, "reply_timeout": 300,
            "settle_timeout": 300, "oracle_timeout": 1200, "max_turns": 10}


def options(args, saved=None):
    result = dict(saved or DEFAULTS)
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            if saved and value != saved[key]:
                raise ValueError(f"Resume cannot change {key}")
            result[key] = value
        setattr(args, key, result[key])
    if result["oracle_timeout"] <= result["settle_timeout"]:
        raise ValueError("oracle-timeout must exceed settle-timeout")
    if result["max_turns"] > 20:
        raise ValueError("max-turns must not exceed 20")
    return result


def runtime(framework):
    if framework == "promptfoo":
        version = json.loads((ROOT / "redteam/promptfoo/node_modules/promptfoo/package.json").read_text())["version"]
        node = subprocess.check_output(["node", "--version"], text=True).strip()
        code = "const fs=require('fs');const e=require('node:util').parseEnv(fs.readFileSync('.env','utf8'));console.log(e.OPENAI_MODEL||process.env.OPENAI_MODEL||'')"
        model = subprocess.check_output(["node", "-e", code], cwd=ROOT, text=True).strip()
        oracle_python = subprocess.check_output(["uv", "python", "find", "3.11"], text=True).strip()
        oracle_version = subprocess.check_output([oracle_python, "-c", "import sys;print(sys.version.split()[0])"], text=True).strip()
        return {"framework": version, "runtime": node, "attacker_model": model,
                "oracle_executable": oracle_python, "oracle_python": oracle_version}
    python = (dreadnode_python(shutil.which("dn")) if framework == "dreadnode" else
              str(ROOT / "redteam" / framework / ".venv/bin/python"))
    packages = [framework, "openai", "httpx", "mcp", "fastmcp"]
    if framework == "deepteam":
        packages.append("deepeval")
    code = "import json,sys,importlib.metadata as m;print(json.dumps({'runtime':sys.version.split()[0],**{p:m.version(p) for p in sys.argv[1:]}}))"
    data = json.loads(subprocess.check_output([python, "-c", code, *packages], text=True))
    data["attacker_model"] = ("openai/" if framework == "dreadnode" else "") + env_value("OPENAI_MODEL")
    return data


def snapshot(frameworks):
    return {"revision": revision(), "configuration": configuration(), "images": images(),
            "runtimes": {fw: runtime(fw) for fw in frameworks}, "harness_python": sys.version.split()[0],
            "platform": platform.platform(), "compose_version": subprocess.check_output(
                ["docker", "compose", "version", "--short"], text=True).strip()}


def verify(saved, frameworks):
    current = snapshot(frameworks)
    for key in ("revision", "configuration", "images", "harness_python"):
        if current[key] != saved[key]:
            raise ValueError(f"Frozen experiment {key} changed; start a new series")
    for key in ("platform", "compose_version"):
        if key in saved and current.get(key) != saved[key]:
            raise ValueError(f"Frozen experiment {key} changed")
    for fw in frameworks:
        if current["runtimes"][fw] != saved["runtimes"][fw]:
            raise ValueError(f"Frozen runtime or model changed: {fw}")
