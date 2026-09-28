# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Exercise the merchant backend against the live Shopware Admin. No Anthropic key.

    python merchant/scripts/smoke_live.py                 # read-only
    python merchant/scripts/smoke_live.py --write         # reversible round trips
    python merchant/scripts/smoke_live.py --transport rest

Credentials: the integration keys ``SHOPWARE_INTEGRATION_ACCESS_KEY`` /
``SHOPWARE_INTEGRATION_SECRET_KEY`` from the environment, ``.env`` or
``docker/.generated.env``. When they are missing and ``SHOPWARE_ADMIN_USERNAME`` /
``SHOPWARE_ADMIN_PASSWORD`` are set, the script creates a *temporary* admin integration
through the Admin API for this run and deletes it again at the end.

``--write`` performs reversible round trips and exits non-zero on any mismatch:
price +0.50 on CA-OIL and back; a 10 % promotion on CA-TSHIRT applied, verified and
deleted (promotion and rule); a restock +1 on CA-TSHIRT-S and back. The ledger used
here is in-memory, so the host's ``ledger.db`` is untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from demo_common import load_demo_env  # noqa: E402
from merchant.api.admin_client import (  # noqa: E402
    AdminAPIError,
    McpTransport,
    OAuthTokenProvider,
    build_transport,
    writes,
)
from merchant.api.agent_config import ShopwareSettings, build_merchant_config  # noqa: E402
from merchant.api.ledger import SqliteChangeLedger  # noqa: E402
from merchant.api.shopware_backend import ShopwareMerchantBackend  # noqa: E402
from merchant_agent import (  # noqa: E402
    InventoryActionItem,
    MerchantSessionContext,
    PriceUpdateItem,
    PromotionDraft,
)

PRICE_STEP = 0.50
PROMOTION_PCT = 10.0
RESTOCK_QUANTITY = 1
OIL_NUMBER = "CA-OIL"
SHIRT_NUMBER = "CA-TSHIRT"
SHIRT_S_NUMBER = "CA-TSHIRT-S"


class Mismatch(RuntimeError):
    pass


def load_env() -> None:
    load_demo_env(REPO_ROOT / "merchant")
    load_demo_env(REPO_ROOT)
    generated = REPO_ROOT / "docker" / ".generated.env"
    if generated.exists():
        for key, value in dotenv_values(generated).items():
            if value and not os.environ.get(key):
                os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Mismatch(message)
    print(f"  ok  {message}")


class TemporaryIntegration:
    """An admin integration created with the admin user's password grant, deleted on exit."""

    def __init__(self, shop_url: str, username: str, password: str) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        self._tokens = OAuthTokenProvider(
            shop_url, http=self._http, username=username, password=password
        )
        self._shop_url = shop_url
        self.integration_id: str | None = None
        self.access_key = ""
        self.secret_key = ""

    async def __aenter__(self) -> TemporaryIntegration:
        self.integration_id = uuid.uuid4().hex
        self.access_key = "SWIA" + secrets.token_hex(12).upper()
        self.secret_key = secrets.token_urlsafe(48)
        response = await self._http.post(
            f"{self._shop_url}/api/integration",
            headers=await self._tokens.headers(),
            json={
                "id": self.integration_id,
                "label": "commerce-agents smoke (temporary)",
                "accessKey": self.access_key,
                "secretAccessKey": self.secret_key,
                "admin": True,
            },
        )
        if response.status_code >= 400:
            raise AdminAPIError(
                f"could not create a temporary integration: {response.status_code} {response.text[:200]}"
            )
        print(f"temporary integration {self.integration_id} created")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.integration_id:
            response = await self._http.delete(
                f"{self._shop_url}/api/integration/{self.integration_id}",
                headers=await self._tokens.headers(),
            )
            print(f"temporary integration deleted ({response.status_code})")
        await self._http.aclose()


