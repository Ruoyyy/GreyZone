# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""A console for the Shopware merchant agent on the Claude Agent SDK, over the live
shop's Admin MCP named by ``.env`` / ``docker/.generated.env``::

    python merchant/agent-sdk/main.py [--once "what needs my attention this morning"]

Needs ANTHROPIC_API_KEY (plus ANTHROPIC_WORKSPACE_ID for an identity-linked key) or an
authenticated Claude Code installation, and the integration credentials
``docker/bootstrap.sh`` writes (or ``SHOPWARE_LOCAL_STORE=1`` for the in-process fake).
In interactive mode the console is the approval host: it asks y/N for each change a turn
stages, and apply_change succeeds only for changes approved here — the host approval
mark of the blueprint, set by the console instead of the portal's apply route
(``--no-host-approval`` accepts approval in chat instead). ``--once`` has no prompt to
ask on, so its staged changes stay staged in this runtime's ledger for a later session.
Mirrors ``merchant-agent/runtime-agent-sdk/main.py`` at the pinned blueprint commit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys

from claude_agent_sdk import ClaudeSDKClient
from shopware_merchant_sdk import (
    CONSOLE_APPROVAL_SURFACE,
    MerchantToolset,
    default_config,
    load_shopware_settings,
    make_options,
    run_turn,
)
from shopware_merchant_sdk.host import prepare_backend, release_backend

from commerce_common.agent_sdk import TurnResult
from merchant.api.admin_client import AdminAPIError
from merchant.api.agent_config import MissingCredentials, ShopwareSettings
from merchant_agent import MerchantAgentConfig

_BOLD = re.compile(r"\*\*(.+?)\*\*")
EXIT_WORDS = {"exit", "quit", "q"}
PROMPT = "operator> "
APPROVAL_PROMPT = "approve? [y/N] "
YES_WORDS = {"y", "yes"}


def _render_prose(text: str) -> str:
    """Markdown bold as ANSI bold on a tty, stripped when piped."""
    replacement = "\033[1m\\1\033[0m" if sys.stdout.isatty() else "\\1"
    return _BOLD.sub(replacement, text)


def print_turn(result: TurnResult) -> None:
    if result.text:
        print(f"\n{_render_prose(result.text)}\n")
    for event in result.ui:
        print(f"--- ui:{event['component']} ---")
        print(json.dumps(event["payload"], indent=2, default=str, ensure_ascii=False))
        print()
    if result.tool_calls:
        print(f"[tools: {', '.join(result.tool_calls)}]")
    for error in result.tool_errors:
        print(f"[tool error: {error}]")
    if result.cost_usd is not None:
        print(f"[cost: ${result.cost_usd:.4f}]")


def apply_attempts(result: TurnResult) -> set[str]:
    """The change ids the model tried to apply this turn."""
    return {
        str(arguments.get("change_id"))
        for name, arguments in zip(result.tool_calls, result.tool_inputs, strict=True)
        if name.endswith("apply_change") and arguments.get("change_id")
    }


async def confirm_staged_changes(
    client: ClaudeSDKClient, toolset: MerchantToolset, declined: set[str]
) -> None:
    """Ask y/N for each newly staged change; a yes marks the change, asks the agent to
    apply it, and clears the mark when that turn returns; a no is remembered so the change
    is not offered again."""
    for change in toolset.pending_host_approvals():
        if change.change_id in declined:
            continue
        print(f"\n{change.change_id} — {change.summary}")
        for item in change.items:
            print(f"  {item.target} {item.field}: {item.before!r} → {item.after!r}")
        for note in change.guardrail_notes:
            print(f"  note: {note}")
        answer = (await asyncio.to_thread(input, APPROVAL_PROMPT)).strip().lower()
        if answer in YES_WORDS:
            toolset.host_approve(change.change_id)
            try:
                result = await run_turn(
                    client,
                    f"Approved {change.change_id} through the console — apply it now.",
                    toolset=toolset,
                )
            finally:
                toolset.host_clear(change.change_id)
            print_turn(result)
        else:
            declined.add(change.change_id)
            print(f"{change.change_id} stays staged (dismiss it in chat to drop it).")


def console_config(host_approval: bool) -> tuple[ShopwareSettings, MerchantAgentConfig]:
    """The host's config with this console as the approval surface: with host approval on,
    apply_change needs the y/N mark; off, an approval typed in chat applies the change."""
    settings = load_shopware_settings()
    config = default_config(settings).model_copy(update={"require_host_approval": host_approval})
    assert config.approval_surface == CONSOLE_APPROVAL_SURFACE
    return settings, config


async def chat(host_approval: bool) -> None:
    settings, config = console_config(host_approval)
    options, toolset = make_options(config=config, settings=settings)
    await prepare_backend(toolset)
    declined: set[str] = set()
    mode = "console approval" if host_approval else "chat approval"
    print(
        f"{toolset.backend.store_name} merchant agent (Agent SDK path, {mode}). "
        f"Operator {toolset.session.operator}. Type 'exit' to quit.\n"
    )
    try:
        async with ClaudeSDKClient(options=options) as client:
            while True:
                try:
                    text = (await asyncio.to_thread(input, PROMPT)).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not text:
                    continue
                if text.lower() in EXIT_WORDS:
                    break
                result = await run_turn(client, text, toolset=toolset)
                print_turn(result)
                if host_approval:
                    # An apply attempt on a declined change reopens the question.
                    declined.difference_update(apply_attempts(result))
                    await confirm_staged_changes(client, toolset, declined)
    finally:
        await release_backend(toolset)


async def once(prompt: str) -> None:
    settings, config = console_config(host_approval=True)
    options, toolset = make_options(config=config, settings=settings)
    await prepare_backend(toolset)
    try:
        async with ClaudeSDKClient(options=options) as client:
            result = await run_turn(client, prompt, toolset=toolset)
            print_turn(result)
        for change in toolset.pending_host_approvals():
            print(f"[staged, awaiting approval in an interactive session: {change.change_id}]")
    finally:
        await release_backend(toolset)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shopware merchant agent on the Claude Agent SDK, over the live shop"
    )
    parser.add_argument("--once", metavar="QUERY", help="run a single query and exit")
    parser.add_argument(
        "--no-host-approval",
        action="store_true",
        help="let a chat-text approval apply changes instead of the console's y/N prompt",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(once(args.once) if args.once else chat(not args.no_host_approval))
    except (MissingCredentials, AdminAPIError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
