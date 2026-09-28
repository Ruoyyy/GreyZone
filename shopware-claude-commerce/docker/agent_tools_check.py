#!/usr/bin/env python3
"""Live check of the ``SwagCommerceAgentTools`` MCP tools (docker/verify.sh §7).

    python docker/agent_tools_check.py --shop-url http://localhost:8080 \
        --generated-env docker/.generated.env

Store API server (``/store-api/_mcp``, sales-channel access key, no allowlist): ``tools/list``
must advertise ``shopping-policy-search``, ``shopping-disclosure`` and
``shopping-fulfillment-options``; each is called once against the seeded catalog.

Admin server (``/api/_mcp``, the merchant integration's ``client_credentials`` token):
``tools/list`` must advertise ``agent-change-stage/list/apply/discard``,
``agent-business-snapshot`` and ``agent-metrics-series``; every *read* tool is called once
(``agent-change-list``, ``agent-business-snapshot``, ``agent-metrics-series``). Nothing is
staged or written. On 6.7.13 ``McpToolGroup`` is inert, so all tools are listed directly.

Exit 1 on the first failed expectation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from _bootstrap_lib import HTTP_TIMEOUT_SECONDS, AdminApi, AdminApiError, read_env_file

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shopware_common.mcp_client import McpClient, McpError  # noqa: E402

SHOPPING_TOOLS: tuple[str, ...] = (
    "shopping-policy-search",
    "shopping-disclosure",
    "shopping-fulfillment-options",
)
MERCHANT_TOOLS: tuple[str, ...] = (
    "agent-change-stage",
    "agent-change-list",
    "agent-change-apply",
    "agent-change-discard",
    "agent-business-snapshot",
    "agent-metrics-series",
)
MERCHANT_READ_TOOLS: tuple[str, ...] = (
    "agent-change-list",
    "agent-business-snapshot",
    "agent-metrics-series",
)
POLICY_QUERY = "Widerruf"
SEEDED_OIL = "CA-OIL"
SEEDED_SHIRT_S = "CA-TSHIRT-S"
ENV_ACCESS_KEY = "SHOPWARE_INTEGRATION_ACCESS_KEY"
ENV_SECRET_KEY = "SHOPWARE_INTEGRATION_SECRET_KEY"
ENV_SALES_CHANNEL_KEY = "SHOPWARE_SALES_CHANNEL_ACCESS_KEY"


class CheckFailed(RuntimeError):
    pass


def ok(message: str) -> None:
    print(f"  ok  {message}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)
    ok(message)


def store_api_product_ids(shop_url: str, access_key: str) -> dict[str, str]:
    """``productNumber -> id`` for the seeded products through the Store API."""
    body = json.dumps(
        {
            "limit": 50,
            "filter": [{"type": "prefix", "field": "productNumber", "value": "CA-"}],
            "includes": {"product": ["id", "productNumber"]},
        }
    ).encode()
    request = urllib.request.Request(
        f"{shop_url}/store-api/product",
        data=body,
        method="POST",
        headers={
            "sw-access-key": access_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read())
    return {str(row["productNumber"]): str(row["id"]) for row in payload.get("elements") or []}


def _payload(result: Any, tool: str) -> dict[str, Any]:
    payload = result.json()
    if result.is_error or not isinstance(payload, dict) or not payload.get("success"):
        raise CheckFailed(f"{tool} failed: {result.text()[:300]}")
    return payload


async def check_store_api(shop_url: str, access_key: str) -> None:
    print("Store API MCP (/store-api/_mcp)")
    products = store_api_product_ids(shop_url, access_key)
    for number in (SEEDED_OIL, SEEDED_SHIRT_S):
        if number not in products:
            raise CheckFailed(f"seeded product {number} not visible in the sales channel")
    async with McpClient(
        f"{shop_url}/store-api/_mcp",
        headers={"sw-access-key": access_key},
        client_name="commerce-agents-verify",
    ) as mcp:
        names = await mcp.tool_names()
        missing = sorted(set(SHOPPING_TOOLS) - names)
        expect(not missing, f"tools/list advertises {', '.join(SHOPPING_TOOLS)}")

        search = _payload(
            await mcp.call_tool(
                "shopping-policy-search",
                {"query": POLICY_QUERY, "limit": 3},
                raise_on_tool_error=False,
            ),
            "shopping-policy-search",
        )
        rows = search.get("data") or []
        first_title = rows[0].get("title") if rows else None
        expect(
            bool(rows) and all("title" in row and "content" in row for row in rows),
            f"shopping-policy-search {POLICY_QUERY!r}: {len(rows)} page(s), first {first_title!r}",
        )

        disclosure = _payload(
            await mcp.call_tool(
                "shopping-disclosure",
                {"productId": products[SEEDED_OIL]},
                raise_on_tool_error=False,
            ),
            "shopping-disclosure",
        )
        keys = [row.get("key") for row in (disclosure.get("data") or {}).get("rows") or []]
        expect(
            {"price", "base_price", "tax", "shipping"} <= set(keys),
            f"shopping-disclosure {SEEDED_OIL}: rows {', '.join(map(str, keys))}",
        )

        fulfillment = _payload(
            await mcp.call_tool(
                "shopping-fulfillment-options",
                {"productIds": json.dumps([products[SEEDED_OIL], products[SEEDED_SHIRT_S]])},
                raise_on_tool_error=False,
            ),
            "shopping-fulfillment-options",
        )
        options = (fulfillment.get("data") or {}).get("options") or []
        summary = ", ".join(
            f"{option.get('name')} {((option.get('fee') or {}).get('amount'))}"
            for option in options
        )
        priced = all((option.get("fee") or {}).get("amount") is not None for option in options)
        expect(
            bool(options) and priced,
            f"shopping-fulfillment-options: {len(options)} method(s) with fees ({summary})",
        )


async def check_admin_api(api: AdminApi, access_key: str, secret_key: str) -> None:
    print("Admin MCP (/api/_mcp, integration token)")
    token = api.client_credentials(access_key, secret_key)
    async with McpClient(
        f"{api.shop_url}/api/_mcp",
        headers={"Authorization": f"Bearer {token}"},
        client_name="commerce-agents-verify",
    ) as mcp:
        names = await mcp.tool_names()
        missing = sorted(set(MERCHANT_TOOLS) - names)
        expect(not missing, f"tools/list advertises {', '.join(MERCHANT_TOOLS)}")

        listed = _payload(
            await mcp.call_tool(
                "agent-change-list",
                {"status": "all", "limit": 5, "page": 1},
                raise_on_tool_error=False,
            ),
            "agent-change-list",
        )
        meta = listed.get("_meta") or {}
        expect(
            isinstance(listed.get("data"), list),
            f"agent-change-list all: {meta.get('total', len(listed.get('data') or []))} change(s) in the ledger",
        )

        snapshot = _payload(
            await mcp.call_tool(
                "agent-business-snapshot", {"period": "30d"}, raise_on_tool_error=False
            ),
            "agent-business-snapshot",
        )
        metrics = (snapshot.get("data") or {}).get("metrics") or {}
        sales = (metrics.get("sales") or {}).get("current")
        orders = (metrics.get("orders") or {}).get("current")
        expect(
            sales is not None and orders is not None,
            f"agent-business-snapshot 30d: sales {sales} orders {orders}",
        )

        series = _payload(
            await mcp.call_tool(
                "agent-metrics-series",
                {"metric": "sales", "period": "7d", "granularity": "day"},
                raise_on_tool_error=False,
            ),
            "agent-metrics-series",
        )
        points = (series.get("data") or {}).get("series")
        expect(
            isinstance(points, list),
            f"agent-metrics-series sales 7d: {len(points or [])} bucket(s)",
        )
        ok(f"read tools exercised: {', '.join(MERCHANT_READ_TOOLS)}; nothing staged or written")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--generated-env", type=Path, required=True)
    args = parser.parse_args()

    known = read_env_file(args.generated_env)
    shop_url = args.shop_url.rstrip("/")
    sales_channel_key = known.get(ENV_SALES_CHANNEL_KEY, "")
    access_key, secret_key = known.get(ENV_ACCESS_KEY, ""), known.get(ENV_SECRET_KEY, "")
    if not sales_channel_key or not access_key or not secret_key:
        print(
            f"{args.generated_env} lacks {ENV_SALES_CHANNEL_KEY} or {ENV_ACCESS_KEY}/{ENV_SECRET_KEY}",
            file=sys.stderr,
        )
        return 1
    try:
        asyncio.run(check_store_api(shop_url, sales_channel_key))
        asyncio.run(check_admin_api(AdminApi(shop_url), access_key, secret_key))
    except (CheckFailed, McpError, AdminApiError, OSError) as error:
        print(f"agent tools check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
