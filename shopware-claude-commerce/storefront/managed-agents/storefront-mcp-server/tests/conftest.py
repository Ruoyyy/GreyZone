# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The server over the Shopware replay (``storefront/api/tests/replay.py``): UCP over MCP
with signature verification, Store API, no network."""

from __future__ import annotations

import os

os.environ.setdefault("CATALOG_WARMUP", "0")
os.environ.setdefault("SHOPWARE_URL", "http://shopware.test")
os.environ.setdefault("SHOPWARE_SALES_CHANNEL_ACCESS_KEY", "test-key")
os.environ.setdefault("UCP_AGENT_PROFILE_URL", "http://shopware.test/.well-known/ucp")
os.environ.setdefault("STOREFRONT_API_PUBLIC_URL", "http://host.test")
os.environ.pop("UCP_AGENT_SIGNING_KEY_PEM_FILE", None)
os.environ.pop("STOREFRONT_MCP_BEHIND_GATEWAY", None)

import httpx
import pytest
from storefront_mcp_server import build_server

from commerce_common.memory import InMemoryMemoryStore
from shopware_common.http_signing import RequestSigner
from storefront.api.handoff import HandoffBroker
from storefront.api.shopware_backend import ShopwareStorefrontBackend
from storefront.api.store_api import StoreApiClient
from storefront.api.tests.replay import ShopwareReplay
from storefront.api.ucp_client import UcpClient

SHOP_URL = "http://shopware.test"
HANDOFF_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def replay_backend() -> ShopwareStorefrontBackend:
    """The live backend class over a fresh replay, signing every UCP request with a key
    the replay verifies like ``signaturePolicy=strict``."""
    signer = RequestSigner.generate()
    transport = httpx.MockTransport(ShopwareReplay(public_key=signer.public_key).handle)
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
def backend() -> ShopwareStorefrontBackend:
    return replay_backend()


@pytest.fixture
def server(backend: ShopwareStorefrontBackend):
    return build_server(backend=backend, memory_store=InMemoryMemoryStore())
