# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""A third-party seller's products (``seller:`` tag, created through the storefront's
seller MCP) and their orders are not this merchant's: the catalog, the orders and the
metrics leave them out, and no change can be staged on them."""

from __future__ import annotations

import pytest

from merchant.api.fake_admin import OIL
from merchant.api.insights import parse_period
from merchant_agent import PriceUpdateItem
from merchant_agent.changes import ChangeNotApplicable

SELLER_TAGS = [{"name": "seller:ruoyu"}]


async def test_a_seller_tagged_product_is_not_in_the_catalog(backend, admin, session):
    admin.patch_product(OIL, tags=SELLER_TAGS)
    await backend.warm()

    assert OIL not in {listing.listing_id for listing in backend.all_listings()}
    assert await backend.get_listing(session, OIL) is None
    with pytest.raises(ChangeNotApplicable):
        await backend.stage_price_update(session, [PriceUpdateItem(listing_id=OIL, new_price=9.5)])


async def test_orders_of_seller_products_are_left_out_of_orders_and_metrics(
    backend, admin, session, now
):
    await backend.warm()
    window = parse_period("last_7d", now=now)
    sales_before, orders_before = await backend._totals(window)
    orders = admin.rows("order")
    tagged = orders[0]  # placed today, completed
    tagged["lineItems"][0]["product"] = {"tags": SELLER_TAGS}
    admin.set_orders(orders)

    listed = {row["order_number"] for row in await backend.portal_orders(100)}
    assert tagged["orderNumber"] not in listed and len(listed) == len(orders) - 1
    sales, count = await backend._totals(window)
    assert count == orders_before - 1
    assert sales == pytest.approx(sales_before - tagged["amountTotal"])
    with pytest.raises(ChangeNotApplicable, match="no order"):
        await backend.stage_refund(session, tagged["orderNumber"])
