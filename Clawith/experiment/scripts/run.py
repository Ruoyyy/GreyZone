#!/usr/bin/env python3
"""Run serial, independently reset Clawith experiments with resumable attempts."""
import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
import signal
from pathlib import Path

from experiment_common import (ROOT, RESULTS, SCENARIOS, FRAMEWORKS, PYTHON, campaign_lock,
    configuration, env_value, now, read_json, revision, safe_name, scenario_data, write_json)
from environment import images, preflight, reset
from process import execute
from progress import Progress
from summarize import classify, collect, load_runs
from baseline import verify as verify_baseline
from diagnostics import archive_diagnostics
from usage import update_usage
import plan


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return number


def command(framework, scenario):
    directory = ROOT / "redteam" / framework
    if framework == "promptfoo":
        return ["node", str(directory / "run.mjs"), "--scenario", scenario]
    python = PYTHON if framework == "dreadnode" else directory / ".venv/bin/python"
    return [str(python), str(directory / "run.py"), "--scenario", scenario]


def native_directories(framework):
    return set((ROOT / ".run" / framework).glob("*"))


def archive(path, framework, previous, run_id, names=None):
    directories = [ROOT / ".run" / framework / name for name in names] if names is not None else native_directories(framework) - previous
    for directory in sorted(directories):
        if directory.is_dir():
            shutil.copytree(directory, path / "framework" / directory.name,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns("cache", "__pycache__"))
    episode_path = ROOT / ".run/oracle/episode.json"
    live = read_json(episode_path) if episode_path.exists() else None
    # A stale result must never be mistaken for a failed new attempt.
    for result_path in (ROOT / ".run/oracle").glob("result-*.json"):
        result = read_json(result_path)
        if result.get("evaluation", {}).get("runId") != run_id:
            continue
        number = result["episode"]
        shutil.copyfile(result_path, path / "oracle/result.json")
        shutil.copyfile(result_path.parent / f"alerts-{number}.jsonl", path / "oracle/alerts.jsonl")
        if live and live.get("n") == number:
            shutil.copyfile(episode_path, path / "oracle/episode.json")
        elif not (path / "oracle/episode.json").exists():
            raise ValueError("Live episode was replaced and no archived episode is available")
    for item in path.rglob("*"):
        item.chmod(0o700 if item.is_dir() else 0o600)


def collect_attempt(path, run, progress, previous=()):
    """Retry collection safely, preserving execution errors and raw Oracle scores."""
    progress.stage("collecting")
    started = time.monotonic()
    try:
        archive(path, run["framework"], set(previous), run["run_id"], run.get("native_paths"))
        for file in (path / "framework").glob("*/completion.json"):
            marker = read_json(file)
            if marker.get("run_id") == run["run_id"] and marker.get("scenario") == run["scenario"]:
                run["framework_status"] = marker.get("framework_status")
                if marker.get("framework_status") == "completed" and marker.get("oracle_status") == "error":
                    run["framework_returncode"] = 0
        if not (path / "oracle/result.json").is_file():
            raise ValueError("Oracle result is not available for this attempt")
        run.pop("collection_error", None)
        run["collection_status"] = "completed"
    except Exception as error:
        run.update(collection_status="error", collection_error=f"{type(error).__name__}: {error}")
    try:
        if not (path / "target-runtime.json").exists() and (path / "events.jsonl").exists():
            live_path = ROOT / ".run/oracle/episode.json"
            live = read_json(live_path) if live_path.exists() else {}
            stored = read_json(path / "oracle/episode.json") if (path / "oracle/episode.json").exists() else {}
            if live.get("runId") != run["run_id"] and (not stored or stored.get("n") != live.get("n")):
                raise ValueError("Live episode was replaced; optional diagnostics are unavailable")
            archive_diagnostics(path, run["started_at"])
        run["diagnostics_status"] = "completed"
        run.pop("diagnostics_error", None)
    except Exception as error:
        run.update(diagnostics_status="warning", diagnostics_error=f"{type(error).__name__}: {error}")
        progress.message("Diagnostic archive warning: " + str(error))
    update_usage(path, run)
    run["oracle_wait_seconds"] = time.monotonic() - started


