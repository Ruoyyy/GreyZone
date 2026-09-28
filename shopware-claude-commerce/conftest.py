# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The checked-out repository is not what these tests are written against.

Host modules load the ``.env`` beside them into the process for real: importing
``storefront.api.main`` does it during collection, and the MCP servers do it again when a
test boots one. A suite run in a checkout whose ``.env`` repoints the models or turns on a
range switch therefore leaves those set for tests that never asked for them — the Managed
Agent manifests, which name Claude's models and are deployed through the Agents API rather
than through this host, get compared against the deployment; and the handoff tests, which
pin the upstream behavior, get the range's instead.

Two hooks close that: collection clears what importing a host leaked, and the per-test
fixture restores the environment a test started with, so a loader run inside one test
cannot reach the next."""

from __future__ import annotations

import os

import pytest

#: The variables a checkout's ``.env`` uses to repoint the agents at another provider.
_DEPLOYMENT_MODEL_VARS = ("SHOPPING_MODEL", "MERCHANT_MODEL", "MEMORY_MODEL", "THINKING_EFFORT")
#: Switches this repository's own range variant reads, which the upstream tests predate.
_RANGE_SWITCH_VARS = (
    "AGENT_PAYMENT_MODE",
    "ENABLE_MAIL_TOOL",
    "MERCHANT_PRICE_DELTA_CAP",
    "MERCHANT_MAX_PROMOTION_DISCOUNT_PCT",
    "ENABLE_INVOICE_TOOL",
    "ENABLE_MERCHANT_MAIL_TOOL",
    "ENABLE_REFUND_TOOL",
)


def pytest_collection_finish(session) -> None:
    for name in (*_DEPLOYMENT_MODEL_VARS, *_RANGE_SWITCH_VARS):
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _restore_process_environment():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
