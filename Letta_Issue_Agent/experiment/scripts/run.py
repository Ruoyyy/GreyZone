#!/usr/bin/env python3
"""Run one framework serially, archive its outputs, and summarize Oracle scores."""
import argparse
import codecs
import contextlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

from baseline import save_baseline, verify_baseline, restore_baseline
from common import (ROOT, RESULTS, SCENARIOS, FRAMEWORKS, campaign_lock, compose,
                    configuration_hashes, framework_completed, now, read_json, safe_name, write_json)
from summarize import oracle_score, summarize
from usage import framework_usage
from progress import Progress


class Gitea:
    def __init__(self, url, repo):
        self.url = url.rstrip("/") + "/api/v1/repos/" + repo

    def get(self, suffix):
        token = (ROOT / "shared/mcp-tokens/mcp-l1.token").read_text().strip()
        request = urllib.request.Request(self.url + suffix,
                                         headers={"Authorization": "token " + token})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def pages(self, suffix):
        rows = []
        for page in range(1, 10001):
            batch = self.get(f"{suffix}{'&' if '?' in suffix else '?'}limit=50&page={page}")
            rows.extend(batch)
            if len(batch) < 50:
                return rows
        raise RuntimeError("Gitea pagination limit exceeded")

    def issues(self):
        return {int(row["number"]): row for row in self.pages("/issues?state=all&type=issues")
                if row.get("pull_request") is None}


def wait_ready(api, timeout=180):
    published = compose("port", "redteam-mcp", "8080", capture_output=True, text=True).stdout.strip()
    address = published.splitlines()[0].replace("0.0.0.0:", "127.0.0.1:").replace("[::]:", "[::1]:")
    health_url = f"http://{address}/healthz"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            api.issues()
            with urllib.request.urlopen(health_url, timeout=5) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(3)
    raise TimeoutError("Gitea or MCP is not ready")


def command_for(framework, scenario, lock_fd):
    directory = ROOT / "redteam" / framework
    if framework == "promptfoo":
        return ["npm", "run", "redteam", "--", "--scenario", scenario], directory
    if framework == "dreadnode":
        return ["bash", str(directory / "run.sh"), "--scenario", scenario, "--print"], ROOT
    python = directory / ".venv/bin/python"
    if framework == "deepteam":
        return [str(python), str(directory / "run.py"), "--scenario", scenario], ROOT
    # PyRIT acquires the shared lock internally. Reuse the parent's open lock
    # rather than releasing it during execution or changing the redteam runner.
    bootstrap = f"""
import os, runpy, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'redteam')!r})
import run_locked
run_locked.acquire = lambda: os.dup({lock_fd})
script = sys.argv.pop(1)
sys.path.insert(0, os.path.dirname(script))
import runtime
original_initialize = runtime.initialize
async def initialize():
    await original_initialize()
    sys.path.insert(0, {str(ROOT / 'experiment/scripts')!r})
    from usage import install_pyrit_usage_hook
    install_pyrit_usage_hook(Path(os.environ['EXPERIMENT_USAGE_FILE']))
runtime.initialize = initialize
runpy.run_path(script, run_name='__main__')
"""
    return [str(python), "-c", bootstrap, str(directory / "run.py"), "--scenario", scenario], ROOT


def execute(command, cwd, env, log, lock_fd, timeout, progress=None):
    with log.open("wb") as stream, selectors.DefaultSelector() as selector:
        child_env = {**env, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, pass_fds=(lock_fd,),
                                   start_new_session=True)
        if progress is not None:
            progress.attach(process)
        selector.register(process.stdout, selectors.EVENT_READ)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        deadline = time.monotonic() + timeout

        def forward(data, final=False):
            text = decoder.decode(data, final=final)
            if progress is not None:
                progress.forward(text)
            else:
                sys.stdout.write(text)
                sys.stdout.flush()

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                events = selector.select(timeout=min(0.2, remaining))
                for key, _ in events:
                    data = os.read(key.fd, 65536)
                    if data:
                        stream.write(data)
                        stream.flush()
                        forward(data)
                    else:
                        selector.unregister(key.fileobj)
                if process.poll() is not None and not events:
                    break
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
            forward(b"", final=True)
            return code
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            process.stdout.close()


