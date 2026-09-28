# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""What a host of this runtime does around the turns when the backend is the live shop:
index the shop's policy pages before the first turn, render the ``checkout`` card with a
one-time continue link, and release the shop connections at the end. The console in
``main.py`` is one such host; a web host would serve the ticket route instead."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from shopping_agent import StorefrontBackend
from shopware_common.handoff import DEFAULT_TTL_SECONDS
from storefront.api.shopware_backend import ShopwareStorefrontBackend

from .shopping_tools import ShoppingToolset

logger = logging.getLogger(__name__)

CHECKOUT_COMPONENT = "checkout"
HANDOFF_TTL_SECONDS = DEFAULT_TTL_SECONDS
DEFAULT_HANDOFF_LABEL = "Checkout"


def _shopware(backend: StorefrontBackend) -> ShopwareStorefrontBackend | None:
    return backend if isinstance(backend, ShopwareStorefrontBackend) else None


def continue_link(toolset: ShoppingToolset) -> str | None:
    """The shop's continue URL carrying a fresh one-time handoff code for this session's
    cart, or ``None`` when the handoff is not configured, the backend is not the live
    shop, or there is no cart. This is the console's version of
    ``GET /api/checkout/handoff/{ticket}`` on the storefront host: minted after the turn,
    shown to the operator, never part of a tool result the model reads."""
    backend = _shopware(toolset.backend)
    if backend is None or not backend.handoff.configured:
        return None
    code = backend.handoff_code_for(toolset.session.session_id)
    if code is None:
        return None
    return f"{backend.handoff.continue_endpoint}?code={quote(code.code, safe='')}"


def render_checkout(payload: dict[str, Any], toolset: ShoppingToolset) -> dict[str, Any]:
    """The checkout payload with the host's ticket URL replaced by the shop's one-time
    continue link; the ticket URL is known only to the storefront host process that
    minted it, so a console cannot serve it. Unchanged when no link can be minted."""
    link = continue_link(toolset)
    if link is None:
        return payload
    handoffs = payload.get("handoffs") or [{"label": DEFAULT_HANDOFF_LABEL}]
    return {**payload, "handoffs": [{**handoff, "url": link} for handoff in handoffs]}


async def prepare_backend(toolset: ShoppingToolset) -> None:
    """What the storefront host does at startup: index the shop's CMS policy pages so a
    terms question is answered from the shop rather than the fallback copy."""
    backend = _shopware(toolset.backend)
    if backend is None:
        return
    try:
        await backend.policies.rebuild()
    except Exception:  # the fallback copy answers instead; the turn must still run
        logger.warning("policy index rebuild failed; fallback copy is used", exc_info=True)


async def release_backend(toolset: ShoppingToolset) -> None:
    """Close the UCP (MCP session) and Store API connections of the live backend."""
    backend = _shopware(toolset.backend)
    if backend is None:
        return
    await backend.client.aclose()
    await backend.store_api.aclose()
