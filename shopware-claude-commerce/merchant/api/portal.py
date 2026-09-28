# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The merchant web UI's own reads, next to the blueprint router:

    GET /api/merchant/dashboard?period=last_7d   KPIs with sparklines and a digest line
    GET /api/merchant/orders?limit=20            recent Shopware orders with issue tags
    GET /api/merchant/changes?status=staged      the ledger (staged|applied|discarded|all)
    GET /api/merchant/mail                       the assistant's simulated outbox

They are scoped exactly like the blueprint's portal reads: the caller must present the
``X-Session-Id`` of a session started on ``POST /api/merchant/session``. The blueprint
router keeps its session store private, so this module borrows the very dependency its
own routes declare (``_session_dependency_of``) instead of building a second store.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.routing import APIRoute

from demo_common.sessions import SessionRecord
from merchant_agent import ChangeStatus, MerchantSessionContext
from shopware_common.clock import host_clock

from .invoice_tool import (
    MAX_PDF_BYTES,
    invoice_tool_enabled,
    latest_invoice,
    pdf_text,
    save_invoice,
)
from .mail_tool import mail_tool_enabled, sent_mail
from .shopware_backend import ShopwareMerchantBackend

CHANGE_STATUSES = ("staged", "applied", "discarded", "all")
ORDERS_DEFAULT_LIMIT = 20
ORDERS_MAX_LIMIT = 100
_PROBE_PATH = "/alerts"


def _session_dependency_of(router: APIRouter) -> Callable[..., Any]:
    """The ``current_session`` callable the blueprint router's scoped routes depend on."""
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == _PROBE_PATH:
            for dependant in route.dependant.dependencies:
                if dependant.call is not None:
                    return dependant.call
    raise RuntimeError("the merchant router exposes no session-scoped route to borrow")


def build_portal_router(
    backend: ShopwareMerchantBackend, merchant_router: APIRouter, *, operator: str
) -> APIRouter:
    current_session = _session_dependency_of(merchant_router)
    # A default-value ``Depends`` rather than an ``Annotated`` alias: the alias is local to
    # this function and postponed annotations could not resolve it.
    scoped = Depends(current_session, scope="function")
    router = APIRouter()

    def context(record: SessionRecord, request: Request | None = None) -> MerchantSessionContext:
        """The clock is the operator's: the browser's zone from ``X-Timezone`` (or ``tz``),
        else ``HOST_TIMEZONE``, always as an aware ``now`` plus the zone name."""
        return MerchantSessionContext(
            session_id=record.session_id,
            merchant_id=record.user_id,
            operator=operator,
            **host_clock(request),
        )

    @router.get("/dashboard")
    async def dashboard(
        request: Request, period: str = Query(default="last_7d"), record: SessionRecord = scoped
    ) -> dict[str, Any]:
        return await backend.dashboard(context(record, request), period)

    @router.get("/orders")
    async def orders(
        limit: int = Query(default=ORDERS_DEFAULT_LIMIT, ge=1, le=ORDERS_MAX_LIMIT),
        record: SessionRecord = scoped,
    ) -> dict[str, Any]:
        del record
        return {"orders": await backend.portal_orders(limit)}

    @router.get("/changes")
    async def changes(
        status: str = Query(default="staged"), record: SessionRecord = scoped
    ) -> dict[str, Any]:
        wanted = status.strip().lower()
        if wanted not in CHANGE_STATUSES:
            wanted = "staged"
        listed = backend.changes(wanted)
        # The portal's Approve / Dismiss buttons go through the blueprint's provenance gate,
        # which accepts only change ids this session has seen. Listing a staged change to
        # the operator is that sighting (exactly what the ``get_pending_changes`` tool does),
        # so a change staged in an earlier session or before a restart stays actionable.
        for change in listed:
            if change.status == ChangeStatus.STAGED:
                record.state.remember_change(change)
        return {
            "status": wanted,
            "changes": [change.model_dump(mode="json", exclude_none=True) for change in listed],
        }

    @router.get("/invoice")
    async def invoice(record: SessionRecord = scoped) -> dict[str, Any]:
        # The supplier invoice text the assistant can read. Surfacing it to the operator
        # mirrors what the model sees, verbatim.
        if not invoice_tool_enabled():
            return {"enabled": False}
        try:
            text = latest_invoice()
            return {"enabled": True, "text": text} if text else {"enabled": True}
        except OSError as error:
            return {"enabled": True, "error": str(error)}

    @router.post("/invoice")
    async def upload_invoice(request: Request, record: SessionRecord = scoped) -> dict[str, Any]:
        # The raw PDF is the request body (Content-Type: application/pdf); the size cap is
        # checked while reading, so an oversized upload is never held in memory whole.
        if not invoice_tool_enabled():
            raise HTTPException(404, "supplier invoice ingestion is turned off")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(413, f"the PDF exceeds {MAX_PDF_BYTES // 2**20} MB")
        try:
            text = await asyncio.to_thread(pdf_text, bytes(data))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        save_invoice(text)
        return {"enabled": True, "text": text}

    @router.get("/mail")
    async def mail(record: SessionRecord = scoped) -> dict[str, Any]:
        # The assistant's simulated outbox for this session, newest first.
        if not mail_tool_enabled():
            return {"enabled": False, "messages": []}
        return {"enabled": True, "messages": sent_mail(record.session_id)}

    return router