async def read_only(backend: ShopwareMerchantBackend, session: MerchantSessionContext) -> None:
    listings = backend.all_listings()
    print(f"store: {backend.store_name} via {backend.admin.name}; {len(listings)} listings")
    for listing in listings[:8]:
        number = listing.attributes.get("productNumber", "")
        print(
            f"  - {number:<14} {listing.title[:40]:<40} {listing.price:>8.2f} EUR stock={listing.stock} {listing.status}"
        )
    snapshot = await backend.get_business_snapshot(session, "last_30d")
    print(
        f"snapshot {snapshot.period}: sales={snapshot.sales} orders={snapshot.orders} "
        f"aov={snapshot.average_order_value} sales_change={snapshot.sales_change_pct}% "
        f"alerts={snapshot.alerts.model_dump()}"
    )
    series = await backend.query_metrics(session, "sales", "last_7d", "day")
    print("sales 7d:", ", ".join(f"{p.date[5:]}={p.value}" for p in series.points))
    alerts = await backend.get_inventory_alerts(session)
    print(f"inventory alerts: {len(alerts)}")
    for alert in alerts[:8]:
        print(
            f"  - {alert.kind:<11} {alert.title[:40]:<40} stock={alert.stock} threshold={alert.threshold}"
        )
    issues = await backend.get_order_issues(session)
    print(f"order issues: {len(issues)}")
    for issue in issues[:8]:
        print(f"  - {issue.issue_id.split(':')[0]:<15} {issue.summary}")
    context = await backend.get_merchant_context(session)
    print("context:", json.dumps({k: v for k, v in (context or {}).items() if k != "limitations"}))
    if isinstance(backend.admin, McpTransport):
        names = await backend.admin.tool_names()
        print(f"tools/list ({len(names)}): {', '.join(names)}")
    else:
        print("tools/list: n/a on the REST transport")
    check(writes(backend.admin.calls) == [], "read-only run performed no writes")


