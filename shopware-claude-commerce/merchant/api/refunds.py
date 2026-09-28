# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""Refunds as staged changes: ``stage_refund`` previews Shopware's ``refund`` transition on
an order's payment (``shopware-order-state`` with ``dryRun=true``) and records it in the
ledger like any other change; ``apply_change`` runs the transition for real
(``ShopwareMerchantBackend.apply_change``). Approval therefore works exactly as it does for
a price move: the operator approves on the portal, or, with
``MERCHANT_REQUIRE_HOST_APPROVAL=0``, the assistant applies its own change. Enabled unless
``ENABLE_REFUND_TOOL=0``.

The pinned blueprint's ``ChangeKind`` has no refund member and its models validate against
that enum, so a refund is carried as ``ChangeKind.CAMPAIGN`` — a kind this deployment never
stages or applies otherwise (``stage_campaign`` refuses) — and recognised by its
``payment_state`` item (:func:`is_refund`). The guardrails the blueprint runs on the
campaign kind look only at a ``budget`` field, which a refund never carries.

The transition marks the payment refunded in Shopware; whether money goes back to the
buyer is up to the payment method's plugin, which the change's notes say.

Nothing here touches the pinned blueprint packages: the executor is swapped through
``MerchantAgent(executor_class=...)`` and the tool definition is appended to the list the
orchestrator builds, from the host side (``merchant.py``).
"""

from __future__ import annotations

import os
from typing import Any

from commerce_common.streaming import ToolOutcome
from merchant_agent import ChangeKind, StagedChange
from merchant_agent.executor import MerchantToolExecutor

#: The ledger kind that carries a refund (see the module doc).
REFUND_KIND = ChangeKind.CAMPAIGN
#: The item field that marks a change as a refund: the payment's state, before → after.
REFUND_FIELD = "payment_state"
#: Shopware's ``order_transaction`` transition for a full refund.
REFUND_ACTION = "refund"
#: Payment states with money to give back. Shopware's state machine also allows ``refund``
#: from ``cancelled`` (6.7.13.1), a payment that never came in; the host refuses that.
REFUNDABLE_STATES = frozenset({"paid", "paid_partially", "refunded_partially"})
#: The write plan's entity name for a state transition (not a DAL entity).
ORDER_STATE_ENTITY = "order_state"

REFUND_TOOL_DEF = {
    "name": "stage_refund",
    "description": (
        "Stage a full refund of a paid order: once applied, Shopware moves the order's "
        "payment from paid to refunded. Staged only, like every stage_* call — it enters "
        "the approval queue and is applied with apply_change after the operator approves. "
        "Name the order by the number the operator or an order tool gave; an order whose "
        "payment was never collected comes back refused with its current state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order": {
                "type": "string",
                "maxLength": 64,
                "description": "The order number (e.g. 10023), or the order's 32-character id.",
            },
            "note": {
                "type": "string",
                "maxLength": 200,
                "description": "One sentence on why the order is refunded.",
            },
        },
        "required": ["order"],
        "additionalProperties": False,
    },
}


def refund_tool_enabled() -> bool:
    return os.environ.get("ENABLE_REFUND_TOOL", "1") != "0"


def is_refund(change: StagedChange) -> bool:
    return change.kind is REFUND_KIND and any(item.field == REFUND_FIELD for item in change.items)


class RefundMerchantExecutor(MerchantToolExecutor):
    """The merchant executor with ``stage_refund`` beside the standard handlers."""

    def handlers(self) -> dict[str, Any]:
        return {**super().handlers(), "stage_refund": self._stage_refund}

    async def _stage_refund(self, tool_input: dict[str, Any]) -> ToolOutcome:
        order = str(tool_input.get("order", "")).strip()
        if not order:
            return ToolOutcome.error("Name the order to refund by its order number.")
        change = await self._backend.stage_refund(self._session, order, self._note(tool_input))
        return await self._staged(change)
