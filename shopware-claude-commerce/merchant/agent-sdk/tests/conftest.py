# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shared fixtures: a toolset over the live backend class on ``FakeAdmin`` (the in-process
stand-in with the Admin MCP tools' semantics, ``merchant/api/fake_admin.py``), warmed the
way the host warms it, and its registered SDK tools. No test here reaches the network or
needs credentials."""

from __future__ import annotations

import os

os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)

from datetime import UTC, datetime
from typing import Any

import pytest
from claude_agent_sdk import SdkMcpTool
from shopware_merchant_sdk import MerchantToolset, build_merchant_sdk_tools, default_config

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
    """The console's config; the host-approval mode has its own tests."""
    return default_config(settings).model_copy(update={"require_host_approval": False})


@pytest.fixture
async def backend(
    admin: FakeAdmin, settings: ShopwareSettings, config: MerchantAgentConfig
) -> ShopwareMerchantBackend:
    backend = ShopwareMerchantBackend(
        admin,
        settings,
        config,
        ledger=SqliteChangeLedger(config, ":memory:"),
        clock=lambda: NOW,
    )
    await backend.warm()
    return backend


@pytest.fixture
def toolset(backend: ShopwareMerchantBackend, config: MerchantAgentConfig) -> MerchantToolset:
    """A toolset bound to the warmed fake shop and an empty operator session."""
    return MerchantToolset(backend=backend, config=config)


@pytest.fixture
def handlers(toolset: MerchantToolset) -> dict[str, SdkMcpTool[Any]]:
    """The registered SDK tools for ``toolset``, indexed by tool name."""
    return {t.name: t for t in build_merchant_sdk_tools(toolset)}
