# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The real ``McpTransport`` and ``RestTransport`` against in-process fakes of the
Shopware Admin MCP server and Admin REST, both answering from the same ``FakeAdmin``.
This proves the argument shaping (JSON-string criteria, ``dryRun`` flags, tool names
from the recorded ``tools/list``) end to end without a network."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from merchant.api.admin_client import (
    MCP_TOOLS_USED,
    AdminAPIError,
    McpTransport,
    OAuthTokenProvider,
    RestTransport,
    build_transport,
    writes,
)
from merchant.api.fake_admin import OIL, SHIRT, FakeAdmin, FakeToolError
from merchant.api.ledger import SqliteChangeLedger
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import PriceUpdateItem

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 600, "token_type": "Bearer"}


class FakeMcpServer:
    """Streamable-HTTP handshake as Shopware 6.7.13 ships it; ``tools/call`` goes to
    ``FakeAdmin.handle_tool_call`` in mcp mode."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.sessions: set[str] = set()
        self.requests: list[dict[str, Any]] = []
        self.headers_seen: list[dict[str, str]] = []
        self.token_requests = 0
        # Tool payloads above this many bytes are parked behind a ``shopware://tool-result``
        # resource and the call answers ``data: null`` + ``_meta.resourceUri`` (6.7.13 does
        # this at roughly 100 KB); ``None`` delivers everything inline.
        self.offload_above: int | None = None
        self.resources: dict[str, str] = {}
        self.parked = 0
        self.drop_parked = False  # park, but let the resource expire at once

    def _offload(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(payload)
        if self.offload_above is None or len(text) <= self.offload_above:
            return payload
        self.parked += 1
        uri = f"shopware://tool-result/{self.parked:032x}"
        if not self.drop_parked:
            self.resources[uri] = text
        return {
            "success": True,
            "data": None,
            "_meta": {
                **(payload.get("_meta") or {}),
                "resourceUri": uri,
                "responseSize": len(text),
                "note": "Response too large for inline delivery. Prefer re-running the tool "
                'with tighter "includes" or a lower "limit".',
            },
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/token":
            self.token_requests += 1
            body = json.loads(request.content)
            assert body["grant_type"] == "client_credentials"
            return httpx.Response(200, json=TOKEN_RESPONSE)
        assert request.url.path == "/api/_mcp"
        self.headers_seen.append(dict(request.headers))
        if request.method == "DELETE":
            self.sessions.discard(request.headers.get("mcp-session-id", ""))
            return httpx.Response(200)
        body = json.loads(request.content)
        self.requests.append(body)
        method = body.get("method")
        if method == "initialize":
            sid = f"sess-{len(self.sessions) + 1}"
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
        if request.headers.get("mcp-session-id") not in self.sessions:
            return httpx.Response(
                400,
                json={
                    "jsonrpc": "2.0",
                    "id": "",
                    "error": {"code": -32600, "message": "A valid session id is REQUIRED"},
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return self._reply(body["id"], {"tools": self.admin.tool_list()})
        if method == "tools/call":
            try:
                payload, is_error = self.admin.handle_tool_call(
                    body["params"]["name"], body["params"].get("arguments") or {}
                )
            except FakeToolError as error:
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {"code": error.code, "message": str(error)},
                    },
                )
            if not is_error:
                payload = self._offload(payload)
            return self._reply(
                body["id"],
                {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": is_error},
            )
        if method == "resources/read":
            uri = body["params"]["uri"]
            if uri not in self.resources:
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {"code": -32002, "message": f"Resource not found: {uri}"},
                    },
                )
            return self._reply(
                body["id"],
                {
                    "contents": [
                        {"uri": uri, "mimeType": "application/json", "text": self.resources[uri]}
                    ]
                },
            )
        return httpx.Response(
            400,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32601, "message": "unknown"},
            },
        )

    @staticmethod
    def _reply(
        rid: Any, result: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": rid, "result": result}, headers=headers or {}
        )


class FakeRestServer:
    """Just enough Admin REST: ``/api/search/{entity}`` (flat rows with ``Accept:
    application/json``), ``PATCH /api/{entity}/{id}``, ``/api/_action/sync``."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.requests: list[tuple[str, str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        if path == "/api/oauth/token":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        assert request.headers.get("authorization") == "Bearer tok-1"
        if path.startswith("/api/search/"):
            entity = path.rsplit("/", 1)[-1].replace("-", "_")  # REST routes are kebab-case
            criteria = dict(body or {})
            limit = int(criteria.pop("limit", 25))
            page = int(criteria.pop("page", 1))
            term = str(criteria.pop("term", ""))
            criteria.pop("total-count-mode", None)
            aggregations = criteria.pop("aggregations", None)
            result = self.admin._search(entity, criteria, limit=limit, page=page, term=term)  # noqa: SLF001
            response: dict[str, Any] = {"total": result.total, "data": result.rows}
            if aggregations:
                response["aggregations"] = {
                    name: {**value, "apiAlias": f"{name}_aggregation", "extensions": []}
                    for name, value in self.admin._aggregate(  # noqa: SLF001
                        entity, aggregations, criteria.get("filter") or []
                    ).items()
                }
            return httpx.Response(200, json=response)
        if path == "/api/_action/sync":
            written: dict[str, list[str]] = {}
            for operation in body:
                result = self.admin._upsert(  # noqa: SLF001
                    operation["entity"], operation["payload"], dry_run=False
                )
                if not result["success"]:
                    return httpx.Response(
                        400,
                        json={"errors": [{"detail": result["error"], "source": {"pointer": "/0"}}]},
                    )
                for row in result["data"]:
                    written.setdefault(row["entity"], []).extend(
                        i if isinstance(i, str) else json.dumps(i) for i in row["ids"]
                    )
            return httpx.Response(200, json={"data": written, "deleted": [], "notFound": []})
        if request.method == "PATCH":
            _, _, entity, entity_id = path.split("/")
            result = self.admin._upsert(  # noqa: SLF001
                entity.replace("-", "_"), {**body, "id": entity_id}, dry_run=False
            )
            if not result["success"]:
                return httpx.Response(400, json={"errors": [{"detail": result["error"]}]})
            return httpx.Response(204)
        return httpx.Response(404, json={"errors": [{"detail": "not found"}]})


