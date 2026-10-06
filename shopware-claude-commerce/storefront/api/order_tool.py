# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The explicit order action for the storefront Messages API runtime."""

from __future__ import annotations

from typing import Any

from commerce_common.streaming import AgentEvent, ToolOutcome
from shopping_agent import Cart
from shopping_agent.serialization import cart_payload

from .mail_tool import MailShoppingExecutor
from .store_api import StoreApiError

PLACE_ORDER_TOOL_DEF = {
    "name": "place_order",
    "description": (
        "Submit the current cart as an order for the signed-in Shopware customer. Use when "
        "the customer explicitly asks to place an order or buy. For a request to "
        "review the cart or total, use checkout to show the summary instead. This tool "
        "does not take payment. Call it only once for a cart; report that the order was "
        "placed only after it succeeds."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

CHECKOUT_PREVIEW_DESCRIPTION = (
    "Show the current cart as a checkout summary card. This only displays the summary; "
    "it does not place an order or take payment. Use when the customer asks for a "
    "checkout summary or to review the cart before ordering. For an explicit request "
    "to buy or place an order, call place_order instead."
)


class OrderShoppingExecutor(MailShoppingExecutor):
    def handlers(self) -> dict[str, Any]:
        return {**super().handlers(), "place_order": self._place_order}

    async def _place_order(self, _tool_input: dict[str, Any]) -> ToolOutcome:
        try:
            placed = await self._backend.place_order(self._session)
        except PermissionError:
            return ToolOutcome.error("Sign in to a Shopware account before placing an order.")
        except ValueError as error:
            return ToolOutcome.error(str(error))
        except StoreApiError as error:
            return ToolOutcome.error(f"Shopware did not confirm the order ({error.status}). Check your account before retrying.")
        empty = Cart(currency=placed.currency)
        return ToolOutcome(
            f"Order placed: {placed.order_id}. This agent did not process payment. "
            "Tell the customer the order was placed, and use present_order_status for the order card.",
            [AgentEvent.cart_update(cart_payload(empty))],
        )
