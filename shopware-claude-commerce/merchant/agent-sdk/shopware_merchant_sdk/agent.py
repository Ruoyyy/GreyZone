# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Shopware merchant agent as ``ClaudeAgentOptions``: the merchant host's static
prompt plus the SDK adapters, the merchant server as the only tools, the vendored skills,
and, when analysis is enabled, the delegate as a subagent restricted to the read tools; a
hook ends the turn on the round that carries the chips, as the Messages API loop does.
:func:`run_turn` adds the host-side grounding and the staging follow-through reminder.

Mirrors ``merchant-agent/runtime-agent-sdk/merchant_agent_sdk/agent.py`` at the pinned
blueprint commit. What is Shopware's: the config comes from
``merchant/api/agent_config.py`` (the house rules the FastAPI host runs under), the
backend is the live shop's Admin MCP with server dry-run previews, the operator and
merchant id come from the host's settings, and the CLI's environment carries the
``anthropic-workspace-id`` header for identity-linked keys.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient

from commerce_common.agent_sdk import (
    SKILL_TOOL_ADAPTER,
    TurnResult,
    close_on_presentation_hook,
    collect_turn,
    ensure_project_skills,
    ground,
    merge_turn_results,
)
from commerce_common.skills import SkillRegistry
from merchant.api.agent_config import ShopwareSettings, build_merchant_config
from merchant_agent import MerchantAgentConfig, MerchantBackend, MerchantSessionContext
from merchant_agent.analysis import (
    ANALYSIS_READ_TOOLS,
    build_analysis_system_prompt,
    build_analysis_tool_definition,
)
from merchant_agent.gates import STAGING_FOLLOWTHROUGH_REMINDER, turn_attempted_staging
from merchant_agent.grounding import GROUNDING_RULES, change_requested
from merchant_agent.prompt import build_static_system
from shopware_common.anthropic_client import sdk_workspace_env
from shopware_common.clock import default_timezone

from ._paths import RUNTIME_ROOT, SKILLS_DIR
from .merchant_tools import (
    SERVER_NAME,
    MerchantToolset,
    allowed_tool_names,
    build_merchant_server,
    load_shopware_backend,
    load_shopware_settings,
    mcp_tool_name,
    store_display_name,
)

# Appended when the deployment enables analysis: run_analysis is a subagent here.
ANALYSIS_AGENT_ADAPTER = """\
# Analysis in this deployment

In this deployment, run_analysis is not a tool: delegate the analysis to the
`run-analysis` subagent instead, passing the question and what you expect back. It runs
in its own context with read-only access to the store's data and returns computed
findings — quote them, never recompute or restate its numbers from memory. The rule
above about when an analysis is (and is not) worth running applies unchanged."""

logger = logging.getLogger(__name__)

ANALYSIS_AGENT_NAME = "run-analysis"
# The CLI must not fold any CLAUDE.md above cwd into the agent's context; the project
# source is on only for the materialized skills directory.
CLI_BASE_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
DEFAULT_SESSION_ID = "local-session"
DEFAULT_MAX_TURNS = 16
#: What the prompt names as the place approval happens when the console is the host.
CONSOLE_APPROVAL_SURFACE = "the y/N prompt in this console"


def build_analysis_agent(config: MerchantAgentConfig) -> AgentDefinition:
    """The delegate contract as a subagent: its description routes, its prompt runs,
    and its tools are exactly the read tools."""
    prompt = build_analysis_system_prompt(config) + (
        "\n\n# This deployment\n\n"
        "The read tools are the mcp__merchant__* tools available to you. There is no "
        "submit_analysis tool here: finish with exactly one structured summary — "
        "headline, findings, figures with units, any derived trend worth noting, and "
        "honest caveats — and nothing else; that summary is your submission."
    )
    return AgentDefinition(
        description=build_analysis_tool_definition()["description"],
        prompt=prompt,
        tools=[mcp_tool_name(name) for name in ANALYSIS_READ_TOOLS],
        model=config.analysis_model,
    )


def default_config(settings: ShopwareSettings | None = None) -> MerchantAgentConfig:
    """The merchant host's config (``merchant/api/agent_config.py``): the house rules in
    ``brand_voice``, analysis off, the store named the way the host names it. The
    approval surface is this console's prompt rather than the host's apply route."""
    settings = settings if settings is not None else load_shopware_settings()
    return build_merchant_config(store_display_name(settings)).model_copy(
        update={"approval_surface": CONSOLE_APPROVAL_SURFACE}
    )


def load_skill_registry(skills_dir: Path | None = None) -> SkillRegistry:
    return SkillRegistry.from_dir(SKILLS_DIR if skills_dir is None else skills_dir)


