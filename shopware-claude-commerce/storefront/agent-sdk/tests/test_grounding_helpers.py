# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""ground_message wires the shopping rules to the toolset's executor and state: the
catalog rule fires on Shopware's hex-UUID ids, the orders rule prefetches, the policy
rule stays a prompt rule."""

from __future__ import annotations

from shopware_shopping_sdk import ground_message

from commerce_common.testing import result_text
from shopping_agent.fencing import STOREFRONT_FENCE
from storefront.api.tests.replay import PRODUCT_ID, VARIANT_S

ORDERS_INTRO = "Recent orders for this turn, fetched by the host"
CATALOG_INTRO = f"Catalog record for {VARIANT_S}, fetched by the host"


async def test_a_hex_uuid_reference_is_grounded_and_unlocks_the_cart_gate(handlers, toolset):
    text = f"Add {VARIANT_S} to my cart."
    grounded = await ground_message(text, toolset)
    assert grounded.startswith(text) and CATALOG_INTRO in grounded
    assert STOREFRONT_FENCE.open in grounded
    assert VARIANT_S in toolset.state.seen_products
    result = await handlers["add_to_cart"].handler({"product_id": VARIANT_S, "quantity": 1})
    assert "is_error" not in result and f"Added {VARIANT_S} x1" in result_text(result)


async def test_a_family_reference_grounds_the_record_with_its_variants(toolset):
    grounded = await ground_message(f"Tell me about {PRODUCT_ID}", toolset)
    assert PRODUCT_ID in toolset.state.seen_products
    assert VARIANT_S in toolset.state.seen_products  # variants enter provenance with the family
    assert VARIANT_S in grounded


async def test_an_order_ask_is_grounded_with_the_sessions_orders(toolset):
    """A guest without a cart has no orders behind its token; the rule still prefetches
    and the host-fetched answer is appended for the model to report from."""
    text = "Where's my order?"
    grounded = await ground_message(text, toolset)
    assert grounded.startswith(text) and ORDERS_INTRO in grounded


async def test_a_policy_question_passes_through_because_its_rule_has_no_prefetch(toolset):
    text = "How do returns work?"
    assert await ground_message(text, toolset) == text
