# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The shop's own merchant tools: ``SwagCommerceAgentTools`` on the Admin MCP server.

The plugin (``shopware-plugins/SwagCommerceAgentTools``) carries the staged-change ledger
(``swag_agent_staged_change``) and the analytics as Admin MCP tools on ``POST /api/_mcp``:

    agent-change-stage      {kind, items, summary, note, guardrailNotes, salesChannelId,
                             currency, margins, dryRun}          → stage_*
    agent-change-list       {status, limit, page}                → get_pending_changes / portal
    agent-change-apply      {changeId, dryRun}                   → apply_change
    agent-change-discard    {changeId, dryRun}                   → discard_change
    agent-business-snapshot {period, salesChannelId}             → get_business_snapshot
    agent-metrics-series    {metric, period, granularity, segment, salesChannelId} → query_metrics

``SHOPWARE_AGENT_TOOLS`` picks the path: ``plugin`` calls the tools (the ledger lives in
Shopware), ``host`` keeps the SQLite ledger and the ``shopware-entity-*`` reads. Unset
(``auto``) means *plugin when the integration's ``tools/list`` advertises all six tools*,
decided once at startup (:meth:`MerchantAgentTools.detect`), else ``host``. The plugin path
needs the MCP transport; on ``SHOPWARE_ADMIN_TRANSPORT=rest`` it is ``host`` whatever the
flag says. Promotions stay on the host path in every mode — the plugin refuses the
``promotion`` kind (its README, "Deferred").

Row mapping (:func:`staged_change_from_row`): the plugin's preview rows (``price.gross`` /
``price.net`` / ``stock`` / ``active`` / listing fields) become the blueprint's
``ChangeItem``s in the host's vocabulary (``price``, ``stock``, ``status``, ``title`` …),
so the blueprint gates — guardrails at stage and apply time, provenance, host approval — run
unchanged on both paths.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from merchant_agent import ActorKind, ChangeItem, ChangeKind, ChangeStatus, StagedChange

from .admin_client import AdminAPIError, AdminTransport

logger = logging.getLogger(__name__)

AGENT_TOOLS_ENV = "SHOPWARE_AGENT_TOOLS"
MODE_AUTO = "auto"
MODE_PLUGIN = "plugin"
MODE_HOST = "host"
MODES = (MODE_AUTO, MODE_PLUGIN, MODE_HOST)

TOOL_CHANGE_STAGE = "agent-change-stage"
TOOL_CHANGE_LIST = "agent-change-list"
TOOL_CHANGE_APPLY = "agent-change-apply"
TOOL_CHANGE_DISCARD = "agent-change-discard"
TOOL_BUSINESS_SNAPSHOT = "agent-business-snapshot"
TOOL_METRICS_SERIES = "agent-metrics-series"
MERCHANT_TOOLS: tuple[str, ...] = (
    TOOL_CHANGE_STAGE,
    TOOL_CHANGE_LIST,
    TOOL_CHANGE_APPLY,
    TOOL_CHANGE_DISCARD,
    TOOL_BUSINESS_SNAPSHOT,
    TOOL_METRICS_SERIES,
)
#: The tools an ACL split can withhold on purpose: the template's maker-checker roles give
#: ``agent_change:update`` (apply/discard) to the approver only, and a read-only role has no
#: ``agent-change-stage`` either. Missing *reads* are always a misconfiguration.
WRITE_TOOLS: frozenset[str] = frozenset({TOOL_CHANGE_STAGE, TOOL_CHANGE_APPLY, TOOL_CHANGE_DISCARD})
#: Change kinds the plugin stages; the others keep the host ledger.
PLUGIN_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
    {ChangeKind.LISTING_UPDATE, ChangeKind.PRICE_UPDATE, ChangeKind.INVENTORY_ACTION}
)
STATUS_ALL = "all"
LIST_PAGE_SIZE = 100  # the tool's maximum
LIST_MAX_PAGES = 10
SUMMARY_MAX_LENGTH = 200  # StagedChange.summary

#: Plugin preview fields → the host's ChangeItem vocabulary. ``price.net`` rows are the
#: derived half of a price move and are dropped; ``active`` becomes ``status``.
PREVIEW_FIELDS: dict[str, str] = {
    "price.gross": "price",
    "stock": "stock",
    "active": "status",
    "name": "title",
    "description": "description",
    "metaTitle": "seo_title",
    "metaDescription": "seo_description",
    "keywords": "keywords",
}
PREVIEW_FIELDS_DROPPED: frozenset[str] = frozenset({"price.net"})


class AgentToolsError(RuntimeError):
    """A plugin tool answered ``success: false`` (business error) or could not be called."""


