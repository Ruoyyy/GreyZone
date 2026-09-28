# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shared fixtures: a toolset over the Shopware replay (``storefront/api/tests/replay.py``,
UCP over MCP with signature verification, Store API) and its registered SDK tools. No
test here reaches the network or needs credentials."""

from __future__ import annotations

import os

os.environ.setdefault("CATALOG_WARMUP", "0")
os.environ.setdefault("SHOPWARE_URL", "http://shopware.test")
os.environ.setdefault("SHOPWARE_SALES_CHANNEL_ACCESS_KEY", "test-key")
os.environ.setdefault("UCP_AGENT_PROFILE_URL", "http://shopware.test/.well-known/ucp")
os.environ.setdefault("STOREFRONT_API_PUBLIC_URL", "http://host.test")
os.environ.pop("UCP_AGENT_SIGNING_KEY_PEM_FILE", None)
os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)

from typing import Any

import httpx
import pytest
from claude_agent_sdk import SdkMcpTool
from shopware_shopping_sdk import ShoppingToolset, build_shopping_sdk_tools, default_config

from shopware_common.http_signing import RequestSigner
from storefront.api.handoff import HandoffBroker
from storefront.api.shopware_backend import ShopwareStorefrontBackend
from storefront.api.store_api import StoreApiClient
from storefront.api.tests.replay import ShopwareReplay
from storefront.api.ucp_client import UcpClient

SHOP_URL = "http://shopware.test"
HANDOFF_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def backend() -> ShopwareStorefrontBackend:
    """The live backend class over the replay, signing every UCP request with a key the
    replay verifies like ``signaturePolicy=strict``; the handoff broker is configured so a
    ``checkout`` card carries a link."""
    signer = RequestSigner.generate()
    replay = ShopwareReplay(public_key=signer.public_key)
    transport = httpx.MockTransport(replay.handle)
    return ShopwareStorefrontBackend(
        UcpClient(
            shop_url=SHOP_URL,
            http=httpx.AsyncClient(transport=transport),
            retry_backoff=0.0,
            transport="mcp",
            signer=signer,
        ),
        store_api=StoreApiClient(
            SHOP_URL, access_key="test-key", http=httpx.AsyncClient(transport=transport)
        ),
        store_name="Shopware",
        handoff=HandoffBroker(SHOP_URL, public_url="http://host.test", secret=HANDOFF_SECRET),
    )


@pytest.fixture
def toolset(backend: ShopwareStorefrontBackend) -> ShoppingToolset:
    """A toolset bound to the replayed shop and an empty guest session."""
    return ShoppingToolset(backend=backend, config=default_config())


@pytest.fixture
def handlers(toolset: ShoppingToolset) -> dict[str, SdkMcpTool[Any]]:
    """The registered SDK tools for ``toolset``, indexed by tool name."""
    return {t.name: t for t in build_shopping_sdk_tools(toolset)}
