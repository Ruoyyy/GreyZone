# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The storefront server's own surface: the published tool list is the host's registry,
the result mapping, per-connection provenance over the Shopware backend, and the
loopback-only bind."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from storefront_mcp_server import BEHIND_GATEWAY_ENV, build_server, default_config

from commerce_common.execution import LOAD_SKILL
from commerce_common.memory import InMemoryMemoryStore
from commerce_common.testing import result_text
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent.gates import provenance_error
from shopping_agent.tools.registry import INLINE_CONTEXT_DESCRIPTIONS, build_tools
from storefront.api.agent_config import build_shopping_config
from storefront.api.tests.replay import PRODUCT_ID, SEARCH_QUERY, VARIANT_L, VARIANT_S

STOREFRONT_TOOLS = {
    "search_products",
    "get_product_details",
    "get_cart",
    "add_to_cart",
    "update_cart_item",
    "remove_from_cart",
    "get_preferences",
    "save_memory",
    "recall_memories",
    "get_orders",
    "get_order_status",
    "search_policies",
    "get_fulfillment_options",
}


def test_the_default_config_is_the_storefront_hosts():
    assert default_config() == build_shopping_config("Shopware")


async def test_tools_list_publishes_the_registrys_non_presentation_contracts(server):
    """What the hosted agent sees is the registry's contract, minus load_skill (the
    platform loads skills) and the presentation tools (custom tools the host executes),
    with the inline-context descriptions and without the ``status`` property."""
    registry = {t["name"]: t for t in build_tools(default_config(), skill_names=[])}
    async with create_connected_server_and_client_session(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(listed) == STOREFRONT_TOOLS
    assert LOAD_SKILL not in listed and "present_products" not in listed
    assert "present_disclosure" not in listed  # a custom tool in agent.yaml, host-executed
    for name, tool in listed.items():
        expected = INLINE_CONTEXT_DESCRIPTIONS.get(name, registry[name]["description"])
        assert tool.description == expected
        assert "status" not in tool.inputSchema.get("properties", {})
        published = {k: v for k, v in registry[name]["input_schema"]["properties"].items()}
        published.pop("status", None)
        assert tool.inputSchema["properties"] == published


async def test_held_calls_are_plain_results_failures_set_is_error_and_reads_are_fenced(server):
    async with create_connected_server_and_client_session(server) as client:
        held = await client.call_tool("add_to_cart", {"product_id": VARIANT_S, "quantity": 1})
        assert not held.isError and result_text(held) == provenance_error(VARIANT_S)
        failed = await client.call_tool(
            "get_product_details", {"product_id": "00000000000000000000000000000000"}
        )
        assert failed.isError
        search = await client.call_tool("search_products", {"query": SEARCH_QUERY})
        assert STOREFRONT_FENCE.open in result_text(search) and PRODUCT_ID in result_text(search)
        # A family is bought as a variant: the record read admits its variants.
        family = await client.call_tool("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
        assert not family.isError and "variant" in result_text(family).lower()
        details = await client.call_tool("get_product_details", {"product_id": PRODUCT_ID})
        assert VARIANT_S in result_text(details)
        added = await client.call_tool("add_to_cart", {"product_id": VARIANT_S, "quantity": 1})
        assert not added.isError and f"Added {VARIANT_S}" in result_text(added)
        # The seeded L is out of stock: the backend's Unavailable is an error result that
        # names the in-stock siblings.
        sold_out = await client.call_tool("add_to_cart", {"product_id": VARIANT_L, "quantity": 1})
        assert sold_out.isError and VARIANT_S in result_text(sold_out)
        # The structured filters argument reaches the executor as sent: the shirt is 29.99.
        filtered = await client.call_tool(
            "search_products", {"query": SEARCH_QUERY, "filters": {"max_price": 20}}
        )
        assert not filtered.isError and PRODUCT_ID not in result_text(filtered)


async def test_provenance_is_scoped_to_the_connection_but_the_cart_is_shared(server):
    async with create_connected_server_and_client_session(server) as first:
        await first.call_tool("search_products", {"query": SEARCH_QUERY})
        await first.call_tool("get_product_details", {"product_id": PRODUCT_ID})
        await first.call_tool("add_to_cart", {"product_id": VARIANT_S, "quantity": 1})
    async with create_connected_server_and_client_session(server) as second:
        # The first connection saw the family; this one did not.
        unseen = await second.call_tool("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
        assert result_text(unseen) == provenance_error(PRODUCT_ID)
        # The line the first connection added grants cart-membership edits.
        updated = await second.call_tool(
            "update_cart_item", {"product_id": VARIANT_S, "quantity": 3}
        )
        assert not updated.isError and "Updated quantity" in result_text(updated)
        removed = await second.call_tool("remove_from_cart", {"product_id": VARIANT_S})
        assert not removed.isError and "Removed" in result_text(removed)


async def test_policies_and_fulfillment_come_from_the_shop(server):
    async with create_connected_server_and_client_session(server) as client:
        policies = await client.call_tool("search_policies", {"query": "Widerruf Rückgabe"})
        assert not policies.isError and STOREFRONT_FENCE.open in result_text(policies)
        await client.call_tool("search_products", {"query": SEARCH_QUERY})
        options = await client.call_tool("get_fulfillment_options", {"product_ids": [PRODUCT_ID]})
        assert not options.isError and "Standard" in result_text(options)


def test_the_server_refuses_to_bind_off_loopback_without_a_gateway(backend, monkeypatch):
    monkeypatch.delenv(BEHIND_GATEWAY_ENV, raising=False)
    with pytest.raises(SystemExit, match="refusing to bind"):
        build_server(backend=backend, memory_store=InMemoryMemoryStore(), host="0.0.0.0")
    monkeypatch.setenv(BEHIND_GATEWAY_ENV, "1")
    assert build_server(backend=backend, memory_store=InMemoryMemoryStore(), host="0.0.0.0")
