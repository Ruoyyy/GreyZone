# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The write side of the staged-change flow.

Staging builds the exact Shopware payload a change will write and previews it with the
Admin MCP dry run (``ShopwareWriter.preview``); the payload is stored with the change.
``apply_change`` — the only live write — replays that stored payload with
``dry_run=False`` (``ShopwareWriter.apply``). Restocks are the one exception to
"replay as staged": the staged *delta* is applied to the stock read at apply time, so
two restocks staged against the same starting level both count.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from merchant_agent import ChangeItem, ChangeKind, StagedChange

from .admin_client import EUR_CURRENCY_ID, AdminAPIError, AdminTransport, WriteResult
from .catalog import CatalogCache, ProductRecord, eur_entry

logger = logging.getLogger(__name__)

LISTING_FIELDS = {
    "title": "name",
    "name": "name",
    "description": "description",
    "short_description": "description",
    "long_description": "description",
    "seo_title": "metaTitle",
    "seo_description": "metaDescription",
}
#: Entities a change *creates*; on a partial failure these are deleted again.
CREATED_ENTITIES = frozenset({"promotion", "rule"})
FALLBACK_TAX_RATE = 19.0
PROMOTION_SCOPE = "cart"
PROMOTION_DISCOUNT_TYPE = "percentage"
PROMOTION_PRIORITY = 1
RULE_PRIORITY = 1
WritePayload = tuple[str, dict[str, Any] | list[dict[str, Any]]]


class WriteFailed(RuntimeError):
    """A live write did not go through. ``completed`` names what was written before the
    failure and ``rolled_back`` what was deleted again."""

    def __init__(
        self,
        message: str,
        *,
        completed: list[str] | None = None,
        rolled_back: list[str] | None = None,
    ) -> None:
        self.completed = completed or []
        self.rolled_back = rolled_back or []
        super().__init__(message)

    def report(self) -> str:
        parts = [str(self)]
        if self.completed:
            parts.append("written before the failure: " + ", ".join(self.completed))
        if self.rolled_back:
            parts.append("rolled back: " + ", ".join(self.rolled_back))
        return "; ".join(parts)


class PreviewRejected(RuntimeError):
    """Shopware's dry run refused the payload; the message is the server's error."""


# --------------------------------------------------------------------------- payloads


def net_of(gross: float, tax_rate: float) -> float:
    return round(gross / (1 + tax_rate / 100), 2)


def price_payload(
    current_entries: list[dict[str, Any]] | None, gross: float, tax_rate: float
) -> list[dict[str, Any]]:
    """The product's ``price`` list with only the EUR entry replaced (other currencies
    keep their values), ``net`` derived from the tax rate, ``linked`` kept true."""
    gross = round(float(gross), 2)
    eur = {
        "currencyId": EUR_CURRENCY_ID,
        "gross": gross,
        "net": net_of(gross, tax_rate),
        "linked": True,
    }
    entries: list[dict[str, Any]] = []
    replaced = False
    for entry in current_entries or []:
        if not isinstance(entry, dict):
            continue
        clean = {k: v for k, v in entry.items() if k not in {"apiAlias", "extensions"}}
        if clean.get("currencyId") == EUR_CURRENCY_ID:
            entries.append({**clean, **eur})
            replaced = True
        else:
            entries.append(clean)
    if not replaced:
        entries.append(eur)
    return entries


