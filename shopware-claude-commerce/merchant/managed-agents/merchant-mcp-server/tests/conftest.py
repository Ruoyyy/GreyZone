# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The server over the live backend class on ``FakeAdmin`` (the in-process stand-in with
the Admin MCP tools' semantics, ``merchant/api/fake_admin.py``); no network, no
credentials."""

from __future__ import annotations

import os

os.environ.pop("MERCHANT_MCP_BEHIND_GATEWAY", None)

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from merchant_mcp_server import build_server, default_config

from commerce_common.memory import InMemoryMemoryStore
from merchant.api.agent_config import ShopwareSettings
from merchant.api.fake_admin import SALES_CHANNEL_ID, FakeAdmin
from merchant.api.ledger import SqliteChangeLedger
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import MerchantAgentConfig

#: A fixed "today" so the seeded orders and the period arithmetic agree in every run.
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def admin() -> FakeAdmin:
    return FakeAdmin(now=NOW)


@pytest.fixture
def settings() -> ShopwareSettings:
    return ShopwareSettings(
        shop_url="http://shopware.test",
        operator="Dana",
        store_name="Demo Shop",
        local_store=True,
        sales_channel_id=SALES_CHANNEL_ID,
        ledger_dsn=":memory:",
    )


@pytest.fixture
def config(settings: ShopwareSettings) -> MerchantAgentConfig:
    return default_config(settings)


BackendFactory = Callable[[MerchantAgentConfig], ShopwareMerchantBackend]


@pytest.fixture
def make_backend(admin: FakeAdmin, settings: ShopwareSettings) -> BackendFactory:
    """A backend over the fake shop under the given config, unwarmed on purpose: the
    server warms it on the first tool call, as it does live."""

    def build(config: MerchantAgentConfig) -> ShopwareMerchantBackend:
        return ShopwareMerchantBackend(
            admin,
            settings,
            config,
            ledger=SqliteChangeLedger(config, ":memory:"),
            clock=lambda: NOW,
        )

    return build


@pytest.fixture
def backend(make_backend: BackendFactory, config: MerchantAgentConfig) -> ShopwareMerchantBackend:
    return make_backend(config)


@pytest.fixture
def server(backend: ShopwareMerchantBackend, settings: ShopwareSettings, config):
    return build_server(
        backend=backend, memory_store=InMemoryMemoryStore(), config=config, settings=settings
    )
