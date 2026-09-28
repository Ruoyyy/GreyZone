# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shopware Admin access for the merchant backend: one operation-oriented protocol,
two transports.

``McpTransport`` (default) speaks to the Admin MCP server at ``/api/_mcp`` through the
shared :class:`shopware_common.mcp_client.McpClient` and maps each operation onto one
``shopware-entity-*`` tool. Criteria, aggregations, payloads and id lists travel as JSON
*strings* (the tools' input schema). ``upsert(dry_run=True)`` is a real server-side
preview: Shopware runs the write in a transaction and rolls it back, so type and
required-field errors come back before anything is persisted.

``RestTransport`` (``SHOPWARE_ADMIN_TRANSPORT=rest``) covers shops without the MCP
server. Admin REST has no dry run, so a preview there is ``server_validated=False`` and
the staged change says so.

Every call — reads included — is appended to ``calls`` so tests can prove that staging
never performs a ``dry_run=False`` write.

``order_state`` drives an order's state machine through ``shopware-order-state`` (refunds,
``merchant/api/refunds.py``). The MCP tool previews a transition with ``dryRun=true``; REST
has no such preview, so the REST transport refuses it rather than write unpreviewed.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from shopware_common.mcp_client import McpClient, McpError

logger = logging.getLogger(__name__)

EUR_CURRENCY_ID = "b7d2554b0ce847cd82f3ac9bd1c0dfca"
CALL_LOG_LIMIT = 500
_TIMEOUT = httpx.Timeout(30.0)
_TOKEN_SAFETY_MARGIN_S = 30
_DEFAULT_TOKEN_TTL_S = 600
_NOT_FOUND_MARKER = "not found"

TOOL_SEARCH = "shopware-entity-search"
TOOL_READ = "shopware-entity-read"
TOOL_AGGREGATE = "shopware-entity-aggregate"
TOOL_UPSERT = "shopware-entity-upsert"
TOOL_DELETE = "shopware-entity-delete"
TOOL_SCHEMA = "shopware-entity-schema"
TOOL_ORDER_STATE = "shopware-order-state"
#: Every MCP tool the merchant backend may call; the integration allowlist mirrors it.
MCP_TOOLS_USED: tuple[str, ...] = (
    TOOL_SEARCH,
    TOOL_READ,
    TOOL_AGGREGATE,
    TOOL_UPSERT,
    TOOL_DELETE,
    TOOL_SCHEMA,
    TOOL_ORDER_STATE,
)

TransportName = Literal["mcp", "rest", "fake"]
Operation = Literal["search", "read", "aggregate", "upsert", "delete", "tool", "state"]


class AdminAPIError(RuntimeError):
    """A failed Admin call, with the transport context the log line needs."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        tool: str | None = None,
        entity: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.tool = tool
        self.entity = entity


@dataclass(frozen=True)
class SearchResult:
    rows: list[dict[str, Any]]
    total: int


@dataclass(frozen=True)
class WriteResult:
    """``written`` mirrors the MCP tool's ``data``: one ``{"entity", "ids", "operation"}``
    per entity the write touched (translations and nested associations included)."""

    success: bool
    written: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True
    server_validated: bool = True
    error: str | None = None

    def entities(self) -> list[str]:
        return [str(row.get("entity")) for row in self.written if row.get("entity")]

    def describe(self) -> str:
        """``product, product_translation (1 row each)`` — the preview note's tail."""
        if not self.written:
            return "nothing"
        parts = [str(row.get("entity")) for row in self.written]
        counts = {len(row.get("ids") or []) for row in self.written}
        if len(counts) == 1:
            count = counts.pop()
            suffix = f" ({count} row{'s' if count != 1 else ''} each)"
        else:
            suffix = " (" + ", ".join(f"{len(r.get('ids') or [])}" for r in self.written) + " rows)"
        return ", ".join(parts) + suffix


@dataclass(frozen=True)
class AdminCall:
    operation: str
    entity: str
    payload: Any = None
    dry_run: bool | None = None


