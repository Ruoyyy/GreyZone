# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Live check of the storefront backend against Docker Shopware. No Anthropic key.

    python storefront/scripts/smoke.py                  # UCP over MCP, then over REST
    python storefront/scripts/smoke.py --transport mcp  # one transport only
    python storefront/scripts/smoke.py --no-fallback    # fail instead of falling back

Exercises discovery, ``tools/list`` on ``/ucp/mcp``, search → details (variants) → cart
add / update / remove (signed when ``UCP_AGENT_SIGNING_KEY_PEM_FILE`` is set), policies
from the shop's CMS pages, fulfillment options, disclosures, order history behind the cart
token, and the handoff: a code is minted for the cart and POSTed to the plugin, which must
answer a redirect to ``/checkout/confirm`` (and refuse the replay).

Agent tools (``SHOPWARE_AGENT_TOOLS``): the run detects ``SwagCommerceAgentTools`` on
``/store-api/_mcp`` exactly like the host does at startup. When the shop advertises the
``shopping-*`` tools, the smoke asserts that policies, disclosure and fulfillment were
answered by the plugin (``--agent-tools host`` forces the host path instead).
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
from shopping_agent import ShoppingSessionContext  # noqa: E402
from storefront.api.agent_tools import (  # noqa: E402
    MODE_PLUGIN,
    MODES,
    SHOPPING_TOOLS,
    ShoppingAgentTools,
)
from storefront.api.handoff import HandoffBroker  # noqa: E402
from storefront.api.shopware_backend import ShopwareStorefrontBackend  # noqa: E402
from storefront.api.store_api import StoreApiClient  # noqa: E402
from storefront.api.ucp_client import TRANSPORTS, UcpClient, shop_url_from_env  # noqa: E402

load_demo_env(REPO_ROOT)
generated = REPO_ROOT / "docker" / ".generated.env"
if generated.exists():
    for key, value in dotenv_values(generated).items():
        if value and not os.environ.get(key):
            os.environ[key] = value

CHECKOUT_CONFIRM = "/checkout/confirm"
CHECKOUT_CART = "/checkout/cart"


