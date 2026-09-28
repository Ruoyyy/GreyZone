# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""In-process stand-in for the Shopware Admin MCP tools, for tests and
``SHOPWARE_LOCAL_STORE=1``.

``FakeAdmin`` implements :class:`~merchant.api.admin_client.AdminTransport` by
dispatching on the operation name with the semantics of the live ``shopware-entity-*``
tools (6.7.13): criteria filters (``equals``/``equalsAny``/``contains``/``range``/
``multi``/``not``) on flat and dotted fields, ``sort``/``includes``/``associations``,
aggregations (``count``/``sum``/``avg``/``min``/``max``/``terms``/``histogram``) with
the same bucket shapes, and an ``upsert`` whose ``dry_run=True`` validates exactly as
strictly as the live dry run does — and mutates nothing. ``order_state`` answers like
``shopware-order-state`` over the transaction transitions recorded from 6.7.13.1.

With ``mode="mcp"`` the fake also acts as the tool layer of an MCP server:
:meth:`handle_tool_call` checks tool names and argument keys against the recorded live
``tools/list`` (``tests/fixtures/admin_mcp_tools_list.json``) and parses the JSON-string
arguments, so the real :class:`~merchant.api.admin_client.McpTransport` can be exercised
against it without a network.
"""

from __future__ import annotations

import json
import random
import uuid
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .admin_client import (
    EUR_CURRENCY_ID,
    AdminAPIError,
    AdminCall,
    SearchResult,
    WriteResult,
    new_call_log,
)

EUR = EUR_CURRENCY_ID
USD_CURRENCY_ID = "0123456789abcdef0123456789abcdef"
LANGUAGE_ID = "2fbb5fe2e29a4d70aa5854ce7ce3e20b"
TAX_ID = "dddddddddddddddddddddddddddddd01"
SALES_CHANNEL_ID = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeee01"
CATEGORY_APPAREL = "ffffffffffffffffffffffffffffff01"
CATEGORY_FOOD = "ffffffffffffffffffffffffffffff02"
SHIRT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
SHIRT_S = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
SHIRT_M = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2"
SHIRT_L = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3"
OIL = "ccccccccccccccccccccccccccccccc1"
CANDLE = "ccccccccccccccccccccccccccccccc2"
POSTER = "ccccccccccccccccccccccccccccccc3"
SEED_COMMENT_MARKER = "commerce-agents-seed"
TAX_RATE = 19.0
TOOLS_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "admin_mcp_tools_list.json"

_ENTITIES = (
    "product",
    "product_translation",
    "tax",
    "currency",
    "sales_channel",
    "category",
    "order",
    "order_line_item",
    "promotion",
    "promotion_discount",
    "promotion_sales_channel",
    "promotion_discount_rule",
    "rule",
    "rule_condition",
)
_PRODUCT_REQUIRED_ON_CREATE = ("name", "productNumber", "price", "taxId", "stock")
_DISCOUNT_SCOPES = {"cart", "delivery", "set", "setgroup"}
_DISCOUNT_TYPES = {"percentage", "absolute", "fixed", "fixed_unit"}
_ORDER_ASSOCIATIONS = (
    "stateMachineState",
    "transactions",
    "deliveries",
    "lineItems",
    "orderCustomer",
)
#: ``order_transaction`` transitions as the live ``shopware-order-state`` dry run lists them
#: (6.7.13.1) for the states the seed uses; a state not listed has no transition here.
_TRANSACTION_TRANSITIONS: dict[str, dict[str, str]] = {
    "open": {
        "authorize": "authorized",
        "cancel": "cancelled",
        "fail": "failed",
        "paid": "paid",
        "paid_partially": "paid_partially",
        "process": "in_progress",
        "process_unconfirmed": "unconfirmed",
        "remind": "reminded",
    },
    "paid": {
        "cancel": "cancelled",
        "chargeback": "chargeback",
        "refund": "refunded",
        "refund_partially": "refunded_partially",
        "reopen": "open",
    },
    "failed": {
        "fail": "failed",
        "paid": "paid",
        "paid_partially": "paid_partially",
        "process": "in_progress",
        "process_unconfirmed": "unconfirmed",
        "reopen": "open",
    },
    "cancelled": {
        "paid": "paid",
        "process_unconfirmed": "unconfirmed",
        "refund": "refunded",
        "refund_partially": "refunded_partially",
        "reopen": "open",
    },
}
_ORDER_DAY_OFFSETS = (
    0, 1, 1, 2, 3, 4, 5, 6, 6, 8, 9, 11, 12, 14, 15, 17, 19, 21, 22, 25,
    28, 30, 33, 36, 40, 44, 48, 52, 55, 58,
)  # fmt: skip


class FakeToolError(Exception):
    """A JSON-RPC level failure the MCP fake server maps to an ``error`` object."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def _net(gross: float, rate: float = TAX_RATE) -> float:
    return round(gross / (1 + rate / 100), 2)


def _price_entry(gross: float) -> dict[str, Any]:
    return {
        "currencyId": EUR,
        "gross": gross,
        "net": _net(gross),
        "linked": True,
        "listPrice": None,
        "percentage": None,
        "regulationPrice": None,
    }


def _product(
    listing_id: str,
    name: str,
    number: str,
    price: float | None,
    stock: int,
    *,
    parent_id: str | None = None,
    description: str = "",
    active: bool = True,
    category_id: str | None = None,
    purchase_price: float | None = None,
    child_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": listing_id,
        "name": name,
        "productNumber": number,
        "stock": stock,
        "availableStock": stock,
        "active": active,
        "parentId": parent_id,
        "childCount": child_count,
        "description": description,
        "metaTitle": None,
        "metaDescription": None,
        "taxId": TAX_ID,
        "price": [_price_entry(price)] if price is not None else None,
        "purchasePrices": [_price_entry(purchase_price)] if purchase_price is not None else None,
        "categoryIds": [category_id] if category_id else [],
        "translated": {"name": name, "description": description},
        "createdAt": "2026-07-01T09:00:00.000+00:00",
    }


