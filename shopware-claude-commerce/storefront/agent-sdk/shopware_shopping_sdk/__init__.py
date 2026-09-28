# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Shopware shopping agent on the Claude Agent SDK::

from claude_agent_sdk import ClaudeSDKClient
from shopware_shopping_sdk import make_options, run_turn

options, toolset = make_options()          # the live shop named by .env
async with ClaudeSDKClient(options=options) as client:
    result = await run_turn(client, "a t-shirt in size M under 40 euros", toolset=toolset)
    print(result.text)
    for event in result.ui:
        print(event["component"], event["payload"])
"""

from ._paths import REPO_ROOT, RUNTIME_ROOT, SKILLS_DIR
from .agent import (
    CLI_BASE_ENV,
    build_system_prompt,
    cli_environment,
    default_config,
    default_session,
    ground_message,
    load_skill_registry,
    make_options,
    run_turn,
)
from .shopping_tools import (
    SERVER_NAME,
    STORE_NAME,
    ShoppingToolset,
    allowed_tool_names,
    build_shopping_sdk_tools,
    build_shopping_server,
    load_shopware_backend,
    mcp_tool_name,
    tool_contracts,
    tool_names,
)

__all__ = [
    "CLI_BASE_ENV",
    "REPO_ROOT",
    "RUNTIME_ROOT",
    "SERVER_NAME",
    "SKILLS_DIR",
    "STORE_NAME",
    "ShoppingToolset",
    "allowed_tool_names",
    "build_shopping_sdk_tools",
    "build_shopping_server",
    "build_system_prompt",
    "cli_environment",
    "default_config",
    "default_session",
    "ground_message",
    "load_shopware_backend",
    "load_skill_registry",
    "make_options",
    "mcp_tool_name",
    "run_turn",
    "tool_contracts",
    "tool_names",
]
