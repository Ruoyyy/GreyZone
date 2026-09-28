# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The wired storefront app, on routes that reach no network: the grid's add button (details
first, then the gated add), cart attach, the click-time handoff page, identity linking."""

from __future__ import annotations

import asyncio
import re
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from demo_common import SESSION_HEADER
from shopping_agent import ShoppingSessionContext
from shopping_agent.prompt import build_dynamic_context
from shopping_agent.types import ProductDetails
from shopware_common.handoff import HandoffCodeVerifier
from shopware_common.http_signing import RequestSigner
from storefront.api import main as main_module
from storefront.api.identity import IdentityError, ShopwareIdentityLinking
from storefront.api.store_api import StoreApiClient, StoreApiError
from storefront.api.ucp_client import UcpClient

from .conftest import HANDOFF_SECRET
from .replay import (
    CART_ID,
    CUSTOMER_EMAIL,
    CUSTOMER_PASSWORD,
    CUSTOMER_TOKEN,
    GONE_CART_ID,
    MAIN_ID,
    PRODUCT_ID,
    VARIANT_S,
    ShopwareReplay,
)


@pytest.fixture
def client():
    return TestClient(main_module.app, base_url="http://localhost")


def start(client: TestClient) -> dict[str, str]:
    token = client.post("/api/session", json={"user_id": "guest"}).json()["session_id"]
    return {SESSION_HEADER: token}


def _bind_replay(
    monkeypatch, shop: ShopwareReplay | None = None, signer: RequestSigner | None = None
) -> ShopwareReplay:
    shop = shop or ShopwareReplay(public_key=signer.public_key if signer else None)
    transport = httpx.MockTransport(shop.handle)
    ucp = UcpClient(
        shop_url="http://shopware.test",
        http=httpx.AsyncClient(transport=transport),
        retry_backoff=0.0,
        transport="mcp",
        signer=signer,
    )
    store = StoreApiClient(
        "http://shopware.test", access_key="test-key", http=httpx.AsyncClient(transport=transport)
    )
    monkeypatch.setattr(main_module.backend, "client", ucp)
    monkeypatch.setattr(main_module.backend, "store_api", store)
    monkeypatch.setattr(main_module.backend.policies, "_store_api", store)
    monkeypatch.setattr(
        main_module.backend.handoff,
        "_issuer",
        main_module.HandoffBroker(
            "http://shopware.test", public_url="http://host.test", secret=HANDOFF_SECRET
        )._issuer,
    )
    monkeypatch.setattr(main_module.backend.handoff, "public_url", "http://host.test")
    monkeypatch.setattr(main_module.backend.handoff, "shop_url", "http://shopware.test")
    return shop


def test_health_and_session(client):
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["store"] == "Shopware"
    assert start(client)


def test_client_user_id_cannot_select_another_shoppers_memory_key(client):
    first = client.post("/api/session", json={"user_id": "customer-1"}).json()
    second = client.post("/api/session", json={"user_id": "customer-1"}).json()
    assert first["user_id"].startswith("guest:")
    assert second["user_id"].startswith("guest:")
    assert first["user_id"] != second["user_id"]


def test_order_tool_and_checkout_preview_are_separate_in_the_live_agent():
    tools = {tool["name"]: tool for tool in main_module.agent._tools}
    assert "place_order" in tools
    assert "结账" in tools["place_order"]["description"]
    assert "结算" in tools["checkout"]["description"]
    assert "does not place an order" in tools["checkout"]["description"]


def test_the_catalog_detail_route_takes_a_hex_id(client):
    product = ProductDetails(product_id=PRODUCT_ID, title="Fake Shirt", price=29.99, currency="EUR")
    main_module.backend.products[PRODUCT_ID] = product
    try:
        response = client.get(f"/api/products/{PRODUCT_ID}")
        assert response.status_code == 200
        assert response.json()["product_id"] == PRODUCT_ID
        assert client.get("/api/products/ffffffffffffffffffffffffffffffff").status_code == 404
    finally:
        main_module.backend.products.pop(PRODUCT_ID, None)


def test_a_fresh_session_has_an_empty_cart_and_no_checkout_url(client):
    payload = client.get("/api/cart", headers=start(client)).json()
    assert payload["items"] == []
    assert payload["checkout_url"] is None
    assert payload["cart_id"] is None


def test_the_add_button_reads_details_first_so_a_variant_enters_provenance(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    response = client.post(
        "/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cart"]["item_count"] == 1
    assert body["cart_id"] == CART_ID
    assert body["checkout_url"].startswith("http://host.test/api/checkout/handoff/")
    record = main_module.host.sessions.require(headers[SESSION_HEADER])
    assert record.state.seen_products[VARIANT_S].variant_of == PRODUCT_ID


def test_the_add_button_runs_under_the_customers_clock(client, monkeypatch):
    """``shopping_context`` gives the executor an aware ``now`` in the browser's zone
    (``X-Timezone``), else ``HOST_TIMEZONE``; never the server's naive wall clock."""
    _bind_replay(monkeypatch)
    monkeypatch.setenv("HOST_TIMEZONE", "Europe/Berlin")
    seen: list[ShoppingSessionContext] = []
    real_executor_class = main_module.agent.executor_class

    def spying_executor(**kwargs):
        seen.append(kwargs["session"])
        return real_executor_class(**kwargs)

    monkeypatch.setattr(main_module.agent, "executor_class", spying_executor)
    headers = start(client)
    client.post(
        "/api/cart/add",
        json={"product_id": VARIANT_S, "quantity": 1},
        headers={**headers, "X-Timezone": "America/Chicago"},
    )
    client.post("/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers)
    # Each add builds two executors: ours for the details read (the host clock), then the
    # shared host's ``direct_add`` (its own context, see the vendor pin).
    ours = seen[0::2]
    assert [context.timezone for context in ours] == ["America/Chicago", "Europe/Berlin"]
    for context in ours:
        assert context.now is not None and context.now.utcoffset() is not None
        assert context.now.tzinfo == ZoneInfo(context.timezone)


def test_storefront_focus_reads_details_so_the_pdp_enters_provenance(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    response = client.post(
        "/api/session/focus", json={"product_id": PRODUCT_ID}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["product_id"] == PRODUCT_ID
    assert "T-Shirt" in body["title"]
    record = main_module.host.sessions.require(headers[SESSION_HEADER])
    assert PRODUCT_ID in record.state.seen_products
    assert VARIANT_S in record.state.seen_products
    assert any("storefront" in event.lower() for event in record.pending_app_events)


def test_sync_catalog_puts_grid_titles_into_the_session(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    response = client.post("/api/session/sync-catalog", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["products"] >= 3
    record = main_module.host.sessions.require(headers[SESSION_HEADER])
    assert MAIN_ID in record.state.seen_products
    assert PRODUCT_ID in record.state.seen_products
    assert any("Main product" in event for event in record.pending_app_events)


def test_the_add_button_on_a_family_is_held_with_the_route_to_a_variant(client, monkeypatch):
    _bind_replay(monkeypatch)
    response = client.post(
        "/api/cart/add", json={"product_id": PRODUCT_ID, "quantity": 1}, headers=start(client)
    )
    assert response.status_code == 400
    assert (
        "variant" in response.json()["detail"].lower()
        or "option" in response.json()["detail"].lower()
    )


def test_the_add_button_on_an_unknown_product_is_404(client, monkeypatch):
    _bind_replay(monkeypatch)
    response = client.post(
        "/api/cart/add",
        json={"product_id": "ffffffffffffffffffffffffffffffff", "quantity": 1},
        headers=start(client),
    )
    assert response.status_code == 404


def test_the_handoff_page_mints_a_one_time_code_and_posts_it_to_the_shop(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    added = client.post(
        "/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers
    ).json()
    ticket_url = added["checkout_url"]
    path = ticket_url[len("http://host.test") :]
    page = client.get(path)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    html = page.text
    assert 'method="post" action="http://shopware.test/claude-commerce/continue"' in html
    assert CART_ID not in html
    code = re.search(r'name="code" value="([^"]+)"', html).group(1)
    assert HandoffCodeVerifier(HANDOFF_SECRET).verify(code) == CART_ID
    # A second click mints a fresh code (the plugin refuses replays of the first).
    second = re.search(r'name="code" value="([^"]+)"', client.get(path).text).group(1)
    assert second != code
    assert client.get("/api/checkout/handoff/unknown-ticket").status_code == 404


def test_the_handoff_page_refuses_when_the_cart_is_gone(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    added = client.post(
        "/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers
    ).json()
    path = added["checkout_url"][len("http://host.test") :]
    client.post("/api/reset", json={}, headers=headers)
    assert client.get(path).status_code == 404


def test_attach_binds_the_session_to_a_shopware_cart(client, monkeypatch):
    _bind_replay(monkeypatch)
    headers = start(client)
    assert client.get("/api/cart", headers=headers).json()["cart_id"] is None

    async def seed() -> str:
        session = ShoppingSessionContext(session_id="attach-seed", user_id="guest")
        await main_module.backend.search_products(session, "shirt", limit=1)
        await main_module.backend.add_to_cart(session, PRODUCT_ID, 1)
        cart_id = main_module.backend.cart_id_for("attach-seed")
        assert cart_id
        return cart_id

    cart_id = asyncio.run(seed())
    attached = client.post("/api/cart/attach", json={"cart_id": cart_id}, headers=headers)
    assert attached.status_code == 200
    assert attached.json()["item_count"] == 1
    assert attached.json()["cart_id"] == cart_id

    unknown = client.post("/api/cart/attach", json={"cart_id": GONE_CART_ID}, headers=headers)
    assert unknown.status_code == 404
    assert client.get("/api/cart", headers=headers).json()["cart_id"] == cart_id


# ---------------------------------------------------------------------------- identity linking


def test_a_fresh_session_can_use_shopware_account_login(client):
    status = client.get("/api/auth/status", headers=start(client)).json()
    assert status["signed_in"] is False
    assert status["available"] is True


def test_signin_start_requires_a_session(client):
    assert client.get("/api/auth/shopware/start", follow_redirects=False).status_code == 401


def test_signin_start_offers_credentials_without_oauth(client):
    response = client.get(
        "/api/auth/shopware/start", params={"session_id": start(client)[SESSION_HEADER]}
    )
    assert response.status_code == 200
    assert response.json()["login"] == "/api/auth/shopware/login"


def test_store_api_login_binds_an_order_identity_without_oauth(client, monkeypatch):
    _bind_replay(monkeypatch)
    linking = ShopwareIdentityLinking(
        main_module.backend.client, main_module.backend.store_api,
        public_url="http://localhost:8004",
    )
    monkeypatch.setattr(main_module, "identity", linking)
    monkeypatch.setattr(main_module.backend, "_order_identity_provider", linking.order_identity)
    headers = start(client)
    guest_context = asyncio.run(main_module.backend.get_account_context(main_module.shopping_context(main_module.host.sessions.require(headers[SESSION_HEADER]))))
    assert guest_context["signed_in"] is False
    added = client.post(
        "/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers
    )
    assert added.status_code == 200, added.text
    login = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD},
        headers=headers,
    )
    assert login.status_code == 200, login.text
    assert linking.order_identity(headers[SESSION_HEADER]).owner_id == "customer-1"
    assert main_module.host.sessions.require(headers[SESSION_HEADER]).user_id == "shopware:customer-1"
    assert client.get("/api/cart", headers=headers).json()["item_count"] == 1
    assert client.get("/api/cart", headers=headers).json()["cart_id"] is None
    account = asyncio.run(main_module.backend.get_account_context(main_module.shopping_context(main_module.host.sessions.require(headers[SESSION_HEADER]))))
    assert account == {
        "signed_in": True,
        "customer_email": CUSTOMER_EMAIL,
        "cart_scope": "verified_shopware_customer",
        "order_history_available": True,
    }
    prompt = build_dynamic_context(
        preferences=None, memory_facts=[], cart=None, page=None, account=account,
    )
    assert CUSTOMER_EMAIL in prompt
    assert CUSTOMER_TOKEN not in prompt


def test_failed_cart_transfer_keeps_the_guest_session(client, monkeypatch):
    _bind_replay(monkeypatch)
    linking = ShopwareIdentityLinking(
        main_module.backend.client, main_module.backend.store_api,
        public_url="http://localhost:8004",
    )
    monkeypatch.setattr(main_module, "identity", linking)
    headers = start(client)
    assert client.post(
        "/api/cart/add", json={"product_id": VARIANT_S, "quantity": 1}, headers=headers
    ).status_code == 200

    async def failed_transfer(_token, _items):
        raise StoreApiError("POST", "/store-api/checkout/cart/line-item", 400, "refused")

    monkeypatch.setattr(main_module.backend.store_api, "add_cart_items", failed_transfer)
    login = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD}, headers=headers,
    )
    assert login.status_code == 502
    assert linking.identity(headers[SESSION_HEADER]) is None
    assert client.get("/api/cart", headers=headers).json()["item_count"] == 1


def test_expired_shopware_token_ends_the_bound_host_session(client, monkeypatch):
    _bind_replay(monkeypatch)
    linking = ShopwareIdentityLinking(
        main_module.backend.client, main_module.backend.store_api,
        public_url="http://localhost:8004",
    )
    monkeypatch.setattr(main_module, "identity", linking)
    monkeypatch.setattr(main_module.backend, "_order_identity_provider", linking.order_identity)
    headers = start(client)
    login = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD}, headers=headers,
    )
    assert login.status_code == 200
    linked = linking.identity(headers[SESSION_HEADER])
    assert linked is not None
    linked.customer_context_token = "invalid-token"
    assert client.get("/api/memory", headers=headers).status_code == 401
    assert main_module.host.sessions.read_state(headers[SESSION_HEADER]) is None
    assert linking.identity(headers[SESSION_HEADER]) is None


def test_reset_preserves_the_verified_identity_on_the_new_session(client, monkeypatch):
    _bind_replay(monkeypatch)
    linking = ShopwareIdentityLinking(
        main_module.backend.client, main_module.backend.store_api,
        public_url="http://localhost:8004",
    )
    monkeypatch.setattr(main_module, "identity", linking)
    headers = start(client)
    assert client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD}, headers=headers,
    ).status_code == 200
    reset = client.post("/api/reset", json={}, headers=headers)
    assert reset.status_code == 200
    new_headers = {SESSION_HEADER: reset.json()["session_id"]}
    assert client.get("/api/auth/status", headers=headers).status_code == 401
    assert client.get("/api/auth/status", headers=new_headers).json()["signed_in"] is True
    assert main_module.host.sessions.require(new_headers[SESSION_HEADER]).user_id == "shopware:customer-1"


