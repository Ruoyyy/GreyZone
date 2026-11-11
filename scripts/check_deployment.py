#!/usr/bin/env python3
"""Read-only deployment preflight for maintainers; optional existing regressions.

This tool does not build images, start services, or produce benchmark verdicts.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
RANGES = {
    "Clawith": {
        "directory": "Clawith", "compose": ["docker-compose.yml"],
        "scripts": ["setup.sh", "start.sh", "restart.sh"],
        "sources": ["backend/pyproject.toml", "backend/uv.lock", "frontend/package-lock.json",
                    "redteam-mcp/requirements.txt", "redteam-mcp/requirements.lock"],
        "tests": ["oracle/test_oracle.py"],
    },
    "Inbox_Zero_Mail_Agent": {
        "directory": "Inbox_Zero_Mail_Agent",
        "tools": ["setsid"],
        "compose": ["docker-compose.yml", "docker/docker-compose.local.yml", "docker-compose.mail-loop.yml"],
        "scripts": ["setup.sh", "scripts/mail-loop-init.sh", "scripts/mail-loop.sh"],
        "sources": ["pnpm-lock.yaml", "pnpm-workspace.yaml", "docker/Dockerfile.local",
                    "docker/Dockerfile.prod", "emulate.local.config.yaml",
                    "scripts/emulate-package.json", "scripts/emulate-package-lock.json",
                    "patches/@inbox-zero__emulate@0.11.1.patch"],
    },
    "Letta_Issue_Agent": {
        "directory": "Letta_Issue_Agent", "compose": ["docker-compose.yml"],
        "scripts": ["init-gitea.sh"],
        "sources": ["repo", "redteam-mcp/comment-prefix.patch", "redteam-mcp/server-name.patch", "runtime-apk.txt",
                    "runtime-cli/package.json", "runtime-cli/package-lock.json"],
        "tests": ["oracle/test_oracle.py"],
    },
    "ZerePy": {
        "directory": "ZerePy", "compose": ["docker-compose.yml"],
        "scripts": ["scripts/init.sh", "chain/entrypoint.sh"],
        "sources": ["poetry.lock", "chain/seed.py", "mock/seed_tweets.json",
                    "chain/requirements.txt", "chain/requirements.lock",
                    "mock/requirements.txt", "mock/requirements.lock",
                    "redteam-mcp/requirements.txt", "redteam-mcp/requirements.lock"],
        "tests": ["oracle/test_oracle.py"],
    },
    "agent-openai-java-banking-assistant": {
        "directory": "agent-openai-java-banking-assistant/app", "compose": ["compose.yaml"],
        "scripts": ["start-compose.sh"],
        "sources": ["copilot/pom.xml", "frontend/package-lock.json",
                    "business-api/account/pom.xml", "business-api/payment/pom.xml",
                    "business-api/transactions-history/pom.xml", "business-api/redteam-mcp/pom.xml"],
        "test_directory": "agent-openai-java-banking-assistant",
        "tests": ["oracle/test_oracle.py"],
    },
    "alpaca-mcp-server": {
        "directory": "alpaca-mcp-server/sim", "compose": ["docker-compose.yml"],
        "sources": ["../uv.lock", "agent/mcp.json", "agent/settings.json",
                    "requirements-mcp.txt", "requirements-mcp.lock",
                    "market/requirements.txt", "market/requirements.lock",
                    "redteam-mcp/requirements.txt", "redteam-mcp/requirements.lock"],
        "tests": ["oracle/test_oracle.py"],
    },
    "commonly": {
        "directory": "commonly", "compose": ["docker-compose.stack.yml"],
        "scripts": ["docker/stack/init-secrets.sh", "docker/stack/agent/entrypoint.sh"],
        "sources": ["backend/package-lock.json", "frontend/package-lock.json",
                    "docker/stack/agent/runtime-cli/package.json",
                    "docker/stack/agent/runtime-cli/package-lock.json",
                    "backend/docs/skills/awesome-agent-skills-index.json",
                    "docs/upstream/skills/awesome-agent-skills-index.json"],
        "tests": ["oracle/test_oracle.py", "oracle/test_fixtures.py"],
    },
    "kubectl-ai": {
        "directory": "kubectl-ai", "tools": ["go", "kind", "kubectl"],
        "scripts": ["sim/up.sh", "sim/up-redteam-mcp.sh", "sim/run-agent.sh"],
        "sources": ["go.mod", "go.sum", "gollm/go.mod", "gollm/go.sum",
                    "kubectl-utils/go.mod", "kubectl-utils/go.sum",
                    "sim/victim/Dockerfile", "sim/redteam-mcp/Dockerfile", "sim/collector/Dockerfile",
                    "sim/redteam-mcp/requirements.txt", "sim/redteam-mcp/requirements.lock",
                    "sim/manifests/alice-rbac.yaml", "sim/manifests/real-workloads.yaml"],
        "tests": ["oracle/test_oracle.py"],
    },
    "shopware-claude-commerce": {
        "directory": "shopware-claude-commerce", "compose": ["docker/compose.yaml"],
        "scripts": ["docker/bootstrap.sh", "docker/verify.sh", "dev.sh"],
        "sources": ["requirements.txt", "constraints.txt", "package-lock.json", "docker/snapshot/captured-at.txt",
                    "storefront/data/discovery.json", "storefront/data/recorded_responses.json",
                    "storefront/data/disclosure_copy.de.json"],
        "tests": ["oracle/test_oracle.py"],
    },
    "smart-home": {
        "directory": "smart-home/sim", "compose": ["docker-compose.yaml"],
        "sources": ["upstreams.lock.json", "config/configuration.yaml",
                    "../hga/requirements_runtime_manifest.txt", "../hga/requirements_runtime.lock",
                    "../hga/custom_components/home_generative_agent/manifest.json"],
        "tests": ["oracle/test_oracle.py", "oracle/test_runtime_files.py"],
    },
}
INBOX_OFFLINE = [
    "scripts/oracle-lib.test.ts", "scripts/emulate-seed.test.ts",
    "scripts/check-client-redirects.test.ts", "__tests__/fixtures/inboxes/adapters.test.ts",
    "utils/llms/index.generate-object.test.ts", "utils/llms/ollama-guidance.test.ts",
    "utils/llms/openai-config.test.ts",
]


def stop_process(process):
    # A timed-out test runner may own child workers or local test servers.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()


def run_command(argv, cwd, timeout):
    if not cwd.is_dir():
        return "FAIL", "", f"Working directory missing: {cwd}", None
    env = dict(os.environ)
    for key in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME",
                "RUN_INTEGRATION_TESTS", "RUN_DB_TESTS", "RUN_E2E_TESTS", "RUN_E2E_FLOW_TESTS"):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", NODE_OPTIONS="--max-old-space-size=512", RUN_AI_TESTS="false")
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as exc:
        return "BLOCKED", "", f"Cannot execute {argv[0]}: {exc}", None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        stop_process(process)
        process.communicate()
        if isinstance(exc, KeyboardInterrupt):
            raise
        return "FAIL", "", f"Timed out after {timeout:g}s; process group stopped", process.returncode
    return ("PASS" if process.returncode == 0 else "FAIL"), stdout, stderr, process.returncode


def build_inputs(config):
    """Check only local build contexts, Dockerfiles and additional contexts."""
    missing, unresolved = [], []
    for name, service in config.get("services", {}).items():
        build = service.get("build")
        if not build:
            continue
        context = build["context"]
        if "${" in context or "://" in context or context.startswith("git@"):
            unresolved.append(name)
            continue
        context = Path(context)
        if not context.is_dir():
            missing.append(f"{name}: build context")
        if not build.get("dockerfile_inline") and not (context / build.get("dockerfile", "Dockerfile")).is_file():
            missing.append(f"{name}: Dockerfile")
        for key, value in build.get("additional_contexts", {}).items():
            if "${" in value:
                unresolved.append(f"{name}/{key}")
            elif "://" not in value and not value.startswith("service:") and not Path(value).exists():
                missing.append(f"{name}/{key}: additional context")
    if missing:
        return "FAIL", "Missing: " + ", ".join(missing)
    if unresolved:
        return "BLOCKED", "Local build input validation incomplete: " + ", ".join(unresolved)
    return "PASS", "Compose structure and local build inputs checked; runtime env/bind mounts need setup"


def regression_commands(project, spec, cwd):
    if project == "Inbox_Zero_Mail_Agent":
        cwd = cwd / "apps/web"
        return [(cwd, ["node", "node_modules/vitest/vitest.mjs", "run", "--maxWorkers=1",
                       "--no-file-parallelism", *INBOX_OFFLINE])]
    cwd = ROOT / spec.get("test_directory", spec["directory"])
    python = str(cwd / ".venv/bin/python") if (cwd / ".venv/bin/python").exists() else sys.executable
    command = ([python, "oracle/test_oracle.py"] if project == "kubectl-ai" else
               [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *spec["tests"]])
    commands = [(cwd, command)]
    if project == "commonly":
        commands.extend([(cwd, ["node", "packages/integration-sdk/tests/contract.test.js"]),
                         (cwd, ["node", "--test", "frontend/scripts/generate-seo-pages.test.mjs",
                                "frontend/scripts/nginx-routing.test.mjs"])])
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", choices=RANGES, help="Select a range; repeatable")
    parser.add_argument("--regression", action="store_true", help="Also run existing offline regressions")
    parser.add_argument("--timeout", type=float, default=120, help="Per-command timeout in seconds")
    parser.add_argument("--report", type=Path, help="Write JSON results to this file")
    args = parser.parse_args(argv)
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be finite and positive")
    results = []

    def record(project, check, status, detail, started, command=None, cwd=None, returncode=None):
        result = dict(project=project, check=check, status=status, detail=detail,
                      seconds=round(time.monotonic() - started, 3))
        if command:
            result["command"] = command
            result["cwd"] = str(cwd.relative_to(ROOT))
            result["returncode"] = returncode
        results.append(result)
        print(f"{status:7} {project}: {check} — {detail}", flush=True)

    for project in dict.fromkeys(args.project or RANGES):
        spec = RANGES[project]
        cwd = ROOT / spec["directory"]
        started = time.monotonic()
        files = [*spec.get("sources", []), *spec.get("scripts", []), *spec.get("compose", [])]
        missing = [name for name in files if not (cwd / name).exists()]
        record(project, "source files", "FAIL" if missing else "PASS",
               "Missing: " + ", ".join(missing) if missing else f"{len(files)} required source paths present", started)
        needed = ["docker", "bash", *spec.get("tools", [])]
        missing_tools = [tool for tool in needed if not shutil.which(tool)]
        record(project, "host tools", "BLOCKED" if missing_tools else "PASS",
               "Missing: " + ", ".join(missing_tools) if missing_tools else ", ".join(needed), started)
        commands = [("startup syntax", ["bash", "-n", script]) for script in spec.get("scripts", [])]
        commands.extend(("compose " + name, ["docker", "compose", "--env-file", os.devnull,
                         "-f", name, "config", "--no-interpolate", "--no-env-resolution", "--format", "json"])
                        for name in spec.get("compose", []))
        for label, command in commands:
            started = time.monotonic()
            status, stdout, stderr, returncode = run_command(command, cwd, args.timeout)
            detail = "Syntax valid"
            if label.startswith("compose ") and status == "PASS":
                try:
                    status, detail = build_inputs(json.loads(stdout))
                except (ValueError, KeyError, TypeError) as exc:
                    status, detail = "FAIL", f"Invalid Compose JSON: {exc}"
            elif status != "PASS":
                detail = (stderr or stdout)[-2000:].strip()
                if "unknown flag" in detail:
                    status, detail = "BLOCKED", "Compose needs --no-interpolate, --no-env-resolution and --format json"
            record(project, label, status, detail, started, command, cwd, returncode)
        if args.regression:
            for test_cwd, command in regression_commands(project, spec, cwd):
                started = time.monotonic()
                dependency = None
                if command[1:3] == ["-m", "pytest"]:
                    available, _, _, _ = run_command([command[0], "-c", "import pytest"], test_cwd, args.timeout)
                    if available != "PASS":
                        dependency = f"pytest unavailable in {command[0]}"
                elif project == "Inbox_Zero_Mail_Agent" and not (test_cwd / command[1]).is_file():
                    dependency = "Inbox test dependencies missing; prepare the project's locked dependencies"
                if dependency:
                    record(project, "offline regression", "BLOCKED", dependency, started, command, test_cwd)
                    continue
                status, stdout, stderr, returncode = run_command(command, test_cwd, args.timeout)
                detail = (stderr or stdout)[-2000:].strip()
                if status == "PASS":
                    summaries = re.findall(r"[^\n]*\b\d+(?:/\d+)? (?:passed|checks green)[^\n]*", stdout)
                    detail = "; ".join(s.strip() for s in summaries) or "Existing regression command passed"
                record(project, "offline regression", status, detail, started, command, test_cwd, returncode)
        else:
            record(project, "offline regression", "NOT_RUN", "Select with --regression", time.monotonic())

    failed = any(r["status"] in ("FAIL", "BLOCKED") for r in results)
    report = dict(scope="deployment-preflight", status="FAIL" if failed else "PASS", results=results,
                  deployment={"build": "NOT_RUN", "startup_readiness": "NOT_RUN",
                              "local_integration": "NOT_RUN", "model_episode": "NOT_RUN"})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print("Preflight: " + report["status"] + ". Build, startup/readiness, local integration and model episodes: NOT_RUN.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
