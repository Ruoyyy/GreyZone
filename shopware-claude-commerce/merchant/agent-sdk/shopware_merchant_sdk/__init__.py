# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Shopware merchant agent on the Claude Agent SDK::

from claude_agent_sdk import ClaudeSDKClient
from shopware_merchant_sdk import make_options, run_turn

options, toolset = make_options()          # the live shop's Admin MCP named by .env
async with ClaudeSDKClient(options=options) as client:
    result = await run_turn(client, "What needs my attention this morning?", toolset=toolset)
    print(result.text)
    for change in toolset.pending_host_approvals():   # staged, no approval mark yet
        ...
"""

from ._paths import DEFAULT_LEDGER_DSN, LEDGER_DSN_ENV, REPO_ROOT, RUNTIME_ROOT, SKILLS_DIR
from .agent import (
    ANALYSIS_AGENT_ADAPTER,
    ANALYSIS_AGENT_NAME,
    CLI_BASE_ENV,
    CONSOLE_APPROVAL_SURFACE,
    build_analysis_agent,
    build_system_prompt,
    cli_environment,
    default_config,
    default_session,
    ground_message,
    load_skill_registry,
    make_options,
    run_turn,
)
from .merchant_tools import (
    SERVER_NAME,
    MerchantToolset,
    allowed_tool_names,
    build_merchant_sdk_tools,
    build_merchant_server,
    load_shopware_backend,
    load_shopware_settings,
    mcp_tool_name,
    store_display_name,
    tool_contracts,
    tool_names,
)

__all__ = [
    "ANALYSIS_AGENT_ADAPTER",
    "ANALYSIS_AGENT_NAME",
    "CLI_BASE_ENV",
    "CONSOLE_APPROVAL_SURFACE",
    "DEFAULT_LEDGER_DSN",
    "LEDGER_DSN_ENV",
    "REPO_ROOT",
    "RUNTIME_ROOT",
    "SERVER_NAME",
    "SKILLS_DIR",
    "MerchantToolset",
    "allowed_tool_names",
    "build_analysis_agent",
    "build_merchant_sdk_tools",
    "build_merchant_server",
    "build_system_prompt",
    "cli_environment",
    "default_config",
    "default_session",
    "ground_message",
    "load_shopware_backend",
    "load_shopware_settings",
    "load_skill_registry",
    "make_options",
    "mcp_tool_name",
    "run_turn",
    "store_display_name",
    "tool_contracts",
    "tool_names",
]
