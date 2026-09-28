# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The one place every runtime in this repo resolves its Anthropic identity.

The blueprint's Messages API runtimes (``ShoppingAgent``, ``MerchantAgent``) construct a
bare ``AsyncAnthropic`` unless one is passed in. Identity-linked API keys additionally
need the ``anthropic-workspace-id`` header on every request, otherwise the API answers
400 ("anthropic-workspace-id is required when authenticating with an identity-linked API
key"). ``ANTHROPIC_WORKSPACE_ID`` supplies that header; when it is unset the client is
exactly what the runtime would have built itself.

The Agent SDK runtimes construct no HTTP client: ``claude-agent-sdk`` starts the Claude
Code CLI, which reads its platform and credentials from the environment
(``ClaudeAgentOptions.env`` overlays the inherited process environment). The CLI has no
variable for an identity-linked key's workspace, but it does send every ``Name: Value``
pair in ``ANTHROPIC_CUSTOM_HEADERS``; :func:`sdk_workspace_env` carries the same header
that way, so both paths resolve the same identity from the same ``.env``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from anthropic import NOT_GIVEN, AsyncAnthropic, NotGiven

WORKSPACE_ID_ENV = "ANTHROPIC_WORKSPACE_ID"
WORKSPACE_ID_HEADER = "anthropic-workspace-id"
#: The Claude Code CLI's extra-headers variable: ``Name: Value`` pairs, one per line.
SDK_CUSTOM_HEADERS_ENV = "ANTHROPIC_CUSTOM_HEADERS"
_HEADER_LINE_SEPARATOR = "\n"


def workspace_headers(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Headers to attach to every API request: the workspace id when configured,
    nothing otherwise. Surrounding whitespace in the variable is ignored."""
    source = os.environ if environ is None else environ
    workspace_id = (source.get(WORKSPACE_ID_ENV) or "").strip()
    return {WORKSPACE_ID_HEADER: workspace_id} if workspace_id else {}


def build_anthropic_client(
    *, timeout: float | NotGiven = NOT_GIVEN, environ: Mapping[str, str] | None = None
) -> AsyncAnthropic:
    """An ``AsyncAnthropic`` for a runtime's ``client=`` seam. ``timeout`` should be the
    agent config's ``request_timeout_s`` so the client matches the one the runtime would
    otherwise build; credentials still come from the SDK's own chain
    (``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``)."""
    headers = workspace_headers(environ)
    return AsyncAnthropic(timeout=timeout, default_headers=headers or None)


def sdk_workspace_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The ``ClaudeAgentOptions.env`` entries that make the Claude Code CLI send the
    workspace header: ``ANTHROPIC_CUSTOM_HEADERS`` with the ``anthropic-workspace-id``
    line appended to whatever pairs the environment already carries (none twice), or
    nothing when no workspace id is configured. Credentials and platform variables stay
    the CLI's own (``ANTHROPIC_API_KEY``, ``ANTHROPIC_BASE_URL``, ``CLAUDE_CODE_USE_*``)."""
    source = os.environ if environ is None else environ
    headers = workspace_headers(source)
    if not headers:
        return {}
    existing = [
        line.strip()
        for line in (source.get(SDK_CUSTOM_HEADERS_ENV) or "").split(_HEADER_LINE_SEPARATOR)
        if line.strip()
    ]
    lines = [
        line for line in existing if line.partition(":")[0].strip().lower() != WORKSPACE_ID_HEADER
    ]
    lines.append(f"{WORKSPACE_ID_HEADER}: {headers[WORKSPACE_ID_HEADER]}")
    return {SDK_CUSTOM_HEADERS_ENV: _HEADER_LINE_SEPARATOR.join(lines)}


def model_env_fields(role: str) -> dict[str, str | None]:
    """The model overrides an agent config takes from the environment: ``{ROLE}_MODEL``
    (the turn loop's model), ``MEMORY_MODEL`` (post-turn extraction), and
    ``THINKING_EFFORT`` (``none`` sends thinking disabled). An unset variable leaves the
    config's own default. Use with ``ANTHROPIC_BASE_URL`` pointing the clients at a
    provider whose Anthropic-compatible endpoint serves other model names."""
    source = os.environ
    fields: dict[str, str | None] = {}
    if model := source.get(f"{role.upper()}_MODEL"):
        fields["model"] = model
    if memory_model := source.get("MEMORY_MODEL"):
        fields["memory_model"] = memory_model
    if (effort := source.get("THINKING_EFFORT")) is not None:
        fields["thinking_effort"] = None if effort.lower() in ("none", "") else effort
    return fields
