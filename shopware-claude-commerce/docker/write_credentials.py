#!/usr/bin/env python3
"""Write the host-side connection values into ``docker/.generated.env`` (Admin API, no SQL).

Keys owned here: ``SHOPWARE_URL``, ``SHOPWARE_ADMIN_URL``, ``SHOPWARE_SALES_CHANNEL_ID``,
``SHOPWARE_SALES_CHANNEL_ACCESS_KEY``, ``UCP_AGENT_PROFILE_URL``, ``UCP_TRANSPORT``,
``SHOPWARE_ADMIN_TRANSPORT``, plus ``UCP_AGENT_SIGNING_KEY_PEM_FILE`` and
``COMMERCE_AGENTS_HANDOFF_SECRET`` when passed. ``SHOPWARE_INTEGRATION_*`` is written by
``merchant_identity.py``; existing keys are kept (upsert). The admin password never lands in
this file — hosts authenticate with the integration only (ADR-14).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _bootstrap_lib import AdminApi, AdminApiError, upsert_env_file

HOST_ONLY_KEYS_REMOVED = ("SHOPWARE_ADMIN_USERNAME", "SHOPWARE_ADMIN_PASSWORD")
HANDOFF_SECRET_ENV = "COMMERCE_AGENTS_HANDOFF_SECRET"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--profile-url", default="http://localhost/agent-profile.json")
    parser.add_argument(
        "--signing-key-pem-file", default="", help="repo-relative path of the agent key"
    )
    parser.add_argument(
        "--handoff-secret-from-env",
        action="store_true",
        help=f"copy {HANDOFF_SECRET_ENV} from this process' environment (bootstrap exports it)",
    )
    args = parser.parse_args()
    shop_url = args.shop_url.rstrip("/")

    api = AdminApi(shop_url)
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
    except AdminApiError as error:
        print(f"write_credentials failed: {error}", file=sys.stderr)
        return 1

    values = {
        "SHOPWARE_URL": shop_url,
        "SHOPWARE_ADMIN_URL": shop_url,
        "SHOPWARE_SALES_CHANNEL_ID": str(channel["id"]),
        "SHOPWARE_SALES_CHANNEL_ACCESS_KEY": str(channel["accessKey"]),
        "UCP_AGENT_PROFILE_URL": args.profile_url,
        "UCP_TRANSPORT": "mcp",
        "SHOPWARE_ADMIN_TRANSPORT": "mcp",
    }
    if args.signing_key_pem_file:
        values["UCP_AGENT_SIGNING_KEY_PEM_FILE"] = args.signing_key_pem_file
    if args.handoff_secret_from_env:
        secret = os.environ.get(HANDOFF_SECRET_ENV, "").strip()
        if not secret:
            print(
                f"write_credentials: {HANDOFF_SECRET_ENV} not set in the environment",
                file=sys.stderr,
            )
            return 1
        values[HANDOFF_SECRET_ENV] = secret
    upsert_env_file(args.out, values, remove=HOST_ONLY_KEYS_REMOVED)
    print(f"Wrote {args.out} ({', '.join(values)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
