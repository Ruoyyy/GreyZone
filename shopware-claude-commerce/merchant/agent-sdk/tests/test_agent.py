# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The SDK path's own surface: the registration is the host's registry, the toolset is
the approval surface, the CLI environment carries the workspace header, the prefetch
texts, and run_turn's reminder pass — over the Shopware backend on the fake Admin."""

from __future__ import annotations

import pytest
import shopware_merchant_sdk.agent as agent_module
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from shopware_merchant_sdk import (
    ANALYSIS_AGENT_ADAPTER,
    ANALYSIS_AGENT_NAME,
    CONSOLE_APPROVAL_SURFACE,
    DEFAULT_LEDGER_DSN,
    LEDGER_DSN_ENV,
    RUNTIME_ROOT,
    SKILLS_DIR,
    MerchantToolset,
    allowed_tool_names,
    build_merchant_sdk_tools,
    build_system_prompt,
    cli_environment,
    default_config,
    ground_message,
    load_shopware_settings,
    load_skill_registry,
    make_options,
    mcp_tool_name,
    run_turn,
    tool_contracts,
    tool_names,
)

from commerce_common.agent_sdk import CLOSE_HOOK_EVENT, SKILL_TOOL_ADAPTER
from commerce_common.execution import LOAD_SKILL
from commerce_common.testing import result_text
from merchant.api.agent_config import MERCHANT_BRAND_VOICE, build_merchant_config
from merchant.api.fake_admin import OIL, SHIRT, FakeAdmin
from merchant_agent import MerchantAgentConfig, MerchantSessionState
from merchant_agent.analysis import (
    ANALYSIS_READ_TOOLS,
    ANALYSIS_TOOL,
    build_analysis_tool_definition,
)
from merchant_agent.fencing import MERCHANT_FENCE
from merchant_agent.gates import (
    STAGED_AND_SHOWN_NOTE,
    STAGING_FOLLOWTHROUGH_REMINDER,
    check_listing_provenance,
)
from merchant_agent.tools.registry import build_tools
from shopware_common.anthropic_client import SDK_CUSTOM_HEADERS_ENV, WORKSPACE_ID_ENV

CHANGE_TEXT = "Raise the olive oil price from 12.90 € to 13.50 € for the autumn push."
ONE_SKILL = "---\nname: supplier-orders\ndescription: Reorder requests.\n---\n\nBody.\n"
WORKSPACE_ID = "wrkspc_01TESTWORKSPACE"
VENDORED_MERCHANT_SKILLS = [
    "catalog-listings",
    "inventory-operations",
    "marketing-campaigns",
    "performance-insights",
    "pricing-promotions",
]


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="test-model")


def _result(cost: float = 0.01) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s-1",
        total_cost_usd=cost,
    )


class ScriptedClient:
    """A ClaudeSDKClient stand-in that plays back one scripted response stream per query."""

    def __init__(self, scripts: list) -> None:
        self.queries: list[str] = []
        self._scripts = list(scripts)

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def receive_response(self):
        assert self._scripts, "more queries than scripted responses"
        script = self._scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for message in script:
            yield message


# -- registration ----------------------------------------------------------------------


def test_default_config_is_the_merchant_hosts_with_the_console_as_approval_surface(settings):
    config = default_config(settings)
    host = build_merchant_config("Demo Shop")
    assert config.brand_name == "Demo Shop" and config.brand_voice == MERCHANT_BRAND_VOICE
    assert config.enable_analysis is False
    assert config.approval_surface == CONSOLE_APPROVAL_SURFACE
    assert config.model_copy(update={"approval_surface": host.approval_surface}) == host


def test_registered_tools_are_the_registry_minus_skill_loading_and_analysis(backend, settings):
    config = default_config(settings).model_copy(update={"enable_analysis": True})
    _, toolset = make_options(backend=backend, config=config, settings=settings, environ={})
    registered = [t.name for t in build_merchant_sdk_tools(toolset)]
    registry = [t["name"] for t in build_tools(config, skill_names=[])]
    assert {LOAD_SKILL, ANALYSIS_TOOL} <= set(registry)
    assert registered == [n for n in registry if n not in {LOAD_SKILL, ANALYSIS_TOOL}]
    assert registered == [n for n in tool_contracts(config) if n not in {LOAD_SKILL, ANALYSIS_TOOL}]
    assert registered == tool_names(config)


