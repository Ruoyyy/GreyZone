# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The portal's own routes over ``TestClient`` and ``FakeAdmin``: session scoping, the
dashboard/orders/changes payload shapes, and the overview extras."""

from __future__ import annotations

import asyncio
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from commerce_common.memory import JsonFileMemoryStore
from commerce_common.skills import SkillRegistry
from merchant.api import invoice_tool, mail_tool
from merchant.api.fake_admin import OIL, FakeAdmin
from merchant.api.merchant import create_merchant_portal
from merchant.api.tests.test_invoice_tool import text_pdf
from merchant_agent import MerchantSessionContext, MerchantSessionState, PriceUpdateItem

PREFIX = "/api/merchant"


@pytest.fixture
def client(monkeypatch, settings, now, tmp_path: Path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    admin = FakeAdmin(now=now)
    portal = create_merchant_portal(
        settings, JsonFileMemoryStore(tmp_path / "memory.json"), admin=admin, clock=lambda: now
    )
    app = FastAPI()
    app.include_router(portal.router, prefix=PREFIX)
    app.include_router(portal.portal_router, prefix=PREFIX)
    asyncio.run(portal.backend.warm())
    with TestClient(app) as test_client:
        test_client.merchant_portal = portal  # type: ignore[attr-defined]
        yield test_client
    portal.backend.ledger.close()


def _session(client: TestClient) -> dict[str, str]:
    response = client.post(f"{PREFIX}/session")
    assert response.status_code == 200, response.text
    return {"X-Session-Id": response.headers.get("X-Session-Id") or response.json()["session_id"]}


def test_routes_require_a_session(client: TestClient):
    for path in ("/dashboard", "/orders", "/changes"):
        assert client.get(f"{PREFIX}{path}").status_code == 401
    assert client.get(f"{PREFIX}/dashboard", headers={"X-Session-Id": "bogus"}).status_code == 401


def test_overview_carries_shop_extras_and_recent_orders(client: TestClient):
    headers = _session(client)
    overview = client.get(f"{PREFIX}/overview", headers=headers).json()
    assert overview["shop"] == {
        "name": "Storefront",
        "operator": "Dana",
        "currency": "EUR",
        "transport": "fake",
        "sales_channel": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeee01",
    }
    assert overview["recent_orders"] and overview["recent_orders"][0]["order_id"] == "10001"
    assert overview["snapshot"]["orders"] > 0 and overview["needs_attention"]["order_issues"]


def test_dashboard_payload_shape(client: TestClient):
    headers = _session(client)
    body = client.get(f"{PREFIX}/dashboard", params={"period": "last_7d"}, headers=headers).json()
    assert body["period"] == {
        "label": "Aug 28 – Sep 3",
        "against": "the prior week",
        "key": "last_7d",
    }
    kpis = body["kpis"]
    assert set(kpis) == {"sales", "orders", "conversion", "average_order"}
    assert kpis["sales"]["unit"] == "EUR" and len(kpis["sales"]["points"]) == 7
    assert set(kpis["sales"]["points"][0]) == {"date", "value"}
    assert kpis["orders"]["unit"] == "count" and kpis["orders"]["value"] > 0
    assert kpis["conversion"]["value"] is None and "traffic" in kpis["conversion"]["note"]
    assert kpis["average_order"]["value"] == round(
        kpis["sales"]["value"] / kpis["orders"]["value"], 2
    )
    assert body["digest"].startswith("Sales are ") and body["digest"].endswith(" need you today.")
    assert "orders and" in body["digest"] and "listings" in body["digest"]
    monthly = client.get(
        f"{PREFIX}/dashboard", params={"period": "last_30d"}, headers=headers
    ).json()
    assert (
        monthly["period"]["against"] == "the prior 30 days"
        and len(monthly["kpis"]["sales"]["points"]) == 30
    )


def test_dashboard_context_carries_the_operators_clock(client: TestClient, monkeypatch):
    """The session context the route builds is aware and in the browser's zone
    (``X-Timezone``), and falls back to ``HOST_TIMEZONE`` when the header is absent."""
    headers = _session(client)
    backend = client.merchant_portal.backend  # type: ignore[attr-defined]
    seen: list[MerchantSessionContext] = []
    real_dashboard = backend.dashboard

    async def spy(context, period):
        seen.append(context)
        return await real_dashboard(context, period)

    monkeypatch.setattr(backend, "dashboard", spy)
    monkeypatch.setenv("HOST_TIMEZONE", "Europe/Berlin")

    client.get(f"{PREFIX}/dashboard", headers={**headers, "X-Timezone": "Asia/Tokyo"})
    client.get(f"{PREFIX}/dashboard", headers=headers)
    client.get(f"{PREFIX}/dashboard", headers={**headers, "X-Timezone": "Nowhere/Land"})

    zones = [context.timezone for context in seen]
    assert zones == ["Asia/Tokyo", "Europe/Berlin", "Europe/Berlin"]
    for context in seen:
        assert context.now is not None and context.now.utcoffset() is not None
        assert context.now.tzinfo == ZoneInfo(context.timezone)


def test_orders_payload_shape(client: TestClient):
    headers = _session(client)
    body = client.get(f"{PREFIX}/orders", params={"limit": 5}, headers=headers).json()
    assert len(body["orders"]) == 5
    first = body["orders"][0]
    assert set(first) >= {
        "order_id", "order_number", "status", "placed_at", "total", "currency", "items", "customer"
    }  # fmt: skip
    assert (
        first["order_number"] == "10001"
        and first["currency"] == "EUR"
        and first["customer"] == "Dana Buyer 1"
    )
    tagged = [o for o in body["orders"] if "issue" in o]
    assert tagged and tagged[0]["issue"] == "buyer_message"
    assert client.get(f"{PREFIX}/orders", params={"limit": 0}, headers=headers).status_code == 422


def test_changes_route_filters_by_status(client: TestClient):
    headers = _session(client)
    backend = client.merchant_portal.backend  # type: ignore[attr-defined]
    context = MerchantSessionContext(session_id="s", merchant_id="m", operator="Dana")
    staged = asyncio.run(
        backend.stage_price_update(context, [PriceUpdateItem(listing_id=OIL, new_price=13.5)])
    )
    applied = asyncio.run(
        backend.stage_price_update(context, [PriceUpdateItem(listing_id=OIL, new_price=13.0)])
    )
    asyncio.run(backend.apply_change(context, applied.change_id))
    body = client.get(f"{PREFIX}/changes", headers=headers).json()
    assert body["status"] == "staged" and [c["change_id"] for c in body["changes"]] == [
        staged.change_id
    ]
    assert body["changes"][0]["items"][0]["target"] == OIL
    applied_body = client.get(
        f"{PREFIX}/changes", params={"status": "applied"}, headers=headers
    ).json()
    assert [c["change_id"] for c in applied_body["changes"]] == [applied.change_id]
    everything = client.get(f"{PREFIX}/changes", params={"status": "all"}, headers=headers).json()
    assert len(everything["changes"]) == 2
    assert (
        client.get(f"{PREFIX}/changes", params={"status": "weird"}, headers=headers).json()[
            "status"
        ]
        == "staged"
    )


def test_listing_a_staged_change_makes_its_portal_buttons_actionable(client: TestClient):
    # A change staged in another session (or before a restart) is unknown to this session's
    # provenance until the portal lists it; without that sighting Approve is held by the gate.
    backend = client.merchant_portal.backend  # type: ignore[attr-defined]
    elsewhere = MerchantSessionContext(session_id="other", merchant_id="m", operator="Dana")
    staged = asyncio.run(
        backend.stage_price_update(elsewhere, [PriceUpdateItem(listing_id=OIL, new_price=13.5)])
    )
    headers = _session(client)
    unseen = client.post(f"{PREFIX}/changes/{staged.change_id}/discard", headers=headers).json()
    assert unseen["ok"] is False and "not staged or listed in this session" in unseen["reason"]

    listed = client.get(f"{PREFIX}/changes", headers=headers).json()
    assert [c["change_id"] for c in listed["changes"]] == [staged.change_id]
    dismissed = client.post(f"{PREFIX}/changes/{staged.change_id}/discard", headers=headers).json()
    assert dismissed["ok"] is True and dismissed["change"]["status"] == "discarded"
    assert dismissed["change"]["discarded_by_kind"] == "operator"


def _upload(client: TestClient, body: bytes, headers: dict[str, str]):
    return client.post(
        f"{PREFIX}/invoice", content=body, headers={**headers, "Content-Type": "application/pdf"}
    )


def test_invoice_upload_keeps_the_pdf_text_as_the_invoice(
    client: TestClient, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(invoice_tool, "INVOICE_TEXT_PATH", tmp_path / "invoice.txt")
    headers = _session(client)
    assert client.get(f"{PREFIX}/invoice", headers=headers).json() == {"enabled": True}
    uploaded = _upload(client, text_pdf("Invoice RE-7 from Acme"), headers)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["text"] == "Invoice RE-7 from Acme"
    assert (
        client.get(f"{PREFIX}/invoice", headers=headers).json()["text"] == "Invoice RE-7 from Acme"
    )


def test_invoice_upload_rejects_oversized_and_non_pdf_bodies(client: TestClient):
    headers = _session(client)
    assert _upload(client, text_pdf("x"), {}).status_code == 401
    oversized = b"%PDF-" + b"0" * invoice_tool.MAX_PDF_BYTES
    assert _upload(client, oversized, headers).status_code == 413
    assert _upload(client, b"<html></html>", headers).status_code == 422


def _mail_executor(client: TestClient, session_id: str):
    agent = client.merchant_portal.agent  # type: ignore[attr-defined]
    return agent.executor_class(
        backend=agent.backend,
        config=agent.config,
        skills=SkillRegistry([]),
        session=MerchantSessionContext(
            session_id=session_id, merchant_id="http://shopware.test", operator="Dana"
        ),
        state=MerchantSessionState(),
    )


def test_the_merchant_agent_offers_send_email_beside_the_invoice_reader(client: TestClient):
    agent = client.merchant_portal.agent  # type: ignore[attr-defined]
    names = [tool["name"] for tool in agent._tools]
    assert names.count("send_email") == 1 and names.count("read_supplier_invoice") == 1
    assert names.count("stage_refund") == 1
    handlers = _mail_executor(client, "ms-1").handlers()
    assert "send_email" in handlers and "read_supplier_invoice" in handlers
    assert "stage_refund" in handlers


def test_mail_send_appears_in_this_sessions_outbox(client: TestClient, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mail_tool, "OUTBOX_PATH", tmp_path / "mail.jsonl")
    headers = _session(client)
    assert client.get(f"{PREFIX}/mail", headers=headers).json() == {
        "enabled": True,
        "messages": [],
    }

    outcome = asyncio.run(
        _mail_executor(client, headers["X-Session-Id"]).execute(
            "send_email",
            {
                "to": "supplier@example.com",
                "cc": ["dana@example.com"],
                "subject": "Restock order",
                "body": "Please send 20 units.",
            },
        )
    )
    assert not outcome.is_error and "message id" in outcome.result_text

    messages = client.get(f"{PREFIX}/mail", headers=headers).json()["messages"]
    assert len(messages) == 1
    message = messages[0]
    assert message["to"] == "supplier@example.com" and message["cc"] == ["dana@example.com"]
    assert message["from"] == mail_tool.SENDER
    assert message["status"] == "sent" and message["message_id"].endswith("@merchant.example>")
    # Another session's outbox stays empty.
    assert client.get(f"{PREFIX}/mail", headers=_session(client)).json()["messages"] == []


def test_mail_with_a_malformed_address_is_rejected_and_not_recorded(
    client: TestClient, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(mail_tool, "OUTBOX_PATH", tmp_path / "mail.jsonl")
    headers = _session(client)
    executor = _mail_executor(client, headers["X-Session-Id"])
    bad_to = asyncio.run(
        executor.execute("send_email", {"to": "not-an-address", "subject": "x", "body": "y"})
    )
    bad_cc = asyncio.run(
        executor.execute(
            "send_email", {"to": "a@example.com", "cc": ["nope"], "subject": "x", "body": "y"}
        )
    )
    assert bad_to.is_error and bad_cc.is_error
    assert client.get(f"{PREFIX}/mail", headers=headers).json()["messages"] == []


def test_mail_route_requires_a_session_and_honors_the_switch(client: TestClient, monkeypatch):
    assert client.get(f"{PREFIX}/mail").status_code == 401
    monkeypatch.setenv("ENABLE_MERCHANT_MAIL_TOOL", "0")
    assert client.get(f"{PREFIX}/mail", headers=_session(client)).json() == {
        "enabled": False,
        "messages": [],
    }