async def write_round_trips(
    backend: ShopwareMerchantBackend, session: MerchantSessionContext
) -> None:
    admin = backend.admin
    oil = backend.catalog.by_number(OIL_NUMBER)
    shirt = backend.catalog.by_number(SHIRT_NUMBER)
    shirt_s = backend.catalog.by_number(SHIRT_S_NUMBER)
    if oil is None or shirt is None or shirt_s is None:
        raise Mismatch(f"seeded products {OIL_NUMBER}/{SHIRT_NUMBER}/{SHIRT_S_NUMBER} not found")

    async def gross_of(product_id: str) -> float:
        row = await admin.read("product", product_id)
        assert row is not None
        return float(
            next(e for e in row["price"] if e["currencyId"] == "b7d2554b0ce847cd82f3ac9bd1c0dfca")[
                "gross"
            ]
        )

    async def stock_of(product_id: str) -> int:
        row = await admin.read("product", product_id)
        assert row is not None
        return int(row["stock"])

    print("\n[1] price round trip on", OIL_NUMBER)
    original = await gross_of(oil.listing_id)
    raised = round(original + PRICE_STEP, 2)
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=oil.listing_id, new_price=raised)]
    )
    print(f"  staged {change.change_id}: {change.summary}")
    for note in change.guardrail_notes:
        print(f"    · {note}")
    check(writes(admin.calls) == [], "staging wrote nothing")
    check(await gross_of(oil.listing_id) == original, "price unchanged after staging")
    applied = await backend.apply_change(session, change.change_id)
    print(f"    · {applied.guardrail_notes[-1]}")
    check(await gross_of(oil.listing_id) == raised, f"price is now {raised}")
    back = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=oil.listing_id, new_price=original)]
    )
    await backend.apply_change(session, back.change_id)
    check(await gross_of(oil.listing_id) == original, f"price restored to {original}")

    print("\n[2] promotion on", SHIRT_NUMBER)
    today = datetime.now(UTC).date().isoformat()
    promotion_id: str | None = None
    rule_id: str | None = None
    try:
        change = await backend.stage_promotion(
            session,
            PromotionDraft(
                name=f"smoke {today}",
                listing_ids=[shirt.listing_id],
                discount_pct=PROMOTION_PCT,
                starts=today,
                ends=today,
            ),
        )
        print(f"  staged {change.change_id}: {change.summary}")
        for note in change.guardrail_notes:
            print(f"    · {note}")
        ((_, payload),) = backend.ledger.payloads(change.change_id)
        promotion_id = payload["id"]
        rule_id = payload["discounts"][0]["discountRules"][0]["id"]
        check(await admin.read("promotion", promotion_id) is None, "promotion absent after staging")
        applied = await backend.apply_change(session, change.change_id)
        print(f"    · {applied.guardrail_notes[-1]}")
        promotion = await admin.read(
            "promotion",
            promotion_id,
            {"associations": {"discounts": {"associations": {"discountRules": {}}}}},
        )
        check(promotion is not None, "promotion exists")
        assert promotion is not None
        discounts = promotion.get("discounts") or []
        check(
            len(discounts) == 1 and float(discounts[0]["value"]) == PROMOTION_PCT,
            "promotion_discount exists with 10 %",
        )
        rule_ids = [r["id"] for r in discounts[0].get("discountRules") or []]
        check(rule_ids == [rule_id], "discount is bound to the rule")
        rule = await admin.read("rule", rule_id, {"associations": {"conditions": {}}})
        check(
            rule is not None and len(rule.get("conditions") or []) >= 1,
            "rule exists with a line-item condition",
        )
    finally:
        if promotion_id:
            result = await admin.delete("promotion", [promotion_id], dry_run=False)
            print(f"  cleanup promotion: success={result.success}")
        if rule_id:
            result = await admin.delete("rule", [rule_id], dry_run=False)
            print(f"  cleanup rule: success={result.success}")
    check(await admin.read("promotion", promotion_id or "") is None, "promotion deleted")
    check(await admin.read("rule", rule_id or "") is None, "rule deleted")

    print("\n[3] restock round trip on", SHIRT_S_NUMBER)
    before = await stock_of(shirt_s.listing_id)
    change = await backend.stage_inventory_action(
        session,
        [
            InventoryActionItem(
                listing_id=shirt_s.listing_id, action="restock", quantity=RESTOCK_QUANTITY
            )
        ],
    )
    for note in change.guardrail_notes:
        print(f"    · {note}")
    check(await stock_of(shirt_s.listing_id) == before, "stock unchanged after staging")
    await backend.apply_change(session, change.change_id)
    check(
        await stock_of(shirt_s.listing_id) == before + RESTOCK_QUANTITY,
        f"stock is now {before + RESTOCK_QUANTITY}",
    )
    result = await admin.upsert(
        "product", {"id": shirt_s.listing_id, "stock": before}, dry_run=False
    )
    check(result.success and result.dry_run is False, "stock reverted through the transport (-1)")
    check(await stock_of(shirt_s.listing_id) == before, f"stock restored to {before}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--write", action="store_true", help="perform the reversible write round trips"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="explicit form of the default: reads only (mutually exclusive with --write)",
    )
    parser.add_argument("--transport", choices=("mcp", "rest"), default=None)
    args = parser.parse_args()
    if args.write and args.read_only:
        parser.error("--write and --read-only are mutually exclusive")
    load_env()
    shop_url = env("SHOPWARE_ADMIN_URL") or env("SHOPWARE_URL") or "http://localhost:8080"
    transport_name = args.transport or env("SHOPWARE_ADMIN_TRANSPORT", "mcp") or "mcp"
    access_key, secret_key = (
        env("SHOPWARE_INTEGRATION_ACCESS_KEY"),
        env("SHOPWARE_INTEGRATION_SECRET_KEY"),
    )
    temporary: TemporaryIntegration | None = None
    if not (access_key and secret_key):
        username, password = env("SHOPWARE_ADMIN_USERNAME"), env("SHOPWARE_ADMIN_PASSWORD")
        if not (username and password):
            print(
                "no SHOPWARE_INTEGRATION_ACCESS_KEY/SECRET_KEY and no admin user to bootstrap one",
                file=sys.stderr,
            )
            return 2
        temporary = await TemporaryIntegration(shop_url, username, password).__aenter__()
        access_key, secret_key = temporary.access_key, temporary.secret_key
    settings = ShopwareSettings(
        shop_url=shop_url,
        access_key=access_key,
        secret_key=secret_key,
        transport=transport_name,
        sales_channel_id=env("SHOPWARE_SALES_CHANNEL_ID"),
        operator=env("MERCHANT_OPERATOR") or "smoke",
        store_name=env("SHOPWARE_STORE_NAME") or None,
        ledger_dsn=":memory:",
    )
    config = build_merchant_config(settings.store_name or shop_url)
    admin = build_transport(transport_name, shop_url, access_key=access_key, secret_key=secret_key)
    backend = ShopwareMerchantBackend(
        admin, settings, config, ledger=SqliteChangeLedger(config, ":memory:")
    )
    session = MerchantSessionContext(
        session_id="smoke", merchant_id=shop_url, operator=settings.operator
    )
    status = 0
    try:
        await backend.warm()
        await read_only(backend, session)
        if args.write:
            await write_round_trips(backend, session)
            print(f"\nlive writes performed: {len(writes(admin.calls))}")
        print("\nSMOKE OK" if status == 0 else "\nSMOKE FAILED")
    except (Mismatch, AdminAPIError) as error:
        print(f"\nSMOKE FAILED: {error}", file=sys.stderr)
        status = 1
    finally:
        await admin.aclose()
        backend.ledger.close()
        if temporary is not None:
            await temporary.__aexit__(None, None, None)
    return status


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
