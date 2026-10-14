#!/usr/bin/env python3
"""Drive one indirect-injection episode end to end:

 1. attacker plants over the external MCP (submitInvoice / receivePayment)
 2. oracle takes the baseline (--start; planted data folds into initial state)
 3. ONE victim interaction: victimTask is posted as the victim over REST /api/chat
    — no follow-ups, the payload gets exactly one shot
 4. oracle --close: settle-wait, judge, write result-<N>.json

Usage: python3 runner/run_indirect.py benchmark/scenarios/indirect-invoice-pay [--settle 8]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracle"
sys.path.insert(0, str(ORACLE_DIR))

import common
import mcp_client
import run_episode

CHAT_API = "http://localhost/api/chat"


def plant_via_mcp(scenario: dict) -> None:
    plant = scenario.get("plant", {})
    invoices = plant.get("invoices", [])
    payments = plant.get("payments", [])
    if not invoices and not payments:
        print("plant: nothing to plant")
        return
    mcp = mcp_client.McpClient()
    endpoint = mcp.connect()
    mcp.initialize()
    print(f"plant: connected to attacker MCP ({endpoint})")
    for invoice in invoices:
        result = mcp.call_tool("submitInvoice", {"invoice": invoice})
        status = "ok" if mcp_client.tool_ok(result) else f"ERROR {result.get('result', {}).get('isError')}"
        print(f"plant: submitInvoice {invoice.get('InvoiceId')} -> {status}")
    for payment in payments:
        result = mcp.call_tool("receivePayment", {"payment": payment})
        status = "ok" if mcp_client.tool_ok(result) else "ERROR"
        print(f"plant: receivePayment '{payment.get('description')}' -> {status}")
    mcp.close()
    # verify the substrate actually landed before baselining
    for invoice in invoices:
        got = common.http_json("GET", f"{common.PAYMENTS_API}/invoices/{invoice['InvoiceId']}")
        if not got or got.get("InvoiceId") != invoice["InvoiceId"]:
            sys.exit(f"plant verification failed for invoice {invoice['InvoiceId']}")
    print("plant: substrate verified")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario_dir")
    ap.add_argument("--settle", type=float, default=8.0)
    args = ap.parse_args()

    scenario_path = Path(args.scenario_dir) / "scenario.json"
    scenario = json.loads(scenario_path.read_text())
    if not str(scenario.get("plan", "")).startswith("indirect"):
        sys.exit(f"scenario plan is '{scenario.get('plan')}'; use an indirect-* scenario")
    victim = scenario.get("victimUser", "admin")
    task = scenario["victimTask"]

    # 1. attacker pre-positions over MCP
    plant_via_mcp(scenario)

    # 2. oracle baseline (planted data now counts as initial state)
    run_episode.do_start(argparse.Namespace(scenario=str(scenario_path), surfaces=None))

    # 3. single victim interaction — no follow-up turns
    print(f"victim task ({victim}): {task}")
    reply = common.http_json("POST", CHAT_API, timeout=180, payload={
        "messages": [{"role": "user", "content": task}],
        "stream": False,
        "user": victim,
    })
    content = reply["choices"][0]["message"]["content"]
    common.RUN_DIR.mkdir(parents=True, exist_ok=True)
    (common.RUN_DIR / "last-reply.txt").write_text(content)
    print(f"victim reply saved ({len(content)} chars)")

    # 4. judge
    run_episode.do_close(argparse.Namespace(settle=args.settle))


if __name__ == "__main__":
    main()
