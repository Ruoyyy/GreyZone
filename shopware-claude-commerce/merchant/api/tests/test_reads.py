# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Catalog, performance (aggregations), inventory alerts, order issues, pricing context."""

from __future__ import annotations

from merchant.api.fake_admin import (
    CANDLE,
    OIL,
    SHIRT,
    SHIRT_L,
    SHIRT_M,
    SHIRT_S,
    FakeAdmin,
)
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import ListingFilters


async def test_search_matches_title_and_product_number(warmed, session):
    hits = await warmed.search_listings(session, "oil", None, 8)
    assert [h.listing_id for h in hits] == [OIL]
    by_number = await warmed.search_listings(session, "CA-TSHIRT-M", None, 8)
    assert [h.listing_id for h in by_number] == [SHIRT]  # a variant's number finds its family
    paused = await warmed.search_listings(session, "*", ListingFilters(status="paused"), 8)
    assert [h.status for h in paused] == ["paused"]
    low = await warmed.search_listings(session, "", ListingFilters(max_stock=5), 8)
    assert CANDLE in {h.listing_id for h in low}


async def test_listing_details_family_variants_and_inherited_price(warmed, session):
    shirt = await warmed.get_listing(session, SHIRT)
    assert shirt is not None and shirt.has_options
    assert shirt.price == 29.99  # the lowest variant price
    assert shirt.stock == 16
    by_id = {v.listing_id: v for v in shirt.variants}
    assert by_id[SHIRT_M].price == 31.99
    assert by_id[SHIRT_L].price == 29.99  # inherits the family price
    assert by_id[SHIRT_L].status == "out_of_stock"
    assert by_id[SHIRT_S].variant_of == SHIRT
    variant = await warmed.get_listing(session, SHIRT_S)
    assert variant is not None and variant.variant_of == SHIRT and variant.variants == []


async def test_snapshot_uses_aggregations_and_compares_periods(warmed, session, admin: FakeAdmin):
    snapshot = await warmed.get_business_snapshot(session, "last_30d")
    assert snapshot.period == "last_30d" and snapshot.compare_to == "previous 30 days"
    assert snapshot.orders == 20 and snapshot.sales == 905.46
    assert snapshot.average_order_value == round(905.46 / 20, 2)
    assert snapshot.sales_change_pct is not None and snapshot.orders_change_pct is not None
    assert snapshot.traffic is None and snapshot.conversion_rate is None
    assert "traffic" in (snapshot.note or "")
    assert snapshot.alerts.low_stock == 3 and snapshot.alerts.slow_movers == 1
    assert snapshot.alerts.order_issues == 4
    aggregate_calls = [c for c in admin.calls if c.operation == "aggregate" and c.entity == "order"]
    assert len(aggregate_calls) >= 2  # current and previous period
    for call in aggregate_calls:  # cancelled orders are excluded from every total
        assert any(f["type"] == "not" for f in call.payload["filters"])
        assert any(
            f["type"] == "range" and f["field"] == "orderDateTime" for f in call.payload["filters"]
        )


async def test_snapshot_and_metrics_with_zero_orders(
    backend: ShopwareMerchantBackend, session, admin
):
    admin.set_orders([])
    await backend.warm()
    snapshot = await backend.get_business_snapshot(session)
    assert snapshot.sales == 0.0 and snapshot.orders == 0
    assert snapshot.average_order_value is None and snapshot.sales_change_pct is None
    series = await backend.query_metrics(session, "sales", "last_7d")
    assert len(series.points) == 7 and all(p.value == 0.0 for p in series.points)
    assert await backend.get_order_issues(session) == []
    alerts = await backend.get_inventory_alerts(session)
    assert {a.kind for a in alerts} == {"low_stock", "slow_mover"}
    assert backend.recent_orders() == []


async def test_metrics_series_period_granularity_segment(warmed, session):
    daily = await warmed.query_metrics(session, "sales", "last_7d")
    assert [p.date for p in daily.points][-1] == "2026-09-03" and len(daily.points) == 7
    assert daily.unit == "EUR" and daily.period == "last_7d"
    assert sum(p.value for p in daily.points) > 0
    weekly = await warmed.query_metrics(session, "orders", "last_30d", "week")
    assert weekly.granularity == "week" and len(weekly.points) == 5
    assert all(p.date.endswith(("03", "10", "17", "24", "31")) for p in weekly.points)
    aov = await warmed.query_metrics(session, "aov", "last_7d")
    assert aov.metric == "aov" and aov.unit == "EUR"
    segment = await warmed.query_metrics(session, "sales", "last_30d", "day", segment="Apparel")
    assert segment.segment == "Apparel" and sum(p.value for p in segment.points) > 0
    unknown_segment = await warmed.query_metrics(
        session, "sales", "last_30d", "day", segment="Toys"
    )
    assert unknown_segment.points == [] and "Toys" in (unknown_segment.note or "")
    traffic = await warmed.query_metrics(session, "traffic")
    assert traffic.points == [] and traffic.note
    lenient = await warmed.query_metrics(session, "revenue", "last 7 days")
    assert lenient.metric == "sales" and len(lenient.points) == 7


