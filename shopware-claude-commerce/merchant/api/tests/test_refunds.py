# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""Refunds are staged changes: staging previews Shopware's ``refund`` transition and
writes nothing; apply runs it and reads the payment state back; the assistant's
``apply_change`` is held by the approval gate unless the deployment turns it off."""

from __future__ import annotations

import pytest

from commerce_common.skills import SkillRegistry
from merchant.api.admin_client import writes
from merchant.api.refunds import REFUND_FIELD, RefundMerchantExecutor, is_refund
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import ChangeStatus, MerchantSessionState
from merchant_agent.changes import ChangeNotApplicable

#: Seeded orders (``fake_admin.seed_orders``): 10001 is paid, 10004 open, 10013 failed,
#: 10016 cancelled.
PAID, OPEN, FAILED, CANCELLED = "10001", "10004", "10013", "10016"


async def test_staging_a_refund_previews_the_transition_and_writes_nothing(warmed, admin, session):
    change = await warmed.stage_refund(session, PAID, "Parcel lost in transit")

    assert is_refund(change) and change.status is ChangeStatus.STAGED
    assert change.summary == "Parcel lost in transit"
    [item] = change.items
    assert (item.target, item.field, item.before, item.after) == (
        PAID,
        REFUND_FIELD,
        "paid",
        "refunded",
    )
    assert any(note.startswith("preview: server dry-run OK") for note in change.guardrail_notes)
    assert any("payment method's plugin" in note for note in change.guardrail_notes)
    assert writes(admin.calls) == []
    assert admin.payment_state(PAID) == "paid"


async def test_applying_a_refund_moves_the_payment_to_refunded(warmed, admin, session):
    change = await warmed.stage_refund(session, PAID)
    assert change.summary.startswith(f"Refund order {PAID} (")

    applied = await warmed.apply_change(session, change.change_id)

    assert applied.status is ChangeStatus.APPLIED
    assert admin.payment_state(PAID) == "refunded"
    assert f"applied: order {PAID} payment paid → refunded" in applied.guardrail_notes
    assert [call.operation for call in writes(admin.calls)] == ["state"]


async def test_an_order_can_be_named_by_its_id(warmed, admin, session):
    order_id = next(o["id"] for o in admin.rows("order") if o["orderNumber"] == PAID)
    change = await warmed.stage_refund(session, order_id)
    assert change.items[0].target == PAID


@pytest.mark.parametrize(("order", "state"), [(OPEN, "open"), (FAILED, "failed")])
async def test_an_order_whose_payment_is_not_paid_is_refused(warmed, session, order, state):
    with pytest.raises(ChangeNotApplicable, match=f"its payment is {state}, and Shopware allows"):
        await warmed.stage_refund(session, order)
    assert warmed.ledger.pending() == []


async def test_a_cancelled_payment_is_refused_although_shopware_allows_it(warmed, admin, session):
    # Shopware's state machine lists ``refund`` from ``cancelled``; nothing was collected.
    preview = await admin.order_state(CANCELLED, transaction_action="refund", dry_run=True)
    assert preview["transactions"][0]["actionValid"] is True
    with pytest.raises(ChangeNotApplicable, match="cancelled, so no money was collected"):
        await warmed.stage_refund(session, CANCELLED)
    assert warmed.ledger.pending() == []


async def test_an_unknown_order_is_refused(warmed, session):
    with pytest.raises(ChangeNotApplicable, match="no order '99999'"):
        await warmed.stage_refund(session, "99999")


async def test_a_payment_refunded_since_staging_leaves_the_change_staged(warmed, admin, session):
    first = await warmed.stage_refund(session, PAID)
    second = await warmed.stage_refund(session, PAID)
    await warmed.apply_change(session, first.change_id)

    with pytest.raises(ChangeNotApplicable, match="refunded now and cannot be refunded"):
        await warmed.apply_change(session, second.change_id)
    assert warmed.ledger.get(second.change_id).status is ChangeStatus.STAGED


async def test_a_real_campaign_change_still_cannot_be_applied(warmed, session):
    from merchant.api.refunds import REFUND_KIND

    campaign = warmed.ledger.stage(
        kind=REFUND_KIND, summary="Budget", items=[], actor="Dana", currency="EUR"
    )
    with pytest.raises(ChangeNotApplicable, match="campaigns are not applied"):
        await warmed.apply_change(session, campaign.change_id)


def _executor(backend: ShopwareMerchantBackend, config, session) -> RefundMerchantExecutor:
    return RefundMerchantExecutor(
        backend=backend,
        config=config,
        skills=SkillRegistry([]),
        session=session,
        state=MerchantSessionState(),
    )


async def test_the_assistant_cannot_apply_its_refund_without_approval(
    warmed, admin, config, session
):
    # Pinned: a checkout's .env may set MERCHANT_REQUIRE_HOST_APPROVAL=0 for the whole run.
    required = config.model_copy(update={"require_host_approval": True})
    executor = _executor(warmed, required, session)
    staged = await executor.execute("stage_refund", {"order": PAID})
    assert not staged.is_error and "chg-" in staged.result_text
    change_id = warmed.ledger.pending()[0].change_id

    held = await executor.execute("apply_change", {"change_id": change_id})

    assert held.blocked == "approval"
    assert admin.payment_state(PAID) == "paid"


async def test_without_host_approval_the_assistant_applies_its_refund(
    warmed, admin, config, session
):
    executor = _executor(
        warmed, config.model_copy(update={"require_host_approval": False}), session
    )
    await executor.execute("stage_refund", {"order": PAID})
    change_id = warmed.ledger.pending()[0].change_id

    applied = await executor.execute("apply_change", {"change_id": change_id})

    assert not applied.is_error and applied.blocked is None
    assert admin.payment_state(PAID) == "refunded"


async def test_a_refused_refund_reaches_the_model_as_a_tool_error(warmed, config, session):
    outcome = await _executor(warmed, config, session).execute("stage_refund", {"order": OPEN})
    assert outcome.is_error and "cannot be refunded" in outcome.result_text


async def test_a_real_run_answers_like_shopwares_order_state_tool(admin):
    # OrderStateTool (6.7.13): a real run reports ``executed`` and ``to``, not ``actionValid``.
    data = await admin.order_state(PAID, transaction_action="refund", dry_run=False)
    [ran] = data["transactions"]
    assert ran["executed"] is True and ran["to"] == "refunded" and "actionValid" not in ran
    again = await admin.order_state(PAID, transaction_action="refund", dry_run=False)
    assert (
        again["transactions"][0]["executed"] is False
        and "not available" in (again["transactions"][0]["note"])
    )


def _add_cancelled_payment(admin, order_number: str) -> None:
    orders = admin.rows("order")
    order = next(o for o in orders if o["orderNumber"] == order_number)
    order["transactions"].append(
        {"id": "f" * 32, "stateMachineState": {"technicalName": "cancelled"}}
    )
    admin.set_orders(orders)


async def test_an_order_whose_other_payment_would_also_move_is_refused(warmed, admin, session):
    # Shopware moves every transaction the action is valid on; a cancelled one would be too.
    _add_cancelled_payment(admin, PAID)
    with pytest.raises(ChangeNotApplicable, match="2 payments Shopware would move"):
        await warmed.stage_refund(session, PAID)
    assert warmed.ledger.pending() == []


async def test_apply_writes_nothing_when_another_payment_became_refundable(warmed, admin, session):
    change = await warmed.stage_refund(session, PAID)
    _add_cancelled_payment(admin, PAID)

    with pytest.raises(ChangeNotApplicable, match="would now also move cancelled"):
        await warmed.apply_change(session, change.change_id)
    assert writes(admin.calls) == [] and admin.payment_state(PAID) == "paid"
    assert warmed.ledger.get(change.change_id).status is ChangeStatus.STAGED
