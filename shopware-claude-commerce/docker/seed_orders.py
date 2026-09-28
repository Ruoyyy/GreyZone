#!/usr/bin/env python3
"""Seed ~40 backdated demo orders (last 60 days) so the merchant agent has history.

    python docker/seed_orders.py --shop-url http://localhost:8080 [--count 40]

Each order is a real Store API guest checkout (register guest → add line item → place
order) so totals, taxes, deliveries and transactions are consistent; the Admin API then
backdates ``orderDateTime`` and drives the state machines. Marker: ``customerComment =
"commerce-agents-seed"`` — the script only creates what is missing up to ``--count`` and
skips entirely once enough seeded orders exist.

State mix (technical names) for the merchant issues detector:

* order ``completed`` / transaction ``paid`` / delivery ``shipped`` — the bulk
* order ``open`` / transaction ``open`` / delivery ``open`` — 4, older than 3 days
* order ``open`` / transaction ``failed`` — 2
* order ``cancelled`` / transaction ``cancelled`` — 2
* order ``in_progress`` / transaction ``paid`` / delivery ``open`` (unshipped) — 3
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from _bootstrap_lib import HTTP_TIMEOUT_SECONDS, AdminApi, AdminApiError

SEED_MARKER = "commerce-agents-seed"
DEFAULT_ORDER_COUNT = 40
HISTORY_DAYS = 60
RANDOM_SEED = 20260903
CONTEXT_TOKEN_HEADER = "sw-context-token"
ACCESS_KEY_HEADER = "sw-access-key"
PAYMENT_METHOD_NAMES = ("Cash on delivery", "Invoice")
SEEDED_LINE_ITEM_NUMBERS = (
    "CA-TSHIRT-S",
    "CA-TSHIRT-M",
    "CA-OIL",
    "SWDEMO10001",
    "SWDEMO10006",
    "SWDEMO100013",
)

FIRST_NAMES = (
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Emma",
    "Felix",
    "Greta",
    "Hannes",
    "Ida",
    "Jonas",
    "Klara",
    "Leon",
)
LAST_NAMES = (
    "Schmidt",
    "Müller",
    "Weber",
    "Fischer",
    "Wagner",
    "Becker",
    "Hoffmann",
    "Koch",
    "Richter",
    "Wolf",
)
CITIES = (
    ("10115", "Berlin"),
    ("20095", "Hamburg"),
    ("80331", "München"),
    ("50667", "Köln"),
    ("60311", "Frankfurt"),
)


@dataclass(frozen=True)
class OrderSpec:
    kind: str  # completed | open_old | payment_failed | cancelled | in_progress
    days_ago: float


def build_plan(count: int, rng: random.Random) -> list[OrderSpec]:
    specials = (
        [OrderSpec("open_old", rng.uniform(4, 12)) for _ in range(4)]
        + [OrderSpec("payment_failed", rng.uniform(0.5, 6)) for _ in range(2)]
        + [OrderSpec("cancelled", rng.uniform(2, 40)) for _ in range(2)]
        + [OrderSpec("in_progress", rng.uniform(0.5, 3)) for _ in range(3)]
    )
    completed = [
        OrderSpec("completed", rng.uniform(1, HISTORY_DAYS))
        for _ in range(max(count - len(specials), 0))
    ]
    plan = (specials + completed)[:count]
    rng.shuffle(plan)
    return plan


# --------------------------------------------------------------------------- Store API


class StoreApi:
    def __init__(self, shop_url: str, access_key: str) -> None:
        self.shop_url = shop_url.rstrip("/")
        self.access_key = access_key

    def request(
        self, method: str, path: str, payload: Any = None, *, context_token: str | None = None
    ) -> tuple[dict[str, Any], str | None]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            ACCESS_KEY_HEADER: self.access_key,
        }
        if context_token:
            headers[CONTEXT_TOKEN_HEADER] = context_token
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.shop_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read()
                token = response.headers.get(CONTEXT_TOKEN_HEADER)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"{method} {path} -> {error.code}: {detail}") from error
        return (json.loads(body) if body else {}), token


def place_guest_order(
    store: StoreApi,
    *,
    salutation_id: str,
    country_id: str,
    payment_method_id: str,
    product_id: str,
    quantity: int,
    index: int,
    rng: random.Random,
) -> dict[str, Any]:
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    zipcode, city = rng.choice(CITIES)
    _, token = store.request(
        "POST",
        "/store-api/account/register",
        {
            "guest": True,
            "salutationId": salutation_id,
            "firstName": first,
            "lastName": last,
            "email": f"seed-{index:03d}-{first.lower()}@example.com",
            "storefrontUrl": store.shop_url,
            "acceptedDataProtection": True,
            "billingAddress": {
                "salutationId": salutation_id,
                "firstName": first,
                "lastName": last,
                "street": f"Musterstraße {rng.randint(1, 120)}",
                "zipcode": zipcode,
                "city": city,
                "countryId": country_id,
            },
        },
    )
    if not token:
        raise RuntimeError("guest registration returned no context token")
    store.request(
        "PATCH", "/store-api/context", {"paymentMethodId": payment_method_id}, context_token=token
    )
    store.request(
        "POST",
        "/store-api/checkout/cart/line-item",
        {"items": [{"type": "product", "referencedId": product_id, "quantity": quantity}]},
        context_token=token,
    )
    order, _ = store.request(
        "POST",
        "/store-api/checkout/order",
        {"customerComment": SEED_MARKER},
        context_token=token,
    )
    if not order.get("id"):
        raise RuntimeError(f"checkout/order returned no id: {json.dumps(order)[:300]}")
    return order


# --------------------------------------------------------------------------- Admin API


def transition(api: AdminApi, entity: str, entity_id: str, name: str) -> None:
    api.request("POST", f"/api/_action/{entity}/{entity_id}/state/{name}", {})


def apply_state(api: AdminApi, order: dict[str, Any], kind: str) -> None:
    order_id = str(order["id"])
    transaction_id = str((order.get("transactions") or [{}])[0].get("id") or "")
    delivery_id = str((order.get("deliveries") or [{}])[0].get("id") or "")
    if kind == "completed":
        transition(api, "order_transaction", transaction_id, "paid")
        transition(api, "order", order_id, "process")
        if delivery_id:
            transition(api, "order_delivery", delivery_id, "ship")
        transition(api, "order", order_id, "complete")
    elif kind == "payment_failed":
        transition(api, "order_transaction", transaction_id, "fail")
    elif kind == "cancelled":
        transition(api, "order_transaction", transaction_id, "cancel")
        transition(api, "order", order_id, "cancel")
    elif kind == "in_progress":
        transition(api, "order_transaction", transaction_id, "paid")
        transition(api, "order", order_id, "process")
    # open_old: leave order/transaction/delivery in their initial "open" states


def backdate(api: AdminApi, order_id: str, when: datetime) -> None:
    api.request(
        "PATCH",
        f"/api/order/{order_id}",
        {"orderDateTime": when.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")},
    )


def seeded_order_count(api: AdminApi) -> int:
    return api.count(
        "order", [{"type": "equals", "field": "customerComment", "value": SEED_MARKER}]
    )


def lookup_products(api: AdminApi) -> list[str]:
    rows = api.search(
        "product",
        {
            "limit": 50,
            "filter": [
                {
                    "type": "equalsAny",
                    "field": "productNumber",
                    "value": list(SEEDED_LINE_ITEM_NUMBERS),
                },
                {"type": "equals", "field": "active", "value": True},
                {"type": "range", "field": "stock", "parameters": {"gt": 0}},
            ],
        },
    )
    ids = [str(row["id"]) for row in rows]
    if not ids:
        raise RuntimeError("no sellable seeded/demo products found — run seed_catalog.py first")
    return ids


def lookup_payment_methods(api: AdminApi, channel_id: str) -> list[str]:
    rows = api.search(
        "payment-method",
        {
            "limit": 10,
            "filter": [
                {"type": "equalsAny", "field": "name", "value": list(PAYMENT_METHOD_NAMES)},
                {"type": "equals", "field": "active", "value": True},
                {"type": "equals", "field": "salesChannels.id", "value": channel_id},
            ],
        },
    )
    ids = [str(row["id"]) for row in rows]
    if not ids:
        raise RuntimeError(
            "neither 'Cash on delivery' nor 'Invoice' is active on the Storefront channel"
        )
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--count", type=int, default=DEFAULT_ORDER_COUNT)
    args = parser.parse_args()
    shop_url = args.shop_url.rstrip("/")

    api = AdminApi(shop_url)
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
        existing = seeded_order_count(api)
        if existing >= args.count:
            print(f"orders: {existing} seeded orders present (>= {args.count}) — nothing to do")
            return 0

        store = StoreApi(shop_url, str(channel["accessKey"]))
        country_id = str(channel["countryId"])
        salutation_id = str(api.search("salutation", {"limit": 1})[0]["id"])
        payment_methods = lookup_payment_methods(api, str(channel["id"]))
        products = lookup_products(api)

        rng = random.Random(RANDOM_SEED)
        plan = build_plan(args.count, rng)[existing:]
        now = datetime.now(tz=UTC)
        counts: dict[str, int] = {}
        for offset, spec in enumerate(plan):
            index = existing + offset
            order = place_guest_order(
                store,
                salutation_id=salutation_id,
                country_id=country_id,
                payment_method_id=payment_methods[index % len(payment_methods)],
                product_id=rng.choice(products),
                quantity=rng.choice((1, 1, 1, 2, 3)),
                index=index,
                rng=rng,
            )
            backdate(api, str(order["id"]), now - timedelta(days=spec.days_ago))
            apply_state(api, order, spec.kind)
            counts[spec.kind] = counts.get(spec.kind, 0) + 1
        print(
            f"orders: created {len(plan)} (now {seeded_order_count(api)} seeded): {json.dumps(counts, sort_keys=True)}"
        )
    except (AdminApiError, RuntimeError, KeyError, IndexError) as error:
        print(f"order seed failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
