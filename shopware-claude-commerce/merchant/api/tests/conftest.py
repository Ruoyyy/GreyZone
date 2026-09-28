# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from merchant.api.agent_config import ShopwareSettings, build_merchant_config
from merchant.api.fake_admin import SALES_CHANNEL_ID, FakeAdmin
from merchant.api.ledger import SqliteChangeLedger
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import MerchantAgentConfig, MerchantSessionContext

#: A fixed "today" so the seeded orders and the period arithmetic agree in every run.
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def admin(now: datetime) -> FakeAdmin:
    return FakeAdmin(now=now)


@pytest.fixture
def settings() -> ShopwareSettings:
    return ShopwareSettings(
        shop_url="http://shopware.test",
        operator="Dana",
        store_name="Demo Shop",
        low_stock_default=8,
        local_store=True,
        sales_channel_id=SALES_CHANNEL_ID,
        ledger_dsn=":memory:",
    )


@pytest.fixture
def config() -> MerchantAgentConfig:
    return build_merchant_config("Demo Shop")


@pytest.fixture
def backend(
    admin: FakeAdmin, settings: ShopwareSettings, config: MerchantAgentConfig, now: datetime
) -> ShopwareMerchantBackend:
    return ShopwareMerchantBackend(
        admin,
        settings,
        config,
        ledger=SqliteChangeLedger(config, ":memory:"),
        clock=lambda: now,
    )


@pytest.fixture
def session() -> MerchantSessionContext:
    return MerchantSessionContext(
        session_id="ms-1",
        merchant_id="http://shopware.test",
        operator="Dana",
    )


@pytest.fixture
async def warmed(backend: ShopwareMerchantBackend) -> ShopwareMerchantBackend:
    await backend.warm()
    return backend
