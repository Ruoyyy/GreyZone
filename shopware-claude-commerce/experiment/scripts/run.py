#!/usr/bin/env python3
"""Run or resume one framework's serial Shopware experiments."""
import argparse
import codecs
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid

from experiment_common import (ROOT, RESULTS, SCENARIOS, PYTHON, FRAMEWORKS, attacker_model,
                    campaign_lock, configuration, now, read_json, revision, safe_name, victim_model, write_json)
from environment import check_scenarios, image_id, preflight, reset
from progress import Progress
from summarize import classify, collect, load_runs
from usage import framework_usage


def execute(command, cwd, env, descriptor, progress, log_path, timeout, *, check=True, keep_children=False):
    """Drain bytes continuously, keep heartbeats independent, and terminate the whole task on failure."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        pass_fds=(descriptor,) if descriptor is not None else ())
    progress.attach(process)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    started = time.monotonic()
    stop_tree = True
    try:
        with selectors.DefaultSelector() as selector, log_path.open("ab") as log:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Command exceeded {timeout}s: {command[0]}")
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(chunk)
                    log.flush()
                    progress.forward(decoder.decode(chunk))
            progress.forward(decoder.decode(b"", final=True))
        code = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        if check and code:
            raise RuntimeError(f"Command exited {code}: {' '.join(command)}")
        stop_tree = not (keep_children and code == 0)
        return code
    finally:
        if stop_tree:
            # Framework descendants must not continue writing after timeout/failure.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close()


def command(framework, scenario):
    here = ROOT / "redteam" / framework
    if framework == "promptfoo":
        return ["node", str(here / "run.mjs"), "--scenario", scenario]
    python = PYTHON if framework == "dreadnode" else here / ".venv/bin/python"
    return [str(python), str(here / "run.py"), "--scenario", scenario]


def native_directories(framework):
    return set((ROOT / ".run" / framework).glob("*"))


def archive(path, framework, previous):
    for directory in native_directories(framework) - previous:
        if directory.is_dir():
            shutil.copytree(directory, path / "framework" / directory.name,
                            ignore=shutil.ignore_patterns("cache", "__pycache__"))
    results = list((path / "oracle").glob("result-*.json"))
    if len(results) == 1:
        result = read_json(results[0])
        alerts = results[0].parent / result["alertsFile"]
        shutil.copyfile(alerts, path / "oracle/alerts.jsonl")
        result["alertsFile"] = "alerts.jsonl"
        result["evaluation"]["evidenceFiles"] = [
            "alerts.jsonl" if filename == alerts.name else filename
            for filename in result["evaluation"].get("evidenceFiles", [])]
        write_json(path / "oracle/result.json", result)
        # Native metadata retains the original result path as provenance.
        results[0].unlink()
        alerts.unlink()
    for item in path.rglob("*"):
        item.chmod(0o700 if item.is_dir() else 0o600)


def attempt(batch, manifest, scenario, repeat, descriptor, args):
    parent = batch / scenario / f"repeat-{repeat:02d}"
    number = max([int(p.name.split("-")[-1]) for p in parent.glob("attempt-*")] or [0]) + 1
    path = parent / f"attempt-{number:02d}"
    (path / "framework").mkdir(parents=True, mode=0o700)
    (path / "oracle").mkdir(mode=0o700)
    run = {"framework": manifest["framework"], "scenario": scenario, "repeat": repeat,
           "attempt": number, "status": "running", "run_id": str(uuid.uuid4()),
           "revision": manifest["revision"], "victim_model": manifest["victim_models"]["buyer" if scenario.startswith("buyer-") else "merchant"],
           "attacker_model": manifest["attacker_model"], "started_at": now()}
    write_json(path / "run.json", run)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "SHOPWARE_CAMPAIGN_LOCK_FD": str(descriptor),
           "SHOPWARE_EXPERIMENT_DIR": str(path), "SHOPWARE_EXPERIMENT_USAGE": str(path / "framework/usage.jsonl"),
           "ORACLE_DIR": str(path / "oracle"), "GREYZONE_RUN_ID": run["run_id"],
           "GREYZONE_REVISION": run["revision"], "GREYZONE_VICTIM_MODEL": run["victim_model"],
           "GREYZONE_ATTACKER_MODEL": run["attacker_model"]}
    progress = Progress(path / "experiment.log", interval=args.status_interval)
    progress.start()
    started = time.monotonic()
    previous = native_directories(manifest["framework"])
    interrupted = False
    try:
        progress.message(f"{manifest['framework']} | {scenario} | repeat={repeat}/{manifest['repeats']} | attempt={number} | {path}")
        # Long-lived hosts must not inherit the campaign lock or attempt metadata.
        reset(progress, lambda cmd, cwd: execute(cmd, cwd, os.environ.copy(), None, progress,
              path / "experiment.log", args.reset_timeout, keep_children=True))
        progress.stage("framework-running", "One seller attack followed by one fixed victim task")
        framework_started = time.monotonic()
        try:
            run["returncode"] = execute(command(manifest["framework"], scenario), ROOT, env,
                descriptor, progress, path / "console.log", args.run_timeout, check=False)
        finally:
            run["framework_duration_seconds"] = time.monotonic() - framework_started
    except KeyboardInterrupt:
        interrupted = True
        run["execution_error"] = "Interrupted by user"
    except Exception as error:
        run["execution_error"] = f"{type(error).__name__}: {error}"
    finally:
        progress.stage("collecting", "Archiving native output and inline Oracle evidence")
        collection_started = time.monotonic()
        try:
            archive(path, manifest["framework"], previous)
        except Exception as error:
            run["execution_error"] = f"{run.get('execution_error', '')} Archive failed: {error}".strip()
        usage = framework_usage(path, manifest["framework"])
        run.update(token_usage_status=usage["status"], token_usage_source=usage["source"])
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            run["framework_" + name] = usage.get(name)
        run.update(oracle_wait_seconds=time.monotonic() - collection_started,
                   duration_seconds=time.monotonic() - started, finished_at=now())
        classify(path, run)
        progress.message(f"{run['status']} | score={run.get('score', 'unavailable')} | {run.get('reason', '')}")
        progress.stop()
        collect(batch)
    if interrupted:
        raise KeyboardInterrupt
    return run["status"] == "valid"


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--framework", choices=FRAMEWORKS, required=True)
    result.add_argument("--scenario", choices=sorted(p.name for p in SCENARIOS.iterdir() if (p / "scenario.json").is_file()), action="append")
    result.add_argument("--repeats", type=positive, default=3)
    result.add_argument("--resume", type=safe_name, help="Existing batch ID under this framework")
    result.add_argument("--batch-id", type=safe_name, help="Explicit new batch ID (used by run_all)")
    result.add_argument("--status-interval", type=positive, default=30)
    result.add_argument("--run-timeout", type=positive, default=3600)
    result.add_argument("--reset-timeout", type=positive, default=900)
    result.add_argument("--check", action="store_true", help="Read-only prerequisites; no reset or model calls")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    preflight(args.framework)
    if args.check:
        print(f"Prerequisites OK | image={image_id()} | attacker={attacker_model(args.framework)}")
        return 0
    with campaign_lock() as descriptor:
        batch_id = args.resume or args.batch_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        batch = RESULTS / args.framework / batch_id
        config = configuration()
        image = image_id()
        if args.resume:
            manifest = read_json(batch / "manifest.json")
            if manifest["framework"] != args.framework or manifest["configuration"] != config or manifest["image_id"] != image:
                raise ValueError("Batch configuration or container image changed; start a new batch")
            if (args.scenario and sorted(set(args.scenario)) != manifest["scenarios"]):
                raise ValueError("Resume scenarios differ from the saved plan")
        else:
            batch.mkdir(parents=True, exist_ok=False, mode=0o700)
            manifest = {"schema_version": 1, "created_at": now(), "framework": args.framework,
                        "scenarios": sorted(set(args.scenario or [p.name for p in SCENARIOS.iterdir() if (p / 'scenario.json').is_file()])),
                        "repeats": args.repeats, "revision": revision(), "image_id": image,
                        "victim_models": {role: victim_model(role) for role in ("buyer", "merchant")}, "attacker_model": attacker_model(args.framework),
                        "configuration": config}
            write_json(batch / "manifest.json", manifest)
        print(f"Batch: {batch}", flush=True)
        check_scenarios(manifest["scenarios"])
        collect(batch)
        valid = {(r["scenario"], r["repeat"]) for r in load_runs(batch) if r["status"] == "valid"}
        for scenario in manifest["scenarios"]:
            for repeat in range(1, manifest["repeats"] + 1):
                if (scenario, repeat) in valid:
                    continue
                if not attempt(batch, manifest, scenario, repeat, descriptor, args):
                    print(f"Batch stopped after an invalid attempt. Resume: --framework {args.framework} --resume {batch_id}", flush=True)
                    return 1
        print(f"Complete: {batch / 'summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Experiment interrupted; saved attempts can be resumed", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Experiment error: {error}", file=sys.stderr)
        raise SystemExit(1)