class ToolCaller(Protocol):
    """What the plugin path needs from the Admin transport (``McpTransport`` has it)."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def tool_names(self) -> list[str]: ...


def mode_from_env() -> str:
    value = (os.environ.get(AGENT_TOOLS_ENV) or MODE_AUTO).strip().lower()
    if value not in MODES:
        logger.warning("%s=%r is not one of %s; using %s", AGENT_TOOLS_ENV, value, MODES, MODE_AUTO)
        return MODE_AUTO
    return value


def resolve_mode(requested: str, advertised: set[str]) -> str:
    """``host`` is final; ``plugin`` is honoured even when tools are missing (the operator
    asked for it; every call then fails with a clear message); ``auto`` becomes ``plugin``
    only when all six tools are advertised."""
    if requested == MODE_HOST:
        return MODE_HOST
    if requested == MODE_PLUGIN:
        return MODE_PLUGIN
    return MODE_PLUGIN if set(MERCHANT_TOOLS) <= advertised else MODE_HOST


def supports_tools(admin: AdminTransport) -> bool:
    return callable(getattr(admin, "call_tool", None)) and callable(getattr(admin, "tool_names", None))


class MerchantAgentTools:
    def __init__(self, admin: AdminTransport, *, mode: str | None = None) -> None:
        self._admin = admin
        self.requested_mode = mode or mode_from_env()
        self.mode: str | None = None
        self.advertised: set[str] = set()
        self.calls: list[str] = []

    # ------------------------------------------------------------------ mode

    @property
    def active(self) -> bool:
        return self.mode == MODE_PLUGIN

    @property
    def description(self) -> str:
        if self.mode is None:
            return f"{self.requested_mode} (not detected yet)"
        if self.mode == self.requested_mode:
            return self.mode
        return f"{self.mode} (requested {self.requested_mode})"

    async def detect(self) -> str:
        if self.requested_mode == MODE_HOST:
            self.mode = MODE_HOST
            return self.mode
        if not supports_tools(self._admin):
            logger.warning(
                "agent tools: the %s transport cannot call MCP tools; host path", self._admin.name
            )
            self.mode = MODE_HOST
            return self.mode
        try:
            self.advertised = set(await self._caller().tool_names())
        except AdminAPIError as error:
            logger.warning("agent tools: tools/list failed (%s); host path", error)
            self.advertised = set()
        self.mode = resolve_mode(self.requested_mode, self.advertised)
        missing = set(MERCHANT_TOOLS) - self.advertised
        if self.mode == MODE_PLUGIN and missing:
            if reads := sorted(missing - WRITE_TOOLS):
                logger.error(
                    "%s=plugin but the integration does not see %s (allowlist?)",
                    AGENT_TOOLS_ENV,
                    ", ".join(reads),
                )
            if writes := sorted(missing & WRITE_TOOLS):
                logger.info(
                    "agent tools: the integration does not see %s — expected when the roles "
                    "are split (maker-checker); those calls fail with a clear message",
                    ", ".join(writes),
                )
        logger.info(
            "agent tools: %s (/api/_mcp advertises %d agent-* tool(s))",
            self.description,
            len(set(MERCHANT_TOOLS) & self.advertised),
        )
        return self.mode

    def _caller(self) -> ToolCaller:
        return self._admin  # type: ignore[return-value]

    # ------------------------------------------------------------------ tools

    async def stage(
        self,
        *,
        kind: ChangeKind,
        items: list[dict[str, Any]],
        summary: str,
        note: str | None = None,
        guardrail_notes: list[str] | None = None,
        sales_channel_id: str | None = None,
        currency: str | None = None,
        margins: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        """``agent-change-stage dryRun=false``: the ledger row Shopware recorded."""
        arguments: dict[str, Any] = {
            "kind": kind.value,
            "items": json_dumps(items),
            "summary": summary,
            "dryRun": False,
        }
        if note:
            arguments["note"] = note
        if guardrail_notes:
            arguments["guardrailNotes"] = json_dumps(list(guardrail_notes))
        if sales_channel_id:
            arguments["salesChannelId"] = sales_channel_id
        if currency:
            arguments["currency"] = currency
        cleaned = {k: v for k, v in (margins or {}).items() if v is not None}
        if cleaned:
            arguments["margins"] = json_dumps(cleaned)
        return self._data(await self._call(TOOL_CHANGE_STAGE, arguments), TOOL_CHANGE_STAGE)

    async def list(
        self, status: str = ChangeStatus.STAGED.value, *, limit: int = LIST_PAGE_SIZE, page: int = 1
    ) -> list[dict[str, Any]]:
        payload = await self._call(
            TOOL_CHANGE_LIST, {"status": status, "limit": int(limit), "page": int(page)}
        )
        rows = payload.get("data")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def list_all(self, status: str = ChangeStatus.STAGED.value) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, LIST_MAX_PAGES + 1):
            batch = await self.list(status, limit=LIST_PAGE_SIZE, page=page)
            rows.extend(batch)
            if len(batch) < LIST_PAGE_SIZE:
                break
        return rows

    async def find(self, change_id: str) -> dict[str, Any] | None:
        """The row for ``change_id`` in any status, or ``None``."""
        for row in await self.list_all(STATUS_ALL):
            if str(row.get("changeId")) == change_id:
                return row
        return None

    async def apply(self, change_id: str) -> dict[str, Any]:
        payload = await self._call(TOOL_CHANGE_APPLY, {"changeId": change_id, "dryRun": False})
        return self._data(payload, TOOL_CHANGE_APPLY)

    async def discard(self, change_id: str) -> dict[str, Any]:
        payload = await self._call(TOOL_CHANGE_DISCARD, {"changeId": change_id, "dryRun": False})
        return self._data(payload, TOOL_CHANGE_DISCARD)

    async def business_snapshot(
        self, period: str, *, sales_channel_id: str | None = None
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"period": period}
        if sales_channel_id:
            arguments["salesChannelId"] = sales_channel_id
        return self._data(
            await self._call(TOOL_BUSINESS_SNAPSHOT, arguments), TOOL_BUSINESS_SNAPSHOT
        )

    async def metrics_series(
        self,
        metric: str,
        period: str,
        granularity: str,
        *,
        segment: str | None = None,
        sales_channel_id: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "metric": metric,
            "period": period,
            "granularity": granularity,
        }
        if segment:
            arguments["segment"] = segment
        if sales_channel_id:
            arguments["salesChannelId"] = sales_channel_id
        return self._data(await self._call(TOOL_METRICS_SERIES, arguments), TOOL_METRICS_SERIES)

    # ------------------------------------------------------------------ plumbing

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.active:
            raise AgentToolsError(f"{tool}: the plugin path is not active ({self.description})")
        self.calls.append(tool)
        try:
            payload = await self._caller().call_tool(tool, arguments)
        except AdminAPIError as error:
            raise AgentToolsError(f"{tool}: {error}") from error
        if not payload.get("success"):
            raise AgentToolsError(f"{tool}: {payload.get('error') or 'tool reported failure'}")
        return payload

    @staticmethod
    def _data(payload: dict[str, Any], tool: str) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AgentToolsError(f"{tool}: no data in the tool response")
        return data


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- row mapping


def plugin_period(start: datetime, end_exclusive: datetime) -> str:
    """The host's half-open ``[start, end)`` window as the plugin's inclusive explicit range
    ``YYYY-MM-DD..YYYY-MM-DD``. Explicit dates keep the two clocks (the host's session clock
    and the shop's) out of the picture."""
    last = (end_exclusive - timedelta(days=1)).date()
    return f"{start.date().isoformat()}..{last.isoformat()}"


def items_from_preview(rows: list[Any]) -> list[ChangeItem]:
    items: list[ChangeItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "")
        if field in PREVIEW_FIELDS_DROPPED:
            continue
        mapped = PREVIEW_FIELDS.get(field, field)
        before, after = row.get("before"), row.get("after")
        if mapped == "status":
            before, after = _status_word(before), _status_word(after)
        items.append(ChangeItem(target=str(row.get("target") or ""), field=mapped, before=before, after=after))
    return items


def _status_word(active: Any) -> str | None:
    if active is None:
        return None
    return "active" if bool(active) else "paused"


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _notes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [note if isinstance(note, str) else json_dumps(note) for note in value]


def staged_change_from_row(row: dict[str, Any], *, extra_notes: list[str] | None = None) -> StagedChange:
    """A ``swag_agent_staged_change`` row (``toToolArray`` shape) as the blueprint's
    ``StagedChange``. ``created_by`` is the principal Shopware recorded (the integration
    or user id); the assistant staged it, so ``created_by_kind`` is ``agent``."""
    kind = ChangeKind(str(row.get("kind")))
    status = ChangeStatus(str(row.get("status")))
    created_at = _moment(row.get("createdAt")) or datetime.now(UTC)
    discarded_by = row.get("discardedBy")
    return StagedChange(
        change_id=str(row.get("changeId")),
        kind=kind,
        status=status,
        summary=str(row.get("summary") or "")[:SUMMARY_MAX_LENGTH],
        items=items_from_preview(row.get("items") or []),
        created_at=created_at,
        created_by=str(row.get("createdBy") or row.get("createdByKind") or "integration"),
        created_by_kind=ActorKind.AGENT,
        applied_at=_moment(row.get("appliedAt")),
        applied_by=str(row["appliedBy"]) if row.get("appliedBy") else None,
        discarded_at=_moment(row.get("discardedAt")),
        discarded_by=str(discarded_by) if discarded_by else None,
        discarded_by_kind=ActorKind.OPERATOR if discarded_by else None,
        guardrail_notes=[*_notes(row.get("guardrailNotes")), *(extra_notes or [])],
        currency=str(row["currency"]) if row.get("currency") else None,
        margin_before_pct=_float(row.get("marginBeforePct")),
        margin_after_pct=_float(row.get("marginAfterPct")),
    )


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def preview_note(row: dict[str, Any]) -> str:
    """The ``guardrail_notes`` line for a change staged through the plugin: Shopware ran the
    write in a rolled-back transaction and recorded the ledger row."""
    rows = [r for r in (row.get("items") or []) if isinstance(r, dict)]
    targets = {str(r.get("target")) for r in rows}
    return (
        f"preview: agent-change-stage dry-run OK — {len(rows)} field(s) on {len(targets)} product(s) "
        f"validated by Shopware; ledger row {row.get('changeId')} in swag_agent_staged_change"
    )