def test_identity_linking_runs_the_signed_pkce_flow_and_adopts_the_customer_cart(
    client, monkeypatch
):
    signer = RequestSigner.generate()
    shop = _bind_replay(monkeypatch, signer=signer)
    linking = ShopwareIdentityLinking(
        main_module.backend.client,
        main_module.backend.store_api,
        public_url="http://localhost:8004",
        http=httpx.AsyncClient(transport=httpx.MockTransport(shop.handle)),
    )
    linking.client_id = "https://platform.example/agent-profile.json"
    monkeypatch.setattr(main_module, "identity", linking)
    monkeypatch.setattr(main_module.backend, "_token_provider", linking.bearer)
    monkeypatch.setattr(main_module.backend, "_order_identity_provider", linking.order_identity)

    def customer_token(session_id: str) -> str | None:
        linked = linking.identity(session_id)
        return linked.customer_context_token if linked else None

    monkeypatch.setattr(main_module.backend, "_customer_token_provider", customer_token)
    headers = start(client)
    assert client.get("/api/auth/shopware/start", headers=headers).json()["mode"] == "credentials"

    bad = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": "wrong"},
        headers=headers,
    )
    assert bad.status_code == 401

    ok = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["signed_in"] is True
    assert client.get("/api/auth/status", headers=headers).json()["signed_in"] is True
    # The customer's context token stays server-side while their cart is loaded.
    assert client.get("/api/cart", headers=headers).json()["cart_id"] is None
    assert main_module.backend.cart_id_for(headers[SESSION_HEADER]) == CUSTOMER_TOKEN
    # UCP OAuth is separate from the customer's Store API cart credential.
    assert linking.identity(headers[SESSION_HEADER]).access_token == "replay-access-token"
    assert not any(r.url.path == "/ucp/mcp" and b"get_cart" in r.content for r in shop.requests)
    linking.drop_bearer(headers[SESSION_HEADER])
    assert client.get("/api/auth/status", headers=headers).json()["signed_in"] is True
    authorize = next(r for r in shop.requests if r.url.path == "/ucp/v1/oauth/authorize")
    assert authorize.headers["sw-context-token"] == CUSTOMER_TOKEN
    assert authorize.headers["signature-input"].startswith("sig=(")
    assert "code_challenge_method=S256" in str(authorize.url)

    signed_out = client.post("/api/auth/signout", headers=headers).json()
    assert signed_out["signed_in"] is False
    assert signed_out["session_id"] != headers[SESSION_HEADER]
    assert client.get("/api/auth/status", headers=headers).status_code == 401
    guest = main_module.host.sessions.require(signed_out["session_id"])
    assert guest.user_id.startswith("guest:")


