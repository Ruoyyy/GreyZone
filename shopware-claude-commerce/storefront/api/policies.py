# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Keyword policy index over the shop's own pages: the Store API footer and service
navigation (CMS pages seeded by ``docker/seed_catalog.py``: Widerruf, Versand, AGB,
Datenschutz, Kontakt) plus ``/agents.md`` and ``/llms.txt``. A static fallback copy is
used only when the shop exposes nothing."""

from __future__ import annotations

import logging
import re

from shopping_agent import Policy, ShoppingSessionContext

from .store_api import StoreApiClient, StoreApiError

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
MAX_POLICY_CHARS = 4000
_CATEGORY_NEEDLES = {
    "returns": ("widerruf", "rückgabe", "retoure", "return"),
    "shipping": ("versand", "liefer", "shipping"),
    "terms": ("agb", "geschäftsbedingungen", "terms"),
    "privacy": ("datenschutz", "privacy"),
    "contact": ("kontakt", "contact", "impressum"),
}
_FALLBACK = [
    Policy(
        policy_id="returns",
        title="Widerruf und Rückgabe",
        category="returns",
        content=(
            "Verbraucher haben ein gesetzliches Widerrufsrecht von 14 Tagen. "
            "Die Widerrufsfrist beginnt, sobald die Ware beim Kunden eingegangen ist. "
            "Details stehen in der Widerrufsbelehrung im Shop-Footer."
        ),
    ),
    Policy(
        policy_id="shipping",
        title="Versand und Lieferzeit",
        category="shipping",
        content=(
            "Lieferzeiten stehen am Produkt (deliveryTime). Versandkosten werden im "
            "Shopware-Checkout berechnet. Lieferungen nach Österreich nutzen die "
            "gleichen Versandarten, sofern die Versandart das Land abdeckt."
        ),
    ),
    Policy(
        policy_id="vat",
        title="Preise und MwSt.",
        category="pricing",
        content="Alle Storefront-Preise sind Bruttopreise inklusive gesetzlicher MwSt., sofern nicht anders gekennzeichnet.",
    ),
]


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


class PolicyIndex:
    def __init__(self, store_api: StoreApiClient) -> None:
        self._store_api = store_api
        self._policies: list[Policy] | None = None

    @property
    def live(self) -> bool:
        """True once the index holds shop-authored pages rather than the fallback copy."""
        fallback_ids = {policy.policy_id for policy in _FALLBACK}
        return any(policy.policy_id not in fallback_ids for policy in self._policies or [])

    async def rebuild(self) -> list[Policy]:
        """Walk the footer and service navigation of the sales channel, read every CMS
        page behind it, then append ``/agents.md`` and ``/llms.txt``. The static fallback
        copy is used only when the shop exposes no page at all."""
        policies: list[Policy] = []
        seen: set[str] = set()
        for name in ("footer-navigation", "service-navigation"):
            try:
                tree = await self._store_api.navigation(name)
            except StoreApiError as error:
                logger.warning("policy index: navigation %s unavailable: %s", name, error)
                continue
            await self._walk(tree, policies, seen)
        for path, title in (("/agents.md", "agents.md"), ("/llms.txt", "llms.txt")):
            text = await self._store_api.text_file(path)
            if text.strip():
                policies.append(
                    Policy(
                        policy_id=path.strip("/").replace(".", "-"),
                        title=title,
                        content=text[:MAX_POLICY_CHARS],
                    )
                )
        if not policies:
            logger.warning(
                "policy index: the shop exposes no policy pages; using the fallback copy"
            )
        self._policies = policies or list(_FALLBACK)
        return self._policies

    async def _walk(self, nodes: list[dict], policies: list[Policy], seen: set[str]) -> None:
        for node in nodes:
            category_id = node.get("id") or node.get("categoryId")
            name = node.get("translated", {}).get("name") or node.get("name") or "Policy"
            if category_id and str(category_id) not in seen:
                seen.add(str(category_id))
                try:
                    record = await self._store_api.category(str(category_id))
                except StoreApiError as error:
                    logger.info("policy index: category %s skipped: %s", category_id, error)
                    record = {}
                cms = record.get("cmsPage") or record.get("slotConfig") or {}
                text = _extract_cms_text(cms) or _strip(
                    str(
                        record.get("translated", {}).get("description")
                        or record.get("description")
                        or ""
                    )
                )
                if text:
                    policies.append(
                        Policy(
                            policy_id=str(category_id),
                            title=str(name),
                            category=_category_of(str(name)),
                            content=text[:MAX_POLICY_CHARS],
                        )
                    )
            children = node.get("children") or node.get("elements") or []
            if children:
                await self._walk(children, policies, seen)

    async def search(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        if self._policies is None:
            await self.rebuild()
        assert self._policies is not None
        tokens = {t.lower() for t in re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", query)}
        if not tokens:
            return self._policies[:5]
        scored: list[tuple[int, Policy]] = []
        for policy in self._policies:
            hay = f"{policy.title} {policy.content}".lower()
            score = sum(1 for token in tokens if token in hay)
            if score:
                scored.append((score, policy))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [policy for _, policy in scored[:8]] or self._policies[:3]


def _extract_cms_text(cms: dict) -> str:
    """Text of a Shopware CMS page: ``sections[].blocks[].slots[]`` carry their copy in
    ``config.content.value`` (``{"source": "static", "value": "<p>…</p>"}``), resolved
    slots in ``data.content``; ``translated.config`` mirrors the former."""
    chunks: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, dict) and isinstance(content.get("value"), str):
                chunks.append(_strip(content["value"]))  # slot config
            data = value.get("data")
            if isinstance(data, dict) and isinstance(data.get("content"), str):
                chunks.append(_strip(data["content"]))  # resolved slot data
            for key, child in value.items():
                if key in {"content", "data"} and isinstance(child, dict):
                    continue
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(cms)
    unique: list[str] = []
    for chunk in chunks:
        if chunk and chunk not in unique:
            unique.append(chunk)
    return " ".join(unique)


def _category_of(title: str) -> str | None:
    lowered = title.lower()
    for category, needles in _CATEGORY_NEEDLES.items():
        if any(needle in lowered for needle in needles):
            return category
    return None


def policy_from_tool_row(row: dict) -> Policy:
    """A ``shopping-policy-search`` row (``SwagCommerceAgentTools``) as the blueprint's
    ``Policy``. The plugin's ``category`` names the navigation the page came from
    (``footer-navigation`` / ``service-navigation`` / ``landing-page``); the host keeps its
    topical category (returns / shipping / …) derived from the title, so the model sees the
    same vocabulary on both paths, and falls back to the plugin's when the title says
    nothing."""
    title = str(row.get("title") or row.get("policy_id") or "Policy")
    return Policy(
        policy_id=str(row.get("policy_id") or title),
        title=title,
        category=_category_of(title) or (str(row["category"]) if row.get("category") else None),
        content=str(row.get("content") or "")[:MAX_POLICY_CHARS],
    )
