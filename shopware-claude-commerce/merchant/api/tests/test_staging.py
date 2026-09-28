# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Staging previews with the server dry run and never writes; apply replays the stored
payload and is the only mutation."""

from __future__ import annotations

import pytest

from merchant.api.admin_client import EUR_CURRENCY_ID, WriteResult, writes
from merchant.api.agent_config import build_merchant_config
from merchant.api.fake_admin import (
    CANDLE,
    OIL,
    SALES_CHANNEL_ID,
    SHIRT,
    SHIRT_L,
    SHIRT_M,
    SHIRT_S,
    USD_CURRENCY_ID,
    FakeAdmin,
)
from merchant.api.ledger import SqliteChangeLedger
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant.api.staging import ShopwareWriter, WriteFailed
from merchant_agent import (
    CampaignDraft,
    ChangeStatus,
    InventoryActionItem,
    PriceUpdateItem,
    PromotionDraft,
)
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation

PREVIEW_PREFIX = "preview: server dry-run OK — would write "


async def _stage_everything(backend: ShopwareMerchantBackend, session) -> list:
    return [
        await backend.stage_listing_update(session, OIL, {"title": "Olive Oil 500 ml"}),
        await backend.stage_price_update(
            session, [PriceUpdateItem(listing_id=OIL, new_price=13.50)]
        ),
        await backend.stage_inventory_action(
            session, [InventoryActionItem(listing_id=SHIRT_S, action="restock", quantity=5)]
        ),
        await backend.stage_inventory_action(
            session, [InventoryActionItem(listing_id=SHIRT, action="pause")]
        ),
        await backend.stage_promotion(
            session,
            PromotionDraft(
                name="Oil week",
                listing_ids=[OIL],
                discount_pct=10,
                starts="2026-09-01",
                ends="2026-09-07",
            ),
        ),
    ]


async def test_staging_previews_but_never_writes(warmed, session, admin: FakeAdmin):
    changes = await _stage_everything(warmed, session)
    assert writes(admin.calls) == []
    previews = [c for c in admin.calls if c.operation == "upsert" and c.dry_run is True]
    assert len(previews) == len(changes)
    for change in changes:
        assert any(note.startswith(PREVIEW_PREFIX) for note in change.guardrail_notes)
    assert admin.product(OIL)["name"] == "Extra Virgin Olive Oil 500 ml"
    assert admin.product(SHIRT_S)["stock"] == 4 and admin.product(SHIRT)["active"] is True
    assert admin.rows("promotion") == []
    assert len(await warmed.get_pending_changes(session)) == 5


async def test_preview_note_names_the_rows_the_server_would_write(warmed, session):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    assert (
        "preview: server dry-run OK — would write product, product_translation (1 row each)"
        in change.guardrail_notes
    )


async def test_rejected_preview_never_reaches_the_ledger(warmed, session, admin: FakeAdmin):
    admin.fail_next_preview = "There are 1 error(s) while writing data.\n\n1. [/0/stock] This value should be of type int."
    with pytest.raises(ChangeNotApplicable) as info:
        await warmed.stage_inventory_action(
            session, [InventoryActionItem(listing_id=OIL, action="restock", quantity=1)]
        )
    assert "Shopware rejected the preview" in str(info.value) and "type int" in str(info.value)
    assert await warmed.get_pending_changes(session) == []


async def test_server_side_validation_catches_bad_payloads(admin: FakeAdmin):
    bad = await admin.upsert("product", {"id": OIL, "stock": "lots"}, dry_run=True)
    assert bad.success is False and "[/0/stock] This value should be of type int." in (
        bad.error or ""
    )
    unknown = await admin.upsert("nonsense", {"id": OIL}, dry_run=True)
    assert unknown.success is False and 'Entity "nonsense" not found' in (unknown.error or "")
    nameless = await admin.upsert("promotion", {"id": "a" * 32, "active": True}, dry_run=True)
    assert nameless.success is False and "/name] This value should not be blank" in (
        nameless.error or ""
    )
    assert writes(admin.calls) == []


async def test_guardrail_violation_is_raised_before_any_preview(warmed, session, admin: FakeAdmin):
    with pytest.raises(GuardrailViolation):
        await warmed.stage_price_update(session, [PriceUpdateItem(listing_id=OIL, new_price=30.0)])
    assert [c for c in admin.calls if c.operation == "upsert"] == []


async def test_range_price_delta_cap_off_lets_an_extreme_cut_stage(
    settings, admin: FakeAdmin, now, session, monkeypatch
):
    # Range env turns the price-move cap off so the low-price lane opens: a cut the default
    # 20% cap would reject stages cleanly, and the model is told no cap.
    monkeypatch.setenv("MERCHANT_PRICE_DELTA_CAP", "0")
    config = build_merchant_config("Demo Shop")
    backend = ShopwareMerchantBackend(
        admin, settings, config, ledger=SqliteChangeLedger(config, ":memory:"), clock=lambda: now
    )
    await backend.warm()
    change = await backend.stage_price_update(session, [PriceUpdateItem(listing_id=OIL, new_price=0.01)])
    assert change.items and float(change.items[0].after) == 0.01
    context = await backend.get_pricing_context(session, OIL)
    assert context is not None and context.max_price_delta_pct is None


async def test_stage_campaign_is_not_applicable(warmed, session):
    with pytest.raises(ChangeNotApplicable):
        await warmed.stage_campaign(session, CampaignDraft(name="Email", budget=100.0))


# ------------------------------------------------------------------ K1 / H9


async def test_apply_refuses_anything_not_staged(warmed, session, admin: FakeAdmin):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    applied = await warmed.apply_change(session, change.change_id)
    assert applied.status is ChangeStatus.APPLIED
    write_count = len(writes(admin.calls))
    with pytest.raises(ChangeNotApplicable, match="applied, not staged"):
        await warmed.apply_change(session, change.change_id)
    with pytest.raises(ChangeNotApplicable, match="applied, not staged"):
        await warmed.discard_change(session, change.change_id)
    discarded = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.0)]
    )
    await warmed.discard_change(session, discarded.change_id)
    with pytest.raises(ChangeNotApplicable, match="discarded, not staged"):
        await warmed.apply_change(session, discarded.change_id)
    with pytest.raises(ChangeNotApplicable, match="no change with id"):
        await warmed.apply_change(session, "chg-9999")
    assert len(writes(admin.calls)) == write_count  # none of the refusals wrote


async def test_apply_that_comes_back_as_a_preview_leaves_the_change_staged(
    warmed, session, admin: FakeAdmin
):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    real_upsert = admin.upsert

    async def preview_only(entity, payload, *, dry_run):
        return await real_upsert(entity, payload, dry_run=True)

    admin.upsert = preview_only  # type: ignore[method-assign]
    with pytest.raises(ChangeNotApplicable, match="still staged"):
        await warmed.apply_change(session, change.change_id)
    assert (await warmed.get_pending_changes(session))[0].change_id == change.change_id
    assert admin.product(OIL)["price"][0]["gross"] == 12.9


async def test_failed_write_leaves_change_staged(warmed, session, admin: FakeAdmin):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    admin.fail_next_write = "There are 1 error(s) while writing data.\n\n1. [/0/price] nope"
    with pytest.raises(ChangeNotApplicable) as info:
        await warmed.apply_change(session, change.change_id)
    assert "still staged" in str(info.value) and "nope" in str(info.value)
    assert (await warmed.get_pending_changes(session))[0].change_id == change.change_id


# ------------------------------------------------------------------ H6 price


async def test_price_write_replaces_only_the_eur_entry_with_net_from_tax(
    warmed, session, admin: FakeAdmin
):
    admin.patch_product(
        OIL,
        price=[
            {"currencyId": EUR_CURRENCY_ID, "gross": 12.9, "net": 10.84, "linked": True},
            {"currencyId": USD_CURRENCY_ID, "gross": 14.0, "net": 11.76, "linked": False},
        ],
    )
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    assert change.items[0].before == 12.9 and change.items[0].after == 13.5
    assert change.margin_before_pct == 41.9 and change.margin_after_pct == 44.4
    applied = await warmed.apply_change(session, change.change_id)
    assert applied.status is ChangeStatus.APPLIED
    write = writes(admin.calls)[0]
    assert write.entity == "product" and write.dry_run is False
    (row,) = write.payload
    assert row["id"] == OIL
    eur = next(e for e in row["price"] if e["currencyId"] == EUR_CURRENCY_ID)
    usd = next(e for e in row["price"] if e["currencyId"] == USD_CURRENCY_ID)
    assert eur["gross"] == 13.5 and eur["net"] == round(13.5 / 1.19, 2) and eur["linked"] is True
    assert usd == {"currencyId": USD_CURRENCY_ID, "gross": 14.0, "net": 11.76, "linked": False}
    assert admin.product(OIL)["price"] == row["price"]


async def test_family_with_per_variant_prices_stages_per_variant(warmed, session, admin: FakeAdmin):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=SHIRT, new_price=32.0)]
    )
    by_target = {item.target: item for item in change.items}
    # S and M carry their own prices; L inherits, so the parent row is written for it.
    assert set(by_target) == {SHIRT_S, SHIRT_M, SHIRT}
    assert by_target[SHIRT_S].before == 29.99 and by_target[SHIRT_M].before == 31.99
    assert by_target[SHIRT].before == 29.99
    (payload,) = warmed.ledger.payloads(change.change_id)
    assert payload[0] == "product" and {row["id"] for row in payload[1]} == {
        SHIRT_S,
        SHIRT_M,
        SHIRT,
    }
    await warmed.apply_change(session, change.change_id)
    assert admin.product(SHIRT_M)["price"][0]["gross"] == 32.0
    assert admin.product(SHIRT_L)["price"] is None  # still inherits, now 32.0 via the parent
    assert admin.product(SHIRT)["price"][0]["gross"] == 32.0


async def test_inheriting_variant_gets_its_own_price_with_a_note(warmed, session):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=SHIRT_L, new_price=27.0)]
    )
    assert change.items[0].target == SHIRT_L and change.items[0].before == 29.99
    assert any("inherited the family price" in note for note in change.guardrail_notes)


# ------------------------------------------------------------------ M1 / M2 inventory


async def test_restock_applies_the_delta_to_the_current_stock(warmed, session, admin: FakeAdmin):
    change = await warmed.stage_inventory_action(
        session, [InventoryActionItem(listing_id=SHIRT_S, action="restock", quantity=5)]
    )
    assert change.items[0].before == 4 and change.items[0].after == 9
    admin.patch_product(SHIRT_S, stock=6)  # someone else sold/added stock meanwhile
    applied = await warmed.apply_change(session, change.change_id)
    assert admin.product(SHIRT_S)["stock"] == 11
    assert any("moved from 4 to 6" in note and "→ 11" in note for note in applied.guardrail_notes)
    with pytest.raises(ChangeNotApplicable, match="family"):
        await warmed.stage_inventory_action(
            session, [InventoryActionItem(listing_id=SHIRT, action="restock", quantity=1)]
        )


async def test_pausing_a_family_covers_parent_and_variants_in_one_write(
    warmed, session, admin: FakeAdmin
):
    change = await warmed.stage_inventory_action(
        session, [InventoryActionItem(listing_id=SHIRT, action="pause")]
    )
    assert {item.target for item in change.items} == {SHIRT, SHIRT_S, SHIRT_M, SHIRT_L}
    assert all(item.after == "paused" for item in change.items)
    await warmed.apply_change(session, change.change_id)
    live = writes(admin.calls)
    assert len(live) == 1 and len(live[0].payload) == 4
    assert all(admin.product(i)["active"] is False for i in (SHIRT, SHIRT_S, SHIRT_M, SHIRT_L))
    listing = await warmed.get_listing(session, SHIRT)
    assert listing is not None and listing.status == "paused"
    reactivate = await warmed.stage_inventory_action(
        session, [InventoryActionItem(listing_id=CANDLE, action="activate")]
    )
    assert [i.target for i in reactivate.items] == [CANDLE]


# ------------------------------------------------------------------ ADR-13 promotions


async def test_promotion_payload_is_one_atomic_shopware_write(warmed, session, admin: FakeAdmin):
    change = await warmed.stage_promotion(
        session,
        PromotionDraft(
            name="Shirt week",
            listing_ids=[SHIRT, OIL],
            discount_pct=10,
            starts="2026-09-03",
            ends="2026-09-10",
        ),
    )
    assert {i.target for i in change.items} == {SHIRT, OIL}
    shirt_item = next(i for i in change.items if i.target == SHIRT)
    assert shirt_item.before == 29.99 and shirt_item.after == 26.99
    ((entity, payload),) = warmed.ledger.payloads(change.change_id)
    assert entity == "promotion"
    assert (
        payload["name"] == "Shirt week"
        and payload["active"] is True
        and payload["useCodes"] is False
    )
    assert payload["validFrom"] == "2026-09-03T00:00:00+00:00"
    assert payload["validUntil"] == "2026-09-10T23:59:59+00:00"
    assert payload["salesChannels"] == [{"salesChannelId": SALES_CHANNEL_ID, "priority": 1}]
    (discount,) = payload["discounts"]
    assert (
        discount["scope"] == "cart"
        and discount["type"] == "percentage"
        and discount["value"] == 10.0
    )
    (rule,) = discount["discountRules"]
    assert rule["name"] == "Promotion Shirt week products"
    identifiers = rule["conditions"][0]["value"]["identifiers"]
    assert set(identifiers) == {SHIRT_S, SHIRT_M, SHIRT_L, OIL}  # variants, not the parent
    assert any(
        note.startswith(PREVIEW_PREFIX) and "promotion_discount_rule" in note
        for note in change.guardrail_notes
    )
    assert any("whole cart" in note for note in change.guardrail_notes)
    assert admin.rows("promotion") == []  # discard is ledger-only: nothing was written
    await warmed.discard_change(session, change.change_id)
    assert writes(admin.calls) == []


async def test_applying_a_promotion_creates_promotion_discount_and_rule(
    warmed, session, admin: FakeAdmin
):
    change = await warmed.stage_promotion(
        session,
        PromotionDraft(
            name="Oil day",
            listing_ids=[OIL],
            discount_pct=15,
            starts="2026-09-03",
            ends="2026-09-03",
        ),
    )
    applied = await warmed.apply_change(session, change.change_id)
    assert applied.status is ChangeStatus.APPLIED
    (promotion,) = admin.rows("promotion")
    (discount,) = admin.rows("promotion_discount")
    (rule,) = admin.rows("rule")
    assert discount["promotionId"] == promotion["id"] and discount["value"] == 15.0
    assert admin.rows("promotion_discount_rule")[0] == {
        "discountId": discount["id"],
        "ruleId": rule["id"],
    }
    assert admin.rows("rule_condition")[0]["value"]["identifiers"] == [OIL]
    read = await admin.read("promotion", promotion["id"], {"associations": {"discounts": {}}})
    assert read is not None and read["discounts"][0]["discountRules"][0]["id"] == rule["id"]
    assert any(note.startswith("applied: wrote promotion") for note in applied.guardrail_notes)


async def test_partial_failure_reports_and_rolls_back_created_entities(
    warmed, admin: FakeAdmin, session
):
    """A change whose write plan has two entities: the second one fails, the first is
    deleted again and the report names both."""
    change = await warmed.stage_promotion(
        session,
        PromotionDraft(
            name="Two-step",
            listing_ids=[OIL],
            discount_pct=5,
            starts="2026-09-03",
            ends="2026-09-04",
        ),
    )
    ((_, promotion_payload),) = warmed.ledger.payloads(change.change_id)
    rule = promotion_payload["discounts"][0]["discountRules"][0]
    plan = [
        ("rule", rule),
        ("promotion", {**promotion_payload, "name": ""}),
    ]  # blank name is refused
    writer = ShopwareWriter(admin, warmed.catalog)
    with pytest.raises(WriteFailed) as info:
        await writer.apply(change, plan)
    report = info.value.report()
    assert "written before the failure: rule" in report
    assert f"rolled back: rule {rule['id']}" in report
    assert admin.rows("rule") == [] and admin.rows("promotion") == []


async def test_writer_refuses_a_result_that_is_not_a_persisted_write(
    warmed, session, admin: FakeAdmin
):
    change = await warmed.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )

    async def half_hearted(entity, payload, *, dry_run):
        return WriteResult(success=True, written=[], dry_run=True)

    admin.upsert = half_hearted  # type: ignore[method-assign]
    writer = ShopwareWriter(admin, warmed.catalog)
    with pytest.raises(WriteFailed, match="preview, not a persisted write"):
        await writer.apply(change, warmed.ledger.payloads(change.change_id))
