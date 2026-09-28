#!/usr/bin/env python3
"""Expose UCP on the Storefront sales channel — idempotent, no raw SQL (ADR-11).

    python docker/enable_ucp.py --shop-url http://localhost:8080 --signature-policy strict

* Exposure (active, capabilities, transports, allowlists, policy) goes through the plugin's
  Admin API ``PUT /api/_admin/ucp/sales-channels/{id}/config`` — the same endpoint the
  Administration module uses, so ``ucp:config:show`` and the UI agree. The config is only
  written when it differs from the desired state.
* Shop signing keys: ``ucp:signing-keys:generate`` only when the channel has no active
  key; surplus active keys (left by earlier experiments) are retired and deleted so
  ``/.well-known/ucp`` publishes exactly one.
* The platform-profile cache (the shop's copy of our ``agent-profile.json``) is purged via
  ``DELETE /api/_admin/ucp/platform-profiles/{id}`` so a changed agent key is picked up.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from _bootstrap_lib import DEFAULT_CONTAINER, AdminApi, AdminApiError, console

DEFAULT_SIGNING_KID = "default"
SIGNATURE_POLICIES = ("strict", "log", "off")
HOST_APP_ORIGINS = ("http://localhost:3005", "http://127.0.0.1:3005")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1")


def desired_config(shop_url: str, signature_policy: str) -> dict[str, Any]:
    return {
        "active": True,
        "profileDomain": shop_url,
        "enabledCapabilities": [
            "catalog",
            "cart",
            "discount",
            "checkout",
            "order",
            "identity_linking",
        ],
        "enabledTransports": ["rest", "mcp", "embedded"],
        "continueUrlTemplate": f"{shop_url}/checkout/confirm?checkoutId={{checkoutId}}",
        "platformAllowlist": list(LOOPBACK_HOSTS),
        "remoteProfileAllowlist": list(LOOPBACK_HOSTS),
        "agentAllowlist": list(LOOPBACK_HOSTS),
        "embeddedAllowedOrigins": list(HOST_APP_ORIGINS),
        "embeddedFrameAncestors": list(HOST_APP_ORIGINS),
        "discoveryBudget": 10,
        "catalogResultLimit": 50,
        "webhookUrlOverride": None,
        "signaturePolicy": signature_policy,
        "idempotencyRequired": True,
    }


def ensure_config(api: AdminApi, sales_channel_id: str, desired: dict[str, Any]) -> None:
    path = f"/api/_admin/ucp/sales-channels/{sales_channel_id}/config"
    current = api.request("GET", path).get("data") or {}
    merged = {**current, **desired}
    if all(current.get(key) == value for key, value in desired.items()):
        print(f"ucp config: unchanged (signaturePolicy={desired['signaturePolicy']})")
        return
    api.request("PUT", path, merged)
    print(f"ucp config: written (signaturePolicy={desired['signaturePolicy']})")


def ensure_single_signing_key(container: str, sales_channel_id: str) -> str:
    listing = console(
        container, f"ucp:signing-keys:list --sales-channel={sales_channel_id} --no-interaction"
    )
    keys = _parse_key_list(listing.stdout)
    active = [key for key in keys if key.get("status") == "active"]
    if not active:
        generated = console(
            container,
            f"ucp:signing-keys:generate --kid={DEFAULT_SIGNING_KID} --sales-channel={sales_channel_id} --no-interaction",
        )
        if generated.returncode != 0:
            raise RuntimeError(
                f"ucp:signing-keys:generate failed: {generated.stderr or generated.stdout}"
            )
        print(f"shop signing key: generated kid={DEFAULT_SIGNING_KID}")
        return DEFAULT_SIGNING_KID
    keep = next((key for key in active if key.get("kid") == DEFAULT_SIGNING_KID), active[-1])
    for key in keys:
        if key is keep:
            continue
        kid = str(key.get("kid"))
        if key.get("status") == "active":
            console(
                container,
                f"ucp:signing-keys:retire --kid={kid} --sales-channel={sales_channel_id} --no-interaction",
            )
        console(
            container,
            f"ucp:signing-keys:delete --kid={kid} --sales-channel={sales_channel_id} --no-interaction",
        )
        print(f"shop signing key: removed surplus kid={kid}")
    print(f"shop signing key: exactly one active (kid={keep.get('kid')})")
    return str(keep.get("kid"))


def _parse_key_list(output: str) -> list[dict[str, Any]]:
    start = output.find("[")
    if start < 0:
        return []
    try:
        parsed = json.loads(output[start:])
    except json.JSONDecodeError:
        return []
    return (
        [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []
    )


def purge_profile_cache(api: AdminApi) -> int:
    entries = api.request("GET", "/api/_admin/ucp/platform-profiles").get("data") or []
    for entry in entries:
        api.request("DELETE", f"/api/_admin/ucp/platform-profiles/{entry['id']}")
    print(
        f"platform profile cache: purged {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
    )
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    parser.add_argument("--signature-policy", choices=SIGNATURE_POLICIES, default="strict")
    args = parser.parse_args()
    shop_url = args.shop_url.rstrip("/")

    api = AdminApi(shop_url)
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
        sales_channel_id = str(channel["id"])
        print(f"Storefront sales channel {sales_channel_id}")
        ensure_config(api, sales_channel_id, desired_config(shop_url, args.signature_policy))
        ensure_single_signing_key(args.container, sales_channel_id)
        purge_profile_cache(api)
    except AdminApiError as error:
        if error.status == 404:
            print(
                "UCP admin API missing — SwagAgenticCommerce not active. Skipping UCP exposure.",
                file=sys.stderr,
            )
            return 0
        print(f"enable_ucp failed: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"enable_ucp failed: {error}", file=sys.stderr)
        return 1

    console(args.container, "cache:clear --no-warmup")
    validate = console(
        args.container, f"ucp:config:validate --sales-channel={sales_channel_id} --no-interaction"
    )
    print((validate.stdout or validate.stderr).strip()[-1200:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