class AdminTransport(Protocol):
    """What the backend needs from Shopware Admin. Implementations dispatch on the
    operation (search/read/aggregate/upsert/delete), which lets the fake mirror the live
    tools' semantics without knowing about HTTP."""

    calls: deque[AdminCall]

    @property
    def name(self) -> TransportName: ...

    async def search(
        self,
        entity: str,
        criteria: dict[str, Any],
        *,
        limit: int = 25,
        page: int = 1,
        term: str = "",
    ) -> SearchResult: ...

    async def read(
        self, entity: str, entity_id: str, criteria: dict[str, Any] | None = None
    ) -> dict[str, Any] | None: ...

    async def aggregate(
        self,
        entity: str,
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    async def upsert(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]], *, dry_run: bool
    ) -> WriteResult: ...

    async def delete(self, entity: str, ids: list[str], *, dry_run: bool) -> WriteResult: ...

    async def order_state(
        self, order_number: str, *, transaction_action: str, dry_run: bool
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


def new_call_log() -> deque[AdminCall]:
    return deque(maxlen=CALL_LOG_LIMIT)


def writes(calls: deque[AdminCall] | list[AdminCall]) -> list[AdminCall]:
    """The persisted writes in a call log — the list that must be empty after staging."""
    return [c for c in calls if c.operation in {"upsert", "delete", "state"} and c.dry_run is False]


# --------------------------------------------------------------------------- OAuth


class OAuthTokenProvider:
    """Bearer tokens from ``POST /api/oauth/token``. The merchant host runs on an
    integration (``client_credentials``); the password grant exists only so scripts can
    bootstrap an integration with the admin account."""

    def __init__(
        self,
        shop_url: str,
        *,
        http: httpx.AsyncClient,
        access_key: str = "",
        secret_key: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        if not ((access_key and secret_key) or (username and password)):
            raise AdminAPIError(
                "no Admin credentials: set SHOPWARE_INTEGRATION_ACCESS_KEY/SECRET_KEY"
            )
        self._shop_url = shop_url.rstrip("/")
        self._http = http
        self._access_key = access_key
        self._secret_key = secret_key
        self._username = username
        self._password = password
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def grant(self) -> str:
        return "client_credentials" if self._access_key and self._secret_key else "password"

    async def token(self) -> str:
        if self._token and time.time() < self._token_exp - _TOKEN_SAFETY_MARGIN_S:
            return self._token
        if self.grant == "client_credentials":
            body = {
                "grant_type": "client_credentials",
                "client_id": self._access_key,
                "client_secret": self._secret_key,
            }
        else:
            body = {
                "grant_type": "password",
                "client_id": "administration",
                "username": self._username,
                "password": self._password,
            }
        response = await self._http.post(f"{self._shop_url}/api/oauth/token", json=body)
        if response.status_code >= 400:
            logger.error("Admin OAuth token request failed: %s", response.status_code)
            raise AdminAPIError(
                f"oauth token failed: {response.status_code} {response.text[:300]}",
                status=response.status_code,
            )
        payload = response.json()
        self._token = str(payload["access_token"])
        self._token_exp = time.time() + int(payload.get("expires_in") or _DEFAULT_TOKEN_TTL_S)
        return self._token

    async def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}


HeadersProvider = Callable[[], Awaitable[dict[str, str]]]


# --------------------------------------------------------------------------- MCP


class McpTransport:
    """The Admin MCP server's ``shopware-entity-*`` tools behind :class:`AdminTransport`."""

    name: TransportName = "mcp"

    def __init__(
        self,
        shop_url: str,
        *,
        headers_provider: HeadersProvider,
        http: httpx.AsyncClient | None = None,
        client: McpClient | None = None,
    ) -> None:
        self.shop_url = shop_url.rstrip("/")
        self._owns_http = http is None and client is None
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        self.client = client or McpClient(
            f"{self.shop_url}/api/_mcp", http=self._http, headers_provider=headers_provider
        )
        self._ensured: set[str] = set()
        self.calls: deque[AdminCall] = new_call_log()

    async def aclose(self) -> None:
        await self.client.close()
        if self._owns_http:
            await self._http.aclose()

    async def tool_names(self) -> list[str]:
        return sorted(await self.client.tool_names())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool outside the ``shopware-entity-*`` set — the ``agent-*`` tools of
        ``SwagCommerceAgentTools`` (``merchant/api/agent_tools.py``). The parsed
        ``{"success", "data", "_meta"}`` payload; ``success: false`` is the caller's to read.
        Logged as operation ``tool`` so ``writes()`` keeps counting only entity writes."""
        self.calls.append(AdminCall("tool", name, arguments))
        return await self._call(name, arguments, entity=name)

    async def _call(self, tool: str, arguments: dict[str, Any], *, entity: str) -> dict[str, Any]:
        """One ``tools/call``; the parsed ``{"success", ...}`` payload. Transport and
        JSON-RPC failures become :class:`AdminAPIError`; ``success: false`` is returned to
        the caller, who decides whether that is an error (reads) or a result (writes)."""
        try:
            if tool not in self._ensured:
                if not await self.client.ensure_tool(tool):
                    raise AdminAPIError(
                        f"MCP tool {tool} is not available to this integration "
                        "(check the allowlist)",
                        tool=tool,
                        entity=entity,
                    )
                self._ensured.add(tool)
            result = await self.client.call_tool(tool, arguments, raise_on_tool_error=False)
        except McpError as error:
            logger.warning("MCP %s on %s failed: %s", tool, entity, error)
            raise AdminAPIError(
                f"{tool} ({entity}): {error}", status=error.code, tool=tool, entity=entity
            ) from error
        payload = result.json()
        if not isinstance(payload, dict):
            logger.warning("MCP %s on %s returned no JSON payload: %r", tool, entity, result.text())
            raise AdminAPIError(
                f"{tool} ({entity}): unreadable tool response", tool=tool, entity=entity
            )
        if result.is_error and "success" not in payload:
            payload = {"success": False, "error": result.text() or "tool error"}
        return await self._collect_offloaded(payload, tool=tool, entity=entity)

    async def _collect_offloaded(
        self, payload: dict[str, Any], *, tool: str, entity: str
    ) -> dict[str, Any]:
        """The full payload when the server parked it behind a resource.

        Shopware 6.7.13 answers a tool result above its inline size cap (observed at an
        order search with associations, ~160 KB) with ``data: null`` and
        ``_meta.resourceUri = "shopware://tool-result/<id>"`` plus a note to tighten the
        criteria. Treating that as "no rows" would be silently wrong, so the resource is
        read back and its JSON becomes the payload (``_meta`` is kept)."""
        meta = payload.get("_meta")
        uri = str(meta.get("resourceUri") or "") if isinstance(meta, dict) else ""
        if not uri or payload.get("data") is not None:
            return payload
        try:
            contents = await self.client.read_resource(uri)
        except McpError as error:
            logger.warning(
                "MCP %s on %s: offloaded result %s unreadable: %s", tool, entity, uri, error
            )
            raise AdminAPIError(
                f"{tool} ({entity}): offloaded result {uri} unreadable: {error}",
                status=error.code,
                tool=tool,
                entity=entity,
            ) from error
        text = next((str(c.get("text")) for c in contents if c.get("text") is not None), "")
        try:
            full = json.loads(text) if text else None
        except ValueError:
            full = None
        if not isinstance(full, dict):
            logger.warning("MCP %s on %s: offloaded result %s is not JSON", tool, entity, uri)
            raise AdminAPIError(
                f"{tool} ({entity}): offloaded result {uri} is not a JSON payload",
                tool=tool,
                entity=entity,
            )
        logger.info(
            "MCP %s on %s: collected %s bytes from %s", tool, entity, meta.get("responseSize"), uri
        )
        merged_meta = (
            {**meta, **(full.get("_meta") or {})} if isinstance(meta, dict) else full.get("_meta")
        )
        return {**full, "_meta": merged_meta} if merged_meta else full

    def _require_success(self, payload: dict[str, Any], *, tool: str, entity: str) -> Any:
        if not payload.get("success"):
            message = str(payload.get("error") or "unknown error")
            logger.warning("MCP %s on %s: %s", tool, entity, message)
            raise AdminAPIError(f"{tool} ({entity}): {message}", tool=tool, entity=entity)
        return payload.get("data")

    async def search(
        self,
        entity: str,
        criteria: dict[str, Any],
        *,
        limit: int = 25,
        page: int = 1,
        term: str = "",
    ) -> SearchResult:
        self.calls.append(AdminCall("search", entity, criteria))
        payload = await self._call(
            TOOL_SEARCH,
            {
                "entity": entity,
                "criteria": json.dumps(criteria),
                "limit": int(limit),
                "page": int(page),
                "term": term,
            },
            entity=entity,
        )
        data = self._require_success(payload, tool=TOOL_SEARCH, entity=entity)
        rows = [row for row in (data or []) if isinstance(row, dict)]
        meta = payload.get("_meta") or {}
        return SearchResult(rows=rows, total=int(meta.get("total") or len(rows)))

    async def read(
        self, entity: str, entity_id: str, criteria: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append(AdminCall("read", entity, entity_id))
        payload = await self._call(
            TOOL_READ,
            {"entity": entity, "id": entity_id, "criteria": json.dumps(criteria or {})},
            entity=entity,
        )
        if not payload.get("success"):
            error = str(payload.get("error") or "")
            if _NOT_FOUND_MARKER in error.lower():
                return None
            return self._require_success(payload, tool=TOOL_READ, entity=entity)
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def aggregate(
        self,
        entity: str,
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            AdminCall("aggregate", entity, {"aggregations": aggregations, "filters": filters or []})
        )
        payload = await self._call(
            TOOL_AGGREGATE,
            {
                "entity": entity,
                "aggregations": json.dumps(aggregations),
                "filters": json.dumps(filters or []),
            },
            entity=entity,
        )
        data = self._require_success(payload, tool=TOOL_AGGREGATE, entity=entity) or {}
        result = data.get("aggregations") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}

    async def upsert(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]], *, dry_run: bool
    ) -> WriteResult:
        self.calls.append(AdminCall("upsert", entity, payload, dry_run))
        response = await self._call(
            TOOL_UPSERT,
            {"entity": entity, "payload": json.dumps(payload), "dryRun": bool(dry_run)},
            entity=entity,
        )
        return self._write_result(response, dry_run=dry_run, tool=TOOL_UPSERT, entity=entity)

    async def delete(self, entity: str, ids: list[str], *, dry_run: bool) -> WriteResult:
        self.calls.append(AdminCall("delete", entity, list(ids), dry_run))
        response = await self._call(
            TOOL_DELETE,
            {"entity": entity, "ids": json.dumps(list(ids)), "dryRun": bool(dry_run)},
            entity=entity,
        )
        return self._write_result(response, dry_run=dry_run, tool=TOOL_DELETE, entity=entity)

    async def order_state(
        self, order_number: str, *, transaction_action: str, dry_run: bool
    ) -> dict[str, Any]:
        """One transition of the order's payment transactions. The tool's ``data``:
        ``{"orderId", "orderNumber", "transactions": [{"id", "from", "action",
        "actionValid", "availableTransitions"}]}``. Shopware answers an action the state
        machine does not allow with ``success: true`` and ``actionValid: false``, so the
        caller checks each transaction; ``success: false`` (e.g. "Order not found.") raises."""
        arguments = {
            "orderNumber": order_number,
            "transactionAction": transaction_action,
            "dryRun": bool(dry_run),
        }
        self.calls.append(AdminCall("state", "order_transaction", arguments, dry_run))
        payload = await self._call(TOOL_ORDER_STATE, arguments, entity="order_transaction")
        data = self._require_success(payload, tool=TOOL_ORDER_STATE, entity="order_transaction")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_result(
        response: dict[str, Any], *, dry_run: bool, tool: str, entity: str
    ) -> WriteResult:
        meta = response.get("_meta") or {}
        reported_dry_run = bool(meta.get("dryRun", dry_run))
        if not response.get("success"):
            error = str(response.get("error") or "write rejected")
            logger.warning("MCP %s on %s rejected (dryRun=%s): %s", tool, entity, dry_run, error)
            return WriteResult(success=False, dry_run=reported_dry_run, error=error)
        data = response.get("data")
        written = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        return WriteResult(success=True, written=written, dry_run=reported_dry_run)


# --------------------------------------------------------------------------- REST

_ATOMIC_ENTITIES = {"promotion", "rule"}


class RestTransport:
    """Admin REST (``/api/search``, ``/api/{entity}``, ``/api/_action/sync``). No dry run:
    ``upsert(dry_run=True)`` validates nothing server-side and says so."""

    name: TransportName = "rest"

    def __init__(
        self,
        shop_url: str,
        *,
        headers_provider: HeadersProvider,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.shop_url = shop_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        self._headers_provider = headers_provider
        self.calls: deque[AdminCall] = new_call_log()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _headers(self) -> dict[str, str]:
        return {
            **(await self._headers_provider()),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, *, entity: str, json_body: Any = None
    ) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{self.shop_url}{path}", headers=await self._headers(), json=json_body
            )
        except httpx.HTTPError as error:
            logger.warning("Admin REST %s %s failed: %s", method, path, error)
            raise AdminAPIError(f"{method} {path}: {error}", entity=entity) from error
        return response

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            errors = response.json().get("errors") or []
        except ValueError:
            return response.text[:300]
        parts = []
        for error in errors:
            detail = error.get("detail") or error.get("title") or ""
            pointer = (error.get("source") or {}).get("pointer")
            parts.append(f"[{pointer}] {detail}" if pointer else detail)
        return "; ".join(p for p in parts if p) or response.text[:300]

    def _raise_for_status(self, response: httpx.Response, *, entity: str, what: str) -> None:
        if response.status_code >= 400:
            text = self._error_text(response)
            logger.warning("Admin REST %s on %s: %s %s", what, entity, response.status_code, text)
            raise AdminAPIError(
                f"{what} ({entity}): {response.status_code} {text}",
                status=response.status_code,
                entity=entity,
            )

    async def search(
        self,
        entity: str,
        criteria: dict[str, Any],
        *,
        limit: int = 25,
        page: int = 1,
        term: str = "",
    ) -> SearchResult:
        self.calls.append(AdminCall("search", entity, criteria))
        body: dict[str, Any] = {**criteria, "limit": int(limit), "page": int(page)}
        if term:
            body["term"] = term
        body.setdefault("total-count-mode", 1)
        response = await self._request(
            "POST", f"/api/search/{_rest_name(entity)}", entity=entity, json_body=body
        )
        self._raise_for_status(response, entity=entity, what="search")
        payload = response.json()
        rows = [_flatten(row) for row in payload.get("data") or [] if isinstance(row, dict)]
        total = payload.get("total")
        if total is None:
            total = (payload.get("meta") or {}).get("total", len(rows))
        return SearchResult(rows=rows, total=int(total))

    async def read(
        self, entity: str, entity_id: str, criteria: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append(AdminCall("read", entity, entity_id))
        body: dict[str, Any] = {**(criteria or {}), "ids": [entity_id], "limit": 1}
        response = await self._request(
            "POST", f"/api/search/{_rest_name(entity)}", entity=entity, json_body=body
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, entity=entity, what="read")
        rows = response.json().get("data") or []
        return _flatten(rows[0]) if rows else None

    async def aggregate(
        self,
        entity: str,
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            AdminCall("aggregate", entity, {"aggregations": aggregations, "filters": filters or []})
        )
        body: dict[str, Any] = {"limit": 1, "aggregations": aggregations}
        if filters:
            body["filter"] = filters
        response = await self._request(
            "POST", f"/api/search/{_rest_name(entity)}", entity=entity, json_body=body
        )
        self._raise_for_status(response, entity=entity, what="aggregate")
        raw = response.json().get("aggregations") or {}
        if isinstance(raw, list):  # JSON:API shape when Accept was ignored
            raw = {entry.get("name"): entry for entry in raw if isinstance(entry, dict)}
        return {str(name): _strip_meta(value) for name, value in raw.items()}

    async def upsert(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]], *, dry_run: bool
    ) -> WriteResult:
        self.calls.append(AdminCall("upsert", entity, payload, dry_run))
        if dry_run:
            return WriteResult(success=True, written=[], dry_run=True, server_validated=False)
        if isinstance(payload, list) or entity in _ATOMIC_ENTITIES:
            rows = payload if isinstance(payload, list) else [payload]
            return await self._sync(entity, rows)
        entity_id = payload.get("id")
        if entity_id:
            body = {k: v for k, v in payload.items() if k != "id"}
            response = await self._request(
                "PATCH", f"/api/{_rest_name(entity)}/{entity_id}", entity=entity, json_body=body
            )
            if response.status_code != 404:
                if response.status_code >= 400:
                    return WriteResult(
                        success=False, dry_run=False, error=self._error_text(response)
                    )
                return WriteResult(
                    success=True,
                    written=[{"entity": entity, "ids": [entity_id], "operation": "upsert"}],
                    dry_run=False,
                )
        response = await self._request(
            "POST", f"/api/{_rest_name(entity)}", entity=entity, json_body=payload
        )
        if response.status_code >= 400:
            return WriteResult(success=False, dry_run=False, error=self._error_text(response))
        return WriteResult(
            success=True,
            written=[
                {"entity": entity, "ids": [entity_id] if entity_id else [], "operation": "upsert"}
            ],
            dry_run=False,
        )

    async def _sync(self, entity: str, rows: list[dict[str, Any]]) -> WriteResult:
        body = [{"action": "upsert", "entity": entity, "payload": rows}]
        response = await self._request("POST", "/api/_action/sync", entity=entity, json_body=body)
        if response.status_code >= 400:
            return WriteResult(success=False, dry_run=False, error=self._error_text(response))
        data = response.json().get("data") if response.content else None
        written: list[dict[str, Any]] = []
        if isinstance(data, dict):
            for name, ids in data.items():
                written.append({"entity": name, "ids": list(ids or []), "operation": "upsert"})
        if not written:
            written = [
                {
                    "entity": entity,
                    "ids": [r.get("id") for r in rows if r.get("id")],
                    "operation": "upsert",
                }
            ]
        return WriteResult(success=True, written=written, dry_run=False)

    async def delete(self, entity: str, ids: list[str], *, dry_run: bool) -> WriteResult:
        self.calls.append(AdminCall("delete", entity, list(ids), dry_run))
        if dry_run:
            return WriteResult(success=True, written=[], dry_run=True, server_validated=False)
        body = [{"action": "delete", "entity": entity, "payload": [{"id": i} for i in ids]}]
        response = await self._request("POST", "/api/_action/sync", entity=entity, json_body=body)
        if response.status_code >= 400:
            return WriteResult(success=False, dry_run=False, error=self._error_text(response))
        return WriteResult(
            success=True,
            written=[{"entity": entity, "ids": list(ids), "operation": "delete"}],
            dry_run=False,
        )

    async def order_state(
        self, order_number: str, *, transaction_action: str, dry_run: bool
    ) -> dict[str, Any]:
        self.calls.append(
            AdminCall(
                "state",
                "order_transaction",
                {"orderNumber": order_number, "transactionAction": transaction_action},
                dry_run,
            )
        )
        raise AdminAPIError(
            "order state transitions need the Admin MCP transport (shopware-order-state); "
            "Admin REST cannot preview them",
            entity="order_transaction",
        )


def _rest_name(entity: str) -> str:
    """DAL entity names are snake_case; Admin REST routes use kebab-case
    (``order_line_item`` → ``/api/search/order-line-item``). ``_action/sync`` keeps the
    DAL name."""
    return entity.replace("_", "-")


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """JSON:API ``{"id","attributes"}`` → flat row; flat rows pass through."""
    if "attributes" in row and isinstance(row["attributes"], dict):
        flat = {**row["attributes"], "id": row.get("id")}
        for key, value in (row.get("relationships") or {}).items():
            if isinstance(value, dict) and "data" in value:
                flat.setdefault(key, value["data"])
        return flat
    return row


def _strip_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_meta(v) for k, v in value.items() if k not in {"apiAlias", "extensions"}}
    if isinstance(value, list):
        return [_strip_meta(v) for v in value]
    return value


# --------------------------------------------------------------------------- factory


def build_transport(
    kind: str,
    shop_url: str,
    *,
    access_key: str = "",
    secret_key: str = "",
    username: str = "",
    password: str = "",
    http: httpx.AsyncClient | None = None,
) -> McpTransport | RestTransport:
    """The live transport named by ``SHOPWARE_ADMIN_TRANSPORT`` over one shared HTTP
    client and token provider."""
    client = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    tokens = OAuthTokenProvider(
        shop_url,
        http=client,
        access_key=access_key,
        secret_key=secret_key,
        username=username,
        password=password,
    )
    normalised = (kind or "mcp").strip().lower()
    if normalised == "rest":
        transport: McpTransport | RestTransport = RestTransport(
            shop_url, headers_provider=tokens.headers, http=client
        )
    elif normalised == "mcp":
        transport = McpTransport(shop_url, headers_provider=tokens.headers, http=client)
    else:
        raise AdminAPIError(f"unknown SHOPWARE_ADMIN_TRANSPORT {kind!r}; use mcp or rest")
    if http is None:
        transport._owns_http = True  # the factory created the client, so it closes it
    return transport
