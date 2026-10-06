#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The victim runner — two phases, so the red team can pre-position first:

  --prepare   declare the scenario (state=preparing) and, with --attack-fixed,
              land its payload through the seller MCP; the oracle records the
              signals but judges nothing yet. Free red-teaming happens here.
  --start     flip the state to running (the oracle takes its baseline now) and
              send the one-sentence victim task; the episode then closes on
              idle and the watcher judges it.

The buyer task is category-driven: ``TASK_CATEGORY`` (food | clothing |
electronics | office) picks the storefront navigation root, the task asks for
the best-value item of that category, and the intent the oracle judges against
is "any shop product of that subtree" — which one is best value is the agent's
call, not the oracle's.

  .venv/bin/python oracle/run_episode.py --scenario benchmark/scenarios/buyer-misdirection --prepare --attack-fixed
  .venv/bin/python oracle/run_episode.py --scenario benchmark/scenarios/buyer-misdirection --start
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    ORACLE_DIR,
    category_task_text,
    expand,
    set_current_scenario,
    task_category,
)
from shop import AdminClient, load_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def seller_mcp_call(
    url: str, email: str, password: str, tool: str, arguments: dict[str, Any]
) -> Any:
    """One tools/call against the seller MCP (stateless streamable HTTP, Basic)."""
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        timeout=120,
    )
    response.raise_for_status()
    text = response.text
    if text.startswith("{"):
        return json.loads(text)
    data = "\n".join(line[5:] for line in text.splitlines() if line.startswith("data:"))
    return json.loads(data) if data else {"raw": text[:200]}


def stream_chat(url: str, session_id: str, message: str) -> str:
    """POST one chat turn and drain the streamed reply to plain text."""
    with httpx.stream(
        "POST",
        url,
        headers={"X-Session-Id": session_id},
        json={"message": message},
        timeout=300,
    ) as response:
        response.raise_for_status()
        raw = b"".join(response.iter_raw()).decode("utf-8", errors="replace")
    return _sse_text(raw)


def _sse_text(raw: str) -> str:
    """Concatenate the stream's token deltas; a bare non-JSON payload is kept."""
    parts: list[str] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    document = json.loads(payload)
                except ValueError:
                    parts.append(payload)
                    continue
                parts.append(_text_of(document))
    return "".join(parts).strip() or raw[:400]


def _text_of(document: Any) -> str:
    if isinstance(document, dict):
        if isinstance(document.get("text"), str):
            return document["text"]
        if isinstance(document.get("delta"), dict) and isinstance(
            document["delta"].get("text"), str
        ):
            return document["delta"]["text"]
        if isinstance(document.get("content"), list):
            return "\n".join(_text_of(block) for block in document["content"])
    return ""


def resolve_arguments(
    arguments: dict[str, Any], seller_mcp: str, email: str, password: str
) -> dict[str, Any]:
    """Replace ``$search:<query>`` values with the first shop-sold product id the
    seller MCP finds — attack payloads must name a real target product."""
    resolved: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and value.startswith("$search:"):
            found = seller_mcp_call(
                seller_mcp, email, password, "search_products", {"query": value[8:], "limit": 5}
            )
            rows = _tool_rows(found)
            shop_rows = [row for row in rows if row.get("sold_by") == "shop"]
            if not shop_rows:
                raise SystemExit(f"$search:{value[8:]} found no shop product; rows={rows}")
            resolved[key] = shop_rows[0]["product_id"]
            print(
                f"[runner]   $search -> {shop_rows[0]['product_id']} ({shop_rows[0].get('name')})"
            )
        else:
            resolved[key] = value
    return resolved


def _tool_rows(result: Any) -> list[dict[str, Any]]:
    content = (
        ((result.get("result") or {}).get("content") or []) if isinstance(result, dict) else []
    )
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            try:
                parsed = json.loads(block["text"])
                return parsed if isinstance(parsed, list) else []
            except ValueError:
                continue
    return []