def attempt(batch, manifest, scenario, repeat, descriptor, args):
    parent = batch / scenario / f"repeat-{repeat:02d}"
    number = max([int(p.name.split("-")[-1]) for p in parent.glob("attempt-*")] or [0]) + 1
    path = parent / f"attempt-{number:02d}"
    for name in ("framework", "oracle"):
        (path / name).mkdir(parents=True, mode=0o700)
    run = {"framework": manifest["framework"], "scenario": scenario, "repeat": repeat,
           "attempt": number, "status": "running", "run_id": str(uuid.uuid4()),
           "revision": manifest["revision"], "started_at": now(),
           "token_usage_status": "unavailable"}
    if manifest.get("schema_version", 1) >= 2:
        run.update(schema_version=2, options=manifest["options"], provenance=manifest["provenance"],
                   attacker_model=manifest["model"], victim_model=manifest.get("victim_model"), stage="resetting")
    if manifest.get("baseline"):
        run["baseline"] = manifest["baseline"]
    write_json(path / "run.json", run)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "CLAWITH_CAMPAIGN_LOCK_FD": str(descriptor),
           "CLAWITH_EXPERIMENT_EVENTS": str(path / "events.jsonl"),
           "GREYZONE_RUN_ID": run["run_id"], "GREYZONE_REVISION": run["revision"]}
    if run.get("attacker_model"):
        env["GREYZONE_ATTACKER_MODEL"] = run["attacker_model"]
    if manifest.get("framework") == "promptfoo" and manifest.get("provenance"):
        env["UV_PYTHON"] = manifest["provenance"]["runtimes"]["promptfoo"]["oracle_executable"]
    env["CLAWITH_EXPERIMENT_USAGE"] = str(path / "framework/usage.jsonl")
    for key, name in (("reply_timeout", "CLAWITH_REPLY_TIMEOUT"), ("settle_timeout", "CLAWITH_SETTLE_TIMEOUT"),
                      ("oracle_timeout", "CLAWITH_ORACLE_TIMEOUT"), ("max_turns", "CLAWITH_MAX_TURNS")):
        if hasattr(args, key):
            env[name] = str(getattr(args, key))
    if manifest.get("baseline"):
        env["CLAWITH_PREPARED_BASELINE"] = str(ROOT / ".run/oracle/restored-prepared.json")
    progress = Progress(path / "experiment.log", interval=args.status_interval)
    progress.start()
    started = time.monotonic()
    previous = native_directories(manifest["framework"])
    interrupted = False
    try:
        progress.message(f"{manifest['framework']} | {scenario} | repeat={repeat}/{manifest['repeats']} | attempt={number}")
        reset_env = {**os.environ, "CLAWITH_SKIP_BUILD": "1",
                     "CLAWITH_CAMPAIGN_LOCK_FD": str(descriptor)}
        reset(progress, lambda cmd, cwd: execute(cmd, cwd, reset_env, descriptor, progress,
              path / "environment.log", args.reset_timeout),
              manifest.get("baseline", {}).get("name"), run["run_id"],
              manifest.get("baseline", {}).get("manifest_sha256"))
        if images() != manifest["images"] or configuration() != manifest["configuration"]:
            raise ValueError("Reset changed images or configuration; inspect the environment and start a new batch")
        progress.stage("framework-running")
        run.update(stage="framework-running", reset_duration_seconds=time.monotonic() - started)
        write_json(path / "run.json", run)
        framework_started = time.monotonic()
        try:
            run["returncode"] = execute(command(manifest["framework"], scenario), ROOT, env,
                descriptor, progress, path / "console.log", args.run_timeout, check=False)
        finally:
            run["framework_duration_seconds"] = time.monotonic() - framework_started
    except KeyboardInterrupt:
        interrupted = True
        run["execution_error"] = "Interrupted by user"
        run["failure_kind"] = "interrupted"
    except Exception as error:
        run["execution_error"] = f"{type(error).__name__}: {error}"
        run["failure_kind"] = "timeout" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else run.get("stage", "execution")
    finally:
        try:
            run["native_paths"] = sorted(p.name for p in native_directories(manifest["framework"]) - previous)
            run["framework_finished_at"] = now()
            write_json(path / "run.json", run)
            collect_attempt(path, run, progress, previous)
            run.update(duration_seconds=time.monotonic() - started, finished_at=now(), stage="finished")
            classify(path, run)
            progress.message(f"{run['status']} | score={run.get('score', 'unavailable')} | {run.get('reason', '')}")
            collect(batch)
        finally:
            progress.stop()
    if interrupted:
        raise KeyboardInterrupt
    return run["status"] == "valid"


def finalization_environment(row):
    saved = row.get("provenance", {}).get("configuration", {})
    current = configuration()
    oracle = lambda config: {k: v for k, v in config.items()
                             if k.startswith("oracle/") or k == "../scripts/oracle_result.py"}
    if not oracle(saved) or oracle(saved) != oracle(current):
        raise ValueError("Oracle code changed; archived evidence can be collected, but finalization cannot be replayed")
    environment = {**os.environ, "GREYZONE_RUN_ID": row["run_id"], "GREYZONE_REVISION": row["revision"],
                   "GREYZONE_ATTACKER_MODEL": row.get("attacker_model", "")}
    runtime = row.get("provenance", {}).get("runtimes", {}).get("promptfoo", {})
    if runtime.get("oracle_executable"):
        environment["UV_PYTHON"] = runtime["oracle_executable"]
    return environment


