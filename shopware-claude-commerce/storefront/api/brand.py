# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shop branding for the web surface, from Store API context (sales-channel name)."""

from __future__ import annotations

import os
import time
from typing import Any

from .store_api import StoreApiClient, StoreApiError

CACHE_TTL = 600.0
DEFAULT_TAGLINE = "Shop with an assistant that knows the store."
SHOPWARE_COLORS = {"background": "#189eff", "foreground": "#ffffff"}


class BrandSource:
    def __init__(self, store_api: StoreApiClient, fallback_name: str = "Shopware") -> None:
        self._store_api = store_api
        self._fallback_name = fallback_name
        self._cached: dict[str, Any] | None = None
        self._fetched_at = 0.0

    async def brand(self, _buyer_ip: str | None = None) -> dict[str, Any]:
        if self._cached is not None and time.monotonic() - self._fetched_at < CACHE_TTL:
            return self._cached
        try:
            context = await self._store_api.context()
        except StoreApiError:
            context = {}
        channel = context.get("salesChannel") or {}
        name = (
            channel.get("translated", {}).get("name") or channel.get("name") or self._fallback_name
        )
        payload = {
            "name": name,
            "slogan": None,
            "tagline": os.environ.get("BRAND_TAGLINE") or DEFAULT_TAGLINE,
            "short_description": None,
            "logo_url": None,
            "cover_image_url": None,
            "colors": SHOPWARE_COLORS,
        }
        if context:
            self._cached = payload
            self._fetched_at = time.monotonic()
        return payload
