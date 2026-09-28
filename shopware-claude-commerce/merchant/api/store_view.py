# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Read-only storefront view so ``build_merchant_router`` can mount unchanged. The
overview's ``recent_orders`` feed comes from the backend's cached Shopware orders."""

from __future__ import annotations

from typing import Never

from shopping_agent import Cart, Order, ProductDetails

from .shopware_backend import ShopwareMerchantBackend


def _unsupported(operation: str) -> Never:
    raise NotImplementedError(
        f"{operation} is a buyer-side operation; this host is the merchant portal"
    )


class ShopwareStoreView:
    def __init__(self, backend: ShopwareMerchantBackend) -> None:
        self._backend = backend

    @property
    def store_name(self) -> str:
        return self._backend.store_name

    @property
    def products(self) -> dict[str, ProductDetails]:
        currency = self._backend.display_currency
        return {
            record.listing_id: ProductDetails(
                product_id=record.listing_id,
                title=record.title,
                price=record.to_listing().price,
                currency=currency,
                in_stock=record.to_listing().stock > 0,
                short_description=record.description,
            )
            for record in self._backend.catalog.cached()
        }

    def product(self, product_id: str) -> ProductDetails | None:
        record = self._backend.catalog.get_cached(product_id)
        if record is None:
            return None
        return ProductDetails(
            product_id=record.listing_id,
            title=record.title,
            price=record.price,
            currency=self._backend.display_currency,
            in_stock=record.stock > 0,
            short_description=record.description,
        )

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return self._backend.recent_orders(limit)

    def reset_session(self, session_id: str) -> None:
        del session_id

    async def get_cart(self, *args: object, **kwargs: object) -> Cart:
        del args, kwargs
        _unsupported("get_cart")

    async def add_to_cart(self, *args: object, **kwargs: object) -> Cart:
        del args, kwargs
        _unsupported("add_to_cart")
