# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""``MerchantBackend`` over the Shopware Admin MCP tools (REST as fallback).

Reads come from ``shopware-entity-search`` / ``-read`` / ``-aggregate``. Every
``stage_*`` builds the exact write payload, previews it with ``shopware-entity-upsert``
``dryRun=true`` (Shopware runs the write in a transaction and rolls it back), records
the server's verdict in ``guardrail_notes`` and stores the payload with the change.
``apply_change`` is the only live write: it replays that payload with ``dryRun=false``.

That is the **host path**. With ``SwagCommerceAgentTools`` installed (``SHOPWARE_AGENT_TOOLS``,
``agent_tools.py``) the **plugin path** hands the same items to ``agent-change-stage``: the
ledger row lives in Shopware's ``swag_agent_staged_change``, ``get_pending_changes`` reads
``agent-change-list``, ``apply_change`` / ``discard_change`` call ``agent-change-apply`` /
``-discard`` after the host's approval gate, and ``get_business_snapshot`` /
``query_metrics`` read ``agent-business-snapshot`` / ``agent-metrics-series``. Promotions
(a kind the plugin refuses) and the ``chg-NNNN`` ids of the SQLite ledger keep the host
path in every mode. The blueprint gates run unchanged on both paths.

Refunds (``refunds.py``) are staged changes too: ``stage_refund`` previews the payment's
``refund`` transition with ``shopware-order-state`` ``dryRun=true``, and ``apply_change``
runs it with ``dryRun=false`` and reads the payment state back.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from demo_common.merchant_fixtures import filter_listings
from merchant_agent import (
    ActorKind,
    AlertCounts,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeStatus,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation, check_guardrails
from shopping_agent import Order

from .admin_client import EUR_CURRENCY_ID, AdminAPIError, AdminTransport
from .agent_config import DATA_DIR, ShopwareSettings
from .agent_tools import (
    MerchantAgentTools,
)
from .catalog import CatalogCache, ProductRecord
from .insights import (
    GRANULARITIES,
    OPEN_ORDER_STATES,
    PAYMENT_PROBLEM_STATES,
    Period,
    change_pct,
    derive_issues,
    histogram_aggregation,
    line_item_period_filters,
    load_pricing_policy,
    load_thresholds,
    not_third_party,
    order_associations,
    parse_period,
    period_filters,
    portal_order,
    read_count,
    read_sum,
    series_points,
    to_shopping_order,
    totals_aggregations,
    units_by_product,
    units_sold_aggregation,
)
from .ledger import SqliteChangeLedger
from .refunds import (
    ORDER_STATE_ENTITY,
    REFUND_ACTION,
    REFUND_FIELD,
    REFUND_KIND,
    REFUNDABLE_STATES,
    is_refund,
)
from .staging import (
    LISTING_FIELDS,
    PreviewRejected,
    ShopwareWriter,
    WriteFailed,
    WritePayload,
    current_gross,
    current_price_entries,
    listing_payload,
    preview_note,
    price_payload,
    promotion_payload,
    tax_rate_for,
)

logger = logging.getLogger(__name__)

CURRENCY = "EUR"
RECENT_ORDERS_LIMIT = 50
ISSUE_SEARCH_LIMIT = 100
METRIC_ALIASES = {
    "sales": "sales",
    "revenue": "sales",
    "turnover": "sales",
    "orders": "orders",
    "order_count": "orders",
    "aov": "aov",
    "average_order_value": "aov",
    "average order value": "aov",
}
STOREFRONT_CHANNEL_NAME = "Storefront"
PLUGIN_SNAPSHOT_NOTE = (
    "Totals from the shop's agent-business-snapshot (SwagCommerceAgentTools), cancelled "
    "orders excluded; Shopware has no traffic or conversion source."
)
PLUGIN_APPLIED_NOTE = "applied: agent-change-apply wrote the change through Shopware's ledger"
Clock = Callable[[], datetime]
_ORDER_ID = re.compile(r"^[0-9a-f]{32}$")
PluginItems = list[dict[str, Any]]


def _margin_pct(price: float, unit_cost: float | None) -> float | None:
    if unit_cost is None or price <= 0:
        return None
    return round((price - unit_cost) / price * 100, 1)


class ShopwareMerchantBackend(MerchantBackend):
    def __init__(
        self,
        admin: AdminTransport,
        settings: ShopwareSettings,
        config: MerchantAgentConfig,
        *,
        ledger: SqliteChangeLedger | None = None,
        clock: Clock | None = None,
        agent_tools: MerchantAgentTools | None = None,
    ) -> None:
        self.admin = admin
        self._settings = settings
        self._config = config
        self.ledger = ledger or SqliteChangeLedger(config, settings.ledger_dsn)
        #: The plugin path (``SHOPWARE_AGENT_TOOLS``); ``warm()`` detects the effective mode.
        self.agent_tools = agent_tools or MerchantAgentTools(admin)
        self.catalog = CatalogCache(admin)
        self._writer = ShopwareWriter(admin, self.catalog)
        self.thresholds = load_thresholds(
            DATA_DIR / "thresholds.json", fallback_default=settings.low_stock_default
        )
        self.pricing_policy = load_pricing_policy(DATA_DIR / "pricing_policy.json")
        self._clock = clock
        self._recent_orders: list[dict[str, Any]] = []
        self._sales_channel_id: str | None = settings.sales_channel_id or None
        self._sales_channel_name: str | None = None

    # ------------------------------------------------------------------ identity

    @property
    def store_name(self) -> str:
        return self._settings.store_name or self._sales_channel_name or "Shopware"

    @property
    def display_currency(self) -> str:
        return CURRENCY

    @property
    def sales_channel_id(self) -> str | None:
        return self._sales_channel_id

    def now(self) -> datetime:
        return (self._clock() if self._clock else datetime.now(UTC)).astimezone(UTC)

    def all_listings(self) -> list[Listing]:
        return self.catalog.all_listings()

    @property
    def plugin_tools_active(self) -> bool:
        return self.agent_tools.active

    async def warm(self) -> None:
        """Agent-tools detection, catalog, sales channel and the recent-order feed. The
        catalog must load; the other reads degrade to empty with a warning."""
        await self.agent_tools.detect()
        await self.catalog.refresh()
        try:
            await self._resolve_sales_channel()
        except AdminAPIError as error:
            logger.warning("sales channel lookup failed: %s", error)
        try:
            self._recent_orders = await self._search_orders(
                [], limit=RECENT_ORDERS_LIMIT, sort_desc=True
            )
        except AdminAPIError as error:
            logger.warning("recent orders unavailable: %s", error)
            self._recent_orders = []

    async def _resolve_sales_channel(self) -> None:
        criteria: dict[str, Any] = {"includes": {"sales_channel": ["id", "name", "translated"]}}
        if self._sales_channel_id:
            criteria["filter"] = [
                {"type": "equals", "field": "id", "value": self._sales_channel_id}
            ]
        result = await self.admin.search("sales_channel", criteria, limit=25)
        rows = result.rows
        if not rows:
            return
        chosen = next(
            (r for r in rows if _name(r) == STOREFRONT_CHANNEL_NAME),
            rows[0],
        )
        self._sales_channel_id = self._sales_channel_id or str(chosen.get("id"))
        self._sales_channel_name = _name(chosen)

    # ------------------------------------------------------------------ performance

    async def _totals(self, period: Period) -> tuple[float, int]:
        aggregations = await self.admin.aggregate(
            "order", totals_aggregations(), period_filters(period)
        )
        return read_sum(aggregations, "sales"), read_count(aggregations, "orders")

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        window = parse_period(period, now=self.now())
        sales, orders = await self._totals(window)
        previous_sales, previous_orders = await self._totals(window.previous)
        alerts = await self.get_inventory_alerts(session)
        issues = await self.get_order_issues(session)
        return BusinessSnapshot(
            period=window.label,
            compare_to=window.previous.label,
            sales=sales,
            orders=orders,
            traffic=None,
            conversion_rate=None,
            average_order_value=round(sales / orders, 2) if orders else None,
            sales_change_pct=change_pct(sales, previous_sales),
            orders_change_pct=change_pct(orders, previous_orders),
            currency=CURRENCY,
            alerts=AlertCounts(
                low_stock=sum(1 for a in alerts if a.kind == "low_stock"),
                slow_movers=sum(1 for a in alerts if a.kind == "slow_mover"),
                order_issues=len(issues),
                pending_changes=len(self.ledger.pending()),
            ),
            note="Totals from Admin order aggregations, cancelled orders excluded; "
            "Shopware has no traffic or conversion source.",
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        key = METRIC_ALIASES.get((metric or "").strip().lower())
        window = parse_period(period, now=self.now())
        step = granularity if granularity in GRANULARITIES else "day"
        if key is None:
            return MetricSeries(
                metric=metric,
                granularity=step,  # type: ignore[arg-type]
                period=window.label,
                points=[],
                note=f"{metric} is not sourced from Shopware — no traffic or conversion data",
            )
        unit = CURRENCY if key in {"sales", "aov"} else "count"
        note: str | None = None
        if segment:
            await self._ensure_catalog()
            product_ids = self._segment_product_ids(segment)
            if not product_ids:
                return MetricSeries(
                    metric=key,
                    unit=unit,
                    granularity=step,  # type: ignore[arg-type]
                    period=window.label,
                    segment=segment,
                    points=[],
                    note=f"segment {segment!r} matches no category or listing",
                )
            aggregations = await self.admin.aggregate(
                "order_line_item",
                [
                    {
                        "name": "series",
                        "type": "histogram",
                        "field": "order.orderDateTime",
                        "interval": step,
                        "aggregation": {"name": "sales", "type": "sum", "field": "totalPrice"},
                    }
                ],
                [
                    {"type": "equalsAny", "field": "productId", "value": product_ids},
                    *line_item_period_filters(window),
                ],
            )
            if key == "orders":
                note = "segment series counts order lines for the segment's products"
        else:
            aggregations = await self.admin.aggregate(
                "order", histogram_aggregation(step), period_filters(window)
            )
        return MetricSeries(
            metric=key,
            unit=unit,
            granularity=step,  # type: ignore[arg-type]
            period=window.label,
            segment=segment,
            points=series_points(aggregations, window, step, key),
            note=note,
        )

    def _segment_product_ids(self, segment: str) -> list[str]:
        wanted = segment.strip().casefold()
        ids: list[str] = []
        for record in self.catalog.cached():
            category = (record.category or "").casefold()
            matches = wanted in {
                category,
                record.listing_id.casefold(),
                record.product_number.casefold(),
            }
            if not matches and category and wanted in category:
                matches = True
            if matches:
                ids.append(record.listing_id)
                ids.extend(c.listing_id for c in record.children)
        return list(dict.fromkeys(ids))

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        raise ChangeNotApplicable(
            "campaign performance is not available — Shopware has no marketing-activity "
            "read in this deployment"
        )

    # ------------------------------------------------------------------ catalog

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        await self._ensure_catalog()
        records = self.catalog.cached()
        cleaned = query.strip()
        if cleaned and cleaned not in {"*", "all"}:
            tokens = {t.casefold() for t in cleaned.replace(",", " ").split() if len(t) > 1}
            exact = [
                r
                for r in records
                if r.listing_id.casefold() in tokens
                or r.product_number.casefold() in tokens
                or any(c.product_number.casefold() in tokens for c in r.children)
            ]
            records = exact or [r for r in records if any(t in _haystack(r) for t in tokens)]
        return filter_listings(
            [r.to_listing() for r in records], filters, limit, sales_of=lambda _id: 0.0
        )

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        record = await self.catalog.get(listing_id)
        return None if record is None else record.to_details()

    # ------------------------------------------------------------------ inventory / orders

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        await self._ensure_catalog()
        alerts: list[InventoryAlert] = []
        rows = self.catalog.stock_rows()
        for record in rows:
            threshold = self.thresholds.low_stock_for(record.product_number)
            if record.stock <= threshold:
                alerts.append(
                    InventoryAlert(
                        listing_id=record.listing_id,
                        title=record.title,
                        kind="low_stock",
                        option_values={"sku": record.product_number} if record.parent_id else {},
                        variant_of=record.parent_id,
                        stock=record.stock,
                        threshold=threshold,
                        storefront_visible=record.active and record.stock > 0,
                    )
                )
        window = Period(
            start=self.now().replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=self.thresholds.slow_mover_window_days - 1),
            end=self.now() + timedelta(seconds=1),
            label=f"last_{self.thresholds.slow_mover_window_days}d",
        )
        try:
            sold = units_by_product(
                await self.admin.aggregate(
                    "order_line_item", units_sold_aggregation(), line_item_period_filters(window)
                )
            )
        except AdminAPIError as error:
            logger.warning("slow-mover aggregation unavailable: %s", error)
            return alerts
        for record in rows:
            if record.stock > 0 and record.active and sold.get(record.listing_id, 0) == 0:
                alerts.append(
                    InventoryAlert(
                        listing_id=record.listing_id,
                        title=record.title,
                        kind="slow_mover",
                        option_values={"sku": record.product_number} if record.parent_id else {},
                        variant_of=record.parent_id,
                        stock=record.stock,
                        sales_last_30d=0,
                        storefront_visible=True,
                    )
                )
        return alerts

    async def _search_orders(
        self, filters: list[dict[str, Any]], *, limit: int, sort_desc: bool = True
    ) -> list[dict[str, Any]]:
        criteria: dict[str, Any] = {
            "associations": order_associations(),
            "sort": [{"field": "orderDateTime", "order": "DESC" if sort_desc else "ASC"}],
            "filter": [*filters, not_third_party("lineItems.product.")],
        }
        result = await self.admin.search("order", criteria, limit=limit)
        return result.rows

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        stuck = await self._search_orders(
            [
                {
                    "type": "equalsAny",
                    "field": "stateMachineState.technicalName",
                    "value": list(OPEN_ORDER_STATES),
                }
            ],
            limit=ISSUE_SEARCH_LIMIT,
        )
        payment = await self._search_orders(
            [
                {
                    "type": "equalsAny",
                    "field": "transactions.stateMachineState.technicalName",
                    "value": list(PAYMENT_PROBLEM_STATES),
                }
            ],
            limit=ISSUE_SEARCH_LIMIT,
        )
        commented = await self._search_orders(
            [
                {
                    "type": "not",
                    "operator": "or",
                    "queries": [
                        {"type": "equals", "field": "customerComment", "value": None},
                        {"type": "equals", "field": "customerComment", "value": ""},
                    ],
                }
            ],
            limit=ISSUE_SEARCH_LIMIT,
        )
        merged: dict[str, dict[str, Any]] = {}
        for row in [*stuck, *payment, *commented]:
            merged.setdefault(str(row.get("id")), row)
        return derive_issues(
            list(merged.values()),
            now=self.now(),
            delayed_after_days=self.thresholds.delayed_after_days,
        )

    # ------------------------------------------------------------------ pricing

    @property
    def _stated_price_cap(self) -> float | None:
        """The price-move cap the model is told; none when the cap is off (inf)."""
        cap = self._config.max_price_delta_pct
        return None if math.isinf(cap) else cap

    def _pricing_context(self, record: ProductRecord) -> PricingContext:
        price = record.price
        floor, basis = self.pricing_policy.floor_for(record.product_number, record.purchase_price)
        return PricingContext(
            listing_id=record.listing_id,
            current_price=price,
            currency=CURRENCY,
            unit_cost=record.purchase_price,
            margin_pct=_margin_pct(price, record.purchase_price),
            min_price=floor,
            min_price_basis=basis,  # type: ignore[arg-type]
            max_price_delta_pct=self._stated_price_cap,
            max_promotion_discount_pct=self._config.max_promotion_discount_pct,
            option_values={"sku": record.product_number} if record.parent_id else {},
        )

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        record = await self.catalog.get(listing_id)
        if record is None:
            return None
        if not record.children:
            return self._pricing_context(record)
        variants = [self._pricing_context(child) for child in record.children]
        return PricingContext(
            listing_id=record.listing_id,
            current_price=min(v.current_price for v in variants),
            currency=CURRENCY,
            max_price_delta_pct=self._stated_price_cap,
            max_promotion_discount_pct=self._config.max_promotion_discount_pct,
            variants=variants,
        )

    # ------------------------------------------------------------------ staging

    async def _stage(
        self,
        *,
        session: MerchantSessionContext,
        kind: ChangeKind,
        summary: str,
        items: list[ChangeItem],
        payloads: list[WritePayload],
        notes: list[str] | None = None,
        margin_impact: float | None = None,
        margin_before_pct: float | None = None,
        margin_after_pct: float | None = None,
        previewed: bool = False,
    ) -> StagedChange:
        """Guardrails, then the server dry run for every payload, then the ledger. A
        payload Shopware refuses never reaches the ledger. ``previewed``: the caller ran
        its own dry run (a state transition is no upsert) and its notes say so."""
        if violations := check_guardrails(kind, items, self._config):
            raise GuardrailViolation(violations)
        preview_notes: list[str] = []
        for entity, payload in [] if previewed else payloads:
            try:
                result = await self._writer.preview(entity, payload)
            except PreviewRejected as error:
                raise ChangeNotApplicable(f"Shopware rejected the preview: {error}") from error
            except AdminAPIError as error:
                raise ChangeNotApplicable(
                    f"Shopware could not preview the change: {error}"
                ) from error
            preview_notes.append(preview_note(result))
        change = self.ledger.stage(
            kind=kind,
            summary=summary,
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=CURRENCY,
            margin_impact=margin_impact,
            margin_before_pct=margin_before_pct,
            margin_after_pct=margin_after_pct,
            guardrail_notes=[*(notes or []), *preview_notes],
        )
        self.ledger.set_payloads(change.change_id, payloads)
        return change

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        record = await self._require_record(listing_id)
        unknown = [name for name in fields if name not in LISTING_FIELDS]
        if unknown:
            raise ChangeNotApplicable(
                f"field {unknown[0]!r} cannot be staged as a listing update on Shopware; "
                f"writable: {', '.join(sorted(LISTING_FIELDS))}"
            )
        if not fields:
            raise ChangeNotApplicable("the listing update names no field")
        fresh = await self.catalog.fresh(record.listing_id) or {}
        items = [
            ChangeItem(
                target=record.listing_id,
                field=name,
                before=_current_text(fresh, record, LISTING_FIELDS[name]),
                after=value,
            )
            for name, value in fields.items()
        ]
        return await self._stage(
            session=session,
            kind=ChangeKind.LISTING_UPDATE,
            summary=note or f"Update listing {record.title}",
            items=items,
            payloads=[("product", listing_payload(record.listing_id, fields))],
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items: list[ChangeItem] = []
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        margins: list[tuple[float, float]] = []
        for item in items:
            record = await self._require_record(item.listing_id)
            targets = self._price_targets(record)
            for target in targets:
                fresh = await self.catalog.fresh(target.listing_id)
                if fresh is None:
                    raise ChangeNotApplicable(
                        f"listing {target.listing_id} is no longer in Shopware"
                    )
                before = current_gross(target, fresh)
                inherited = before is None and target.parent is not None
                if before is None:
                    before = target.price
                tax_rate, tax_note = tax_rate_for(target, fresh)
                if tax_note:
                    notes.append(tax_note)
                entries = price_payload(
                    current_price_entries(target, fresh), item.new_price, tax_rate
                )
                rows.append({"id": target.listing_id, "price": entries})
                change_items.append(
                    ChangeItem(
                        target=target.listing_id, field="price", before=before, after=item.new_price
                    )
                )
                label = target.product_number or target.title
                eur = next(e for e in entries if e["currencyId"] == EUR_CURRENCY_ID)
                notes.append(
                    f"{label}: {before:.2f} → {item.new_price:.2f} {CURRENCY} "
                    f"(net {eur['net']:.2f} at {tax_rate:g} %)"
                )
                if inherited:
                    notes.append(
                        f"{label} inherited the family price until now; it will carry its own price"
                    )
                if target.purchase_price is not None:
                    margin_before = _margin_pct(before, target.purchase_price)
                    margin_after = _margin_pct(item.new_price, target.purchase_price)
                    if margin_before is not None and margin_after is not None:
                        margins.append((margin_before, margin_after))
                        notes.append(
                            f"{label} margin: {margin_before}% → {margin_after}% "
                            f"({margin_after - margin_before:+.1f} pts)"
                        )
        return await self._stage(
            session=session,
            kind=ChangeKind.PRICE_UPDATE,
            summary=note or f"Price update for {len(change_items)} listing(s)",
            items=change_items,
            payloads=[("product", rows)],
            notes=notes,
            margin_before_pct=margins[0][0] if len(margins) == 1 else None,
            margin_after_pct=margins[0][1] if len(margins) == 1 else None,
        )

    @staticmethod
    def _price_targets(record: ProductRecord) -> list[ProductRecord]:
        """The rows a price write lands on. A family whose children all inherit is priced
        on the parent; a family with per-variant prices is repriced per variant (plus the
        parent for the children that still inherit); a plain product or variant is itself."""
        if not record.children:
            return [record]
        priced = [c for c in record.children if c.own_price is not None]
        if not priced:
            return [record]
        targets = list(priced)
        if len(priced) < len(record.children):
            targets.append(record)
        return targets

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items: list[ChangeItem] = []
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        for item in items:
            record = await self._require_record(item.listing_id)
            if item.action == "restock":
                if record.children:
                    raise ChangeNotApplicable(
                        f"{record.product_number or record.listing_id} is a family; restock one of its "
                        f"variants: {', '.join(c.product_number for c in record.children)}"
                    )
                quantity = int(item.quantity or 0)
                if quantity <= 0:
                    raise ChangeNotApplicable(
                        f"restock of {record.title} needs a positive quantity"
                    )
                fresh = await self.catalog.fresh(record.listing_id)
                current = int((fresh or {}).get("stock") if fresh else record.stock) or 0
                change_items.append(
                    ChangeItem(
                        target=record.listing_id,
                        field="stock",
                        before=current,
                        after=current + quantity,
                    )
                )
                rows.append({"id": record.listing_id, "stock": current + quantity})
                notes.append(
                    f"{record.product_number or record.title}: stock {current} → {current + quantity} "
                    f"(+{quantity}; the delta is applied to the stock level at apply time)"
                )
                continue
            active = item.action == "activate"
            affected = [record, *record.children] if record.children else [record]
            if record.children:
                notes.append(
                    f"{record.product_number or record.title} is a family: {item.action} covers the "
                    f"parent and {len(record.children)} variant(s)"
                )
            for target in affected:
                change_items.append(
                    ChangeItem(
                        target=target.listing_id,
                        field="status",
                        before=target.status,
                        after="active" if active else "paused",
                    )
                )
                rows.append({"id": target.listing_id, "active": active})
        return await self._stage(
            session=session,
            kind=ChangeKind.INVENTORY_ACTION,
            summary=note or f"Inventory action for {len(items)} listing(s)",
            items=change_items,
            payloads=[("product", rows)],
            notes=notes,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        if not self._sales_channel_id:
            try:
                await self._resolve_sales_channel()
            except AdminAPIError as error:
                raise ChangeNotApplicable(f"no sales channel for the promotion: {error}") from error
        if not self._sales_channel_id:
            raise ChangeNotApplicable(
                "no sales channel to bind the promotion to — set SHOPWARE_SALES_CHANNEL_ID"
            )
        items: list[ChangeItem] = []
        variant_ids: list[str] = []
        for listing_id in promotion.listing_ids:
            record = await self._require_record(listing_id)
            price = record.to_listing().price
            after = round(price * (1 - promotion.discount_pct / 100), 2)
            items.append(
                ChangeItem(target=record.listing_id, field="price", before=price, after=after)
            )
            if record.children:
                variant_ids.extend(c.listing_id for c in record.children)
            else:
                variant_ids.append(record.listing_id)
        plan = promotion_payload(
            name=promotion.name,
            discount_pct=promotion.discount_pct,
            starts=promotion.starts,
            ends=promotion.ends,
            sales_channel_id=self._sales_channel_id,
            variant_ids=list(dict.fromkeys(variant_ids)),
        )
        valid_from = plan.payload["validFrom"]
        valid_until = plan.payload["validUntil"]
        notes = [
            f"promotion {plan.promotion_id}: {promotion.discount_pct:g} % off, cart scope, "
            f"valid {valid_from} → {valid_until}, sales channel {self._sales_channel_id}",
            f"rule {plan.rule_id} limits it to carts holding one of {len(plan.variant_ids)} product(s); "
            "the discount applies to the whole cart (per-line scoping is a Phase 3 refinement)",
        ]
        return await self._stage(
            session=session,
            kind=ChangeKind.PROMOTION,
            summary=f"{promotion.name} ({promotion.discount_pct:g}% off, {promotion.starts} to {promotion.ends})",
            items=items,
            payloads=[("promotion", plan.payload)],
            notes=notes,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        raise ChangeNotApplicable(
            "campaigns are not applied to Shopware in this deployment — stage a promotion "
            "or listing update instead"
        )

    async def _order(self, order: str) -> dict[str, Any] | None:
        """The order by number, or by id when ``order`` is a 32-character hex id."""
        field = "id" if _ORDER_ID.match(order) else "orderNumber"
        rows = await self._search_orders(
            [{"type": "equals", "field": field, "value": order}], limit=1
        )
        return rows[0] if rows else None

    async def _refund_transitions(
        self, order_number: str, *, dry_run: bool
    ) -> list[dict[str, Any]]:
        """The ``refund`` transition on *every* payment transaction of the order, as
        Shopware answers it: a dry run lists ``from``, ``actionValid`` and
        ``availableTransitions``; a real run moves each transaction where the action is
        valid and answers ``from``, ``to`` and ``executed`` (``OrderStateTool``, 6.7.13)."""
        data = await self.admin.order_state(
            order_number, transaction_action=REFUND_ACTION, dry_run=dry_run
        )
        return [t for t in data.get("transactions") or [] if isinstance(t, dict)]

    async def stage_refund(
        self, session: MerchantSessionContext, order: str, note: str | None = None
    ) -> StagedChange:
        try:
            row = await self._order(order)
            if row is None:
                raise ChangeNotApplicable(f"no order {order!r} in Shopware")
            number = str(row.get("orderNumber") or "")
            transactions = await self._refund_transitions(number, dry_run=True)
        except AdminAPIError as error:
            raise ChangeNotApplicable(f"Shopware could not preview the refund: {error}") from error
        valid = [t for t in transactions if t.get("actionValid")]
        refundable = [t for t in valid if t.get("from") in REFUNDABLE_STATES]
        if not refundable:
            states = ", ".join(sorted({str(t.get("from")) for t in transactions})) or "none"
            if any(t.get("actionValid") for t in transactions):
                raise ChangeNotApplicable(
                    f"order {number} cannot be refunded: its payment is {states}, so no money "
                    "was collected to give back"
                )
            allowed = sorted(
                {
                    str(step.get("actionName"))
                    for t in transactions
                    for step in t.get("availableTransitions") or []
                }
            )
            raise ChangeNotApplicable(
                f"order {number} cannot be refunded: its payment is {states}, and Shopware "
                f"allows {', '.join(allowed) or 'no transition'} from there"
            )
        if len(valid) > 1:
            # The tool moves every transaction the action is valid on, not just one.
            states = ", ".join(str(t.get("from")) for t in valid)
            raise ChangeNotApplicable(
                f"order {number} has {len(valid)} payments Shopware would move to refunded "
                f"together ({states}); refund it in the Shopware Administration, where one "
                "transaction can be picked"
            )
        transaction = refundable[0]
        before = str(transaction.get("from"))
        after = next(
            (
                str(step.get("toStateName"))
                for step in transaction.get("availableTransitions") or []
                if step.get("actionName") == REFUND_ACTION
            ),
            "refunded",
        )
        amount = row.get("amountTotal")
        amount_text = f"{float(amount):.2f} {CURRENCY}" if amount is not None else "full amount"
        return await self._stage(
            session=session,
            kind=REFUND_KIND,
            summary=note or f"Refund order {number} ({amount_text})",
            items=[ChangeItem(target=number, field=REFUND_FIELD, before=before, after=after)],
            payloads=[
                (
                    ORDER_STATE_ENTITY,
                    {
                        "orderNumber": number,
                        "transactionAction": REFUND_ACTION,
                        "transactionId": transaction.get("id"),
                        "toState": after,
                    },
                )
            ],
            notes=[
                f"order {number}: payment {before} → {after}, full refund of {amount_text}",
                f"preview: server dry-run OK — transition {REFUND_ACTION!r} is valid on "
                f"transaction {transaction.get('id')}",
                "Shopware records the payment as refunded; whether the money goes back to "
                "the buyer depends on the payment method's plugin",
            ],
            previewed=True,
        )

    async def _apply_refund(self, change: StagedChange, payloads: list[WritePayload]) -> list[str]:
        """Dry-run again (the transition must still touch exactly the staged transaction),
        run it for real, then read the payment state back: the change counts as applied
        only when Shopware shows the target state."""
        notes: list[str] = []
        for _entity, plan in payloads:
            if not isinstance(plan, dict):
                raise WriteFailed(f"change {change.change_id} has an unreadable refund plan")
            number = str(plan.get("orderNumber"))
            planned_id = plan.get("transactionId")
            try:
                preview = await self._refund_transitions(number, dry_run=True)
            except AdminAPIError as error:
                raise WriteFailed(str(error)) from error
            planned = next((t for t in preview if t.get("id") == planned_id), None)
            if planned is None or not planned.get("actionValid"):
                state = planned.get("from") if planned else "gone"
                raise WriteFailed(
                    f"order {number}: the payment is {state} now and cannot be refunded"
                )
            others = [t for t in preview if t.get("actionValid") and t.get("id") != planned_id]
            if others:
                raise WriteFailed(
                    f"order {number}: Shopware would now also move "
                    f"{', '.join(str(t.get('from')) for t in others)} payment(s) to refunded; "
                    "nothing was written"
                )
            try:
                transactions = await self._refund_transitions(number, dry_run=False)
                row = await self._order(number)
            except AdminAPIError as error:
                raise WriteFailed(str(error)) from error
            ran = next((t for t in transactions if t.get("id") == planned_id), None)
            if ran is None or ran.get("executed") is not True:
                reason = (ran or {}).get("note") or "no result for the staged transaction"
                raise WriteFailed(f"order {number}: Shopware did not run the refund ({reason})")
            now = next(
                (
                    (t.get("stateMachineState") or {}).get("technicalName")
                    for t in (row or {}).get("transactions") or []
                    if t.get("id") == plan.get("transactionId")
                ),
                None,
            )
            if now != plan.get("toState"):
                raise WriteFailed(
                    f"order {number}: Shopware accepted the refund but the payment reads "
                    f"{now or 'unknown'}, not {plan.get('toState')}"
                )
            notes.append(f"applied: order {number} payment {ran.get('from')} → {now}")
        return notes

    # ------------------------------------------------------------------ queue

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        return self.ledger.pending()

    def _require_staged(self, change_id: str, action: str) -> StagedChange:
        change = self.ledger.get(change_id)
        if change is None:
            raise ChangeNotApplicable(f"no change with id {change_id!r} to {action}")
        if change.status is not ChangeStatus.STAGED:
            raise ChangeNotApplicable(
                f"change {change_id} is {change.status.value}, not staged — nothing to {action}"
            )
        return change

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        change = self._require_staged(change_id, "apply")
        refund = is_refund(change)
        if change.kind is ChangeKind.CAMPAIGN and not refund:
            raise ChangeNotApplicable("campaigns are not applied to Shopware in this deployment")
        if violations := check_guardrails(change.kind, change.items, self._config):
            raise GuardrailViolation(violations)
        payloads = self.ledger.payloads(change_id)
        if not payloads:
            raise ChangeNotApplicable(
                f"change {change_id} has no stored write plan — stage it again. It is still staged."
            )
        try:
            if refund:
                notes = await self._apply_refund(change, payloads)
            else:
                notes = await self._writer.apply(change, payloads)
        except WriteFailed as error:
            logger.warning("apply %s failed: %s", change_id, error.report())
            raise ChangeNotApplicable(
                f"the store did not accept change {change_id}: {error.report()}. It is still staged."
            ) from error
        self.ledger.apply(change_id, session.operator)
        return self.ledger.annotate(change_id, notes)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        self._require_staged(change_id, "discard")
        return self.ledger.discard(change_id, session.operator, actor_kind)

    # ------------------------------------------------------------------ context / portal

    @property
    def previews_server_validated(self) -> bool:
        return self.admin.name != "rest"

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        return {
            "store": self.store_name,
            "currency": CURRENCY,
            "transport": self.admin.name,
            "sales_channel": self._sales_channel_id,
            "previews_server_validated": self.previews_server_validated,
            "slow_mover_window_days": self.thresholds.slow_mover_window_days,
            "limitations": [
                {"source": "admin-api", "note": "No traffic or conversion source in Shopware."},
                {"source": "admin-api", "note": "Marketing campaigns are not read or written."},
                {
                    "source": "promotion",
                    "note": "A promotion discounts the whole cart when a listed product is in it.",
                },
            ],
        }

    def shop_info(self) -> dict[str, Any]:
        return {
            "name": self._sales_channel_name or self.store_name,
            "operator": self._settings.operator,
            "currency": CURRENCY,
            "transport": self.admin.name,
            "sales_channel": self._sales_channel_id,
        }

    def recent_orders(self, limit: int = 6) -> list[Order]:
        orders = [to_shopping_order(row) for row in self._recent_orders[:limit]]
        return [o for o in orders if o is not None]

    async def dashboard(
        self, session: MerchantSessionContext, period: str | None
    ) -> dict[str, Any]:
        window = parse_period(period or "last_7d", now=self.now())
        step = "day" if window.days <= 31 else "week"
        sales, orders = await self._totals(window)
        previous_sales, previous_orders = await self._totals(window.previous)
        aggregations = await self.admin.aggregate(
            "order", histogram_aggregation(step), period_filters(window)
        )
        alerts = await self.get_inventory_alerts(session)
        issues = await self.get_order_issues(session)
        aov = round(sales / orders, 2) if orders else None
        previous_aov = round(previous_sales / previous_orders, 2) if previous_orders else None
        sales_change = change_pct(sales, previous_sales)
        return {
            "period": {"label": window.display(), "against": window.against(), "key": window.label},
            "kpis": {
                "sales": {
                    "value": sales,
                    "unit": CURRENCY,
                    "change_pct": sales_change,
                    "points": _points(series_points(aggregations, window, step, "sales")),
                },
                "orders": {
                    "value": orders,
                    "unit": "count",
                    "change_pct": change_pct(orders, previous_orders),
                    "points": _points(series_points(aggregations, window, step, "orders")),
                },
                "conversion": {
                    "value": None,
                    "note": "Shopware has no traffic source, so conversion cannot be computed.",
                },
                "average_order": {
                    "value": aov,
                    "unit": CURRENCY,
                    "change_pct": change_pct(aov or 0.0, previous_aov or 0.0),
                    "points": _points(series_points(aggregations, window, step, "aov")),
                },
            },
            "digest": _digest(sales_change, window, len(issues), len(alerts)),
        }

    async def portal_orders(self, limit: int) -> list[dict[str, Any]]:
        rows = await self._search_orders([], limit=max(1, min(limit, ISSUE_SEARCH_LIMIT)))
        self._recent_orders = rows or self._recent_orders
        return [
            portal_order(row, now=self.now(), delayed_after_days=self.thresholds.delayed_after_days)
            for row in rows
        ]

    def changes(self, status: str | None) -> list[StagedChange]:
        wanted = (status or "staged").strip().lower()
        if wanted == "all":
            return self.ledger.all()
        return [c for c in self.ledger.all() if c.status.value == wanted]

    # ------------------------------------------------------------------ helpers

    async def _ensure_catalog(self) -> None:
        if not self.catalog.cached():
            await self.catalog.refresh()

    async def _require_record(self, listing_id: str) -> ProductRecord:
        record = await self.catalog.get(listing_id)
        if record is None:
            raise ChangeNotApplicable(f"listing {listing_id} is not in the Shopware catalog")
        return record


def _name(row: dict[str, Any]) -> str | None:
    value = row.get("name") or (row.get("translated") or {}).get("name")
    return str(value) if value else None


def _haystack(record: ProductRecord) -> str:
    parts = [record.title, record.listing_id, record.product_number, record.category or ""]
    parts.extend(f"{c.title} {c.product_number}" for c in record.children)
    return " ".join(parts).casefold()


def _current_text(fresh: dict[str, Any], record: ProductRecord, field: str) -> Any:
    value = fresh.get(field)
    if value in (None, ""):
        value = (fresh.get("translated") or {}).get(field)
    if value in (None, ""):
        value = {
            "name": record.title,
            "description": record.description,
            "metaTitle": record.meta_title,
            "metaDescription": record.meta_description,
        }.get(field)
    return value


def _points(points: list) -> list[dict[str, Any]]:
    return [{"date": p.date, "value": p.value} for p in points]


def _digest(sales_change: float | None, window: Period, issues: int, alerts: int) -> str:
    span = "on the week" if window.days == 7 else f"against {window.against()}"
    if sales_change is None:
        lead = f"No prior-period sales to compare {span}."
    elif abs(sales_change) < 0.05:
        lead = f"Sales are flat {span}."
    else:
        lead = f"Sales are {'up' if sales_change > 0 else 'down'} {abs(sales_change):.1f}% {span}."
    orders_text = f"{issues} order{'s' if issues != 1 else ''}"
    listings_text = f"{alerts} listing{'s' if alerts != 1 else ''}"
    return f"{lead} {orders_text} and {listings_text} need you today."