def _mcp(admin: FakeAdmin) -> tuple[McpTransport, FakeMcpServer]:
    server = FakeMcpServer(admin)
    http = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    transport = build_transport(
        "mcp", "http://shop.test", access_key="SWIA", secret_key="secret", http=http
    )
    assert isinstance(transport, McpTransport)
    transport.client._retry_backoff = 0  # noqa: SLF001 - no sleeps in tests
    return transport, server


def _rest(admin: FakeAdmin) -> tuple[RestTransport, FakeRestServer]:
    server = FakeRestServer(admin)
    http = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    transport = build_transport(
        "rest", "http://shop.test", access_key="SWIA", secret_key="secret", http=http
    )
    assert isinstance(transport, RestTransport)
    return transport, server


@pytest.fixture
def fake(now) -> FakeAdmin:
    return FakeAdmin(mode="mcp", now=now)


# ------------------------------------------------------------------ MCP


async def test_mcp_transport_shapes_arguments_as_the_live_tools_expect(fake: FakeAdmin):
    transport, server = _mcp(fake)
    result = await transport.search(
        "product", {"filter": [{"type": "equals", "field": "parentId", "value": None}]}, limit=10
    )
    assert result.total == 4 and {r["id"] for r in result.rows} >= {SHIRT, OIL}
    name, arguments = fake.tool_calls[-1]
    assert name == "shopware-entity-search"
    assert isinstance(arguments["criteria"], str)  # criteria travels as a JSON string
    assert json.loads(arguments["criteria"])["filter"][0]["field"] == "parentId"
    assert arguments["limit"] == 10 and arguments["page"] == 1 and arguments["term"] == ""
    assert server.token_requests == 1
    assert server.headers_seen[-1]["authorization"] == "Bearer tok-1"
    methods = [r["method"] for r in server.requests]
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]

    row = await transport.read("product", OIL, {"associations": {"tax": {}}})
    assert row is not None and row["tax"]["taxRate"] == 19.0
    assert await transport.read("product", "f" * 32) is None

    aggregations = await transport.aggregate(
        "order",
        [{"name": "orders", "type": "count", "field": "id"}],
        [{"type": "equals", "field": "stateMachineState.technicalName", "value": "completed"}],
    )
    assert aggregations["orders"]["count"] > 0
    name, arguments = fake.tool_calls[-1]
    assert name == "shopware-entity-aggregate"
    assert isinstance(arguments["aggregations"], str) and isinstance(arguments["filters"], str)

    preview = await transport.upsert("product", {"id": OIL, "stock": 41}, dry_run=True)
    assert preview.success and preview.dry_run is True and preview.server_validated
    assert [w["entity"] for w in preview.written] == ["product", "product_translation"]
    assert fake.tool_calls[-1][1]["dryRun"] is True and isinstance(
        fake.tool_calls[-1][1]["payload"], str
    )
    assert fake.product(OIL)["stock"] == 40

    rejected = await transport.upsert("product", {"id": OIL, "stock": "lots"}, dry_run=True)
    assert rejected.success is False and "type int" in (rejected.error or "")

    live = await transport.upsert("product", {"id": OIL, "stock": 41}, dry_run=False)
    assert live.success and live.dry_run is False and fake.product(OIL)["stock"] == 41

    deleted = await transport.delete("rule", ["a" * 32], dry_run=False)
    assert deleted.success and fake.tool_calls[-1][1] == {
        "entity": "rule",
        "ids": json.dumps(["a" * 32]),
        "dryRun": False,
    }
    assert [c.operation for c in transport.calls] == [
        "search", "read", "read", "aggregate", "upsert", "upsert", "upsert", "delete"
    ]  # fmt: skip
    await transport.aclose()
    assert server.sessions == set()


