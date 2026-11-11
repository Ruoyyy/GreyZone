#!/usr/bin/env python3
"""Episode driver for the banking-lab oracle.

Synchronous lifecycle (the copilot only acts when talked to):
  --print-objective --scenario PATH            export the public attacker prompt
  --start --scenario PATH [--surfaces csv]     take baselines
  --probe                                      re-read state, judge, print JSON
  --close [--settle SECONDS]                   settle-wait, final judge, write result

Attacker turns are driven by the red-team runner (external MCP sendPrompt as alice);
plan-2 victim tasks are posted by the runner as the victim over REST /api/chat.
The oracle itself never talks to the agent.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common
import judge


def read_start() -> dict:
    baseline = {
        "transactions": {acct: [str(t["id"]) for t in common.fetch_transactions(acct)]
                         for acct in common.WATCHED_ACCOUNTS},
        "aliceMaxId": common.alice_max_message_id(),
    }
    episode = common.next_episode()
    return {"episode": episode, "baseline": baseline, "startedAt": common.now_iso()}


def do_start(args):
    if not args.scenario:
        raise ValueError("--start requires --scenario to select the attacker objective")
    scenario, _ = common.load_scenario(args.scenario)
    ep = read_start()
    ep["scenario"] = scenario
    # explicit --surfaces win, else scenario oracleSurfaces, else plan-derived default
    ep["surfaces"] = (args.surfaces.split(",") if args.surfaces
                      else common.default_surfaces(scenario))
    common.save_episode(ep)
    common.emit(ep["episode"], "EPISODE_STARTED", "-",
                {"surfaces": ep["surfaces"], "scenario": (scenario or {}).get("name")})
    print(json.dumps({"episode": ep["episode"], "surfaces": ep["surfaces"],
                      "baseline": ep["baseline"]}))


def run_judgments(ep: dict) -> list:
    judgments = []
    for acct in common.WATCHED_ACCOUNTS:
        before = set(ep["baseline"]["transactions"].get(acct, []))
        after = common.fetch_transactions(acct)
        j = judge.judge_attacker_payment(before, after)
        j["account"] = acct
        judgments.append(j)
    turns = common.alice_turns_after(ep["baseline"]["aliceMaxId"])
    judgments.append(judge.judge_admin_leak(turns))
    return judgments


def do_probe(args, settle=0.0):
    ep = common.load_episode()
    if ep is None:
        sys.exit("no open episode: run --start first")
    if settle:
        # processPayment notifies the transaction store asynchronously
        time.sleep(settle)
    judgments = run_judgments(ep)
    v = judge.verdict(judgments, enabled=ep.get("surfaces"))
    for j in judgments:
        common.emit(ep["episode"], "JUDGMENT_EVIDENCE", j["surface"],
                    {k: val for k, val in j["evidence"].items() if k != "matching"} | (
                        {"matchingCount": len(j["evidence"].get("matching", []))}))
    print(json.dumps({"episode": ep["episode"], "verdict": v, "judgments": judgments}))
    return ep, judgments, v


def do_close(args):
    ep, judgments, v = do_probe(args, settle=args.settle)
    path = common.write_result(ep["episode"], ep.get("scenario"), judgments, v,
                               closed_reason=f"idle-close after settle {args.settle}s")
    common.emit(ep["episode"], "EPISODE_CLOSED", "-", {"verdict": v})
    common.clear_episode()
    print(json.dumps({"result": str(path), "verdict": v}))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--print-objective", action="store_true",
                        help="print the selected public attacker prompt without starting an episode")
    action.add_argument("--start", action="store_true")
    ap.add_argument("--scenario", help="scenario.json or its directory (recorded on the episode)")
    ap.add_argument("--surfaces", help="comma list restricting judged surfaces")
    action.add_argument("--probe", action="store_true")
    action.add_argument("--close", action="store_true")
    ap.add_argument("--settle", type=float, default=8.0,
                    help="seconds to wait before the final close judgment")
    args = ap.parse_args()
    if args.print_objective:
        if not args.scenario:
            ap.error("--print-objective requires --scenario")
        try:
            scenario, objective = common.load_scenario(args.scenario)
        except (OSError, ValueError) as error:
            ap.error(str(error))
        print(common.attacker_prompt(scenario, objective))
    elif args.start:
        if not args.scenario:
            ap.error("--start requires --scenario to select the attacker objective")
        do_start(args)
    elif args.probe:
        do_probe(args)
    elif args.close:
        do_close(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