def test_config_gated_tools_register_and_allowlist_in_lockstep(backend, config, settings):
    options, toolset = make_options(backend=backend, config=config, settings=settings, environ={})
    registered = [t.name for t in build_merchant_sdk_tools(toolset)]
    assert options.allowed_tools == [f"mcp__merchant__{name}" for name in registered]
    assert options.allowed_tools == allowed_tool_names(config)
    assert options.permission_mode == "dontAsk"
    assert options.model == config.model
    assert options.cwd == RUNTIME_ROOT
    # Analysis is off in the host's config: no subagent, Skill is the only built-in.
    assert options.agents is None
    assert options.tools == ["Skill"]
    assert ANALYSIS_AGENT_ADAPTER not in options.system_prompt
    assert list(options.hooks) == [CLOSE_HOOK_EVENT]


def test_skills_dir_selects_the_indexed_and_materialized_skills(
    backend, config, settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "RUNTIME_ROOT", tmp_path / "runtime")
    skills_dir = tmp_path / "skills"
    (skills_dir / "supplier-orders").mkdir(parents=True)
    (skills_dir / "supplier-orders" / "SKILL.md").write_text(ONE_SKILL, encoding="utf-8")
    common = {"backend": backend, "config": config, "settings": settings, "environ": {}}
    options, _ = make_options(skills_dir=skills_dir, **common)
    assert options.skills == ["supplier-orders"]
    assert "`supplier-orders`" in options.system_prompt
    materialized = tmp_path / "runtime" / ".claude" / "skills"
    assert [p.name for p in materialized.iterdir()] == ["supplier-orders"]
    default, _ = make_options(**common)
    assert default.skills == load_skill_registry().names == VENDORED_MERCHANT_SKILLS
    assert SKILLS_DIR.name == "merchant" and SKILLS_DIR.parent.parent.name == "vendor"
    assert default.system_prompt == build_system_prompt(config, load_skill_registry())
    assert default.system_prompt.endswith(SKILL_TOOL_ADAPTER)
    assert "product number" in default.system_prompt  # the host's house rule on ids


def test_analysis_maps_onto_a_read_only_subagent(backend, settings):
    config = default_config(settings).model_copy(update={"enable_analysis": True})
    options, _ = make_options(backend=backend, config=config, settings=settings, environ={})
    assert options.tools == ["Skill", "Task"]
    assert set(options.agents) == {ANALYSIS_AGENT_NAME}
    agent = options.agents[ANALYSIS_AGENT_NAME]
    assert agent.description == build_analysis_tool_definition()["description"]
    assert agent.tools == [mcp_tool_name(name) for name in ANALYSIS_READ_TOOLS]
    assert not any(
        fragment in tool
        for tool in agent.tools
        for fragment in ("stage", "apply", "discard", "present", "memory")
    )
    assert ANALYSIS_AGENT_ADAPTER in options.system_prompt


def test_the_identity_comes_from_the_hosts_settings_unless_given(backend, config, settings):
    _, from_settings = make_options(backend=backend, config=config, settings=settings, environ={})
    assert from_settings.session.merchant_id == settings.merchant_id == "http://shopware.test"
    assert from_settings.session.operator == "Dana"
    assert from_settings.session.timezone is not None and from_settings.session.now is None
    _, explicit = make_options(
        backend=backend, config=config, merchant_id="shop-1", operator="Kim", environ={}
    )
    assert (explicit.session.merchant_id, explicit.session.operator) == ("shop-1", "Kim")


def test_the_cli_environment_carries_the_workspace_header_only_for_identity_linked_keys(
    backend, config, settings
):
    common = {"backend": backend, "config": config, "settings": settings}
    plain, _ = make_options(environ={"ANTHROPIC_API_KEY": "sk-ant-test"}, **common)
    assert plain.env == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
    linked, _ = make_options(environ={WORKSPACE_ID_ENV: WORKSPACE_ID}, **common)
    assert linked.env[SDK_CUSTOM_HEADERS_ENV] == f"anthropic-workspace-id: {WORKSPACE_ID}"
    assert cli_environment({WORKSPACE_ID_ENV: WORKSPACE_ID}) == linked.env


