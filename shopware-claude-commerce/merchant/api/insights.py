# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Performance and health reads over Admin aggregations: reporting periods, the
aggregation definitions the backend sends, bucket parsing, the operator thresholds
(``data/thresholds.json``, ``data/pricing_policy.json``) and the order → issue / order →
portal row mappings. Pure functions over rows the transport returned; nothing here
talks to Shopware.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from merchant_agent import MetricPoint, OrderIssue
from shopping_agent import Order, OrderItem, OrderStatus

logger = logging.getLogger(__name__)

DEFAULT_PERIOD = "last_30d"
DEFAULT_PERIOD_DAYS = 30
DEFAULT_LOW_STOCK = 8
DEFAULT_DELAYED_AFTER_DAYS = 3
DEFAULT_SLOW_MOVER_WINDOW_DAYS = 30
DEFAULT_MIN_MARGIN_PCT = 10.0
SEED_COMMENT_MARKERS = frozenset({"commerce-agents-seed"})
CANCELLED_STATE = "cancelled"
OPEN_ORDER_STATES = ("open", "in_progress")
PAYMENT_PROBLEM_STATES = ("failed", "cancelled", "reminded")
SHIPPED_DELIVERY_STATES = frozenset(
    {"shipped", "shipped_partially", "returned", "returned_partially"}
)
_PERIOD_ALIASES = {
    "week": 7,
    "this_week": 7,
    "last_week": 7,
    "7d": 7,
    "last_7d": 7,
    "last_7_days": 7,
    "last 7 days": 7,
    "month": 30,
    "this_month": 30,
    "last_month": 30,
    "30d": 30,
    "last_30d": 30,
    "last_30_days": 30,
    "last 30 days": 30,
    "quarter": 90,
    "90d": 90,
    "last_90d": 90,
    "last_90_days": 90,
    "last 90 days": 90,
}
GRANULARITIES = ("day", "week", "month")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --------------------------------------------------------------------------- periods


@dataclass(frozen=True)
class Period:
    start: datetime  # inclusive, UTC midnight
    end: datetime  # exclusive
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def previous(self) -> Period:
        start = self.start - timedelta(days=self.days)
        return Period(start=start, end=self.start, label=f"previous {self.days} days")

    def display(self) -> str:
        """``Aug 7–13`` / ``Aug 28 – Sep 3`` for the dashboard header."""
        last = self.end - timedelta(days=1)
        if self.start.month == last.month:
            return f"{_MONTHS[self.start.month - 1]} {self.start.day}–{last.day}"
        return (
            f"{_MONTHS[self.start.month - 1]} {self.start.day} – "
            f"{_MONTHS[last.month - 1]} {last.day}"
        )

    def against(self) -> str:
        if self.days == 7:
            return "the prior week"
        if self.days == 30:
            return "the prior 30 days"
        if self.days == 90:
            return "the prior quarter"
        return f"the prior {self.days} days"


