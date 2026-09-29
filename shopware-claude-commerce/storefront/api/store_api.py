# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Store API client for surfaces UCP does not cover: context/branding, CMS policies,
shipping methods, product associations (variants, Grundpreis, deliveryTime), and the
customer's orders behind the cart's own ``sw-context-token``.

Every failed call is logged with method, path and Shopware's error detail and raised as
:class:`StoreApiError`; the only silent ``None`` is a 404 on a single-record lookup, which
means "no such product" and nothing else.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0)
ORDER_PAGE_LIMIT = 20
CHILD_PRODUCTS_LIMIT = 50
_NOT_CONFIGURED = "SHOPWARE_SALES_CHANNEL_ACCESS_KEY is not set; Store API calls are skipped"


def access_key_from_env() -> str:
    return os.environ.get("SHOPWARE_SALES_CHANNEL_ACCESS_KEY", "")


class StoreApiError(RuntimeError):
    """Shopware's Store API answered ≥ 400 (or is not configured)."""

    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"Store API {method} {path} → {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class StoreApiNotConfigured(StoreApiError):
    def __init__(self, method: str, path: str) -> None:
        super().__init__(method, path, 0, _NOT_CONFIGURED)


class StoreApiClient:
    def __init__(
        self,
        shop_url: str,
        access_key: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.shop_url = shop_url.rstrip("/")
        self.access_key = access_key if access_key is not None else access_key_from_env()
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        self._warned_unconfigured = False

    @property
    def configured(self) -> bool:
        return bool(self.access_key)

    def _headers(self, context_token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "sw-access-key": self.access_key,
        }
        if context_token:
            headers["sw-context-token"] = context_token
        return headers

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        context_token: str | None = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        if not self.configured:
            if not self._warned_unconfigured:
                self._warned_unconfigured = True
                logger.warning(_NOT_CONFIGURED)
            raise StoreApiNotConfigured(method, path)
        try:
            response = await self._http.request(
                method,
                f"{self.shop_url}{path}",
                headers=self._headers(context_token),
                json=json,
                params=params,
            )
        except httpx.HTTPError as error:
            logger.error("Store API %s %s failed: %s", method, path, error)
            raise StoreApiError(method, path, 0, str(error)) from error
        if response.status_code >= 400:
            detail = _error_detail(response)
            level = logging.INFO if response.status_code == 404 else logging.ERROR
            logger.log(
                level, "Store API %s %s → %s: %s", method, path, response.status_code, detail
            )
            raise StoreApiError(method, path, response.status_code, detail)
        if raw:
            return response
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    # ------------------------------------------------------------------ context & account

    async def context(self, context_token: str | None = None) -> dict[str, Any]:
        return await self._request("GET", "/store-api/context", context_token=context_token) or {}

    async def customer(self, context_token: str) -> dict[str, Any]:
        """The account proven by this Store API context token."""
        return await self._request(
            "POST",
            "/store-api/account/customer",
            json={"associations": {"defaultShippingAddress": {"associations": {"country": {}}}}},
            context_token=context_token,
        ) or {}

    async def login(self, email: str, password: str) -> str:
        """Customer login; returns the customer's context token (Store API
        ``account/login``). Credentials are not logged. Shopware 6.7 returns the token in
        the ``sw-context-token`` response header; the JSON body carries it only on older
        versions, so the header is read first with the body as a fallback."""
        response = await self._request(
            "POST", "/store-api/account/login", json={"username": email, "password": password},
            raw=True,
        )
        token = response.headers.get("sw-context-token")
        if not token:
            try:
                token = (response.json() or {}).get("contextToken")
            except ValueError:
                token = None
        if not token:
            raise StoreApiError(
                "POST", "/store-api/account/login", 200, "no contextToken in response"
            )
        return str(token)

    # ------------------------------------------------------------------ customer cart & order

    async def cart(self, context_token: str) -> dict[str, Any]:
        return await self._request("GET", "/store-api/checkout/cart", context_token=context_token)

    async def add_cart_item(
        self, context_token: str, product_id: str, quantity: int
    ) -> dict[str, Any]:
        return await self.add_cart_items(context_token, [(product_id, quantity)])

    async def add_cart_items(
        self, context_token: str, items: list[tuple[str, int]]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/store-api/checkout/cart/line-item",
            json={"items": [
                {"type": "product", "referencedId": product_id, "quantity": quantity}
                for product_id, quantity in items
            ]},
            context_token=context_token,
        )

    async def update_cart_item(
        self, context_token: str, line_id: str, quantity: int
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", "/store-api/checkout/cart/line-item",
            json={"items": [{"id": line_id, "quantity": quantity}]},
            context_token=context_token,
        )

    async def remove_cart_item(self, context_token: str, line_id: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", "/store-api/checkout/cart/line-item",
            json={"ids": [line_id]}, context_token=context_token,
        )

    async def place_order(
        self, context_token: str, customer_comment: str | None = None
    ) -> dict[str, Any]:
        """Shopware creates the order for the customer behind this token. A
        ``customer_comment`` is stored on the order as the buyer's note."""
        body = {"customerComment": customer_comment} if customer_comment else {}
        return await self._request(
            "POST", "/store-api/checkout/order", json=body, context_token=context_token
        )

    # ------------------------------------------------------------------ catalog

    async def search_products(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Full-text search. An empty or wildcard query lists the catalog instead —
        ``POST /store-api/search`` requires a term and answers 400 without one."""
        term = (query or "").strip()
        if not term or term == "*":
            return await self.list_products(limit)
        payload = {"limit": limit, "search": term, "associations": _product_associations()}
        try:
            body = await self._request(
                "POST",
                "/store-api/search",
                json=payload,
                params={"search": term},
            )
        except StoreApiError as error:
            # HTML 400 = storefront router (path / sales-channel miss), not a JSON search miss.
            if error.status in {400, 404, 405}:
                logger.info("Store API search unavailable (%s); listing fallback", error)
                return await self._filter_catalog(term, limit)
            raise
        rows = (body or {}).get("elements") or (body or {}).get("products") or []
        rows = await self._families_of(rows, limit)
        if rows:
            return rows
        # 200 + empty is common when the search index is unbuilt (WASM / demo); the listing
        # is the same catalog the product grid shows.
        logger.info("Store API search returned no rows for %r; listing fallback", term)
        return await self._filter_catalog(term, limit)

    async def _families_of(self, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Search answers a variant family with its children; callers key products by family
        (like ``list_products``), so each child becomes its family's listing row, once."""
        if not any(row.get("parentId") for row in rows):
            return rows
        listed = {row["id"]: row for row in await self.list_products(max(limit, 24))}
        families: list[dict[str, Any]] = []
        for row in rows:
            family = listed.get(row["parentId"]) if row.get("parentId") else row
            if family is not None and family not in families:
                families.append(family)
        return families

    async def _filter_catalog(self, term: str, limit: int) -> list[dict[str, Any]]:
        listed = await self.list_products(max(limit, 24))
        return _filter_listed(listed, term)[:limit]

    async def list_products(self, limit: int = 24) -> list[dict[str, Any]]:
        """Visible catalog rows (``POST /store-api/product``). Children are dropped so the
        grid and warm-up show families, matching UCP search; the ``parentId`` filter keeps
        them from using up ``limit``."""
        body = await self._request(
            "POST",
            "/store-api/product",
            json={
                "limit": limit,
                "filter": [{"type": "equals", "field": "parentId", "value": None}],
                "associations": _product_associations(),
            },
        )
        if not body:
            return []
        rows = body.get("elements") or body.get("products") or []
        return [row for row in rows if not row.get("parentId")]

    async def product(self, product_id: str) -> dict[str, Any] | None:
        try:
            body = await self._request(
                "POST",
                f"/store-api/product/{product_id}",
                json={"associations": _product_associations()},
            )
        except StoreApiError as error:
            if error.status in {400, 404}:
                return None
            raise
        if not body:
            return None
        return body.get("product") or body.get("data") or body

    async def child_products(self, parent_id: str) -> list[dict[str, Any]]:
        """Variants of a parent. Product detail often returns the selected child, not children[]."""
        body = await self._request(
            "POST",
            "/store-api/product",
            json={
                "limit": CHILD_PRODUCTS_LIMIT,
                "filter": [{"type": "equals", "field": "parentId", "value": parent_id}],
                "associations": {
                    "options": {"associations": {"group": {}}},
                    "cover": {"associations": {"media": {}}},
                    "deliveryTime": {},
                },
            },
        )
        if not body:
            return []
        return body.get("elements") or body.get("products") or []

    async def save_product_review(
        self, product_id: str, context_token: str, review: dict[str, Any]
    ) -> None:
        """A logged-in customer's review of a product (``product/{id}/review``); with an
        ``id`` it updates that customer's own review. Shopware answers 204."""
        await self._request(
            "POST",
            f"/store-api/product/{product_id}/review",
            json=review,
            context_token=context_token,
            raw=True,
        )

    async def product_reviews(self, product_id: str, limit: int = 3) -> dict[str, Any]:
        """Approved reviews, newest first, with the exact total and the average points.
        Sent without a context token, so a customer's own pending review is never included."""
        body = await self._request(
            "POST",
            f"/store-api/product/{product_id}/reviews",
            json={
                "limit": limit,
                "total-count-mode": "exact",
                "sort": [{"field": "createdAt", "order": "DESC"}],
                "aggregations": [{"name": "rating-average", "type": "avg", "field": "points"}],
            },
        )
        return body or {}

    # ------------------------------------------------------------------ fulfillment

    async def shipping_methods(self, context_token: str | None = None) -> list[dict[str, Any]]:
        body = await self._request(
            "POST",
            "/store-api/shipping-method",
            json={"associations": {"prices": {}, "deliveryTime": {}}},
            params={"onlyAvailable": 1},
            context_token=context_token,
        )
        if not body:
            return []
        return body.get("elements") or []

    # ------------------------------------------------------------------ orders

    async def orders(
        self, context_token: str, limit: int = ORDER_PAGE_LIMIT
    ) -> list[dict[str, Any]]:
        """The orders of the customer (or guest) bound to ``context_token``. Shopware
        refuses without a customer in the context; that surfaces as an empty list."""
        try:
            body = await self._request(
                "POST",
                "/store-api/order",
                json={
                    "limit": limit,
                    "sort": [{"field": "orderDateTime", "order": "DESC"}],
                    "associations": {
                        "lineItems": {"associations": {"cover": {}}},
                        "deliveries": {
                            "associations": {"stateMachineState": {}, "shippingMethod": {}}
                        },
                        "transactions": {"associations": {"stateMachineState": {}}},
                        "stateMachineState": {},
                        "currency": {},
                    },
                },
                context_token=context_token,
            )
        except StoreApiError as error:
            if error.status == 403:
                logger.info("Store API order list refused for this context (no customer yet)")
                return []
            raise
        orders = (body or {}).get("orders") or {}
        if isinstance(orders, dict):
            return orders.get("elements") or []
        return orders if isinstance(orders, list) else []

    # ------------------------------------------------------------------ CMS / policies

    async def navigation(self, name: str = "footer-navigation") -> list[dict[str, Any]]:
        body = await self._request(
            "GET", f"/store-api/navigation/{name}/{name}", params={"depth": 3}
        )
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return body.get("elements") or []
        return []

    async def category(self, category_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/store-api/category/{category_id}", json={}) or {}

    async def text_file(self, path: str) -> str:
        try:
            response = await self._http.get(f"{self.shop_url}{path}", follow_redirects=True)
        except httpx.HTTPError as error:
            logger.warning("GET %s failed: %s", path, error)
            return ""
        if response.status_code >= 400:
            return ""
        return response.text


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            return str(first.get("detail") or first.get("title") or first.get("code") or errors[0])
        return str(payload.get("detail") or payload.get("message") or payload)[:300]
    return str(payload)[:300]


def _filter_listed(products: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    tokens = [part for part in (query or "").lower().split() if part and part != "*"]
    if not tokens:
        return products

    def blob(product: dict[str, Any]) -> str:
        translated = product.get("translated") if isinstance(product.get("translated"), dict) else {}
        categories = product.get("categories") or []
        category_names = [
            str((item.get("translated") or {}).get("name") or item.get("name") or "")
            if isinstance(item, dict)
            else str(item)
            for item in categories
        ]
        return " ".join(
            [
                str(product.get("name") or ""),
                str(translated.get("name") or ""),
                str(product.get("productNumber") or ""),
                str(translated.get("description") or ""),
                *category_names,
            ]
        ).lower()

    return [product for product in products if all(token in blob(product) for token in tokens)]


def _product_associations() -> dict[str, Any]:
    return {
        "children": {
            "associations": {
                "options": {"associations": {"group": {}}},
                "cover": {"associations": {"media": {}}},
                "deliveryTime": {},
            }
        },
        "options": {"associations": {"group": {}}},
        "properties": {"associations": {"group": {}}},
        "cover": {"associations": {"media": {}}},
        "deliveryTime": {},
        "unit": {},
        "manufacturer": {},
        "categories": {},
        "seoUrls": {},
    }
