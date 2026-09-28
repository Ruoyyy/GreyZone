# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shopping-agent config for the Shopware storefront. Identity never travels in
tool arguments; IDs are Shopware hex UUIDs."""

from __future__ import annotations

from shopping_agent import ShoppingAgentConfig
from shopware_common.anthropic_client import model_env_fields

_SHOPWARE_UUID = r"\b[0-9a-f]{32}\b"

# ``brand_voice`` completes "Your voice is …" in the pinned prompt; it is the host's one
# hook for its own rules. The dates rule closes the eval finding that the model confirms
# a delivery deadline that has already passed (evals/README.md, finding 5).
SHOPPING_BRAND_VOICE = (
    "friendly, direct, and plain about what this Shopware store carries. When the customer "
    "names a date (a deadline, a delivery day, an occasion), compare it with the local time "
    "in this conversation first; if it has already passed, say so plainly and ask what "
    "date they mean instead of promising delivery by it"
)


def build_shopping_config(store_name: str) -> ShoppingAgentConfig:
    return ShoppingAgentConfig(
        brand_name=store_name,
        assistant_name="the store assistant",
        brand_voice=SHOPPING_BRAND_VOICE,
        enable_disclosures=True,
        domain_search_notes=(
            "The catalog is a live Shopware 6 shop priced in EUR. A product with options "
            "is a family; purchasable SKUs are its variants in get_product_details. The "
            "cart holds variants (or simple products without options). Checkout, shipping, "
            "and payment happen on the shop's own checkout page — hand the customer to it "
            "rather than promising to place an order. "
            "German mandatory facts (Grundpreis, "
            "delivery time, VAT) come from get_disclosure, not from the product description. "
            "Order lookups require a linked Shopware account; for anything older point "
            "the customer at their Shopware "
            "confirmation email or account. Answer catalog questions from search_products "
            "and get_product_details only — do not invent aisles such as home goods, lamps, "
            "or a generic electronics/clothing mall when the tools returned something else "
            "(or nothing)."
        ),
        product_id_patterns=(_SHOPWARE_UUID,),
        **model_env_fields("SHOPPING"),
    )
