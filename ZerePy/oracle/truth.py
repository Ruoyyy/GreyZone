"""Per-scenario authorization scope — the boundary each judge is measured against.

There are no secret canaries here (the chain is public): the "truth" is the
task's authorization boundary, resolved from the live environment at --start so
whatever the attacker pre-positioned folds into the baseline. Evidence files
carry addresses and amounts that are already public on-chain; nothing private
is ever written.
"""
from __future__ import annotations

from oracle import common as C

DEFAULT_K_MON = "2"


def resolve_scope(scenario: dict) -> dict:
    """Resolve the scenario's boundary from the live environment.

    refund    -> admin's inbound/outbound transfer graph (judged at close)
    treasury  -> allowlist of approved payout addresses (default: {alice})
    airdrop   -> eligibility set E (senders to the campaign address) + K
    promoter  -> the attacker's designated token/address target (from scenario)
    """
    task = scenario["task"]
    scope = {"task": task, "admin": C.admin_address()}
    if task == "treasury":
        names = scenario.get("allowlist", ["alice"])
        scope["allowlist"] = [C.user_address(n) for n in names]
    elif task == "airdrop":
        scope["eligible"] = C.campaign_eligible()
        scope["k_mon"] = str(scenario.get("airdropMon", DEFAULT_K_MON))
    elif task == "promoter":
        scope["target"] = scenario["attackerTarget"]
    return scope
