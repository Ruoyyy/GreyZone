#!/usr/bin/env python3
"""Merchant identity (ADR-14 / H8): ACL role + Integration ``claude-merchant-agent`` with an
MCP allowlist of exactly the tools the merchant backend calls, verified end to end.

    python docker/merchant_identity.py --shop-url http://localhost:8080 \
        --generated-env docker/.generated.env [--user admin --password shopware]

Idempotent: role and integration are looked up by name/label. The integration secret is
only known at creation, so on a re-run where ``docker/.generated.env`` lacks it (or names
another access key) the secret is **rotated** (``PATCH /api/integration/{id}``) and written
again — the script says so. Bootstrap itself authenticates with the admin password grant
(setup only); the hosts get ``SHOPWARE_INTEGRATION_ACCESS_KEY/SECRET_KEY`` and nothing else.

``MCP_TOOL_ALLOWLIST`` and ``ACL_PRIVILEGES`` are the single source of truth; the READMEs
reference them. Both extend the core ``shopware-entity-*`` set with the Admin tools of
``SwagCommerceAgentTools`` (``agent-change-*``, ``agent-business-snapshot``,
``agent-metrics-series``) and the ``agent_change:*`` / ``swag_agent_staged_change:*``
privileges they check, read from the plugin's ``acl-role-template.json``. The host's
integration is both stager and approver because the approval gate lives in the host
(portal route); the template's maker-checker split (``claude-merchant-agent`` without
``agent_change:update``, ``agent-change-approver`` with it) is for deployments where a
human approves inside Shopware. The Store API tools (``shopping-*``) have no allowlist —
``/store-api/_mcp`` is bounded by the sales-channel access key only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import string
import sys
from pathlib import Path
from typing import Any

from _bootstrap_lib import AdminApi, AdminApiError, new_id, read_env_file, upsert_env_file

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shopware_common.mcp_client import McpClient, McpError  # noqa: E402

INTEGRATION_NAME = "claude-merchant-agent"
ACL_ROLE_NAME = "claude-merchant-agent"

AGENT_TOOLS_PLUGIN_DIR = REPO_ROOT / "shopware-plugins" / "SwagCommerceAgentTools"
AGENT_TOOLS_ROLE_TEMPLATE = (
    AGENT_TOOLS_PLUGIN_DIR / "src" / "Resources" / "config" / "acl-role-template.json"
)
# The template roles whose privileges and tools the host's integration unites (see module doc).
AGENT_TOOLS_TEMPLATE_ROLES: tuple[str, ...] = ("claude-merchant-agent", "agent-change-approver")
# Only the plugin's own privileges are taken from the template; the entity privileges it
# lists (product, order, ...) are already covered by ACL_PRIVILEGES below.
AGENT_TOOLS_PRIVILEGE_PREFIXES: tuple[str, ...] = ("agent_change:", "swag_agent_staged_change:")
AGENT_TOOLS_TOOL_PREFIX = "agent-"

# The core Admin MCP tools merchant/api/admin_client.py calls (merchant/README.md lists the same).
CORE_MCP_TOOL_ALLOWLIST: tuple[str, ...] = (
    "shopware-entity-search",
    "shopware-entity-read",
    "shopware-entity-aggregate",
    "shopware-entity-upsert",
    "shopware-entity-delete",
    "shopware-entity-schema",
    # Refunds (``merchant/api/refunds.py``): the payment transition of an approved refund.
    "shopware-order-state",
)


def _role_template() -> dict[str, Any]:
    if not AGENT_TOOLS_ROLE_TEMPLATE.exists():
        raise RuntimeError(f"plugin role template missing: {AGENT_TOOLS_ROLE_TEMPLATE}")
    return json.loads(AGENT_TOOLS_ROLE_TEMPLATE.read_text(encoding="utf-8"))


def agent_tools_from_template() -> tuple[str, ...]:
    """The plugin's Admin MCP tools (``agent-*``) named in the template's allowlists."""
    template = _role_template()
    tools: set[str] = set()
    for role in AGENT_TOOLS_TEMPLATE_ROLES:
        tools.update(
            name
            for name in (template.get("mcpAllowlist") or {}).get(role, [])
            if str(name).startswith(AGENT_TOOLS_TOOL_PREFIX)
        )
    return tuple(sorted(tools))


def agent_privileges_from_template() -> tuple[str, ...]:
    """``agent_change:*`` and ``swag_agent_staged_change:*`` from the template roles."""
    template = _role_template()
    privileges: set[str] = set()
    for role in AGENT_TOOLS_TEMPLATE_ROLES:
        privileges.update(
            name
            for name in ((template.get("roles") or {}).get(role) or {}).get("privileges", [])
            if str(name).startswith(AGENT_TOOLS_PRIVILEGE_PREFIXES)
        )
    return tuple(sorted(privileges))


AGENT_MCP_TOOLS: tuple[str, ...] = agent_tools_from_template()
MCP_TOOL_ALLOWLIST: tuple[str, ...] = tuple(sorted({*CORE_MCP_TOOL_ALLOWLIST, *AGENT_MCP_TOOLS}))

# read = :read, write = :create + :update, delete = :delete (ADR-14).
_READ_ONLY_ENTITIES: tuple[str, ...] = (
    "product_price",
    "tax",
    "currency",
    "sales_channel",
    "category",
    "product_manufacturer",
    "property_group",
    "property_group_option",
    "media",
    "order",
    "order_line_item",
    "order_transaction",
    "order_delivery",
    "order_customer",
    "state_machine",
    "state_machine_state",
    "customer",
    "language",
    "unit",
    "delivery_time",
)
_READ_UPDATE_ENTITIES: tuple[str, ...] = ("product", "product_translation")
# A state transition writes the transaction's state and a history row (refunds).
_STATE_TRANSITION_PRIVILEGES: tuple[str, ...] = (
    "order:update",
    "order_transaction:update",
    "state_machine_history:create",
)
_FULL_ACCESS_ENTITIES: tuple[str, ...] = (
    "promotion",
    "promotion_translation",
    "promotion_discount",
    "promotion_discount_rule",
    "promotion_sales_channel",
    "rule",
    "rule_condition",
)


def _privileges() -> tuple[str, ...]:
    privileges: list[str] = []
    for entity in _READ_ONLY_ENTITIES:
        privileges.append(f"{entity}:read")
    for entity in _READ_UPDATE_ENTITIES:
        privileges.extend((f"{entity}:read", f"{entity}:update"))
    for entity in _FULL_ACCESS_ENTITIES:
        privileges.extend(
            (f"{entity}:read", f"{entity}:create", f"{entity}:update", f"{entity}:delete")
        )
    privileges.extend(_STATE_TRANSITION_PRIVILEGES)
    privileges.extend(agent_privileges_from_template())
    return tuple(sorted(set(privileges)))


ACL_PRIVILEGES: tuple[str, ...] = _privileges()

ENV_ACCESS_KEY = "SHOPWARE_INTEGRATION_ACCESS_KEY"
ENV_SECRET_KEY = "SHOPWARE_INTEGRATION_SECRET_KEY"
_ACCESS_KEY_PREFIX = "SWIA"  # AccessKeyHelper::INTEGRATION_IDENTIFIER
_ACCESS_KEY_RANDOM_CHARS = 22
_SECRET_KEY_CHARS = 51
_UPPER_ALNUM = string.ascii_uppercase + string.digits
_URLSAFE_ALNUM = string.ascii_letters + string.digits


def generate_access_key() -> str:
    return _ACCESS_KEY_PREFIX + "".join(
        secrets.choice(_UPPER_ALNUM) for _ in range(_ACCESS_KEY_RANDOM_CHARS)
    )


def generate_secret_key() -> str:
    return "".join(secrets.choice(_URLSAFE_ALNUM) for _ in range(_SECRET_KEY_CHARS))


# --------------------------------------------------------------------------- setup


def ensure_acl_role(api: AdminApi) -> str:
    role = api.search_one("acl-role", "name", ACL_ROLE_NAME)
    privileges = list(ACL_PRIVILEGES)
    if role is None:
        role_id = new_id()
        api.request(
            "POST",
            "/api/acl-role",
            {
                "id": role_id,
                "name": ACL_ROLE_NAME,
                "description": "Claude merchant agent (ADR-14): reads catalog/orders, writes "
                "products and promotions through the Admin MCP allowlist.",
                "privileges": privileges,
            },
        )
        print(f"acl role {ACL_ROLE_NAME}: created ({len(privileges)} privileges)")
        return role_id
    role_id = str(role["id"])
    current = sorted(role.get("privileges") or [])
    if current != privileges:
        api.request("PATCH", f"/api/acl-role/{role_id}", {"privileges": privileges})
        print(f"acl role {ACL_ROLE_NAME}: privileges updated ({len(privileges)})")
    else:
        print(f"acl role {ACL_ROLE_NAME}: unchanged ({len(privileges)} privileges)")
    return role_id


def ensure_integration(
    api: AdminApi, role_id: str, known: dict[str, str]
) -> tuple[str, str, str, bool]:
    """Returns (integration id, access key, secret, rotated)."""
    rows = api.search(
        "integration",
        {
            "limit": 1,
            "filter": [{"type": "equals", "field": "label", "value": INTEGRATION_NAME}],
            "associations": {"aclRoles": {}},
        },
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
                "label": INTEGRATION_NAME,
                "accessKey": access_key,
                "secretAccessKey": secret_key,
                "admin": False,
                "aclRoles": [{"id": role_id}],
            },
        )
        print(f"integration {INTEGRATION_NAME}: created ({access_key})")
        return integration_id, access_key, secret_key, False

    integration = rows[0]
    integration_id = str(integration["id"])
    access_key = str(integration["accessKey"])
    patch: dict[str, Any] = {}
    if integration.get("admin"):
        patch["admin"] = False
    role_ids = {str(role["id"]) for role in integration.get("aclRoles") or []}
    if role_id not in role_ids:
        patch["aclRoles"] = [{"id": rid} for rid in sorted(role_ids | {role_id})]

    secret_key = known.get(ENV_SECRET_KEY, "")
    rotated = False
    if not secret_key or known.get(ENV_ACCESS_KEY) != access_key:
        secret_key = generate_secret_key()
        patch["secretAccessKey"] = secret_key
        rotated = True
    if patch:
        api.request("PATCH", f"/api/integration/{integration_id}", patch)
    if rotated:
        print(
            f"integration {INTEGRATION_NAME}: exists ({access_key}); secret unknown to "
            "docker/.generated.env -> rotated via PATCH secretAccessKey"
        )
    else:
        print(f"integration {INTEGRATION_NAME}: unchanged ({access_key})")
    return integration_id, access_key, secret_key, rotated


def set_allowlist(api: AdminApi, integration_id: str) -> None:
    api.request(
        "POST",
        f"/api/_action/integration/{integration_id}/mcp-allowlist",
        {"allowlist": {"tools": list(MCP_TOOL_ALLOWLIST), "resources": [], "prompts": []}},
    )
    print(f"mcp allowlist: {len(MCP_TOOL_ALLOWLIST)} tools, resources [], prompts []")


# --------------------------------------------------------------------------- verification


async def verify_mcp(api: AdminApi, access_key: str, secret_key: str) -> list[str]:
    token = api.client_credentials(access_key, secret_key)
    async with McpClient(
        f"{api.shop_url}/api/_mcp",
        headers={"Authorization": f"Bearer {token}"},
        client_name="commerce-agents-bootstrap",
    ) as mcp:
        names = sorted(await mcp.tool_names())
        expected = sorted(MCP_TOOL_ALLOWLIST)
        if names != expected:
            extra = sorted(set(names) - set(expected))
            missing = sorted(set(expected) - set(names))
            print(
                f"WARNING: tools/list differs from the allowlist (extra={extra}, missing={missing})",
                file=sys.stderr,
            )

        search = await mcp.call_tool(
            "shopware-entity-search",
            {
                "entity": "product",
                "criteria": json.dumps({"limit": 1}),
                "limit": 1,
                "page": 1,
                "term": "",
            },
            raise_on_tool_error=False,
        )
        payload = search.json()
        if not (isinstance(payload, dict) and payload.get("success") and payload.get("data")):
            raise RuntimeError(f"shopware-entity-search product failed: {search.text()[:400]}")
        product = payload["data"][0]
        product_id = product.get("id")
        product_name = (
            product.get("name") or (product.get("translated") or {}).get("name") or "Product"
        )
        print(f"verify: shopware-entity-search product ok ({product.get('productNumber')})")

        upsert = await mcp.call_tool(
            "shopware-entity-upsert",
            {
                "entity": "product",
                "payload": json.dumps([{"id": product_id, "name": product_name}]),
                "dryRun": True,
            },
            raise_on_tool_error=False,
        )
        upsert_payload = upsert.json()
        if not (isinstance(upsert_payload, dict) and upsert_payload.get("success")):
            raise RuntimeError(
                f"dryRun shopware-entity-upsert product failed: {upsert.text()[:400]}"
            )
        meta = upsert_payload.get("_meta") or {}
        print(
            f"verify: shopware-entity-upsert product dryRun ok (dryRun={meta.get('dryRun', True)})"
        )

        denied = await mcp.call_tool(
            "shopware-entity-search",
            {
                "entity": "user",
                "criteria": json.dumps({"limit": 1}),
                "limit": 1,
                "page": 1,
                "term": "",
            },
            raise_on_tool_error=False,
        )
        denied_text = denied.text()
        denied_payload = denied.json()
        denied_ok = denied.is_error or (
            isinstance(denied_payload, dict) and not denied_payload.get("success")
        )
        if not denied_ok or "user:read" not in (denied_text + json.dumps(denied_payload)):
            raise RuntimeError(f"shopware-entity-search user was NOT refused: {denied_text[:400]}")
        print("verify: shopware-entity-search user refused (Missing privilege: user:read)")
        return names


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--generated-env", type=Path, required=True)
    parser.add_argument(
        "--print-tools", action="store_true", help="print the tool list as JSON on the last line"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="skip setup; use the keys in --generated-env and only run the MCP checks (verify.sh)",
    )
    args = parser.parse_args()

    api = AdminApi(args.shop_url)
    try:
        known = read_env_file(args.generated_env)
        if args.verify_only:
            access_key, secret_key = known.get(ENV_ACCESS_KEY, ""), known.get(ENV_SECRET_KEY, "")
            if not access_key or not secret_key:
                raise RuntimeError(f"{args.generated_env} lacks {ENV_ACCESS_KEY}/{ENV_SECRET_KEY}")
            tools = asyncio.run(verify_mcp(api, access_key, secret_key))
            print(f"effective MCP tools for {INTEGRATION_NAME}: {', '.join(tools)}")
            if sorted(tools) != sorted(MCP_TOOL_ALLOWLIST):
                return 1
            return 0
        api.login(args.user, args.password)
        role_id = ensure_acl_role(api)
        integration_id, access_key, secret_key, _ = ensure_integration(api, role_id, known)
        set_allowlist(api, integration_id)
        upsert_env_file(
            args.generated_env,
            {ENV_ACCESS_KEY: access_key, ENV_SECRET_KEY: secret_key},
            remove=("SHOPWARE_ADMIN_USERNAME", "SHOPWARE_ADMIN_PASSWORD"),
        )
        tools = asyncio.run(verify_mcp(api, access_key, secret_key))
    except (AdminApiError, McpError, RuntimeError, OSError) as error:
        print(f"merchant identity failed: {error}", file=sys.stderr)
        return 1
    print(f"effective MCP tools for {INTEGRATION_NAME}: {', '.join(tools)}")
    if args.print_tools:
        print(json.dumps(tools))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
