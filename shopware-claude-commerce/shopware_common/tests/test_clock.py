# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The host clock: browser zone first, then ``HOST_TIMEZONE``, then Europe/Berlin; the
``now`` it yields is aware and in that zone; garbage never becomes a zone."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from commerce_common.types import ClockContext
from shopware_common.clock import (
    DEFAULT_TIMEZONE,
    TIMEZONE_ENV,
    TIMEZONE_HEADER,
    TIMEZONE_QUERY,
    default_timezone,
    host_clock,
    request_timezone,
    valid_zone,
)


def _request(headers: dict[str, str] | None = None, query: dict[str, str] | None = None):
    return SimpleNamespace(headers=headers or {}, query_params=query or {})


def test_the_default_is_berlin_without_configuration(monkeypatch):
    monkeypatch.delenv(TIMEZONE_ENV, raising=False)
    assert default_timezone() == DEFAULT_TIMEZONE == "Europe/Berlin"
    assert request_timezone(None) == "Europe/Berlin"


def test_host_timezone_env_sets_the_default_and_a_bad_value_is_ignored(monkeypatch):
    monkeypatch.setenv(TIMEZONE_ENV, "America/New_York")
    assert default_timezone() == "America/New_York"
    monkeypatch.setenv(TIMEZONE_ENV, "Mars/Olympus")
    assert default_timezone() == DEFAULT_TIMEZONE


def test_the_browser_header_wins_over_query_and_env(monkeypatch):
    monkeypatch.setenv(TIMEZONE_ENV, "Europe/Berlin")
    request = _request({TIMEZONE_HEADER: "Asia/Tokyo"}, {TIMEZONE_QUERY: "Europe/London"})
    assert request_timezone(request) == "Asia/Tokyo"
    assert request_timezone(_request(query={TIMEZONE_QUERY: "Europe/London"})) == "Europe/London"


def test_an_unknown_zone_in_the_request_falls_through_to_the_default(monkeypatch):
    monkeypatch.setenv(TIMEZONE_ENV, "Europe/Berlin")
    assert request_timezone(_request({TIMEZONE_HEADER: "Not/AZone"})) == "Europe/Berlin"
    assert valid_zone("") is None
    assert valid_zone("x" * 200) is None
    assert valid_zone("../etc/passwd") is None


def test_host_clock_yields_an_aware_now_in_the_resolved_zone(monkeypatch):
    monkeypatch.delenv(TIMEZONE_ENV, raising=False)
    clock = host_clock(_request({TIMEZONE_HEADER: "America/Los_Angeles"}))
    assert clock["timezone"] == "America/Los_Angeles"
    now: datetime = clock["now"]
    assert now.tzinfo is not None and now.utcoffset() is not None
    assert now.tzinfo == ZoneInfo("America/Los_Angeles")
    # It is a valid ClockContext, and local_now() is the aware value, not the server's clock.
    context = ClockContext(**clock)
    assert context.local_now() == now