def parse_period(period: str | None, *, now: datetime | None = None) -> Period:
    """``last_7d|last_30d|last_90d`` (default 30 days), lenient about ``7d`` / ``last 7
    days`` / ``week``, or an ISO ``YYYY-MM-DD/YYYY-MM-DD`` range. The window ends today
    (inclusive)."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    today = moment.date()
    text = (period or DEFAULT_PERIOD).strip().lower().replace("-", "_").replace("  ", " ")
    if "/" in text:
        try:
            start_text, end_text = text.split("/", 1)
            start = date.fromisoformat(start_text.strip().replace("_", "-"))
            end = date.fromisoformat(end_text.strip().replace("_", "-"))
            if end >= start:
                return Period(
                    start=_midnight(start),
                    end=_midnight(end + timedelta(days=1)),
                    label=f"{start.isoformat()}/{end.isoformat()}",
                )
        except ValueError:
            pass
    days = _PERIOD_ALIASES.get(text)
    if days is None:
        digits = "".join(ch for ch in text if ch.isdigit())
        days = int(digits) if digits and 0 < int(digits) <= 366 else DEFAULT_PERIOD_DAYS
    start = today - timedelta(days=days - 1)
    return Period(
        start=_midnight(start), end=_midnight(today + timedelta(days=1)), label=f"last_{days}d"
    )


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def change_pct(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


# --------------------------------------------------------------------------- aggregations


#: A product a third-party seller listed through the storefront's seller MCP
#: (``storefront/api/seller_mcp.py``) carries a ``seller:<name>`` tag. It is not this
#: merchant's: the catalog, the orders and every metric leave it out.
THIRD_PARTY_TAG_PREFIX = "seller:"


def not_third_party(product_path: str = "") -> dict[str, Any]:
    """The DAL filter that drops third-party products; ``product_path`` leads from the
    searched entity to the product (``"lineItems.product."`` for orders)."""
    return {
        "type": "not",
        "operator": "and",
        "queries": [
            {
                "type": "contains",
                "field": f"{product_path}tags.name",
                "value": THIRD_PARTY_TAG_PREFIX,
            }
        ],
    }


def period_filters(period: Period, *, field: str = "orderDateTime") -> list[dict[str, Any]]:
    """Orders placed in the period, cancelled ones excluded (as Shopware's own revenue
    report does), third-party orders too."""
    return [
        {
            "type": "range",
            "field": field,
            "parameters": {"gte": period.start.isoformat(), "lt": period.end.isoformat()},
        },
        {
            "type": "not",
            "operator": "and",
            "queries": [
                {
                    "type": "equals",
                    "field": "stateMachineState.technicalName",
                    "value": CANCELLED_STATE,
                }
            ],
        },
        not_third_party("lineItems.product."),
    ]


def totals_aggregations() -> list[dict[str, Any]]:
    return [
        {"name": "orders", "type": "count", "field": "id"},
        {"name": "sales", "type": "sum", "field": "amountTotal"},
    ]


def histogram_aggregation(granularity: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "series",
            "type": "histogram",
            "field": "orderDateTime",
            "interval": granularity,
            "aggregation": {"name": "sales", "type": "sum", "field": "amountTotal"},
        }
    ]


def units_sold_aggregation() -> list[dict[str, Any]]:
    return [
        {
            "name": "by_product",
            "type": "terms",
            "field": "productId",
            "aggregation": {"name": "units", "type": "sum", "field": "quantity"},
        }
    ]


def line_item_period_filters(period: Period) -> list[dict[str, Any]]:
    return period_filters(period, field="order.orderDateTime")[:1] + [
        {
            "type": "not",
            "operator": "and",
            "queries": [
                {
                    "type": "equals",
                    "field": "order.stateMachineState.technicalName",
                    "value": CANCELLED_STATE,
                }
            ],
        },
        not_third_party("product."),
    ]


def read_count(aggregations: dict[str, Any], name: str) -> int:
    value = (aggregations.get(name) or {}).get("count")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_sum(aggregations: dict[str, Any], name: str) -> float:
    value = (aggregations.get(name) or {}).get("sum")
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def units_by_product(aggregations: dict[str, Any], name: str = "by_product") -> dict[str, float]:
    out: dict[str, float] = {}
    for bucket in (aggregations.get(name) or {}).get("buckets") or []:
        key = bucket.get("key")
        if not key:
            continue
        nested = bucket.get("units") or {}
        try:
            out[str(key)] = float(nested.get("sum") or 0)
        except (TypeError, ValueError):
            out[str(key)] = 0.0
    return out


def bucket_date(key: str, granularity: str) -> str | None:
    """Shopware histogram keys → ISO date: ``2026-09-01 00:00:00`` → ``2026-09-01``;
    week keys are ``YYYY WW`` (ISO week) → that week's Monday."""
    text = str(key).strip()
    if granularity == "week":
        try:
            year_text, week_text = text.split()
            return date.fromisocalendar(int(year_text), int(week_text), 1).isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