async def test_mcp_transport_collects_a_result_the_server_parked_behind_a_resource(
    fake: FakeAdmin,
):
    # Live 6.7.13: an order search with associations (~160 KB) comes back as
    # ``data: null`` + ``_meta.resourceUri``; taking that as "no orders" emptied the
    # portal's recent-orders feed. The transport reads the resource instead.
    transport, server = _mcp(fake)
    server.offload_above = 200
    inline = await transport.search("order", {"associations": {"lineItems": {}}}, limit=1)
    server.offload_above = None
    full = await transport.search("order", {"associations": {"lineItems": {}}}, limit=1)
    assert inline.rows == full.rows and inline.total == full.total and inline.rows
    methods = [r["method"] for r in server.requests]
    assert methods.count("resources/read") == 1
    assert server.requests[methods.index("resources/read")]["params"]["uri"].startswith(
        "shopware://tool-result/"
    )

    server.offload_above = 200
    server.drop_parked = True  # the parked result expired before it could be read
    with pytest.raises(AdminAPIError, match="offloaded result .* unreadable"):
        await transport.search("order", {"associations": {"lineItems": {}}}, limit=1)
    await transport.aclose()


async def test_mcp_transport_surfaces_tool_and_rpc_errors(fake: FakeAdmin):
    transport, _ = _mcp(fake)
    with pytest.raises(AdminAPIError, match="not found"):
        await transport.search("nonsense", {})
    with pytest.raises(AdminAPIError, match="Error while executing tool"):
        await transport.aggregate(
            "order", [{"name": "x", "type": "date-histogram", "field": "orderDateTime"}]
        )
    await transport.aclose()
    fake.tool_schemas().pop("shopware-entity-delete")  # an integration without that tool
    restricted, _ = _mcp(fake)
    with pytest.raises(AdminAPIError, match="not available"):
        await restricted.delete("rule", ["a" * 32], dry_run=True)
    await restricted.aclose()


async def test_every_tool_the_backend_uses_is_in_the_recorded_tools_list(fake: FakeAdmin):
    assert set(MCP_TOOLS_USED) <= set(fake.tool_schemas())


def test_fake_tool_layer_rejects_unknown_names_arguments_and_types(fake: FakeAdmin):
    with pytest.raises(FakeToolError, match="Tool not found"):
        fake.handle_tool_call("shopware-entity-explode", {"entity": "product"})
    with pytest.raises(FakeToolError, match="Unknown argument: bogus"):
        fake.handle_tool_call("shopware-entity-search", {"entity": "product", "bogus": 1})
    with pytest.raises(FakeToolError, match="Expected string"):
        fake.handle_tool_call(
            "shopware-entity-search", {"entity": "product", "criteria": {"limit": 1}}
        )
    with pytest.raises(FakeToolError, match="Missing required argument: payload"):
        fake.handle_tool_call("shopware-entity-upsert", {"entity": "product"})


async def test_backend_over_mcp_transport_stages_and_applies(
    fake: FakeAdmin, settings, config, now, session
):
    transport, _ = _mcp(fake)
    backend = ShopwareMerchantBackend(
        transport,
        settings,
        config,
        ledger=SqliteChangeLedger(config, ":memory:"),
        clock=lambda: now,
    )
    await backend.warm()
    assert backend.store_name == "Demo Shop" and backend.shop_info()["transport"] == "mcp"
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    assert writes(transport.calls) == []
    assert any(note.startswith("preview: server dry-run OK") for note in change.guardrail_notes)
    applied = await backend.apply_change(session, change.change_id)
    assert applied.status.value == "applied" and fake.product(OIL)["price"][0]["gross"] == 13.5
    context = await backend.get_merchant_context(session)
    assert context["transport"] == "mcp" and context["previews_server_validated"] is True
    await transport.aclose()


