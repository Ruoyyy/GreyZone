#!/usr/bin/env python3
"""Run one mail red-team framework at a time and collect authoritative Oracle scores."""
import argparse
import codecs
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from common import (APP, ROOT, EXPERIMENT, RESULTS, FRAMEWORKS, SCENARIOS, app_env,
                    campaign_lock, configuration, now, read_json, revision, safe_name, scenario, write_json)
from environment import assert_no_watcher, backup_and_reset, save_baseline
from progress import Progress
from usage import framework_usage
from summarize import summarize, validate_result


REPAIR_FILES = {"redteam/mail_protocol.py", "redteam/pyrit/run.py", "redteam/dreadnode/mail_campaign.py",
    "experiment/scripts/summarize.py", "experiment/scripts/run.py", "experiment/scripts/test_experiment.py",
    "redteam/pyrit/test_integration.py", "redteam/dreadnode/test_campaign.py"}


def accept_resume_repair(manifest, current):
    previous = manifest["configuration"]
    changed = {key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)}
    if changed - REPAIR_FILES:
        raise ValueError("Resume repair cannot accept changed models, scenarios or other configuration: "
                         + ", ".join(sorted(changed - REPAIR_FILES)))
    manifest.setdefault("repairs", []).append({"accepted_at": now(),
        "files": {key: {"before": previous.get(key), "after": current.get(key)} for key in sorted(changed)}})
    manifest["configuration"] = current


def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def start(command, cwd, env, path, progress, descriptor=None):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
        pass_fds=(descriptor,) if descriptor is not None else ())

    def pump():
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        with path.open("ab") as stream:
            while data := os.read(process.stdout.fileno(), 4096):
                stream.write(data)
                stream.flush()
                progress.forward(decoder.decode(data))
            progress.forward(decoder.decode(b"", final=True))
        process.stdout.close()

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return process, thread


def command_for(framework, selected):
    directory = ROOT / "redteam" / framework
    if framework == "promptfoo":
        return ["node", "run.mjs", "--scenario", selected], directory
    if framework == "dreadnode":
        return ["bash", "run.sh", "--scenario", selected, "--print"], directory
    return [str(directory / ".venv/bin/python"), "run.py", "--scenario", selected], directory


def native_directories(framework):
    path = ROOT / ".tmp" / framework
    return set(path.iterdir()) if path.exists() else set()


def archive_native(framework, before, destination):
    destination.mkdir(exist_ok=True)
    for path in native_directories(framework) - before:
        shutil.copytree(path, destination / path.name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("cache"))


def archive_conversation(path):
    from email import policy
    from email.parser import BytesParser
    from summarize import mail_events
    entries = []
    for event in mail_events(path):
        if event.get("event") == "submit_intent":
            entries.append({"direction": "attack", "at": event["at"], "raw": event["eml"]})
    mailbox = path / "mail"
    index = mailbox / "log.jsonl"
    if index.exists():
        for line in index.read_text().splitlines():
            delivery = json.loads(line)
            file = mailbox / (delivery["deliveryId"] + ".eml")
            if file.exists():
                raw = file.read_bytes()
                message = BytesParser(policy=policy.default).parsebytes(raw)
                entries.append({"direction": "outbound", "at": delivery["receivedAt"],
                    "delivery_id": delivery["deliveryId"], "subject": str(message["Subject"]),
                    "raw": raw.decode("utf-8", errors="replace")})
    write_json(path / "conversation.json", sorted(entries, key=lambda e: e["at"]))


def preflight(framework):
    assert_no_watcher()
    command, _ = command_for(framework, "knowledge-leak")
    dependency = (ROOT / "redteam/promptfoo/node_modules/.bin/promptfoo" if framework == "promptfoo"
                  else ROOT / "redteam" / framework / ".venv/bin/python")
    if framework != "dreadnode" and not dependency.exists():
        raise ValueError(f"Install {framework} dependencies using redteam/{framework}/README.md first")
    if framework == "dreadnode" and not shutil.which("dn"):
        raise ValueError("Dreadnode CLI is not installed")
    if framework == "promptfoo":
        directory = ROOT / "redteam/promptfoo"
        check = subprocess.run(["node", "--input-type=module", "-e",
            "import fs from 'node:fs';import YAML from 'yaml';"
            "const c=YAML.parse(fs.readFileSync('promptfooconfig.yaml','utf8'));"
            "process.exit(c.redteam.numTests===1 && c.redteam.plugins.length===1 ? 0 : 1);"], cwd=directory)
        if check.returncode:
            raise ValueError("Configure one Promptfoo test and one policy plugin per attempt")
    elif framework in ("deepteam", "pyrit"):
        directory = ROOT / "redteam" / framework
        check = subprocess.run([str(dependency), "-c", "import yaml;from pathlib import Path;"
            "c=yaml.safe_load(Path('config.yaml').read_text());raise SystemExit(0 if c['campaign']['cases']==1 else 1)"], cwd=directory)
        if check.returncode:
            raise ValueError("Configure campaign.cases: 1 before running experiments")
    for path in (APP / ".env", APP / "generated/prisma/client.ts", ROOT / ".tmp/emulate-patched/dist/index.js",
                 APP / ".tmp/customer-mail/.transport-token"):
        if not path.exists():
            raise ValueError(f"Missing prerequisite: {path.relative_to(ROOT)}")


