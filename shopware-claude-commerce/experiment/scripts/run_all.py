#!/usr/bin/env python3
"""Run all frameworks serially; keep separate resumable batches and continue after invalid runs."""
import argparse
import os
import subprocess
import sys
import time
import uuid

from experiment_common import FRAMEWORKS, PYTHON, RESULTS, ROOT, now, read_json, safe_name, write_json
from run import positive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=positive, default=3)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--resume", type=safe_name, help="Series ID printed by an earlier run_all invocation")
    parser.add_argument("--status-interval", type=positive, default=30)
    parser.add_argument("--run-timeout", type=positive, default=3600)
    parser.add_argument("--reset-timeout", type=positive, default=900)
    args = parser.parse_args()
    os.umask(0o077)
    series_id = args.resume or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    path = RESULTS / f"series-{series_id}.json"
    if args.resume:
        series = read_json(path)
        if args.scenario and sorted(set(args.scenario)) != series["scenarios"]:
            raise ValueError("Resume scenarios differ from the saved series")
    else:
        series = {"created_at": now(), "repeats": args.repeats, "scenarios": sorted(set(args.scenario or [])),
                  "batches": {fw: series_id for fw in FRAMEWORKS}}
        write_json(path, series)
    print(f"Series: {series_id} | metadata: {path}", flush=True)
    failed = []
    for framework, batch_id in series["batches"].items():
        command = [str(PYTHON), str(ROOT / "experiment/scripts/run.py"), "--framework", framework,
                   "--repeats", str(series["repeats"]), "--status-interval", str(args.status_interval),
                   "--run-timeout", str(args.run_timeout), "--reset-timeout", str(args.reset_timeout)]
        existing = RESULTS / framework / batch_id / "manifest.json"
        command += ["--resume" if existing.exists() else "--batch-id", batch_id]
        for scenario in series["scenarios"]:
            command += ["--scenario", scenario]
        print(f"Starting {framework}", flush=True)
        # Children cannot consume subsequent commands from redirected shell input.
        code = subprocess.call(command, cwd=ROOT, stdin=subprocess.DEVNULL)
        if code in (130, -2):
            return 130
        if code:
            failed.append(framework)
    print("Incomplete frameworks: " + (", ".join(failed) if failed else "none"), flush=True)
    print(f"Resume series: bash experiment/run_all.sh --resume {series_id}", flush=True)
    return int(bool(failed))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Series interrupted; no further frameworks will start", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError) as error:
        print(f"Series error: {error}", file=sys.stderr)
        raise SystemExit(1)
