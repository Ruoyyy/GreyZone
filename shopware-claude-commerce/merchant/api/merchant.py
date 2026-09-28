# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Portal wiring: the shared merchant router plus the portal's own reads, over the
Shopware Admin transport named by ``SHOPWARE_ADMIN_TRANSPORT`` (MCP by default)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from commerce_common.memory import MemoryStore
from demo_common import MerchantIdentity, build_merchant_router
from merchant_agent.executor import MerchantToolExecutor
from merchant_agent_runtime import MerchantAgent
from shopware_common.anthropic_client import build_anthropic_client

from .admin_client import AdminTransport, build_transport
from .agent_config import DATA_DIR, SKILLS_DIR, ShopwareSettings, build_merchant_config
from .fake_admin import FakeAdmin
from .invoice_tool import INVOICE_TOOL_DEF, InvoiceMerchantExecutor, invoice_tool_enabled
from .mail_tool import MAIL_TOOL_DEF, MailMerchantExecutor, mail_tool_enabled
from .portal import build_portal_router
from .refunds import REFUND_TOOL_DEF, RefundMerchantExecutor, refund_tool_enabled
from .shopware_backend import Clock, ShopwareMerchantBackend
from .store_view import ShopwareStoreView


@dataclass(frozen=True)
class MerchantPortal:
    router: APIRouter
    portal_router: APIRouter
    backend: ShopwareMerchantBackend
    client: AdminTransport
    agent: MerchantAgent


def create_merchant_portal(
    settings: ShopwareSettings,
    memory_store: MemoryStore,
    *,
    admin: AdminTransport | None = None,
    clock: Clock | None = None,
) -> MerchantPortal:
    if admin is None:
        if settings.local_store:
            admin = FakeAdmin.from_seed(
                DATA_DIR / "seed.json", sales_channel_id=settings.sales_channel_id
            )
        else:
            admin = build_transport(
                settings.transport,
                settings.shop_url,
                access_key=settings.access_key,
                secret_key=settings.secret_key,
            )
    config = build_merchant_config(settings.store_name or settings.shop_url)
    backend = ShopwareMerchantBackend(admin, settings, config, clock=clock)
    store_view = ShopwareStoreView(backend)

    # The range's extra tools: a supplier PDF invoice reader, a simulated mailer, and
    # staged refunds (Shopware's payment state machine, behind the approval gate). The
    # executor swap goes through MerchantAgent's sanctioned ``executor_class`` seam; the
    # tool definitions are appended to the orchestrator's module-level ``build_tools``
    # output, so the model sees them in the same tools[] as everything else. The pinned
    # blueprint packages are untouched.
    from merchant_agent_runtime import orchestrator as _orchestrator

    # Idempotent: create_merchant_portal runs per deployment (and per test), so wrap the
    # upstream build_tools only once rather than stacking a wrapper on each call; the
    # switches are read when the tools are built, not when the wrapper is installed.
    if not getattr(_orchestrator.build_tools, "_range_tools_wrapped", False):
        _upstream_build_tools = _orchestrator.build_tools

        def _build_tools_with_range_tools(config, skill_names, *args, **kwargs):
            return [
                *_upstream_build_tools(config, skill_names, *args, **kwargs),
                *([INVOICE_TOOL_DEF] if invoice_tool_enabled() else []),
                *([MAIL_TOOL_DEF] if mail_tool_enabled() else []),
                *([REFUND_TOOL_DEF] if refund_tool_enabled() else []),
            ]

        _build_tools_with_range_tools._range_tools_wrapped = True
        _orchestrator.build_tools = _build_tools_with_range_tools

    # Each enabled tool's executor adds its handler on top of super().handlers(), so the
    # enabled ones compose into a single class.
    range_executors = tuple(
        executor
        for enabled, executor in (
            (invoice_tool_enabled(), InvoiceMerchantExecutor),
            (mail_tool_enabled(), MailMerchantExecutor),
            (refund_tool_enabled(), RefundMerchantExecutor),
        )
        if enabled
    )
    executor_class = (
        type("RangeMerchantExecutor", range_executors, {})
        if range_executors
        else MerchantToolExecutor
    )

    agent = MerchantAgent(
        backend=backend,
        skills_dir=SKILLS_DIR,
        config=config,
        memory_store=memory_store,
        client=build_anthropic_client(timeout=config.request_timeout_s),
        executor_class=executor_class,
    )
    router = build_merchant_router(
        storefront=store_view,
        backend=backend,
        agent=agent,
        identity=MerchantIdentity(merchant_id=settings.merchant_id, operator=settings.operator),
        example_dir="merchant",
        overview_extras=lambda: {"shop": backend.shop_info()},
    )
    portal_router = build_portal_router(backend, router, operator=settings.operator)
    return MerchantPortal(
        router=router, portal_router=portal_router, backend=backend, client=admin, agent=agent
    )
