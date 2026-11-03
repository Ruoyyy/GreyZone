"""MCP bridge exposing alice's Clawith group-chat capabilities to external agents.

Design (one environment = one token):
- At startup this service mints a long-lived HS256 JWT for alice using the
  same JWT_SECRET_KEY the backend uses. The token exists only in this
  process (and once in this container's logs); it is never written to disk
  or committed anywhere.
- External MCP clients authenticate with MCP_API_KEY (auto-generated at
  startup when unset) and never see the alice JWT.
- Every tool call hits the backend REST API as alice, so all actions are
  attributed to alice in message history and workspace revisions.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("mcp-bridge")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000").rstrip("/") + "/api"
JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ALICE_USER_ID = os.environ["ALICE_USER_ID"]
ALICE_ROLE = os.environ.get("ALICE_ROLE", "member")
ALICE_TENANT_ID = os.environ["ALICE_TENANT_ID"]
TOKEN_TTL_DAYS = int(os.environ.get("TOKEN_TTL_DAYS", "3650"))
MCP_PORT = int(os.environ.get("MCP_PORT", "3010"))


def mint_alice_token() -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
    payload = {
        "sub": ALICE_USER_ID,
        "role": ALICE_ROLE,
        "exp": exp,
        "tenant_id": ALICE_TENANT_ID,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


import uuid as _uuid

def _validate_env() -> None:
    """Fail fast with a clear message when .env ids are still placeholders
    (start.sh fills them after the bootstrap; do not start the bridge before)."""
    for name in ("ALICE_USER_ID", "ALICE_TENANT_ID"):
        try:
            _uuid.UUID(os.environ[name])
        except (KeyError, ValueError):
            raise SystemExit(
                f"mcp-bridge: {name} is missing or not a UUID — run ./start.sh "
                "(the bootstrap writes real ids into .env before this service starts)"
            )

_validate_env()

ALICE_TOKEN = mint_alice_token()
MCP_API_KEY = os.environ.get("MCP_API_KEY") or secrets.token_urlsafe(32)

log.info("minted alice JWT (exp=%dd): %s", TOKEN_TTL_DAYS, ALICE_TOKEN)
log.info("MCP API key: %s", MCP_API_KEY)


class StaticKeyVerifier(TokenVerifier):
    """Accept exactly one bearer key: the per-environment MCP key."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, MCP_API_KEY):
            return AccessToken(token=token, client_id="external-agent", scopes=[])
        return None


mcp = FastMCP(name="clawith-alice-bridge", auth=StaticKeyVerifier())

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=BACKEND_URL,
            headers={"Authorization": f"Bearer {ALICE_TOKEN}"},
            timeout=30,
        )
    return _client


async def _call(method: str, path: str, **kwargs) -> str:
    resp = await client().request(method, path, **kwargs)
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise RuntimeError(f"backend {resp.status_code} on {path}: {detail}")
    if not resp.content:
        return "ok"
    return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool
async def list_groups() -> str:
    """List all groups alice belongs to (id, name, description)."""
    return await _call("GET", "/groups")


@mcp.tool
async def list_group_members(group_id: str) -> str:
    """List members of a group. Returns participant_id + display_name + role,
    needed to @-mention agents in send_group_message."""
    return await _call("GET", f"/groups/{group_id}/members")


@mcp.tool
async def list_sessions(group_id: str) -> str:
    """List chat sessions of a group (session_id, title, is_primary)."""
    return await _call("GET", f"/groups/{group_id}/sessions")


@mcp.tool
async def list_group_messages(
    group_id: str,
    session_id: str,
    after: str | None = None,
    limit: int = 100,
) -> str:
    """Read alice-visible group messages. Pass the last cursor as after to
    continue a multi-turn conversation without replaying older messages."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    params: dict = {"limit": limit}
    if after:
        params["after"] = after
    return await _call("GET", f"/groups/{group_id}/sessions/{session_id}/messages", params=params)


@mcp.tool
async def send_group_message(
    group_id: str,
    session_id: str,
    content: str,
    mention_participant_ids: list[str] | None = None,
) -> str:
    """Send a message to a group chat as alice. Use participant_ids from
    list_group_members to @-mention agents."""
    body: dict = {
        "content": content,
        "mentions": [{"participant_id": pid} for pid in (mention_participant_ids or [])],
        "message_id": str(uuid.uuid4()),
    }
    return await _call("POST", f"/groups/{group_id}/sessions/{session_id}/messages", json=body)


@mcp.tool
async def list_workspace(group_id: str, path: str = "") -> str:
    """List entries of the group shared workspace under one path."""
    return await _call("GET", f"/groups/{group_id}/workspace", params={"path": path})


@mcp.tool
async def read_workspace_file(group_id: str, path: str) -> str:
    """Read one UTF-8 text file from the group shared workspace."""
    return await _call("GET", f"/groups/{group_id}/workspace/file", params={"path": path})


@mcp.tool
async def write_workspace_file(group_id: str, path: str, content: str) -> str:
    """Create or overwrite one UTF-8 text file in the group shared workspace."""
    return await _call(
        "PUT",
        f"/groups/{group_id}/workspace/file",
        params={"path": path},
        json={"content": content},
    )


@mcp.tool
async def upload_workspace_file(group_id: str, path: str, content_base64: str) -> str:
    """Upload one binary file (base64-encoded) into the group shared workspace."""
    data = base64.b64decode(content_base64)
    resp = await client().post(
        f"/groups/{group_id}/workspace/upload",
        params={"path": path},
        files={"file": (path.rsplit("/", 1)[-1], data)},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"backend {resp.status_code}: {resp.text[:500]}")
    return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool
async def delete_workspace_file(group_id: str, path: str, expected_version_token: str | None = None) -> str:
    """Delete one file from the group shared workspace (version token optional)."""
    params = {"path": path}
    if expected_version_token:
        params["expected_version_token"] = expected_version_token
    return await _call("DELETE", f"/groups/{group_id}/workspace/file", params=params)


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=MCP_PORT)
