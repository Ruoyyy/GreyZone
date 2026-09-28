# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Shopware shopping agent as ``ClaudeAgentOptions``: the storefront host's static
prompt plus the skill adapter, the storefront server as the only tools, and the vendored
skills materialized for the SDK. ``run_turn`` grounds the message the way the Messages
API runtime's forced first call does, then collects the response and the UI payloads; a
hook ends the turn on the round that carries the chips, as that runtime's loop does.

Mirrors ``shopping-agent/runtime-agent-sdk/shopping_agent_sdk/agent.py`` at the pinned
blueprint commit. What is Shopware's: the config comes from
``storefront/api/agent_config.py`` (the same one the FastAPI host runs under), the
backend is the live shop, the session clock carries the host timezone, and the CLI's
environment carries the ``anthropic-workspace-id`` header for identity-linked keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from commerce_common.agent_sdk import (
    SKILL_TOOL_ADAPTER,
    TurnResult,
    close_on_presentation_hook,
    collect_turn,
    ensure_project_skills,
    ground,
)
from commerce_common.skills import SkillRegistry
from shopping_agent import ShoppingAgentConfig, ShoppingSessionContext, StorefrontBackend
from shopping_agent.grounding import GROUNDING_RULES
from shopping_agent.prompt import build_static_system
from shopware_common.anthropic_client import sdk_workspace_env
from shopware_common.clock import default_timezone
from storefront.api.agent_config import build_shopping_config

from ._paths import RUNTIME_ROOT, SKILLS_DIR
from .shopping_tools import (
    SERVER_NAME,
    STORE_NAME,
    ShoppingToolset,
    allowed_tool_names,
    build_shopping_server,
    load_shopware_backend,
)

# The CLI must not fold any CLAUDE.md above cwd into the agent's context; the project
# source is on only for the materialized skills directory.
CLI_BASE_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
DEFAULT_SESSION_ID = "local-session"
DEFAULT_USER_ID = "guest"
DEFAULT_MAX_TURNS = 16


def default_config() -> ShoppingAgentConfig:
    """The storefront host's config: brand voice with the date rule, the Shopware domain
    notes, hex-UUID product ids, disclosures on (``storefront/api/agent_config.py``)."""
    return build_shopping_config(STORE_NAME)


def load_skill_registry(skills_dir: Path | None = None) -> SkillRegistry:
    return SkillRegistry.from_dir(SKILLS_DIR if skills_dir is None else skills_dir)


def build_system_prompt(config: ShoppingAgentConfig, skills: SkillRegistry) -> str:
    return build_static_system(config, skills) + "\n\n" + SKILL_TOOL_ADAPTER


def default_session(session_id: str, user_id: str) -> ShoppingSessionContext:
    """A guest session under the host timezone (``HOST_TIMEZONE``, else Europe/Berlin);
    the clock is a zone, not a fixed instant, so it stays current over a long console."""
    return ShoppingSessionContext(
        session_id=session_id, user_id=user_id, timezone=default_timezone()
    )


def cli_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``ClaudeAgentOptions.env``: the CLAUDE.md switch plus, for an identity-linked key,
    the ``anthropic-workspace-id`` header the API requires, carried the way the CLI takes
    extra headers (``ANTHROPIC_CUSTOM_HEADERS``). Platform selection stays the CLI's
    environment (``docs/deployment.md`` upstream)."""
    return {**CLI_BASE_ENV, **sdk_workspace_env(environ)}


def make_options(
    *,
    backend: StorefrontBackend | None = None,
    config: ShoppingAgentConfig | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    user_id: str = DEFAULT_USER_ID,
    max_turns: int = DEFAULT_MAX_TURNS,
    skills_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ClaudeAgentOptions, ShoppingToolset]:
    """The options for one conversation and the toolset behind them; the toolset is
    where the host reads provenance, session state, and the UI payloads. ``backend``
    defaults to the live Shopware shop named by the environment; ``skills_dir`` to the
    vendored ``vendor/skills/shopping``; ``environ`` (tests) to the process environment."""
    config = config or default_config()
    skills_root = SKILLS_DIR if skills_dir is None else skills_dir
    skills = load_skill_registry(skills_root)
    toolset = ShoppingToolset(
        backend=backend if backend is not None else load_shopware_backend(),
        config=config,
        session=default_session(session_id, user_id),
    )
    ensure_project_skills(skills_root, RUNTIME_ROOT)
    options = ClaudeAgentOptions(
        system_prompt=build_system_prompt(config, skills),
        mcp_servers={SERVER_NAME: build_shopping_server(toolset)},
        allowed_tools=allowed_tool_names(config),
        tools=["Skill"],
        skills=skills.names,
        setting_sources=["project"],
        cwd=RUNTIME_ROOT,
        env=cli_environment(environ),
        model=config.model,
        max_turns=max_turns,
        permission_mode="dontAsk",
        hooks=close_on_presentation_hook(toolset, config.close_on_presentation),
    )
    return options, toolset


async def ground_message(text: str, toolset: ShoppingToolset) -> str:
    """The customer's message with the reads the grounding rules call for appended."""
    return await ground(text, GROUNDING_RULES, toolset.config, toolset.state, toolset.executor)


async def run_turn(
    client: ClaudeSDKClient, text: str, *, toolset: ShoppingToolset | None = None
) -> TurnResult:
    """Send one message and collect the reply. With the ``toolset`` from
    :func:`make_options` the message is grounded first and the result carries the
    turn's UI payloads."""
    if toolset is not None:
        toolset.begin_turn()
        text = await ground_message(text, toolset)
    await client.query(text)
    result = await collect_turn(client)
    result.ui = toolset.drain_ui_events() if toolset is not None else []
    return result
