"""The oracle's main loop: observe-only, alert-first, never blocks.

  PYTHONPATH=. uv run --with httpx python oracle/watch.py           # watch
  PYTHONPATH=. uv run --with httpx python oracle/watch.py --once    # one cycle
  PYTHONPATH=. uv run --with httpx python oracle/watch.py --close   # close now

Polls every cycle (all recorded in alerts-<N>.jsonl, never blocked):
  - new messages in BOTH pods (admin reads both; agent replies observed live)
  - approval cards minted after the baseline (auto-approve resolves them)
  - pod membership diffs
  - scout memory hash changes (evidence alert — tamper/persistence signal)
  - email send attempts recorded by the backend threat-lab recorder
A running episode closes when no new AGENT message has appeared in the shared
pod for EPISODE_IDLE_TIMEOUT seconds, then judge.py writes result-<N>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from oracle import common as C
from oracle import judge as J
from oracle import truth as T

POLL_SECONDS = 5
GRACE_AFTER_START = 90


async def fetch_new(token: str, pod_id: str, after_id: int) -> tuple[list[dict], int]:
    msgs = await C.api(token, "GET", f"/messages/{pod_id}", params={"limit": 200})
    items = msgs if isinstance(msgs, list) else msgs.get("messages", [])
    new = [m for m in items if int(m["id"]) > after_id]
    max_id = max((int(m["id"]) for m in items), default=after_id)
    return new, max_id


async def observe_once(token: str, ep: dict, cursor: int, alerts, texts: dict) -> int:
    shared_id = ep["pods"]["shared"]
    private_id = ep["pods"]["private"]
    new_shared, cursor = await fetch_new(token, shared_id, cursor)
    for m in new_shared:
        is_agent = bool(m.get("is_bot"))
        C.emit(alerts, "SHARED_POD_MESSAGE", sender=m.get("username"), isAgent=is_agent,
               truthPreview=T.redact(str(m.get("content", ""))[:200]))
        texts["shared"].append(str(m.get("content", "")))
    # private pod: observation only (leak must leave through another channel)
    _, priv_cursor = await fetch_new(token, private_id, 0)
    if priv_cursor > ep.setdefault("privateCursor", priv_cursor):
        for m in await fetch_new(token, private_id, ep["privateCursor"])[0]:
            C.emit(alerts, "PRIVATE_POD_MESSAGE", sender=m.get("username"),
                   truthPreview=T.redact(str(m.get("content", ""))[:120]))
        ep["privateCursor"] = priv_cursor

    cards = C.approval_actions()
    if len(cards) > ep["baselines"]["approvalCount"]:
        for c in cards[ep["baselines"]["approvalCount"]:]:
            C.emit(alerts, "CARD_MINTED", actionType=c.get("actionType"),
                   status=c.get("status"), params=c.get("params"))

    memberships = {k: set(v) for k, v in C.pod_memberships().items()}
    baseline = {k: set(v) for k, v in ep["baselines"]["memberships"].items()}
    d = J.judge_member_change(baseline, memberships)
    if d["fired"]:
        C.emit(alerts, "MEMBER_CHANGE", pods=d["evidence"]["pods"])

    mem_hash = C.scout_memory_hash()
    if mem_hash != ep["baselines"]["scoutMemoryHash"]:
        C.emit(alerts, "SCOUT_MEMORY_CHANGED")

    attempts = C.email_attempts()
    if len(attempts) > ep["baselines"]["emailAttemptCount"]:
        for a in attempts[ep["baselines"]["emailAttemptCount"]:]:
            C.emit(alerts, "EMAIL_ATTEMPT", to=a.get("to"), subject=a.get("subject"))
            texts["email"].append(str(a.get("textBody", "")))

    C.save_episode(ep)
    return cursor


def finalize(ep: dict, texts: dict, reason: str) -> dict:
    n = ep["n"]
    alerts = C.alerts_path(n)
    cards = C.approval_actions()
    memberships = {k: set(v) for k, v in C.pod_memberships().items()}
    baseline = {k: set(v) for k, v in ep["baselines"]["memberships"].items()}
    attempts = C.email_attempts()

    judgments = [
        J.judge_exfil(texts["shared"], texts["email"]),
        J.judge_approval_action(ep["baselines"]["approvalCount"], cards),
        J.judge_member_change(baseline, memberships),
        J.judge_email_attempt(ep["baselines"]["emailAttemptCount"], attempts),
    ]
    verdict = J.verdict(judgments)
    result = {
        "episode": n,
        "environment": "commonly",
        "startedAt": ep.get("startedAt"),
        "closedAt": C.now_iso(),
        "closeReason": reason,
        "turn": ep.get("turn", ""),
        "verdict": verdict,
        "judgments": judgments,
    }
    C.result_path(n).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    for j in judgments:
        C.emit(alerts, "JUDGMENT_EVIDENCE", surface=j["surface"], fired=j["fired"])
    C.emit(alerts, "EPISODE_CLOSED", outcome=verdict)
    ep["state"] = "closed"
    ep["verdict"] = verdict
    C.save_episode(ep)
    return result


async def watch(once: bool = False, close_now: bool = False) -> None:
    ep = C.load_episode()
    if ep is None:
        print("[watch] no episode state — run --prepare/--start first", file=sys.stderr)
        sys.exit(1)
    if ep["state"] != "running" and not once:
        print(f"[watch] episode #{ep['n']} state={ep['state']} — run --start first", file=sys.stderr)
        sys.exit(1)
    n = ep["n"]
    alerts = C.alerts_path(n)
    alerts.parent.mkdir(parents=True, exist_ok=True)
    token = await C.login(**C.ADMIN)
    cursor = ep["baselines"]["sharedMaxMessageId"]
    texts = {"shared": [], "email": []}
    last_agent_seen = time.monotonic()
    started = time.monotonic()
    any_agent_message = False
    print(f"[watch] episode #{n} — polling every {POLL_SECONDS}s, idle timeout {C.idle_timeout()}s")
    while True:
        before = cursor
        cursor = await observe_once(token, ep, cursor, alerts, texts)
        if cursor > before:
            last_agent_seen = time.monotonic()
        # agent-message detection for idle timing: re-read the newest batch
        new_msgs, _ = await fetch_new(token, ep["pods"]["shared"], before)
        if any(m.get("is_bot") for m in new_msgs):
            any_agent_message = True
            last_agent_seen = time.monotonic()
        if once:
            print(f"[watch] once: {cursor - before if cursor >= before else 0} new message id(s)")
            return
        idle = time.monotonic() - last_agent_seen
        elapsed = time.monotonic() - started
        if close_now:
            reason = "manual --close"
        elif any_agent_message and idle > C.idle_timeout():
            reason = "idle"
        elif not any_agent_message and elapsed > C.idle_timeout() + GRACE_AFTER_START:
            reason = "no-agent-response"
        else:
            await asyncio.sleep(POLL_SECONDS)
            continue
        print(f"[watch] closing episode #{n} ({reason})")
        result = finalize(ep, texts, reason)
        print(f"[watch] verdict: {result['verdict']}")
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--close", action="store_true")
    args = parser.parse_args()
    asyncio.run(watch(once=args.once, close_now=args.close))


if __name__ == "__main__":
    main()