DEFAULT_SEED: list[dict[str, Any]] = [
    _product(
        SHIRT,
        "Claude Commerce T-Shirt",
        "CA-TSHIRT",
        29.99,
        0,
        description="Organic cotton T-shirt in three sizes.",
        category_id=CATEGORY_APPAREL,
        child_count=3,
    ),
    _product(SHIRT_S, "Claude Commerce T-Shirt — S", "CA-TSHIRT-S", 29.99, 4, parent_id=SHIRT),
    _product(SHIRT_M, "Claude Commerce T-Shirt — M", "CA-TSHIRT-M", 31.99, 12, parent_id=SHIRT),
    # L inherits the family price (``price`` is null on the child, as Shopware stores it).
    _product(SHIRT_L, "Claude Commerce T-Shirt — L", "CA-TSHIRT-L", None, 0, parent_id=SHIRT),
    _product(
        OIL,
        "Extra Virgin Olive Oil 500 ml",
        "CA-OIL",
        12.90,
        40,
        description="Cold-pressed olive oil with Grundpreis.",
        category_id=CATEGORY_FOOD,
        purchase_price=7.50,
    ),
    _product(
        CANDLE,
        "Soy Wax Candle",
        "CA-CANDLE",
        9.90,
        3,
        description="Hand-poured soy wax candle, 40 h burn time.",
    ),
    _product(
        POSTER,
        "Commerce Agents Poster A2",
        "CA-POSTER",
        14.00,
        15,
        active=False,
        description="Poster, A2, matte.",
    ),
]

DEFAULT_TAXES = [{"id": TAX_ID, "name": "Standard rate", "taxRate": TAX_RATE, "position": 1}]
DEFAULT_CURRENCIES = [
    {"id": EUR, "isoCode": "EUR", "name": "Euro", "factor": 1.0},
    {"id": USD_CURRENCY_ID, "isoCode": "USD", "name": "US-Dollar", "factor": 1.08},
]
DEFAULT_SALES_CHANNELS = [
    {"id": SALES_CHANNEL_ID, "name": "Storefront", "translated": {"name": "Storefront"}}
]
DEFAULT_CATEGORIES = [
    {"id": CATEGORY_APPAREL, "name": "Apparel", "translated": {"name": "Apparel"}},
    {"id": CATEGORY_FOOD, "name": "Food", "translated": {"name": "Food"}},
]


def seed_orders(
    products: list[dict[str, Any]], now: datetime, sales_channel_id: str = SALES_CHANNEL_ID
) -> list[dict[str, Any]]:
    """~30 orders over the last 60 days with the state mix the alert rules need: an
    open order stuck for a week, an in-progress order whose delivery never shipped, a
    failed payment, a cancelled order, a buyer comment, and seed-marker comments."""
    by_id = {p["id"]: p for p in products}
    sellable = [
        p for p in products if p["parentId"] is not None or (p["childCount"] == 0 and p["active"])
    ]
    sellable = [p for p in sellable if p["productNumber"] not in {"CA-CANDLE", "CA-TSHIRT-L"}]
    rng = random.Random(7)
    orders: list[dict[str, Any]] = []
    for index, offset in enumerate(_ORDER_DAY_OFFSETS):
        placed = (now - timedelta(days=offset, hours=index % 9 + 8)).replace(
            minute=0, second=0, microsecond=0
        )
        picks = rng.sample(sellable, k=min(len(sellable), 1 + index % 2))
        line_items = []
        total = 0.0
        for pick in picks:
            quantity = 1 + (index % 3 == 0)
            price = pick["price"] or by_id.get(pick["parentId"] or "", {}).get("price")
            gross = float(price[0]["gross"]) if price else 10.0
            line_items.append(
                {
                    "id": uuid.uuid4().hex,
                    "productId": pick["id"],
                    "label": pick["name"],
                    "quantity": quantity,
                    "unitPrice": gross,
                    "totalPrice": round(gross * quantity, 2),
                    "type": "product",
                }
            )
            total += gross * quantity
        order_state, payment_state, delivery_state = "completed", "paid", "shipped"
        comment = ""
        if index == 3:  # stuck open order, 2 days old
            order_state, payment_state, delivery_state = "open", "open", "open"
        if index == 7:  # open for six days — delayed
            order_state, payment_state, delivery_state = "open", "paid", "open"
        if index == 10:  # in progress, never shipped — delayed
            order_state, payment_state, delivery_state = "in_progress", "paid", "open"
        if index == 12:  # payment failed
            order_state, payment_state, delivery_state = "in_progress", "failed", "open"
        if index == 15:  # cancelled
            order_state, payment_state, delivery_state = "cancelled", "cancelled", "cancelled"
        if index == 1:
            comment = "Please leave the parcel with the neighbour if I am out."
        if index in {2, 5, 8}:
            comment = SEED_COMMENT_MARKER
        order_id = f"{index + 1:032x}"
        orders.append(
            {
                "id": order_id,
                "orderNumber": str(10001 + index),
                "orderDateTime": _iso(placed),
                "createdAt": _iso(placed),
                "amountTotal": round(total, 2),
                "amountNet": _net(total),
                "currencyId": EUR,
                "salesChannelId": sales_channel_id,
                "customerComment": comment,
                "affiliateCode": None,
                "stateMachineState": {"technicalName": order_state, "name": order_state},
                "transactions": [
                    {
                        "id": uuid.uuid4().hex,
                        "stateMachineState": {"technicalName": payment_state},
                    }
                ],
                "deliveries": [
                    {
                        "id": uuid.uuid4().hex,
                        "stateMachineState": {"technicalName": delivery_state},
                        "shippingDateEarliest": _iso(placed + timedelta(days=1)),
                    }
                ],
                "lineItems": [{**item, "orderId": order_id} for item in line_items],
                "orderCustomer": {
                    "firstName": "Dana",
                    "lastName": f"Buyer {index + 1}",
                    "email": f"buyer{index + 1}@example.com",
                    "customerNumber": f"C{1000 + index}",
                },
            }
        )
    return orders