def listing_payload(listing_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": listing_id}
    for name, value in fields.items():
        payload[LISTING_FIELDS[name]] = value
    return payload


def iso_window(starts: str, ends: str) -> tuple[str, str]:
    """``PromotionDraft`` dates as Shopware ``validFrom``/``validUntil``: a date-only
    start is that day's midnight, a date-only end is the end of that day."""
    return _iso_bound(starts, end=False), _iso_bound(ends, end=True)


def _iso_bound(raw: str, *, end: bool) -> str:
    text = str(raw).strip()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.isoformat()
    moment = datetime.combine(day, time(23, 59, 59) if end else time(0, 0, 0), tzinfo=UTC)
    return moment.isoformat()


@dataclass(frozen=True)
class PromotionPlan:
    promotion_id: str
    rule_id: str
    payload: dict[str, Any]
    variant_ids: list[str] = field(default_factory=list)


def promotion_payload(
    *,
    name: str,
    discount_pct: float,
    starts: str,
    ends: str,
    sales_channel_id: str,
    variant_ids: list[str],
) -> PromotionPlan:
    """One atomic promotion write: the promotion, its sales-channel binding, one cart
    discount, and — nested under ``discountRules`` — the rule that limits it to carts
    holding one of ``variant_ids``. Shopware writes the nested rule in the same
    transaction, so the dry run validates all of it and a failure leaves nothing behind."""
    valid_from, valid_until = iso_window(starts, ends)
    promotion_id = uuid.uuid4().hex
    rule_id = uuid.uuid4().hex
    payload = {
        "id": promotion_id,
        "name": name,
        "active": True,
        "useCodes": False,
        "useIndividualCodes": False,
        "useSetGroups": False,
        "priority": PROMOTION_PRIORITY,
        "exclusive": False,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "salesChannels": [{"salesChannelId": sales_channel_id, "priority": PROMOTION_PRIORITY}],
        "discounts": [
            {
                "scope": PROMOTION_SCOPE,
                "type": PROMOTION_DISCOUNT_TYPE,
                "value": float(discount_pct),
                "considerAdvancedRules": True,
                "discountRules": [
                    {
                        "id": rule_id,
                        "name": f"Promotion {name} products",
                        "priority": RULE_PRIORITY,
                        "conditions": [
                            {
                                "type": "cartLineItem",
                                "value": {"identifiers": list(variant_ids), "operator": "="},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    return PromotionPlan(
        promotion_id=promotion_id, rule_id=rule_id, payload=payload, variant_ids=list(variant_ids)
    )


def preview_note(result: WriteResult) -> str:
    """The ``guardrail_notes`` line that records the server preview."""
    if not result.server_validated:
        return (
            "preview not server-validated (REST transport) — Shopware checks the payload on apply"
        )
    return f"preview: server dry-run OK — would write {result.describe()}"


# --------------------------------------------------------------------------- writer


@dataclass
class ShopwareWriter:
    admin: AdminTransport
    catalog: CatalogCache

    async def preview(
        self, entity: str, payload: dict[str, Any] | list[dict[str, Any]]
    ) -> WriteResult:
        """The server dry run for one payload. Raises :class:`PreviewRejected` when
        Shopware refuses it and :class:`AdminAPIError` when the call itself failed."""
        result = await self.admin.upsert(entity, payload, dry_run=True)
        if not result.success:
            raise PreviewRejected(result.error or "rejected without a message")
        return result

    async def apply(self, change: StagedChange, payloads: list[WritePayload]) -> list[str]:
        """Replay the stored payloads with ``dry_run=False``. Every result must come back
        as a persisted write (``dry_run is False and success``); anything else raises
        :class:`WriteFailed` after deleting the entities this change created so far."""
        if not payloads:
            raise WriteFailed(f"change {change.change_id} has no write plan")
        notes: list[str] = []
        completed: list[str] = []
        created: list[tuple[str, list[str]]] = []
        for entity, payload in payloads:
            payload = await self._current_stock_base(change, entity, payload, notes)
            try:
                result = await self.admin.upsert(entity, payload, dry_run=False)
            except AdminAPIError as error:
                rolled_back = await self._rollback(created)
                raise WriteFailed(
                    str(error), completed=completed, rolled_back=rolled_back
                ) from error
            if result.dry_run is not False or not result.success:
                rolled_back = await self._rollback(created)
                reason = result.error or "the write came back as a preview, not a persisted write"
                raise WriteFailed(
                    f"{entity}: {reason}", completed=completed, rolled_back=rolled_back
                )
            completed.append(f"{entity} ({result.describe()})")
            if entity in CREATED_ENTITIES:
                created.append((entity, _payload_ids(payload)))
        notes.append("applied: wrote " + "; ".join(completed))
        try:
            await self.catalog.refresh()
        except AdminAPIError as error:  # the write is in; only the cache is stale
            logger.warning("catalog refresh after apply failed: %s", error)
        return notes

    async def _rollback(self, created: list[tuple[str, list[str]]]) -> list[str]:
        rolled_back: list[str] = []
        for entity, ids in reversed(created):
            if not ids:
                continue
            try:
                result = await self.admin.delete(entity, ids, dry_run=False)
            except AdminAPIError as error:
                logger.error("rollback of %s %s failed: %s", entity, ids, error)
                continue
            if result.success:
                rolled_back.append(f"{entity} {', '.join(ids)}")
            else:
                logger.error("rollback of %s %s refused: %s", entity, ids, result.error)
        return rolled_back

    async def _current_stock_base(
        self,
        change: StagedChange,
        entity: str,
        payload: dict[str, Any] | list[dict[str, Any]],
        notes: list[str],
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Restock rows carry the staged *delta*: re-read the stock now and write
        ``current + delta``. Everything else is replayed exactly as staged."""
        if change.kind is not ChangeKind.INVENTORY_ACTION or entity != "product":
            return payload
        deltas: dict[str, tuple[int, int]] = {}
        for item in change.items:
            if item.field == "stock":
                before, after = _int(item.before), _int(item.after)
                deltas[item.target] = (before, after - before)
        rows = payload if isinstance(payload, list) else [payload]
        adjusted: list[dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            target = str(row.get("id"))
            if "stock" in row and target in deltas:
                staged_before, delta = deltas[target]
                fresh = await self.catalog.fresh(target)
                if fresh is None:
                    raise WriteFailed(f"product {target} is no longer in the catalog")
                current = _int(fresh.get("stock"))
                row["stock"] = current + delta
                if current != staged_before:
                    notes.append(
                        f"stock on {target} moved from {staged_before} to {current} since staging; "
                        f"applied {delta:+d} on the current level → {row['stock']}"
                    )
            adjusted.append(row)
        return adjusted if isinstance(payload, list) else adjusted[0]


def _payload_ids(payload: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
    rows = payload if isinstance(payload, list) else [payload]
    return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def by_target(items: list[ChangeItem]) -> dict[str, list[ChangeItem]]:
    grouped: dict[str, list[ChangeItem]] = {}
    for item in items:
        grouped.setdefault(item.target, []).append(item)
    return grouped


def tax_rate_for(record: ProductRecord, fresh: dict[str, Any] | None) -> tuple[float, str | None]:
    """The product's tax rate from the fresh row's ``tax`` association, the cached
    record, or the family; 19 % with a note only when none is readable."""
    if isinstance(fresh, dict):
        tax = fresh.get("tax")
        if isinstance(tax, dict) and tax.get("taxRate") is not None:
            return float(tax["taxRate"]), None
    if record.effective_tax_rate is not None:
        return record.effective_tax_rate, None
    return FALLBACK_TAX_RATE, (
        f"tax rate for {record.product_number or record.listing_id} unreadable — "
        f"net computed with {FALLBACK_TAX_RATE:.0f} %"
    )


def current_price_entries(
    record: ProductRecord, fresh: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    if isinstance(fresh, dict) and isinstance(fresh.get("price"), list):
        return fresh["price"]
    return record.price_entries


def current_gross(record: ProductRecord, fresh: dict[str, Any] | None) -> float | None:
    entry = eur_entry(fresh.get("price")) if isinstance(fresh, dict) else None
    if entry is not None:
        try:
            return float(entry.get("gross"))
        except (TypeError, ValueError):
            return None
    return record.own_price
