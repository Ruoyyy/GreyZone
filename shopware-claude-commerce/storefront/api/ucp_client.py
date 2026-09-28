# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""UCP client for a Shopware sales channel — one document model, two transports.

* **MCP** (primary, ADR-12) ``POST /ucp/mcp`` — Streamable HTTP through
  :class:`shopware_common.mcp_client.McpClient` (``initialize`` → ``Mcp-Session-Id`` →
  ``tools/call``). Tool names and argument shapes are the ones the live ``tools/list``
  returns (fixture: ``tests/fixtures/ucp_mcp_tools_list.json``): ``payload`` travels as an
  object and ``ids`` as a JSON string; mutating tools default to ``dryRun=true`` on the
  server, so this client always sends ``dryRun=false`` for real cart writes.
* **REST** (fallback / ``UCP_TRANSPORT=rest``) ``/ucp/v1/*`` — the same documents on
  the SDK's REST routes.

Every request carries ``UCP-Agent: platform; profile="…"``; every POST/PATCH carries a
fresh ``Idempotency-Key`` (the channel runs ``idempotencyRequired=true``). When
``UCP_AGENT_SIGNING_KEY_PEM_FILE`` names a P-256 key, every request is signed per RFC 9421
+ RFC 9530 (``Content-Digest``, ``Signature-Input``, ``Signature``) over exactly the bytes
that go on the wire, so the channel can run ``signaturePolicy=strict``.

A transport that is *unavailable* (endpoint missing, 5xx after one retry, connection
error, broken MCP handshake) falls back to the other one for that call. A transport that
*answers* — a UCP error document, a tool ``isError`` — never falls back: the answer is
the answer, and it surfaces as :class:`UcpError` / :class:`UcpCartGoneError` /
:class:`UcpAuthError`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from shopware_common.http_signing import RequestSigner, signer_from_env
from shopware_common.mcp_client import McpClient, McpError, McpToolError, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_SHOP_URL = "http://localhost:8080"
DEFAULT_TRANSPORT = "mcp"
TRANSPORTS = ("mcp", "rest")
DEFAULT_SEARCH_LIMIT = 8
_TIMEOUT = httpx.Timeout(25.0)
_TRANSIENT = {429, 500, 502, 503, 504}
_ENDPOINT_MISSING = {404, 405, 501}
_CART_GONE = {"cart_not_found", "invalid_cart_id"}
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_METHOD_NOT_FOUND = -32601

Transport = Literal["mcp", "rest"]

# UCP MCP tool names behind /ucp/mcp. Since plugin 1.3.0 these are the names the
# specification's OpenRPC document gives them, so the map is identity apart from the
# get_product alias; it stays a map because it is also the allowlist.
MCP_TOOLS = {
    "search_catalog": "search_catalog",
    "lookup_catalog": "lookup_catalog",
    "get_product": "lookup_catalog",
    "create_cart": "create_cart",
    "update_cart": "update_cart",
    "get_cart": "get_cart",
    "cancel_cart": "cancel_cart",
    "apply_discount": "apply_discount",
    "create_checkout": "create_checkout",
    "update_checkout": "update_checkout",
    "get_checkout": "get_checkout",
    "complete_checkout": "complete_checkout",
    "cancel_checkout": "cancel_checkout",
    "get_order": "get_order",
}

MUTATING_OPERATIONS = frozenset(
    {
        "create_cart",
        "update_cart",
        "cancel_cart",
        "apply_discount",
        "create_checkout",
        "update_checkout",
        "complete_checkout",
        "cancel_checkout",
    }
)


def shop_url_from_env() -> str:
    return os.environ.get("SHOPWARE_URL", DEFAULT_SHOP_URL).rstrip("/")


def profile_url_from_env(shop_url: str | None = None) -> str:
    explicit = os.environ.get("UCP_AGENT_PROFILE_URL")
    if explicit:
        return explicit
    # Shopware fetches this URL from inside Docker. The published host port
    # (localhost:8080) is unreachable from the container; Apache listens on :80.
    # Bootstrap / compose publish agent-profile.json at that path.
    return "http://localhost/agent-profile.json"


def transport_from_env() -> Transport:
    value = os.environ.get("UCP_TRANSPORT", DEFAULT_TRANSPORT).strip().lower()
    if value not in TRANSPORTS:
        logger.warning(
            "UCP_TRANSPORT=%r is not one of %s; using %s", value, TRANSPORTS, DEFAULT_TRANSPORT
        )
        return DEFAULT_TRANSPORT
    return value  # type: ignore[return-value]


def origin_host(url: str) -> str:
    return urlparse(url).hostname or "localhost"


class UcpError(RuntimeError):
    """The shop answered with a UCP error document (or a tool error)."""

    def __init__(self, message: str, codes: frozenset[str] = frozenset()) -> None:
        super().__init__(message)
        self.codes = codes


class UcpAuthError(UcpError):
    """Buyer token rejected (401)."""


class UcpCartGoneError(UcpError):
    """The shop no longer accepts this cart id."""


class UcpTransportUnavailable(UcpError):
    """The transport itself failed (no endpoint, 5xx, connection error, broken handshake)."""


class UcpClient:
    def __init__(
        self,
        shop_url: str | None = None,
        profile_url: str | None = None,
        http: httpx.AsyncClient | None = None,
        retry_backoff: float = 0.5,
        transport: Transport | None = None,
        signer: RequestSigner | None | Literal["env"] = "env",
        fallback: bool = True,
    ) -> None:
        self.shop_url = (shop_url or shop_url_from_env()).rstrip("/")
        self.discovery_url = f"{self.shop_url}/.well-known/ucp"
        self.mcp_url = f"{self.shop_url}/ucp/mcp"
        self.rest_prefix = f"{self.shop_url}/ucp/v1"
        self.profile_url = profile_url or profile_url_from_env(self.shop_url)
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        self._owns_http = http is None
        self._retry_backoff = retry_backoff
        self.transport: Transport = transport or transport_from_env()
        self.fallback = fallback
        self.signer: RequestSigner | None = signer_from_env() if signer == "env" else signer
        self.mcp = McpClient(
            self.mcp_url,
            http=self._http,
            client_name="commerce-agents-storefront",
            headers={"UCP-Agent": self.agent_header},
            headers_provider=self._per_request_headers,
            request_hook=self._sign,
            retry_backoff=retry_backoff,
        )
        self._fallback_logged: set[Transport] = set()

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        await self.mcp.close()
        if self._owns_http:
            await self._http.aclose()

    @property
    def agent_header(self) -> str:
        return f'platform; profile="{self.profile_url}"'

    @property
    def signs_requests(self) -> bool:
        return self.signer is not None

    async def _per_request_headers(self) -> dict[str, str]:
        return {"Idempotency-Key": str(uuid.uuid4())}

    def _sign(self, method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, str]:
        if self.signer is None:
            return {}
        return self.signer.headers_for(method, url, body)

    # ------------------------------------------------------------------ discovery

    async def discover(self) -> dict[str, Any]:
        response = await self._http.get(self.discovery_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    async def tool_names(self) -> set[str]:
        """Live ``tools/list`` on ``/ucp/mcp`` (for smoke and the mapping doc)."""
        return await self.mcp.tool_names()

    # ------------------------------------------------------------------ operations

    async def call_ucp(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        bearer_token: str | None = None,
        document_error_ok: bool = False,
    ) -> dict[str, Any]:
        """One UCP operation (catalog / cart / checkout / order) on the configured
        transport, falling back to the other transport only when this one is unavailable."""
        if name not in MCP_TOOLS:
            raise UcpError(f"Unknown UCP operation {name!r}")
        primary = self.transport
        try:
            return await self._call(primary, name, arguments, bearer_token, document_error_ok)
        except UcpTransportUnavailable as error:
            if not self.fallback:
                raise
            secondary: Transport = "rest" if primary == "mcp" else "mcp"
            if primary not in self._fallback_logged:
                self._fallback_logged.add(primary)
                logger.warning(
                    "UCP %s transport unavailable (%s); falling back to %s",
                    primary,
                    error,
                    secondary,
                )
            return await self._call(secondary, name, arguments, bearer_token, document_error_ok)

    async def _call(
        self,
        transport: Transport,
        name: str,
        arguments: dict[str, Any],
        bearer_token: str | None,
        document_error_ok: bool,
    ) -> dict[str, Any]:
        if transport == "mcp":
            return await self._call_mcp(name, arguments, bearer_token, document_error_ok)
        return await self._call_rest(name, arguments, bearer_token, document_error_ok)

    # ------------------------------------------------------------------ MCP

    async def _call_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        bearer_token: str | None,
        document_error_ok: bool,
    ) -> dict[str, Any]:
        tool = MCP_TOOLS[name]
        tool_arguments = mcp_arguments(name, arguments)
        extra = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
        try:
            result = await self.mcp.call_tool(
                tool, tool_arguments, extra_headers=extra, raise_on_tool_error=False
            )
        except McpToolError as error:  # pragma: no cover - raise_on_tool_error=False
            result = error.result
        except McpError as error:
            raise _classify_mcp_error(name, error) from error
        except httpx.HTTPError as error:
            raise UcpTransportUnavailable(f"{name}: MCP transport error: {error}") from error
        return _document_from_tool(name, result, document_error_ok=document_error_ok)

    # ------------------------------------------------------------------ REST

    async def _call_rest(
        self,
        name: str,
        arguments: dict[str, Any],
        bearer_token: str | None,
        document_error_ok: bool,
    ) -> dict[str, Any]:
        method, path, body = rest_request(name, arguments)
        url = f"{self.rest_prefix}{path}"
        payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "UCP-Agent": self.agent_header,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Idempotency-Key"] = str(uuid.uuid4())
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        try:
            response = await self._rest_send(method, url, headers, payload)
        except httpx.HTTPError as error:
            raise UcpTransportUnavailable(f"{name}: REST transport error: {error}") from error
        if response.status_code == 401:
            raise UcpAuthError(f"{name}: UCP rejected the token (401)")
        if response.status_code in _ENDPOINT_MISSING and not _has_ucp_messages(response):
            raise UcpTransportUnavailable(f"{name}: REST {method} {path} → {response.status_code}")
        if response.status_code in _TRANSIENT:
            raise UcpTransportUnavailable(f"{name}: REST {method} {path} → {response.status_code}")
        if response.status_code >= 400:
            raise _http_error(name, response)
        return _maybe_document_error(
            name, _json_payload(response), document_error_ok=document_error_ok
        )

    async def _rest_send(
        self, method: str, url: str, headers: dict[str, str], payload: bytes
    ) -> httpx.Response:
        signed = {**headers, **self._sign(method, url, headers, payload)}
        response = await self._http.request(method, url, headers=signed, content=payload or None)
        if response.status_code in _TRANSIENT:
            await asyncio.sleep(self._retry_backoff)
            # A fresh signature: ``created`` must not be replayed.
            signed = {**headers, **self._sign(method, url, headers, payload)}
            response = await self._http.request(
                method, url, headers=signed, content=payload or None
            )
        return response


# ---------------------------------------------------------------------- argument mapping


def _catalog(arguments: dict[str, Any]) -> dict[str, Any]:
    return arguments.get("catalog") or {}


def _search_terms(arguments: dict[str, Any]) -> tuple[str, int]:
    catalog = _catalog(arguments)
    query = str(catalog.get("query") or arguments.get("query") or "")
    limit = (
        (catalog.get("pagination") or {}).get("limit")
        or arguments.get("limit")
        or DEFAULT_SEARCH_LIMIT
    )
    return query, int(limit)


def _lookup_ids(arguments: dict[str, Any]) -> list[str]:
    catalog = _catalog(arguments)
    ids = catalog.get("ids") or arguments.get("ids")
    product_id = catalog.get("id") or arguments.get("id")
    return list(ids or ([product_id] if product_id else []))


def _document_body(arguments: dict[str, Any], key: str) -> dict[str, Any]:
    document = arguments.get(key) or {k: v for k, v in arguments.items() if k != "id"}
    return {k: v for k, v in document.items() if k != "id"}


def rest_request(name: str, arguments: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    """``(method, path, body)`` on ``/ucp/v1`` for a UCP operation."""
    if name == "search_catalog":
        query, limit = _search_terms(arguments)
        body: dict[str, Any] = {"query": query, "limit": limit}
        filters = _catalog(arguments).get("filters") or arguments.get("filters")
        if filters:
            body["filters"] = filters
        return "POST", "/catalog/search", body
    if name == "get_product":
        ids = _lookup_ids(arguments)
        if len(ids) == 1:
            return "GET", f"/catalog/product/{ids[0]}", None
        return "POST", "/catalog/lookup", {"ids": ids}
    if name == "lookup_catalog":
        return "POST", "/catalog/lookup", {"ids": _lookup_ids(arguments)}
    if name == "create_cart":
        return "POST", "/carts", _document_body(arguments, "cart")
    if name == "get_cart":
        return "GET", f"/carts/{arguments['id']}", None
    if name == "update_cart":
        cart_id = arguments["id"]
        return "PATCH", f"/carts/{cart_id}", {"id": cart_id, **_document_body(arguments, "cart")}
    if name == "cancel_cart":
        return "POST", f"/carts/{arguments['id']}/cancel", {}
    if name == "apply_discount":
        # The SDK's REST routes have no discount endpoint; the MCP tool is the only surface.
        raise UcpError("apply_discount is only available over MCP (apply_discount)")
    if name == "create_checkout":
        return "POST", "/checkout-sessions", _document_body(arguments, "checkout")
    if name == "get_checkout":
        return "GET", f"/checkout-sessions/{arguments['id']}", None
    if name == "update_checkout":
        checkout_id = arguments["id"]
        return (
            "PATCH",
            f"/checkout-sessions/{checkout_id}",
            {"id": checkout_id, **_document_body(arguments, "checkout")},
        )
    if name == "complete_checkout":
        return (
            "POST",
            f"/checkout-sessions/{arguments['id']}/complete",
            _document_body(arguments, "checkout"),
        )
    if name == "cancel_checkout":
        return "POST", f"/checkout-sessions/{arguments['id']}/cancel", {}
    if name == "get_order":
        return "GET", f"/orders/{arguments['id']}", None
    raise UcpError(f"Unknown UCP operation {name!r}")


def mcp_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments per the live ``tools/list`` schemas: ``payload`` is an object
    (plugin 1.3.0 types it ``array $payload``), ``ids`` is still a JSON string, and
    ``dryRun=false`` on every mutating tool."""
    if name == "search_catalog":
        query, limit = _search_terms(arguments)
        # The MCP search tool has no filter argument; callers filter client-side.
        return {"query": query, "limit": limit}
    if name in {"lookup_catalog", "get_product"}:
        return {"ids": json.dumps(_lookup_ids(arguments))}
    if name in {"get_cart", "get_checkout", "get_order"}:
        return {"id": arguments["id"]}
    if name == "create_cart":
        return {"payload": _document_body(arguments, "cart"), "dryRun": False}
    if name == "update_cart":
        return {
            "id": arguments["id"],
            "payload": _document_body(arguments, "cart"),
            "dryRun": False,
        }
    if name in {"cancel_cart", "cancel_checkout"}:
        return {"id": arguments["id"], "dryRun": False}
    if name == "apply_discount":
        return {"cartId": arguments["cart_id"], "code": arguments["code"], "dryRun": False}
    if name == "create_checkout":
        return {"payload": _document_body(arguments, "checkout"), "dryRun": False}
    if name in {"update_checkout", "complete_checkout"}:
        return {
            "id": arguments["id"],
            "payload": _document_body(arguments, "checkout"),
            "dryRun": False,
        }
    raise UcpError(f"Unknown UCP operation {name!r}")


# ---------------------------------------------------------------------- response mapping


def _has_ucp_messages(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and bool(payload.get("messages"))


def _json_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise UcpError(f"Non-JSON UCP response ({response.status_code})") from error
    return _as_document(data)


def _as_document(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"results": data}
    return {"text": str(data)}


def _error_from_document(name: str, payload: dict[str, Any], fallback: str) -> UcpError:
    messages = payload.get("messages") or []
    contents = [str(m.get("content", "")) for m in messages if isinstance(m, dict)]
    text = "; ".join(c for c in contents if c) or str(payload.get("detail") or "") or fallback
    codes = frozenset(str(m.get("code")) for m in messages if isinstance(m, dict) and m.get("code"))
    cls = UcpCartGoneError if codes & _CART_GONE or "cart_not_found" in text else UcpError
    return cls(f"{name}: {text}", codes)


def _http_error(name: str, response: httpx.Response) -> UcpError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return _error_from_document(name, payload, response.text[:400] or str(response.status_code))


def _is_error_document(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages") or []
    return bool(payload.get("isError")) or any(
        isinstance(m, dict) and m.get("type") == "error" for m in messages
    )


def _maybe_document_error(
    name: str, payload: dict[str, Any], *, document_error_ok: bool
) -> dict[str, Any]:
    if _is_error_document(payload) and not (document_error_ok and payload.get("id")):
        raise _error_from_document(name, payload, "UCP error document")
    return payload


def _unwrap_envelope(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Shopware's UCP tools answer ``{"success": bool, "dryRun"?: bool, "data": <document>}``
    (live ``tools/call``, 6.7.13); the UCP document is ``data``."""
    if "success" not in payload:
        return payload
    if not payload.get("success"):
        detail = (
            payload.get("error")
            or payload.get("message")
            or payload.get("data")
            or "tool reported failure"
        )
        raise UcpError(f"{name}: {detail}")
    data = payload.get("data")
    return (
        _as_document(data)
        if data is not None
        else {k: v for k, v in payload.items() if k != "success"}
    )


def _document_from_tool(
    name: str, result: ToolResult, *, document_error_ok: bool
) -> dict[str, Any]:
    parsed = result.json()
    payload = _as_document(parsed) if parsed is not None else None
    if payload is not None and not result.is_error:
        payload = _unwrap_envelope(name, payload)
    if result.is_error:
        if document_error_ok and payload and payload.get("id"):
            return payload
        text = result.text()
        if payload is not None:
            raise _error_from_document(name, payload, text or "tool error")
        lowered = text.lower()
        if "cart" in lowered and ("not found" in lowered or "gone" in lowered):
            raise UcpCartGoneError(f"{name}: {text}", frozenset({"cart_not_found"}))
        raise UcpError(f"{name}: {text or 'tool error'}")
    if payload is None:
        return {"text": result.text()}
    return _maybe_document_error(name, payload, document_error_ok=document_error_ok)


def _classify_mcp_error(name: str, error: McpError) -> UcpError:
    text = str(error)
    if "(401)" in text:
        return UcpAuthError(f"{name}: UCP rejected the token (401)")
    if error.code in {_JSONRPC_INVALID_PARAMS}:
        return UcpError(f"{name}: {text}")
    if error.code == _JSONRPC_METHOD_NOT_FOUND:
        return UcpTransportUnavailable(f"{name}: {text}")
    return UcpTransportUnavailable(f"{name}: {text}")
