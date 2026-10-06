#!/usr/bin/env python3
"""Third-party seller identity: ACL role + Integration ``third-party-seller`` for the
storefront host's seller MCP (``storefront/api/seller_mcp.py``), which creates products on
behalf of the one third-party seller (ruoyu). Separate from ``claude-merchant-agent``: this
integration can create products and tag them, and nothing else.

    python docker/seller_identity.py --shop-url http://localhost:8080 \
        --generated-env docker/.generated.env [--user admin --password shopware]

Idempotent like ``merchant_identity.py``: role and integration are looked up by name; the
secret is only known at creation, so a re-run without it in ``--generated-env`` rotates it.
Writes ``SELLER_INTEGRATION_ACCESS_KEY`` / ``SELLER_INTEGRATION_SECRET_KEY``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _bootstrap_lib import AdminApi, AdminApiError, new_id, read_env_file, upsert_env_file
from merchant_identity import generate_access_key, generate_secret_key

NAME = "third-party-seller"
ENV_ACCESS_KEY = "SELLER_INTEGRATION_ACCESS_KEY"
ENV_SECRET_KEY = "SELLER_INTEGRATION_SECRET_KEY"
#: The seller MCP writes through Admin REST (create only); the Admin MCP upsert tool would
#: demand ``product:update`` over every product, so no Admin MCP tool is allowed.
MCP_TOOL_ALLOWLIST: tuple[str, ...] = ()
#: Create a product with its translation, storefront visibility and seller tag; read the
#: tax, currency and sales channel it references, and the seller's own product reviews.
ACL_PRIVILEGES: tuple[str, ...] = tuple(
    sorted(
        {
            "product:read",
            "product:create",
            "product_translation:create",
            "product_visibility:create",
            "product_tag:create",
            # Finds ruoyu's own review of a product, so a second review updates it.
            "product_review:read",
            "tag:read",
            "tag:create",
            "tax:read",
            "currency:read",
            "sales_channel:read",
        }
    )
)


def ensure_role(api: AdminApi) -> str:
    role = api.search_one("acl-role", "name", NAME)
    privileges = list(ACL_PRIVILEGES)
    if role is None:
        role_id = new_id()
        api.request(
            "POST",
            "/api/acl-role",
            {
                "id": role_id,
                "name": NAME,
                "description": "Third-party seller (ruoyu): creates tagged products only.",
                "privileges": privileges,
            },
        )
        print(f"acl role {NAME}: created ({len(privileges)} privileges)")
        return role_id
    role_id = str(role["id"])
    if sorted(role.get("privileges") or []) != privileges:
        api.request("PATCH", f"/api/acl-role/{role_id}", {"privileges": privileges})
        print(f"acl role {NAME}: privileges updated ({len(privileges)})")
    return role_id


def ensure_integration(api: AdminApi, role_id: str, known: dict[str, str]) -> tuple[str, str, str]:
    rows = api.search(
        "integration",
        {"limit": 1, "filter": [{"type": "equals", "field": "label", "value": NAME}]},
    )
    if not rows:
        integration_id, access_key, secret_key = (
            new_id(),
            generate_access_key(),
            generate_secret_key(),
        )
        api.request(
            "POST",
            "/api/integration",
            {
                "id": integration_id,
                "label": NAME,
                "accessKey": access_key,
                "secretAccessKey": secret_key,
                "admin": False,
                "aclRoles": [{"id": role_id}],
            },
        )
        print(f"integration {NAME}: created ({access_key})")
        return integration_id, access_key, secret_key
    integration_id, access_key = str(rows[0]["id"]), str(rows[0]["accessKey"])
    patch: dict[str, Any] = {"admin": False, "aclRoles": [{"id": role_id}]}
    secret_key = known.get(ENV_SECRET_KEY, "")
    secret_valid = bool(secret_key and known.get(ENV_ACCESS_KEY) == access_key)
    if secret_valid:
        try:
            api.client_credentials(access_key, secret_key)
        except AdminApiError as error:
            if error.status not in {400, 401}:
                raise
            secret_valid = False
    if not secret_valid:
        secret_key = generate_secret_key()
        patch["secretAccessKey"] = secret_key
        print(f"integration {NAME}: exists ({access_key}); secret rotated")
    api.request("PATCH", f"/api/integration/{integration_id}", patch)
    return integration_id, access_key, secret_key


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--generated-env", type=Path, required=True)
    args = parser.parse_args()

    api = AdminApi(args.shop_url)
    try:
        api.login(args.user, args.password)
        role_id = ensure_role(api)
        integration_id, access_key, secret_key = ensure_integration(
            api, role_id, read_env_file(args.generated_env)
        )
        api.request(
            "POST",
            f"/api/_action/integration/{integration_id}/mcp-allowlist",
            {"allowlist": {"tools": list(MCP_TOOL_ALLOWLIST), "resources": [], "prompts": []}},
        )
        upsert_env_file(
            args.generated_env, {ENV_ACCESS_KEY: access_key, ENV_SECRET_KEY: secret_key}
        )
    except (AdminApiError, RuntimeError, OSError) as error:
        print(f"seller identity failed: {error}", file=sys.stderr)
        return 1
    print(f"integration {NAME}: {len(ACL_PRIVILEGES)} privileges, tools {list(MCP_TOOL_ALLOWLIST)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
