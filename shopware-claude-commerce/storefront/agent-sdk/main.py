# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""A console for the Shopware shopping agent on the Claude Agent SDK, over the live shop
named by ``.env`` / ``docker/.generated.env``::

    python storefront/agent-sdk/main.py [--once "a t-shirt in size M under 40 euros"]

Needs ANTHROPIC_API_KEY (plus ANTHROPIC_WORKSPACE_ID for an identity-linked key) or an
authenticated Claude Code installation, and the Docker shop from ``docker/bootstrap.sh``.
The console is the host: it prints the reply, each presentation payload as JSON, the
tools called, and the cost; for a ``checkout`` card it mints the one-time handoff code the
storefront host's ticket route would mint at click time and prints the shop's continue
link, so no checkout URL ever passes through the model. Mirrors
``shopping-agent/runtime-agent-sdk/main.py`` at the pinned blueprint commit.
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
from shopware_shopping_sdk import ShoppingToolset, make_options, run_turn
from shopware_shopping_sdk.host import (
    CHECKOUT_COMPONENT,
    DEFAULT_HANDOFF_LABEL,
    HANDOFF_TTL_SECONDS,
    prepare_backend,
    release_backend,
    render_checkout,
)

from commerce_common.agent_sdk import TurnResult

_BOLD = re.compile(r"\*\*(.+?)\*\*")
EXIT_WORDS = {"exit", "quit", "q"}
PROMPT = "you> "


def _render_prose(text: str) -> str:
    """Markdown bold as ANSI bold on a tty, stripped when piped."""
    replacement = "\033[1m\\1\033[0m" if sys.stdout.isatty() else "\\1"
    return _BOLD.sub(replacement, text)


def print_turn(result: TurnResult, toolset: ShoppingToolset | None = None) -> None:
    if result.text:
        print(f"\n{_render_prose(result.text)}\n")
    for event in result.ui:
        payload = event["payload"]
        is_checkout = toolset is not None and event["component"] == CHECKOUT_COMPONENT
        if is_checkout:
            payload = render_checkout(payload, toolset)
        print(f"--- ui:{event['component']} ---")
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        if is_checkout:
            for handoff in payload.get("handoffs") or []:
                label = handoff.get("label") or DEFAULT_HANDOFF_LABEL
                print(f"→ {label} (one-time link, valid {HANDOFF_TTL_SECONDS} s): {handoff['url']}")
        print()
    if result.tool_calls:
        print(f"[tools: {', '.join(result.tool_calls)}]")
    for error in result.tool_errors:
        print(f"[tool error: {error}]")
    if result.cost_usd is not None:
        print(f"[cost: ${result.cost_usd:.4f}]")


async def chat() -> None:
    options, toolset = make_options()
    await prepare_backend(toolset)
    print(f"{toolset.backend.store_name} shopping agent (Agent SDK path). Type 'exit' to quit.\n")
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
                print_turn(result, toolset)
    finally:
        await release_backend(toolset)


async def once(prompt: str) -> None:
    options, toolset = make_options()
    await prepare_backend(toolset)
    try:
        async with ClaudeSDKClient(options=options) as client:
            result = await run_turn(client, prompt, toolset=toolset)
            print_turn(result, toolset)
    finally:
        await release_backend(toolset)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shopware shopping agent on the Claude Agent SDK, over the live shop"
    )
    parser.add_argument("--once", metavar="QUERY", help="run a single query and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(once(args.once) if args.once else chat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
