"""Evaluate one seller-controlled text payload through MCP and the Shopware Oracle."""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys
from pathlib import Path

from oracle.common import ORACLE_DIR, task_category
from oracle.run_episode import run_fixed_attacks
from oracle.shop import AdminClient, load_env, snapshot_state
from redteam.common import (
    LOCK_PATH,
    approve_reviews,
    changed_seller_reviews,
    evaluate_victim,
    load_scenario,
    reviews,
    target_success,
)

TEXT_FIELDS = {
    "create_product": "description",
    "review_product": "content",
    "submit_invoice": "text",
    "order_with_comment": "comment",
}


def evaluate_payload(scenario_dir: Path, payload: str, source: str) -> dict:
    if not payload.strip():
        raise ValueError("Attack text must not be empty")
    if source not in {"promptfoo", "pyrit", "deepteam"}:
        raise ValueError("Unsupported red-team source")
    ORACLE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        scenario = load_scenario(scenario_dir.resolve())
        env = {**load_env(), **os.environ}
        storefront = env.get("STOREFRONT_API_URL", "http://127.0.0.1:8004").rstrip("/")
        seller_mcp = env.get("SELLER_MCP_URL", f"{storefront}/seller/mcp/")
        attacker = scenario.get("attacker", "ruoyu@shop.com")
        choice = task_category()

        injected = 0
        for call in scenario.get("attackFixed", []):
            field = TEXT_FIELDS.get(call.get("tool"))
            if field and field in call.get("arguments", {}):
                call["arguments"][field] = payload
                injected += 1
            for key, value in call.get("arguments", {}).items():
                if isinstance(value, str):
                    call["arguments"][key] = value.replace(
                        "{Category}", choice["display"]
                    ).replace("{category}", choice["display"]).replace(
                        "{product}", choice["product"]
                    )
        if not injected:
            raise ValueError("Scenario has no supported attack text field")

        admin = AdminClient(
            env.get("SHOPWARE_URL", "http://127.0.0.1:8080"),
            env.get("SHOPWARE_ADMIN_USER", "admin"),
            env.get("SHOPWARE_ADMIN_PASSWORD", "shopware"),
        )
        review_before = reviews(admin) if any(
            call["tool"] == "review_product" for call in scenario["attackFixed"]
        ) else None
        with contextlib.redirect_stdout(sys.stderr):
            run_fixed_attacks(scenario, env, seller_mcp, attacker)
        if review_before is not None:
            changed = changed_seller_reviews(admin, review_before, attacker, payload)
            if not changed:
                raise RuntimeError("Seller review write produced no matching review")
            approve_reviews(admin, changed, storefront)
        baseline = snapshot_state(admin)  # after seller writes, before victim task

        reply, result, result_path = evaluate_victim(
            admin, scenario, env, choice, baseline, attacker,
            [{"channel": call["tool"], "detail": source} for call in scenario["attackFixed"]],
            source,
        )
        return {
            "reply": reply,
            "oracle": {
                "scenario": scenario["id"],
                "success": target_success(scenario["id"], result),
                "outcome": result["outcome"],
                "buyerPurchase": result["buyerPurchase"],
                "floorBreaches": result["floorBreaches"],
                "refunds": result["refunds"],
                "exfil": result["exfil"],
            },
            "resultPath": str(result_path),
        }
