"""Batch runner — pure orchestration over oracle/run_episode.py.

    python3 oracle/batch.py [--scenarios a,b|all] [--trials 3] [--settle 15]
                            [--report .run/oracle/report.json] [--timeout 900]

Contract: batch NEVER judges. It loops scenarios × trials, drives the episode
CLI as subprocesses (prepare → start → close), and aggregates the verdicts the
oracle already wrote into result.json. It does not touch the cluster, the
collector or the trace for verdict purposes; the only truth source is
result.json. If a run dies mid-episode, batch invokes --close best-effort so
teardown (oracle's job) still happens, then moves on.
"""
import argparse
import csv
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import common as C  # noqa: E402

SCEN_DIR = C.REPO / "benchmark/scenarios"
RUNNER = pathlib.Path(__file__).parent / "run_episode.py"


def run_step(args, timeout):
    p = subprocess.run([sys.executable, str(RUNNER)] + args,
                       capture_output=True, text=True, timeout=timeout,
                       env=C.KUBE_ENV)
    return p.returncode, p.stdout + p.stderr


def current_episode():
    f = C.RUN / "current.json"
    return json.loads(f.read_text()) if f.exists() else {}


def one_run(name, settle, timeout):
    """One episode via CLI; returns a row (ok or error), never raises."""
    log = []
    try:
        for step in (["--prepare", str(SCEN_DIR / f"{name}.json")],
                     ["--start"],
                     ["--close", "--settle", str(settle)]):
            code, out = run_step(step, timeout)
            log.append(out)
            if code != 0:
                # best-effort recovery so teardown still runs, then give up
                try:
                    _, rec = run_step(["--close", "--settle", "5"], timeout)
                    log.append(rec)
                except Exception:
                    pass
                ep = current_episode().get("n")
                return {"scenario": name, "episode": ep, "ok": False,
                        "error": f"{step[0]} failed", "log": "\n".join(log)[-2000:]}
        ep = current_episode().get("n")
        r = json.loads((C.RUN / f"episode-{ep}" / "result.json").read_text())
        d = r.get("diagnostics") or {}
        return {"scenario": name, "episode": ep, "ok": True,
                "verdict": r["verdict"],
                "fired": [j["surface"] for j in r["judgments"] if j["fired"]],
                "enteredContext": d.get("enteredContext"),
                "readNotSent": (d.get("readNotSent") or {}).get("matched")}
    except subprocess.TimeoutExpired:
        try:
            run_step(["--close", "--settle", "5"], 60)
        except Exception:
            pass
        return {"scenario": name, "episode": current_episode().get("n"),
                "ok": False, "error": "timeout"}
    except Exception as e:
        return {"scenario": name, "episode": current_episode().get("n"),
                "ok": False, "error": str(e)[:200]}


def summarize(rows):
    per = {}
    for r in rows:
        s = per.setdefault(r["scenario"], {"trials": 0, "success": 0,
                                           "failed": 0, "error": 0,
                                           "firedSurfaces": [], "entered": []})
        s["trials"] += 1
        if not r["ok"]:
            s["error"] += 1
            continue
        s["success" if r["verdict"] != "failed" else "failed"] += 1
        s["firedSurfaces"] += r["fired"]
        if r["enteredContext"] is not None:
            s["entered"].append(r["enteredContext"])
    for s in per.values():
        s["enteredAvg"] = round(sum(s.pop("entered")) / len(s["entered"]), 1) \
            if s["entered"] else None
        s["firedSurfaces"] = sorted(set(s["firedSurfaces"]))
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="all",
                    help="comma list of scenario names, or 'all'")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--settle", type=int, default=15)
    ap.add_argument("--report", default=str(C.RUN / "report.json"))
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    names = sorted(p.stem for p in SCEN_DIR.glob("*.json")) \
        if a.scenarios == "all" else [s.strip() for s in a.scenarios.split(",")]
    print(f"batch: {len(names)} scenarios × {a.trials} trials = "
          f"{len(names) * a.trials} runs")

    rows = []
    t0 = time.time()
    for i, name in enumerate(names):
        for t in range(1, a.trials + 1):
            r = one_run(name, a.settle, a.timeout)
            rows.append(r)
            tag = r.get("verdict") or f"ERROR({r.get('error')})"
            print(f"[{len(rows):>2}/{len(names) * a.trials}] {name} "
                  f"t{t} ep{r.get('episode')} -> {tag}")

    report = {"startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "durationSec": round(time.time() - t0),
              "trials": a.trials, "rows": rows, "summary": summarize(rows)}
    out = pathlib.Path(a.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    with out.with_suffix(".csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "trial", "episode", "verdict", "fired",
                    "enteredContext", "readNotSent", "ok"])
        for i, r in enumerate(rows):
            w.writerow([r["scenario"], i % a.trials + 1, r.get("episode"),
                        r.get("verdict", ""), ",".join(r.get("fired", [])),
                        r.get("enteredContext", ""), r.get("readNotSent", ""),
                        r["ok"]])

    print(f"\n{'scenario':<38} {'succ':>4} {'fail':>4} {'err':>4} {'entAvg':>6} fired")
    for name, s in report["summary"].items():
        print(f"{name:<38} {s['success']:>4} {s['failed']:>4} {s['error']:>4} "
              f"{str(s['enteredAvg']):>6} {','.join(s['firedSurfaces']) or '-'}")
    print(f"\nreport: {out} (+ .csv)  [{report['durationSec']}s]")


if __name__ == "__main__":
    main()
