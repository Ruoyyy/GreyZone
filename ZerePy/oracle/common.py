"""Shared helpers for the ZerePy oracle: observe-only reads + episode state.

The oracle runs on the host and only READS the environment — it never posts,
transfers, or mutates anything. Chain facts come from the simulated explorer
(the Etherscan v2-compatible txlist the agent itself uses) and the RPC; the
agent's posts come from the X service's /__admin/state. Addresses the oracle
needs as truth (admin, bob, alice) are read from the local key files the
environment was seeded from, the same way the siblings read their stores.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = REPO_ROOT / ".run" / "oracle"

MOCK_URL = os.environ.get("ORACLE_MOCK_URL", "http://localhost:8788")
RPC_URL = os.environ.get("ORACLE_RPC_URL", "http://localhost:8545")
CAMPAIGN_ADDRESS = os.environ.get("ORACLE_CAMPAIGN", "0x9e1AaC48Aa0299b962103aE3811B56df2393ae29")
CHAIN_ID = 10143


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def idle_timeout() -> int:
    return int(os.environ.get("EPISODE_IDLE_TIMEOUT", "180"))


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _rpc(method: str, *params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode()
    req = urllib.request.Request(RPC_URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["result"]


# ── chain reads (observe-only) ──

def block_height() -> int:
    return int(_rpc("eth_blockNumber"), 16)


def address_transfers(address: str) -> list[dict]:
    """Normalized native transfers touching an address, via the explorer txlist
    (what the agent sees too). Newest first; failed transfers kept but flagged."""
    q = urllib.parse.urlencode({
        "chainid": CHAIN_ID, "module": "account", "action": "txlist",
        "address": address, "sort": "desc", "apikey": "oracle",
    })
    data = _get_json(f"{MOCK_URL}/v2/api?{q}")
    if data.get("status") != "1":
        return []
    out = []
    for t in data["result"]:
        out.append({
            "hash": t["hash"], "from": t["from"], "to": t["to"],
            "amount": str(int(t["value"]) / 10**18), "ok": t["txreceipt_status"] == "1",
            "block": int(t["blockNumber"]), "timestamp": int(t["timeStamp"]),
        })
    return out


def campaign_eligible() -> list[str]:
    """Eligibility set E: addresses that sent a tx to the campaign address."""
    return sorted({t["from"].lower() for t in address_transfers(CAMPAIGN_ADDRESS)
                   if t["ok"] and t["to"].lower() == CAMPAIGN_ADDRESS.lower()})


# ── X reads (observe-only) ──

def admin_user_id() -> str:
    return _get_json(f"{MOCK_URL}/__admin/state")["me"]


def admin_posts(after_id: str | None = None) -> list[dict]:
    """admin's own tweets (newest-first), optionally only those after a cursor id."""
    state = _get_json(f"{MOCK_URL}/__admin/state")
    me = state["me"]
    posts = [t for t in state["tweets"] if t["author_id"] == me]
    if after_id is not None:
        posts = [t for t in posts if int(t["id"]) > int(after_id)]
    return sorted(posts, key=lambda t: int(t["id"]), reverse=True)


def max_post_id() -> str | None:
    posts = admin_posts()
    return posts[0]["id"] if posts else None


# ── local truth (key files the environment was seeded from) ──

def user_address(name: str) -> str:
    keys = json.loads((REPO_ROOT / "chain" / "users.keys.json").read_text())
    return keys[name]["address"]


def admin_address() -> str:
    from eth_account import Account
    for line in (REPO_ROOT / ".env").read_text().splitlines():
        if line.startswith("MONAD_PRIVATE_KEY="):
            return Account.from_key(line.split("=", 1)[1].strip()).address
    raise RuntimeError("MONAD_PRIVATE_KEY not found in .env")


# ── episode state / artifacts ──

def alerts_path(n: int) -> Path:
    return RUN_DIR / f"alerts-{n}.jsonl"


def result_path(n: int) -> Path:
    return RUN_DIR / f"result-{n}.json"


def load_episode() -> dict | None:
    f = RUN_DIR / "episode.json"
    return json.loads(f.read_text()) if f.exists() else None


def save_episode(ep: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "episode.json").write_text(json.dumps(ep, ensure_ascii=False, indent=2))


def next_episode_number() -> int:
    used = [int(p.stem.split("-")[1]) for p in RUN_DIR.glob("result-*.json")]
    return (max(used) + 1) if used else 1


def emit(alerts_file: Path, kind: str, **detail) -> None:
    record = {"ts": now_iso(), "kind": kind, **detail}
    alerts_file.parent.mkdir(parents=True, exist_ok=True)
    with alerts_file.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[alert] {kind} {json.dumps(detail, ensure_ascii=False)[:160]}")


def _result_helpers():
    import sys
    from pathlib import Path as ResultPath
    try:
        import oracle_result
    except ModuleNotFoundError:
        sys.path.insert(0, str(ResultPath(__file__).resolve().parents[2] / "scripts"))
        import oracle_result
    return oracle_result


def evaluation(*args, **kwargs):
    """Attach the shared contract while retaining the application's native fields."""
    from pathlib import Path as ResultPath
    helpers = _result_helpers()
    kwargs.setdefault("victim_model", helpers.configured_model(ResultPath(__file__).resolve().parents[1]))
    return helpers.build_evaluation(*args, **kwargs)


def write_result_json(path, payload):
    return _result_helpers().write_json(path, payload)
