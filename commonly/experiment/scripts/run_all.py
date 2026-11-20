#!/usr/bin/env python3
"""Freeze and run all four framework batches serially, preserving recoverable episodes."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from experiment_common import FRAMEWORKS, RESULTS, ROOT, child_env, now, options, read_json, run_lock, write_json
import baseline
from environment import preflight
import plan
from process import execute
from progress import Progress
from run import add_options, manifest_for, terminate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(parser)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.collect_only and not args.resume:
        parser.error("collect-only requires resume")
    if args.fresh_reset and args.baseline or args.repeats is not None and args.repeats < 1:
        parser.error("Invalid reset mode or repetition count")
    with run_lock() as descriptor:
        if args.check:
            preflight(FRAMEWORKS, descriptor)
            options(args)
            if args.baseline:
                baseline.verify(args.baseline)
            print("All framework prerequisites passed (no reset or model calls)")
            return 0
        series_id = args.resume or (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6])
        path = RESULTS / f"series-{series_id}.json"
        RESULTS.mkdir(parents=True, mode=0o700, exist_ok=True)
        if args.resume:
            series = read_json(path)
            if not args.collect_only:
                from run import check_resume
                check_resume(args, {**series, "framework": FRAMEWORKS[0]})
                plan.verify(series["provenance"], FRAMEWORKS)
        else:
            preflight(FRAMEWORKS, descriptor)
            provenance = plan.snapshot(FRAMEWORKS)
            series = manifest_for(args, FRAMEWORKS[0], provenance)
            series.pop("framework")
            series.update(batches={fw: series_id for fw in FRAMEWORKS}, status="planned")
        write_json(path, series)
        print(f"Series: {series_id} | metadata: {path}", flush=True)
        series.update(status="running", last_started_at=now())
        write_json(path, series)
        failed = []
        progress = Progress(None, args.status_interval)
        progress.start()
        try:
            for framework, batch_id in series["batches"].items():
                if not args.collect_only:
                    plan.verify(series["provenance"], FRAMEWORKS)
                    if series.get("baseline") and baseline.verify(series["baseline"]["name"]) != series["baseline"]:
                        raise ValueError("Series baseline changed")
                existing = (RESULTS / framework / batch_id / "manifest.json").is_file()
                if args.collect_only and not existing:
                    failed.append(framework)
                    continue
                command = [sys.executable, "experiment/scripts/run.py", "--framework", framework,
                           "--resume" if existing else "--batch-id", batch_id,
                           "--status-interval", str(args.status_interval)]
                if args.collect_only:
                    command += ["--collect-only"]
                else:
                    command += ["--series-plan", str(path), "--repeats", str(series["repeats"])]
                    command += ["--baseline", series["baseline"]["name"]] if series.get("baseline") else ["--fresh-reset"]
                    for scenario in series["scenarios"]:
                        command += ["--scenario", scenario]
                    for key, value in series["options"].items():
                        command += ["--" + key.replace("_", "-"), str(value)]
                progress.stage("framework-batch", framework)
                code = execute(command, ROOT, child_env(descriptor), descriptor, progress,
                    None,
                    max(86400, series["repeats"] * len(series["scenarios"]) *
                        (series["options"]["run_timeout"] + series["options"]["reset_timeout"] + 300)), check=False)
                series.setdefault("framework_status", {})[framework] = "complete" if code == 0 else "incomplete"
                write_json(path, series)
                if code in {130, -signal.SIGINT, -signal.SIGTERM}:
                    raise KeyboardInterrupt
                if code == 75:
                    series.update(status="needs_collection", finished_at=now())
                    return 75
                if code:
                    failed.append(framework)
            series.update(status="incomplete" if failed else "complete", finished_at=now())
            return int(bool(failed))
        except KeyboardInterrupt:
            series.update(status="interrupted", interrupted_at=now())
            raise
        except Exception as error:
            series.update(status="error", error=str(error), finished_at=now())
            raise
        finally:
            write_json(path, series)
            progress.stop()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Series interrupted; no further frameworks will start", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Series error: {error}", file=sys.stderr)
        raise SystemExit(1)
