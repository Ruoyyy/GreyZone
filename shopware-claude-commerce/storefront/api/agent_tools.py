# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The shop's own agent tools: ``SwagCommerceAgentTools`` on the Store API MCP server.

The plugin (``shopware-plugins/SwagCommerceAgentTools``) moves three of this host's
capabilities into Shopware as MCP tools on ``POST /store-api/_mcp``:

    shopping-policy-search       {query, limit}   → search_policies
    shopping-disclosure          {productId}      → get_disclosure
    shopping-fulfillment-options {productIds}     → get_fulfillment_options

``SHOPWARE_AGENT_TOOLS`` picks the path: ``plugin`` calls the tools, ``host`` keeps the
implementations in ``policies.py`` / ``disclosures.py`` / ``store_api.py``. Unset (``auto``)
means *plugin when the shop advertises the three tools* — decided once at startup with
``tools/list`` (:meth:`ShoppingAgentTools.detect`) — else ``host``. Every plugin call that
fails at the transport or tool level falls back to the host implementation for that call,
so a shop without the plugin (or with it deactivated mid-session) keeps answering.

Authentication is the Store API's: ``sw-access-key`` on every request, plus the session's
``sw-context-token`` (the UCP cart id) on ``tools/call`` so the tools run in the shopper's
context — the fulfillment fee is then the cart's exact delivery cost (``fee.estimated``
false) rather than the price-matrix estimate. The Store API MCP server has no allowlist; the
sales-channel key is the boundary. On 6.7.13 the plugin's ``McpToolGroup`` attribute is
inert, so the tools are listed directly (no ``toolset-enable`` step).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from shopware_common.mcp_client import McpClient, McpError, ToolResult

logger = logging.getLogger(__name__)

AGENT_TOOLS_ENV = "SHOPWARE_AGENT_TOOLS"
MODE_AUTO = "auto"
MODE_PLUGIN = "plugin"
MODE_HOST = "host"
MODES = (MODE_AUTO, MODE_PLUGIN, MODE_HOST)
STORE_API_MCP_PATH = "/store-api/_mcp"
ACCESS_KEY_HEADER = "sw-access-key"
CONTEXT_TOKEN_HEADER = "sw-context-token"

TOOL_POLICY_SEARCH = "shopping-policy-search"
TOOL_DISCLOSURE = "shopping-disclosure"
TOOL_FULFILLMENT = "shopping-fulfillment-options"
SHOPPING_TOOLS: tuple[str, ...] = (TOOL_POLICY_SEARCH, TOOL_DISCLOSURE, TOOL_FULFILLMENT)
#: The plugin caps ``limit`` at its ``policySearchMaxResults`` config (default 5).
DEFAULT_POLICY_LIMIT = 5
_TIMEOUT = httpx.Timeout(20.0)


class AgentToolsError(RuntimeError):
    """A plugin tool could not be called or answered ``success: false`` / ``isError``."""


def mode_from_env() -> str:
    value = (os.environ.get(AGENT_TOOLS_ENV) or MODE_AUTO).strip().lower()
    if value not in MODES:
        logger.warning("%s=%r is not one of %s; using %s", AGENT_TOOLS_ENV, value, MODES, MODE_AUTO)
        return MODE_AUTO
    return value


def resolve_mode(requested: str, advertised: set[str]) -> str:
    """The effective path for a requested mode and the tools ``tools/list`` advertised.

    ``host`` is final. ``plugin`` is honoured even when the tools are missing (the operator
    asked for it; every call then fails over to the host path with a warning). ``auto``
    becomes ``plugin`` only when all three tools are advertised.
    """
    if requested == MODE_HOST:
        return MODE_HOST
    if requested == MODE_PLUGIN:
        return MODE_PLUGIN
    return MODE_PLUGIN if set(SHOPPING_TOOLS) <= advertised else MODE_HOST


