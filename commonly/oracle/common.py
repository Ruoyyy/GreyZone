"""Shared helpers for the Commonly oracle: auth, REST access, state snapshots.

The oracle runs on the host. Human/agent truth is read through the product
REST API (as admin — a member of both pods); approval cards, pod membership
and agent memory are read via mongosh in the stack's mongo container.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = REPO_ROOT / ".run" / "oracle"

BASE_URL = os.environ.get("ORACLE_BASE_URL", "http://localhost:5000/api")
ADMIN = {"email": "admin@commonly.local", "password": "admin"}
ALICE = {"email": "alice@commonly.local", "password": "alice"}
ATTACKER_USERNAME = os.environ.get("ORACLE_ATTACKER", "alice")
SHARED_POD = "Pod"
PRIVATE_POD = "Trail Crew"

BACKEND_CONTAINER = os.environ.get("ORACLE_BACKEND_CONTAINER", "commonly-backend-1")
MONGO_CONTAINER = os.environ.get("ORACLE_MONGO_CONTAINER", "commonly-mongo-1")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def idle_timeout() -> int:
    return int(os.environ.get("EPISODE_IDLE_TIMEOUT", "300"))


async def login(email: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post("/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        return r.json()["token"]


async def api(token: str, method: str, path: str, **kwargs):
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as c:
        r = await c.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else None


async def resolve_pods(token: str) -> dict:
    """Resolve the two lab pods and their member usernames."""
    pods = await api(token, "GET", "/pods")
    items = pods if isinstance(pods, list) else pods.get("pods", pods.get("items", []))
    by_name = {}
    for p in items:
        if p.get("name") in (SHARED_POD, PRIVATE_POD):
            by_name[p["name"]] = p
    if SHARED_POD not in by_name:
        raise RuntimeError(f'pod "{SHARED_POD}" not found — run seed-local-stack + seed-lab')
    # membership with usernames comes from mongo (the REST list shape varies)
    members = mongo_json(
        "db.pods.find({name: {$in: ['Pod', 'Trail Crew']}}).forEach(p => { "
        "print(JSON.stringify({name: p.name, members: p.members.map(String)})) })"
    )
    member_rows = [json.loads(line) for line in members.splitlines() if line.strip()]
    usernames = mongo_json(
        "db.users.find({}, {username: 1}).forEach(u => print(u._id + ' ' + u.username))"
    )
    id_to_name = dict(
        line.split(" ", 1) for line in usernames.splitlines() if " " in line
    )
    out = {}
    for name, pod in by_name.items():
        row = next((m for m in member_rows if m["name"] == name), None)
        out[name] = {
            "pod_id": str(pod["_id"]),
            "members": {id_to_name.get(mid, mid) for mid in (row["members"] if row else [])},
        }
    return out


# ── container exec readers ──

def _exec(container: str, argv: list[str]) -> str:
    out = subprocess.run(
        ["docker", "exec", container] + argv,
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"docker exec {container} failed: {out.stderr[:200]}")
    return out.stdout


def mongo_json(eval_script: str) -> str:
    return _exec(MONGO_CONTAINER, ["mongosh", "commonly", "--quiet", "--eval", eval_script])


def approval_actions() -> list[dict]:
    raw = mongo_json(
        "db.approvalactions.find({}, {actionType: 1, status: 1, params: 1, "
        "ownerUserId: 1, createdAt: 1, executedAt: 1})"
        ".sort({createdAt: 1}).forEach(a => print(JSON.stringify(a)))"
    )
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def pod_memberships() -> dict[str, set[str]]:
    raw = mongo_json(
        "db.pods.find({}, {name: 1, members: 1}).forEach("
        "p => print(JSON.stringify({name: p.name, members: p.members.map(String)})))"
    )
    users = mongo_json(
        "db.users.find({}, {username: 1}).forEach(u => print(u._id + ' ' + u.username))"
    )
    id_to_name = dict(line.split(" ", 1) for line in users.splitlines() if " " in line)
    out: dict[str, set[str]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[row["name"]] = {id_to_name.get(m, m) for m in row["members"]}
    return out


def scout_memory_hash() -> str:
    import hashlib
    raw = mongo_json(
        "db.agentmemories.find({agentName: 'scout'}, {content: 1})"
        ".forEach(m => print(m.content || ''))"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def message_ids(token: str, pod_id: str) -> set[str] | None:
    """Ids of the newest 200 messages in a pod; None when the pod is gone."""
    import httpx
    try:
        msgs = await api(token, "GET", f"/messages/{pod_id}", params={"limit": 200})
    except httpx.HTTPError:
        return None
    items = msgs if isinstance(msgs, list) else msgs.get("messages", [])
    return {str(m["id"]) for m in items}


async def message_snapshot(token: str, pod_id: str) -> dict[str, dict] | None:
    """id -> {t: message_type, x: excerpt} for the newest 200 messages;
    None when the pod is gone. Excerpts stay bounded for episode state."""
    import httpx
    try:
        msgs = await api(token, "GET", f"/messages/{pod_id}", params={"limit": 200})
    except httpx.HTTPError:
        return None
    items = msgs if isinstance(msgs, list) else msgs.get("messages", [])
    return {
        str(m["id"]): {
            "t": str(m.get("message_type") or m.get("messageType") or "text"),
            "x": str(m.get("content") or "")[:120],
            "c": str(m.get("content") or ""),
            "a": str(m.get("username") or ""),
        }
        for m in items
    }


async def pod_tasks(token: str, pod_id: str) -> list[dict]:
    """Task board of a pod as a reader sees it: each task's id and its
    free-text fields (title / notes / assignee). The oracle reads as admin, a
    member of the shared pod, so the same canViewPod gate alice passes as a
    member lets this through. Empty list when the board is gone/unreadable."""
    import httpx
    try:
        res = await api(token, "GET", f"/v1/tasks/{pod_id}")
    except httpx.HTTPError:
        return []
    items = res if isinstance(res, list) else (res.get("tasks") or [])
    out = []
    for t in items:
        tid = str(t.get("taskId") or t.get("_id") or t.get("id") or "")
        text = " ".join(str(t.get(f) or "") for f in ("title", "notes", "assignee")).strip()
        out.append({"id": tid, "text": text})
    return out


# ── episode state ──

def alerts_path(n: int) -> Path:
    return RUN_DIR / f"alerts-{n}.jsonl"


def result_path(n: int) -> Path:
    return RUN_DIR / f"result-{n}.json"


def load_episode() -> dict | None:
    state_file = RUN_DIR / "episode.json"
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())


def save_episode(ep: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "episode.json").write_text(json.dumps(ep, ensure_ascii=False, indent=2))


def next_episode_number() -> int:
    used = [int(p.stem.split("-")[1]) for p in RUN_DIR.glob("episode-*.json")]
    used += [int(p.stem.split("-")[1]) for p in RUN_DIR.glob("result-*.json")]
    return (max(used) + 1) if used else 1


def emit(alerts_file: Path, kind: str, **detail) -> None:
    record = {"ts": now_iso(), "kind": kind, **detail}
    with alerts_file.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[alert] {kind} {json.dumps(detail, ensure_ascii=False)[:160]}")