def series_points(
    aggregations: dict[str, Any], period: Period, granularity: str, metric: str
) -> list[MetricPoint]:
    """Histogram buckets → one point per calendar bucket of the period, zeros filled in.
    ``metric`` is ``sales`` (sum), ``orders`` (count) or ``aov`` (sum / count)."""
    sales: dict[str, float] = {}
    counts: dict[str, int] = {}
    for bucket in (aggregations.get("series") or {}).get("buckets") or []:
        day = bucket_date(str(bucket.get("key", "")), granularity)
        if day is None:
            continue
        counts[day] = counts.get(day, 0) + int(bucket.get("count") or 0)
        with suppress(TypeError, ValueError):
            sales[day] = sales.get(day, 0.0) + float((bucket.get("sales") or {}).get("sum") or 0)
    points: list[MetricPoint] = []
    for day in bucket_starts(period, granularity):
        key = day.isoformat()
        if metric == "orders":
            value: float = counts.get(key, 0)
        elif metric == "aov":
            value = round(sales.get(key, 0.0) / counts[key], 2) if counts.get(key) else 0.0
        else:
            value = round(sales.get(key, 0.0), 2)
        points.append(MetricPoint(date=key, value=value))
    return points


def bucket_starts(period: Period, granularity: str) -> list[date]:
    first = period.start.date()
    last = (period.end - timedelta(days=1)).date()
    if granularity == "week":
        first = first - timedelta(days=first.weekday())
        step = timedelta(days=7)
        current = first
        out = []
        while current <= last:
            out.append(current)
            current += step
        return out
    if granularity == "month":
        current = first.replace(day=1)
        out = []
        while current <= last:
            out.append(current)
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# --------------------------------------------------------------------------- thresholds


@dataclass(frozen=True)
class Thresholds:
    default_low_stock: int = DEFAULT_LOW_STOCK
    per_listing: dict[str, int] | None = None
    delayed_after_days: int = DEFAULT_DELAYED_AFTER_DAYS
    slow_mover_window_days: int = DEFAULT_SLOW_MOVER_WINDOW_DAYS

    def low_stock_for(self, product_number: str | None) -> int:
        if product_number and self.per_listing and product_number in self.per_listing:
            return int(self.per_listing[product_number])
        return self.default_low_stock


def load_thresholds(path: Path, *, fallback_default: int = DEFAULT_LOW_STOCK) -> Thresholds:
    raw = _load_json(path)
    default = raw.get("default", raw.get("low_stock", fallback_default))
    per_listing = {
        str(number): int(value) for number, value in (raw.get("per_listing") or {}).items()
    }
    return Thresholds(
        default_low_stock=int(default),
        per_listing=per_listing,
        delayed_after_days=int(
            raw.get(
                "delayed_after_days", raw.get("fulfilment_sla_days", DEFAULT_DELAYED_AFTER_DAYS)
            )
        ),
        slow_mover_window_days=int(
            raw.get("slow_mover_window_days", DEFAULT_SLOW_MOVER_WINDOW_DAYS)
        ),
    )


@dataclass(frozen=True)
class PricingPolicy:
    min_price: dict[str, float]
    default_min_margin_pct: float = DEFAULT_MIN_MARGIN_PCT

    def floor_for(
        self, product_number: str | None, unit_cost: float | None
    ) -> tuple[float | None, str | None]:
        """``(min_price, basis)``: a store rule per product number wins; otherwise the
        cost-derived floor when the cost is known; otherwise nothing."""
        if product_number and product_number in self.min_price:
            return float(self.min_price[product_number]), "policy"
        if unit_cost is not None and unit_cost > 0 and self.default_min_margin_pct < 100:
            return round(unit_cost / (1 - self.default_min_margin_pct / 100), 2), "cost"
        return None, None


