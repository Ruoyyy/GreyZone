# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The SQLite ledger keeps changes, write payloads and the id sequence across restarts."""

from __future__ import annotations

from pathlib import Path

import pytest

from merchant.api.fake_admin import OIL, FakeAdmin
from merchant.api.ledger import MEMORY_DSN, SqliteChangeLedger, sqlite_path
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent import ChangeStatus, PriceUpdateItem


def test_dsn_parsing(tmp_path: Path):
    assert sqlite_path(None) == "./merchant/data/ledger.db"
    assert sqlite_path("sqlite:///./merchant/data/ledger.db") == "./merchant/data/ledger.db"
    assert sqlite_path(f"sqlite:///{tmp_path}/x.db") == f"{tmp_path}/x.db"
    assert sqlite_path(":memory:") == MEMORY_DSN and sqlite_path("sqlite:///:memory:") == MEMORY_DSN
    with pytest.raises(ValueError):
        sqlite_path("sqlite:///")


async def test_changes_and_payloads_survive_a_restart(
    tmp_path: Path, admin: FakeAdmin, settings, config, now, session
):
    dsn = f"sqlite:///{tmp_path / 'ledger.db'}"
    first = SqliteChangeLedger(config, dsn)
    backend = ShopwareMerchantBackend(admin, settings, config, ledger=first, clock=lambda: now)
    await backend.warm()
    staged = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.5)]
    )
    applied_source = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.0)]
    )
    await backend.apply_change(session, applied_source.change_id)
    assert staged.change_id == "chg-0001" and applied_source.change_id == "chg-0002"
    first.close()

    second = SqliteChangeLedger(config, dsn)
    assert [c.change_id for c in second.pending()] == ["chg-0001"]
    assert second.get("chg-0002").status is ChangeStatus.APPLIED
    assert any(
        note.startswith("applied: wrote product") for note in second.get("chg-0002").guardrail_notes
    )
    ((entity, payload),) = second.payloads("chg-0001")
    assert (
        entity == "product" and payload[0]["id"] == OIL and payload[0]["price"][0]["gross"] == 13.5
    )

    restarted = ShopwareMerchantBackend(admin, settings, config, ledger=second, clock=lambda: now)
    await restarted.warm()
    third = await restarted.stage_price_update(
        session, [PriceUpdateItem(listing_id=OIL, new_price=13.2)]
    )
    assert third.change_id == "chg-0003"  # the sequence continues
    applied = await restarted.apply_change(session, "chg-0001")  # the old payload replays
    assert (
        applied.status is ChangeStatus.APPLIED and admin.product(OIL)["price"][0]["gross"] == 13.5
    )
    assert [c.change_id for c in restarted.changes("all")] == ["chg-0001", "chg-0002", "chg-0003"]
    assert [c.change_id for c in restarted.changes("staged")] == ["chg-0003"]
    second.close()

    fourth = SqliteChangeLedger(config, dsn)
    assert fourth.get("chg-0001").status is ChangeStatus.APPLIED
    fourth.close()


def test_memory_ledger_starts_empty(config):
    ledger = SqliteChangeLedger(config, ":memory:")
    assert ledger.pending() == [] and ledger.all() == []
    ledger.close()