class ShoppingAgentTools:
    def __init__(
        self,
        shop_url: str,
        access_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        mode: str | None = None,
    ) -> None:
        self.shop_url = shop_url.rstrip("/")
        self.access_key = access_key
        self.requested_mode = mode or mode_from_env()
        self.mode: str | None = None  # effective mode, set by detect()
        self.advertised: set[str] = set()
        self.calls: list[str] = []  # tool names called, for smoke and tests
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)
        self.mcp = McpClient(
            f"{self.shop_url}{STORE_API_MCP_PATH}",
            http=self._http,
            client_name="commerce-agents-storefront",
            headers={ACCESS_KEY_HEADER: access_key},
        )

    # ------------------------------------------------------------------ lifecycle

    @property
    def active(self) -> bool:
        """True when the plugin path is in effect (after :meth:`detect`)."""
        return self.mode == MODE_PLUGIN

    @property
    def description(self) -> str:
        if self.mode is None:
            return f"{self.requested_mode} (not detected yet)"
        if self.mode == self.requested_mode:
            return self.mode
        return f"{self.mode} (requested {self.requested_mode})"

    async def detect(self) -> str:
        """Decide the effective mode from ``tools/list`` on the Store API MCP server."""
        if self.requested_mode == MODE_HOST:
            self.mode = MODE_HOST
            return self.mode
        if not self.access_key:
            # The Store API MCP server authenticates with the sales-channel key; without it
            # the plugin cannot be reached, whatever the flag says.
            logger.warning("SHOPWARE_SALES_CHANNEL_ACCESS_KEY is not set; agent tools: host path")
            self.mode = MODE_HOST
            return self.mode
        try:
            self.advertised = await self.mcp.tool_names(force=True)
        except (McpError, httpx.HTTPError) as error:
            logger.warning("Store API MCP tools/list failed (%s); agent tools: host path", error)
            self.advertised = set()
        self.mode = resolve_mode(self.requested_mode, self.advertised)
        missing = sorted(set(SHOPPING_TOOLS) - self.advertised)
        if self.mode == MODE_PLUGIN and missing:
            logger.error(
                "%s=plugin but the shop does not advertise %s; calls fall back to the host path",
                AGENT_TOOLS_ENV,
                ", ".join(missing),
            )
        logger.info(
            "agent tools: %s (%s advertises %d shopping-* tool(s))",
            self.description,
            STORE_API_MCP_PATH,
            len(set(SHOPPING_TOOLS) & self.advertised),
        )
        return self.mode

    async def aclose(self) -> None:
        await self.mcp.close()
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ tools

    async def policy_search(
        self, query: str, limit: int = DEFAULT_POLICY_LIMIT, *, context_token: str | None = None
    ) -> list[dict[str, Any]]:
        payload = await self._call(
            TOOL_POLICY_SEARCH, {"query": query, "limit": int(limit)}, context_token
        )
        rows = payload.get("data")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def disclosure(
        self, product_id: str, *, context_token: str | None = None
    ) -> dict[str, Any]:
        """``{"productId", "rows": [...]}`` plus ``_meta`` (locale, currency, taxState)."""
        payload = await self._call(TOOL_DISCLOSURE, {"productId": product_id}, context_token)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AgentToolsError(f"{TOOL_DISCLOSURE}: no data in the tool response")
        return {**data, "_meta": payload.get("_meta") or {}}

    async def fulfillment_options(
        self, product_ids: list[str], *, context_token: str | None = None
    ) -> dict[str, Any]:
        """``{"options": [...], "products": [...]}`` plus ``_meta``."""
        # The tool takes a JSON array or a comma-separated list; JSON like the UCP tools.
        payload = await self._call(
            TOOL_FULFILLMENT, {"productIds": json.dumps(list(product_ids))}, context_token
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AgentToolsError(f"{TOOL_FULFILLMENT}: no data in the tool response")
        return {**data, "_meta": payload.get("_meta") or {}}

    async def _call(
        self, tool: str, arguments: dict[str, Any], context_token: str | None
    ) -> dict[str, Any]:
        if not self.active:
            raise AgentToolsError(f"{tool}: the plugin path is not active ({self.description})")
        extra = {CONTEXT_TOKEN_HEADER: context_token} if context_token else None
        self.calls.append(tool)
        try:
            result = await self.mcp.call_tool(
                tool, arguments, extra_headers=extra, raise_on_tool_error=False
            )
        except McpError as error:
            raise AgentToolsError(f"{tool}: {error}") from error
        except httpx.HTTPError as error:
            raise AgentToolsError(f"{tool}: transport error: {error}") from error
        return _unwrap(tool, result)


def _unwrap(tool: str, result: ToolResult) -> dict[str, Any]:
    payload = result.json()
    if result.is_error:
        raise AgentToolsError(f"{tool}: {result.text()[:300] or 'tool error'}")
    if not isinstance(payload, dict):
        raise AgentToolsError(f"{tool}: unreadable tool response: {result.text()[:200]}")
    if not payload.get("success"):
        raise AgentToolsError(f"{tool}: {payload.get('error') or 'tool reported failure'}")
    return payload