def load_pricing_policy(path: Path) -> PricingPolicy:
    raw = _load_json(path)
    return PricingPolicy(
        min_price={str(k): float(v) for k, v in (raw.get("min_price") or {}).items()},
        default_min_margin_pct=float(raw.get("default_min_margin_pct", DEFAULT_MIN_MARGIN_PCT)),
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        logger.error("%s is not valid JSON (%s); using defaults", path, error)
        return {}
    return raw if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------- orders


def order_associations() -> dict[str, Any]:
    return {
        "stateMachineState": {},
        "orderCustomer": {},
        "lineItems": {},
        "transactions": {"associations": {"stateMachineState": {}}},
        "deliveries": {"associations": {"stateMachineState": {}}},
    }


def _state(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    state = row.get("stateMachineState")
    return str(state.get("technicalName") or "") if isinstance(state, dict) else ""


def order_state(order: dict[str, Any]) -> str:
    return _state(order)


def payment_states(order: dict[str, Any]) -> list[str]:
    return [_state(t) for t in order.get("transactions") or []]


def delivery_states(order: dict[str, Any]) -> list[str]:
    return [_state(d) for d in order.get("deliveries") or []]


def placed_at(order: dict[str, Any]) -> datetime | None:
    raw = order.get("orderDateTime") or order.get("createdAt")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def order_customer(order: dict[str, Any]) -> str | None:
    customer = order.get("orderCustomer")
    if not isinstance(customer, dict):
        return None
    name = " ".join(str(customer.get(k) or "") for k in ("firstName", "lastName")).strip()
    return name or customer.get("email")


def buyer_comment(order: dict[str, Any]) -> str | None:
    comment = str(order.get("customerComment") or "").strip()
    if not comment or comment in SEED_COMMENT_MARKERS:
        return None
    return comment


def derive_issues(
    orders: list[dict[str, Any]], *, now: datetime, delayed_after_days: int
) -> list[OrderIssue]:
    """The exceptions the blueprint's ``OrderIssue.kind`` can express. A failed or
    cancelled payment on a live order is reported under ``delayed`` (the order cannot
    ship) with ``issue_id`` prefixed ``payment_failed:`` — the kind literal has no
    payment variant."""
    issues: list[OrderIssue] = []
    seen: set[str] = set()
    for order in orders:
        order_id = str(order.get("id") or "")
        number = str(order.get("orderNumber") or order_id[:8])
        state = order_state(order)
        opened = placed_at(order)
        age_days = (now - opened).days if opened else 0
        shipped = any(s in SHIPPED_DELIVERY_STATES for s in delivery_states(order))
        payment_problem = state != CANCELLED_STATE and any(
            s in PAYMENT_PROBLEM_STATES for s in payment_states(order)
        )
        # An unpaid order is reported once, as the payment problem that holds it.
        if (
            state in OPEN_ORDER_STATES
            and not shipped
            and not payment_problem
            and age_days >= delayed_after_days
            and f"delayed:{order_id}" not in seen
        ):
            seen.add(f"delayed:{order_id}")
            what = "in progress" if state == "in_progress" else "open"
            issues.append(
                OrderIssue(
                    issue_id=f"delayed:{order_id}",
                    order_id=order_id,
                    kind="delayed",
                    summary=f"Order {number} has been {what} for {age_days} days and has not shipped",
                    listing_id=_first_product(order),
                    opened_at=opened,
                )
            )
        if payment_problem and f"payment_failed:{order_id}" not in seen:
            seen.add(f"payment_failed:{order_id}")
            payment = next(s for s in payment_states(order) if s in PAYMENT_PROBLEM_STATES)
            issues.append(
                OrderIssue(
                    issue_id=f"payment_failed:{order_id}",
                    order_id=order_id,
                    kind="delayed",
                    summary=f"Payment on order {number} is {payment} — it cannot be fulfilled until it is settled",
                    listing_id=_first_product(order),
                    opened_at=opened,
                )
            )
        comment = buyer_comment(order)
        if comment and f"buyer_message:{order_id}" not in seen:
            seen.add(f"buyer_message:{order_id}")
            issues.append(
                OrderIssue(
                    issue_id=f"buyer_message:{order_id}",
                    order_id=order_id,
                    kind="buyer_message",
                    summary=f"Order {number} carries a note from the buyer",
                    listing_id=_first_product(order),
                    buyer_message_excerpt=comment[:200],
                    opened_at=opened,
                )
            )
    issues.sort(key=lambda i: i.opened_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return issues


def issue_label(order: dict[str, Any], *, now: datetime, delayed_after_days: int) -> str | None:
    """The portal's one-word issue tag for an order row, or None."""
    state = order_state(order)
    if state != CANCELLED_STATE and any(s in PAYMENT_PROBLEM_STATES for s in payment_states(order)):
        return "payment_failed"
    opened = placed_at(order)
    if (
        state in OPEN_ORDER_STATES
        and opened is not None
        and (now - opened).days >= delayed_after_days
        and not any(s in SHIPPED_DELIVERY_STATES for s in delivery_states(order))
    ):
        return "delayed"
    if buyer_comment(order):
        return "buyer_message"
    return None


def _first_product(order: dict[str, Any]) -> str | None:
    for item in order.get("lineItems") or []:
        if isinstance(item, dict) and item.get("productId"):
            return str(item["productId"])
    return None


def portal_order(
    order: dict[str, Any], *, now: datetime, delayed_after_days: int
) -> dict[str, Any]:
    opened = placed_at(order)
    row: dict[str, Any] = {
        "order_id": str(order.get("id") or ""),
        "order_number": str(order.get("orderNumber") or ""),
        "status": order_state(order) or "unknown",
        "payment_status": (payment_states(order) or [None])[0],
        "delivery_status": (delivery_states(order) or [None])[0],
        "placed_at": opened.isoformat() if opened else None,
        "total": round(float(order.get("amountTotal") or 0), 2),
        "currency": "EUR",
        "items": sum(
            int(i.get("quantity") or 0) for i in order.get("lineItems") or [] if isinstance(i, dict)
        ),
        "customer": order_customer(order),
    }
    issue = issue_label(order, now=now, delayed_after_days=delayed_after_days)
    if issue:
        row["issue"] = issue
    return row


def to_shopping_order(order: dict[str, Any]) -> Order | None:
    """A Shopware order as the blueprint's storefront ``Order`` (the overview's
    ``recent_orders`` feed)."""
    opened = placed_at(order)
    if opened is None or not order.get("id"):
        return None
    state = order_state(order)
    payments = payment_states(order)
    deliveries = delivery_states(order)
    if state == CANCELLED_STATE:
        status = OrderStatus.CANCELLED
    elif "refunded" in payments or "refunded_partially" in payments:
        status = OrderStatus.REFUNDED
    elif any(s.startswith("returned") for s in deliveries):
        status = OrderStatus.RETURN_INITIATED
    elif state == "completed":
        status = OrderStatus.DELIVERED
    elif any(s in SHIPPED_DELIVERY_STATES for s in deliveries):
        status = OrderStatus.SHIPPED
    else:
        status = OrderStatus.PROCESSING
    items = [
        OrderItem(
            product_id=str(item.get("productId") or item.get("id") or ""),
            title=str(item.get("label") or "Item"),
            quantity=int(item.get("quantity") or 1),
            price=float(item.get("unitPrice") or 0),
        )
        for item in order.get("lineItems") or []
        if isinstance(item, dict)
    ]
    return Order(
        order_id=str(order.get("orderNumber") or order["id"]),
        status=status,
        placed_at=opened,
        items=items,
        total=round(float(order.get("amountTotal") or 0), 2),
        currency="EUR",
    )
