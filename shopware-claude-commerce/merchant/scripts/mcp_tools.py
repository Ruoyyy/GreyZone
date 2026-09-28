# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Print the live Admin MCP ``tools/list`` for the current credentials.

    python merchant/scripts/mcp_tools.py            # integration keys from the environment
    python merchant/scripts/mcp_tools.py --admin    # the admin user's password grant

Use it to verify an integration's tool allowlist: the merchant backend needs exactly the
names in ``merchant.api.admin_client.MCP_TOOLS_USED``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from demo_common import load_demo_env  # noqa: E402
from merchant.api.admin_client import (  # noqa: E402
    MCP_TOOLS_USED,
    AdminAPIError,
    McpTransport,
    OAuthTokenProvider,
)


def load_env() -> None:
    load_demo_env(REPO_ROOT / "merchant")
    load_demo_env(REPO_ROOT)
    generated = REPO_ROOT / "docker" / ".generated.env"
    if generated.exists():
        for key, value in dotenv_values(generated).items():
            if value and not os.environ.get(key):
                os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", action="store_true", help="use SHOPWARE_ADMIN_USERNAME/PASSWORD")
    args = parser.parse_args()
    load_env()
    shop_url = env("SHOPWARE_ADMIN_URL") or env("SHOPWARE_URL") or "http://localhost:8080"
    http = httpx.AsyncClient(timeout=30.0)
    try:
        if args.admin:
            tokens = OAuthTokenProvider(
                shop_url,
                http=http,
                username=env("SHOPWARE_ADMIN_USERNAME"),
                password=env("SHOPWARE_ADMIN_PASSWORD"),
            )
        else:
            tokens = OAuthTokenProvider(
                shop_url,
                http=http,
                access_key=env("SHOPWARE_INTEGRATION_ACCESS_KEY"),
                secret_key=env("SHOPWARE_INTEGRATION_SECRET_KEY"),
            )
        transport = McpTransport(shop_url, headers_provider=tokens.headers, http=http)
        try:
            names = await transport.tool_names()
        finally:
            await transport.aclose()
    except AdminAPIError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    finally:
        await http.aclose()
    print(f"{shop_url} ({tokens.grant}): {len(names)} tools")
    for name in names:
        marker = "*" if name in MCP_TOOLS_USED else " "
        print(f" {marker} {name}")
    missing = sorted(set(MCP_TOOLS_USED) - set(names))
    if missing:
        print(f"\nmissing for the merchant backend: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("\n* = used by the merchant backend; all present")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
