# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Replay of Shopware-shaped UCP (REST **and** MCP), Store API and OAuth responses over
``httpx.MockTransport``. No test in this package touches the network.

* Catalog documents match the UCP shapes Shopware's ``SwagAgenticCommerce`` mapper emits
  (hex UUIDs, cart id = ``sw-context-token``). Cart is stateful so the lifecycle tests
  create / patch / get without a combinatorial fixture dump.
* ``/ucp/mcp`` speaks Streamable HTTP like the shop: ``initialize`` hands out an
  ``Mcp-Session-Id``, every later call needs it (``400 A valid session id is REQUIRED``
  otherwise), ``tools/list`` is the recorded live list (``fixtures/ucp_mcp_tools_list.json``)
  and ``tools/call`` takes the recorded argument shapes (documents as JSON strings,
  ``dryRun``) and answers a text block carrying the JSON document.
* With ``public_key`` set the replay verifies RFC 9421 / RFC 9530 on every UCP request
  the way the shop does under ``signaturePolicy=strict`` (401 otherwise).
* ``/store-api/_mcp`` is the Store API MCP server with ``SwagCommerceAgentTools``
  installed: the handshake needs the ``sw-access-key``, ``tools/list`` and the three
  ``shopping-*`` ``tools/call`` answers are the recorded live ones
  (``fixtures/store_api_mcp_agent_tools.json``, ids remapped to this module's constants);
  the ``sw-context-token`` of every call is kept in ``agent_tool_tokens`` so tests can
  prove the cart token travels with the call. ``agent_tools_available=False`` hides the
  plugin (``tools/list`` without the ``shopping-*`` tools, calls answer "Tool not found").
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import ec

from shopware_common import http_signing

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

PRODUCT_ID = "11111111111111111111111111111111"
VARIANT_S = "22222222222222222222222222222222"
VARIANT_M = "33333333333333333333333333333333"
VARIANT_L = "44444444444444444444444444444444"
OIL_ID = "55555555555555555555555555555555"
MAIN_ID = "66666666666666666666666666666666"
CART_ID = "SWSCVGF5ZUJQBWJ0QMP1OXPZNQ"
GONE_CART_ID = "SWSCGONE00000000000000000000"
CUSTOMER_TOKEN = "SWSCCUSTOMER0000000000000000"
ORDER_ID = "88888888888888888888888888888888"
ORDER_NUMBER = "10042"
RETURNS_CATEGORY_ID = "99999999999999999999999999999901"
SHIPPING_CATEGORY_ID = "99999999999999999999999999999902"
CONTACT_CATEGORY_ID = "99999999999999999999999999999903"
SEARCH_QUERY = "shirt"
MCP_SESSION_ID = "replay-mcp-session"
STORE_API_MCP_SESSION_ID = "replay-store-api-mcp-session"
AGENT_TOOLS_FIXTURE = FIXTURES / "store_api_mcp_agent_tools.json"
AGENT_TOOL_PREFIX = "shopping-"
OAUTH_CODE = "replay-authorization-code"
ACCESS_TOKEN = "replay-access-token"
REFRESH_TOKEN = "replay-refresh-token"
CUSTOMER_EMAIL = "kunde@example.com"
CUSTOMER_PASSWORD = "shopware"

_PRICE = {"amount": 2999, "currency": "EUR"}


def _variant(vid: str, size: str, available: bool) -> dict[str, Any]:
    return {
        "id": vid,
        "title": size,
        "price": dict(_PRICE),
        "availability": {"available": available},
        "options": [{"name": "Size", "label": size}],
        "image_url": "https://cdn.example/shirt.jpg",
    }


SHIRT: dict[str, Any] = {
    "id": PRODUCT_ID,
    "title": "Claude Commerce T-Shirt",
    "name": "Claude Commerce T-Shirt",
    "description": {"html": "Organic cotton T-shirt in three sizes."},
    "price": dict(_PRICE),
    "currency": "EUR",
    "image_url": "https://cdn.example/shirt.jpg",
    "availability": {"available": True},
    "tags": ["Apparel"],
    # Shopware emits the parent itself as the first variants[] row; it is not a child SKU.
    "variants": [
        {**_variant(PRODUCT_ID, "Claude Commerce T-Shirt", True), "options": []},
        _variant(VARIANT_S, "S", True),
        _variant(VARIANT_M, "M", True),
        _variant(VARIANT_L, "L", False),
    ],
}

OIL: dict[str, Any] = {
    "id": OIL_ID,
    "title": "Extra Virgin Olive Oil 500 ml",
    "name": "Extra Virgin Olive Oil 500 ml",
    "description": {"html": "Cold-pressed olive oil. Grundpreis is shown on the product."},
    "price": {"amount": 1290, "currency": "EUR"},
    "currency": "EUR",
    "image_url": "https://cdn.example/oil.jpg",
    "availability": {"available": True},
    "tags": ["Grocery"],
    "variants": [],
}


def _ucp_child_doc(vid: str, size: str) -> dict[str, Any]:
    """A thin UCP document for one child, as the live shop answers for some variant ids
    (and for ``GET /ucp/v1/catalog/product/{family}``, see the REST route below)."""
    title = f"Claude Commerce T-Shirt — {size}"
    return {
        "id": vid,
        "title": title,
        "description": {"plain": title},
        "price_range": {
            "min": {"amount": 2999, "currency": "EUR"},
            "max": {"amount": 2999, "currency": "EUR"},
        },
        "variants": [{"id": vid, "title": title, "price": {"amount": 2999, "currency": "EUR"}}],
    }


SHIRT_REST_CHILD_S = _ucp_child_doc(VARIANT_S, "S")
SHIRT_CHILD_M = _ucp_child_doc(VARIANT_M, "M")

# Live behaviour differs per child: some ids answer the family document (S and L here),
# others the child itself (M here). The backend must resolve both to the family.
CATALOG = {
    PRODUCT_ID: SHIRT,
    OIL_ID: OIL,
    VARIANT_S: SHIRT,
    VARIANT_M: SHIRT_CHILD_M,
    VARIANT_L: SHIRT,
}


def _store_child(vid: str, size: str, stock: int) -> dict[str, Any]:
    return {
        "id": vid,
        "parentId": PRODUCT_ID,
        "name": f"Claude Commerce T-Shirt — {size}",
        "translated": {"name": f"Claude Commerce T-Shirt — {size}"},
        "available": stock > 0,
        "availableStock": stock,
        "stock": stock,
        "calculatedPrice": {"unitPrice": 29.99},
        "options": [
            {
                "name": size,
                "translated": {"name": size},
                "group": {"name": "Size", "translated": {"name": "Size"}},
            }
        ],
        "cover": {"media": {"url": "https://cdn.example/shirt.jpg"}},
    }


STORE_CHILDREN = [
    _store_child(VARIANT_S, "S", 20),
    _store_child(VARIANT_M, "M", 12),
    _store_child(VARIANT_L, "L", 0),
]

STORE_PRODUCTS: dict[str, dict[str, Any]] = {
    PRODUCT_ID: {
        "id": PRODUCT_ID,
        "name": "Claude Commerce T-Shirt",
        "translated": {"name": "Claude Commerce T-Shirt", "description": "Organic cotton T-shirt."},
        "available": True,
        "availableStock": 32,
        "calculatedPrice": {"unitPrice": 29.99},
        "cover": {"media": {"url": "https://cdn.example/shirt.jpg"}},
        "deliveryTime": {
            "name": "1–3 Werktage",
            "translated": {"name": "1–3 Werktage"},
            "min": 1,
            "max": 3,
        },
        # Product detail resolves to the family with children[] empty (live behaviour);
        # children come from the parentId listing.
        "children": [],
        "manufacturer": {"name": "Commerce Agents", "translated": {"name": "Commerce Agents"}},
        "categories": [{"name": "Apparel", "translated": {"name": "Apparel"}}],
    },
    OIL_ID: {
        "id": OIL_ID,
        "name": "Extra Virgin Olive Oil 500 ml",
        "translated": {"name": "Extra Virgin Olive Oil 500 ml", "description": "Cold-pressed."},
        "available": True,
        "availableStock": 40,
        "calculatedPrice": {
            "unitPrice": 12.90,
            "referencePrice": {"price": 25.80, "unitName": "1 l"},
        },
        "purchaseUnit": 0.5,
        "referenceUnit": 1.0,
        "cover": {"media": {"url": "https://cdn.example/oil.jpg"}},
        "deliveryTime": {
            "name": "2–4 Werktage",
            "translated": {"name": "2–4 Werktage"},
            "min": 2,
            "max": 4,
        },
        "children": [],
        "unit": {"shortCode": "l", "name": "Liter"},
    },
    MAIN_ID: {
        "id": MAIN_ID,
        "name": "Main product",
        "translated": {"name": "Main product", "description": "Shopware demo main product."},
        "available": True,
        "availableStock": 10,
        "calculatedPrice": {"unitPrice": 495.95},
        "cover": {"media": {"url": "https://cdn.example/main.jpg"}},
        "children": [],
        "categories": [
            {
                "name": "Free time & electronics",
                "translated": {"name": "Free time & electronics"},
            }
        ],
    },
}
for _child in STORE_CHILDREN:
    STORE_PRODUCTS[_child["id"]] = _child

SHIPPING_METHODS = [
    {
        "id": "aaaa0000000000000000000000000001",
        "name": "Standard",
        "translated": {"name": "Standard"},
        "deliveryTime": {
            "name": "2–4 Tage",
            "translated": {"name": "2–4 Tage"},
            "min": 2,
            "max": 4,
            "unit": "day",
        },
        "prices": [
            {
                "quantityStart": 1,
                "currencyPrice": [
                    {"currencyId": "b7d2554b0ce847cd82f3ac9bd1c0dfca", "gross": 4.9, "net": 4.12}
                ],
            }
        ],
    },
    {
        "id": "aaaa0000000000000000000000000002",
        "name": "Express",
        "translated": {"name": "Express"},
        "deliveryTime": {
            "name": "1–2 Tage",
            "translated": {"name": "1–2 Tage"},
            "min": 1,
            "max": 2,
            "unit": "day",
        },
        "prices": [
            {
                "quantityStart": 1,
                "currencyPrice": [
                    {"currencyId": "b7d2554b0ce847cd82f3ac9bd1c0dfca", "gross": 9.9, "net": 8.32}
                ],
            }
        ],
    },
]


def _cms_page(text: str) -> dict[str, Any]:
    return {
        "type": "page",
        "sections": [
            {
                "blocks": [
                    {
                        "type": "text",
                        "slots": [
                            {
                                "slot": "content",
                                "type": "text",
                                "config": {
                                    "content": {"source": "static", "value": f"<p>{text}</p>"},
                                    "verticalAlign": {"source": "static", "value": "flex-start"},
                                },
                                "data": {"content": f"<p>{text}</p>"},
                            }
                        ],
                    }
                ]
            }
        ],
    }


STORE_CATEGORIES: dict[str, dict[str, Any]] = {
    RETURNS_CATEGORY_ID: {
        "id": RETURNS_CATEGORY_ID,
        "name": "Widerrufsbelehrung",
        "translated": {"name": "Widerrufsbelehrung"},
        "cmsPage": _cms_page(
            "Verbraucher haben ein Widerrufsrecht von 14 Tagen ab Erhalt der Ware. "
            "Rücksendungen bitte an die im Shop genannte Adresse."
        ),
    },
    SHIPPING_CATEGORY_ID: {
        "id": SHIPPING_CATEGORY_ID,
        "name": "Versand & Lieferzeit",
        "translated": {"name": "Versand & Lieferzeit"},
        "cmsPage": _cms_page(
            "Versand innerhalb Deutschlands 4,90 €, Express 9,90 €. Lieferzeit 2–4 Tage."
        ),
    },
    CONTACT_CATEGORY_ID: {
        "id": CONTACT_CATEGORY_ID,
        "name": "Kontakt",
        "translated": {"name": "Kontakt"},
        "cmsPage": _cms_page("Sie erreichen uns unter service@example.com."),
    },
}

FOOTER_NAVIGATION = [
    {
        "id": "99999999999999999999999999999900",
        "name": "Service",
        "translated": {"name": "Service"},
        "children": [
            {
                "id": RETURNS_CATEGORY_ID,
                "name": "Widerrufsbelehrung",
                "translated": {"name": "Widerrufsbelehrung"},
            },
            {
                "id": SHIPPING_CATEGORY_ID,
                "name": "Versand & Lieferzeit",
                "translated": {"name": "Versand & Lieferzeit"},
            },
        ],
    }
]
SERVICE_NAVIGATION = [
    {"id": CONTACT_CATEGORY_ID, "name": "Kontakt", "translated": {"name": "Kontakt"}}
]

ORDERS_BY_TOKEN: dict[str, list[dict[str, Any]]] = {
    CART_ID: [
        {
            "id": ORDER_ID,
            "orderNumber": ORDER_NUMBER,
            "orderDateTime": "2026-08-30T09:12:00.000+00:00",
            "amountTotal": 64.88,
            "currency": {"isoCode": "EUR"},
            "stateMachineState": {"technicalName": "in_progress"},
            "transactions": [{"stateMachineState": {"technicalName": "paid"}}],
            "deliveries": [
                {
                    "stateMachineState": {"technicalName": "shipped"},
                    "trackingCodes": ["1Z999"],
                    "shippingDateLatest": "2026-09-03T00:00:00.000+00:00",
                    "shippingMethod": {"trackingUrl": "https://track.example/%s"},
                }
            ],
            "lineItems": [
                {
                    "type": "product",
                    "productId": VARIANT_S,
                    "referencedId": VARIANT_S,
                    "label": "Claude Commerce T-Shirt — S",
                    "quantity": 2,
                    "unitPrice": 29.99,
                    "payload": {
                        "parentId": PRODUCT_ID,
                        "options": [{"group": "Size", "option": "S"}],
                    },
                },
                {"type": "promotion", "label": "Sommer", "quantity": 1, "unitPrice": -5.0},
            ],
        }
    ],
    CUSTOMER_TOKEN: [],
}


def _json_body(request: httpx.Request) -> dict[str, Any]:
    if not request.content:
        return {}
    try:
        data = json.loads(request.content)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _ucp_error(status: int, code: str, content: str) -> httpx.Response:
    return httpx.Response(
        status, json={"messages": [{"type": "error", "code": code, "content": content}]}
    )


class ShopwareReplay:
    """In-memory Shopware: UCP REST + MCP, Store API, OAuth AS."""

    def __init__(
        self,
        public_key: ec.EllipticCurvePublicKey | None = None,
        *,
        agent_tools_available: bool = True,
    ) -> None:
        self.public_key = public_key
        self.carts: dict[str, dict[str, Any]] = {}
        self.customer_carts: dict[str, list[dict[str, Any]]] = {CUSTOMER_TOKEN: []}
        self.customer_orders = deepcopy(ORDERS_BY_TOKEN)
        self.checkouts: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.mcp_sessions: set[str] = set()
        self.mcp_calls: list[dict[str, Any]] = []
        self.oauth_codes: dict[str, dict[str, Any]] = {}
        self._cart_seq = 0
        recorded = json.loads((FIXTURES / "ucp_mcp_tools_list.json").read_text(encoding="utf-8"))
        self._tools = {"tools": recorded["tools"]}
        # SwagCommerceAgentTools on the Store API MCP server.
        self.agent_tools_available = agent_tools_available
        self.agent_tool_calls: list[dict[str, Any]] = []
        self.agent_tool_tokens: list[str | None] = []
        self.agent_tool_failures: set[str] = set()  # tool names that answer isError
        self.store_api_mcp_sessions: set[str] = set()
        self._agent_tools = json.loads(AGENT_TOOLS_FIXTURE.read_text(encoding="utf-8"))
        # Live WASM/Pages failures: UCP or Store API search answering HTML instead of JSON.
        self.ucp_search_html = False
        self.store_search_html = False

    # ------------------------------------------------------------------ dispatch

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parsed = urlparse(str(request.url))
        path = parsed.path
        method = request.method.upper()
        body = _json_body(request)

        if method == "GET" and path.endswith("/.well-known/ucp"):
            return httpx.Response(200, json=_discovery())
        if path.startswith("/ucp/"):
            if (refused := self._check_signature(request)) is not None:
                return refused
            if path.startswith("/ucp/v1/oauth/"):
                return self._oauth(method, path, request, body)
            if path.startswith("/ucp/v1/"):
                if method != "GET" and not request.headers.get("idempotency-key"):
                    return _ucp_error(
                        400, "invalid_request", "$.headers.idempotency-key is required"
                    )
                return self._ucp_rest(method, path[len("/ucp/v1") :], body)
            if path == "/ucp/mcp":
                return self._ucp_mcp(method, request, body)
        if path == "/store-api/_mcp":
            return self._store_api_mcp(method, request, body)
        if path.startswith("/store-api/"):
            return self._store_api(method, path, parsed.query, request, body)
        return httpx.Response(404, json={"detail": f"unmocked {method} {path}"})

    def _check_signature(self, request: httpx.Request) -> httpx.Response | None:
        if self.public_key is None:
            return None
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            http_signing.verify(
                self.public_key, request.method, str(request.url), request.content, headers
            )
        except ValueError as error:
            return _ucp_error(401, "signature_invalid", str(error))
        return None

    # ------------------------------------------------------------------ UCP REST

    def _ucp_rest(self, method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        if method == "POST" and path == "/catalog/search":
            if self.ucp_search_html:
                return httpx.Response(
                    400, text="<html>Oops</html>", headers={"content-type": "text/html"}
                )
            query = str(body.get("query") or "").lower()
            products = []
            if not query or query in {"*", "a"} or "shirt" in query or "claude" in query:
                products.append(deepcopy(SHIRT))
            if "oil" in query or "olive" in query or query in {"", "*", "a"}:
                products.append(deepcopy(OIL))
            if "zzzz" in query:
                products = []
            return httpx.Response(200, json={"products": products})

        if method == "GET" and path.startswith("/catalog/product/"):
            wanted = path.rsplit("/", 1)[-1]
            if wanted == PRODUCT_ID:
                # Live 6.7.13 quirk: the REST product route resolves a family id to its
                # first child; catalog-lookup (and the MCP tool) answer the family itself.
                return httpx.Response(200, json={"product": deepcopy(SHIRT_REST_CHILD_S)})
            record = CATALOG.get(wanted)
            if record is None:
                return _ucp_error(404, "product_not_found", "Product not found")
            return httpx.Response(200, json={"product": deepcopy(record)})

        if method == "POST" and path == "/catalog/lookup":
            found = [deepcopy(CATALOG[i]) for i in body.get("ids") or [] if i in CATALOG]
            return httpx.Response(200, json={"products": found})

        if method == "POST" and path == "/carts":
            return httpx.Response(201, json=self._create_cart(body.get("line_items") or []))

        if path.startswith("/carts/"):
            rest = path[len("/carts/") :]
            if rest.endswith("/cancel"):
                cart_id = rest[: -len("/cancel")]
                self.carts.pop(cart_id, None)
                return httpx.Response(200, json={"id": cart_id, "line_items": []})
            cart_id = rest
            if cart_id == GONE_CART_ID or cart_id not in self.carts:
                return _ucp_error(404, "cart_not_found", "The requested cart is gone")
            if method == "GET":
                return httpx.Response(200, json=deepcopy(self.carts[cart_id]))
            if method in {"PATCH", "PUT"}:
                self.carts[cart_id] = self._cart_document(cart_id, body.get("line_items") or [])
                return httpx.Response(200, json=deepcopy(self.carts[cart_id]))

        if method == "GET" and path.startswith("/orders/"):
            return _ucp_error(404, "order_not_found", "Order not found")

        if path == "/checkout-sessions" and method == "POST":
            # The id is the cart it was converted from: UCP keeps the session identity
            # across the cart → checkout transition.
            checkout_id = str(body.get("cart_id") or CART_ID)
            self.checkouts[checkout_id] = body
            return httpx.Response(
                201, json={"id": checkout_id, "status": "incomplete", "currency": "EUR"}
            )
        if path.startswith("/checkout-sessions/"):
            rest = path[len("/checkout-sessions/") :]
            if rest.endswith("/complete"):
                return self._complete_checkout(rest[: -len("/complete")], body)
            if method == "GET" and rest in self.checkouts:
                return httpx.Response(
                    200, json={"id": rest, "status": "incomplete", "currency": "EUR"}
                )

        return httpx.Response(404, json={"detail": f"unmocked UCP {method} {path}"})

    # ------------------------------------------------------------------ UCP MCP

    def _ucp_mcp(self, method: str, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if method == "DELETE":
            self.mcp_sessions.discard(request.headers.get("mcp-session-id", ""))
            return httpx.Response(200)
        if method != "POST":
            return httpx.Response(405)
        rpc_method = body.get("method")
        rpc_id = body.get("id")
        if rpc_method == "initialize":
            if not body.get("params", {}).get("clientInfo", {}).get("version"):
                return httpx.Response(
                    400,
                    json={
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32600, "message": "A valid session id is REQUIRED"},
                    },
                )
            self.mcp_sessions.add(MCP_SESSION_ID)
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": MCP_SESSION_ID},
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "shopware-store-api-mcp", "version": "6.7.13.0"},
                    },
                },
            )
        session_id = request.headers.get("mcp-session-id")
        if not session_id or session_id not in self.mcp_sessions:
            return httpx.Response(
                400,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32600, "message": "A valid session id is REQUIRED"},
                },
            )
        if rpc_method == "notifications/initialized":
            return httpx.Response(202)
        if rpc_method == "tools/list":
            return self._sse({"jsonrpc": "2.0", "id": rpc_id, "result": deepcopy(self._tools)})
        if rpc_method != "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"Method not found: {rpc_method}"},
                },
            )
        params = body.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        self.mcp_calls.append({"name": name, "arguments": arguments})
        schema = next((t for t in self._tools["tools"] if t["name"] == name), None)
        if schema is None:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32602, "message": f"Unknown tool {name}"},
                },
            )
        for key, value in arguments.items():
            expected = (schema["inputSchema"].get("properties") or {}).get(key, {}).get("type")
            if expected == "string" and not isinstance(value, str):
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": f"Invalid type for {key}. Expected 'string'.",
                        },
                    },
                )
        rest = self._mcp_to_rest(name, arguments)
        if rest is None:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32602, "message": f"Unsupported tool {name} in replay"},
                },
            )
        try:
            data = rest.json()
        except ValueError:
            data = {"detail": rest.text}
        # Live 6.7.13 shape: a success envelope around the UCP document; tool-level
        # failures (unknown cart etc.) come back as isError text blocks.
        if rest.status_code >= 400:
            result = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": True}
        else:
            envelope: dict[str, Any] = {"success": True, "data": data}
            if "dryRun" in arguments:
                envelope["dryRun"] = bool(arguments["dryRun"])
            result = {"content": [{"type": "text", "text": json.dumps(envelope)}], "isError": False}
        return self._sse({"jsonrpc": "2.0", "id": rpc_id, "result": result})

    def _mcp_to_rest(self, name: str, arguments: dict[str, Any]) -> httpx.Response | None:
        def doc(key: str = "payload") -> dict[str, Any]:
            # Plugin 1.3.0 types the tool argument `array $payload`, so it arrives as an
            # object; 1.2.x took a JSON string. `ids` is still a string, decoded separately.
            parsed = arguments.get(key)
            return parsed if isinstance(parsed, dict) else {}

        dry_run = arguments.get("dryRun", True)
        if name == "search_catalog":
            return self._ucp_rest(
                "POST",
                "/catalog/search",
                {"query": arguments.get("query", ""), "limit": arguments.get("limit", 10)},
            )
        if name == "lookup_catalog":
            try:
                ids = json.loads(arguments.get("ids") or "[]")
            except ValueError:
                ids = []
            return self._ucp_rest("POST", "/catalog/lookup", {"ids": ids})
        if name == "get_cart":
            return self._ucp_rest("GET", f"/carts/{arguments.get('id')}", {})
        if name == "create_cart":
            if dry_run:
                return httpx.Response(
                    200,
                    json={"dryRun": True, "id": None, "line_items": doc().get("line_items") or []},
                )
            return self._ucp_rest("POST", "/carts", doc())
        if name == "update_cart":
            if dry_run:
                return httpx.Response(
                    200,
                    json={
                        "dryRun": True,
                        "id": arguments.get("id"),
                        "line_items": doc().get("line_items") or [],
                    },
                )
            return self._ucp_rest("PATCH", f"/carts/{arguments.get('id')}", doc())
        if name == "cancel_cart":
            return self._ucp_rest("POST", f"/carts/{arguments.get('id')}/cancel", {})
        if name == "create_checkout":
            # ``doc()`` is the UCP document here: only ``complete_checkout`` nests it
            # under ``checkout`` inside the payload.
            return self._ucp_rest("POST", "/checkout-sessions", doc())
        if name == "complete_checkout":
            return self._ucp_rest(
                "POST",
                f"/checkout-sessions/{arguments.get('id')}/complete",
                doc(),
            )
        if name == "get_order":
            return self._ucp_rest("GET", f"/orders/{arguments.get('id')}", {})
        raise AssertionError(f"replay has no mapping for MCP tool {name!r}")

    @staticmethod
    def _sse(message: dict[str, Any]) -> httpx.Response:
        # The body is already materialized: a streaming client reads a MockTransport
        # response only until the stream ends, so a response still marked streaming
        # leaves the call hanging.
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(f"event: message\ndata: {json.dumps(message)}\n\n".encode()),
        )

    # ------------------------------------------------------------------ Store API MCP

    def _store_api_mcp(
        self, method: str, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        """``/store-api/_mcp``: the sales-channel key authenticates, the session id is
        handed out on ``initialize``; ``shopping-*`` tools answer the recorded documents."""
        if request.headers.get("sw-access-key") != "test-key":
            return httpx.Response(401, json={"errors": [{"detail": "Access key is invalid"}]})
        if method == "DELETE":
            self.store_api_mcp_sessions.discard(request.headers.get("mcp-session-id", ""))
            return httpx.Response(200)
        if method != "POST":
            return httpx.Response(405)
        rpc_method = body.get("method")
        rpc_id = body.get("id")
        if rpc_method == "initialize":
            self.store_api_mcp_sessions.add(STORE_API_MCP_SESSION_ID)
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": STORE_API_MCP_SESSION_ID},
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "protocolVersion": self._agent_tools["protocolVersion"],
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": self._agent_tools["serverInfo"],
                    },
                },
            )
        if request.headers.get("mcp-session-id") not in self.store_api_mcp_sessions:
            return httpx.Response(
                400,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32600, "message": "A valid session id is REQUIRED"},
                },
            )
        if rpc_method == "notifications/initialized":
            return httpx.Response(202)
        if rpc_method == "tools/list":
            tools = [
                tool
                for tool in self._agent_tools["tools"]
                if self.agent_tools_available or not tool["name"].startswith(AGENT_TOOL_PREFIX)
            ]
            return self._sse({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": tools}})
        if rpc_method != "tools/call":
            return self._rpc_error(rpc_id, -32601, f"Method not found: {rpc_method}")
        params = body.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        self.agent_tool_calls.append({"name": name, "arguments": arguments})
        self.agent_tool_tokens.append(request.headers.get("sw-context-token"))
        if name.startswith(AGENT_TOOL_PREFIX) and not self.agent_tools_available:
            return self._rpc_error(rpc_id, -32602, f"Tool not found: {name}")
        if name in self.agent_tool_failures:
            return self._sse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": "Error while executing tool"}],
                        "isError": True,
                    },
                }
            )
        recorded = self._recorded_agent_tool_answer(name, arguments)
        if recorded is None:
            return self._rpc_error(rpc_id, -32602, f"Tool not found: {name}")
        return self._sse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(recorded)}],
                    "isError": False,
                },
            }
        )

    def _recorded_agent_tool_answer(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        responses = self._agent_tools["responses"]
        if name == "shopping-policy-search":
            query = str(arguments.get("query") or "").lower()
            key = "shopping-policy-search:kontakt" if "kontakt" in query else name
            if "zzzz" in query:
                return {"success": True, "data": [], "_meta": {"query": query, "pagesSearched": 5}}
            return deepcopy(responses[key]["result"])
        if name == "shopping-disclosure":
            product_id = str(arguments.get("productId") or "")
            if product_id == OIL_ID:
                return deepcopy(responses["shopping-disclosure:oil"]["result"])
            if product_id in {PRODUCT_ID, VARIANT_S, VARIANT_M, VARIANT_L}:
                return deepcopy(responses["shopping-disclosure:family"]["result"])
            return deepcopy(responses["shopping-disclosure:unknown"]["result"])
        if name == "shopping-fulfillment-options":
            return deepcopy(responses["shopping-fulfillment-options"]["result"])
        if name == "shopware-store-api-context":
            return {"success": True, "data": {"salesChannelId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0"}}
        return None

    @staticmethod
    def _rpc_error(rpc_id: Any, code: int, message: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        )

    # ------------------------------------------------------------------ OAuth AS

    def _oauth(
        self, method: str, path: str, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        query = dict(
            p.split("=", 1) for p in urlparse(str(request.url)).query.split("&") if "=" in p
        )
        if path.endswith("/authorize") and method == "GET":
            if self.public_key is None or not request.headers.get("signature"):
                return _ucp_error(
                    401,
                    "oauth_error",
                    "UCP identity linking requires a signed platform profile request.",
                )
            if not query.get("client_id", "").startswith("https%3A") and not query.get(
                "client_id", ""
            ).startswith("https:"):
                return _ucp_error(
                    400,
                    "oauth_error",
                    "UCP identity linking requires an HTTPS platform profile URI as client ID.",
                )
            if request.headers.get("sw-context-token") != CUSTOMER_TOKEN:
                return _ucp_error(
                    401,
                    "oauth_error",
                    "A logged-in Shopware customer context token is required for UCP identity linking.",
                )
            if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
                return _ucp_error(400, "oauth_error", "PKCE code challenge is required.")
            self.oauth_codes[OAUTH_CODE] = {
                "challenge": query["code_challenge"],
                "state": query.get("state"),
            }
            return httpx.Response(
                200,
                json={
                    "client_id": query.get("client_id"),
                    "code": OAUTH_CODE,
                    "state": query.get("state"),
                    "subject": "customer-1",
                    "redirect_to": f"http://localhost:8004/api/auth/shopware/callback?code={OAUTH_CODE}&state={query.get('state')}",
                },
            )
        if path.endswith("/token") and method == "POST":
            grant = body.get("grant_type")
            if grant == "authorization_code":
                pending = self.oauth_codes.pop(str(body.get("code")), None)
                if pending is None or not body.get("code_verifier"):
                    return _ucp_error(
                        400,
                        "oauth_error",
                        "Authorization code is invalid, expired, or already consumed.",
                    )
                import base64
                import hashlib

                digest = hashlib.sha256(str(body["code_verifier"]).encode()).digest()
                if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != pending["challenge"]:
                    return _ucp_error(
                        400,
                        "oauth_error",
                        "PKCE code verifier does not match the authorization request.",
                    )
            elif grant == "refresh_token":
                if body.get("refresh_token") != REFRESH_TOKEN:
                    return _ucp_error(400, "oauth_error", "Refresh token is invalid or expired.")
            else:
                return _ucp_error(
                    400,
                    "oauth_error",
                    "Only authorization_code and refresh_token grants are supported.",
                )
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": REFRESH_TOKEN,
                    "scope": "dev.ucp.shopping.cart:manage dev.ucp.shopping.order:read",
                },
            )
        return httpx.Response(404, json={"detail": f"unmocked OAuth {method} {path}"})

    # ------------------------------------------------------------------ Store API

    def _store_api(
        self, method: str, path: str, query: str, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        if request.headers.get("sw-access-key") != "test-key":
            return httpx.Response(401, json={"errors": [{"detail": "Access key is invalid"}]})
        token = request.headers.get("sw-context-token")
        if path == "/store-api/context":
            return httpx.Response(
                200,
                json={"salesChannel": {"name": "Storefront", "translated": {"name": "Storefront"}}},
            )
        if path == "/store-api/account/customer" and method == "POST":
            if token != CUSTOMER_TOKEN:
                return httpx.Response(403, json={"errors": [{"detail": "Not signed in"}]})
            return httpx.Response(200, json={
                "id": "customer-1",
                "firstName": "Test",
                "lastName": "Customer",
                "email": CUSTOMER_EMAIL,
                "defaultShippingAddress": {
                    "id": "address-1",
                    "street": "Example Street 1",
                    "zipcode": "10115",
                    "city": "Berlin",
                    "country": {"iso": "DE"},
                },
            })
        if path == "/store-api/account/login":
            if body.get("username") == CUSTOMER_EMAIL and body.get("password") == CUSTOMER_PASSWORD:
                # Shopware 6.7 returns the context token in the header, not the body.
                return httpx.Response(
                    200,
                    headers={"sw-context-token": CUSTOMER_TOKEN},
                    json={"apiAlias": "account_login", "redirectUrl": None},
                )
            return httpx.Response(
                401,
                json={
                    "errors": [
                        {
                            "code": "CHECKOUT__CUSTOMER_AUTH_BAD_CREDENTIALS",
                            "detail": "Invalid username and/or password.",
                        }
                    ]
                },
            )
        if path == "/store-api/checkout/cart" and method == "GET":
            if token not in self.customer_carts:
                return httpx.Response(403, json={"errors": [{"detail": "Not signed in"}]})
            return httpx.Response(200, json={"token": token, "lineItems": deepcopy(self.customer_carts[token])})
        if path == "/store-api/checkout/cart/line-item":
            if token not in self.customer_carts:
                return httpx.Response(403, json={"errors": [{"detail": "Not signed in"}]})
            lines = self.customer_carts[token]
            if method == "POST":
                for item in body.get("items") or []:
                    product_id = item["referencedId"]
                    existing = next((line for line in lines if line["referencedId"] == product_id), None)
                    if existing:
                        existing["quantity"] += item["quantity"]
                    else:
                        lines.append({
                            "id": f"line-{product_id}", "type": "product",
                            "referencedId": product_id, "label": "Test product",
                            "quantity": item["quantity"], "price": {"unitPrice": 29.99},
                        })
            elif method == "PATCH":
                for item in body.get("items") or []:
                    line = next(line for line in lines if line["id"] == item["id"])
                    line["quantity"] = item["quantity"]
            elif method == "DELETE":
                lines[:] = [line for line in lines if line["id"] not in body.get("ids", [])]
            return httpx.Response(200, json={"token": token, "lineItems": deepcopy(lines)})
        if path == "/store-api/checkout/order" and method == "POST":
            if token not in self.customer_carts or not self.customer_carts[token]:
                return httpx.Response(400, json={"errors": [{"detail": "Empty cart"}]})
            order = {
                "id": ORDER_ID, "orderNumber": ORDER_NUMBER,
                "orderDateTime": "2026-09-25T09:12:00.000+00:00",
                "amountTotal": sum(line["price"]["unitPrice"] * line["quantity"] for line in self.customer_carts[token]),
                "currency": {"isoCode": "EUR"},
                "stateMachineState": {"technicalName": "open"},
                "lineItems": [{
                    "type": "product", "referencedId": line["referencedId"],
                    "label": line["label"], "quantity": line["quantity"],
                    "unitPrice": line["price"]["unitPrice"],
                } for line in self.customer_carts[token]],
            }
            self.customer_orders[token].insert(0, order)
            self.customer_carts[token] = []
            return httpx.Response(200, json=deepcopy(order))
        if path == "/store-api/product" and method == "POST":
            parent_filter = next(
                (
                    f
                    for f in body.get("filter") or []
                    if isinstance(f, dict) and f.get("field") == "parentId"
                ),
                None,
            )
            if parent_filter is not None and parent_filter.get("value") is not None:
                parent = parent_filter.get("value")
                children = [c for c in STORE_CHILDREN if c["parentId"] == parent]
                return httpx.Response(200, json={"elements": deepcopy(children)})
            listed = [
                deepcopy(STORE_PRODUCTS[PRODUCT_ID]),
                deepcopy(STORE_PRODUCTS[OIL_ID]),
                deepcopy(STORE_PRODUCTS[MAIN_ID]),
            ]
            return httpx.Response(200, json={"elements": listed})
        if path.startswith("/store-api/product/"):
            wanted = path.rsplit("/", 1)[-1]
            if wanted == PRODUCT_ID:
                # Live behaviour: the product route resolves a family id to its best child,
                # which carries ``parentId == <family>`` and the inherited delivery time.
                best_child = deepcopy(STORE_CHILDREN[0])
                best_child["deliveryTime"] = deepcopy(STORE_PRODUCTS[PRODUCT_ID]["deliveryTime"])
                return httpx.Response(200, json={"product": best_child})
            product = STORE_PRODUCTS.get(wanted)
            if product is None:
                return httpx.Response(404, json={"errors": [{"detail": "Product not found"}]})
            return httpx.Response(200, json={"product": deepcopy(product)})
        if path == "/store-api/search":
            if self.store_search_html:
                return httpx.Response(
                    400, text="<html>Oops</html>", headers={"content-type": "text/html"}
                )
            params = dict(part.split("=", 1) for part in (query or "").split("&") if "=" in part)
            term = str(params.get("search") or body.get("search") or "").lower()
            if "shirt" in term:
                products = [deepcopy(SHIRT)]
            elif "oil" in term or "olive" in term:
                products = [deepcopy(OIL)]
            elif "tee" in term:
                # Live behaviour: search answers a variant family with its children.
                products = [deepcopy(STORE_CHILDREN[0]), deepcopy(STORE_CHILDREN[1])]
            elif not term.strip() or term.strip() in {"*", "a"}:
                products = [deepcopy(SHIRT), deepcopy(OIL)]
            else:
                products = []
            return httpx.Response(200, json={"elements": products})
        if path == "/store-api/shipping-method":
            return httpx.Response(200, json={"elements": deepcopy(SHIPPING_METHODS)})
        if path == "/store-api/order":
            if token is None or token not in self.customer_orders:
                return httpx.Response(
                    403,
                    json={
                        "errors": [
                            {
                                "code": "CHECKOUT__CUSTOMER_NOT_LOGGED_IN",
                                "detail": "Customer is not logged in.",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200, json={"orders": {"elements": deepcopy(self.customer_orders[token])}}
            )
        if path.startswith("/store-api/navigation/footer-navigation"):
            return httpx.Response(200, json=deepcopy(FOOTER_NAVIGATION))
        if path.startswith("/store-api/navigation/service-navigation"):
            return httpx.Response(200, json=deepcopy(SERVICE_NAVIGATION))
        if path.startswith("/store-api/category/"):
            category = STORE_CATEGORIES.get(path.rsplit("/", 1)[-1])
            if category is None:
                return httpx.Response(404, json={"errors": [{"detail": "Category not found"}]})
            return httpx.Response(200, json=deepcopy(category))
        return httpx.Response(404, json={"errors": [{"detail": f"unmocked {method} {path}"}]})

    # ------------------------------------------------------------------ cart state

    def _complete_checkout(self, checkout_id: str, body: dict[str, Any]) -> httpx.Response:
        """Completing places the order. What the completion request carries is merged
        over what the checkout already holds — the live shop refuses a session that
        ends up with no buyer or no destination."""
        session = self.checkouts.get(checkout_id)
        if session is None:
            return _ucp_error(404, "checkout_not_found", "No such checkout session")
        merged = {**session, **body}
        if not merged.get("buyer"):
            return httpx.Response(
                400, json={"detail": "$.checkout_session.buyer.email is required"}
            )
        methods = (merged.get("fulfillment") or {}).get("methods") or []
        if not any(
            destination.get("street_address")
            for method in methods
            for destination in (method.get("destinations") or [])
        ):
            return httpx.Response(
                400,
                json={
                    "detail": "$.fulfillment.methods[0].destinations[0].street_address is required"
                },
            )
        self.checkouts.pop(checkout_id)
        return httpx.Response(
            200,
            json={
                "id": checkout_id,
                "status": "completed",
                "currency": "EUR",
                "order": {
                    "id": ORDER_NUMBER,
                    "permalink_url": f"http://shopware.test/account/order/{ORDER_NUMBER}",
                },
            },
        )

    def _create_cart(self, line_items: list[dict[str, Any]]) -> dict[str, Any]:
        self._cart_seq += 1
        cart_id = CART_ID if self._cart_seq == 1 else f"{CART_ID[:-2]}{self._cart_seq:02d}"
        self.carts[cart_id] = self._cart_document(cart_id, line_items)
        return deepcopy(self.carts[cart_id])

    def _cart_document(self, cart_id: str, line_items: list[dict[str, Any]]) -> dict[str, Any]:
        mapped = []
        for index, line in enumerate(line_items, start=1):
            if int(line.get("quantity") or 0) <= 0:
                continue
            item = line.get("item") or {}
            vid = str(item.get("id") or "")
            title = "Claude Commerce T-Shirt — S"
            for variant in SHIRT["variants"]:
                if variant["id"] == vid and vid != PRODUCT_ID:
                    title = f"Claude Commerce T-Shirt — {variant['title']}"
            if vid == OIL_ID:
                title = OIL["title"]
            mapped.append(
                {
                    "id": str(line.get("id") or f"line-{index}"),
                    "quantity": int(line.get("quantity") or 1),
                    "item": {
                        "id": vid,
                        "title": title,
                        "price": dict(_PRICE if vid != OIL_ID else OIL["price"]),
                    },
                }
            )
        return {"id": cart_id, "currency": "EUR", "line_items": mapped}


def _discovery() -> dict[str, Any]:
    path = DATA_DIR / "discovery.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "ucp": {"version": "2026-08-25"},
        "services": {
            "dev.ucp.shopping": {"rest": {"endpoint": "/ucp/v1"}, "mcp": {"endpoint": "/ucp/mcp"}}
        },
    }


def replay_transport(shop: ShopwareReplay | None = None) -> httpx.MockTransport:
    return httpx.MockTransport((shop or ShopwareReplay()).handle)
