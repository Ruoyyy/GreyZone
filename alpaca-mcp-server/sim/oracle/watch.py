"""The oracle's main loop: observe-only, alert-first, never blocks.

  python3 oracle/watch.py           # watch (Ctrl-C or idle timeout finalizes)
  python3 oracle/watch.py --once    # one poll cycle (smoke)
  python3 oracle/watch.py --close   # close a running episode now

The watcher is a standalone poller over GET /admin/state — it never writes
to the world, never talks to the victim agent, and may run before, during,
after, or without it. Each cycle it judges the three surfaces against the
baseline; newly-fired surfaces are appended to alerts-<N>.jsonl in real
time. An episode closes when no world change has been seen for
EPISODE_IDLE_TIMEOUT seconds (or on Ctrl-C / --close), then the judgments
land in result-<N>.json as success(<surfaces>) or failed.

Env switches: ORACLE_ENABLED (master), ORACLE_ENABLE (surface subset),
WORLD_API_URL, ORACLE_POLL_SECONDS, EPISODE_IDLE_TIMEOUT, ORACLE_MAX_DURATION.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a plain script

from oracle import common as C
from oracle import judge as J

GRACE_AFTER_START = 60  # the victim agent may take a while to wake and act


def state_fingerprint(state: dict) -> str:
    """Orders + positions identity: any victim action that matters moves it."""
    orders = [(o["id"], o["status"], o.get("qty"), o.get("limit_price"), o.get("stop_price"),
               o.get("replaced_by")) for o in state["orders"]]
    positions = sorted((s, round(p["qty"], 6)) for s, p in state.get("positions", {}).items())
    return json.dumps([sorted(map(str, orders)), positions], default=str)


def load_or_start_episode(baseline: dict) -> dict:
    """Adopt a running episode (watcher restart) or open the next one."""
    if C.episode_path().exists():
        ep = json.loads(C.episode_path().read_text())
        print(f"[oracle] resuming episode {ep['n']}")
        return ep
    ep = {
        "n": C.next_episode(),
        "started_at": C.now_iso(),
        "started_monotonic": time.monotonic(),
        "last_change": time.monotonic(),
        "baseline_prepared_at": baseline.get("prepared_at"),
    }
    C.run_dir().mkdir(parents=True, exist_ok=True)
    C.episode_path().write_text(json.dumps({
        k: v for k, v in ep.items() if not k.endswith("_monotonic")}))
    print(f"[oracle] episode {ep['n']} open — watching {C.world_api_url()}")
    return ep


def close_episode(episode: dict, reason: str, state: dict, baseline: dict,
                  surfaces: set[str]) -> None:
    judgments = J.judge_all(state, baseline, surfaces)
    path = C.write_result(episode, judgments, reason)
    C.episode_path().unlink(missing_ok=True)
    print(f"[oracle] episode {episode['n']} closed ({reason}) -> {path}")
    print(f"[oracle] verdict: {J.verdict(judgments)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single poll cycle, no episode lifecycle")
    ap.add_argument("--close", action="store_true", help="close the running episode now")
    args = ap.parse_args()

    if not C.enabled():
        print("[oracle] ORACLE_ENABLED is off — nothing to do")
        return

    baseline = C.load_baseline()
    surfaces = C.enabled_surfaces()

    if args.close:
        if not C.episode_path().exists():
            sys.exit("[oracle] no running episode to close")
        ep = json.loads(C.episode_path().read_text())
        ep.setdefault("baseline_prepared_at", baseline.get("prepared_at"))
        close_episode(ep, "manual", C.fetch_state(), baseline, surfaces)
        return

    if args.once:  # smoke: one poll cycle, no episode lifecycle
        judgments = J.judge_all(C.fetch_state(), baseline, surfaces)
        print(f"[oracle] --once verdict: {J.verdict(judgments)}")
        for j in judgments:
            print(f"  {j['surface']}: fired={j['fired']}")
        return

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    ep = load_or_start_episode(baseline)
    fired_seen: set[str] = set()
    prev_fp: str | None = None

    while True:
        if not C.episode_path().exists():  # closed externally via --close
            print("[oracle] episode closed externally — exiting")
            return
        try:
            state = C.fetch_state()
        except Exception as exc:  # observe errors are records, not failures
            C.emit(ep["n"], "OBSERVE_ERROR", None, {"error": str(exc)})
            print(f"[oracle] observe error: {exc}", file=sys.stderr)
            time.sleep(C.poll_seconds())
            continue

        fp = state_fingerprint(state)
        world_changed = fp != prev_fp
        if world_changed:
            ep["last_change"] = time.monotonic()
            if prev_fp is not None:
                C.emit(ep["n"], "WORLD_CHANGE", None,
                       {"new_orders": len(state["orders"]),
                        "positions": state.get("positions", {})})
            prev_fp = fp

        for j in J.judge_all(state, baseline, surfaces):
            if (j["fired"] and j["surface"] not in fired_seen
                    and j["surface"] not in J.CLOSE_ONLY_SURFACES):
                fired_seen.add(j["surface"])
                C.emit(ep["n"], "SURFACE_FIRED", j["surface"], j["evidence"])
                print(f"[oracle] SURFACE FIRED: {j['surface']}")

        idle = time.monotonic() - ep["last_change"]
        runtime = time.monotonic() - ep["started_monotonic"]
        if runtime > GRACE_AFTER_START and idle > C.idle_timeout():
            close_episode(ep, "idle", state, baseline, surfaces)
            return
        if runtime > C.max_duration():
            close_episode(ep, "max-duration", state, baseline, surfaces)
            return
        if stop["flag"]:
            close_episode(ep, "signal", state, baseline, surfaces)
            return

        time.sleep(C.poll_seconds())


if __name__ == "__main__":
    main()
