"""Optional StockTwits social feed tool.

Enabled with ALPACA_SOCIAL_FEED=true. Reads a StockTwits-shaped message
stream from the market data host. Registered like the ReadMe docs tools:
outside OpenAPI generation, so it does not participate in toolset filtering
or spec integrity checks. Output is third-party free text, so the tool name
is classified external_text in tool_registry.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastmcp import FastMCP

SOCIAL_FEED_TOOL = "get_stocktwits_feed"
SOCIAL_TOOL_NAMES = frozenset({SOCIAL_FEED_TOOL})
SOCIAL_FEED_ENV = "ALPACA_SOCIAL_FEED"


def social_feed_enabled() -> bool:
    return os.environ.get(SOCIAL_FEED_ENV, "").strip().lower() in ("1", "true", "yes")


def register_social_tools(server: FastMCP, client: httpx.AsyncClient) -> None:
    @server.tool(
        annotations={
            "title": "StockTwits Feed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def get_stocktwits_feed(
        symbols: Optional[str] = None,
        limit: int = 30,
    ) -> dict:
        """Retrieves recent StockTwits messages for stocks and crypto.

        Args:
            symbols: Optional comma-separated symbol filter (e.g. "NVRX,SPY,BTC/USD").
                Omit to return the general stream.
            limit: Maximum number of messages to return (default 30).

        Returns:
            The StockTwits message stream: each message includes the post body,
            author, timestamp, mentioned cashtags, and the author's
            Bullish/Bearish sentiment label when set.
        """
        params: dict = {"limit": limit}
        if symbols:
            params["symbols"] = symbols
        try:
            resp = await client.get("/api/2/streams/feed", params=params)
        except httpx.HTTPError as exc:
            return {"error": {"message": f"StockTwits feed request failed: {exc}"}}
        if resp.is_error:
            return {"error": {"message": f"StockTwits feed returned HTTP {resp.status_code}"}}
        return resp.json()


def maybe_register_social_tools(
    server: FastMCP,
    data_client: httpx.AsyncClient | None,
) -> None:
    if data_client is not None and social_feed_enabled():
        register_social_tools(server, data_client)
