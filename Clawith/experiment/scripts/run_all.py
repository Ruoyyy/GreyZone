#!/usr/bin/env python3
"""Run a frozen serial series with resumable per-framework results."""
import argparse
import os
import signal
import subprocess
import sys
import time
import uuid

from experiment_common import FRAMEWORKS, PYTHON, RESULTS, ROOT, SCENARIOS, now, read_json, safe_name, write_json
from run import positive, terminate
from baseline import verify as verify_baseline
import plan


def invoke(command):
    child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=positive)
    parser.add_argument("--scenario", action="append", choices=sorted(p.name for p in SCENARIOS.iterdir()))
    parser.add_argument("--framework", action="append", choices=FRAMEWORKS)
    parser.add_argument("--resume", type=safe_name)
    parser.add_argument("--collect-only", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline", type=safe_name)
    mode.add_argument("--fresh-reset", action="store_true")
    parser.add_argument("--status-interval", type=positive, default=30)
    for key in plan.DEFAULTS:
        parser.add_argument("--" + key.replace("_", "-"), type=positive)
    args = parser.parse_args(argv)
    if args.collect_only and not args.resume:
        parser.error("--collect-only requires --resume")
    os.umask(0o077)
    series_id = args.resume or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    path = RESULTS / f"series-{series_id}.json"
    if args.resume:
        series = read_json(path)
        if args.scenario and sorted(set(args.scenario)) != series["scenarios"]:
            raise ValueError("Resume scenarios differ from the saved series")
        if args.repeats is not None and args.repeats != series["repeats"]:
            raise ValueError("Resume repetition count differs from saved series")
        if args.framework and list(dict.fromkeys(args.framework)) != list(series["batches"]):
            raise ValueError("Resume frameworks differ from saved series")
        saved = series.get("baseline")
        if ((args.baseline and (not saved or args.baseline != saved["name"])) or (args.fresh_reset and saved)):
            raise ValueError("Resume baseline differs from saved series")
        budgets = plan.options(args, series.get("options"))
        if not args.collect_only:
            if saved and verify_baseline(saved["name"]) != saved:
                raise ValueError("Series baseline changed")
            if series.get("provenance"):
                plan.verify(series["provenance"], series["batches"])
    else:
        frameworks = list(dict.fromkeys(args.framework or FRAMEWORKS))
        budgets = plan.options(args)
        baseline = None if args.fresh_reset else verify_baseline(args.baseline or "clean")
        series = {"schema_version": 2, "created_at": now(), "repeats": args.repeats or 3,
                  "scenarios": sorted(set(args.scenario or [p.name for p in SCENARIOS.iterdir()])),
                  "batches": {fw: series_id for fw in frameworks}, "baseline": baseline, "options": budgets,
                  "provenance": plan.snapshot(frameworks), "status": "planned"}
    if not args.collect_only:
        for framework in series["batches"]:
            subprocess.run([PYTHON, str(ROOT / "experiment/scripts/run.py"), "--framework", framework,
                            "--check"], cwd=ROOT, stdin=subprocess.DEVNULL, check=True)
    if not args.resume:
        write_json(path, series)
    print(f"Series: {series_id} | metadata: {path}", flush=True)
    failed = []
    series.update(status="running", last_started_at=now())
    write_json(path, series)
    try:
        for framework, batch_id in series["batches"].items():
            if not args.collect_only:
                if series.get("provenance"):
                    plan.verify(series["provenance"], series["batches"])
                if series.get("baseline") and verify_baseline(series["baseline"]["name"]) != series["baseline"]:
                    raise ValueError("Series baseline changed")
            existing = RESULTS / framework / batch_id / "manifest.json"
            if args.collect_only and not existing.exists():
                failed.append(framework)
                continue
            command = [PYTHON, str(ROOT / "experiment/scripts/run.py"), "--framework", framework,
                       "--status-interval", str(args.status_interval), "--resume" if existing.exists() else "--batch-id", batch_id]
            if args.collect_only:
                command += ["--collect-only"]
            else:
                command += ["--repeats", str(series["repeats"])]
                for key, value in budgets.items():
                    command += ["--" + key.replace("_", "-"), str(value)]
                if series.get("provenance"):
                    command += ["--series-plan", str(path)]
                command += ["--baseline", series["baseline"]["name"]] if series.get("baseline") else ["--fresh-reset"]
                for scenario in series["scenarios"]:
                    command += ["--scenario", scenario]
            print(f"Starting {framework}", flush=True)
            code = invoke(command)
            series.setdefault("framework_status", {})[framework] = "complete" if code == 0 else "incomplete"
            write_json(path, series)
            if code in (130, -2, -15):
                raise KeyboardInterrupt
            if code == 75:
                series.update(status="needs_collection", finished_at=now())
                print(f"Recover before restoring another episode: bash experiment/run_all.sh --resume {series_id} --collect-only")
                return 75
            if code:
                failed.append(framework)
        series.update(status="incomplete" if failed else "complete", finished_at=now())
    except KeyboardInterrupt:
        series.update(status="interrupted", interrupted_at=now())
        raise
    except Exception as error:
        series.update(status="error", error=str(error), finished_at=now())
        raise
    finally:
        write_json(path, series)
    print("Incomplete frameworks: " + (", ".join(failed) if failed else "none"), flush=True)
    print(f"Resume series: bash experiment/run_all.sh --resume {series_id}", flush=True)
    return int(bool(failed))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Series interrupted; no further frameworks will start", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Series error: {error}", file=sys.stderr)
        raise SystemExit(1)