def output_snapshot(framework):
    directory = ROOT / "redteam" / framework
    if framework in ("deepteam", "pyrit", "dreadnode"):
        return {p for p in (directory / "results").glob("*") if p.is_dir()}
    if framework == "promptfoo":
        paths = list((directory / "promptfoo-results").glob("*"))
        paths += [directory / name for name in ("scenario-config.yaml", "redteam.yaml")]
        return {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in paths if p.is_file()}
    return {}


def collect_framework(framework, before, destination):
    destination.mkdir(exist_ok=True)
    after = output_snapshot(framework)
    if framework in ("deepteam", "pyrit", "dreadnode"):
        for path in sorted(after - before):
            shutil.copytree(path, destination / path.name)
    elif framework == "promptfoo":
        for path, stamp in after.items():
            if before.get(path) != stamp:
                shutil.copy2(path, destination / path.name)
    # Native print output and the actual conversation are archived separately.


def collect_oracle(api, issue, run, destination, timeout, progress=None):
    if progress is not None:
        progress.stage("waiting-for-oracle", f"issue={issue} | timeout={timeout}s")
    destination.mkdir(exist_ok=True)
    deadline = time.monotonic() + timeout
    source = f"/home/runner/letta-state/logs/result-{issue}.json"
    while time.monotonic() < deadline:
        result = subprocess.run(["docker", "compose", "exec", "-T", "runner", "cat", source],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            evaluation = data.get("evaluation", {})
            # A slow framework grader may exit after Oracle finalization. Compare
            # against the actual conversation, rather than framework exit time.
            comments = api.pages(f"/issues/{issue}/comments")
            activity = [c["created_at"] for c in comments
                        if c.get("user", {}).get("login") in ("alice", "gitea-actions")]
            latest = max((datetime.fromisoformat(t.replace("Z", "+00:00")) for t in activity),
                         default=datetime.fromisoformat(run["started_at"]))
            closed_at = datetime.fromisoformat(evaluation["closedAt"].replace("Z", "+00:00"))
            if (evaluation.get("runId") == run["run_id"] and
                    closed_at >= latest and
                    api.get(f"/issues/{issue}")["state"] == "closed"):
                write_json(destination / "result.json", data)
                if progress is not None:
                    progress.stage("collecting-oracle-evidence", f"issue={issue}")
                for name, target in ((f"result-{issue}", "result.txt"),
                                     (f"alerts-{issue}", "alerts.jsonl"),
                                     (f"issue-{issue}.log", "issue.log")):
                    compose("cp", f"runner:/home/runner/letta-state/logs/{name}",
                            str(destination / target), stdout=subprocess.DEVNULL)
                oracle_score(data, run["scenario"], issue, run["run_id"])
                return
        time.sleep(5)
    raise TimeoutError("Final Oracle result was not collected before the deadline")


def validate_conversation(comments, scenario):
    max_turns = read_json(SCENARIOS / scenario / "scenario.json")["maxTurns"]
    attacks = 1 + sum(c.get("user", {}).get("login") == "alice" for c in comments)
    if attacks > max_turns:
        raise RuntimeError(f"Attack budget exceeded: {attacks} > {max_turns}")
    last_attack = max((int(c["id"]) for c in comments
                       if c.get("user", {}).get("login") == "alice"), default=0)
    last_reply = max((int(c["id"]) for c in comments
                      if c.get("user", {}).get("login") == "gitea-actions"
                      and "Letta Code finished" in c.get("body", "")), default=-1)
    if last_reply <= last_attack:
        raise RuntimeError("The final attack has no completed target reply")


def run_once(args, batch, scenario, repeat, lock_fd):
    started = time.monotonic()
    directory = batch / scenario / f"repeat-{repeat:02d}"
    attempt = max([int(p.name.split("-")[-1]) for p in directory.glob("attempt-*")] or [0]) + 1
    path = directory / f"attempt-{attempt:02d}"
    path.mkdir(parents=True)
    run = {"framework": args.framework, "scenario": scenario, "repeat": repeat,
           "attempt": attempt, "run_id": uuid4().hex, "baseline": args.baseline,
           "oracle_idle_timeout_seconds": args.oracle_idle_timeout,
           "started_at": now(), "status": "running"}
    write_json(path / "run.json", run)
    framework_before = output_snapshot(args.framework)
    env = dict(os.environ)
    env.pop("PROMPTFOO_GITEA_ISSUE", None)
    env["EXPERIMENT_USAGE_FILE"] = str(path / "framework/usage.jsonl")
    monitor, = read_json(SCENARIOS / scenario / "scenario.json")["oracleMonitors"]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env.update(ORACLE_ENABLE=monitor, ORACLE_SCENARIO_ID=scenario,
               GREYZONE_RUN_ID=run["run_id"], GREYZONE_REVISION=revision)
    if args.oracle_idle_timeout is not None:
        env["EPISODE_IDLE_TIMEOUT"] = str(args.oracle_idle_timeout)
    api = Gitea(args.web_url, args.repo)
    progress = Progress(path / "experiment.log", args.status_interval)
    progress.start()
    try:
        idle_window = f"{args.oracle_idle_timeout}s" if args.oracle_idle_timeout is not None else "configured"
        progress.stage("restoring-baseline", f"baseline={args.baseline} | oracle_idle_timeout={idle_window}")
        restore_baseline(args.baseline, env)
        progress.stage("waiting-for-services")
        wait_ready(api)
        issues_before = api.issues()
        command, cwd = command_for(args.framework, scenario, lock_fd)
        run.update(command=command, cwd=str(cwd), revision=revision)
        write_json(path / "run.json", run)
        execution_error = None
        run["framework_started_at"] = now()
        framework_started = time.monotonic()
        progress.stage("running-framework",
                       f"framework={args.framework} | scenario={scenario} | repeat={repeat}/{args.repeats}")
        try:
            run["returncode"] = execute(command, cwd, env, path / "console.log", lock_fd,
                                        args.run_timeout, progress)
        except (OSError, subprocess.SubprocessError) as exc:
            execution_error = str(exc)
            run.update(returncode=-1, execution_error=execution_error)
        finally:
            run["framework_finished_at"] = now()
            run["framework_duration_seconds"] = round(time.monotonic() - framework_started, 3)
            progress.stage("collecting-framework-results",
                           f"exit_code={run.get('returncode', 'interrupted')} | elapsed={run['framework_duration_seconds']}s")
            collect_framework(args.framework, framework_before, path / "framework")
        issues_after = api.issues()
        new_issues = sorted(issues_after.keys() - issues_before.keys())
        write_json(path / "issues.json", [issues_after[i] for i in new_issues])
        if len(new_issues) != 1:
            raise RuntimeError(f"Expected one new issue, found {len(new_issues)}")
        issue, = new_issues
        run["issue"] = issue
        write_json(path / "run.json", run)
        try:
            collect_oracle(api, issue, run, path / "oracle", args.oracle_timeout, progress)
        finally:
            comments = api.pages(f"/issues/{issue}/comments")
            write_json(path / "comments.json", comments)
            write_json(path / "issues.json", [api.get(f"/issues/{issue}")])
        validate_conversation(comments, scenario)
        if not framework_completed(path, run):
            raise RuntimeError(execution_error or f"Framework exited with code {run['returncode']}")
        run["status"] = "completed"
    except Exception as exc:
        run.update(status="invalid", error=str(exc))
    except KeyboardInterrupt:
        run.update(status="interrupted", error="Interrupted; inspect target before resuming")
        raise
    finally:
        try:
            progress.stage("saving-statistics")
            run["token_usage"] = framework_usage(path, args.framework)
            run["finished_at"] = now()
            run["duration_seconds"] = round(time.monotonic() - started, 3)
            write_json(path / "run.json", run)
            summarize(batch)
            details = f"status={run['status']} | elapsed={run['duration_seconds']}s"
            if run["status"] == "completed":
                score, success, _, _ = oracle_score(read_json(path / "oracle/result.json"), scenario)
                details += f" | oracle_score={score} | attack_success={str(success).lower()}"
            elif run.get("error"):
                details += f" | error={run['error']}"
            progress.message(details)
        finally:
            progress.stop()
    return run["status"] == "completed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", choices=FRAMEWORKS)
    parser.add_argument("--scenario", choices=sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir()))
    parser.add_argument("--repeats", type=int, default=3,
                        help="Independent repetitions per scenario (default: 3)")
    parser.add_argument("--baseline", type=safe_name)
    parser.add_argument("--save-baseline", type=safe_name)
    parser.add_argument("--resume", type=safe_name, help="Resume a batch, retrying invalid repetitions")
    parser.add_argument("--collect-only", action="store_true",
                        help="Collect existing attempts in --resume without restoring state or running attacks")
    parser.add_argument("--web-url", default="http://127.0.0.1:3000")
    parser.add_argument("--repo", default="letta-admin/GreyZone")
    parser.add_argument("--run-timeout", type=int, default=14400)
    parser.add_argument("--oracle-timeout", type=int, default=1200,
                        help="Maximum time to collect Oracle results (default: 1200 seconds)")
    parser.add_argument("--oracle-idle-timeout", type=int, default=60,
                        help="Oracle inactivity window for new batches (default: 60 seconds)")
    parser.add_argument("--status-interval", type=float, default=30,
                        help="Seconds between live status updates (default: 30)")
    args = parser.parse_args()
    os.umask(0o077)
    if min(args.repeats, args.run_timeout, args.oracle_timeout, args.oracle_idle_timeout) < 1:
        parser.error("Repetitions and timeouts must be positive")
    if not 0 < args.status_interval < float("inf"):
        parser.error("--status-interval must be finite and positive")
    with campaign_lock() as lock_fd:
        if args.collect_only:
            if not args.framework or not args.resume:
                parser.error("--collect-only requires --framework and --resume")
            batch = RESULTS / args.framework / args.resume
            manifest = read_json(batch / "manifest.json")
            args.repo, args.web_url = manifest["repo"], manifest["web_url"]
            for scenario in manifest["scenarios"]:
                for repeat in range(1, manifest["repeats"] + 1):
                    attempts = sorted((batch / scenario / f"repeat-{repeat:02d}").glob("attempt-*/run.json"))
                    if attempts and not any(valid_attempt(p, scenario) for p in attempts):
                        recover_attempt(args, attempts[-1])
            summaries = summarize(batch)
            print(batch / "summary.csv")
            return 0 if all(s["status"] == "completed" for s in summaries) else 1
        if args.save_baseline:
            api = Gitea(args.web_url, args.repo)
            if any(i["state"] == "open" for i in api.issues().values()):
                parser.error("Close all issues and wait for all jobs before capturing a clean baseline")
            print(save_baseline(args.save_baseline))
            return 0
        if not args.framework or not args.baseline:
            parser.error("--framework and --baseline are required for experiments")
        verify_baseline(args.baseline)
        if args.resume:
            batch = RESULTS / args.framework / args.resume
            manifest = read_json(batch / "manifest.json")
            if manifest["baseline"] != args.baseline or manifest["configuration_hashes"] != configuration_hashes():
                parser.error("Resume requires the original baseline and configuration")
        else:
            name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
            batch = RESULTS / args.framework / name
            manifest = {"framework": args.framework, "baseline": args.baseline,
                "scenarios": [args.scenario] if args.scenario else sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir()),
                "repeats": args.repeats, "created_at": now(),
                "oracle_idle_timeout_seconds": args.oracle_idle_timeout,
                "repo": args.repo, "web_url": args.web_url,
                "configuration_hashes": configuration_hashes()}
            write_json(batch / "manifest.json", manifest)
        args.repo, args.web_url = manifest["repo"], manifest["web_url"]
        args.baseline = manifest["baseline"]
        args.repeats = manifest["repeats"]
        # Old batches retain their original Compose/.env inactivity window.
        args.oracle_idle_timeout = manifest.get("oracle_idle_timeout_seconds")
        print(f"Batch: {batch}", flush=True)
        for scenario in manifest["scenarios"]:
            for repeat in range(1, manifest["repeats"] + 1):
                previous = sorted((batch / scenario / f"repeat-{repeat:02d}").glob("attempt-*/run.json"))
                if any(valid_attempt(p, scenario) for p in previous):
                    continue
                if not run_once(args, batch, scenario, repeat, lock_fd):
                    print("Batch stopped. Inspect the invalid attempt before using --resume.")
                    return 1
        summarize(batch)
        print(batch / "summary.csv")
        return 0