def test_the_console_stages_into_its_own_ledger_never_the_hosts(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPWARE_LOCAL_STORE", "1")
    monkeypatch.setenv("MERCHANT_LEDGER_DSN", "sqlite:///./merchant/data/ledger.db")
    monkeypatch.delenv(LEDGER_DSN_ENV, raising=False)
    settings = load_shopware_settings()
    assert settings.local_store is True
    assert settings.ledger_dsn == DEFAULT_LEDGER_DSN
    assert f"sqlite:///{RUNTIME_ROOT / '.ledger.db'}" == DEFAULT_LEDGER_DSN
    own = f"sqlite:///{tmp_path / 'console.db'}"
    monkeypatch.setenv(LEDGER_DSN_ENV, own)
    assert load_shopware_settings().ledger_dsn == own


# -- the toolset ------------------------------------------------------------------------


async def test_held_calls_are_plain_results_and_failures_are_flagged(handlers):
    held = await handlers["stage_price_update"].handler(
        {"items": [{"listing_id": OIL, "new_price": 13.5}]}
    )
    assert "is_error" not in held
    assert result_text(held) == check_listing_provenance(MerchantSessionState(), [OIL]).result_text
    failed = await handlers["get_listing"].handler(
        {"listing_id": "00000000000000000000000000000000"}
    )
    assert failed["is_error"] is True


async def test_search_stage_preview_and_apply_round_trip_through_the_registered_tools(
    handlers, toolset: MerchantToolset, admin: FakeAdmin
):
    search = await handlers["search_listings"].handler({"query": "olive oil"})
    assert MERCHANT_FENCE.open in result_text(search) and OIL in result_text(search)
    staged = await handlers["stage_price_update"].handler(
        {"items": [{"listing_id": OIL, "new_price": 13.5}]}
    )
    assert STAGED_AND_SHOWN_NOTE in result_text(staged)
    change_id = next(iter(toolset.state.seen_changes))
    # Staging ran Shopware's dry run; nothing was written yet.
    assert admin.product(OIL)["price"][0]["gross"] == 12.9
    assert "server dry-run OK" in " ".join(toolset.state.seen_changes[change_id].guardrail_notes)
    (shown,) = toolset.drain_ui_events()
    assert shown["component"] == "change_preview" and shown["payload"]["change_id"] == change_id
    await handlers["present_change_preview"].handler({"change_id": change_id})
    assert [event["component"] for event in toolset.drain_ui_events()] == ["change_preview"]
    applied = await handlers["apply_change"].handler({"change_id": change_id})
    assert "is_error" not in applied and toolset.session.operator in result_text(applied)
    assert admin.product(OIL)["price"][0]["gross"] == 13.5
    assert toolset.state.seen_changes[change_id].applied_by == toolset.session.operator


async def test_host_approval_goes_through_the_toolsets_approval_api(
    handlers, toolset: MerchantToolset, admin: FakeAdmin
):
    """The console's y/N answer is the blueprint's host approval mark: without it, apply
    is held and names the console prompt; with it, the change is written."""
    toolset.config.require_host_approval = True
    await handlers["search_listings"].handler({"query": "olive oil"})
    await handlers["stage_price_update"].handler(
        {"items": [{"listing_id": OIL, "new_price": 13.5}]}
    )
    change_id = next(iter(toolset.state.seen_changes))
    assert [c.change_id for c in toolset.pending_host_approvals()] == [change_id]

    refused = await handlers["apply_change"].handler({"change_id": change_id})
    assert "is_error" not in refused
    assert CONSOLE_APPROVAL_SURFACE in result_text(refused)
    assert admin.product(OIL)["price"][0]["gross"] == 12.9

    # A cleared mark approves nothing: the change is pending again and apply is held.
    toolset.host_approve(change_id)
    toolset.host_clear(change_id)
    assert [c.change_id for c in toolset.pending_host_approvals()] == [change_id]
    held = await handlers["apply_change"].handler({"change_id": change_id})
    assert CONSOLE_APPROVAL_SURFACE in result_text(held)

    toolset.host_approve(change_id)
    applied = await handlers["apply_change"].handler({"change_id": change_id})
    assert "is_error" not in applied
    assert admin.product(OIL)["price"][0]["gross"] == 13.5
    assert toolset.pending_host_approvals() == []


async def test_a_family_price_without_a_variant_is_held_at_its_options(handlers):
    await handlers["search_listings"].handler({"query": "t-shirt"})
    held = await handlers["stage_price_update"].handler(
        {"items": [{"listing_id": SHIRT, "new_price": 32.0}]}
    )
    assert "is_error" not in held and "variant" in result_text(held).lower()


# -- ground_message: the two rules' prefetch texts ---------------------------------------


async def test_a_performance_question_is_grounded_with_the_fenced_snapshot(toolset):
    text = "How did sales do this week?"
    grounded = await ground_message(text, toolset)
    assert grounded.startswith(text) and MERCHANT_FENCE.open in grounded and '"sales"' in grounded
    assert toolset.state.latest_snapshot is not None


async def test_an_apply_request_is_grounded_with_the_queue(toolset):
    text = "There's a price change we settled on yesterday — go ahead and put it through."
    grounded = await ground_message(text, toolset)
    assert grounded.startswith(text) and "Pending approval queue for this turn" in grounded


# -- run_turn --------------------------------------------------------------------------


async def test_change_turn_without_staging_sends_the_reminder_once(toolset):
    client = ScriptedClient(
        [
            [_assistant(TextBlock("Want me to set that up?")), _result(0.01)],
            [_assistant(TextBlock("Here is the follow-through.")), _result(0.02)],
        ]
    )
    result = await run_turn(client, CHANGE_TEXT, toolset=toolset)
    assert client.queries == [CHANGE_TEXT, STAGING_FOLLOWTHROUGH_REMINDER]
    assert "Want me to set that up?" in result.text
    assert "Here is the follow-through." in result.text
    assert result.cost_usd == pytest.approx(0.03)


async def test_reminder_suppressed_when_a_stage_tool_ran(toolset):
    client = ScriptedClient(
        [
            [
                _assistant(
                    ToolUseBlock(
                        id="t1",
                        name=mcp_tool_name("stage_price_update"),
                        input={"items": [{"listing_id": OIL, "new_price": 13.5}]},
                    ),
                    TextBlock("Staged — review the preview to apply it."),
                ),
                _result(),
            ]
        ]
    )
    result = await run_turn(client, CHANGE_TEXT, toolset=toolset)
    assert client.queries == [CHANGE_TEXT]
    assert result.tool_calls == [mcp_tool_name("stage_price_update")]


async def test_non_change_turn_is_never_reminded(toolset):
    client = ScriptedClient([[_assistant(TextBlock("All quiet this morning.")), _result()]])
    await run_turn(client, "Anything urgent in the queue this morning?", toolset=toolset)
    assert client.queries == ["Anything urgent in the queue this morning?"]


async def test_reminded_turn_merges_tool_calls_and_ui(handlers, toolset):
    # On the reminder query the client stages through the real handler, which renders
    # the change's preview card.
    await handlers["search_listings"].handler({"query": "olive oil"})

    class RemindedPassClient(ScriptedClient):
        async def query(self, text: str) -> None:
            await ScriptedClient.query(self, text)
            if text != STAGING_FOLLOWTHROUGH_REMINDER:
                return
            await handlers["stage_price_update"].handler(
                {"items": [{"listing_id": OIL, "new_price": 13.5}]}
            )

    client = RemindedPassClient(
        [
            [
                _assistant(
                    ToolUseBlock(
                        id="t1",
                        name=mcp_tool_name("get_pricing_context"),
                        input={"listing_id": OIL},
                    ),
                    TextBlock("Here is the pricing context."),
                ),
                _result(0.01),
            ],
            [
                _assistant(
                    ToolUseBlock(
                        id="t2",
                        name=mcp_tool_name("stage_price_update"),
                        input={"items": [{"listing_id": OIL, "new_price": 13.5}]},
                    ),
                    TextBlock("Staged — review the preview to apply it."),
                ),
                _result(0.02),
            ],
        ]
    )
    result = await run_turn(client, CHANGE_TEXT, toolset=toolset)
    assert client.queries == [CHANGE_TEXT, STAGING_FOLLOWTHROUGH_REMINDER]
    assert result.tool_calls == [
        mcp_tool_name("get_pricing_context"),
        mcp_tool_name("stage_price_update"),
    ]
    assert [event["component"] for event in result.ui] == ["change_preview"]
    assert toolset.drain_ui_events() == []


async def test_reminder_failure_degrades_to_the_unreminded_result(toolset):
    client = ScriptedClient(
        [
            [_assistant(TextBlock("Want me to set that up?")), _result(0.01)],
            RuntimeError("stream dropped"),
        ]
    )
    result = await run_turn(client, CHANGE_TEXT, toolset=toolset)
    assert client.queries == [CHANGE_TEXT, STAGING_FOLLOWTHROUGH_REMINDER]
    assert result.text == "Want me to set that up?"
    assert result.cost_usd == pytest.approx(0.01)
    assert not result.is_error


def test_the_default_config_needs_no_analysis_config_on_the_fake(settings):
    """``MerchantAgentConfig`` rejects unknown fields; the host's config round-trips."""
    assert MerchantAgentConfig.model_validate(default_config(settings).model_dump())
