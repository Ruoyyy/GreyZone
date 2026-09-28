# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Eval-only fixtures merged into a backend for one run.

Poisoned listings, their benign counterparts, and hostile buyer messages never live in
demo data, seeds, or recorded captures (``commerce-evals`` skill, "Poisoned fixtures").
The overlays below wrap the real backend: overlay records answer search, details, and
issue reads beside the shop's own; a cart write for an overlay product is kept in
memory so the cart the scorers read reflects it; a merchant write against an overlay
listing is refused, because nothing in Shopware could carry it.
"""

from __future__ import annotations

from typing import Any

from merchant_agent import (
    ActorKind,
    CampaignDraft,
    InventoryActionItem,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantBackend,
    MerchantSessionContext,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from merchant_agent.changes import ChangeNotApplicable
from shopping_agent import (
    Cart,
    CartItem,
    Disclosure,
    Policy,
    Product,
    ProductDetails,
    ShoppingSessionContext,
    StorefrontBackend,
    Unavailable,
)

OVERLAY_ID_PREFIX = "evl-"


def _tokens(text: str) -> set[str]:
    return {t for t in text.lower().replace(",", " ").split() if len(t) > 2}


def _matches(query: str, *fields: str | None) -> bool:
    wanted = _tokens(query)
    if not wanted:
        return True
    hay = " ".join(f for f in fields if f).lower()
    return any(token in hay for token in wanted)


def check_overlay_ids(records: list[dict[str, Any]], key: str) -> None:
    for record in records:
        value = str(record.get(key) or "")
        if not value.startswith(OVERLAY_ID_PREFIX):
            raise ValueError(
                f"eval-only fixture ids start with {OVERLAY_ID_PREFIX!r}, got {value!r}"
            )


class OverlayStorefront(StorefrontBackend):
    """``inner`` plus eval-only products and policies."""

    def __init__(
        self,
        inner: StorefrontBackend,
        products: list[dict[str, Any]] = (),
        policies: list[dict[str, Any]] = (),
    ) -> None:
        check_overlay_ids(list(products), "product_id")
        self.inner = inner
        self.products: dict[str, ProductDetails] = {}
        for raw in products:
            details = ProductDetails.model_validate(raw)
            self.products[details.product_id] = details
            for variant in details.variants:
                self.products[variant.product_id] = ProductDetails(**variant.model_dump())
        self.policies = [Policy.model_validate(raw) for raw in policies]
        self._lines: dict[str, dict[str, CartItem]] = {}

    def __getattr__(self, name: str) -> Any:  # anything not overridden goes to the shop backend
        return getattr(self.inner, name)

    # -- catalog -------------------------------------------------------------------------

    async def search_products(self, session, query, filters=None, limit=8):
        results = list(await self.inner.search_products(session, query, filters, limit))
        for details in self.products.values():
            if details.variant_of:
                continue
            if _matches(
                query, details.title, details.short_description, details.category, details.brand
            ):
                if filters and filters.max_price is not None and details.price > filters.max_price:
                    continue
                if filters and filters.min_price is not None and details.price < filters.min_price:
                    continue
                results.append(
                    Product(
                        **details.model_dump(
                            exclude={"long_description", "specs", "review_highlights", "variants"}
                        )
                    )
                )
        return results[:limit]

    async def get_product_details(self, session, product_id):
        if product_id in self.products:
            return self.products[product_id]
        return await self.inner.get_product_details(session, product_id)

    # -- cart ----------------------------------------------------------------------------

    def _overlay_lines(self, session: ShoppingSessionContext) -> dict[str, CartItem]:
        return self._lines.setdefault(session.session_id, {})

    def _merge(self, session: ShoppingSessionContext, cart: Cart) -> Cart:
        lines = self._overlay_lines(session)
        if not lines:
            return cart
        return Cart(items=[*cart.items, *lines.values()], currency=cart.currency)

    async def get_cart(self, session):
        return self._merge(session, await self.inner.get_cart(session))

    async def add_to_cart(self, session, product_id, quantity):
        if product_id not in self.products:
            return self._merge(session, await self.inner.add_to_cart(session, product_id, quantity))
        details = self.products[product_id]
        if details.has_options:
            raise Unavailable(f"{product_id} is a family; choose a variant")
        if not details.in_stock:
            raise Unavailable(f"{product_id} is unavailable")
        lines = self._overlay_lines(session)
        existing = lines.get(product_id)
        lines[product_id] = CartItem(
            product_id=product_id,
            title=details.title,
            price=details.price,
            quantity=quantity + (existing.quantity if existing else 0),
            option_values=details.option_values,
            variant_of=details.variant_of,
        )
        return await self.get_cart(session)

    async def update_cart_item(self, session, product_id, quantity):
        lines = self._overlay_lines(session)
        if product_id in lines:
            lines[product_id] = lines[product_id].model_copy(update={"quantity": quantity})
            return await self.get_cart(session)
        return self._merge(
            session, await self.inner.update_cart_item(session, product_id, quantity)
        )

    async def remove_from_cart(self, session, product_id):
        lines = self._overlay_lines(session)
        if product_id in lines:
            lines.pop(product_id)
            return await self.get_cart(session)
        return self._merge(session, await self.inner.remove_from_cart(session, product_id))

    # -- the rest of the contract --------------------------------------------------------

    async def get_preferences(self, session):
        return await self.inner.get_preferences(session)

    async def get_orders(self, session, limit=5):
        return await self.inner.get_orders(session, limit)

    async def get_order(self, session, order_id):
        return await self.inner.get_order(session, order_id)

    async def search_policies(self, session, query):
        found = list(await self.inner.search_policies(session, query))
        found.extend(p for p in self.policies if _matches(query, p.title, p.content, p.category))
        return found

    async def get_fulfillment_options(self, session, product_ids):
        return await self.inner.get_fulfillment_options(session, product_ids)

    async def get_disclosure(self, session, product_id) -> Disclosure | None:
        if product_id in self.products:
            return None
        return await self.inner.get_disclosure(session, product_id)

    async def checkout_handoff(self, session, cart):
        return await self.inner.checkout_handoff(session, cart)

    async def get_account_context(self, session):
        return await self.inner.get_account_context(session)


class OverlayMerchant(MerchantBackend):
    """``inner`` plus eval-only listings and order issues. Writes to an overlay listing
    are refused as not applicable."""

    def __init__(
        self,
        inner: MerchantBackend,
        listings: list[dict[str, Any]] = (),
        order_issues: list[dict[str, Any]] = (),
    ) -> None:
        check_overlay_ids(list(listings), "listing_id")
        self.inner = inner
        self.listings: dict[str, ListingDetails] = {}
        for raw in listings:
            details = ListingDetails.model_validate(raw)
            self.listings[details.listing_id] = details
            for variant in details.variants:
                self.listings[variant.listing_id] = ListingDetails(**variant.model_dump())
        self.order_issues = [OrderIssue.model_validate(raw) for raw in order_issues]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _refuse(self, ids: list[str]) -> None:
        overlay = [lid for lid in ids if lid in self.listings]
        if overlay:
            raise ChangeNotApplicable(
                f"listing {overlay[0]} is not in the Shopware catalog and cannot be changed"
            )

    # -- reads ---------------------------------------------------------------------------

    async def get_business_snapshot(self, session, period=None):
        return await self.inner.get_business_snapshot(session, period)

    async def query_metrics(self, session, metric, period=None, granularity="day", segment=None):
        return await self.inner.query_metrics(session, metric, period, granularity, segment)

    async def get_campaign_performance(self, session, campaign_id=None):
        return await self.inner.get_campaign_performance(session, campaign_id)

    async def search_listings(self, session, query, filters: ListingFilters | None = None, limit=8):
        results = list(await self.inner.search_listings(session, query, filters, limit))
        for details in self.listings.values():
            if details.variant_of:
                continue
            if _matches(query, details.title, details.short_description, details.category):
                results.append(
                    Listing(
                        **details.model_dump(
                            exclude={
                                "long_description",
                                "review_snippets",
                                "sales_last_30d",
                                "return_rate_pct",
                                "missing_attributes",
                                "variants",
                            }
                        )
                    )
                )
        return results[:limit]

    async def get_listing(self, session, listing_id):
        if listing_id in self.listings:
            return self.listings[listing_id]
        return await self.inner.get_listing(session, listing_id)

    async def get_inventory_alerts(self, session):
        return await self.inner.get_inventory_alerts(session)

    async def get_order_issues(self, session):
        return [*await self.inner.get_order_issues(session), *self.order_issues]

    async def get_pricing_context(self, session, listing_id):
        if listing_id in self.listings:
            listing = self.listings[listing_id]
            return PricingContext(
                listing_id=listing_id, current_price=listing.price, currency=listing.currency
            )
        return await self.inner.get_pricing_context(session, listing_id)

    # -- staged writes -------------------------------------------------------------------

    async def stage_listing_update(self, session, listing_id, fields, note=None) -> StagedChange:
        self._refuse([listing_id])
        return await self.inner.stage_listing_update(session, listing_id, fields, note)

    async def stage_price_update(
        self, session, items: list[PriceUpdateItem], note=None
    ) -> StagedChange:
        self._refuse([item.listing_id for item in items])
        return await self.inner.stage_price_update(session, items, note)

    async def stage_inventory_action(
        self, session, items: list[InventoryActionItem], note=None
    ) -> StagedChange:
        self._refuse([item.listing_id for item in items])
        return await self.inner.stage_inventory_action(session, items, note)

    async def stage_promotion(self, session, promotion: PromotionDraft) -> StagedChange:
        self._refuse(list(promotion.listing_ids))
        return await self.inner.stage_promotion(session, promotion)

    async def stage_campaign(self, session, campaign: CampaignDraft) -> StagedChange:
        return await self.inner.stage_campaign(session, campaign)

    async def get_pending_changes(self, session):
        return await self.inner.get_pending_changes(session)

    async def apply_change(self, session, change_id):
        return await self.inner.apply_change(session, change_id)

    async def discard_change(self, session, change_id, actor_kind: ActorKind = ActorKind.OPERATOR):
        return await self.inner.discard_change(session, change_id, actor_kind)

    async def get_merchant_context(self, session: MerchantSessionContext):
        return await self.inner.get_merchant_context(session)

    async def execute_analysis_query(self, session, sql):
        return await self.inner.execute_analysis_query(session, sql)

    async def get_analysis_schema(self, session):
        return await self.inner.get_analysis_schema(session)


__all__ = ["OVERLAY_ID_PREFIX", "OverlayMerchant", "OverlayStorefront", "check_overlay_ids"]
