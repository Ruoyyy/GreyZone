# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Mode detection: a maker-checker ACL split withholds the write tools on purpose, so their
absence is INFO; a missing read tool stays an ERROR."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from merchant.api.agent_tools import (
    MERCHANT_TOOLS,
    MODE_PLUGIN,
    TOOL_CHANGE_APPLY,
    TOOL_CHANGE_DISCARD,
    TOOL_CHANGE_LIST,
    MerchantAgentTools,
)


class FakeMcpAdmin:
    name = "mcp"

    def __init__(self, advertised: list[str]) -> None:
        self._advertised = advertised

    async def tool_names(self) -> list[str]:
        return self._advertised

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not called")


async def detect_with(advertised: list[str], caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    tools = MerchantAgentTools(FakeMcpAdmin(advertised), mode=MODE_PLUGIN)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="merchant.api.agent_tools"):
        assert await tools.detect() == MODE_PLUGIN
    return [(r.levelno, r.getMessage()) for r in caplog.records]


async def test_withheld_write_tools_are_not_an_error(caplog: pytest.LogCaptureFixture):
    advertised = [t for t in MERCHANT_TOOLS if t not in (TOOL_CHANGE_APPLY, TOOL_CHANGE_DISCARD)]
    records = await detect_with(advertised, caplog)
    assert not [m for level, m in records if level >= logging.WARNING]
    assert any(TOOL_CHANGE_APPLY in m and TOOL_CHANGE_DISCARD in m for _, m in records)


async def test_missing_read_tool_still_errors(caplog: pytest.LogCaptureFixture):
    advertised = [t for t in MERCHANT_TOOLS if t != TOOL_CHANGE_LIST]
    errors = [m for level, m in await detect_with(advertised, caplog) if level >= logging.ERROR]
    assert errors and TOOL_CHANGE_LIST in errors[0]