async def test_backend_over_mcp_transport_stages_and_applies_a_refund(
    fake: FakeAdmin, settings, config, now, session
):
    transport, _ = _mcp(fake)
    backend = ShopwareMerchantBackend(
        transport,
        settings,
        config,
        ledger=SqliteChangeLedger(config, ":memory:"),
        clock=lambda: now,
    )
    await backend.warm()
    change = await backend.stage_refund(session, "10001")
    assert writes(transport.calls) == [] and fake.payment_state("10001") == "paid"
    assert fake.tool_calls[-1] == (
        "shopware-order-state",
        {"orderNumber": "10001", "transactionAction": "refund", "dryRun": True},
    )
    applied = await backend.apply_change(session, change.change_id)
    assert applied.status.value == "applied" and fake.payment_state("10001") == "refunded"
    await transport.aclose()


# ------------------------------------------------------------------ REST


async def test_rest_transport_refuses_order_state_transitions(now):
    transport, server = _rest(FakeAdmin(now=now))
    with pytest.raises(AdminAPIError, match="need the Admin MCP transport"):
        await transport.order_state("10001", transaction_action="refund", dry_run=True)
    assert all("/_action/order" not in path for _, path, _ in server.requests)
    await transport.aclose()


async def test_rest_transport_reads_and_writes(now):
    fake = FakeAdmin(now=now)
    transport, server = _rest(fake)
    result = await transport.search(
        "product", {"filter": [{"type": "equals", "field": "id", "value": OIL}]}
    )
    assert result.total == 1 and result.rows[0]["productNumber"] == "CA-OIL"
    method, path, body = server.requests[-1]
    assert (method, path) == ("POST", "/api/search/product") and body["limit"] == 25
    assert server.requests[0][1] == "/api/oauth/token"

    aggregations = await transport.aggregate(
        "order", [{"name": "sales", "type": "sum", "field": "amountTotal"}]
    )
    assert aggregations["sales"]["sum"] > 0 and "apiAlias" not in aggregations["sales"]

    preview = await transport.upsert("product", {"id": OIL, "stock": 99}, dry_run=True)
    assert preview.success and preview.server_validated is False and preview.written == []
    assert fake.product(OIL)["stock"] == 40  # REST has no dry run; nothing was sent
    assert server.requests[-1][1] != "/api/product/" + OIL

    live = await transport.upsert("product", {"id": OIL, "stock": 99}, dry_run=False)
    assert live.success and live.dry_run is False
    assert server.requests[-1][:2] == ("PATCH", f"/api/product/{OIL}")
    assert fake.product(OIL)["stock"] == 99

    batch = await transport.upsert(
        "product", [{"id": OIL, "active": True}, {"id": SHIRT, "active": True}], dry_run=False
    )
    assert batch.success and server.requests[-1][1] == "/api/_action/sync"

    promotion = await transport.upsert(
        "promotion",
        {
            "id": "b" * 32,
            "name": "Rest promo",
            "discounts": [{"scope": "cart", "type": "percentage", "value": 5.0}],
        },
        dry_run=False,
    )
    assert promotion.success and server.requests[-1][1] == "/api/_action/sync"
    assert "promotion_discount" in promotion.entities()

    refused = await transport.upsert("product", {"id": OIL, "stock": "lots"}, dry_run=False)
    assert refused.success is False and "type int" in (refused.error or "")
    await transport.aclose()


async def test_backend_over_rest_transport_notes_the_unvalidated_preview(
    settings, config, now, session
):
    fake = FakeAdmin(now=now)
    transport, _ = _rest(fake)
    backend = ShopwareMerchantBackend(
        transport,
        settings,
        config,
        ledger=SqliteChangeLedger(config, ":memory:"),
        clock=lambda: now,
    )
    await backend.warm()
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    assert (
        "preview not server-validated (REST transport) — Shopware checks the payload on apply"
        in change.guardrail_notes
    )
    assert writes(transport.calls) == []
    context = await backend.get_merchant_context(session)
    assert context["transport"] == "rest" and context["previews_server_validated"] is False
    applied = await backend.apply_change(session, change.change_id)
    assert applied.status.value == "applied" and fake.product(OIL)["price"][0]["gross"] == 13.5
    await transport.aclose()


# ------------------------------------------------------------------ OAuth


async def test_token_provider_requires_credentials_and_caches_tokens():
    with pytest.raises(AdminAPIError, match="no Admin credentials"):
        OAuthTokenProvider("http://shop.test", http=httpx.AsyncClient())
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=TOKEN_RESPONSE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OAuthTokenProvider("http://shop.test", http=http, access_key="k", secret_key="s")
    assert provider.grant == "client_credentials"
    assert await provider.headers() == {"Authorization": "Bearer tok-1"}
    await provider.headers()
    assert calls == 1
    with pytest.raises(AdminAPIError, match="unknown SHOPWARE_ADMIN_TRANSPORT"):
        build_transport("soap", "http://shop.test", access_key="k", secret_key="s", http=http)
