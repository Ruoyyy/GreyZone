#!/usr/bin/env python3
"""Serial Commonly experiments with immutable baselines and evidence-based recovery."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from experiment_common import (DEFAULTS, FRAMEWORKS, ORACLE, PYTHON, RESULTS, ROOT, SCENARIOS,
    child_env, configuration, interpreter, now, options, positive, read_json, run_lock,
    safe_name, scenario_data, write_json)
import baseline
import diagnostics
from environment import preflight
import plan
from process import execute
from progress import Progress
from summarize import classify, collect


def terminate(signum, frame):
    raise KeyboardInterrupt(f"Received signal {signum}")


def add_options(parser):
    parser.add_argument("--baseline", type=safe_name)
    parser.add_argument("--fresh-reset", action="store_true")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--resume", type=safe_name)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--status-interval", type=positive, default=30)
    for key in DEFAULTS:
        parser.add_argument("--" + key.replace("_", "-"), type=int if key == "max_steps" else positive)


def manifest_for(args, framework, provenance=None, reference=None):
    scenarios = args.scenario or list(SCENARIOS)
    if len(set(scenarios)) != len(scenarios):
        raise ValueError("Duplicate scenarios")
    specs = {}
    for scenario in scenarios:
        data, _ = scenario_data(scenario)
        specs[scenario] = {k: v for k, v in data.items() if k != "_dir"}
    budgets = options(args)
    if not args.fresh_reset and reference is None:
        reference = baseline.verify(args.baseline or "clean")
    return {"schema_version": 1, "created_at": now(), "framework": framework,
            "scenarios": scenarios, "scenario_specs": specs, "repeats": args.repeats or 3,
            "baseline": reference, "reset_mode": "fresh" if args.fresh_reset else "baseline",
            "options": budgets, "provenance": provenance or plan.snapshot([framework])}


def check_resume(args, manifest):
    if getattr(args, "framework", manifest["framework"]) != manifest["framework"]:
        raise ValueError("Batch framework mismatch")
    options(args, manifest["options"])
    if args.scenario is not None and args.scenario != manifest["scenarios"]:
        raise ValueError("Resume cannot change scenarios")
    if args.repeats is not None and args.repeats != manifest["repeats"]:
        raise ValueError("Resume cannot change repetitions")
    if args.fresh_reset and manifest["reset_mode"] != "fresh":
        raise ValueError("Resume cannot change reset mode")
    if args.baseline is not None and (manifest.get("baseline") or {}).get("name") != args.baseline:
        raise ValueError("Resume cannot change baseline")
    plan.verify(manifest["provenance"], [manifest["framework"]])
    if manifest.get("baseline") and baseline.verify(manifest["baseline"]["name"]) != manifest["baseline"]:
        raise ValueError("Frozen baseline changed")


def command(manifest, scenario):
    framework = manifest["framework"]
    result = [str(interpreter(framework)), f"redteam/{framework}/run.py", "--scenario", scenario,
              "--oracle-wait", str(manifest["options"]["oracle_wait"])]
    if framework == "dreadnode":
        result += ["--max-steps", str(manifest["options"]["max_steps"])]
    return result


def can_finalize(path, row):
    """No retries of uncertain sends, failed frameworks or interrupted processes."""
    if row.get("execution_error"):
        return False
    directory = path / "framework/native"
    try:
        marker, state = read_json(directory / "completion.json"), read_json(directory / "attack-state.json")
        return (marker.get("frameworkStatus") == "completed" and not marker.get("error")
            and marker.get("oracleStatus") == "error" and bool(state["events"])
            and len(state["events"]) == state["turns"]
            and all(e["status"] in {"continue", "oracle-success"} for e in state["events"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def needs_collection(path, row):
    if not can_finalize(path, row):
        return False
    live = ORACLE / "episode.json"
    return live.is_file() and read_json(live).get("redteam", {}).get("runId") == row["run_id"]


def archive(path, row, progress):
    progress.stage("collecting")
    started = time.monotonic()
    try:
        diagnostics.collect(path, row)
        row["collection_status"] = "completed"
        row.pop("collection_error", None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        row.update(collection_status="error", collection_error=str(error))
    try:
        diagnostics.optional_diagnostics(path, row)
        row["diagnostics_status"] = "completed"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        row.update(diagnostics_status="warning", diagnostics_error=str(error))
        progress.message(f"Optional diagnostics unavailable: {type(error).__name__}")
    row["oracle_wait_seconds"] = time.monotonic() - started


def attempt(batch, manifest, scenario, repeat, args, descriptor):
    parent = batch / scenario / f"repeat-{repeat:02}"
    attempts = [int(p.name.split("-")[1]) for p in parent.glob("attempt-*") if p.is_dir()]
    number = max(attempts, default=0) + 1
    path = parent / f"attempt-{number:02}"
    path.mkdir(parents=True, mode=0o700)
    provenance = manifest["provenance"]
    row = {"schema_version": 1, "framework": manifest["framework"], "scenario": scenario,
           "repeat": repeat, "attempt": number, "run_id": str(uuid.uuid4()), "started_at": now(),
           "revision": provenance["revision"], "options": manifest["options"],
           "attacker_model": provenance["attacker_model"], "victim_model": provenance["victim_model"],
           "baseline": manifest.get("baseline"), "status": "running", "stage": "starting"}
    write_json(path / "run.json", row)
    progress = Progress(path / "experiment.log", args.status_interval)
    progress.start()
    started, interrupted = time.monotonic(), False
    env = child_env(descriptor, COMMONLY_EXPERIMENT_RUN_ID=row["run_id"],
        GREYZONE_RUN_ID=row["run_id"], GREYZONE_REVISION=row["revision"],
        GREYZONE_ATTACKER_MODEL=row["attacker_model"], COMMONLY_RUN_TIMEOUT=args.run_timeout)
    try:
        progress.message(f"{row['framework']} | {scenario} | repeat={repeat}/{manifest['repeats']} | attempt={number}")
        plan.verify(provenance, [manifest["framework"]])
        progress.stage("restoring-baseline" if manifest.get("baseline") else "resetting")
        if manifest.get("baseline"):
            ref = manifest["baseline"]
            reset_command = [sys.executable, "experiment/scripts/baseline.py", "restore", ref["name"],
                             "--run-id", row["run_id"], "--manifest-sha256", ref["manifest_sha256"]]
            env["COMMONLY_BASELINE_RECEIPT"] = str(baseline.RECEIPT)
        else:
            reset_command = [str(PYTHON), "redteam/reset.py", "--hard"]
        reset_env = {**env, "COMMONLY_RESET_DEADLINE": str(time.monotonic() + args.reset_timeout)}
        execute(reset_command, ROOT, reset_env, descriptor, progress, path / "environment.log", args.reset_timeout)
        row["reset_duration_seconds"] = time.monotonic() - started
        plan.verify(provenance, [manifest["framework"]])
        row["stage"] = "framework-running"
        write_json(path / "run.json", row)
        progress.stage("framework-running")
        framework_started = time.monotonic()
        try:
            row["returncode"] = execute(command(manifest, scenario), ROOT, env, descriptor,
                progress, path / "console.log", args.run_timeout, check=False)
        finally:
            row["framework_duration_seconds"] = time.monotonic() - framework_started
    except KeyboardInterrupt:
        interrupted = True
        row.update(execution_error="Interrupted", failure_kind="interrupted")
    except Exception as error:
        row.update(execution_error=f"{type(error).__name__}: {error}",
                   failure_kind="timeout" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else row["stage"])
    finally:
        try:
            archive(path, row, progress)
            row.update(finished_at=now(), duration_seconds=time.monotonic() - started, stage="finished")
            classify(path, row, manifest)
            progress.message(f"{row['status']} | score={row.get('score', 'unavailable')} | {row.get('reason', '')}")
            collect(batch)
        finally:
            progress.stop()
    if interrupted:
        raise KeyboardInterrupt
    return row["status"] == "valid", needs_collection(path, row)


def recover(batch, manifest, args, descriptor):
    """Collect archives first; finalize only under the original Oracle source."""
    for file in sorted(batch.glob("*/repeat-*/attempt-*/run.json")):
        path, row = file.parent, read_json(file)
        if row.get("status") == "valid":
            continue
        progress = Progress(path / "experiment.log", args.status_interval)
        progress.start()
        try:
            row.setdefault("recovery_history", []).append({"at": now(), "status": row.get("status"),
                "reason": row.get("reason"), "duration_seconds": row.get("duration_seconds"),
                "collection_error": row.get("collection_error")})
            if can_finalize(path, row) and needs_collection(path, row):
                saved = manifest["provenance"]["configuration"]
                current = configuration()
                oracle_files = lambda data: {k: v for k, v in data.items() if k.startswith(("oracle/", "redteam/")) or k == "../scripts/oracle_result.py"}
                if oracle_files(saved) != oracle_files(current):
                    raise ValueError("Oracle or redteam source changed; cannot retry finalization")
                if plan.runtime(row["framework"]) != manifest["provenance"]["runtimes"][row["framework"]]:
                    raise ValueError("Saved Oracle interpreter or SDK versions changed")
                native = diagnostics.native_path(row)
                if not native.is_dir():
                    raise ValueError("Original native candidate unavailable for finalization")
                env = child_env(descriptor, GREYZONE_RUN_ID=row["run_id"], GREYZONE_REVISION=row["revision"],
                                GREYZONE_ATTACKER_MODEL=row["attacker_model"])
                progress.stage("recovering-oracle")
                code = execute([str(interpreter(row["framework"])), "-m", "redteam.finalize", "--directory", str(native),
                    "--run-id", row["run_id"]], ROOT, env, descriptor, progress, path / "recovery.log",
                    manifest["options"]["collection_timeout"], check=False)
                if code == 0:
                    row["framework_returncode"] = 0
                else:
                    row["collection_error"] = f"Finalization exited {code}"
            archive(path, row, progress)
            row.update(finished_at=now(), duration_clock="wall_clock_recovery")
            row["duration_seconds"] = (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()
            classify(path, row, manifest)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            row.update(collection_error=str(error), collection_status="error", status="invalid")
            write_json(file, row)
            progress.message(f"Recovery unavailable: {type(error).__name__}")
        finally:
            progress.stop()
    return collect(batch)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    parser.add_argument("--framework", choices=FRAMEWORKS)
    parser.add_argument("--save-baseline", type=safe_name)
    parser.add_argument("--batch-id", type=safe_name)
    parser.add_argument("--series-plan", type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.repeats is not None and args.repeats < 1:
        parser.error("repeats must be positive")
    if args.fresh_reset and args.baseline:
        parser.error("fresh-reset and baseline are mutually exclusive")
    if args.collect_only and not args.resume:
        parser.error("collect-only requires resume")
    with run_lock() as descriptor:
        if args.save_baseline:
            if args.resume or args.collect_only or args.check:
                parser.error("save-baseline cannot be combined with resume, collect-only or check")
            preflight(descriptor=descriptor)
            budgets = options(args)
            directory = ROOT / ".run"
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            progress = Progress(directory / "baseline-capture.log", args.status_interval)
            progress.start()
            try:
                execute([sys.executable, "experiment/scripts/baseline.py", "save", args.save_baseline], ROOT,
                        child_env(descriptor, COMMONLY_RESET_DEADLINE=time.monotonic() + budgets["reset_timeout"]),
                        descriptor, progress, directory / "baseline-capture-console.log", budgets["reset_timeout"])
            finally:
                progress.stop()
            return 0
        if not args.framework:
            parser.error("framework is required")
        batch_id = args.resume or args.batch_id or (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6])
        batch = RESULTS / args.framework / batch_id
        if args.collect_only:
            manifest = read_json(batch / "manifest.json")
            if manifest["framework"] != args.framework:
                raise ValueError("Batch framework mismatch")
            return 0 if recover(batch, manifest, args, descriptor) else 1
        preflight([args.framework], descriptor)
        if args.check:
            options(args)
            if args.baseline:
                baseline.verify(args.baseline)
            print(f"{args.framework}: experiment prerequisites passed (no reset or model calls)")
            return 0
        if args.resume:
            manifest = read_json(batch / "manifest.json")
            check_resume(args, manifest)
            recover(batch, manifest, args, descriptor)
            if any(needs_collection(p.parent, read_json(p)) for p in batch.glob("*/repeat-*/attempt-*/run.json")):
                return 75
        else:
            provenance = None
            if args.series_plan:
                series = read_json(args.series_plan)
                provenance = series["provenance"]
                plan.verify(provenance, [args.framework])
            manifest = manifest_for(args, args.framework, provenance)
            if args.series_plan:
                if series["batches"].get(args.framework) != batch_id or any(
                    manifest[k] != series[k] for k in ("scenarios", "scenario_specs", "repeats", "baseline", "options", "reset_mode")):
                    raise ValueError("Batch parameters differ from the frozen series")
            batch.mkdir(parents=True, mode=0o700, exist_ok=False)
            write_json(batch / "manifest.json", manifest)
        print(f"Batch: {batch}", flush=True)
        for scenario in manifest["scenarios"]:
            for repeat in range(1, manifest["repeats"] + 1):
                files = list((batch / scenario / f"repeat-{repeat:02}").glob("attempt-*/run.json"))
                if any(read_json(p).get("status") == "valid" for p in files):
                    continue
                valid, pending = attempt(batch, manifest, scenario, repeat, args, descriptor)
                if not valid:
                    return 75 if pending else 1
        return 0 if collect(batch) else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Experiment interrupted; no attack will be resent", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Experiment error: {error}", file=sys.stderr)
        raise SystemExit(1)
