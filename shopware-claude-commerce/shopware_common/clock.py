# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The host clock a session context carries.

``ClockContext`` (``commerce_common.types``) takes an IANA ``timezone`` and/or an aware
``now``; a naive ``datetime.now()`` is the server's wall clock with no zone, which is not
the user's. The hosts resolve the zone per request:

1. the browser's zone when the web app sends it (``X-Timezone`` header, or ``tz`` query
   parameter for links), validated against the IANA database;
2. otherwise ``HOST_TIMEZONE`` from the environment;
3. otherwise ``Europe/Berlin`` (the Docker shop's locale).

``host_clock(request)`` returns the keyword arguments for a ``ClockContext`` subclass:
``{"timezone": <zone>, "now": <aware datetime in that zone>}``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

TIMEZONE_HEADER = "X-Timezone"
TIMEZONE_QUERY = "tz"
TIMEZONE_ENV = "HOST_TIMEZONE"
DEFAULT_TIMEZONE = "Europe/Berlin"
_MAX_ZONE_LENGTH = 64


class RequestLike(Protocol):
    """The two things a Starlette ``Request`` offers that the clock reads."""

    @property
    def headers(self) -> Any: ...

    @property
    def query_params(self) -> Any: ...


def valid_zone(candidate: str | None) -> str | None:
    """``candidate`` when it names an IANA zone, else ``None``."""
    if not candidate:
        return None
    name = candidate.strip()
    if not name or len(name) > _MAX_ZONE_LENGTH:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None
    return name


def default_timezone() -> str:
    """``HOST_TIMEZONE`` when set and valid (a bad value is logged once per call and
    ignored), else ``Europe/Berlin``."""
    configured = os.environ.get(TIMEZONE_ENV, "")
    zone = valid_zone(configured)
    if zone is None and configured.strip():
        logger.warning(
            "%s=%r is not an IANA timezone; using %s", TIMEZONE_ENV, configured, DEFAULT_TIMEZONE
        )
    return zone or DEFAULT_TIMEZONE


def request_timezone(request: RequestLike | None) -> str:
    """The zone for one request: browser header, then query parameter, then the default."""
    if request is not None:
        for candidate in (
            request.headers.get(TIMEZONE_HEADER),
            request.query_params.get(TIMEZONE_QUERY),
        ):
            zone = valid_zone(candidate)
            if zone is not None:
                return zone
    return default_timezone()


def host_clock(request: RequestLike | None = None) -> dict[str, Any]:
    """``{"timezone", "now"}`` for a ``ClockContext``: the resolved zone and an aware
    ``now`` in it."""
    zone = request_timezone(request)
    return {"timezone": zone, "now": datetime.now(ZoneInfo(zone))}


__all__ = [
    "DEFAULT_TIMEZONE",
    "TIMEZONE_ENV",
    "TIMEZONE_HEADER",
    "TIMEZONE_QUERY",
    "default_timezone",
    "host_clock",
    "request_timezone",
    "valid_zone",
]
