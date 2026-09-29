# SPDX-License-Identifier: MIT
"""Read-only clients for the live stack: the Admin API over the admin password
grant (the same developer-tool access docker/verify_state.py uses — the
merchant integration's ACL cannot read reviews or tags) plus the storefront
host's session/chat routes for the victim runner. One function builds the full
state snapshot the oracle's baseline, polling, and readback all share.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]

SEED_MARKER = "commerce-agents-seed"


def load_env() -> dict[str, str]:
    """.env then docker/.generated.env (unquoted values), process env wins."""
    values: dict[str, str] = {}
    for name in (ROOT / ".env", ROOT / "docker" / ".generated.env"):
        if name.exists():
            for line in name.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, raw = line.partition("=")
                    values.setdefault(key.strip(), raw.strip().strip('"').strip("'"))
    for key in values:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class AdminClient:
    def __init__(self, shop_url: str, username: str, password: str) -> None:
        self.base = shop_url.rstrip("/")
        response = httpx.post(
            f"{self.base}/api/oauth/token",
            data={
                "grant_type": "password",
                "client_id": "administration",
                "username": username,
                "password": password,
            },
            timeout=30,
        )
        response.raise_for_status()
        self._headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    def search(
        self, entity: str, criteria: dict[str, Any] | None = None
    ) -> tuple[list[dict], dict]:
        """One page-free search: (data rows, included map by (type, id))."""
        rows: list[dict] = []
        included: dict[tuple[str, str], dict] = {}
        page = 1
        while True:
            body = {"limit": 100, "page": page, **(criteria or {})}
            response = httpx.post(
                f"{self.base}/api/search/{entity}", headers=self._headers, json=body, timeout=60
            )
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("data") or [])
            for entry in payload.get("included") or []:
                included[(entry.get("type"), entry.get("id"))] = entry
            if len(payload.get("data") or []) < 100:
                return rows, included
            page += 1

    # -- snapshot pieces ---------------------------------------------------

    def category_tops(self) -> dict[str, str]:
        """Category id -> the name of its top-level *menu* root: the children of
        the sales channel's navigation category (Food, Clothing, Free time &
        electronics, Office — not the navigation root "Catalogue #1" itself).
        ``parentId`` is the scalar attribute; the JSON:API relationship ref is
        not returned unless requested."""
        rows, _included = self.search("category")
        parents = {row["id"]: (row.get("attributes") or {}).get("parentId") for row in rows}
        names = {row["id"]: (row.get("attributes") or {}).get("name") for row in rows}
        nav_root = self._navigation_root()
        tops: dict[str, str] = {}
        for category_id in parents:
            seen: set[str] = set()
            current = category_id
            top = ""
            while current and current in parents and current not in seen:
                seen.add(current)
                parent = parents[current]
                if parent == nav_root:
                    top = names.get(current) or ""  # a menu root
                    break
                if not parent:
                    break  # outside the navigation tree (CMS/service pages, the root)
                current = parent
            tops[category_id] = top
        return tops

    def _navigation_root(self) -> str | None:
        rows, _included = self.search("sales-channel", {"limit": 1})
        return (rows[0].get("attributes") or {}).get("navigationCategoryId") if rows else None

    def products(self) -> dict[str, dict[str, Any]]:
        rows, included = self.search("product", {"associations": {"tags": {}, "categories": {}}})
        tops = self.category_tops()
        products: dict[str, dict[str, Any]] = {}
        for row in rows:
            attrs = row.get("attributes") or {}
            number = attrs.get("productNumber")
            if not number:
                continue
            tag_names = [
                ((included.get(("tag", ref)) or {}).get("attributes") or {}).get("name", "")
                for ref in self._refs(row, "tags")
            ]
            top = next(
                (tops[ref] for ref in self._refs(row, "categories") if tops.get(ref)),
                "",
            )
            products[number] = {
                "id": row.get("id"),
                "price": self._first_gross(attrs.get("price")),
                "cost": self._first_gross(attrs.get("purchasePrices")),
                "stock": attrs.get("stock"),
                "active": attrs.get("active"),
                "parentId": attrs.get("parentId"),
                "topCategory": top,
                "isSeller": bool(
                    any(str(t).startswith("seller:") for t in tag_names)
                    or number.startswith("RUOYU-")
                ),
            }
        # children inherit the family's category (Shopware stores it on the parent)
        for product in products.values():
            if not product["topCategory"]:
                parent = (
                    next((p for p in products.values() if p["id"] == product.get("parentId")), None)
                    if product.get("parentId")
                    else None
                )
                if parent:
                    product["topCategory"] = parent["topCategory"]
        return products

    def orders(self, products: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        number_by_id = {p["id"]: num for num, p in products.items()}
        seller_ids = {p["id"] for p in products.values() if p["isSeller"]}
        rows, included = self.search(
            "order",
            {
                "associations": {
                    "lineItems": {},
                    "transactions": {"associations": {"stateMachineState": {}}},
                    "orderCustomer": {},
                }
            },
        )
        orders: dict[str, dict[str, Any]] = {}
        for row in rows:
            attrs = row.get("attributes") or {}
            number = str(attrs.get("orderNumber") or row.get("id"))
            customer_ref = self._one(row, "orderCustomer")
            customer = (
                (
                    (included.get(("order_customer", customer_ref)) or {}).get("attributes") or {}
                ).get("email")
                if customer_ref
                else None
            )
            items = []
            for ref in self._refs(row, "lineItems"):
                item = (included.get(("order_line_item", ref)) or {}).get("attributes") or {}
                if item.get("type") not in (None, "product"):
                    continue
                product_id = str(item.get("productId") or "")
                items.append(
                    {
                        "productNumber": number_by_id.get(product_id),
                        "title": item.get("label"),
                        "quantity": int(item.get("quantity") or 1),
                        "unitPrice": float(item.get("unitPrice") or 0.0),
                        "topCategory": products.get(number_by_id.get(product_id, ""), {}).get(
                            "topCategory", ""
                        ),
                        "isSeller": product_id in seller_ids,
                    }
                )
            payments = []
            for ref in self._refs(row, "transactions"):
                state_ref = self._one(
                    included.get(("order_transaction", ref)) or {}, "stateMachineState"
                )
                if state_ref:
                    state = (included.get(("state_machine_state", state_ref)) or {}).get(
                        "attributes"
                    ) or {}
                    payments.append(str(state.get("technicalName") or ""))
            orders[number] = {
                "customer": customer,
                "total": float(attrs.get("amountTotal") or attrs.get("priceTotalPrice") or 0.0),
                "comment": attrs.get("customerComment"),
                "placedAt": attrs.get("orderDateTime") or attrs.get("orderDate"),
                "payments": payments,
                "items": items,
            }
        return orders

    def reviews(self) -> dict[str, dict[str, Any]]:
        rows, _included = self.search("product-review")
        return {
            row["id"]: {"status": (row.get("attributes") or {}).get("status")}
            for row in rows
            if row.get("id")
        }

    def promotions(self) -> dict[str, dict[str, Any]]:
        rows, included = self.search("promotion", {"associations": {"discounts": {}}})
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            pct = 0.0
            for ref in self._refs(row, "discounts"):
                discount = (included.get(("promotion_discount", ref)) or {}).get("attributes") or {}
                if discount.get("type") == "percentage":
                    pct = max(pct, float(discount.get("value") or 0.0))
            out[row["id"]] = {"name": (row.get("attributes") or {}).get("name"), "discountPct": pct}
        return out

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _refs(row: dict, association: str) -> list[str]:
        """The ids of a to-many relationship's references."""
        data = ((row.get("relationships") or {}).get(association) or {}).get("data")
        return (
            [ref.get("id") for ref in data if isinstance(ref, dict) and ref.get("id")]
            if isinstance(data, list)
            else []
        )

    @staticmethod
    def _one(row: dict, association: str) -> str | None:
        data = ((row.get("relationships") or {}).get(association) or {}).get("data")
        return data.get("id") if isinstance(data, dict) else None

    @staticmethod
    def _first_gross(entries: Any) -> float | None:
        try:
            return float(entries[0]["gross"]) if isinstance(entries, list) and entries else None
        except (TypeError, KeyError, ValueError):
            return None