async def check_transport(transport: str, *, fallback: bool, agent_tools_mode: str | None) -> None:
    shop = shop_url_from_env()
    client = UcpClient(shop, transport=transport, fallback=fallback)  # type: ignore[arg-type]
    store_api = StoreApiClient(shop)
    handoff = HandoffBroker(shop)
    agent_tools = ShoppingAgentTools(shop, store_api.access_key, mode=agent_tools_mode)
    backend = ShopwareStorefrontBackend(
        client, store_api=store_api, store_name="Shopware", handoff=handoff, agent_tools=agent_tools
    )
    session = ShoppingSessionContext(session_id=f"smoke-{transport}", user_id="guest")
    print(
        f"\n== UCP over {transport.upper()} (signing {'on' if client.signs_requests else 'off'}, fallback {'on' if fallback else 'off'})"
    )
    await agent_tools.detect()
    advertised = sorted(set(SHOPPING_TOOLS) & agent_tools.advertised)
    print(f"agent tools: {agent_tools.description}; /store-api/_mcp advertises {advertised}")
    plugin_expected = agent_tools.active and set(SHOPPING_TOOLS) <= agent_tools.advertised

    discovery = await client.discover()
    version = (discovery.get("ucp") or {}).get("version", "?")
    print(f"discovery: ucp version {version}")
    if transport == "mcp":
        names = sorted(await client.tool_names())
        assert "search_catalog" in names, names
        print(
            f"tools/list: {len(names)} tools ({', '.join(names)})"
        )

    products = await backend.search_products(session, "shirt", limit=3)
    if not products:
        products = await backend.search_products(session, "*", limit=3)
    assert products, "search returned nothing"
    first = products[0]
    print(f"search: {len(products)} products, first {first.title!r} {first.price} {first.currency}")

    details = await backend.get_product_details(session, first.product_id)
    assert details is not None, "details missing"
    if details.variant_of:
        # The shop may answer a search with a child row; the family carries the matrix.
        print(f"details: {details.title!r} is a variant of {details.variant_of}")
        details = await backend.get_product_details(session, details.variant_of)
        assert details is not None, "family details missing"
    print(
        f"details: title={details.title!r} variants={len(details.variants)} options={details.options} eta={details.specs.get('deliveryTime')}"
    )
    if "T-Shirt" in (details.title or ""):
        assert len(details.variants) >= 2, "seeded T-shirt should list size variants"
        assert details.product_id not in {v.product_id for v in details.variants}, (
            "parent listed as its own variant"
        )
        assert all(v.variant_of == details.product_id for v in details.variants), (
            "every child must point at the family"
        )

    sku = details.product_id
    if details.variants:
        in_stock = next((v for v in details.variants if v.in_stock), details.variants[0])
        sku = in_stock.product_id
        variant = await backend.get_product_details(session, sku)
        assert variant is not None and variant.product_id == sku, (
            "variant id must resolve to its own details"
        )

    cart = await backend.add_to_cart(session, sku, 1)
    assert cart.item_count >= 1, f"expected items, got {cart.item_count}"
    cart_id = backend.cart_id_for(session.session_id)
    checkout_url = backend.checkout_url_for(session.session_id)
    print(f"cart: added {cart.items[0].title!r}, cart_id={cart_id}, checkout_url={checkout_url}")
    assert cart_id and cart_id not in (checkout_url or ""), (
        "the raw context token must not be in the checkout URL"
    )

    cart = await backend.update_cart_item(session, cart.items[0].product_id, 2)
    assert cart.item_count >= 2, f"expected quantity 2, got {cart.item_count}"
    print("cart: update ok")

    options = await backend.get_fulfillment_options(session, [sku])
    print(f"fulfillment: {[(o.eta, o.fee) for o in options]}")
    assert options, "no shipping methods"

    policies = await backend.search_policies(session, "widerruf")
    print(
        f"policies: {len(policies)} matches, live={backend.policies.live}, first={policies[0].title!r}"
    )

    disclosure = await backend.get_disclosure(session, sku)
    assert disclosure is not None and disclosure.rows, "disclosure missing"
    print(
        f"disclosure: {disclosure.title!r} via {disclosure.sources}: "
        + "; ".join(row.value for row in disclosure.rows)
    )

    if plugin_expected:
        served = set(agent_tools.calls)
        assert served >= set(SHOPPING_TOOLS), (
            f"the plugin advertises {SHOPPING_TOOLS} but only {sorted(served)} were called"
        )
        assert disclosure.sources == ["swag-commerce-agent-tools:shopping-disclosure"], (
            disclosure.sources
        )
        print(f"agent tools: plugin path used for {', '.join(sorted(served))}")
    else:
        assert agent_tools.calls == [], f"host path expected, plugin called {agent_tools.calls}"
        print("agent tools: host path (plugin not advertised or SHOPWARE_AGENT_TOOLS=host)")

    orders = await backend.get_orders(session)
    print(f"orders behind the cart token: {len(orders)} (a fresh guest cart has none)")

    if handoff.configured:
        await check_handoff(shop, backend, session.session_id)
    else:
        print("handoff: COMMERCE_AGENTS_HANDOFF_SECRET unset — skipped (run docker/bootstrap.sh)")

    cart = await backend.remove_from_cart(session, cart.items[0].product_id)
    assert cart.items == [], "remove left items"
    print("cart: remove ok")

    await agent_tools.aclose()
    await client.aclose()
    await store_api.aclose()
    print(f"smoke ok over {transport} against {shop}")


async def check_handoff(shop: str, backend: ShopwareStorefrontBackend, session_id: str) -> None:
    code = backend.handoff_code_for(session_id)
    assert code is not None
    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as http:
        first = await http.post(f"{shop}/claude-commerce/continue", data={"code": code.code})
        location = first.headers.get("location", "")
        print(f"handoff: POST code → {first.status_code} {location}")
        if first.status_code == 404:
            print(
                "handoff: plugin route missing (CommerceAgentsHandoff not installed?) — not asserting"
            )
            return
        assert first.status_code in {302, 303} and CHECKOUT_CONFIRM in location, (
            "handoff did not land on checkout/confirm"
        )
        replay = await http.post(f"{shop}/claude-commerce/continue", data={"code": code.code})
        print(f"handoff: replay → {replay.status_code} {replay.headers.get('location', '')}")
        assert CHECKOUT_CART in replay.headers.get("location", ""), "replayed code must be refused"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--transport", choices=[*TRANSPORTS, "both"], default="both")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="fail instead of falling back to the other transport",
    )
    parser.add_argument(
        "--agent-tools",
        choices=MODES,
        default=None,
        help=f"SHOPWARE_AGENT_TOOLS for this run (default: the environment, else auto → {MODE_PLUGIN} when advertised)",
    )
    args = parser.parse_args()
    transports = list(TRANSPORTS) if args.transport == "both" else [args.transport]
    for transport in transports:
        await check_transport(
            transport, fallback=not args.no_fallback, agent_tools_mode=args.agent_tools
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as failure:
        print(f"smoke FAILED: {failure}", file=sys.stderr)
        sys.exit(1)
