# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""MCP Streamable-HTTP client against an in-process fake of Shopware's server (the
handshake and error texts come from the live 6.7.13 ``/api/_mcp`` and ``/ucp/mcp``)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from shopware_common.mcp_client import (
    McpClient,
    McpError,
    McpToolError,
    parse_rpc_body,
)

SESSION_REQUIRED = {
    "jsonrpc": "2.0",
    "id": "",
    "error": {
        "code": -32600,
        "message": "A valid session id is REQUIRED for non-initialize requests.",
    },
}
SESSION_EXPIRED = {
    "jsonrpc": "2.0",
    "id": "",
    "error": {"code": -32600, "message": "Session not found or has expired."},
}


class FakeMcpServer:
    """Streamable-HTTP semantics of ``mcp/sdk`` as Shopware ships it."""

    def __init__(self, *, sse: bool = False, toolsets: bool = False) -> None:
        self.sessions: set[str] = set()
        self.requests: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []
        self.sse = sse
        self.toolsets = toolsets
        self.enabled: set[str] = set()
        self._counter = 0
        self.deleted: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.headers_seen.append(dict(request.headers))
        if request.method == "DELETE":
            self.deleted.append(request.headers.get("mcp-session-id", ""))
            self.sessions.discard(request.headers.get("mcp-session-id", ""))
            return httpx.Response(200)
        body = json.loads(request.content)
        self.requests.append(body)
        method = body.get("method")
        session = request.headers.get("mcp-session-id")
        if method == "initialize":
            if session:
                return httpx.Response(400, json=SESSION_REQUIRED)
            if body["params"]["clientInfo"].get("version") in {"", "0"}:
                return httpx.Response(400, json=SESSION_REQUIRED)  # PHP empty("0") quirk
            self._counter += 1
            sid = f"sess-{self._counter}"
            self.sessions.add(sid)
            return self._reply(
                body["id"],
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "shopware", "version": "6.7.13.0"},
                },
                headers={"Mcp-Session-Id": sid},
            )
        if not session:
            return httpx.Response(400, json=SESSION_REQUIRED)
        if session not in self.sessions:
            return httpx.Response(404, json=SESSION_EXPIRED)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            tools = [{"name": "shopware-entity-search", "inputSchema": {"type": "object"}}]
            if self.toolsets:
                tools = [
                    {"name": "shopware-toolsets-list", "inputSchema": {"type": "object"}},
                    {
                        "name": "shopware-toolset-enable",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"toolset": {"type": "string"}},
                            "required": ["toolset"],
                        },
                    },
                ] + [
                    {"name": name, "inputSchema": {"type": "object"}}
                    for name in sorted(self.enabled)
                ]
            return self._reply(body["id"], {"tools": tools})
        if method == "tools/call":
            name = body["params"]["name"]
            if name == "shopware-toolsets-list":
                return self._tool(
                    body["id"],
                    {"toolsets": [{"name": "entity", "tools": ["shopware-entity-read"]}]},
                )
            if name == "shopware-toolset-enable":
                self.enabled.add("shopware-entity-read")
                return self._tool(body["id"], {"enabled": body["params"]["arguments"]})
            if name == "boom":
                return self._tool(body["id"], {"success": False, "error": "nope"}, is_error=True)
            if name == "expire":
                self.sessions.discard(session)
                return httpx.Response(404, json=SESSION_EXPIRED)
            return self._tool(body["id"], {"success": True, "data": body["params"]["arguments"]})
        return httpx.Response(
            400,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32601, "message": "unknown"},
            },
        )

    def _tool(self, rid: Any, payload: dict[str, Any], *, is_error: bool = False) -> httpx.Response:
        return self._reply(
            rid,
            {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": is_error},
        )

    def _reply(
        self, rid: Any, result: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        message = {"jsonrpc": "2.0", "id": rid, "result": result}
        if self.sse:
            text = f"event: message\ndata: {json.dumps(message)}\n\n"
            return httpx.Response(
                200, text=text, headers={"content-type": "text/event-stream", **(headers or {})}
            )
        return httpx.Response(200, json=message, headers=headers or {})


def make_client(server: FakeMcpServer, **kwargs: Any) -> McpClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    return McpClient("http://shop.test/api/_mcp", http=http, retry_backoff=0, **kwargs)


async def test_handshake_then_tools_call_carries_session_and_protocol():
    server = FakeMcpServer()
    client = make_client(server, headers={"Authorization": "Bearer t"})
    result = await client.call_tool("shopware-entity-search", {"entity": "product"})
    assert result.json() == {"success": True, "data": {"entity": "product"}}
    methods = [r["method"] for r in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert client.session_id == "sess-1"
    assert client.protocol_version == "2025-11-25"
    call_headers = server.headers_seen[-1]
    assert call_headers["mcp-session-id"] == "sess-1"
    assert call_headers["mcp-protocol-version"] == "2025-11-25"
    assert call_headers["authorization"] == "Bearer t"
    assert "text/event-stream" in call_headers["accept"]
    await client.close()
    assert server.deleted == ["sess-1"] and client.session_id is None


async def test_sse_bodies_are_parsed():
    server = FakeMcpServer(sse=True)
    client = make_client(server)
    tools = await client.list_tools()
    assert [t["name"] for t in tools] == ["shopware-entity-search"]
    result = await client.call_tool("shopware-entity-search", {"entity": "tax"})
    assert result.json()["data"] == {"entity": "tax"}


async def test_expired_session_is_reinitialised_once():
    server = FakeMcpServer()
    client = make_client(server)
    await client.ensure_session()
    with pytest.raises(McpError):
        await client.call_tool("expire", {})  # server drops the session mid-call
    # Next call: 404 → re-initialise → replay.
    result = await client.call_tool("shopware-entity-search", {"entity": "x"})
    assert result.json()["success"] is True
    assert client.session_id == "sess-3"
    assert [r["method"] for r in server.requests].count("initialize") == 3


async def test_concurrent_calls_on_one_session_each_match_their_own_answer():
    # The portal's dashboard, overview and ledger reads hit one session together. Shopware's
    # server answers one of two simultaneous calls on a session with an empty body, so the
    # client runs them one at a time, and each answer is matched to its own id.
    server = FakeMcpServer()
    in_flight = 0
    peak = 0

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        body = json.loads(request.content)
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            if body.get("method") == "tools/call" and body["params"]["arguments"].get("slow"):
                await asyncio.sleep(0.05)
            return server.handler(request)
        finally:
            in_flight -= 1

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    client = McpClient("http://shop.test/api/_mcp", http=http, retry_backoff=0)
    await client.ensure_session()
    slow, fast = await asyncio.gather(
        client.call_tool("shopware-entity-aggregate", {"slow": True, "entity": "order"}),
        client.call_tool("shopware-entity-search", {"entity": "product"}),
    )
    assert slow.json()["data"] == {"slow": True, "entity": "order"}
    assert fast.json()["data"] == {"entity": "product"}
    ids = [r["id"] for r in server.requests if r["method"] == "tools/call"]
    assert len(set(ids)) == 2
    assert peak == 1


async def test_tool_error_raises_with_payload():
    client = make_client(FakeMcpServer())
    with pytest.raises(McpToolError) as info:
        await client.call_tool("boom", {})
    assert info.value.result.json() == {"success": False, "error": "nope"}
    lenient = await client.call_tool("boom", {}, raise_on_tool_error=False)
    assert lenient.is_error


async def test_progressive_discovery_enables_toolset():
    server = FakeMcpServer(toolsets=True)
    client = make_client(server)
    assert "shopware-entity-read" not in await client.tool_names()
    assert await client.ensure_tool("shopware-entity-read") is True
    assert "shopware-entity-read" in await client.tool_names()
    enable = next(
        r
        for r in server.requests
        if r["method"] == "tools/call" and r["params"]["name"] == "shopware-toolset-enable"
    )
    assert enable["params"]["arguments"] == {"toolset": "entity"}
    assert await client.ensure_tool("does-not-exist") is False


async def test_request_hook_sees_exact_bytes():
    server = FakeMcpServer()
    seen: list[bytes] = []

    def hook(method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, str]:
        seen.append(body)
        return {"Signature": "sig=:x:"}

    client = make_client(server, request_hook=hook)
    await client.call_tool("shopware-entity-search", {}, extra_headers={"Idempotency-Key": "k"})
    assert all(json.loads(b) for b in seen)
    assert server.headers_seen[-1]["signature"] == "sig=:x:"
    assert server.headers_seen[-1]["idempotency-key"] == "k"


def test_parse_rpc_body_picks_matching_id_in_multi_event_stream():
    text = (
        'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
    )
    response = httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    assert parse_rpc_body(response, request_id=7) == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"ok": True},
    }
    assert parse_rpc_body(httpx.Response(202)) is None
