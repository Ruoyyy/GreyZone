# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""What a host of this runtime does around the turns when the backend is the live shop:
load the catalog before the first turn, as the merchant host's lifespan does, and close
the Admin transport and the ledger at the end."""

from __future__ import annotations

from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import MerchantBackend

from .merchant_tools import MerchantToolset


def _shopware(backend: MerchantBackend) -> ShopwareMerchantBackend | None:
    return backend if isinstance(backend, ShopwareMerchantBackend) else None


async def prepare_backend(toolset: MerchantToolset) -> None:
    """Catalog, sales channel and the recent-order feed. The catalog must load; an
    unreachable shop or refused integration raises here, before any turn is spent."""
    backend = _shopware(toolset.backend)
    if backend is not None:
        await backend.warm()


async def release_backend(toolset: MerchantToolset) -> None:
    backend = _shopware(toolset.backend)
    if backend is None:
        return
    await backend.admin.aclose()
    backend.ledger.close()
