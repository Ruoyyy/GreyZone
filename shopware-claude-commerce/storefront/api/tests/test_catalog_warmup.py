# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Catalog warm-up fills the display cache and names live products in the prompt notes."""

from __future__ import annotations

from storefront.api.agent_config import build_shopping_config
from storefront.api.catalog_warmup import apply_live_catalog_notes, live_catalog_notes, warm_catalog


async def test_warmup_lists_the_catalog_when_ucp_search_returns_html(backend, shop, monkeypatch):
    monkeypatch.setenv("CATALOG_WARMUP", "1")
    shop.ucp_search_html = True
    shop.store_search_html = True
    config = build_shopping_config("Shopware")
    cached = await warm_catalog(backend, config)
    assert cached >= 2
    notes = live_catalog_notes(backend)
    assert "Claude Commerce T-Shirt" in notes
    assert "Extra Virgin Olive Oil" in notes
    assert "Main product" in notes
    apply_live_catalog_notes(backend, config)
    assert "live catalog" in config.domain_search_notes
    assert "Claude Commerce T-Shirt" in config.domain_search_notes
