# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shopware storefront backend over recorded UCP documents — every test runs over MCP and
over REST (``client`` fixture), signed, against a replay that verifies the signatures."""

from __future__ import annotations

import pytest

from commerce_common.skills import SkillRegistry
from shopping_agent import OrderStatus, SearchFilters, ShoppingSessionContext, Unavailable
from shopping_agent.executor import ShoppingToolExecutor
from shopware_common.handoff import HandoffCodeVerifier
from storefront.api.agent_config import build_shopping_config
from storefront.api.order_tool import OrderShoppingExecutor
from storefront.api.store_api import StoreApiError

from .conftest import HANDOFF_SECRET
from .replay import (
    CART_ID,
    CUSTOMER_EMAIL,
    CUSTOMER_PASSWORD,
    CUSTOMER_TOKEN,
    GONE_CART_ID,
    MAIN_ID,
    OIL_ID,
    ORDER_NUMBER,
    PRODUCT_ID,
    SEARCH_QUERY,
    VARIANT_L,
    VARIANT_M,
    VARIANT_S,
)


async def search(backend, session):
    return await backend.search_products(session, SEARCH_QUERY, limit=3)


# ---------------------------------------------------------------------------- catalog


async def test_search_maps_hex_ids_and_minor_unit_prices(backend, session):
    products = await search(backend, session)
    first = products[0]
    assert first.product_id == PRODUCT_ID
    assert first.title == "Claude Commerce T-Shirt"
    assert (first.price, first.currency) == (29.99, "EUR")
    assert first.in_stock
    assert first.image_url == "https://cdn.example/shirt.jpg"


async def test_the_parent_row_in_variants_is_not_a_child_sku(backend, session):
    products = await search(backend, session)
    details = backend.products[products[0].product_id]
    assert PRODUCT_ID not in {v.product_id for v in details.variants}
    assert {v.product_id for v in details.variants} == {
        VARIANT_S,
        "33333333333333333333333333333333",
        VARIANT_L,
    }


async def test_details_list_real_children_including_out_of_stock(backend, session):
    details = await backend.get_product_details(session, PRODUCT_ID)
    assert details is not None
    assert {v.product_id for v in details.variants} >= {VARIANT_S, VARIANT_L}
    sold_out = next(v for v in details.variants if v.product_id == VARIANT_L)
    assert sold_out.in_stock is False
    assert details.options == {"Size": ["S", "M", "L"]}
    assert details.specs.get("deliveryTime") == "1–3 Werktage"
    assert all(v.variant_of == PRODUCT_ID for v in details.variants)


async def test_a_seen_variant_id_resolves_to_its_own_details(backend, session):
    await backend.get_product_details(session, PRODUCT_ID)
    details = await backend.get_product_details(session, VARIANT_S)
    assert details is not None
    assert details.product_id == VARIANT_S
    assert details.title.endswith("S")
    assert details.variant_of == PRODUCT_ID


async def test_a_variant_id_resolves_from_a_fresh_session_too(backend):
    other = ShoppingSessionContext(session_id="s-fresh", user_id="guest")
    details = await backend.get_product_details(other, VARIANT_L)
    assert details is not None
    assert details.product_id == VARIANT_L
    assert details.in_stock is False


async def test_a_child_that_the_shop_answers_as_itself_still_resolves_to_its_family(
    backend, session
):
    # The live shop answers some variant ids with the child document (not the family);
    # the Store API's parentId decides, so the child is never filed as a family of its
    # siblings.
    details = await backend.get_product_details(session, "33333333333333333333333333333333")
    assert details is not None
    assert details.product_id == "33333333333333333333333333333333"
    assert details.variant_of == PRODUCT_ID
    family = backend.products[PRODUCT_ID]
    assert {v.product_id for v in family.variants} == {
        VARIANT_S,
        "33333333333333333333333333333333",
        VARIANT_L,
    }
    assert (
        "33333333333333333333333333333333" not in backend.products
        or not backend.products["33333333333333333333333333333333"].variants
    )


async def test_an_unknown_product_id_is_none(backend, session):
    assert await backend.get_product_details(session, "ffffffffffffffffffffffffffffffff") is None


async def test_search_falls_back_to_listing_when_ucp_and_search_return_html(
    backend, session, shop
):
    shop.ucp_search_html = True
    shop.store_search_html = True
    products = await backend.search_products(session, "shirt", limit=5)
    assert {p.product_id for p in products} == {PRODUCT_ID}


async def test_search_answers_a_variant_family_once_not_its_children(backend, session):
    products = await backend.search_products(session, "tee", limit=8)
    assert [p.product_id for p in products] == [PRODUCT_ID]
    assert VARIANT_S not in backend.products and VARIANT_M not in backend.products


async def test_an_unknown_aisle_stays_empty_when_search_falls_back(backend, session, shop):
    shop.ucp_search_html = True
    products = await backend.search_products(session, "lamps", limit=5)
    assert products == []


async def test_an_exact_grid_title_is_found_when_search_returns_empty(backend, session, shop):
    """The customer demo: UCP/search miss the title the grid already shows."""
    shop.ucp_search_html = True
    shop.store_search_html = True
    products = await backend.search_products(session, "Main product", limit=5)
    assert [p.product_id for p in products] == [MAIN_ID]
    assert products[0].title == "Main product"


async def test_electronics_matches_the_seeded_shopware_aisle(backend, session, shop):
    shop.ucp_search_html = True
    shop.store_search_html = True
    products = await backend.search_products(session, "electronics", limit=5)
    assert {p.product_id for p in products} == {MAIN_ID}


async def test_get_product_details_resolves_the_exact_grid_title(backend, session, shop):
    shop.ucp_search_html = True
    shop.store_search_html = True
    details = await backend.get_product_details(session, "Main product")
    assert details is not None
    assert details.product_id == MAIN_ID
    assert details.title == "Main product"


async def test_price_filters_are_enforced_on_the_result(backend, session):
    cheap = await backend.search_products(
        session, "", filters=SearchFilters(max_price=20.0), limit=5
    )
    assert [p.product_id for p in cheap] == [OIL_ID]
    pricey = await backend.search_products(
        session, "", filters=SearchFilters(min_price=20.0), limit=5
    )
    assert {p.product_id for p in pricey} == {PRODUCT_ID, MAIN_ID}


# ---------------------------------------------------------------------------- cart & handoff


async def test_the_cart_lifecycle_keeps_one_token_per_session(backend, session):
    assert (await backend.get_cart(session)).items == []
    assert backend.checkout_url_for(session.session_id) is None

    products = await search(backend, session)
    cart = await backend.add_to_cart(session, products[0].product_id, 1)
    assert [(i.product_id, i.quantity, i.price) for i in cart.items] == [(VARIANT_S, 1, 29.99)]
    assert cart.currency == "EUR"
    assert backend.cart_id_for(session.session_id) == CART_ID

    checkout = backend.checkout_url_for(session.session_id)
    assert checkout and checkout.startswith("http://host.test/api/checkout/handoff/")
    assert CART_ID not in checkout

    assert (await backend.get_cart(session)).item_count == 1
    updated = await backend.update_cart_item(session, VARIANT_S, 2)
    assert updated.item_count == 2
    removed = await backend.remove_from_cart(session, VARIANT_S)
    assert removed.items == []
    assert backend.checkout_url_for(session.session_id) is None


async def test_the_handoff_code_decrypts_to_the_cart_token_and_is_single_use(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, VARIANT_S, 1)
    code = backend.handoff_code_for(session.session_id)
    assert code is not None
    verifier = HandoffCodeVerifier(HANDOFF_SECRET)
    assert verifier.verify(code.code) == CART_ID
    with pytest.raises(ValueError):
        verifier.verify(code.code)
    assert backend.handoff_code_for("no-such-session") is None


async def test_adding_a_variant_id_directly_skips_default_resolution(backend, session):
    await search(backend, session)
    cart = await backend.add_to_cart(session, VARIANT_S, 1)
    assert cart.items[0].product_id == VARIANT_S


async def test_out_of_stock_variant_raises_unavailable_with_siblings(backend, session):
    await backend.get_product_details(session, PRODUCT_ID)
    with pytest.raises(Unavailable, match=VARIANT_S):
        await backend.add_to_cart(session, VARIANT_L, 1)


async def test_reset_session_drops_the_cart_binding(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    ticket_url = backend.checkout_url_for(session.session_id)
    backend.reset_session(session.session_id)
    assert (await backend.get_cart(session)).items == []
    assert backend.checkout_url_for(session.session_id) is None
    assert backend.handoff.session_for(ticket_url.rsplit("/", 1)[-1]) is None


async def test_sessions_do_not_share_carts(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    other = ShoppingSessionContext(session_id="s-2", user_id="guest")
    assert (await backend.get_cart(other)).items == []


async def test_a_dropped_cart_reads_as_empty_and_unbinds(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    backend._sessions[session.session_id].cart_id = GONE_CART_ID
    cart = await backend.get_cart(session)
    assert cart.items == []
    assert backend._sessions[session.session_id].cart_id is None


async def test_adding_into_a_dropped_cart_starts_a_fresh_one(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    backend._sessions[session.session_id].cart_id = GONE_CART_ID
    cart = await backend.add_to_cart(session, PRODUCT_ID, 1)
    assert cart.items[0].product_id == VARIANT_S
    assert backend._sessions[session.session_id].cart_id != GONE_CART_ID


async def test_checkout_handoff_points_at_the_ticket_and_never_completes(backend, session, shop):
    await search(backend, session)
    cart = await backend.add_to_cart(session, PRODUCT_ID, 1)
    handoff = await backend.checkout_handoff(session, cart)
    assert handoff and handoff[0].url == backend.checkout_url_for(session.session_id)
    assert "Shopware" in handoff[0].label
    assert not any("checkout" in r.url.path for r in shop.requests)


async def signed_customer_cart(backend, session):
    backend._customer_token_provider = lambda _session_id: CUSTOMER_TOKEN
    await search(backend, session)
    await backend.attach_cart(session.session_id, CUSTOMER_TOKEN)
    return await backend.add_to_cart(session, VARIANT_S, 1)


async def test_place_order_submits_for_the_signed_in_customer(backend, session, shop):
    cart = await signed_customer_cart(backend, session)
    preview = await backend.checkout_handoff(session, cart)
    assert preview and preview[0].url == backend.checkout_url_for(session.session_id)
    assert shop.checkouts == {}
    placed = await backend.place_order(session)
    assert placed.order_id == ORDER_NUMBER
    assert placed.status == OrderStatus.PROCESSING
    assert shop.checkouts == {}
    assert backend.cart_id_for(session.session_id) is None
    assert shop.customer_carts[CUSTOMER_TOKEN] == []
    assert any(request.url.path == "/store-api/checkout/order" and
               request.headers.get("sw-context-token") == CUSTOMER_TOKEN for request in shop.requests)


async def test_place_order_requires_a_verified_identity(backend, session, shop):
    backend._order_identity_provider = None
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    with pytest.raises(PermissionError):
        await backend.place_order(session)
    assert not any("checkout" in request.url.path for request in shop.requests)


async def test_place_order_failure_keeps_cart_and_does_not_claim_success(
    backend, session, shop, monkeypatch
):
    async def rejected(_token):
        raise StoreApiError("POST", "/store-api/checkout/order", 400, "refused")

    monkeypatch.setattr(backend.store_api, "place_order", rejected)
    await signed_customer_cart(backend, session)
    with pytest.raises(StoreApiError):
        await backend.place_order(session)
    assert backend.cart_id_for(session.session_id) is not None
    with pytest.raises(StoreApiError):
        await backend.place_order(session)


async def test_uncertain_order_response_blocks_a_duplicate_attempt(backend, session, monkeypatch):
    async def disconnected(_token):
        raise StoreApiError("POST", "/store-api/checkout/order", 0, "connection lost")

    monkeypatch.setattr(backend.store_api, "place_order", disconnected)
    await signed_customer_cart(backend, session)
    with pytest.raises(StoreApiError):
        await backend.place_order(session)
    with pytest.raises(ValueError, match="previous order attempt"):
        await backend.place_order(session)


async def test_placed_order_is_visible_only_with_the_customer_credential(backend, session):
    await signed_customer_cart(backend, session)
    await backend.place_order(session)
    later = ShoppingSessionContext(session_id="s-2", user_id=session.user_id)
    orders = await backend.get_orders(later)
    assert [o.order_id for o in orders] == [ORDER_NUMBER]
    order = await backend.get_order(later, ORDER_NUMBER)
    assert order is not None and order.items and order.total > 0
    backend._order_identity_provider = None
    assert await backend.get_orders(later) == []
    assert await backend.get_order(later, ORDER_NUMBER) is None


async def test_guest_cannot_read_orders_with_a_cart_token(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, VARIANT_S, 1)
    backend._customer_token_provider = lambda _session_id: backend.cart_id_for(session.session_id)
    backend._order_identity_provider = None
    assert await backend.get_orders(session) == []
    assert await backend.get_order(session, ORDER_NUMBER) is None


async def test_preview_and_place_order_are_distinct_agent_tools(backend, session, state, shop):
    backend._customer_token_provider = lambda _session_id: CUSTOMER_TOKEN
    await backend.attach_cart(session.session_id, CUSTOMER_TOKEN)
    executor = OrderShoppingExecutor(
        backend=backend,
        config=build_shopping_config("Shopware"),
        skills=SkillRegistry([]),
        session=session,
        state=state,
    )
    await executor.execute("search_products", {"query": SEARCH_QUERY})
    await executor.execute("get_product_details", {"product_id": PRODUCT_ID})
    await executor.execute("add_to_cart", {"product_id": VARIANT_S, "quantity": 1})
    preview = await executor.execute("checkout", {})
    assert not preview.refused
    assert not any("complete_checkout" in str(request.content) for request in shop.requests)
    placed = await executor.execute("place_order", {})
    assert not placed.refused
    assert "Order placed" in placed.result_text
    assert any(event.type == "cart_update" for event in placed.events)


# ---------------------------------------------------------------------------- orders


async def test_login_reads_the_context_token_from_the_header(store_api):
    # Shopware 6.7 returns the customer context token in the sw-context-token header, not
    # the JSON body; login must read it from there.
    assert await store_api.login(CUSTOMER_EMAIL, CUSTOMER_PASSWORD) == CUSTOMER_TOKEN


async def test_orders_come_from_the_store_api_behind_the_customer_token(backend, session):
    assert await backend.get_orders(session) == []
    await search(backend, session)
    await backend.add_to_cart(session, VARIANT_S, 1)
    backend._customer_token_provider = lambda _session_id: backend.cart_id_for(session.session_id)
    orders = await backend.get_orders(session, limit=5)
    assert len(orders) == 1
    order = orders[0]
    assert order.order_id == ORDER_NUMBER
    assert order.status == OrderStatus.SHIPPED
    assert order.total == 64.88 and order.currency == "EUR"
    assert order.tracking_url == "https://track.example/1Z999"
    assert order.estimated_delivery == "2026-09-03"
    assert [(i.product_id, i.quantity, i.option_values) for i in order.items] == [
        (VARIANT_S, 2, {"Size": "S"})
    ]
    assert order.items[0].variant_of == PRODUCT_ID

    assert (await backend.get_order(session, ORDER_NUMBER)) is not None
    assert (await backend.get_order(session, "88888888888888888888888888888888")) is not None
    assert await backend.get_order(session, "nope") is None


async def test_reviews_fill_rating_count_and_newest_highlights(backend, monkeypatch):
    async def reviews(product_id, limit):
        assert (product_id, limit) == (PRODUCT_ID, 3)
        return {
            "total": 4,
            "aggregations": {"rating-average": {"avg": 4.25}},
            "elements": [{"content": "Fits <b>true</b> to size."}, {"content": ""}, {"content": "x" * 400}],
        }

    monkeypatch.setattr(backend.store_api, "product_reviews", reviews)
    assert await backend.product_reviews(PRODUCT_ID) == {
        "rating": 4.2,
        "review_count": 4,
        "review_highlights": ["Fits true to size.", "x" * 280],
    }


async def test_the_agents_product_details_carry_the_reviews(backend, session, monkeypatch):
    async def reviews(product_id, limit):
        return {"total": 2, "aggregations": {"rating-average": {"avg": 4.5}}, "elements": [{"content": "Soft cotton."}]}

    monkeypatch.setattr(backend.store_api, "product_reviews", reviews)
    await search(backend, session)
    for product_id in (PRODUCT_ID, VARIANT_S):
        details = await backend.get_product_details(session, product_id)
        assert (details.rating, details.review_count, details.review_highlights) == (4.5, 2, ["Soft cotton."])


async def test_a_product_without_reviews_shows_none_and_a_non_id_is_not_sent(backend, monkeypatch):
    sent = []

    async def reviews(product_id, limit):
        sent.append(product_id)
        return {"total": 0, "aggregations": {"rating-average": {"avg": 0}}, "elements": []}

    monkeypatch.setattr(backend.store_api, "product_reviews", reviews)
    empty = {"rating": None, "review_count": None, "review_highlights": []}
    assert await backend.product_reviews(PRODUCT_ID) == empty
    assert await backend.product_reviews("../account") == empty
    assert sent == [PRODUCT_ID]


# ---------------------------------------------------------------------------- policies, facts, fulfillment


async def test_policies_come_from_the_shops_cms_pages(backend, session):
    policies = await backend.search_policies(session, "widerruf")
    assert policies
    assert policies[0].title == "Widerrufsbelehrung"
    assert "14 Tagen" in policies[0].content
    assert "flex-start" not in policies[0].content
    assert policies[0].category == "returns"
    assert backend.policies.live
    contact = await backend.search_policies(session, "kontakt")
    assert contact[0].title == "Kontakt"


async def test_disclosures(backend, session):
    disclosure = await backend.get_disclosure(session, PRODUCT_ID)
    assert disclosure is not None
    oil = await backend.get_disclosure(session, OIL_ID)
    assert oil is not None
    assert any("Grundpreis" in row.label or "25,80" in row.value for row in oil.rows)


def test_disclosure_rows_read_zero_stock_and_do_not_repeat_the_delivery_range():
    """``availableStock: 0`` is sold out (not "fall back to ``stock``"), and a delivery-time
    name that already spells its range is not suffixed with the same range."""
    from storefront.api.disclosures import disclosure_from_store_product

    sold_out = disclosure_from_store_product(
        "p1",
        {
            "availableStock": 0,
            "stock": 7,
            "deliveryTime": {"name": "2–4 Tage", "min": 2, "max": 4},
        },
    )
    rows = {row.label: row.value for row in sold_out.rows}
    assert rows["Verfügbarkeit"] == "Derzeit nicht lieferbar"
    assert rows["Lieferzeit"] == "2–4 Tage"
    assert "Versand & Lieferzeit" in rows["Versand"]

    named = disclosure_from_store_product(
        "p2", {"stock": 3, "deliveryTime": {"name": "Standardversand", "min": 1, "max": 3}}
    )
    rows = {row.label: row.value for row in named.rows}
    assert rows["Verfügbarkeit"] == "Auf Lager"
    assert rows["Lieferzeit"] == "Standardversand (1–3 Tage)"


async def test_fulfillment_options_carry_fee_and_eta_from_shipping_methods(backend, session):
    await backend.get_product_details(session, PRODUCT_ID)
    options = await backend.get_fulfillment_options(session, [PRODUCT_ID])
    assert [(o.method, o.fee) for o in options] == [("shipping", 4.9), ("shipping", 9.9)]
    assert options[0].eta == "Standard: 2–4 Tage (Verfügbarkeit: 1–3 Werktage)"
    assert options[1].eta.startswith("Express: 1–2 Tage")


# ---------------------------------------------------------------------------- gates


def make_executor(backend, session, state) -> ShoppingToolExecutor:
    return ShoppingToolExecutor(
        backend=backend,
        config=build_shopping_config("Shopware"),
        skills=SkillRegistry([]),
        session=session,
        state=state,
    )


async def test_cart_writes_hold_without_provenance_and_pass_with_it(backend, session, state):
    executor = make_executor(backend, session, state)
    held = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert held.blocked

    await executor.execute("search_products", {"query": SEARCH_QUERY})
    assert PRODUCT_ID in state.seen_products
    details = await executor.execute("get_product_details", {"product_id": PRODUCT_ID})
    assert not details.refused
    added = await executor.execute("add_to_cart", {"product_id": VARIANT_S, "quantity": 1})
    assert not added.refused
    updated = await executor.execute("update_cart_item", {"product_id": VARIANT_S, "quantity": 2})
    assert not updated.refused
    removed = await executor.execute("remove_from_cart", {"product_id": VARIANT_S})
    assert not removed.refused


@pytest.mark.parametrize("unit_price", [900, 20, 10.9, 495.95])
def test_store_api_fallback_keeps_whole_number_prices_in_major_units(unit_price):
    # 900.00 arrives from the Store API as the int 900; it must not be read as 900 cents.
    from storefront.api.shopware_backend import _product_price, _store_api_to_ucp

    record = _store_api_to_ucp({"id": "a" * 32, "calculatedPrice": {"unitPrice": unit_price}})
    assert _product_price(record) == (float(unit_price), "EUR")