def recover(args):
    """Collect existing evidence only; never reset or replay an attack."""
    from datetime import datetime
    batch = RESULTS / args.framework / args.resume
    read_json(batch / "manifest.json")
    from contextlib import nullcontext
    with (nullcontext() if getattr(args, "already_locked", False) else campaign_lock()):
        latest = {}
        for file in sorted(batch.glob("*/repeat-*/attempt-*/run.json")):
            row = read_json(file)
            latest[row["scenario"], row["repeat"]] = file
        for file in latest.values():
            row = read_json(file)
            if row.get("status") == "valid" and row.get("diagnostics_status") == "completed":
                continue
            allowed = (0, 100) if args.framework == "promptfoo" else (0,)
            if row.get("framework_returncode", row.get("returncode")) not in allowed or row.get("execution_error"):
                print(f"Cannot recover failed or interrupted execution: {file.parent}")
                continue
            row.setdefault("recovery_history", []).append({"at": now(), "status": row.get("status"),
                "reason": row.get("reason"), "collection_error": row.get("collection_error"),
                "finished_at": row.get("finished_at"), "duration_seconds": row.get("duration_seconds")})
            progress = Progress(file.parent / "experiment.log", interval=args.status_interval)
            progress.start()
            try:
                live_path = ROOT / ".run/oracle/episode.json"
                if (not (file.parent / "oracle/result.json").exists() and live_path.exists()
                        and row.get("framework_status") == "completed"):
                    live = read_json(live_path)
                    if live.get("runId") == row["run_id"] and live.get("state") == "running":
                        environment = finalization_environment(row)
                        budgets = row.get("options", plan.DEFAULTS)
                        environment["CLAWITH_SETTLE_TIMEOUT"] = str(budgets["settle_timeout"])
                        execute(["uv", "run", "--with-requirements", "oracle/requirements.txt", "python", "-m", "oracle.watch", "--close"],
                                ROOT, environment, None, progress, file.parent / "recovery.log", budgets["oracle_timeout"])
                collect_attempt(file.parent, row, progress)
                row.update(finished_at=now(), duration_clock="wall_clock_recovery")
                row["duration_seconds"] = (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()
                classify(file.parent, row)
            except Exception as error:
                row.update(collection_status="error", collection_error=f"{type(error).__name__}: {error}")
                classify(file.parent, row)
            finally:
                progress.stop()
        complete = collect(batch)
        pending = any(r.get("collection_status") == "error" and not r.get("execution_error")
                      and r.get("framework_returncode", r.get("returncode")) in (0, 100) for r in load_runs(batch))
        return 0 if complete else 75 if pending else 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--framework", choices=FRAMEWORKS)
    result.add_argument("--save-baseline", type=safe_name, help="Hard reset, prepare once and save a new baseline")
    result.add_argument("--baseline", type=safe_name, help="Prepared snapshot; defaults to clean for new batches")
    result.add_argument("--fresh-reset", action="store_true", help="Explicitly use hard reset and prepare every attempt")
    result.add_argument("--scenario", choices=sorted(p.name for p in SCENARIOS.iterdir()), action="append")
    result.add_argument("--repeats", type=positive)
    result.add_argument("--resume", type=safe_name)
    result.add_argument("--batch-id", type=safe_name)
    for key in plan.DEFAULTS:
        result.add_argument("--" + key.replace("_", "-"), type=positive)
    result.add_argument("--collect-only", action="store_true", help="Recover artifacts only; no reset or attacks")
    result.add_argument("--series-plan", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--status-interval", type=positive, default=30)
    result.add_argument("--check", action="store_true", help="Read-only prerequisites; no reset or model calls")
    return result


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    os.umask(0o077)
    if args.save_baseline:
        if args.framework or args.resume or args.baseline or args.fresh_reset or args.check:
            cli.error("--save-baseline is a standalone operation")
        preflight()
        plan.options(args)
        with campaign_lock() as descriptor:
            log = ROOT / ".run/baseline-capture.log"
            progress = Progress(log, interval=args.status_interval)
            progress.start()
            try:
                execute([PYTHON, str(ROOT / "experiment/scripts/baseline.py"), "save", args.save_baseline],
                        ROOT, {**os.environ, "CLAWITH_CAMPAIGN_LOCK_FD": str(descriptor)}, descriptor,
                        progress, ROOT / ".run/baseline-capture-console.log", args.reset_timeout)
            finally:
                progress.stop()
        return 0
    if not args.framework:
        cli.error("--framework is required unless saving a baseline")
    if args.baseline and args.fresh_reset:
        cli.error("--baseline and --fresh-reset are mutually exclusive")
    if args.collect_only:
        if not args.resume:
            cli.error("--collect-only requires --resume")
        return recover(args)
    preflight(args.framework)
    scenarios = sorted(set(args.scenario or [p.name for p in SCENARIOS.iterdir() if (p / 'scenario.json').is_file()]))
    for scenario in scenarios:
        scenario_data(scenario)
    if args.framework == "promptfoo":
        config = read_json(ROOT / "redteam/promptfoo/promptfooconfig.json")
        crescendo = [s for s in config["redteam"]["strategies"] if s["id"] == "crescendo"]
        if config["redteam"]["numTests"] != 1 or len(crescendo) != 1:
            raise ValueError("Experiments require one Promptfoo candidate")
    if args.check:
        if args.baseline:
            verify_baseline(args.baseline)
        print(f"Prerequisites OK | {args.framework} | {len(scenarios)} scenarios")
        return 0
    with campaign_lock() as descriptor:
        batch_id = args.resume or args.batch_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        batch = RESULTS / args.framework / batch_id
        config, image_ids = configuration(), images()
        if args.resume:
            manifest = read_json(batch / "manifest.json")
            if args.repeats is not None and args.repeats != manifest["repeats"]:
                raise ValueError("Resume repetition count differs from saved plan")
            plan.options(args, manifest.get("options"))
            if manifest.get("provenance"):
                plan.verify(manifest["provenance"], [args.framework])
            if (manifest["framework"] != args.framework or manifest["configuration"] != config
                    or manifest["images"] != image_ids or manifest["revision"] != revision()):
                raise ValueError("Batch configuration, revision or image changed; start a new batch")
            if args.scenario and scenarios != manifest["scenarios"]:
                raise ValueError("Resume scenarios differ from the saved plan")
            saved = manifest.get("baseline")
            if ((args.baseline and (not saved or args.baseline != saved["name"]))
                    or (args.fresh_reset and saved)):
                raise ValueError("Resume baseline differs from the saved plan")
            if saved and verify_baseline(saved["name"]) != saved:
                raise ValueError("Batch baseline changed")
        else:
            budgets = plan.options(args)
            provenance = plan.snapshot([args.framework])
            if args.series_plan:
                series = read_json(args.series_plan)
                plan.verify(series["provenance"], [args.framework])
                if budgets != series["options"]:
                    raise ValueError("Batch budgets differ from frozen series")
            baseline = None if args.fresh_reset else verify_baseline(args.baseline or "clean")
            batch.mkdir(parents=True, exist_ok=False, mode=0o700)
            manifest = {"schema_version": 2, "created_at": now(), "framework": args.framework,
                        "scenarios": scenarios, "repeats": args.repeats or 3, "revision": revision(),
                        "images": image_ids, "configuration": config, "max_turns": args.max_turns,
                        "model": provenance["runtimes"][args.framework]["attacker_model"],
                        "options": budgets, "provenance": provenance}
            if baseline:
                saved_baseline = read_json(ROOT / "experiment/baselines" / baseline["name"] / "manifest.json")
                manifest["victim_models"] = saved_baseline.get("victim_models", {})
            manifest["victim_model"] = manifest.get("victim_models", {}).get("Morty", {}).get("primary") or env_value("OPENAI_MODEL")
            if baseline:
                manifest["baseline"] = baseline
            write_json(batch / "manifest.json", manifest)
        print(f"Batch: {batch}", flush=True)
        collect(batch)
        if args.resume:
            rows = load_runs(batch)
            if any(r.get("collection_status") == "error" and not r.get("execution_error")
                   and r.get("framework_returncode", r.get("returncode")) in (0, 100) for r in rows):
                args.already_locked = True
                recover(args)
                if any(r.get("collection_status") == "error" for r in load_runs(batch)):
                    print("Collection is still incomplete; use --collect-only before retrying attacks")
                    return 75
        valid = {(r["scenario"], r["repeat"]) for r in load_runs(batch) if r["status"] == "valid"}
        for scenario in manifest["scenarios"]:
            for repeat in range(1, manifest["repeats"] + 1):
                if (scenario, repeat) not in valid and not attempt(batch, manifest, scenario, repeat, descriptor, args):
                    print(f"Batch stopped. Resume: --framework {args.framework} --resume {batch_id}", flush=True)
                    latest = sorted((batch / scenario / f"repeat-{repeat:02d}").glob("attempt-*/run.json"))[-1]
                    row = read_json(latest)
                    return 75 if (row.get("collection_status") == "error" and not row.get("execution_error")
                                  and row.get("framework_returncode", row.get("returncode")) in (0, 100)) else 1
        print(f"Complete: {batch / 'summary.csv'}", flush=True)
    return 0


def terminate(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; saved attempts can be resumed", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Experiment error: {error}", file=sys.stderr)
        raise SystemExit(1)
