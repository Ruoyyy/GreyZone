# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""UCP client: MCP handshake and tool shapes, REST shapes, signing on both transports,
idempotency, retry, cart-gone, and the unavailable-transport fallback in both directions."""

from __future__ import annotations

import json

import httpx
import pytest

from shopware_common.http_signing import RequestSigner
from storefront.api.ucp_client import (
    MCP_TOOLS,
    UcpCartGoneError,
    UcpClient,
    UcpError,
    UcpTransportUnavailable,
    mcp_arguments,
    rest_request,
)

from .replay import CART_ID, GONE_CART_ID, MCP_SESSION_ID, PRODUCT_ID, VARIANT_S, ShopwareReplay


def capture_client(
    *responses: httpx.Response, transport: str = "rest", **kwargs
) -> tuple[UcpClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = UcpClient(
        shop_url="http://shopware.test",
        profile_url="http://shopware.test/.well-known/ucp",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        retry_backoff=0.0,
        transport=transport,  # type: ignore[arg-type]
        signer=None,
        **kwargs,
    )
    return client, requests


def replay_client(
    shop: ShopwareReplay, transport: str, signer: RequestSigner | None = None, **kwargs
) -> UcpClient:
    return UcpClient(
        shop_url="http://shopware.test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(shop.handle)),
        retry_backoff=0.0,
        transport=transport,  # type: ignore[arg-type]
        signer=signer,
        **kwargs,
    )


# ---------------------------------------------------------------------------- MCP transport


async def test_mcp_runs_the_handshake_once_and_calls_the_recorded_tool_shapes():
    shop = ShopwareReplay()
    client = replay_client(shop, "mcp")
    payload = await client.call_ucp(
        "search_catalog", {"catalog": {"query": "shirt", "pagination": {"limit": 3}}}
    )
    assert payload["products"][0]["id"] == PRODUCT_ID
    cart = await client.call_ucp(
        "create_cart", {"cart": {"line_items": [{"item": {"id": VARIANT_S}, "quantity": 1}]}}
    )
    assert cart["id"] == CART_ID

    methods = [
        json.loads(r.content)["method"]
        for r in shop.requests
        if r.url.path == "/ucp/mcp" and r.content
    ]
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert methods.count("initialize") == 1
    assert all(r.headers.get("mcp-session-id") == MCP_SESSION_ID for r in shop.requests[2:])
    assert all(r.headers["ucp-agent"].startswith("platform; profile=") for r in shop.requests)
    assert all(r.headers.get("idempotency-key") for r in shop.requests if r.method == "POST")

    search, create = shop.mcp_calls
    assert search == {
        "name": MCP_TOOLS["search_catalog"],
        "arguments": {"query": "shirt", "limit": 3},
    }
    assert create["name"] == MCP_TOOLS["create_cart"]
    assert create["arguments"]["dryRun"] is False
    assert create["arguments"]["payload"]["line_items"][0]["item"]["id"] == VARIANT_S
    await client.aclose()
    assert MCP_SESSION_ID not in shop.mcp_sessions  # DELETE ended the session


async def test_mcp_sends_ids_as_a_json_string_and_payload_as_an_object():
    assert mcp_arguments("get_product", {"catalog": {"id": PRODUCT_ID}}) == {
        "ids": json.dumps([PRODUCT_ID])
    }
    assert mcp_arguments("update_cart", {"id": CART_ID, "cart": {"line_items": []}}) == {
        "id": CART_ID,
        "payload": {"line_items": []},
        "dryRun": False,
    }


async def test_mcp_tool_error_for_a_gone_cart_is_the_recoverable_subclass():
    client = replay_client(ShopwareReplay(), "mcp", fallback=False)
    with pytest.raises(UcpCartGoneError):
        await client.call_ucp("get_cart", {"id": GONE_CART_ID})


async def test_a_lost_mcp_session_is_reinitialised_once():
    shop = ShopwareReplay()
    client = replay_client(shop, "mcp")
    await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})
    shop.mcp_sessions.clear()  # the server forgot us
    payload = await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})
    assert payload["products"]
    methods = [json.loads(r.content)["method"] for r in shop.requests if r.content]
    assert methods.count("initialize") == 2


async def test_mcp_signature_is_verified_by_the_shop_and_unsigned_is_refused():
    signer = RequestSigner.generate()
    shop = ShopwareReplay(public_key=signer.public_key)
    signed = replay_client(shop, "mcp", signer=signer, fallback=False)
    assert (await signed.call_ucp("search_catalog", {"catalog": {"query": "shirt"}}))["products"]
    unsigned = replay_client(shop, "mcp", signer=None, fallback=False)
    with pytest.raises(UcpError):
        await unsigned.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})


# ---------------------------------------------------------------------------- REST transport


async def test_rest_search_sends_ucp_agent_header_and_profile():
    client, requests = capture_client(httpx.Response(200, json={"products": []}))
    await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})
    headers = requests[0].headers
    assert headers["ucp-agent"] == 'platform; profile="http://shopware.test/.well-known/ucp"'
    assert json.loads(requests[0].content)["query"] == "shirt"
    assert str(requests[0].url).endswith("/ucp/v1/catalog/search")


async def test_rest_writes_send_idempotency_key_and_reads_do_not():
    client, requests = capture_client(httpx.Response(201, json={"id": "c1", "line_items": []}))
    await client.call_ucp("create_cart", {"cart": {"line_items": []}})
    assert requests[0].headers.get("idempotency-key")
    await client.call_ucp("get_cart", {"id": "c1"})
    assert requests[1].method == "GET" and "idempotency-key" not in requests[1].headers


async def test_rest_signature_is_verified_by_the_shop():
    signer = RequestSigner.generate()
    shop = ShopwareReplay(public_key=signer.public_key)
    client = replay_client(shop, "rest", signer=signer, fallback=False)
    cart = await client.call_ucp(
        "create_cart", {"cart": {"line_items": [{"item": {"id": VARIANT_S}, "quantity": 1}]}}
    )
    assert cart["id"] == CART_ID
    assert (await client.call_ucp("get_cart", {"id": CART_ID}))["line_items"]
    request = next(r for r in shop.requests if r.url.path == "/ucp/v1/carts")
    assert request.headers["content-digest"].startswith("sha-256=:")
    assert request.headers["signature-input"].startswith("sig=(")


async def test_a_transient_status_is_retried_once():
    ok = httpx.Response(200, json={"products": []})
    client, requests = capture_client(httpx.Response(429, json={}), ok)
    assert await client.call_ucp("search_catalog", {"catalog": {"query": "x"}}) == {"products": []}
    assert len(requests) == 2


async def test_rest_cart_gone_code_raises_recoverable_subclass():
    client, _ = capture_client(
        httpx.Response(
            404,
            json={
                "messages": [
                    {
                        "type": "error",
                        "code": "cart_not_found",
                        "content": "The requested cart is gone",
                    }
                ]
            },
        ),
        fallback=False,
    )
    with pytest.raises(UcpCartGoneError, match="gone"):
        await client.call_ucp("get_cart", {"id": GONE_CART_ID})


def test_rest_request_shapes():
    assert rest_request("get_product", {"catalog": {"id": PRODUCT_ID}}) == (
        "GET",
        f"/catalog/product/{PRODUCT_ID}",
        None,
    )
    method, path, body = rest_request("update_cart", {"id": CART_ID, "cart": {"line_items": [1]}})
    assert (method, path, body) == (
        "PATCH",
        f"/carts/{CART_ID}",
        {"id": CART_ID, "line_items": [1]},
    )
    with pytest.raises(UcpError):
        rest_request("apply_discount", {"cart_id": CART_ID, "code": "X"})


# ---------------------------------------------------------------------------- fallback


async def test_mcp_endpoint_missing_falls_back_to_rest():
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ucp/mcp":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"products": [{"id": PRODUCT_ID}]})

    client = UcpClient(
        shop_url="http://shopware.test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        retry_backoff=0.0,
        transport="mcp",
        signer=None,
    )
    assert (await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}}))["products"]


async def test_rest_endpoint_missing_falls_back_to_mcp():
    shop = ShopwareReplay()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/ucp/v1/"):
            return httpx.Response(404, json={"detail": "no rest"})
        return shop.handle(request)

    client = UcpClient(
        shop_url="http://shopware.test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        retry_backoff=0.0,
        transport="rest",
        signer=None,
    )
    payload = await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})
    assert payload["products"][0]["id"] == PRODUCT_ID
    assert shop.mcp_calls[0]["name"] == MCP_TOOLS["search_catalog"]


async def test_without_fallback_an_unavailable_transport_raises():
    client, _ = capture_client(httpx.Response(503, text="down"), fallback=False)
    with pytest.raises(UcpTransportUnavailable):
        await client.call_ucp("search_catalog", {"catalog": {"query": "x"}})


async def test_a_ucp_error_document_never_falls_back():
    client, requests = capture_client(
        httpx.Response(
            422,
            json={
                "messages": [
                    {"type": "error", "code": "invalid_request", "content": "quantity must be > 0"}
                ]
            },
        ),
    )
    with pytest.raises(UcpError, match="quantity"):
        await client.call_ucp("create_cart", {"cart": {"line_items": []}})
    assert all(r.url.path != "/ucp/mcp" for r in requests)


async def test_jsonrpc_error_raises():
    client, _ = capture_client(
        httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "nope"}}
        ),
        transport="mcp",
        fallback=False,
    )
    # initialize also answers with this body; the client must not loop.
    with pytest.raises(UcpError):
        await client.call_ucp("search_catalog", {"catalog": {"query": "x"}})
