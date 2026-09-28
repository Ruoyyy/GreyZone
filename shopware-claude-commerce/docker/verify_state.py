#!/usr/bin/env python3
"""Idempotency summary for docker/verify.sh: counts that must be exactly 1 (or the seeded
number) after any number of bootstrap runs. Uses the admin password grant (developer tool).

    python docker/verify_state.py --shop-url http://localhost:8080
"""

from __future__ import annotations

import argparse
import sys

from _bootstrap_lib import DEFAULT_CONTAINER, AdminApi, AdminApiError, console
from enable_ucp import _parse_key_list
from merchant_identity import ACL_ROLE_NAME, INTEGRATION_NAME
from seed_catalog import (
    CONTACT_PAGE,
    DELIVERY_TIME_EXPRESS,
    DELIVERY_TIME_STANDARD,
    POLICY_PAGES,
    SEEDED_PRODUCT_NUMBERS,
)
from seed_orders import DEFAULT_ORDER_COUNT, SEED_MARKER


def equals(field: str, value: object) -> list[dict[str, object]]:
    return [{"type": "equals", "field": field, "value": value}]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--orders", type=int, default=DEFAULT_ORDER_COUNT)
    args = parser.parse_args()

    api = AdminApi(args.shop_url.rstrip("/"))
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
    except AdminApiError as error:
        print(f"verify_state: {error}", file=sys.stderr)
        return 1

    checks: list[tuple[str, int, int]] = []  # label, actual, expected
    checks.append(
        (
            f"integration '{INTEGRATION_NAME}'",
            api.count("integration", equals("label", INTEGRATION_NAME)),
            1,
        )
    )
    checks.append(
        (f"acl role '{ACL_ROLE_NAME}'", api.count("acl-role", equals("name", ACL_ROLE_NAME)), 1)
    )
    for number in SEEDED_PRODUCT_NUMBERS:
        checks.append(
            (f"product {number}", api.count("product", equals("productNumber", number)), 1)
        )
    for spec in (DELIVERY_TIME_STANDARD, DELIVERY_TIME_EXPRESS):
        checks.append(
            (
                f"delivery time '{spec['name']}'",
                api.count("delivery-time", equals("name", spec["name"])),
                1,
            )
        )
    for name, _ in (*POLICY_PAGES, CONTACT_PAGE):
        checks.append((f"cms page '{name}'", api.count("cms-page", equals("name", name)), 1))
        checks.append((f"category '{name}'", api.count("category", equals("name", name)), 1))
    checks.append(
        ("shipping method 'Express'", api.count("shipping-method", equals("name", "Express")), 1)
    )
    checks.append(
        ("sales channel footerCategoryId set", int(bool(channel.get("footerCategoryId"))), 1)
    )
    checks.append(
        ("sales channel serviceCategoryId set", int(bool(channel.get("serviceCategoryId"))), 1)
    )
    seeded_orders = api.count("order", equals("customerComment", SEED_MARKER))
    checks.append((f"seeded orders (>= {args.orders})", int(seeded_orders >= args.orders), 1))

    listing = console(
        args.container, f"ucp:signing-keys:list --sales-channel={channel['id']} --no-interaction"
    )
    active = [key for key in _parse_key_list(listing.stdout) if key.get("status") == "active"]
    checks.append(("active shop signing keys", len(active), 1))

    failures = 0
    for label, actual, expected in checks:
        ok = actual == expected
        failures += not ok
        print(f"  {label}: {actual} (expected {expected}) [{'ok' if ok else 'FAIL'}]")
    print(f"  seeded orders total: {seeded_orders}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
