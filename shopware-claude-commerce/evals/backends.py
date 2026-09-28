# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The one place the eval suite touches the project's backend modules.

``storefront/api/**``, ``merchant/api/**`` and ``shopware_common/**`` are imported here
and nowhere else in ``evals/``, so a rename in those packages surfaces as one explicit
:class:`BackendImportError` naming the module and attribute, not as a stack trace deep
in a run. ``EVALS_PROJECT_ROOT`` points the imports at another checkout (a
``git worktree`` of an earlier commit) when the working tree is mid-refactor.

Two modes:

``replay``
    The recorded Shopware fixtures: ``ShopwareReplay`` (UCP + Store API over
    ``httpx.MockTransport``) for the storefront, ``FakeAdmin`` (the in-process stand-in
    for the Admin MCP tools) for the merchant. Deterministic, no network on the backend
    side; only the model is real.
``live``
    The Docker shop named by ``SHOPWARE_URL`` / ``SHOPWARE_ADMIN_URL`` and the
    credentials in ``.env`` / ``docker/.generated.env``.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from anthropic import AsyncAnthropic

from merchant_agent import MerchantAgentConfig, MerchantBackend, StagedChange
from shopping_agent import (
    ShoppingAgentConfig,
    ShoppingSessionContext,
    StorefrontBackend,
)

from . import REPO_ROOT

Mode = Literal["replay", "live"]
MODES: tuple[Mode, ...] = ("replay", "live")
PROJECT_ROOT_ENV = "EVALS_PROJECT_ROOT"
UCP_TRANSPORT_ENV = "EVALS_UCP_TRANSPORT"  # rest | mcp for replay mode
REPLAY_SHOP_URL = "http://shopware.test"
REPLAY_HOST_URL = "http://host.test"
REPLAY_ACCESS_KEY = "test-key"
REPLAY_HANDOFF_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
REPLAY_STORE_NAME = "Shopware"
REPLAY_MERCHANT_STORE = "Demo Shop"
REPLAY_OPERATOR = "Dana"

# Fixture names a case may write as ``$NAME``. Live mode resolves them from the shop.
SHOPPING_FIXTURES = ("SHIRT", "SHIRT_S", "SHIRT_M", "SHIRT_L", "OIL", "ORDER")
MERCHANT_FIXTURES = ("SHIRT", "SHIRT_S", "SHIRT_M", "SHIRT_L", "OIL", "CANDLE", "POSTER")
LIVE_SHOPPING_TITLES = {"SHIRT": "T-Shirt", "OIL": "Olive"}
LIVE_MERCHANT_NUMBERS = {
    "SHIRT": "CA-TSHIRT",
    "SHIRT_S": "CA-TSHIRT-S",
    "SHIRT_M": "CA-TSHIRT-M",
    "SHIRT_L": "CA-TSHIRT-L",
    "OIL": "CA-OIL",
    "CANDLE": "CA-CANDLE",
    "POSTER": "CA-POSTER",
}


class BackendImportError(RuntimeError):
    """A project module or attribute the factory relies on is missing or renamed."""


def project_root() -> Path:
    override = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    return Path(override).expanduser().resolve() if override else REPO_ROOT


def _ensure_project_on_path() -> None:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _import(module: str, *attributes: str) -> tuple[Any, ...]:
    """``(attr, ...)`` from ``module`` under the project root, or one explicit error."""
    _ensure_project_on_path()
    try:
        loaded = importlib.import_module(module)
    except Exception as error:  # noqa: BLE001 - the message must name the module
        raise BackendImportError(
            f"cannot import {module!r} from {project_root()}: {type(error).__name__}: {error}. "
            f"If the tree is mid-refactor, point {PROJECT_ROOT_ENV} at a stable checkout."
        ) from error
    values = []
    for name in attributes:
        if not hasattr(loaded, name):
            raise BackendImportError(
                f"{module!r} has no attribute {name!r} (renamed during the refactor?). "
                f"Update evals/backends.py or set {PROJECT_ROOT_ENV}."
            )
        values.append(getattr(loaded, name))
    return tuple(values)


def load_project_env() -> None:
    """``.env`` at the project root, then ``docker/.generated.env``; existing variables win."""
    from dotenv import dotenv_values, load_dotenv

    load_dotenv(project_root() / ".env", override=False)
    generated = project_root() / "docker" / ".generated.env"
    if generated.exists():
        for key, value in dotenv_values(generated).items():
            if value and not os.environ.get(key):
                os.environ[key] = value


# -- the Anthropic client --------------------------------------------------------------


def anthropic_client(timeout: float) -> AsyncAnthropic:
    """``shopware_common.anthropic_client.build_anthropic_client`` when present (it adds
    the ``anthropic-workspace-id`` header identity-linked keys need); otherwise the same
    thing built here."""
    try:
        (build,) = _import("shopware_common.anthropic_client", "build_anthropic_client")
    except BackendImportError:
        workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
        headers = {"anthropic-workspace-id": workspace} if workspace else None
        return AsyncAnthropic(timeout=timeout, default_headers=headers)
    return build(timeout=timeout)


# -- harnesses -------------------------------------------------------------------------


@dataclass
class ShoppingHarness:
    backend: StorefrontBackend
    config: ShoppingAgentConfig
    skills_dir: Path
    fixture_ids: dict[str, str]
    mode: Mode
    closers: list[Callable[[], Any]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in self.closers:
            result = close()
            if hasattr(result, "__await__"):
                await result


@dataclass
class MerchantHarness:
    backend: MerchantBackend
    config: MerchantAgentConfig
    skills_dir: Path
    fixture_ids: dict[str, str]
    merchant_id: str
    operator: str
    all_changes: Callable[[], list[StagedChange]]
    mode: Mode
    closers: list[Callable[[], Any]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in self.closers:
            result = close()
            if hasattr(result, "__await__"):
                await result


def shopping_skills_dir() -> Path:
    return project_root() / "vendor" / "skills" / "shopping"


def merchant_skills_dir() -> Path:
    return project_root() / "vendor" / "skills" / "merchant"


# -- shopping ----------------------------------------------------------------------------


async def build_shopping_harness(mode: Mode) -> ShoppingHarness:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    (build_shopping_config,) = _import("storefront.api.agent_config", "build_shopping_config")
    (ShopwareStorefrontBackend,) = _import(
        "storefront.api.shopware_backend", "ShopwareStorefrontBackend"
    )
    (UcpClient,) = _import("storefront.api.ucp_client", "UcpClient")
    (StoreApiClient,) = _import("storefront.api.store_api", "StoreApiClient")
    (HandoffBroker,) = _import("storefront.api.handoff", "HandoffBroker")

    if mode == "replay":
        import httpx

        replay_mod = _import(
            "storefront.api.tests.replay",
            "ShopwareReplay",
            "PRODUCT_ID",
            "VARIANT_S",
            "VARIANT_M",
            "VARIANT_L",
            "OIL_ID",
            "ORDER_NUMBER",
        )
        ShopwareReplay, product_id, variant_s, variant_m, variant_l, oil_id, order_number = (
            replay_mod
        )
        shop = ShopwareReplay()
        transport = httpx.MockTransport(shop.handle)
        http = httpx.AsyncClient(transport=transport)
        ucp_transport = os.environ.get(UCP_TRANSPORT_ENV, "rest").strip().lower() or "rest"
        client = UcpClient(
            shop_url=REPLAY_SHOP_URL,
            http=http,
            retry_backoff=0.0,
            transport=ucp_transport,
            signer=None,
        )
        store_api = StoreApiClient(REPLAY_SHOP_URL, access_key=REPLAY_ACCESS_KEY, http=http)
        handoff = HandoffBroker(
            REPLAY_SHOP_URL, public_url=REPLAY_HOST_URL, secret=REPLAY_HANDOFF_SECRET
        )
        backend = ShopwareStorefrontBackend(
            client, store_api=store_api, store_name=REPLAY_STORE_NAME, handoff=handoff
        )
        fixture_ids = {
            "SHIRT": product_id,
            "SHIRT_S": variant_s,
            "SHIRT_M": variant_m,
            "SHIRT_L": variant_l,
            "OIL": oil_id,
            "ORDER": order_number,
        }
        return ShoppingHarness(
            backend=backend,
            config=build_shopping_config(REPLAY_STORE_NAME),
            skills_dir=shopping_skills_dir(),
            fixture_ids=fixture_ids,
            mode=mode,
            closers=[client.aclose],
        )

    load_project_env()
    shop_url = (os.environ.get("SHOPWARE_URL") or "").strip().rstrip("/")
    if not shop_url:
        raise RuntimeError("live mode needs SHOPWARE_URL (see .env.example)")
    client = UcpClient(shop_url)
    store_api = StoreApiClient(shop_url)
    handoff = HandoffBroker(shop_url)
    backend = ShopwareStorefrontBackend(
        client, store_api=store_api, store_name=REPLAY_STORE_NAME, handoff=handoff
    )
    fixture_ids = await _resolve_live_shopping_ids(backend)
    return ShoppingHarness(
        backend=backend,
        config=build_shopping_config(backend.store_name),
        skills_dir=shopping_skills_dir(),
        fixture_ids=fixture_ids,
        mode=mode,
        closers=[client.aclose],
    )


async def _resolve_live_shopping_ids(backend: StorefrontBackend) -> dict[str, str]:
    """Fixture names → live Shopware ids, found by title through the backend itself so
    the mapping follows whatever the seed script created. Names that do not resolve are
    left out; cases that need them are skipped with a reason."""
    session = ShoppingSessionContext(session_id="evals-resolve", user_id="evals")
    ids: dict[str, str] = {}
    for name, needle in LIVE_SHOPPING_TITLES.items():
        products = await backend.search_products(session, needle, None, 8)
        match = next((p for p in products if needle.lower() in p.title.lower()), None)
        if match is None:
            continue
        details = await backend.get_product_details(session, match.product_id)
        if details is None:
            continue
        ids[name] = details.product_id
        for variant in details.variants:
            label = "".join(variant.option_values.values()).strip().upper()
            if label in {"S", "M", "L"}:
                ids[f"{name}_{label}"] = variant.product_id
    return ids


# -- merchant ----------------------------------------------------------------------------


async def build_merchant_harness(mode: Mode) -> MerchantHarness:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    ShopwareSettings, build_merchant_config, load_settings = _import(
        "merchant.api.agent_config", "ShopwareSettings", "build_merchant_config", "load_settings"
    )
    (ShopwareMerchantBackend,) = _import("merchant.api.shopware_backend", "ShopwareMerchantBackend")
    (SqliteChangeLedger,) = _import("merchant.api.ledger", "SqliteChangeLedger")

    if mode == "replay":
        fake = _import(
            "merchant.api.fake_admin",
            "FakeAdmin",
            "SALES_CHANNEL_ID",
            "SHIRT",
            "SHIRT_S",
            "SHIRT_M",
            "SHIRT_L",
            "OIL",
            "CANDLE",
            "POSTER",
        )
        FakeAdmin, sales_channel_id, shirt, shirt_s, shirt_m, shirt_l, oil, candle, poster = fake
        settings = ShopwareSettings(
            shop_url=REPLAY_SHOP_URL,
            operator=REPLAY_OPERATOR,
            store_name=REPLAY_MERCHANT_STORE,
            local_store=True,
            sales_channel_id=sales_channel_id,
            ledger_dsn=":memory:",
        )
        config = _eval_merchant_config(build_merchant_config(REPLAY_MERCHANT_STORE))
        admin = FakeAdmin()
        backend = ShopwareMerchantBackend(
            admin, settings, config, ledger=SqliteChangeLedger(config, ":memory:")
        )
        await backend.warm()
        return MerchantHarness(
            backend=backend,
            config=config,
            skills_dir=merchant_skills_dir(),
            fixture_ids={
                "SHIRT": shirt,
                "SHIRT_S": shirt_s,
                "SHIRT_M": shirt_m,
                "SHIRT_L": shirt_l,
                "OIL": oil,
                "CANDLE": candle,
                "POSTER": poster,
            },
            merchant_id=settings.merchant_id,
            operator=settings.operator,
            all_changes=backend.ledger.all,
            mode=mode,
            closers=[backend.ledger.close],
        )

    load_project_env()
    (build_transport,) = _import("merchant.api.admin_client", "build_transport")
    settings = load_settings()
    if settings.local_store:
        raise RuntimeError("live mode needs integration credentials, not SHOPWARE_LOCAL_STORE=1")
    admin = build_transport(
        settings.transport,
        settings.shop_url,
        access_key=settings.access_key,
        secret_key=settings.secret_key,
    )
    config = _eval_merchant_config(build_merchant_config(settings.store_name or settings.shop_url))
    backend = ShopwareMerchantBackend(
        admin, settings, config, ledger=SqliteChangeLedger(config, ":memory:")
    )
    await backend.warm()
    fixture_ids = {}
    for name, number in LIVE_MERCHANT_NUMBERS.items():
        record = backend.catalog.by_number(number)
        if record is not None:
            fixture_ids[name] = record.listing_id
    return MerchantHarness(
        backend=backend,
        config=config,
        skills_dir=merchant_skills_dir(),
        fixture_ids=fixture_ids,
        merchant_id=settings.merchant_id,
        operator=settings.operator,
        all_changes=backend.ledger.all,
        mode=mode,
        closers=[backend.ledger.close, admin.aclose],
    )


def _eval_merchant_config(config: MerchantAgentConfig) -> MerchantAgentConfig:
    """The deployment's config with host approval forced on: the approval gate is
    under test, whatever ``MERCHANT_REQUIRE_HOST_APPROVAL`` says in the environment."""
    return config.model_copy(update={"require_host_approval": True, "enable_analysis": False})


__all__ = [
    "MERCHANT_FIXTURES",
    "MODES",
    "PROJECT_ROOT_ENV",
    "SHOPPING_FIXTURES",
    "BackendImportError",
    "MerchantHarness",
    "Mode",
    "ShoppingHarness",
    "anthropic_client",
    "build_merchant_harness",
    "build_shopping_harness",
    "load_project_env",
    "project_root",
]
