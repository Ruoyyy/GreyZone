"""Freeze effective source, configuration, models, images and installed runtimes."""
import json
import os
import platform
import subprocess
import importlib.util

from experiment_common import ROOT, configuration, interpreter, revision
from environment import images


def runtime(framework):
    packages = ["httpx", "mcp", "fastmcp"]
    if framework in {"deepteam", "pyrit"}:
        packages += [framework, "openai"]
    if framework == "deepteam":
        packages += ["deepeval"]
    code = ("import sys,json,importlib.metadata as m;"
            "print(json.dumps({'python':sys.version.split()[0],"
            "'packages':{p:m.version(p) for p in sys.argv[1:]}}))")
    result = json.loads(subprocess.check_output([str(interpreter(framework)), "-c", code, *packages], text=True))
    if framework == "promptfoo":
        result["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
        package = ROOT / "redteam/promptfoo/node_modules/promptfoo/package.json"
        result["promptfoo"] = json.loads(package.read_text())["version"]
    if framework == "dreadnode":
        result["dreadnode"] = subprocess.check_output(["dn", "--version"], text=True).strip()
    return result


def snapshot(frameworks):
    spec = importlib.util.spec_from_file_location("commonly_result_contract", ROOT.parent / "scripts/oracle_result.py")
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    return {"revision": revision(), "configuration": configuration(), "images": images(),
            "oracle_policy": contract.SCORE_POLICY_VERSION,
            "runtimes": {fw: runtime(fw) for fw in frameworks}, "platform": platform.platform(),
            "harness_python": platform.python_version(),
            "compose": subprocess.check_output(["docker", "compose", "version", "--short"], text=True).strip(),
            "attacker_model": os.environ["OPENAI_MODEL"], "victim_model": os.environ["OPENAI_MODEL"]}


def verify(saved, frameworks):
    current = snapshot(frameworks)
    for key in current:
        if key == "runtimes":
            if any(current[key][fw] != saved[key].get(fw) for fw in frameworks):
                raise ValueError("Frozen framework runtime changed; start a new batch")
        elif current[key] != saved.get(key):
            raise ValueError(f"Frozen experiment {key} changed; start a new batch")
