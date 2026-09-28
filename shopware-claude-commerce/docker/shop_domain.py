#!/usr/bin/env python3
"""Point the Storefront sales-channel domain(s) at the published host port (Admin API).

    python docker/shop_domain.py --shop-url http://localhost:8080

Dockware ships ``http://localhost`` (port 80); the stack publishes ``8080:80``, and the
storefront answers 400 until a domain matches. ``bin/console sales-channel:update:domain``
only swaps the *host* and keeps the port, so it cannot add ``:8080`` — hence the Admin API
``PATCH /api/sales-channel-domain/{id}``. Idempotent: rewrites only domains whose URL
differs. ``APP_URL`` in the container's ``.env`` is handled by bootstrap.sh (no console
command edits .env).
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlsplit

from _bootstrap_lib import AdminApi, AdminApiError

LOOPBACK_HOSTS = ("localhost", "127.0.0.1")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    args = parser.parse_args()
    shop_url = args.shop_url.rstrip("/")

    api = AdminApi(shop_url)
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
        domains = channel.get("domains") or []
        changed = 0
        for domain in domains:
            url = str(domain.get("url") or "")
            host = urlsplit(url).hostname or ""
            if url == shop_url or host not in LOOPBACK_HOSTS:
                continue
            api.request("PATCH", f"/api/sales-channel-domain/{domain['id']}", {"url": shop_url})
            changed += 1
            print(f"sales channel domain: {url} -> {shop_url}")
        if not domains:
            print("sales channel domain: none found on the Storefront channel", file=sys.stderr)
            return 1
        if not changed:
            print(f"sales channel domain: already {shop_url}")
    except AdminApiError as error:
        print(f"shop_domain failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
