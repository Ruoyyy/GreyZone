# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""MCP Streamable-HTTP client (spec 2025-06-18) for Shopware's two MCP servers.

Both ``/ucp/mcp`` (shopper, proxied to the Store API MCP server) and ``/api/_mcp``
(merchant, Admin API MCP server) speak the same handshake:

1. ``initialize`` — the response carries ``Mcp-Session-Id`` (header) and the protocol
   version the server negotiated (body).
2. ``notifications/initialized`` — a notification; the server answers ``202``.
3. ``tools/list`` (paged with ``cursor``) and ``tools/call``.
4. ``DELETE`` with the session id ends the session.

The server may answer with ``application/json`` or a single-event ``text/event-stream``
body; both are handled. A session the server forgot (``404 Session not found`` /
``400 A valid session id is REQUIRED``) is re-initialised once and the request replayed.
The negotiated ``MCP-Protocol-Version`` is echoed on every follow-up request.

Shopware 6.7.11–6.7.13 list every allow-listed tool right after ``initialize``. From
6.7.14 the server lists toolsets first (``shopware-toolsets-list`` /
``shopware-toolset-enable``); :meth:`McpClient.ensure_tool` enables the toolset that
carries a tool the caller needs, so the same client works on both lanes.

Request bytes are serialised here (compact JSON) and handed to an optional
``request_hook`` before sending so a caller can sign exactly the bytes that go on the
wire (RFC 9421 for UCP).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_INVALID_PARAMS = -32602
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_SESSION_LOST_STATUS = {400, 404}
_SESSION_LOST_MARKERS = ("session id is required", "session not found", "has expired")
TOOLSETS_LIST_TOOL = "shopware-toolsets-list"
TOOLSET_ENABLE_TOOL = "shopware-toolset-enable"

RequestHook = Callable[[str, str, dict[str, str], bytes], dict[str, str]]
HeadersProvider = Callable[[], Awaitable[dict[str, str]]]


