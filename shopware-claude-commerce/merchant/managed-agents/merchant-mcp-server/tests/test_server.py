# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The merchant server's own surface: the published tool list is the host's registry, the
result mapping, per-connection provenance over the Shopware backend, the approval config,
and the loopback-only bind."""

from __future__ import annotations

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from merchant_mcp_server import (
    BEHIND_GATEWAY_ENV,
    DEFAULT_LEDGER_DSN,
    LEDGER_DSN_ENV,
    PLATFORM_APPROVAL_SURFACE,
    SERVER_DIR,
    build_server,
    default_config,
    load_shopware_settings,
)

from commerce_common.execution import LOAD_SKILL
from commerce_common.memory import InMemoryMemoryStore
from commerce_common.testing import result_text
from merchant.api.agent_config import build_merchant_config
from merchant.api.fake_admin import OIL, SHIRT, FakeAdmin
from merchant_agent import MerchantSessionState
from merchant_agent.analysis import ANALYSIS_TOOL
from merchant_agent.gates import check_apply_change, check_listing_provenance
from merchant_agent.tools.registry import INLINE_CONTEXT_DESCRIPTIONS, build_tools

MERCHANT_TOOLS = {
    "get_business_snapshot",
    "query_metrics",
    "get_campaign_performance",
    "search_listings",
    "get_listing",
    "get_inventory_alerts",
    "get_order_issues",
    "get_pricing_context",
    "get_pending_changes",
    "recall_memories",
    "stage_listing_update",
    "stage_price_update",
    "stage_inventory_action",
    "stage_promotion",
    "stage_campaign",
    "discard_change",
    "save_memory",
    "apply_change",
}


def test_the_default_config_is_the_hosts_with_the_platform_as_approval_surface(settings):
    config = default_config(settings)
    host = build_merchant_config("Demo Shop")
    assert config.require_host_approval is False
    assert config.stage_shows_preview is False
    assert config.approval_surface == PLATFORM_APPROVAL_SURFACE
    assert config.brand_voice == host.brand_voice and config.enable_analysis is False
    # The guardrail caps the backend stages under are the host's, unchanged.
    assert config.max_price_delta_pct == host.max_price_delta_pct
    assert config.max_items_per_change == host.max_items_per_change


def test_the_server_stages_into_its_own_ledger_never_the_hosts(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPWARE_LOCAL_STORE", "1")
    monkeypatch.setenv("MERCHANT_LEDGER_DSN", "sqlite:///./merchant/data/ledger.db")
    monkeypatch.delenv(LEDGER_DSN_ENV, raising=False)
    assert load_shopware_settings().ledger_dsn == DEFAULT_LEDGER_DSN
    assert f"sqlite:///{SERVER_DIR / '.ledger.db'}" == DEFAULT_LEDGER_DSN
    own = f"sqlite:///{tmp_path / 'server.db'}"
    monkeypatch.setenv(LEDGER_DSN_ENV, own)
    assert load_shopware_settings().ledger_dsn == own


async def test_tools_list_publishes_the_registrys_non_presentation_contracts(server, config):
    """What the hosted agent sees is the registry's contract, minus load_skill (the
    platform loads skills), run_analysis (no analysis on this path) and the presentation
    tools (custom tools the portal executes), with the inline-context description and
    without the ``status`` property."""
    registry = {t["name"]: t for t in build_tools(config, skill_names=[])}
    async with create_connected_server_and_client_session(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(listed) == MERCHANT_TOOLS
    assert LOAD_SKILL not in listed and ANALYSIS_TOOL not in listed
    assert "present_change_preview" not in listed
    for name, tool in listed.items():
        expected = INLINE_CONTEXT_DESCRIPTIONS.get(name, registry[name]["description"])
        assert tool.description == expected
        published = dict(registry[name]["input_schema"]["properties"])
        published.pop("status", None)
        assert tool.inputSchema["properties"] == published


async def _stage_oil_price(client: ClientSession, new_price: float) -> str:
    """Searches for provenance, stages an olive-oil price move, and returns the change id."""
    await client.call_tool("search_listings", {"query": "olive oil"})
    result = await client.call_tool(
        "stage_price_update", {"items": [{"listing_id": OIL, "new_price": new_price}]}
    )
    text = result_text(result)
    assert not result.isError and "Staged only" in text
    return text.split('"change_id": "', 1)[1].split('"', 1)[0]


async def test_held_calls_are_plain_results_and_the_default_config_applies_on_the_platforms_say_so(
    server, admin: FakeAdmin
):
    async with create_connected_server_and_client_session(server) as client:
        held = await client.call_tool(
            "stage_price_update", {"items": [{"listing_id": OIL, "new_price": 13.5}]}
        )
        assert not held.isError
        assert (
            result_text(held) == check_listing_provenance(MerchantSessionState(), [OIL]).result_text
        )
        failed = await client.call_tool(
            "get_listing", {"listing_id": "00000000000000000000000000000000"}
        )
        assert failed.isError
        # The structured filters argument reaches the executor as sent: the oil holds 40.
        kept = await client.call_tool(
            "search_listings", {"query": "olive oil", "filters": {"max_stock": 40}}
        )
        dropped = await client.call_tool(
            "search_listings", {"query": "olive oil", "filters": {"max_stock": 39}}
        )
        assert OIL in result_text(kept) and OIL not in result_text(dropped)

        change_id = await _stage_oil_price(client, 13.5)
        # Staging ran Shopware's dry run and recorded the payload; nothing was written yet.
        assert admin.product(OIL)["price"][0]["gross"] == 12.9
        pending = await client.call_tool("get_pending_changes", {})
        assert "server dry-run OK" in result_text(pending)
        applied = await client.call_tool("apply_change", {"change_id": change_id})
        assert not applied.isError and f"Applied {change_id}" in result_text(applied)
        assert admin.product(OIL)["price"][0]["gross"] == 13.5
        listing = await client.call_tool("get_listing", {"listing_id": OIL})
        assert '"price": 13.5' in result_text(listing)

        again = await client.call_tool("apply_change", {"change_id": change_id})
        assert again.isError and "not staged" in result_text(again)


async def test_a_family_price_without_a_variant_is_held_at_its_options(server):
    async with create_connected_server_and_client_session(server) as client:
        await client.call_tool("search_listings", {"query": "t-shirt"})
        held = await client.call_tool(
            "stage_price_update", {"items": [{"listing_id": SHIRT, "new_price": 32.0}]}
        )
        assert not held.isError and "variant" in result_text(held).lower()


async def test_campaigns_answer_with_the_backends_reason_instead_of_a_permission_refusal(
    server,
):
    """The manifest enables the campaign tools so the agent reads the deployment's own
    answer: Shopware has no marketing-activity read, and campaigns are not applied."""
    async with create_connected_server_and_client_session(server) as client:
        performance = await client.call_tool("get_campaign_performance", {})
        assert performance.isError and "marketing-activity read" in result_text(performance)
        staged = await client.call_tool("stage_campaign", {"name": "Autumn push"})
        assert staged.isError and "not applied to Shopware" in result_text(staged)


async def test_provenance_is_scoped_to_the_connection_and_the_queue_is_shared(server, settings):
    async with create_connected_server_and_client_session(server) as first:
        change_id = await _stage_oil_price(first, 13.5)
    async with create_connected_server_and_client_session(server) as second:
        staged = await second.call_tool(
            "stage_price_update", {"items": [{"listing_id": OIL, "new_price": 13.0}]}
        )
        assert (
            result_text(staged)
            == check_listing_provenance(MerchantSessionState(), [OIL]).result_text
        )
        applied = await second.call_tool("apply_change", {"change_id": change_id})
        approval_on = build_merchant_config("Demo Shop")
        expected = check_apply_change(MerchantSessionState(), approval_on, change_id)
        assert result_text(applied) == expected.result_text
        # Listing the queue grants this connection provenance for the change.
        pending = await second.call_tool("get_pending_changes", {})
        assert change_id in result_text(pending)
        applied = await second.call_tool("apply_change", {"change_id": change_id})
        assert "Applied" in result_text(applied)


def test_a_config_that_leaves_approval_on_is_reported_at_startup(
    make_backend, settings, config, capsys
):
    approval_on = config.model_copy(update={"require_host_approval": True})
    build_server(
        backend=make_backend(approval_on),
        memory_store=InMemoryMemoryStore(),
        config=approval_on,
        settings=settings,
    )
    assert "require_host_approval is on" in capsys.readouterr().err


async def test_a_config_that_leaves_approval_on_holds_every_apply(
    make_backend, admin, settings, config
):
    approval_on = config.model_copy(update={"require_host_approval": True})
    server = build_server(
        backend=make_backend(approval_on),
        memory_store=InMemoryMemoryStore(),
        config=approval_on,
        settings=settings,
    )
    async with create_connected_server_and_client_session(server) as client:
        change_id = await _stage_oil_price(client, 13.5)
        result = await client.call_tool("apply_change", {"change_id": change_id})
        assert not result.isError and "has not been approved" in result_text(result)
        assert admin.product(OIL)["price"][0]["gross"] == 12.9


def test_the_server_refuses_to_bind_off_loopback_without_a_gateway(
    backend, settings, config, monkeypatch
):
    monkeypatch.delenv(BEHIND_GATEWAY_ENV, raising=False)
    common = {
        "backend": backend,
        "memory_store": InMemoryMemoryStore(),
        "config": config,
        "settings": settings,
    }
    with pytest.raises(SystemExit, match="refusing to bind"):
        build_server(host="0.0.0.0", **common)
    monkeypatch.setenv(BEHIND_GATEWAY_ENV, "1")
    assert build_server(host="0.0.0.0", **common)