def test_optional_ucp_oauth_failure_does_not_reject_store_api_login(client, monkeypatch):
    signer = RequestSigner.generate()
    _bind_replay(monkeypatch, signer=signer)
    linking = ShopwareIdentityLinking(
        main_module.backend.client, main_module.backend.store_api,
        public_url="http://localhost:8004",
    )
    linking.client_id = "https://platform.example/agent-profile.json"

    async def oauth_failed(_token, _state, _challenge):
        raise IdentityError("OAuth unavailable")

    monkeypatch.setattr(linking, "_authorize", oauth_failed)
    monkeypatch.setattr(main_module, "identity", linking)
    headers = start(client)
    login = client.post(
        "/api/auth/shopware/login",
        json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD}, headers=headers,
    )
    assert login.status_code == 200
    assert login.json()["scope"] == "store-api"
    assert client.get("/api/auth/status", headers=headers).json()["signed_in"] is True


def test_mail_tool_send_appears_in_the_outbox(client, monkeypatch, tmp_path):
    from storefront.api import mail_tool

    monkeypatch.setattr(mail_tool, "OUTBOX_PATH", tmp_path / "mail.jsonl")
    headers = start(client)
    record = main_module.host.sessions.require(headers[SESSION_HEADER])
    executor = main_module._shopping_executor(record)

    outcome = asyncio.run(
        executor.execute(
            "send_email",
            {"to": "jane@example.com", "subject": "Your shortlist", "body": "Two picks inside."},
        )
    )
    assert not outcome.is_error and "message id" in outcome.result_text

    body = client.get("/api/mail", headers=headers).json()
    assert body["enabled"] is True and len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["to"] == "jane@example.com"
    assert message["from"] == mail_tool.SENDER
    assert message["status"] == "sent" and message["message_id"].endswith("@storefront.example>")


def test_mail_tool_rejects_a_malformed_address(client, monkeypatch, tmp_path):
    from storefront.api import mail_tool

    monkeypatch.setattr(mail_tool, "OUTBOX_PATH", tmp_path / "mail.jsonl")
    headers = start(client)
    record = main_module.host.sessions.require(headers[SESSION_HEADER])
    executor = main_module._shopping_executor(record)

    outcome = asyncio.run(
        executor.execute("send_email", {"to": "not-an-address", "subject": "x", "body": "y"})
    )
    assert outcome.is_error
    assert client.get("/api/mail", headers=headers).json()["messages"] == []
