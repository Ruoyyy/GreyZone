# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Every fixture here runs over ``replay_transport`` — Shopware-shaped responses, never the
live shop. The backend fixtures are parametrised over both UCP transports (MCP, REST) and
sign every request; the replay verifies the signatures like ``signaturePolicy=strict``."""

from __future__ import annotations

import os

os.environ.setdefault("CATALOG_WARMUP", "0")
os.environ.setdefault("SHOPWARE_URL", "http://shopware.test")
os.environ.setdefault("SHOPWARE_SALES_CHANNEL_ACCESS_KEY", "test-key")
os.environ.setdefault("UCP_AGENT_PROFILE_URL", "http://shopware.test/.well-known/ucp")
os.environ.setdefault("UCP_TRANSPORT", "mcp")
os.environ.setdefault("STOREFRONT_API_PUBLIC_URL", "http://host.test")
os.environ.setdefault(
    "COMMERCE_AGENTS_HANDOFF_SECRET",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
os.environ.pop("UCP_AGENT_SIGNING_KEY_PEM_FILE", None)

import httpx
import pytest

from shopping_agent import ShoppingSessionContext, ShoppingSessionState
from shopware_common.http_signing import RequestSigner
from storefront.api.agent_tools import MODE_AUTO, ShoppingAgentTools
from storefront.api.handoff import HandoffBroker
from storefront.api.identity import OrderIdentity
from storefront.api.shopware_backend import ShopwareStorefrontBackend
from storefront.api.store_api import StoreApiClient
from storefront.api.ucp_client import UcpClient

from .replay import SEARCH_QUERY, ShopwareReplay

__all__ = ["SEARCH_QUERY"]

HANDOFF_SECRET = os.environ["COMMERCE_AGENTS_HANDOFF_SECRET"]
TEST_ORDER_IDENTITY = OrderIdentity(
    owner_id="customer-1",
    buyer={"first_name": "Test", "last_name": "Customer", "email": "customer@example.test"},
    fulfillment={"methods": [{"type": "shipping", "destinations": [{
        "id": "address-1", "street_address": "Example Street 1", "postal_code": "10115",
        "address_locality": "Berlin", "address_country": "DE",
    }]}]},
)


@pytest.fixture
def signer() -> RequestSigner:
    return RequestSigner.generate()


@pytest.fixture
def shop(signer: RequestSigner) -> ShopwareReplay:
    return ShopwareReplay(public_key=signer.public_key)


@pytest.fixture
def transport(shop: ShopwareReplay) -> httpx.MockTransport:
    return httpx.MockTransport(shop.handle)


@pytest.fixture(params=["mcp", "rest"])
def ucp_transport(request) -> str:
    return request.param


@pytest.fixture
def client(transport: httpx.MockTransport, signer: RequestSigner, ucp_transport: str) -> UcpClient:
    return UcpClient(
        shop_url="http://shopware.test",
        http=httpx.AsyncClient(transport=transport),
        retry_backoff=0.0,
        transport=ucp_transport,  # type: ignore[arg-type]
        signer=signer,
    )


@pytest.fixture
def store_api(transport: httpx.MockTransport) -> StoreApiClient:
    return StoreApiClient(
        "http://shopware.test",
        access_key="test-key",
        http=httpx.AsyncClient(transport=transport),
    )


@pytest.fixture
def handoff() -> HandoffBroker:
    return HandoffBroker(
        "http://shopware.test", public_url="http://host.test", secret=HANDOFF_SECRET
    )


@pytest.fixture
def backend(
    client: UcpClient, store_api: StoreApiClient, handoff: HandoffBroker
) -> ShopwareStorefrontBackend:
    """The host path for policies / disclosures / fulfillment (no ``agent_tools``)."""
    return ShopwareStorefrontBackend(
        client,
        store_api=store_api,
        store_name="Shopware",
        handoff=handoff,
        order_identity_provider=lambda _session_id: TEST_ORDER_IDENTITY,
    )


@pytest.fixture
def agent_tools(transport: httpx.MockTransport) -> ShoppingAgentTools:
    """``SwagCommerceAgentTools`` on the replay's ``/store-api/_mcp``, mode ``auto`` (not yet
    detected — tests call ``detect()`` or set the mode)."""
    return ShoppingAgentTools(
        "http://shopware.test",
        "test-key",
        http=httpx.AsyncClient(transport=transport),
        mode=MODE_AUTO,
    )


@pytest.fixture
async def plugin_backend(
    client: UcpClient,
    store_api: StoreApiClient,
    handoff: HandoffBroker,
    agent_tools: ShoppingAgentTools,
) -> ShopwareStorefrontBackend:
    """The plugin path: ``detect()`` has run against a shop that advertises the tools."""
    await agent_tools.detect()
    assert agent_tools.active
    return ShopwareStorefrontBackend(
        client,
        store_api=store_api,
        store_name="Shopware",
        handoff=handoff,
        agent_tools=agent_tools,
        order_identity_provider=lambda _session_id: TEST_ORDER_IDENTITY,
    )


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="s-1", user_id="guest")


@pytest.fixture
def state() -> ShoppingSessionState:
    return ShoppingSessionState()
