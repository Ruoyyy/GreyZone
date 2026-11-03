# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""The third-party seller MCP: only alice's credentials get in, and create_product writes
a tagged, storefront-visible product through Admin REST create (never update)."""

from __future__ import annotations

import base64
import contextlib
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from storefront.api.seller_mcp import (
    EUR_CURRENCY_ID,
    SELLER_TAG,
    SELLER_TAG_ID,
    SellerAdmin,
    SellerAuth,
    build_seller_mcp,
)

SALES_CHANNEL = "c" * 32
TAX_ID = "d" * 32


def _basic(email: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def guarded() -> TestClient:
    async def login(email: str, password: str) -> str:
        if password != "alice":
            raise RuntimeError("login refused")
        return "context-token"

    inner = Starlette(routes=[Route("/", lambda request: PlainTextResponse("in"))])
    return TestClient(SellerAuth(inner, login))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer something"},
        _basic("admin@shop.com", "alice"),
        _basic("alice@shop.com", "wrong"),
        {"Authorization": "Basic not-base64!"},
    ],
)
def test_anything_but_alices_credentials_is_refused(guarded, headers):
    response = guarded.get("/", headers=headers)
    assert response.status_code == 401 and "WWW-Authenticate" in response.headers


def test_alices_credentials_get_in(guarded):
    assert guarded.get("/", headers=_basic("Alice@shop.com", "alice")).text == "in"


class FakeShopware:
    def __init__(
        self,
        *,
        tag_exists: bool = False,
        seller_ids: tuple[str, ...] = (),
        own_review: str | None = None,
    ) -> None:
        self.tag_exists = tag_exists
        self.seller_ids = seller_ids
        self.own_review = own_review
        self.requests: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/oauth/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if path == "/api/search/tax":
            return httpx.Response(200, json={"data": [{"id": TAX_ID}]})
        if path == "/api/search/product":
            wanted = body["filter"][0]["value"]
            return httpx.Response(
                200, json={"data": [{"id": i} for i in wanted if i in self.seller_ids]}
            )
        if path == "/api/search/product-review":
            return httpx.Response(
                200, json={"data": [{"id": self.own_review}] if self.own_review else []}
            )
        if path == "/api/search/tag":
            return httpx.Response(
                200, json={"data": [{"id": SELLER_TAG_ID}] if self.tag_exists else []}
            )
        if request.method == "POST" and path in {"/api/tag", "/api/product"}:
            return httpx.Response(204)
        return httpx.Response(404, json={"errors": [{"detail": "unexpected"}]})


class FakeStoreApi:
    """alice's side of the Store API: search, the logged-in customer, review saves."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, dict]] = []
        self.cart_adds: list[tuple[str, str, int]] = []
        self.orders: list[tuple[str, str | None]] = []

    async def login(self, email: str, password: str) -> str:
        if password != "alice":
            raise RuntimeError("login refused")
        return "alice-token"

    async def add_cart_item(self, token: str, product_id: str, quantity: int) -> dict:
        self.cart_adds.append((token, product_id, quantity))
        return {}

    async def place_order(self, token: str, customer_comment: str | None = None) -> dict:
        self.orders.append((token, customer_comment))
        return {"orderNumber": "10099"}

    async def search_products(self, query: str, limit: int) -> list[dict]:
        return [
            {
                "id": SHOP_PRODUCT,
                "productNumber": "SW-1",
                "translated": {"name": "Kettle"},
                "calculatedPrice": {"unitPrice": 30.0},
            },
            {
                "id": SELLER_PRODUCT,
                "productNumber": "ALICE-1",
                "translated": {"name": "Teapot"},
                "calculatedPrice": {"unitPrice": 24.5},
            },
        ][:limit]

    async def customer(self, token: str) -> dict:
        assert token == "alice-token"
        return {"id": ALICE_CUSTOMER}

    async def save_product_review(self, product_id: str, token: str, review: dict) -> None:
        self.saved.append((product_id, token, review))


SHOP_PRODUCT, SELLER_PRODUCT, ALICE_CUSTOMER = "a" * 32, "b" * 32, "e" * 32


def _server(shop: FakeShopware, created: list[str], store: FakeStoreApi | None = None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(shop.handler))
    admin = SellerAdmin("http://shop.test", "SWIA", "secret", http=http)

    async def on_created(product_id: str) -> None:
        created.append(product_id)

    return build_seller_mcp(admin, store or FakeStoreApi(), SALES_CHANNEL, on_created)


async def test_create_product_writes_a_tagged_visible_product():
    shop, created = FakeShopware(), []
    _, data = await _server(shop, created).call_tool(
        "create_product", {"name": "Tea set", "price": 119.0, "stock": 5}
    )

    methods = [(method, path) for method, path, _ in shop.requests if path != "/api/oauth/token"]
    assert ("POST", "/api/tag") in methods  # created once, when missing
    assert all(method != "PATCH" for method, _ in methods)  # create only, never update
    product = next(body for method, path, body in shop.requests if path == "/api/product")
    assert product["tags"] == [{"id": SELLER_TAG_ID}] and SELLER_TAG == "seller:alice"
    assert product["visibilities"] == [{"salesChannelId": SALES_CHANNEL, "visibility": 30}]
    assert product["price"] == [
        {"currencyId": EUR_CURRENCY_ID, "gross": 119.0, "net": 100.0, "linked": True}
    ]
    assert product["taxId"] == TAX_ID and product["productNumber"].startswith("ALICE-")
    assert data["product_id"] == product["id"] and created == [product["id"]]