def build_system_prompt(config: MerchantAgentConfig, skills: SkillRegistry) -> str:
    prompt = build_static_system(config, skills) + "\n\n" + SKILL_TOOL_ADAPTER
    if config.enable_analysis:
        prompt += "\n\n" + ANALYSIS_AGENT_ADAPTER
    return prompt


def default_session(session_id: str, merchant_id: str, operator: str) -> MerchantSessionContext:
    """The operator's session under the host timezone (``HOST_TIMEZONE``, else
    Europe/Berlin); the clock is a zone, not a fixed instant, so it stays current."""
    return MerchantSessionContext(
        session_id=session_id,
        merchant_id=merchant_id,
        operator=operator,
        timezone=default_timezone(),
    )


def cli_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``ClaudeAgentOptions.env``: the CLAUDE.md switch plus, for an identity-linked key,
    the ``anthropic-workspace-id`` header the API requires, carried the way the CLI takes
    extra headers (``ANTHROPIC_CUSTOM_HEADERS``). Platform selection stays the CLI's
    environment (``docs/deployment.md`` upstream)."""
    return {**CLI_BASE_ENV, **sdk_workspace_env(environ)}


def make_options(
    *,
    backend: MerchantBackend | None = None,
    config: MerchantAgentConfig | None = None,
    settings: ShopwareSettings | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    merchant_id: str | None = None,
    operator: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    skills_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ClaudeAgentOptions, MerchantToolset]:
    """The options for one conversation and the toolset behind them; the toolset is
    where the host reads provenance, approves changes, and collects the UI payloads.
    ``settings`` (the merchant host's, loaded from the environment when absent) supplies
    the backend, the store name, the merchant id and the operator stamped on the changes
    this conversation stages; each can be passed explicitly instead. ``skills_dir``
    defaults to the vendored ``vendor/skills/merchant``."""
    if any(part is None for part in (backend, config, merchant_id, operator)):
        settings = settings if settings is not None else load_shopware_settings()
        config = config if config is not None else default_config(settings)
        merchant_id = settings.merchant_id if merchant_id is None else merchant_id
        operator = settings.operator if operator is None else operator
        backend = backend if backend is not None else load_shopware_backend(config, settings)
    assert config is not None and backend is not None
    assert merchant_id is not None and operator is not None
    skills_root = SKILLS_DIR if skills_dir is None else skills_dir
    skills = load_skill_registry(skills_root)
    toolset = MerchantToolset(
        backend=backend,
        config=config,
        session=default_session(session_id, merchant_id, operator),
    )
    ensure_project_skills(skills_root, RUNTIME_ROOT)
    options = ClaudeAgentOptions(
        system_prompt=build_system_prompt(config, skills),
        mcp_servers={SERVER_NAME: build_merchant_server(toolset)},
        allowed_tools=allowed_tool_names(config),
        # Task is the SDK's delegation transport; mounted only when there is a subagent.
        tools=["Skill", "Task"] if config.enable_analysis else ["Skill"],
        skills=skills.names,
        setting_sources=["project"],
        cwd=RUNTIME_ROOT,
        env=cli_environment(environ),
        agents=(
            {ANALYSIS_AGENT_NAME: build_analysis_agent(config)} if config.enable_analysis else None
        ),
        model=config.model,
        max_turns=max_turns,
        permission_mode="dontAsk",
        hooks=close_on_presentation_hook(toolset, config.close_on_presentation),
    )
    return options, toolset


def _nothing_staged(tool_calls: Sequence[str]) -> bool:
    return not turn_attempted_staging(tool_calls)


async def ground_message(text: str, toolset: MerchantToolset) -> str:
    """The operator's message with the reads the grounding rules call for appended."""
    return await ground(text, GROUNDING_RULES, toolset.config, toolset.state, toolset.executor)


async def run_turn(
    client: ClaudeSDKClient, text: str, *, toolset: MerchantToolset | None = None
) -> TurnResult:
    """One message in, one :class:`TurnResult` out. With a ``toolset`` the message is
    grounded first, and a change request that ends without a staging attempt gets the
    reminder as a second pass merged into the same result; until then the closing hook
    leaves a chips round open, so the reminder comes before the turn can close."""
    remind = False
    if toolset is not None:
        remind = change_requested(toolset.config, text)
        toolset.begin_turn(holds_turn_open=_nothing_staged if remind else None)
        text = await ground_message(text, toolset)
    await client.query(text)
    result = await collect_turn(client)
    if toolset is not None and remind and not turn_attempted_staging(result.tool_calls):
        toolset.holds_turn_open = None
        try:
            await client.query(STAGING_FOLLOWTHROUGH_REMINDER)
            followup = await collect_turn(client)
        except Exception:  # the reminder must not fail the turn
            logger.warning("staging reminder pass failed", exc_info=True)
            followup = None
        if followup is not None:
            result = merge_turn_results(result, followup)
    result.ui = toolset.drain_ui_events() if toolset is not None else []
    return result
