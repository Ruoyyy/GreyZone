# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Host side of the checkout handoff (ADR-10).

The cart the agent builds *is* a Shopware context token, and the browser cannot set
``sw-context-token`` on the shop origin. The token must never travel in a URL either, so
the cart payload carries an opaque, session-bound **ticket URL** on this host. When the
customer clicks it, the host mints a one-time, ≤ 120 s, HMAC-signed handoff code
(:mod:`shopware_common.handoff`) that encrypts the token and answers a page that
auto-submits it as ``POST /claude-commerce/continue`` to the ``CommerceAgentsHandoff``
plugin, which verifies, migrates the session, adopts the token and lands on
``/checkout/confirm``. The plugin also accepts ``GET …?code=`` as a no-JS fallback; the
page's link does the same, still with the code only.
"""

from __future__ import annotations

import html
import logging
import os
import secrets
from urllib.parse import quote

from shopware_common.handoff import HandoffCode, HandoffCodeIssuer, secret_from_env

logger = logging.getLogger(__name__)

CONTINUE_PATH = "/claude-commerce/continue"
TICKET_ROUTE = "/api/checkout/handoff"
PUBLIC_URL_ENV = "STOREFRONT_API_PUBLIC_URL"
DEFAULT_PUBLIC_URL = "http://localhost:8004"
_TICKET_BYTES = 24


def public_url_from_env() -> str:
    return os.environ.get(PUBLIC_URL_ENV, DEFAULT_PUBLIC_URL).rstrip("/")


class HandoffBroker:
    """Session → ticket mapping plus code minting. Tickets are random, bound to one
    session, and revoked whenever the cart binding changes or the session resets."""

    def __init__(
        self, shop_url: str, *, public_url: str | None = None, secret: str | None = None
    ) -> None:
        self.shop_url = shop_url.rstrip("/")
        self.public_url = (public_url or public_url_from_env()).rstrip("/")
        resolved = secret if secret is not None else secret_from_env()
        self._issuer = HandoffCodeIssuer(resolved) if resolved else None
        if self._issuer is None:
            logger.warning(
                "COMMERCE_AGENTS_HANDOFF_SECRET is not set; checkout handoff is disabled "
                "(run docker/bootstrap.sh, which writes it to docker/.generated.env)"
            )
        self._ticket_of: dict[str, str] = {}
        self._session_of: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return self._issuer is not None

    @property
    def continue_endpoint(self) -> str:
        return f"{self.shop_url}{CONTINUE_PATH}"

    def ticket_for(self, session_id: str) -> str:
        ticket = self._ticket_of.get(session_id)
        if ticket is None:
            ticket = secrets.token_urlsafe(_TICKET_BYTES)
            self._ticket_of[session_id] = ticket
            self._session_of[ticket] = session_id
        return ticket

    def session_for(self, ticket: str) -> str | None:
        return self._session_of.get(ticket)

    def revoke(self, session_id: str) -> None:
        ticket = self._ticket_of.pop(session_id, None)
        if ticket is not None:
            self._session_of.pop(ticket, None)

    def continue_url(self, session_id: str) -> str | None:
        if not self.configured:
            return None
        return f"{self.public_url}{TICKET_ROUTE}/{self.ticket_for(session_id)}"

    def mint(self, context_token: str) -> HandoffCode:
        if self._issuer is None:
            raise RuntimeError("handoff secret is not configured")
        return self._issuer.issue(context_token)

    def page(self, code: HandoffCode) -> str:
        """An HTML page that POSTs the code to the plugin on load; the visible link is the
        GET fallback (same code, still never the raw token)."""
        action = html.escape(self.continue_endpoint, quote=True)
        escaped = html.escape(code.code, quote=True)
        fallback = f"{self.continue_endpoint}?code={quote(code.code, safe='')}"
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer">'
            "<title>Continuing to checkout…</title>"
            "<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;"
            "min-height:100vh;margin:0;color:#1f2937}a{color:#189EFF}</style></head>"
            '<body><form id="handoff" method="post" action="' + action + '">'
            '<input type="hidden" name="code" value="' + escaped + '">'
            "<p>Taking you to the Shopware checkout… "
            '<noscript><a href="' + html.escape(fallback, quote=True) + '">Continue</a></noscript>'
            "</p></form>"
            "<script>document.getElementById('handoff').submit();</script>"
            "</body></html>"
        )
