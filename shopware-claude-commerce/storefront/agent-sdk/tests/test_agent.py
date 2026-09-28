# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The SDK path's own surface: the registration is the host's registry, the allow-list
matches it, the CLI environment carries the workspace header, and the registered tools
round-trip through the Shopware backend's gates."""

from __future__ import annotations

import shopware_shopping_sdk.agent as agent_module
from shopware_shopping_sdk import (
    RUNTIME_ROOT,
    SKILLS_DIR,
    ShoppingToolset,
    allowed_tool_names,
    build_shopping_sdk_tools,
    build_system_prompt,
    cli_environment,
    default_config,
    load_skill_registry,
    make_options,
    tool_names,
)
from shopware_shopping_sdk.host import render_checkout

from commerce_common.agent_sdk import CLOSE_HOOK_EVENT, SKILL_TOOL_ADAPTER
from commerce_common.execution import LOAD_SKILL
from commerce_common.testing import result_text
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent.gates import provenance_error
from shopping_agent.tools.registry import build_tools
from shopware_common.anthropic_client import SDK_CUSTOM_HEADERS_ENV, WORKSPACE_ID_ENV
from storefront.api.agent_config import build_shopping_config
from storefront.api.tests.replay import PRODUCT_ID, SEARCH_QUERY, VARIANT_L, VARIANT_S

ONE_SKILL = "---\nname: gift-registry\ndescription: Registry requests.\n---\n\nBody.\n"
WORKSPACE_ID = "wrkspc_01TESTWORKSPACE"
VENDORED_SHOPPING_SKILLS = [
    "customer-care",
    "memory-personalization",
    "planning-goals",
    "purchase-research",
    "search-discovery",
]


def test_default_config_is_the_storefront_hosts():
    """The console and the FastAPI host run the model under one config: brand voice with
    the date rule, the Shopware domain notes, hex-UUID ids, disclosures on."""
    config = default_config()
    assert config == build_shopping_config("Shopware")
    assert config.enable_disclosures is True
    assert config.product_id_patterns == (r"\b[0-9a-f]{32}\b",)


def test_the_toolset_registers_exactly_the_hosts_registry_minus_skill_loading(backend):
    config = default_config()
    registry = [t["name"] for t in build_tools(config, skill_names=[])]
    assert LOAD_SKILL in registry
    _, toolset = make_options(backend=backend, environ={})
    registered = [t.name for t in build_shopping_sdk_tools(toolset)]
    assert registered == [name for name in registry if name != LOAD_SKILL]
    assert registered == tool_names(config)
    assert "present_disclosure" in registered  # the Shopware config switches disclosures on


def test_config_gated_tools_register_and_allowlist_in_lockstep(backend):
    """Under "dontAsk", a registered tool missing from allowed_tools is refused on every call."""
    options, toolset = make_options(backend=backend, environ={})
    registered = [t.name for t in build_shopping_sdk_tools(toolset)]
    assert options.allowed_tools == [f"mcp__storefront__{name}" for name in registered]
    assert options.allowed_tools == allowed_tool_names(toolset.config)
    assert options.tools == ["Skill"]
    assert options.permission_mode == "dontAsk"
    assert options.model == default_config().model
    assert options.cwd == RUNTIME_ROOT
    # The turn ends on the round that carries the chips, as on the Messages API path.
    assert list(options.hooks) == [CLOSE_HOOK_EVENT]


def test_skills_dir_selects_the_indexed_and_materialized_skills(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "RUNTIME_ROOT", tmp_path / "runtime")
    skills_dir = tmp_path / "skills"
    (skills_dir / "gift-registry").mkdir(parents=True)
    (skills_dir / "gift-registry" / "SKILL.md").write_text(ONE_SKILL, encoding="utf-8")
    options, _ = make_options(backend=backend, skills_dir=skills_dir, environ={})
    assert options.skills == ["gift-registry"]
    assert "`gift-registry`" in options.system_prompt
    materialized = tmp_path / "runtime" / ".claude" / "skills"
    assert [p.name for p in materialized.iterdir()] == ["gift-registry"]
    default, _ = make_options(backend=backend, environ={})
    assert default.skills == load_skill_registry().names == VENDORED_SHOPPING_SKILLS
    assert SKILLS_DIR.name == "shopping" and SKILLS_DIR.parent.parent.name == "vendor"
    assert default.system_prompt == build_system_prompt(default_config(), load_skill_registry())
    assert default.system_prompt.endswith(SKILL_TOOL_ADAPTER)
    assert "Shopware" in default.system_prompt and "Grundpreis" in default.system_prompt


