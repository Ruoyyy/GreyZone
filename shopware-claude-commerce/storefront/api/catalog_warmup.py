# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Fill the product grid display cache at startup. Uses UCP search, then Store API."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from shopping_agent import ShoppingSessionContext

if TYPE_CHECKING:
    from .shopware_backend import ShopwareStorefrontBackend

logger = logging.getLogger(__name__)

_WARMUP_QUERIES = ("", "*", "a")
_NOTES_MARK = " This shop's live catalog"


def warmup_enabled() -> bool:
    return os.environ.get("CATALOG_WARMUP", "1") != "0"


def live_catalog_notes(backend: ShopwareStorefrontBackend) -> str:
    """Facts from the warmed cache so the model names real categories, not a generic mall."""
    titles: list[str] = []
    categories: list[str] = []
    seen_titles: set[str] = set()
    seen_categories: set[str] = set()
    for details in backend.products.values():
        title = (details.title or "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            titles.append(title)
        category = (details.category or "").strip()
        if category and category not in seen_categories:
            seen_categories.add(category)
            categories.append(category)
    if not titles and not categories:
        return ""
    parts = [" This shop's live catalog (only name what search_products returns; do not invent aisles):"]
    if categories:
        parts.append(f" categories {', '.join(categories)};")
    if titles:
        parts.append(f" products include {', '.join(titles[:12])}.")
    return "".join(parts)


def apply_live_catalog_notes(backend: ShopwareStorefrontBackend, config: Any) -> None:
    extra = live_catalog_notes(backend)
    if not extra:
        return
    current = str(getattr(config, "domain_search_notes", "") or "")
    if _NOTES_MARK in current:
        current = current.split(_NOTES_MARK, 1)[0].rstrip()
    try:
        config.domain_search_notes = (current + extra).strip()
    except Exception as error:
        logger.info("catalog notes not applied (%s)", error)


async def warm_catalog(backend: ShopwareStorefrontBackend, config: Any | None = None) -> int:
    if not warmup_enabled():
        return 0
    session = ShoppingSessionContext(session_id="warmup", user_id="warmup")
    try:
        products: list[Any] = []
        for query in _WARMUP_QUERIES:
            products = await backend.search_products(session, query, limit=24)
            if products:
                break
        for product in products:
            details = backend.products.get(product.product_id)
            if details:
                backend.warm_display_cache(details)
        # Warm-up must not leave provenance on a real session.
        backend.reset_session(session.session_id)
        if config is not None:
            apply_live_catalog_notes(backend, config)
        logger.info("catalog warm-up cached %d products", len(products))
        return len(products)
    except Exception as failure:
        logger.warning("catalog warm-up skipped (%s); the grid fills from sessions", failure)
        return 0
