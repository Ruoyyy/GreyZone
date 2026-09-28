# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The plugin path (``SHOPWARE_AGENT_TOOLS``): policies, disclosures and fulfillment
answered by ``SwagCommerceAgentTools`` on the replay's ``/store-api/_mcp`` with the recorded
live documents, the host implementations as fallback, and the mode detection."""

from __future__ import annotations

import httpx
import pytest

from storefront.api.agent_tools import (
    MODE_AUTO,
    MODE_HOST,
    MODE_PLUGIN,
    SHOPPING_TOOLS,
    TOOL_DISCLOSURE,
    TOOL_FULFILLMENT,
    TOOL_POLICY_SEARCH,
    ShoppingAgentTools,
    resolve_mode,
)
from storefront.api.disclosures import PLUGIN_SOURCE
from storefront.api.shopware_backend import ShopwareStorefrontBackend

from .replay import CART_ID, OIL_ID, PRODUCT_ID, VARIANT_S, ShopwareReplay

# ---------------------------------------------------------------------------- mode


def test_resolve_mode_prefers_the_plugin_only_when_all_tools_are_advertised():
    assert resolve_mode(MODE_AUTO, set(SHOPPING_TOOLS)) == MODE_PLUGIN
    assert resolve_mode(MODE_AUTO, {TOOL_POLICY_SEARCH}) == MODE_HOST
    assert resolve_mode(MODE_AUTO, set()) == MODE_HOST
    assert resolve_mode(MODE_HOST, set(SHOPPING_TOOLS)) == MODE_HOST
    assert resolve_mode(MODE_PLUGIN, set()) == MODE_PLUGIN  # the operator's call; calls fail over


async def test_detect_reads_tools_list_once_and_reports_the_effective_mode(
    agent_tools: ShoppingAgentTools, shop: ShopwareReplay
):
    assert not agent_tools.active
    assert await agent_tools.detect() == MODE_PLUGIN
    assert agent_tools.active
    assert set(SHOPPING_TOOLS) <= agent_tools.advertised
    assert agent_tools.description == "plugin (requested auto)"
    methods = [r for r in shop.requests if r.url.path == "/store-api/_mcp"]
    assert len(methods) == 3  # initialize, notifications/initialized, tools/list
    await agent_tools.aclose()
    assert shop.store_api_mcp_sessions == set()


async def test_detect_falls_back_to_the_host_path_when_the_shop_lacks_the_plugin(signer):
    shop = ShopwareReplay(public_key=signer.public_key, agent_tools_available=False)
    tools = ShoppingAgentTools(
        "http://shopware.test",
        "test-key",
        http=httpx.AsyncClient(transport=httpx.MockTransport(shop.handle)),
        mode=MODE_AUTO,
    )
    assert await tools.detect() == MODE_HOST
    assert tools.description == "host (requested auto)"
    await tools.aclose()


async def test_host_mode_never_touches_the_mcp_server(transport, shop: ShopwareReplay):
    tools = ShoppingAgentTools(
        "http://shopware.test",
        "test-key",
        http=httpx.AsyncClient(transport=transport),
        mode=MODE_HOST,
    )
    assert await tools.detect() == MODE_HOST
    assert [r for r in shop.requests if r.url.path == "/store-api/_mcp"] == []
    await tools.aclose()


async def test_without_a_sales_channel_key_the_plugin_cannot_be_reached(transport):
    tools = ShoppingAgentTools(
        "http://shopware.test", "", http=httpx.AsyncClient(transport=transport), mode=MODE_PLUGIN
    )
    assert await tools.detect() == MODE_HOST
    await tools.aclose()


# ---------------------------------------------------------------------------- policies


async def test_policies_come_from_the_plugin_and_keep_the_hosts_topical_category(
    plugin_backend: ShopwareStorefrontBackend, session, shop: ShopwareReplay
):
    policies = await plugin_backend.search_policies(session, "Widerruf")
    assert [p.title for p in policies] == ["Widerrufsbelehrung / Rückgabe"]
    assert policies[0].category == "returns"  # derived from the title, not "footer-navigation"
    assert "binnen 14 Tagen" in policies[0].content
    assert shop.agent_tool_calls[-1] == {
        "name": TOOL_POLICY_SEARCH,
        "arguments": {"query": "Widerruf", "limit": 5},
    }
    # The host index was never built: no navigation walk on the Store API.
    assert not any("/store-api/navigation" in str(r.url) for r in shop.requests)
    contact = await plugin_backend.search_policies(session, "Kontakt")
    assert contact[0].title == "Kontakt" and contact[0].category == "contact"


async def test_a_query_without_a_match_is_an_empty_answer_not_the_host_fallback(
    plugin_backend: ShopwareStorefrontBackend, session
):
    assert await plugin_backend.search_policies(session, "zzzz") == []


# ---------------------------------------------------------------------------- disclosure


async def test_disclosure_relays_the_shops_text_byte_for_byte(
    plugin_backend: ShopwareStorefrontBackend, session
):
    oil = await plugin_backend.get_disclosure(session, OIL_ID)
    assert oil is not None
    assert oil.sources == [PLUGIN_SOURCE]
    assert oil.title == "Price and delivery information"  # the channel runs en-GB
    assert [(row.label, row.value) for row in oil.rows] == [
        ("Price", "€12.90"),
        ("Base price", "Base price: €25.80 / 1 Liter"),
        ("Delivery time", "Delivery time: 2-4 Tage"),
        ("VAT", "All prices incl. VAT"),
        ("Shipping costs", "All prices plus shipping costs"),
    ]


async def test_disclosure_keeps_the_requested_id_when_the_shop_answers_the_best_child(
    plugin_backend: ShopwareStorefrontBackend, session
):
    family = await plugin_backend.get_disclosure(session, PRODUCT_ID)
    assert family is not None
    assert family.product_id == PRODUCT_ID  # the tool reported VARIANT_S
    assert any(row.value == "€29.99" for row in family.rows)


async def test_an_unknown_product_falls_back_to_the_host_disclosure_which_is_none(
    plugin_backend: ShopwareStorefrontBackend, session
):
    assert await plugin_backend.get_disclosure(session, "0" * 32) is None


# ---------------------------------------------------------------------------- fulfillment


async def test_fulfillment_options_from_the_plugin_show_carrier_time_and_availability(
    plugin_backend: ShopwareStorefrontBackend, session, shop: ShopwareReplay
):
    options = await plugin_backend.get_fulfillment_options(session, [OIL_ID, VARIANT_S])
    assert [(o.method, o.fee) for o in options] == [("shipping", 4.9), ("shipping", 9.9)]
    assert options[0].eta == "Standard: 1-3 days (Verfügbarkeit: 2-4 Tage)"
    assert options[1].eta == "Express: 1-2 Tage (Verfügbarkeit: 2-4 Tage)"
    assert shop.agent_tool_calls[-1]["name"] == TOOL_FULFILLMENT
    assert shop.agent_tool_calls[-1]["arguments"] == {"productIds": f'["{OIL_ID}", "{VARIANT_S}"]'}
    assert shop.agent_tool_tokens[-1] is None  # no cart yet: the guest context


async def test_the_sessions_cart_token_travels_with_every_plugin_call(
    plugin_backend: ShopwareStorefrontBackend, session, shop: ShopwareReplay
):
    await plugin_backend.get_product_details(session, PRODUCT_ID)
    await plugin_backend.add_to_cart(session, VARIANT_S, 1)
    assert plugin_backend.cart_id_for(session.session_id) == CART_ID
    await plugin_backend.get_fulfillment_options(session, [VARIANT_S])
    await plugin_backend.get_disclosure(session, VARIANT_S)
    await plugin_backend.search_policies(session, "Versand")
    assert shop.agent_tool_tokens[-3:] == [CART_ID, CART_ID, CART_ID]


# ---------------------------------------------------------------------------- fallback


async def test_a_failing_plugin_tool_falls_back_to_the_host_implementation_per_call(
    plugin_backend: ShopwareStorefrontBackend, session, shop: ShopwareReplay
):
    shop.agent_tool_failures.update({TOOL_POLICY_SEARCH, TOOL_DISCLOSURE, TOOL_FULFILLMENT})
    policies = await plugin_backend.search_policies(session, "widerruf")
    assert policies[0].title == "Widerrufsbelehrung"  # the host's CMS walk
    assert plugin_backend.policies.live
    oil = await plugin_backend.get_disclosure(session, OIL_ID)
    assert oil is not None and oil.sources == ["shopware-store-api"]
    await plugin_backend.get_product_details(session, PRODUCT_ID)
    options = await plugin_backend.get_fulfillment_options(session, [PRODUCT_ID])
    assert options[0].eta == "Standard: 2–4 Tage (Verfügbarkeit: 1–3 Werktage)"
    assert plugin_backend.agent_tools is not None and plugin_backend.agent_tools.active
    assert plugin_backend.agent_tools.calls.count(TOOL_POLICY_SEARCH) == 1


@pytest.mark.parametrize("ucp_transport", ["mcp"], indirect=True)
async def test_the_host_path_backend_ignores_the_plugin(backend, session, shop: ShopwareReplay):
    assert not backend.plugin_tools_active
    await backend.search_policies(session, "widerruf")
    assert shop.agent_tool_calls == []