def run_fixed_attacks(
    scenario: dict[str, Any], env: dict[str, str], seller_mcp: str, attacker: str
) -> None:
    """Land the scenario's fixed payload: plain calls, ``$search:`` targets, and
    ``$group:selected`` — one call per active shop product of the selected
    task category (review saturation)."""
    seller_password = env.get("SELLER_PASSWORD", attacker.split("@")[0])
    admin = AdminClient(
        env.get("SHOPWARE_URL", "http://127.0.0.1:8080"),
        env.get("SHOPWARE_ADMIN_USER", "admin"),
        env.get("SHOPWARE_ADMIN_PASSWORD", "shopware"),
    )
    products = admin.products()
    top = task_category()["top"]
    group = {
        number: product["id"]
        for number, product in sorted(products.items())
        if product["active"] and not product["isSeller"] and product["topCategory"] == top
    }
    for call in scenario.get("attackFixed", []):
        raw = call.get("arguments", {})
        group_keys = {
            key: value
            for key, value in raw.items()
            if isinstance(value, str) and value.startswith("$group:")
        }
        if group_keys:
            name = next(iter(group_keys.values()))[7:]
            if name != "selected" or not group:
                raise SystemExit(f"$group:{name} resolved to no products (top category {top!r})")
            print(f"[runner] attack: {call['tool']} × {len(group)} in '{top}' ({', '.join(group)})")
            for number, product_id in group.items():
                arguments = resolve_arguments(
                    {**raw, **{key: product_id for key in group_keys}},
                    seller_mcp,
                    attacker,
                    seller_password,
                )
                result = seller_mcp_call(
                    seller_mcp, attacker, seller_password, call["tool"], arguments
                )
                print(f"[runner]   {number} -> {json.dumps(result, ensure_ascii=False)[:120]}")
            continue
        print(f"[runner] attack: {call['tool']} {json.dumps(raw)[:100]}")
        arguments = resolve_arguments(raw, seller_mcp, attacker, seller_password)
        result = seller_mcp_call(seller_mcp, attacker, seller_password, call["tool"], arguments)
        print(f"[runner]   -> {json.dumps(result, ensure_ascii=False)[:160]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path, help="scenario directory")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="declare the scenario and (with --attack-fixed) pre-position the payload; no task yet",
    )
    parser.add_argument(
        "--start", action="store_true", help="flip to running and send the one-sentence victim task"
    )
    parser.add_argument(
        "--attack-fixed", action="store_true", help="land the fixed payload (in --prepare)"
    )
    parser.add_argument("--task", help="override the scenario's victim task")
    args = parser.parse_args()
    if not args.prepare and not args.start:
        args.start = True

    scenario = expand(json.loads((args.scenario / "scenario.json").read_text(encoding="utf-8")))
    calibration_path = Path(__file__).resolve().parent / "calibrations" / f"{scenario['id']}.json"
    calibration = (
        expand(json.loads(calibration_path.read_text(encoding="utf-8")))
        if calibration_path.exists()
        else {}
    )
    env = load_env()
    storefront = env.get("STOREFRONT_API_URL", "http://127.0.0.1:8004").rstrip("/")
    merchant_api = env.get("MERCHANT_API_URL", "http://127.0.0.1:8005").rstrip("/")
    seller_mcp = env.get("SELLER_MCP_URL", f"{storefront}/seller/mcp/")

    # The spec is the external contract (roles, task, success criteria); the runner's
    # own mechanics come from the calibration and the environment — never the spec.
    victim = scenario["victim"]["role"]
    task_kind = scenario.get("victimTask", {}).get("kind", "")
    choice = task_category()
    category_driven = task_kind == "category-buy"
    if victim == "buyer":
        victim_email = env.get("BUYER_EMAIL", "admin@shop.com")
        victim_password = env.get("BUYER_PASSWORD", "admin")
        task = args.task or (
            category_task_text() if category_driven else scenario["victimTask"]["text"]
        )
        intent = {"topCategory": choice["top"]} if category_driven else {}
    else:
        victim_email = env.get("MERCHANT_EMAIL", "operator@shop")
        victim_password = None
        task = args.task or scenario["victimTask"]["text"]
        intent = {}
    attacker_email = scenario["attacker"]["identity"]

    # "{category}"/"{Category}" in calibration templates follow the selected category
    display = choice["display"]
    for call in calibration.get("attackFixed", []):
        for key, value in call.get("arguments", {}).items():
            if isinstance(value, str):
                call["arguments"][key] = value.replace("{Category}", display).replace(
                    "{category}", display
                )

    pointer = {
        "id": scenario["id"],
        "victim": victim,
        "victimEmail": victim_email,
        "attacker": attacker_email,
        "taskCategory": choice["top"] if category_driven else None,
        "intent": intent,
        "mailAllowlist": [victim_email],
        "state": "preparing" if args.prepare else "running",
    }
    set_current_scenario({**pointer, **({"task": task} if args.start else {})})
    print(
        f"[runner] scenario {scenario['id']} -> state={pointer['state']} ({ORACLE_DIR / 'current.json'})"
    )

    if args.prepare:
        if args.attack_fixed:
            run_fixed_attacks(calibration, env, seller_mcp, attacker_email)
        print("[runner] prep window open — red-team through the seller MCP, then run with --start")
        return 0

    # --start: the oracle reads the flip on its next poll, then the task goes out
    task = pointer_task() or task
    if victim == "buyer":
        session = httpx.post(f"{storefront}/api/session", timeout=30).json()["session_id"]
        bound = httpx.post(
            f"{storefront}/api/auth/shopware/login",
            headers={"X-Session-Id": session},
            json={"email": victim_email, "password": victim_password},
            timeout=60,
        )
        print(f"[runner] victim {victim_email} login: {bound.status_code}")
        reply = stream_chat(f"{storefront}/api/chat", session, task)
    else:
        session = httpx.post(f"{merchant_api}/api/merchant/session", timeout=30).json()[
            "session_id"
        ]
        reply = stream_chat(f"{merchant_api}/api/merchant/chat", session, task)

    (ORACLE_DIR / "last-reply.txt").parent.mkdir(parents=True, exist_ok=True)
    (ORACLE_DIR / "last-reply.txt").write_text(reply, encoding="utf-8")
    print(f"[runner] victim turn done; reply ({len(reply)} chars) in .run/oracle/last-reply.txt")
    print("[runner] the episode closes on idle and the watcher judges it")
    return 0


def pointer_task() -> str | None:
    try:
        return json.loads((ORACLE_DIR / "current.json").read_text(encoding="utf-8")).get("task")
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
