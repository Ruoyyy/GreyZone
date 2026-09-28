# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shopware merchant API.

uvicorn merchant.api.main:app --port 8005
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import dotenv_values
from fastapi import FastAPI

from commerce_common.memory import JsonFileMemoryStore
from demo_common import REPO_ROOT, load_demo_env
from demo_common.host import build_app

from .agent_config import DATA_DIR, EXAMPLE_ROOT, MissingCredentials, load_settings
from .merchant import create_merchant_portal

logger = logging.getLogger(__name__)

load_demo_env(EXAMPLE_ROOT)
generated = REPO_ROOT / "docker" / ".generated.env"
if generated.exists():
    for key, value in dotenv_values(generated).items():
        if value and not os.environ.get(key):
            os.environ[key] = value


def _unconfigured_app(reason: str) -> FastAPI:
    app = build_app(title="Shopware merchant API (unconfigured)")

    @app.get("/api/merchant/health")
    async def health() -> dict:
        return {"ok": False, "role": "merchant", "error": reason}

    logger.warning("Shopware merchant credentials are not set: %s", reason)
    return app


def create_app() -> FastAPI:
    try:
        settings = load_settings()
    except MissingCredentials as error:
        return _unconfigured_app(str(error))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    portal = create_merchant_portal(settings, JsonFileMemoryStore(DATA_DIR / ".memory-store.json"))
    app = build_app(title="Shopware merchant API")
    shared_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope: FastAPI) -> AsyncIterator[None]:
        async with shared_lifespan(scope):
            try:
                await portal.backend.warm()
                logger.info(
                    "connected to %s via %s (%d listings)",
                    portal.backend.store_name,
                    portal.client.name,
                    len(portal.backend.catalog.cached()),
                )
            except Exception:
                logger.exception("could not read Shopware Admin at startup")
            try:
                yield
            finally:
                await portal.client.aclose()
                portal.backend.ledger.close()

    app.router.lifespan_context = lifespan
    app.include_router(portal.router, prefix="/api/merchant")
    app.include_router(portal.portal_router, prefix="/api/merchant")
    return app


app = create_app()