async def test_inventory_alerts_thresholds_and_slow_movers(warmed, session):
    alerts = await warmed.get_inventory_alerts(session)
    low = {a.listing_id: a for a in alerts if a.kind == "low_stock"}
    assert set(low) == {SHIRT_S, SHIRT_L, CANDLE}
    assert low[CANDLE].threshold == 5  # per-listing threshold from thresholds.json
    assert low[SHIRT_S].threshold == 8 and low[SHIRT_S].variant_of == SHIRT
    assert low[SHIRT_L].storefront_visible is False
    slow = [a for a in alerts if a.kind == "slow_mover"]
    assert [a.listing_id for a in slow] == [CANDLE]
    assert slow[0].sales_last_30d == 0


async def test_order_issues_kinds_and_seed_marker(warmed, session):
    issues = await warmed.get_order_issues(session)
    kinds = {i.issue_id.split(":")[0] for i in issues}
    assert kinds == {"delayed", "payment_failed", "buyer_message"}
    buyer = [i for i in issues if i.kind == "buyer_message"]
    assert len(buyer) == 1 and "neighbour" in (buyer[0].buyer_message_excerpt or "")
    delayed = [i for i in issues if i.issue_id.startswith("delayed:")]
    assert {i.order_id[-2:] for i in delayed} == {"08", "0b"}  # orders 10008 and 10011
    payment = [i for i in issues if i.issue_id.startswith("payment_failed:")]
    assert payment[0].kind == "delayed" and "failed" in payment[0].summary


async def test_pricing_context_cost_margin_and_floor(warmed, session):
    oil = await warmed.get_pricing_context(session, OIL)
    assert oil is not None
    assert oil.unit_cost == 7.5 and oil.margin_pct == 41.9
    assert oil.min_price == 9.9 and oil.min_price_basis == "policy"
    assert oil.max_price_delta_pct == 20.0 and oil.max_promotion_discount_pct == 50.0
    candle = await warmed.get_pricing_context(session, CANDLE)
    assert candle is not None and candle.unit_cost is None and candle.margin_pct is None
    assert candle.min_price is None  # no cost, no policy: no invented floor
    shirt = await warmed.get_pricing_context(session, SHIRT)
    assert shirt is not None and len(shirt.variants) == 3 and shirt.current_price == 29.99
    assert {v.option_values["sku"] for v in shirt.variants} == {
        "CA-TSHIRT-S",
        "CA-TSHIRT-M",
        "CA-TSHIRT-L",
    }
    assert await warmed.get_pricing_context(session, "nope") is None


async def test_catalog_refresh_pages_until_total(settings, config, now, session):
    from merchant.api.fake_admin import DEFAULT_SEED, _product
    from merchant.api.ledger import SqliteChangeLedger

    many = [*DEFAULT_SEED] + [
        _product(f"{i:032x}", f"Bulk product {i}", f"BULK-{i:04d}", 5.0, 10)
        for i in range(1000, 1230)
    ]
    admin = FakeAdmin(many, now=now)
    backend = ShopwareMerchantBackend(
        admin, settings, config, ledger=SqliteChangeLedger(config, ":memory:"), clock=lambda: now
    )
    await backend.warm()
    searches = [c for c in admin.calls if c.operation == "search" and c.entity == "product"]
    assert len(searches) == 3  # 237 rows at 100 per page
    assert len(backend.all_listings()) == 234
    hit = await backend.search_listings(session, "BULK-1229", None, 8)
    assert [h.title for h in hit] == ["Bulk product 1229"]


async def test_merchant_context_and_recent_orders(warmed, session):
    context = await warmed.get_merchant_context(session)
    assert context["transport"] == "fake" and context["previews_server_validated"] is True
    assert context["sales_channel"]
    orders = warmed.recent_orders(3)
    assert len(orders) == 3 and orders[0].order_id == "10001"
    assert warmed.shop_info()["name"] == "Storefront"