def collect(path, run, timeout, progress):
    progress.stage("collecting-oracle", "Waiting for episode closure and matching mail IDs")
    hold = path / "oracle/hold"
    hold.unlink(missing_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        for candidate in (APP / ".tmp/oracle").glob("result-*.json"):
            result = read_json(candidate)
            if result.get("evaluation", {}).get("runId") == run["run_id"]:
                matches.append((candidate, result))
        if matches:
            if len(matches) != 1:
                raise ValueError("Campaign was split across multiple Oracle episodes")
            candidate, result = matches[0]
            shutil.copy2(candidate, path / "oracle/result.json")
            shutil.copy2(candidate.parent / result["alertsFile"], path / "oracle/alerts.jsonl")
            validate_result(result, run, path)
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("Oracle collection timed out; inspect oracle/console.log and use --collect-only")
        time.sleep(2)


def run_once(args, batch, selected, repeat, descriptor):
    parent = batch / selected / f"repeat-{repeat:02d}"
    number = len(list(parent.glob("attempt-*"))) + 1
    path = parent / f"attempt-{number:02d}"
    path.mkdir(parents=True, mode=0o700)
    (path / "oracle").mkdir()
    run = {"run_id": uuid4().hex, "framework": args.framework, "scenario": selected,
           "repeat": repeat, "attempt": number, "started_at": now(), "status": "running"}
    write_json(path / "run.json", run)
    progress = Progress(path / "experiment.log", args.status_interval)
    progress.start()
    watcher = framework = None
    watcher_thread = framework_thread = None
    started = time.monotonic()
    before = native_directories(args.framework)
    try:
        backup_and_reset(EXPERIMENT / "baselines" / args.baseline, path, progress)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "GREYZONE_RUN_ID": run["run_id"],
               "GREYZONE_REVISION": revision(), "GREYZONE_MAIL_EVENTS": str(path / "mail-events.jsonl"),
               "MAIL_CAMPAIGN_LOCK_FD": str(descriptor),
               "MAIL_EXPERIMENT_USAGE": str(path / "framework/usage.jsonl")}
        write_json(path / "oracle/hold", {"run_id": run["run_id"]})
        spec = scenario(selected)
        oracle_env = {**os.environ, **app_env(), "GREYZONE_RUN_ID": run["run_id"], "GREYZONE_REVISION": revision(),
                      "ORACLE_DIR": str(APP / ".tmp/oracle"), "CUSTOMER_OUTBOX": str(APP / ".tmp/customer-mail"),
                      "ATTACKER_EMAIL": spec["attackerEmail"], "MANAGED_EMAIL": spec["managedEmail"],
                      "EMULATOR_BASE_URL": "http://127.0.0.1:4002",
                      "GREYZONE_ORACLE_READY": str(path / "oracle/ready.json"),
                      "GREYZONE_ORACLE_HOLD": str(path / "oracle/hold"),
                      "EPISODE_IDLE_TIMEOUT": str(args.oracle_idle_timeout)}
        progress.stage("starting-oracle")
        watcher, watcher_thread = start(["node", "--import", "tsx", "scripts/oracle-watch.ts",
            ",".join(scenario(selected)["oracleMonitors"])], APP, oracle_env,
            path / "oracle/console.log", progress)
        run["oracle_pid"] = watcher.pid
        write_json(path / "run.json", run)
        deadline = time.monotonic() + 60
        while not (path / "oracle/ready.json").exists():
            if watcher.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Oracle did not become ready; inspect oracle/console.log")
            time.sleep(0.2)
        progress.stage("running-framework", f"{args.framework} / {selected} / repeat {repeat}")
        command, cwd = command_for(args.framework, selected)
        framework_started = time.monotonic()
        framework, framework_thread = start(command, cwd, env, path / "console.log", progress, descriptor)
        progress.attach(framework)
        try:
            deadline = time.monotonic() + args.run_timeout
            while framework.poll() is None:
                if watcher.poll() is not None:
                    run["oracle_watcher_healthy"] = False
                    raise RuntimeError("Oracle exited before the framework completed")
                if time.monotonic() > deadline:
                    raise TimeoutError("Framework execution deadline exceeded")
                time.sleep(0.2)
            run["returncode"] = framework.returncode
            run["oracle_watcher_healthy"] = watcher.poll() is None
            if not run["oracle_watcher_healthy"]:
                raise RuntimeError("Oracle exited during framework execution")
        finally:
            stop(framework)
            framework_thread.join()
            run["framework_duration_seconds"] = time.monotonic() - framework_started
        archive_native(args.framework, before, path / "framework")
        shutil.copytree(APP / ".tmp/customer-mail", path / "mail", ignore=shutil.ignore_patterns(".transport-token"))
        archive_conversation(path)
        write_json(path / "run.json", run)
        oracle_started = time.monotonic()
        result = collect(path, run, args.oracle_timeout, progress)
        run["oracle_wait_seconds"] = time.monotonic() - oracle_started
        run["episode"] = result["episode"]
        run["status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        run.update(status="invalid", error=f"{type(exc).__name__}: {exc}")
        progress.message(run["error"])
    finally:
        stop(framework)
        stop(watcher)
        if watcher_thread:
            watcher_thread.join()
        archive_native(args.framework, before, path / "framework")
        run.update(finished_at=now(), duration_seconds=time.monotonic() - started,
                   framework_usage=framework_usage(path, args.framework))
        write_json(path / "run.json", run)
        progress.message(f"Attempt {run['status']}: {path}")
        progress.stop()
        summarize(batch)
    return run["status"] == "completed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", choices=FRAMEWORKS)
    parser.add_argument("--scenario", choices=sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir()))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline", type=safe_name)
    parser.add_argument("--save-baseline", type=safe_name)
    parser.add_argument("--resume", type=safe_name)
    parser.add_argument("--accept-repair", action="store_true",
                        help="On resume, audit and accept only the no-reply handling/validator repair files")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--run-timeout", type=int, default=14400)
    parser.add_argument("--oracle-timeout", type=int, default=1200)
    parser.add_argument("--oracle-idle-timeout", type=int, default=60)
    parser.add_argument("--status-interval", type=float, default=30)
    args = parser.parse_args()
    os.umask(0o077)
    for value in (args.repeats, args.run_timeout, args.oracle_timeout, args.oracle_idle_timeout, args.status_interval):
        if not math.isfinite(value) or value <= 0:
            parser.error("Repeat counts, timeouts and intervals must be positive and finite")
    if args.oracle_timeout <= args.oracle_idle_timeout:
        parser.error("--oracle-timeout must exceed --oracle-idle-timeout")
    descriptor = campaign_lock()
    try:
        if args.save_baseline:
            print(save_baseline(args.save_baseline))
            return 0
        if not args.framework:
            parser.error("--framework is required")
        if args.resume:
            batch = RESULTS / args.framework / args.resume
            manifest = read_json(batch / "manifest.json")
            current = configuration()
            if manifest["configuration"] != current:
                if not args.accept_repair:
                    raise ValueError("Configuration changed since this batch started; use --accept-repair only for the documented no-reply fix")
                accept_resume_repair(manifest, current)
                write_json(batch / "manifest.json", manifest)
            for key in ("baseline", "repeats", "oracle_idle_timeout", "oracle_timeout", "run_timeout"):
                setattr(args, key, manifest[key])
            selected = manifest["scenarios"]
        else:
            if args.accept_repair:
                parser.error("--accept-repair requires --resume")
            if args.collect_only:
                parser.error("--collect-only requires --resume")
            if not args.baseline:
                parser.error("--baseline is required")
            preflight(args.framework)
            if not (EXPERIMENT / "baselines" / args.baseline / "manifest.json").exists():
                raise ValueError("Baseline missing; run --save-baseline first")
            selected = [args.scenario] if args.scenario else sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir())
            batch = RESULTS / args.framework / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid4().hex[:8])
            batch.mkdir(parents=True)
            write_json(batch / "manifest.json", {"framework": args.framework, "created_at": now(),
                "revision": revision(), "configuration": configuration(), "scenarios": selected,
                **{key: getattr(args, key) for key in ("baseline", "repeats", "oracle_idle_timeout", "oracle_timeout", "run_timeout")}})
        print(f"Batch: {batch}", flush=True)
        if args.collect_only:
            for file in sorted(batch.glob("*/repeat-*/attempt-*/run.json")):
                run = read_json(file)
                if run["status"] == "completed":
                    continue
                progress = Progress(file.parent / "experiment.log", args.status_interval)
                progress.start()
                try:
                    collect(file.parent, run, args.oracle_timeout, progress)
                    run.update(original_status=run["status"], original_error=run.get("error"), status="completed", error=None,
                               finished_at=now(), duration_clock="wall_clock_recovery")
                    from datetime import datetime
                    run["duration_seconds"] = (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
                    write_json(file, run)
                finally:
                    progress.stop()
                    summarize(batch)
            return 0
        preflight(args.framework)
        for name in selected:
            for repeat in range(1, args.repeats + 1):
                summaries = summarize(batch)
                if any(r["scenario"] == name and r["repeat"] == repeat and r["status"] == "completed"
                       for r in summaries[1]):
                    continue
                if not run_once(args, batch, name, repeat, descriptor):
                    return 1
        return 0
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Experiment stopped: {exc}", file=sys.stderr)
        sys.exit(1)
