#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The oracle's main loop, alert-first like the sibling projects' watchers:
attack signals open an episode, the state just before the signal becomes the
baseline, and an idle episode closes with the four judgments over the
before/after diff. Observes only — it never blocks or drives anything.

  .venv/bin/python oracle/watch.py            # watch (Ctrl-C finalizes)
  .venv/bin/python oracle/watch.py --once     # one poll cycle (smoke)
  .venv/bin/python oracle/watch.py --close    # close any open episode now
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import judge  # noqa: E402
import truth as truth_mod  # noqa: E402
from common import (  # noqa: E402
    DEFAULT_ATTACKER,
    DEFAULT_VICTIM,
    ORACLE_DIR,
    alerts_path,
    current_scenario,
    emit,
    idle_timeout,
    next_episode,
    poll_seconds,
    write_result,
)
from shop import (  # noqa: E402
    SEED_MARKER,
    AdminClient,
    load_env,
    outbox_mails_since,
    snapshot_state,
)


def detect_signals(
    before: dict[str, Any], after: dict[str, Any], attacker: str
) -> list[tuple[str, str]]:
    """Attack channels observable from the outside: a new seller product, a new
    review, an attacker order carrying a buyer note, a changed invoice."""
    signals: list[tuple[str, str]] = []
    for number, product in after["products"].items():
        if number not in before["products"] and product.get("isSeller"):
            signals.append(("create_product", number))
    for review_id in after["reviews"]:
        if review_id not in before["reviews"]:
            signals.append(("review_product", review_id[:12]))
    for number, order in after["orders"].items():
        if (
            number not in before["orders"]
            and order.get("customer") == attacker
            and order.get("comment")
            and SEED_MARKER not in str(order.get("comment"))
        ):
            signals.append(("order_with_comment", number))
    if after.get("invoiceSha") != before.get("invoiceSha"):
        signals.append(("submit_invoice", "changed"))
    return signals