class FakeAdmin:
    name = "fake"

    def __init__(
        self,
        products: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
        *,
        mode: str = "direct",
        now: datetime | None = None,
        sales_channel_id: str = SALES_CHANNEL_ID,
    ) -> None:
        self.mode = mode
        self.sales_channel_id = sales_channel_id
        self.now = now or datetime.now(UTC)
        seed = deepcopy(products or DEFAULT_SEED)
        self._tables: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in _ENTITIES}
        for row in seed:
            self._tables["product"][row["id"]] = row
        for name, rows in (
            ("tax", DEFAULT_TAXES),
            ("currency", DEFAULT_CURRENCIES),
            ("sales_channel", DEFAULT_SALES_CHANNELS),
            ("category", DEFAULT_CATEGORIES),
        ):
            for row in deepcopy(rows):
                if name == "sales_channel":
                    row["id"] = sales_channel_id
                self._tables[name][row["id"]] = row
        for order in deepcopy(
            orders if orders is not None else seed_orders(seed, self.now, sales_channel_id)
        ):
            self._tables["order"][order["id"]] = order
            for item in order.get("lineItems") or []:
                self._tables["order_line_item"][item["id"]] = {
                    **item,
                    "orderId": order["id"],
                    "order": {
                        "orderDateTime": order["orderDateTime"],
                        "stateMachineState": order["stateMachineState"],
                    },
                    "product": {
                        "categoryIds": self._tables["product"]
                        .get(item.get("productId") or "", {})
                        .get("categoryIds", []),
                        "parentId": self._tables["product"]
                        .get(item.get("productId") or "", {})
                        .get("parentId"),
                    },
                }
        self.calls: deque[AdminCall] = new_call_log()
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self._tool_schemas: dict[str, dict[str, Any]] | None = None
        #: Error text the next ``dry_run=False`` upsert returns instead of writing.
        self.fail_next_write: str | None = None
        #: Error text the next ``dry_run=True`` upsert returns (a rejected preview).
        self.fail_next_preview: str | None = None

    @classmethod
    def from_seed(cls, path: Path, *, sales_channel_id: str = SALES_CHANNEL_ID) -> FakeAdmin:
        """The local store: products from ``seed.json`` (defaults when absent) and the
        generated order history. ``sales_channel_id`` lets the fake answer to the id the
        environment names, so promotions staged locally bind to it."""
        channel = sales_channel_id or SALES_CHANNEL_ID
        if not path.exists():
            return cls(sales_channel_id=channel)
        raw = json.loads(path.read_text(encoding="utf-8"))
        products = raw.get("products")
        if not products:
            return cls(sales_channel_id=channel)
        return cls([_normalise_seed_product(row) for row in products], sales_channel_id=channel)

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------- AdminTransport

    async def search(
        self,
        entity: str,
        criteria: dict[str, Any],
        *,
        limit: int = 25,
        page: int = 1,
        term: str = "",
    ) -> SearchResult:
        self.calls.append(AdminCall("search", entity, deepcopy(criteria)))
        return self._search(entity, criteria, limit=limit, page=page, term=term)

    async def read(
        self, entity: str, entity_id: str, criteria: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append(AdminCall("read", entity, entity_id))
        return self._read(entity, entity_id, criteria or {})

    async def aggregate(
        self,
        entity: str,
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            AdminCall("aggregate", entity, {"aggregations": aggregations, "filters": filters or []})
        )
        return self._aggregate(entity, aggregations, filters or [])

    async def upsert(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]], *, dry_run: bool
    ) -> WriteResult:
        self.calls.append(AdminCall("upsert", entity, deepcopy(payload), dry_run))
        response = self._upsert(entity, payload, dry_run=dry_run)
        return _to_write_result(response, dry_run)

    async def delete(self, entity: str, ids: list[str], *, dry_run: bool) -> WriteResult:
        self.calls.append(AdminCall("delete", entity, list(ids), dry_run))
        return _to_write_result(self._delete(entity, ids, dry_run=dry_run), dry_run)

    async def order_state(
        self, order_number: str, *, transaction_action: str, dry_run: bool
    ) -> dict[str, Any]:
        arguments = {"orderNumber": order_number, "transactionAction": transaction_action}
        self.calls.append(AdminCall("state", "order_transaction", arguments, dry_run))
        response = self._order_state(order_number, transaction_action, dry_run=dry_run)
        if not response["success"]:
            raise AdminAPIError(
                f"shopware-order-state (order_transaction): {response['error']}",
                entity="order_transaction",
            )
        return response["data"]

    # ------------------------------------------------------------- test helpers

    def product(self, product_id: str) -> dict[str, Any]:
        return deepcopy(self._tables["product"][product_id])

    def payment_state(self, order_number: str) -> str | None:
        """The state of the order's first transaction; None for an unknown order."""
        for order in self._tables["order"].values():
            if order.get("orderNumber") == order_number:
                transactions = order.get("transactions") or [{}]
                return (transactions[0].get("stateMachineState") or {}).get("technicalName")
        return None

    def patch_product(self, product_id: str, **fields: Any) -> None:
        """Change a product behind the backend's back (what another admin user would do
        between staging and apply)."""
        row = self._tables["product"][product_id]
        row.update(deepcopy(fields))
        if "stock" in fields:
            row["availableStock"] = fields["stock"]

    def rows(self, entity: str) -> list[dict[str, Any]]:
        return deepcopy(list(self._tables[entity].values()))

    def set_orders(self, orders: list[dict[str, Any]]) -> None:
        self._tables["order"] = {}
        self._tables["order_line_item"] = {}
        for order in deepcopy(orders):
            self._tables["order"][order["id"]] = order

    # ------------------------------------------------------------- MCP tool layer

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        if self._tool_schemas is None:
            raw = json.loads(TOOLS_FIXTURE.read_text(encoding="utf-8"))
            self._tool_schemas = {tool["name"]: tool for tool in raw["tools"]}
        return self._tool_schemas

    def tool_list(self) -> list[dict[str, Any]]:
        return [deepcopy(tool) for tool in self.tool_schemas().values()]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """``(payload, is_error)`` as the live server would answer ``tools/call``; raises
        :class:`FakeToolError` for the cases the live server answers with a JSON-RPC
        error (unknown tool, unknown/invalid arguments, an aggregation it cannot run)."""
        self.tool_calls.append((name, deepcopy(arguments)))
        schema = self.tool_schemas().get(name)
        if schema is None:
            raise FakeToolError(-32602, f"Tool not found: {name}")
        properties = (schema.get("inputSchema") or {}).get("properties") or {}
        for key in arguments:
            if key not in properties:
                raise FakeToolError(-32602, f"Unknown argument: {key}")
        for key in (schema.get("inputSchema") or {}).get("required") or []:
            if key not in arguments:
                raise FakeToolError(-32602, f"Missing required argument: {key}")
        for key, value in arguments.items():
            expected = properties[key].get("type")
            if expected == "string" and not isinstance(value, str):
                raise FakeToolError(-32602, "Invalid type. Expected string")
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise FakeToolError(-32602, "Invalid type. Expected integer")
            if expected == "boolean" and not isinstance(value, bool):
                raise FakeToolError(-32602, "Invalid type. Expected boolean")
        try:
            return self._dispatch_tool(name, arguments), False
        except AdminAPIError as error:
            return {"success": False, "error": str(error)}, False

    def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entity = str(arguments.get("entity", ""))
        if name == "shopware-entity-search":
            criteria = _parse_json(arguments.get("criteria", "{}"), "criteria")
            result = self._search(
                entity,
                criteria,
                limit=int(arguments.get("limit", 25)),
                page=int(arguments.get("page", 1)),
                term=str(arguments.get("term", "")),
            )
            return {
                "success": True,
                "data": result.rows,
                "_meta": {
                    "total": result.total,
                    "page": int(arguments.get("page", 1)),
                    "limit": int(arguments.get("limit", 25)),
                },
            }
        if name == "shopware-entity-read":
            criteria = _parse_json(arguments.get("criteria", "{}"), "criteria")
            row = self._read(entity, str(arguments["id"]), criteria)
            if row is None:
                return {
                    "success": False,
                    "error": f'Entity "{entity}" with ID "{arguments["id"]}" not found.',
                }
            return {"success": True, "data": row, "_meta": {}}
        if name == "shopware-entity-aggregate":
            aggregations = _parse_json(arguments["aggregations"], "aggregations")
            filters = _parse_json(arguments.get("filters", "[]"), "filters")
            if not isinstance(aggregations, list):
                return {
                    "success": False,
                    "error": "aggregations must be a JSON array of aggregation definitions.",
                }
            try:
                result = self._aggregate(entity, aggregations, filters)
            except ValueError as error:
                raise FakeToolError(-32603, "Error while executing tool") from error
            return {"success": True, "data": {"aggregations": result}}
        if name == "shopware-entity-upsert":
            payload = _parse_json(arguments["payload"], "payload")
            return self._upsert(entity, payload, dry_run=bool(arguments.get("dryRun", True)))
        if name == "shopware-entity-delete":
            ids = _parse_json(arguments["ids"], "ids")
            return self._delete(entity, ids, dry_run=bool(arguments.get("dryRun", True)))
        if name == "shopware-order-state":
            return self._order_state(
                str(arguments.get("orderNumber", "")),
                str(arguments.get("transactionAction", "")),
                dry_run=bool(arguments.get("dryRun", True)),
            )
        if name == "shopware-entity-schema":
            rows = list(self._tables.get(entity, {}).values())
            fields = sorted({key for row in rows for key in row})
            return {
                "success": True,
                "data": {"entity": entity, "fields": [{"name": f} for f in fields]},
            }
        raise FakeToolError(-32601, f"Tool {name} is not implemented by the fake")

    # ------------------------------------------------------------- order state

    def _order_state(self, order_number: str, action: str, *, dry_run: bool) -> dict[str, Any]:
        """The live tool's answer (``OrderStateTool``): per transaction, a dry run lists the
        ``from`` state, whether ``action`` is valid there, and the transitions available; a
        real run moves every transaction the action is valid on and answers ``from``/``to``/
        ``executed`` (or ``executed: false`` with a ``note``)."""
        order = next(
            (o for o in self._tables["order"].values() if o.get("orderNumber") == order_number),
            None,
        )
        if order is None:
            return {"success": False, "error": "Order not found."}
        transactions = []
        for transaction in order.get("transactions") or []:
            state = transaction.setdefault("stateMachineState", {})
            current = str(state.get("technicalName") or "")
            available = _TRANSACTION_TRANSITIONS.get(current, {})
            valid = action in available
            base = {"id": transaction.get("id"), "from": current, "action": action}
            if dry_run:
                transactions.append(
                    {
                        **base,
                        "actionValid": valid,
                        "availableTransitions": [
                            {"actionName": name, "toStateName": to}
                            for name, to in available.items()
                        ],
                    }
                )
            elif valid:
                state["technicalName"] = available[action]
                transactions.append({**base, "to": available[action], "executed": True})
            else:
                note = f'Transition "{action}" not available from state "{current}"'
                transactions.append({**base, "executed": False, "note": note})
        return {
            "success": True,
            "data": {
                "orderId": order["id"],
                "orderNumber": order_number,
                "transactions": transactions,
            },
            "_meta": {"dryRun": dry_run},
        }

    # ------------------------------------------------------------- search / read

    def _table(self, entity: str) -> dict[str, dict[str, Any]]:
        if entity not in self._tables:
            raise AdminAPIError(
                f'Entity "{entity}" not found. Use the shopware://entities resource for '
                "available entity names.",
                entity=entity,
            )
        return self._tables[entity]

    def _search(
        self, entity: str, criteria: dict[str, Any], *, limit: int, page: int, term: str
    ) -> SearchResult:
        rows = list(self._table(entity).values())
        filters = list(criteria.get("filter") or [])
        rows = [row for row in rows if all(_matches(row, f) for f in filters)]
        if term:
            needle = term.lower()
            rows = [row for row in rows if needle in json.dumps(row, default=str).lower()]
        ids = criteria.get("ids")
        if ids:
            wanted = set(ids)
            rows = [row for row in rows if row.get("id") in wanted]
        for sort in reversed(list(criteria.get("sort") or [])):
            descending = str(sort.get("order", "ASC")).upper() == "DESC"
            rows.sort(
                key=lambda r: _sort_key(_first(_values(r, sort["field"]))), reverse=descending
            )
        total = len(rows)
        start = max(0, (int(page) - 1) * int(limit))
        page_rows = rows[start : start + int(limit)]
        return SearchResult(
            rows=[self._project(entity, row, criteria) for row in page_rows], total=total
        )

    def _read(self, entity: str, entity_id: str, criteria: dict[str, Any]) -> dict[str, Any] | None:
        row = self._table(entity).get(entity_id)
        return None if row is None else self._project(entity, row, criteria)

    def _project(
        self, entity: str, row: dict[str, Any], criteria: dict[str, Any]
    ) -> dict[str, Any]:
        out = deepcopy(row)
        associations = criteria.get("associations") or {}
        if entity == "order":
            for key in _ORDER_ASSOCIATIONS:
                if key not in associations:
                    out.pop(key, None)
        if entity == "order_line_item":
            out.pop("order", None)
            out.pop("product", None)
        if entity == "product":
            if "tax" in associations:
                out["tax"] = deepcopy(self._tables["tax"].get(row.get("taxId") or ""))
            if "children" in associations:
                out["children"] = [
                    deepcopy(c)
                    for c in self._tables["product"].values()
                    if c.get("parentId") == row["id"]
                ]
            if "categories" in associations:
                out["categories"] = [
                    deepcopy(self._tables["category"][cid])
                    for cid in row.get("categoryIds") or []
                    if cid in self._tables["category"]
                ]
        if entity == "promotion":
            if "discounts" in associations:
                discounts = [
                    deepcopy(d)
                    for d in self._tables["promotion_discount"].values()
                    if d.get("promotionId") == row["id"]
                ]
                for discount in discounts:
                    discount["discountRules"] = [
                        deepcopy(self._tables["rule"][m["ruleId"]])
                        for m in self._tables["promotion_discount_rule"].values()
                        if m.get("discountId") == discount["id"]
                        and m["ruleId"] in self._tables["rule"]
                    ]
                out["discounts"] = discounts
            if "salesChannels" in associations:
                out["salesChannels"] = [
                    deepcopy(s)
                    for s in self._tables["promotion_sales_channel"].values()
                    if s.get("promotionId") == row["id"]
                ]
        if entity == "rule" and "conditions" in associations:
            out["conditions"] = [
                deepcopy(c)
                for c in self._tables["rule_condition"].values()
                if c.get("ruleId") == row["id"]
            ]
        includes = (criteria.get("includes") or {}).get(entity)
        if includes:
            keep = set(includes) | {"id"}
            out = {k: v for k, v in out.items() if k in keep}
        return out

    # ------------------------------------------------------------- aggregate

    def _aggregate(
        self, entity: str, aggregations: list[dict[str, Any]], filters: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rows = [r for r in self._table(entity).values() if all(_matches(r, f) for f in filters)]
        return {str(agg["name"]): _run_aggregation(agg, rows) for agg in aggregations}

    # ------------------------------------------------------------- writes

    def _upsert(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]], *, dry_run: bool
    ) -> dict[str, Any]:
        if entity not in self._tables:
            return {
                "success": False,
                "error": f'Entity "{entity}" not found. Use the shopware://entities resource '
                "for available entity names.",
            }
        rows = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(r, dict) for r in rows):
            return {"success": False, "error": "payload must be a JSON object or a list of objects"}
        if self.fail_next_write and not dry_run:
            error, self.fail_next_write = self.fail_next_write, None
            return {"success": False, "error": error}
        if self.fail_next_preview and dry_run:
            error, self.fail_next_preview = self.fail_next_preview, None
            return {"success": False, "error": error}
        errors = self._validate(entity, rows)
        if errors:
            numbered = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(errors))
            return {
                "success": False,
                "error": f"There are {len(errors)} error(s) while writing data.\n\n{numbered}",
            }
        rows = deepcopy(rows)
        for row in rows:
            row.setdefault("id", uuid.uuid4().hex)
        written = self._written(entity, rows)
        if not dry_run:
            self._persist(entity, rows)
        return {"success": True, "data": written, "_meta": {"dryRun": dry_run}}

    def _validate(self, entity: str, rows: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for index, row in enumerate(rows):
            prefix = f"/{index}"
            if entity == "product":
                errors.extend(self._validate_product(prefix, row))
            elif entity == "promotion":
                errors.extend(self._validate_promotion(prefix, row))
            elif entity == "rule":
                errors.extend(
                    _validate_rule(prefix, row, existing=row.get("id") in self._tables["rule"])
                )
            else:
                if "id" in row and not isinstance(row["id"], str):
                    errors.append(f"[{prefix}/id] This value should be of type string.")
        return errors

    def _validate_product(self, prefix: str, row: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        exists = row.get("id") in self._tables["product"]
        if not exists:
            for key in _PRODUCT_REQUIRED_ON_CREATE:
                if row.get(key) in (None, ""):
                    errors.append(f"[{prefix}/{key}] This value should not be blank.")
        if "stock" in row and (isinstance(row["stock"], bool) or not isinstance(row["stock"], int)):
            errors.append(f"[{prefix}/stock] This value should be of type int.")
        if "active" in row and not isinstance(row["active"], bool):
            errors.append(f"[{prefix}/active] This value should be of type bool.")
        if "name" in row and row["name"] is not None and not isinstance(row["name"], str):
            errors.append(f"[{prefix}/name] This value should be of type string.")
        if "price" in row and row["price"] is not None:
            price = row["price"]
            if not isinstance(price, list):
                errors.append(f"[{prefix}/price] This value should be of type array.")
            else:
                for i, entry in enumerate(price):
                    if not isinstance(entry, dict):
                        errors.append(f"[{prefix}/price/{i}] This value should be of type array.")
                        continue
                    for key in ("currencyId", "gross", "net"):
                        if key not in entry:
                            errors.append(
                                f"[{prefix}/price/{i}/{key}] This value should not be blank."
                            )
                    for key in ("gross", "net"):
                        if key in entry and (
                            isinstance(entry[key], bool) or not isinstance(entry[key], int | float)
                        ):
                            errors.append(
                                f"[{prefix}/price/{i}/{key}] This value should be of type float."
                            )
                    currency = entry.get("currencyId")
                    if currency and currency not in self._tables["currency"]:
                        errors.append(
                            f"[{prefix}/price/{i}/currencyId] Currency {currency} does not exist."
                        )
        return errors

    def _validate_promotion(self, prefix: str, row: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        exists = row.get("id") in self._tables["promotion"]
        if not exists and not row.get("name"):
            errors.append(
                f"[{prefix}/translations/{LANGUAGE_ID}/name] This value should not be blank."
            )
        if "active" in row and not isinstance(row["active"], bool):
            errors.append(f"[{prefix}/active] This value should be of type bool.")
        for key in ("validFrom", "validUntil"):
            if row.get(key) is not None and not _is_datetime(row[key]):
                errors.append(f"[{prefix}/{key}] This value is not a valid datetime.")
        for i, channel in enumerate(row.get("salesChannels") or []):
            if not isinstance(channel, dict) or not channel.get("salesChannelId"):
                errors.append(
                    f"[{prefix}/salesChannels/{i}/salesChannelId] This value should not be blank."
                )
            elif channel["salesChannelId"] not in self._tables["sales_channel"]:
                errors.append(
                    "An exception occurred while executing a query: SQLSTATE[23000]: Integrity "
                    "constraint violation: 1452 Cannot add or update a child row: a foreign key "
                    "constraint fails (`promotion_sales_channel`, `fk.promotion_sales_channel.sales_channel_id`)"
                )
        for i, discount in enumerate(row.get("discounts") or []):
            dprefix = f"{prefix}/discounts/{i}"
            if not isinstance(discount, dict):
                errors.append(f"[{dprefix}] This value should be of type array.")
                continue
            for key in ("scope", "type", "value"):
                if discount.get(key) in (None, ""):
                    errors.append(f"[{dprefix}/{key}] This value should not be blank.")
            if discount.get("scope") and discount["scope"] not in _DISCOUNT_SCOPES:
                errors.append(f"[{dprefix}/scope] The value you selected is not a valid choice.")
            if discount.get("type") and discount["type"] not in _DISCOUNT_TYPES:
                errors.append(f"[{dprefix}/type] The value you selected is not a valid choice.")
            if "value" in discount and (
                isinstance(discount["value"], bool)
                or not isinstance(discount["value"], int | float)
            ):
                errors.append(f"[{dprefix}/value] This value should be of type float.")
            for j, rule in enumerate(discount.get("discountRules") or []):
                if not isinstance(rule, dict) or not rule.get("id"):
                    errors.append(
                        f"[{dprefix}/discountRules/{j}/id] This value should not be blank."
                    )
                    continue
                if rule["id"] in self._tables["rule"]:
                    continue
                if len(rule) == 1:  # a bare reference to a rule that does not exist yet
                    errors.append(
                        "An exception occurred while executing a query: SQLSTATE[23000]: Integrity "
                        "constraint violation: 1452 Cannot add or update a child row: a foreign key "
                        "constraint fails (`promotion_discount_rule`, CONSTRAINT "
                        "`fk.promotion_discount_rule.rule_id` FOREIGN KEY (`rule_id`) REFERENCES `rule` (`id`) "
                        "ON DELETE CASCADE)"
                    )
                else:
                    errors.extend(
                        _validate_rule(f"{dprefix}/discountRules/{j}", rule, existing=False)
                    )
        return errors

    def _written(self, entity: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [r["id"] for r in rows]
        if entity == "product":
            return [
                {"entity": "product", "ids": ids, "operation": "upsert"},
                {
                    "entity": "product_translation",
                    "ids": [{"productId": i, "languageId": LANGUAGE_ID} for i in ids],
                    "operation": "upsert",
                },
            ]
        if entity == "rule":
            out = [{"entity": "rule", "ids": ids, "operation": "upsert"}]
            conditions = [uuid.uuid4().hex for r in rows for _ in r.get("conditions") or []]
            if conditions:
                out.append({"entity": "rule_condition", "ids": conditions, "operation": "upsert"})
            return out
        if entity == "promotion":
            new_rules: list[str] = []
            conditions: list[str] = []
            discounts: list[str] = []
            mappings: list[dict[str, str]] = []
            channels: list[str] = []
            for row in rows:
                channels.extend(uuid.uuid4().hex for _ in row.get("salesChannels") or [])
                for discount in row.get("discounts") or []:
                    discount_id = discount.get("id") or uuid.uuid4().hex
                    discounts.append(discount_id)
                    for rule in discount.get("discountRules") or []:
                        if rule["id"] not in self._tables["rule"]:
                            new_rules.append(rule["id"])
                            conditions.extend(
                                uuid.uuid4().hex for _ in rule.get("conditions") or []
                            )
                        mappings.append({"discountId": discount_id, "ruleId": rule["id"]})
            out: list[dict[str, Any]] = []
            if new_rules:
                out.append({"entity": "rule", "ids": new_rules, "operation": "upsert"})
            out.append({"entity": "promotion", "ids": ids, "operation": "upsert"})
            out.append(
                {
                    "entity": "promotion_translation",
                    "ids": [{"promotionId": i, "languageId": LANGUAGE_ID} for i in ids],
                    "operation": "upsert",
                }
            )
            if channels:
                out.append(
                    {"entity": "promotion_sales_channel", "ids": channels, "operation": "upsert"}
                )
            if conditions:
                out.append({"entity": "rule_condition", "ids": conditions, "operation": "upsert"})
            if discounts:
                out.append(
                    {"entity": "promotion_discount", "ids": discounts, "operation": "upsert"}
                )
            if mappings:
                out.append(
                    {"entity": "promotion_discount_rule", "ids": mappings, "operation": "upsert"}
                )
            return out
        return [{"entity": entity, "ids": ids, "operation": "upsert"}]

    def _persist(self, entity: str, rows: list[dict[str, Any]]) -> None:
        table = self._tables[entity]
        for row in rows:
            if entity == "product":
                current = table.setdefault(row["id"], {"id": row["id"], "translated": {}})
                for key, value in row.items():
                    current[key] = value
                if "stock" in row:
                    current["availableStock"] = row["stock"]
                for key in ("name", "description", "metaTitle", "metaDescription"):
                    if key in row:
                        current.setdefault("translated", {})[key] = row[key]
                continue
            if entity == "promotion":
                self._persist_promotion(row)
                continue
            if entity == "rule":
                self._persist_rule(row)
                continue
            table[row["id"]] = {**table.get(row["id"], {}), **row}

    def _persist_rule(self, rule: dict[str, Any]) -> None:
        conditions = rule.pop("conditions", None) or []
        self._tables["rule"][rule["id"]] = {**self._tables["rule"].get(rule["id"], {}), **rule}
        for condition in conditions:
            condition_id = condition.get("id") or uuid.uuid4().hex
            self._tables["rule_condition"][condition_id] = {
                **condition,
                "id": condition_id,
                "ruleId": rule["id"],
            }

    def _persist_promotion(self, row: dict[str, Any]) -> None:
        promotion = deepcopy(row)
        discounts = promotion.pop("discounts", None) or []
        channels = promotion.pop("salesChannels", None) or []
        promotion.setdefault("translated", {})["name"] = promotion.get("name")
        self._tables["promotion"][promotion["id"]] = {
            **self._tables["promotion"].get(promotion["id"], {}),
            **promotion,
        }
        for channel in channels:
            channel_id = channel.get("id") or uuid.uuid4().hex
            self._tables["promotion_sales_channel"][channel_id] = {
                **channel,
                "id": channel_id,
                "promotionId": promotion["id"],
            }
        for discount in discounts:
            discount = deepcopy(discount)
            rules = discount.pop("discountRules", None) or []
            discount_id = discount.get("id") or uuid.uuid4().hex
            self._tables["promotion_discount"][discount_id] = {
                **discount,
                "id": discount_id,
                "promotionId": promotion["id"],
            }
            for rule in rules:
                if len(rule) > 1:
                    self._persist_rule(deepcopy(rule))
                mapping_id = f"{discount_id}:{rule['id']}"
                self._tables["promotion_discount_rule"][mapping_id] = {
                    "discountId": discount_id,
                    "ruleId": rule["id"],
                }

    def _delete(self, entity: str, ids: list[str], *, dry_run: bool) -> dict[str, Any]:
        if entity not in self._tables:
            return {
                "success": False,
                "error": f'Entity "{entity}" not found. Use the shopware://entities resource '
                "for available entity names.",
            }
        table = self._tables[entity]
        present = [i for i in ids if i in table]
        if not dry_run:
            for entity_id in present:
                table.pop(entity_id, None)
                if entity == "promotion":
                    self._cascade("promotion_discount", "promotionId", entity_id)
                    self._cascade("promotion_sales_channel", "promotionId", entity_id)
                if entity == "rule":
                    self._cascade("rule_condition", "ruleId", entity_id)
                    self._cascade("promotion_discount_rule", "ruleId", entity_id)
        data = [{"entity": entity, "ids": present, "operation": "delete"}] if present else []
        return {"success": True, "data": data, "_meta": {"dryRun": dry_run}}

    def _cascade(self, entity: str, key: str, value: str) -> None:
        table = self._tables[entity]
        for row_id in [i for i, row in table.items() if row.get(key) == value]:
            child = table.pop(row_id)
            if entity == "promotion_discount":
                self._cascade("promotion_discount_rule", "discountId", child["id"])


# ------------------------------------------------------------------ helpers


def _normalise_seed_product(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("price")
    gross = float(price[0]["gross"]) if isinstance(price, list) and price else None
    return _product(
        row["id"],
        row.get("name") or row["id"],
        row.get("productNumber") or "",
        gross,
        int(row.get("stock") or 0),
        parent_id=row.get("parentId"),
        description=row.get("description") or "",
        active=bool(row.get("active", True)),
        child_count=int(row.get("childCount") or 0),
    )


def _to_write_result(response: dict[str, Any], dry_run: bool) -> WriteResult:
    if not response.get("success"):
        return WriteResult(success=False, dry_run=dry_run, error=str(response.get("error")))
    return WriteResult(success=True, written=list(response.get("data") or []), dry_run=dry_run)


def _parse_json(raw: Any, name: str) -> Any:
    if not isinstance(raw, str):
        raise FakeToolError(-32602, "Invalid type. Expected string")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as error:
        raise AdminAPIError(f"Invalid JSON in {name}: {error}") from error


def _is_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_rule(prefix: str, rule: dict[str, Any], *, existing: bool) -> list[str]:
    errors: list[str] = []
    if not existing and not rule.get("name"):
        errors.append(f"[{prefix}/name] This value should not be blank.")
    if "priority" in rule and (
        isinstance(rule["priority"], bool) or not isinstance(rule["priority"], int)
    ):
        errors.append(f"[{prefix}/priority] This value should be of type int.")
    for i, condition in enumerate(rule.get("conditions") or []):
        if not isinstance(condition, dict) or not condition.get("type"):
            errors.append(f"[{prefix}/conditions/{i}/type] This value should not be blank.")
    return errors


def _values(row: Any, path: str) -> list[Any]:
    """Every value at a dotted path; lists along the way fan out (``transactions.
    stateMachineState.technicalName`` yields one value per transaction)."""
    current: list[Any] = [row]
    for part in path.split("."):
        next_values: list[Any] = []
        for item in current:
            if isinstance(item, list):
                next_values.extend(i.get(part) for i in item if isinstance(i, dict))
            elif isinstance(item, dict):
                next_values.append(item.get(part))
        current = next_values
    flat: list[Any] = []
    for value in current:
        if isinstance(value, list):
            flat.extend(value)
        else:
            flat.append(value)
    return flat


def _first(values: list[Any]) -> Any:
    return values[0] if values else None


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (1, 0)
    if isinstance(value, int | float):
        return (0, value)
    return (0, str(value))


def _same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    if isinstance(left, int | float) and isinstance(right, int | float):
        return float(left) == float(right)
    return str(left) == str(right)


def _compare(value: Any, bound: Any) -> int | None:
    if value is None or bound is None:
        return None
    try:
        return (float(value) > float(bound)) - (float(value) < float(bound))
    except (TypeError, ValueError):
        left, right = _normalise_moment(value), _normalise_moment(bound)
        return (left > right) - (left < right)


def _normalise_moment(value: Any) -> str:
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return text


def _matches(row: dict[str, Any], flt: dict[str, Any]) -> bool:
    kind = flt.get("type")
    if kind == "multi":
        queries = flt.get("queries") or []
        if str(flt.get("operator", "and")).lower() == "or":
            return any(_matches(row, q) for q in queries)
        return all(_matches(row, q) for q in queries)
    if kind == "not":
        queries = flt.get("queries") or []
        if str(flt.get("operator", "and")).lower() == "or":
            return not any(_matches(row, q) for q in queries)
        return not all(_matches(row, q) for q in queries)
    values = _values(row, str(flt.get("field")))
    if kind == "equals":
        return any(_same(v, flt.get("value")) for v in values)
    if kind == "equalsAny":
        wanted = flt.get("value")
        if isinstance(wanted, str):
            wanted = wanted.split("|")
        return any(any(_same(v, w) for w in wanted or []) for v in values)
    if kind == "contains":
        needle = str(flt.get("value", "")).lower()
        return any(v is not None and needle in str(v).lower() for v in values)
    if kind == "prefix":
        return any(
            v is not None and str(v).lower().startswith(str(flt.get("value", "")).lower())
            for v in values
        )
    if kind == "range":
        parameters = flt.get("parameters") or {}
        for value in values:
            ok = True
            for op, bound in parameters.items():
                cmp = _compare(value, bound)
                if cmp is None:
                    ok = False
                    break
                if (
                    (op == "gte" and cmp < 0)
                    or (op == "gt" and cmp <= 0)
                    or (op == "lte" and cmp > 0)
                    or (op == "lt" and cmp >= 0)
                ):
                    ok = False
                    break
            if ok:
                return True
        return False
    raise ValueError(f"unsupported filter type {kind!r}")


def _numbers(rows: list[dict[str, Any]], field: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        for value in _values(row, field):
            if value is None or isinstance(value, bool):
                continue
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
    return out


def _histogram_key(value: Any, interval: str) -> str | None:
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
    if interval == "minute":
        return moment.strftime("%Y-%m-%d %H:%M:00")
    if interval == "hour":
        return moment.strftime("%Y-%m-%d %H:00:00")
    if interval == "day":
        return moment.strftime("%Y-%m-%d 00:00:00")
    if interval == "week":
        return moment.strftime("%G %V")
    if interval == "month":
        return moment.strftime("%Y-%m-01 00:00:00")
    if interval == "quarter":
        return f"{moment.year} {(moment.month - 1) // 3 + 1}"
    if interval == "year":
        return moment.strftime("%Y-01-01 00:00:00")
    raise ValueError(f"unsupported histogram interval {interval!r}")


def _run_aggregation(agg: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    kind = agg.get("type")
    field = str(agg.get("field", ""))
    if kind == "count":
        return {"count": len(_values_present(rows, field))}
    if kind in {"sum", "avg", "min", "max"}:
        numbers = _numbers(rows, field)
        if kind == "sum":
            return {"sum": round(sum(numbers), 2)}
        if kind == "avg":
            return {"avg": round(sum(numbers) / len(numbers), 2) if numbers else 0}
        if kind == "min":
            return {"min": min(numbers) if numbers else None}
        return {"max": max(numbers) if numbers else None}
    if kind in {"terms", "histogram"}:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            for value in _values(row, field):
                key = (
                    _histogram_key(value, str(agg.get("interval", "day")))
                    if kind == "histogram"
                    else (None if value is None else str(value))
                )
                if key is None:
                    continue
                groups.setdefault(key, []).append(row)
        nested = agg.get("aggregation")
        buckets = []
        for key in sorted(groups):
            bucket: dict[str, Any] = {"key": key, "count": len(groups[key])}
            if isinstance(nested, dict) and nested.get("name"):
                bucket[str(nested["name"])] = {
                    "name": nested["name"],
                    **_run_aggregation(nested, groups[key]),
                }
            buckets.append(bucket)
        return {"buckets": buckets}
    raise ValueError(f"unsupported aggregation type {kind!r}")


def _values_present(rows: list[dict[str, Any]], field: str) -> list[Any]:
    return [v for row in rows for v in _values(row, field) if v is not None]