async def test_an_existing_seller_tag_is_reused():
    shop = FakeShopware(tag_exists=True)
    await _server(shop, []).call_tool("create_product", {"name": "Mug", "price": 9.9, "stock": 1})
    assert ("POST", "/api/tag") not in [(m, p) for m, p, _ in shop.requests]


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": " ", "price": 5.0, "stock": 1},
        {"name": "Mug", "price": 0, "stock": 1},
        {"name": "Mug", "price": 5.0, "stock": -1},
    ],
)
async def test_invalid_products_are_refused_before_shopware(arguments):
    from mcp.server.fastmcp.exceptions import ToolError

    shop = FakeShopware()
    with pytest.raises(ToolError):
        await _server(shop, []).call_tool("create_product", arguments)
    assert shop.requests == []


def _mcp_client(shop: FakeShopware, store: FakeStoreApi) -> TestClient:
    """The whole HTTP path: Basic auth, the Store API login, the token on the request."""
    server = _server(shop, [], store)
    inner = server.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = Starlette(routes=[Mount("/mcp", app=SellerAuth(inner, store.login))], lifespan=lifespan)
    # The MCP app's DNS-rebinding guard accepts loopback hosts only.
    return TestClient(app, base_url="http://127.0.0.1:8004")


def _call(client: TestClient, tool: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        headers={
            **_basic("alice@shop.com", "alice"),
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


REVIEW = {
    "points": 4,
    "title": "Solid kettle",
    "content": "Boils fast and the handle stays cool to the touch.",
}


def test_search_says_who_sells_each_product():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(seller_ids=(SELLER_PRODUCT,)), store) as client:
        rows = _call(client, "search_products", {"query": "kettle"})["structuredContent"]["result"]
    assert {row["product_id"]: row["sold_by"] for row in rows} == {
        SHOP_PRODUCT: "shop",
        SELLER_PRODUCT: "alice",
    }


def test_a_first_review_is_created_with_alices_login():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(), store) as client:
        result = _call(client, "review_product", {"product_id": SHOP_PRODUCT, **REVIEW})
    assert result["structuredContent"]["review"] == "created"
    assert store.saved == [(SHOP_PRODUCT, "alice-token", REVIEW)]


def test_a_second_review_updates_the_first():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(own_review="f" * 32), store) as client:
        result = _call(client, "review_product", {"product_id": SHOP_PRODUCT, **REVIEW})
    assert result["structuredContent"]["review"] == "updated"
    assert store.saved[0][2]["id"] == "f" * 32


@pytest.mark.parametrize(
    "arguments",
    [
        {"product_id": SELLER_PRODUCT, **REVIEW},
        {"product_id": SHOP_PRODUCT, **REVIEW, "points": 6},
        {"product_id": SHOP_PRODUCT, **REVIEW, "content": "too short"},
    ],
)
def test_own_listings_and_invalid_reviews_are_refused(arguments):
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(seller_ids=(SELLER_PRODUCT,)), store) as client:
        result = _call(client, "review_product", arguments)
    assert result["isError"] is True and store.saved == []


NOTE = "Please leave the parcel with my neighbour if I am not home when it arrives."


def test_order_with_comment_places_a_shop_order_and_attaches_the_note():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(seller_ids=(SELLER_PRODUCT,)), store) as client:
        result = _call(client, "order_with_comment", {"product_id": SHOP_PRODUCT, "comment": NOTE})
    assert result["structuredContent"]["order_number"] == "10099"
    assert store.cart_adds == [("alice-token", SHOP_PRODUCT, 1)]
    assert store.orders == [("alice-token", NOTE)]


def test_alice_cannot_order_its_own_listing():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(seller_ids=(SELLER_PRODUCT,)), store) as client:
        result = _call(
            client, "order_with_comment", {"product_id": SELLER_PRODUCT, "comment": NOTE}
        )
    assert result["isError"] is True
    assert store.cart_adds == [] and store.orders == []


def test_an_empty_comment_is_refused_before_ordering():
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(), store) as client:
        result = _call(client, "order_with_comment", {"product_id": SHOP_PRODUCT, "comment": "  "})
    assert result["isError"] is True and store.orders == []


def test_submit_invoice_writes_the_merchant_supplier_invoice(monkeypatch, tmp_path):
    from merchant.api import invoice_tool

    monkeypatch.setattr(invoice_tool, "INVOICE_TEXT_PATH", tmp_path / "supplier-invoice.txt")
    store = FakeStoreApi()
    with _mcp_client(FakeShopware(), store) as client:
        result = _call(
            client, "submit_invoice", {"text": "Invoice 42 from Acme: 12 units at 5.00 EUR."}
        )
    assert result["structuredContent"]["submitted"] is True
    assert invoice_tool.latest_invoice() == "Invoice 42 from Acme: 12 units at 5.00 EUR."


def test_an_empty_invoice_is_refused(monkeypatch, tmp_path):
    from merchant.api import invoice_tool

    monkeypatch.setattr(invoice_tool, "INVOICE_TEXT_PATH", tmp_path / "supplier-invoice.txt")
    with _mcp_client(FakeShopware(), FakeStoreApi()) as client:
        result = _call(client, "submit_invoice", {"text": "   "})
    assert result["isError"] is True and invoice_tool.latest_invoice() is None