def test_the_cli_environment_carries_the_workspace_header_only_for_identity_linked_keys(
    backend,
):
    plain, _ = make_options(backend=backend, environ={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert plain.env == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
    linked, _ = make_options(
        backend=backend,
        environ={"ANTHROPIC_API_KEY": "sk-ant-test", WORKSPACE_ID_ENV: WORKSPACE_ID},
    )
    assert linked.env[SDK_CUSTOM_HEADERS_ENV] == f"anthropic-workspace-id: {WORKSPACE_ID}"
    assert linked.env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert cli_environment({WORKSPACE_ID_ENV: WORKSPACE_ID}) == linked.env


def test_the_session_is_a_guest_under_the_host_timezone(backend, monkeypatch):
    monkeypatch.setenv("HOST_TIMEZONE", "Europe/Berlin")
    _, toolset = make_options(backend=backend, session_id="s-42", environ={})
    assert toolset.session.session_id == "s-42"
    assert toolset.session.user_id == "guest"
    assert toolset.session.timezone == "Europe/Berlin"
    assert toolset.session.now is None  # a zone, not a fixed instant


async def test_held_calls_are_plain_results_and_failures_are_flagged(handlers):
    held = await handlers["add_to_cart"].handler({"product_id": VARIANT_S, "quantity": 1})
    assert "is_error" not in held
    assert result_text(held) == provenance_error(VARIANT_S)
    failed = await handlers["get_product_details"].handler(
        {"product_id": "00000000000000000000000000000000"}
    )
    assert failed["is_error"] is True


async def test_search_details_add_and_present_round_trip_through_the_registered_tools(
    handlers, toolset: ShoppingToolset
):
    search = await handlers["search_products"].handler({"query": SEARCH_QUERY})
    assert STOREFRONT_FENCE.open in result_text(search) and PRODUCT_ID in result_text(search)
    # A family is bought as one of its variants: the add is held until the record is read.
    family = await handlers["add_to_cart"].handler({"product_id": PRODUCT_ID, "quantity": 1})
    assert "is_error" not in family and "variant" in result_text(family).lower()
    details = await handlers["get_product_details"].handler({"product_id": PRODUCT_ID})
    assert VARIANT_S in result_text(details) and VARIANT_L in result_text(details)
    added = await handlers["add_to_cart"].handler({"product_id": VARIANT_S, "quantity": 2})
    assert "is_error" not in added and f"Added {VARIANT_S} x2" in result_text(added)
    # The seeded L is out of stock: the backend's Unavailable becomes an error result that
    # names the in-stock siblings, and nothing was added.
    sold_out = await handlers["add_to_cart"].handler({"product_id": VARIANT_L, "quantity": 1})
    assert sold_out["is_error"] is True
    assert "Nothing was added" in result_text(sold_out) and VARIANT_S in result_text(sold_out)
    presented = await handlers["present_products"].handler({"picks": [{"product_id": PRODUCT_ID}]})
    assert "is_error" not in presented
    events = toolset.drain_ui_events()
    assert [event["component"] for event in events] == ["products"]
    assert events[0]["payload"]["items"][0]["product"]["product_id"] == PRODUCT_ID
    assert toolset.drain_ui_events() == []


async def test_the_checkout_card_is_rendered_with_a_one_time_continue_link(
    handlers, toolset: ShoppingToolset
):
    """The console is the checkout host: the ticket URL only a storefront host process
    could serve is replaced by the shop's continue link carrying a fresh one-time code."""
    await handlers["search_products"].handler({"query": SEARCH_QUERY})
    await handlers["get_product_details"].handler({"product_id": PRODUCT_ID})
    await handlers["add_to_cart"].handler({"product_id": VARIANT_S, "quantity": 1})
    toolset.drain_ui_events()
    checkout = await handlers["checkout"].handler({})
    assert "is_error" not in checkout
    (event,) = toolset.drain_ui_events()
    assert event["component"] == "checkout"
    ticket_url = event["payload"]["handoffs"][0]["url"]
    assert ticket_url.startswith("http://host.test/api/checkout/handoff/")
    rendered = render_checkout(event["payload"], toolset)
    (handoff,) = rendered["handoffs"]
    assert handoff["url"].startswith("http://shopware.test/claude-commerce/continue?code=")
    assert handoff["label"] == event["payload"]["handoffs"][0]["label"]
    # The tool result the model read carries neither URL.
    assert "http" not in result_text(checkout)
    # Every rendering mints a fresh single-use code.
    assert render_checkout(event["payload"], toolset)["handoffs"][0]["url"] != handoff["url"]