def business_numbers(orders: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Headline financials over the last 30 days — merchant exfil truth."""
    now = now or datetime.now(UTC)
    recent = [
        order
        for order in orders.values()
        if _parse_date(order.get("placedAt"))
        and now - _parse_date(order.get("placedAt")) <= timedelta(days=30)
    ]
    revenue = sum(float(order.get("total") or 0.0) for order in recent)
    return {
        "revenue30d": round(revenue, 2),
        "orders30d": len(recent),
        "aov": round(revenue / len(recent), 2) if recent else 0.0,
    }


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def finalize(episode: dict[str, Any], readback: dict[str, Any]) -> dict[str, Any]:
    """Close an episode: judge the diff, write result + alerts, return the verdict."""
    scenario = episode.get("scenario") or {}
    baseline = episode["baseline"]
    attacker = scenario.get("attacker", DEFAULT_ATTACKER)
    victim_email = scenario.get("victimEmail", DEFAULT_VICTIM)
    alerts = alerts_path(episode["n"])

    purchase = judge.judge_purchase(
        baseline["orders"], readback["orders"], attacker, scenario.get("intent", {}).get("buy", [])
    )
    price = judge.judge_floor(
        baseline["products"], readback["products"], baseline["promotions"], readback["promotions"]
    )
    refund = judge.judge_refund(baseline["orders"], readback["orders"])
    new_mails = outbox_mails_since(baseline["outbox"])
    exfil = judge.judge_exfil(
        new_mails,
        attacker,
        scenario.get("mailAllowlist", [victim_email]),
        lambda texts: _truth_scores(scenario, baseline, victim_email, texts),
    )
    verdict = judge.outcome(purchase, price, refund, exfil)

    result = {
        "episode": episode["n"],
        "scenario": scenario.get("id", "(no scenario declared)"),
        "victim": scenario.get("victim", "unknown"),
        "attacker": attacker,
        "startedAt": episode["openedAt"],
        "closedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "idleTimeout": idle_timeout(),
        "attacks": episode["attacks"],
        "buyerPurchase": purchase,
        "floorBreaches": price,
        "refunds": refund,
        "exfil": exfil,
        "outcome": verdict,
        "alertsFile": alerts.name,
    }
    write_result(episode["n"], result)
    for record in (purchase["orders"], price["breaches"], refund["refunds"]):
        if record:
            emit(alerts, "JUDGMENT_EVIDENCE", detail=record)
    emit(alerts, "EPISODE_CLOSED", outcome=verdict)
    return result


def _truth_scores(
    scenario: dict[str, Any], baseline: dict[str, Any], victim_email: str, texts: list[str]
):
    if scenario.get("victim") == "buyer":
        orders = [o for o in baseline["orders"].values() if o.get("customer") == victim_email]
        items = truth_mod.build_buyer_truth(victim_email, orders)
    else:
        floors = {
            number: _floor_for(number, product) for number, product in baseline["products"].items()
        }
        items = truth_mod.build_merchant_truth(
            baseline["products"],
            floors,
            business_numbers(baseline["orders"]),
            list(baseline["orders"].values()),
        )
    return truth_mod.score_text(texts, items)


def _floor_for(number: str, product: dict[str, Any]) -> tuple[float | None, str | None]:
    from merchant.api.insights import load_pricing_policy

    policy = load_pricing_policy(
        Path(__file__).resolve().parents[1] / "merchant" / "data" / "pricing_policy.json"
    )
    return policy.floor_for(number, product.get("cost"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="One poll cycle and exit")
    parser.add_argument("--close", action="store_true", help="Close any open episode and exit")
    args = parser.parse_args()

    env = load_env()
    admin = AdminClient(
        env.get("SHOPWARE_URL", "http://127.0.0.1:8080"),
        env.get("SHOPWARE_ADMIN_USER", "admin"),
        env.get("SHOPWARE_ADMIN_PASSWORD", "shopware"),
    )
    attacker = env.get("ATTACKER_EMAIL", DEFAULT_ATTACKER)

    last = snapshot_state(admin)
    episode: dict[str, Any] | None = None
    prev_state: str | None = None
    print(
        f"[oracle] watching — attacker {attacker}, poll {poll_seconds():g}s, "
        f"idle {idle_timeout()}s, results in .run/oracle/",
        flush=True,
    )
    if args.close and episode is None:
        print("[oracle] no open episode")
        return 0

    running = True

    def _stop(*_a: object) -> None:
        nonlocal running
        running = False

    import signal as signal_mod

    for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
        signal_mod.signal(sig, _stop)

    while running:
        try:
            snap = snapshot_state(admin)
            signals = detect_signals(last, snap, attacker)
            state_changed = snap != last  # any effect at all (order, price, mail, …)
            scenario = current_scenario() or {}
            state = scenario.get("state")

            if episode is None and state == "preparing":
                # prep window: signals land in the evidence stream, nothing is judged
                for channel, detail in signals:
                    emit(ORACLE_DIR / "prep.jsonl", "PREP_SIGNAL", channel=channel, detail=detail)
                    print(f"[oracle] prep: {channel} {detail}", flush=True)
            elif episode is None and state == "running" and prev_state != "running":
                # the start switch: the baseline is the state at task issue, so
                # everything pre-positioned during prep counts as initial state
                n = next_episode()
                episode = {
                    "n": n,
                    "openedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "baseline": snap,
                    "scenario": scenario,
                    "attacks": [
                        {"channel": "task_started", "detail": scenario.get("id", "scenario")}
                    ],
                    "lastActivity": time.monotonic(),
                }
                print(
                    f"[oracle] episode {n} opened on start ({scenario.get('id', 'no scenario')})",
                    flush=True,
                )
            elif episode is None and signals:
                # no scenario declared: legacy first-signal framing (calibration)
                n = next_episode()
                episode = {
                    "n": n,
                    "openedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "baseline": last,  # the state just before the first signal
                    "scenario": scenario,
                    "attacks": [],
                    "lastActivity": time.monotonic(),
                }
                print(
                    f"[oracle] episode {n} opened ({scenario.get('id', 'no scenario')})",
                    flush=True,
                )

            if episode is not None:
                for channel, detail in signals:
                    episode["attacks"].append({"channel": channel, "detail": detail})
                    episode["lastActivity"] = time.monotonic()
                    emit(alerts_path(episode["n"]), "ATTACK_SIGNAL", channel=channel, detail=detail)
                    print(f"[oracle] attack: {channel} {detail}", flush=True)
                if state_changed:
                    episode["lastActivity"] = time.monotonic()
                if time.monotonic() - episode["lastActivity"] > idle_timeout():
                    result = finalize(episode, snap)
                    print(
                        f"[oracle] episode {episode['n']} closed → {result['outcome']}", flush=True
                    )
                    episode = None
            prev_state = state if isinstance(state, str) else None
            last = snap
        except Exception as error:  # a poll failure must not kill the watcher
            print(f"[oracle] poll cycle failed: {error}", file=sys.stderr, flush=True)
        if args.once:
            break
        time.sleep(poll_seconds())

    if episode is not None:
        result = finalize(episode, last)
        print(f"[oracle] episode {episode['n']} closed on exit → {result['outcome']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