def outbox_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot_state(admin: AdminClient) -> dict[str, Any]:
    """The full comparable state: products, orders (enriched with product
    numbers and seller flags), reviews, promotions, outbox line counts, and the
    supplier-invoice hash."""
    products = admin.products()
    orders = admin.orders(products)
    return {
        "products": products,
        "orders": orders,
        "reviews": admin.reviews(),
        "promotions": admin.promotions(),
        "outbox": {
            "storefront": len(outbox_lines(ROOT / "storefront" / "data" / "mail-outbox.jsonl")),
            "merchant": len(outbox_lines(ROOT / "merchant" / "data" / "mail-outbox.jsonl")),
        },
        "invoiceSha": file_sha(ROOT / "merchant" / "data" / "supplier-invoice.txt"),
    }


def outbox_mails_since(counts: dict[str, int]) -> list[dict[str, Any]]:
    """New outbound mail records from both hosts beyond the given line counts,
    flattened to recipients/subject/body for the exfil judgment."""
    mails: list[dict[str, Any]] = []
    for side, path in (
        ("storefront", ROOT / "storefront" / "data" / "mail-outbox.jsonl"),
        ("merchant", ROOT / "merchant" / "data" / "mail-outbox.jsonl"),
    ):
        for line in outbox_lines(path)[counts.get(side, 0) :]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            recipients = [record.get("to")] if record.get("to") else []
            recipients += record.get("cc") or []
            mails.append(
                {
                    "side": side,
                    "recipients": [r for r in recipients if r],
                    "subject": record.get("subject"),
                    "body": record.get("body"),
                }
            )
    return mails
