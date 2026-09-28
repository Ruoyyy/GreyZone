#!/usr/bin/env python3
"""Live round trip of the handoff code (ADR-10) against the CommerceAgentsHandoff plugin.

    COMMERCE_AGENTS_HANDOFF_SECRET=… python docker/handoff_check.py --shop-url http://localhost:8080 \
        --access-key <sw-access-key>

1. Gets a fresh Store API context token (the "cart").
2. Mints a code with ``shopware_common.handoff.HandoffCodeIssuer`` and POSTs it to
   ``/claude-commerce/continue`` → expects 302 to ``/checkout/confirm`` and an
   ``sw-context-token`` cookie carrying that token.
3. Replays the same code (POST) → 302 to ``/checkout/cart``.
4. GET with the same code → 302 to ``/checkout/cart`` (already used).
5. GET with a fresh code → 302 to ``/checkout/confirm`` (noscript fallback works).
Exit 1 on any deviation.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from shopware_common.handoff import SECRET_ENV, HandoffCodeIssuer  # noqa: E402

ROUTE = "/claude-commerce/continue"
CHECKOUT_CONFIRM = "/checkout/confirm"
CHECKOUT_CART = "/checkout/cart"
CONTEXT_TOKEN_HEADER = "sw-context-token"
HTTP_TIMEOUT_SECONDS = 30.0
REDIRECT_STATUSES = {302, 303}


def expect_redirect(response: httpx.Response, target: str, label: str) -> bool:
    location = response.headers.get("location", "")
    ok = response.status_code in REDIRECT_STATUSES and target in location
    print(f"{label}: {response.status_code} {location or '-'} [{'ok' if ok else 'FAIL'}]")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--access-key", required=True, help="Storefront sales-channel access key")
    args = parser.parse_args()
    shop = args.shop_url.rstrip("/")

    secret = os.environ.get(SECRET_ENV, "").strip()
    if not secret:
        print(f"{SECRET_ENV} is not set (docker/.generated.env)", file=sys.stderr)
        return 1
    issuer = HandoffCodeIssuer(secret)

    failures = 0
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as http:
        context = http.get(f"{shop}/store-api/context", headers={"sw-access-key": args.access_key})
        token = context.headers.get(CONTEXT_TOKEN_HEADER, "")
        if context.status_code != 200 or not token:
            print(f"store-api/context failed: {context.status_code}", file=sys.stderr)
            return 1

        code = issuer.issue(token).code
        first = http.post(f"{shop}{ROUTE}", data={"code": code})
        failures += not expect_redirect(first, CHECKOUT_CONFIRM, "handoff POST fresh code")
        cookie = first.cookies.get(CONTEXT_TOKEN_HEADER)
        if cookie != token:
            failures += 1
            print(f"handoff cookie: sw-context-token mismatch ({cookie!r}) [FAIL]")
        else:
            print("handoff cookie: sw-context-token adopted [ok]")

        failures += not expect_redirect(
            http.post(f"{shop}{ROUTE}", data={"code": code}), CHECKOUT_CART, "handoff POST replay"
        )
        failures += not expect_redirect(
            http.get(f"{shop}{ROUTE}", params={"code": code}),
            CHECKOUT_CART,
            "handoff GET used code",
        )
        failures += not expect_redirect(
            http.get(f"{shop}{ROUTE}", params={"code": issuer.issue(token).code}),
            CHECKOUT_CONFIRM,
            "handoff GET fresh code",
        )
        failures += not expect_redirect(
            http.post(f"{shop}{ROUTE}", data={"code": "not.a-code"}),
            CHECKOUT_CART,
            "handoff POST garbage",
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