def valid_attempt(path, scenario):
    try:
        run = read_json(path)
        if run.get("status") != "completed" or not framework_completed(path.parent, run):
            return False
        oracle_score(read_json(path.parent / "oracle/result.json"), scenario,
                     run["issue"], run["run_id"])
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def recover_attempt(args, metadata_path):
    """Repair collection using existing issue evidence; never submit another attack."""
    started = time.monotonic()
    path = metadata_path.parent
    metadata = read_json(metadata_path)
    api = Gitea(args.web_url, args.repo)
    issue = metadata.get("issue")
    if issue is None:
        candidates = set()
        if metadata["framework"] == "promptfoo":
            report = read_json(path / "framework" / f"{metadata['scenario']}.json")
            for row in report["results"]["results"]:
                value = row.get("response", {}).get("metadata", {}).get("sessionId")
                if value is not None and str(value).isdigit():
                    candidates.add(int(value))
        else:
            for summary in (path / "framework").glob("*/case-*-summary.json"):
                value = read_json(summary).get("issue_number")
                if value is not None:
                    candidates.add(int(value))
        if len(candidates) != 1:
            raise ValueError("Cannot uniquely identify the existing issue; refusing to guess")
        issue, = candidates
    metadata.setdefault("original_status", metadata["status"])
    metadata.setdefault("original_finished_at", metadata.get("finished_at"))
    if "duration_seconds" in metadata:
        metadata.setdefault("original_duration_seconds", metadata["duration_seconds"])
    if metadata.get("error"):
        metadata.setdefault("original_error", metadata["error"])
    metadata["issue"] = issue
    write_json(metadata_path, metadata)
    progress = Progress(path / "experiment.log", getattr(args, "status_interval", 30))
    progress.start()
    try:
        collect_oracle(api, issue, metadata, path / "oracle", args.oracle_timeout, progress)
        comments = api.pages(f"/issues/{issue}/comments")
        write_json(path / "comments.json", comments)
        write_json(path / "issues.json", [api.get(f"/issues/{issue}")])
        validate_conversation(comments, metadata["scenario"])
        if not framework_completed(path, metadata):
            raise ValueError("Framework execution failed; Oracle evidence was retained")
        metadata.update(status="completed", recovered_at=now())
        metadata.pop("error", None)
    except Exception as exc:
        metadata.update(status="invalid", error=str(exc))
        raise
    finally:
        metadata["recovery_duration_seconds"] = round(
            metadata.get("recovery_duration_seconds", 0) + time.monotonic() - started, 3)
        metadata["finished_at"] = now()
        if metadata.get("started_at"):
            # Recovery spans processes, so the original monotonic clock is gone.
            metadata["duration_seconds"] = round((datetime.fromisoformat(metadata["finished_at"]) -
                datetime.fromisoformat(metadata["started_at"])).total_seconds(), 3)
            metadata["duration_clock"] = "wall_clock_recovery"
        elif "original_duration_seconds" in metadata:
            metadata["duration_seconds"] = round(
                metadata["original_duration_seconds"] + metadata["recovery_duration_seconds"], 3)
        try:
            write_json(metadata_path, metadata)
            progress.message(f"collection status={metadata['status']} | issue={issue}")
        finally:
            progress.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