class McpError(RuntimeError):
    """A JSON-RPC error or a transport failure talking to an MCP server."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpSessionLost(McpError):
    """The server no longer knows our ``Mcp-Session-Id``."""


class McpToolError(McpError):
    """``tools/call`` returned ``isError: true``. ``result`` carries the tool payload."""

    def __init__(self, message: str, result: ToolResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class ToolResult:
    name: str
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    is_error: bool
    raw: dict[str, Any] = field(repr=False)

    def text(self) -> str:
        return "\n".join(
            str(block.get("text", ""))
            for block in self.content
            if isinstance(block, dict) and block.get("type", "text") == "text"
        )

    def json(self) -> Any:
        """``structuredContent`` when present, else the first JSON-parseable text block."""
        if isinstance(self.structured_content, dict):
            return self.structured_content
        for block in self.content:
            if not isinstance(block, dict) or block.get("type", "text") != "text":
                continue
            try:
                return json.loads(str(block.get("text", "")))
            except (TypeError, ValueError):
                continue
        return None


def encode_body(body: dict[str, Any]) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_rpc_body(response: httpx.Response, *, request_id: Any = None) -> dict[str, Any] | None:
    """One JSON-RPC message from a JSON or single/multi-event SSE body.

    With ``request_id`` the matching response is returned; without, the last message.
    ``None`` for an empty body (``202`` on notifications).
    """
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        try:
            payload = response.json()
        except ValueError as error:
            raise McpError(
                f"MCP server returned non-JSON ({response.status_code}): {response.text[:200]}"
            ) from error
        return payload if isinstance(payload, dict) else {"result": payload}
    chosen: dict[str, Any] | None = None
    for chunk in response.text.split("\n\n"):
        data_lines = [line[5:].strip() for line in chunk.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        try:
            message = json.loads("\n".join(data_lines))
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if request_id is not None and message.get("id") == request_id:
            return message
        chosen = message
    return chosen


class McpClient:
    def __init__(
        self,
        url: str,
        *,
        http: httpx.AsyncClient | None = None,
        client_name: str = "commerce-agents",
        client_version: str = "0.1.0",
        protocol_version: str = PROTOCOL_VERSION,
        headers: dict[str, str] | None = None,
        headers_provider: HeadersProvider | None = None,
        request_hook: RequestHook | None = None,
        retry_backoff: float = 0.5,
        timeout: float = 30.0,
    ) -> None:
        self.url = url
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._owns_http = http is None
        self._client_info = {"name": client_name, "version": client_version}
        self._requested_protocol = protocol_version
        self._static_headers = dict(headers or {})
        self._headers_provider = headers_provider
        self._request_hook = request_hook
        self._retry_backoff = retry_backoff
        self._next_id = 0
        self._lock = asyncio.Lock()  # guards the handshake in ensure_session
        self._io_lock = asyncio.Lock()  # one in-flight request per session (see request)
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        self._tools: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def initialized(self) -> bool:
        return self.session_id is not None

    async def initialize(self) -> dict[str, Any]:
        """Run the handshake. Safe to call again: the previous session is dropped."""
        self.session_id = None
        self.protocol_version = None
        self._tools = None
        params = {
            "protocolVersion": self._requested_protocol,
            "capabilities": {},
            "clientInfo": self._client_info,
        }
        response, request_id = await self._post("initialize", params)
        message = parse_rpc_body(response, request_id=request_id)
        result = self._unwrap(message, "initialize")
        session_id = response.headers.get(SESSION_HEADER)
        if not session_id:
            raise McpError("MCP initialize response carried no Mcp-Session-Id header")
        self.session_id = session_id
        self.protocol_version = str(result.get("protocolVersion") or self._requested_protocol)
        self.server_info = dict(result.get("serverInfo") or {})
        self.server_capabilities = dict(result.get("capabilities") or {})
        await self._notify("notifications/initialized")
        logger.debug(
            "MCP session %s on %s (protocol %s, server %s)",
            session_id,
            self.url,
            self.protocol_version,
            self.server_info.get("name"),
        )
        return result

    async def ensure_session(self) -> None:
        if self.session_id is None:
            async with self._lock:
                if self.session_id is None:
                    await self.initialize()

    async def close(self) -> None:
        """``DELETE`` the session (best effort) and release the HTTP client we own."""
        if self.session_id is not None:
            try:
                headers = await self._headers(write=False)
                await self._http.delete(self.url, headers=headers)
            except httpx.HTTPError as error:  # pragma: no cover - network noise
                logger.debug("MCP session DELETE failed: %s", error)
            finally:
                self.session_id = None
                self._tools = None
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> McpClient:
        await self.ensure_session()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------ tools

    async def list_tools(self, *, force: bool = False) -> list[dict[str, Any]]:
        if self._tools is not None and not force:
            return self._tools
        await self.ensure_session()
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self.request("tools/list", params)
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = tools
        return tools

    async def tool_names(self, *, force: bool = False) -> set[str]:
        return {str(tool.get("name")) for tool in await self.list_tools(force=force)}

    async def ensure_tool(self, name: str) -> bool:
        """Make ``name`` callable. On 6.7.14+ (progressive discovery) this enables the
        toolset that carries it; on 6.7.11–13 it only checks the allowlist."""
        names = await self.tool_names()
        if name in names:
            return True
        if TOOLSETS_LIST_TOOL not in names:
            return False
        toolsets = await self.call_tool(TOOLSETS_LIST_TOOL, {})
        for toolset in _toolsets(toolsets.json()):
            if name in toolset.get("tools", []):
                schema = next(
                    (t for t in await self.list_tools() if t.get("name") == TOOLSET_ENABLE_TOOL),
                    {},
                )
                argument = _first_string_argument(schema) or "toolset"
                await self.call_tool(TOOLSET_ENABLE_TOOL, {argument: toolset["name"]})
                names = await self.tool_names(force=True)
                return name in names
        return False

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
        raise_on_tool_error: bool = True,
    ) -> ToolResult:
        result = await self.request(
            "tools/call", {"name": name, "arguments": arguments}, extra_headers=extra_headers
        )
        tool_result = ToolResult(
            name=name,
            content=[b for b in result.get("content") or [] if isinstance(b, dict)],
            structured_content=(
                result.get("structuredContent")
                if isinstance(result.get("structuredContent"), dict)
                else None
            ),
            is_error=bool(result.get("isError")),
            raw=result,
        )
        if tool_result.is_error and raise_on_tool_error:
            raise McpToolError(f"{name}: {tool_result.text() or 'tool error'}", tool_result)
        return tool_result

    # ------------------------------------------------------------------ resources

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        """``resources/read``: the ``contents`` blocks (``{"uri", "mimeType", "text"|"blob"}``).
        Shopware parks a tool result that is too large for inline delivery behind a
        ``shopware://tool-result/<id>`` resource and answers the call with
        ``_meta.resourceUri``; this is how the caller collects it."""
        result = await self.request("resources/read", {"uri": uri})
        return [c for c in result.get("contents") or [] if isinstance(c, dict)]

    # ------------------------------------------------------------------ JSON-RPC

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """A JSON-RPC request with one transparent re-initialise on a lost session.

        Requests on one session run one at a time: Shopware's streamable-HTTP server
        (``mcp/sdk``) races when a session answers two ``tools/call`` at once and one of
        them comes back with an empty body (observed live on 6.7.13 with three concurrent
        calls). Serialising here keeps the portal's parallel reads and the agent's tool
        calls on one session correct without a second session per caller.
        """
        await self.ensure_session()
        async with self._io_lock:
            try:
                response, request_id = await self._post(method, params, extra_headers=extra_headers)
            except McpSessionLost:
                logger.info("MCP session on %s expired; re-initialising once", self.url)
                await self.initialize()
                response, request_id = await self._post(method, params, extra_headers=extra_headers)
        message = parse_rpc_body(response, request_id=request_id)
        return self._unwrap(message, method)

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        response = await self._send(body, extra_headers=None)
        if response.status_code >= 400:
            raise self._transport_error(response, method)

    async def _post(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, int]:
        """The HTTP response and the JSON-RPC id it answers. The id travels with the call
        (not on ``self``): requests run concurrently on one session — the portal's
        dashboard, overview and ledger reads land together — and each must match its own
        answer, not the id of whichever request was issued last."""
        self._next_id += 1
        request_id = self._next_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        response = await self._send(body, extra_headers=extra_headers)
        if response.status_code >= 400:
            raise self._transport_error(response, method)
        return response, request_id

    async def _send(
        self, body: dict[str, Any], *, extra_headers: dict[str, str] | None
    ) -> httpx.Response:
        payload = encode_body(body)
        headers = await self._headers(write=True)
        if extra_headers:
            headers.update(extra_headers)
        if self._request_hook is not None:
            headers.update(self._request_hook("POST", self.url, headers, payload))
        response = await self._http.post(self.url, content=payload, headers=headers)
        if response.status_code in _TRANSIENT_STATUS:
            await asyncio.sleep(self._retry_backoff)
            if self._request_hook is not None:
                # A fresh signature: ``created`` / nonce must not be replayed.
                headers.update(self._request_hook("POST", self.url, headers, payload))
            response = await self._http.post(self.url, content=payload, headers=headers)
        return response

    async def _headers(self, *, write: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            **self._static_headers,
        }
        if write:
            headers["Content-Type"] = "application/json"
        if self._headers_provider is not None:
            headers.update(await self._headers_provider())
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        headers[PROTOCOL_HEADER] = self.protocol_version or self._requested_protocol
        return headers

    def _transport_error(self, response: httpx.Response, method: str) -> McpError:
        message = parse_rpc_body(response) if response.content else None
        error = (message or {}).get("error") if isinstance(message, dict) else None
        text = str((error or {}).get("message") or response.text[:300] or response.status_code)
        code = (error or {}).get("code") if isinstance(error, dict) else None
        lowered = text.lower()
        if response.status_code in _SESSION_LOST_STATUS and any(
            marker in lowered for marker in _SESSION_LOST_MARKERS
        ):
            return McpSessionLost(f"{method}: {text}", code=code)
        if response.status_code == 401:
            return McpError(f"{method}: unauthorised (401): {text}", code=code)
        return McpError(f"{method}: HTTP {response.status_code}: {text}", code=code, data=error)

    @staticmethod
    def _unwrap(message: dict[str, Any] | None, method: str) -> dict[str, Any]:
        if message is None:
            raise McpError(f"{method}: empty response from MCP server")
        if "error" in message:
            error = message["error"] or {}
            raise McpError(
                f"{method}: {error.get('message', 'JSON-RPC error')}",
                code=error.get("code"),
                data=error.get("data"),
            )
        result = message.get("result")
        if isinstance(result, dict):
            return result
        return {"value": result}


def _toolsets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("toolsets", "data", "result"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
            if isinstance(payload.get(key), dict):
                return _toolsets(payload[key])
    if not isinstance(payload, list):
        return []
    toolsets = []
    for entry in payload:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        tools = entry.get("tools") or []
        names = [t.get("name") if isinstance(t, dict) else str(t) for t in tools]
        toolsets.append({"name": str(entry["name"]), "tools": [n for n in names if n]})
    return toolsets


def _first_string_argument(tool: dict[str, Any]) -> str | None:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if (properties.get(name) or {}).get("type") == "string":
            return str(name)
    for name, spec in properties.items():
        if (spec or {}).get("type") == "string":
            return str(name)
    return None


def new_idempotency_key() -> str:
    return str(uuid.uuid4())
