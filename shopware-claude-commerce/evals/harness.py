# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""One case, one trial: build the snapshot state through the backend, run one agent
turn against the real model, record the outcome, apply the scorers.

The snapshot is built the way a conversation would have built it. ``seen_products`` go
through ``get_product_details`` (so variants enter provenance with their family), cart
lines through ``add_to_cart``, staged changes through the backend's own ``stage_*``
(so the server dry run and the ledger see them), approvals onto
``MerchantSessionState.approved_change_ids`` as the portal's approve route would set
them. Nothing is injected past the gates.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from anthropic import AsyncAnthropic

from commerce_common.memory import InMemoryMemoryStore
from commerce_common.types import MemoryCategory, MemoryFact
from merchant_agent import (
    InventoryActionItem,
    Listing,
    ListingDetails,
    MerchantSessionContext,
    MerchantSessionState,
    PriceUpdateItem,
    PromotionDraft,
    StagedChange,
)
from merchant_agent_runtime import MerchantAgent
from shopping_agent import PageContext, ShoppingSessionContext, ShoppingSessionState
from shopping_agent.serialization import cart_payload
from shopping_agent_runtime import ShoppingAgent

from .backends import MerchantHarness, ShoppingHarness
from .cases import Case, MerchantState, ShoppingState, resolve
from .judge import run_judge
from .overlay import OverlayMerchant, OverlayStorefront
from .scorers import (
    JudgeArgs,
    Outcome,
    ScoreResult,
    ToolCall,
    ToolResult,
    UiEvent,
    is_judge,
    score,
    validate_args,
)

logger = logging.getLogger(__name__)

TURN_TIMEOUT_S = 300.0
DISCLOSURE_COMPONENT = "disclosure"
# The session clock. ``host`` mirrors the deployments (``demo_common`` storefront host and
# the merchant portal both pass ``now=datetime.now()``, the server's naive local time, no
# timezone); ``none`` leaves the context without a clock, which is how the prompt then
# carries no ``local_time`` and the model falls back to its training-era year.
SESSION_CLOCK_ENV = "EVALS_SESSION_CLOCK"
SESSION_CLOCK_MODES = ("host", "none")


def session_now() -> datetime | None:
    """The ``now`` a snapshot's session context carries, per ``EVALS_SESSION_CLOCK``."""
    mode = os.environ.get(SESSION_CLOCK_ENV, "host").strip().lower()
    if mode not in SESSION_CLOCK_MODES:
        raise ValueError(f"{SESSION_CLOCK_ENV} must be one of {SESSION_CLOCK_MODES}, got {mode!r}")
    return datetime.now() if mode == "host" else None


class SnapshotError(RuntimeError):
    """The precondition could not be built (an id the backend does not know, a stage
    call the backend refused). A trial with this error is reported as ``setup_error``."""


class UnresolvedFixture(RuntimeError):
    """A ``$NAME`` the harness cannot resolve in this mode; the case is skipped."""


@dataclass
class TrialResult:
    case_id: str
    trial: int
    passed: bool
    scores: list[ScoreResult]
    outcome: Outcome | None
    usage: dict[str, int] = field(default_factory=dict)
    cache_hit_rate: float | None = None
    cost_usd: float | None = None
    elapsed_ms: int = 0
    error: str | None = None
    error_kind: str | None = None  # setup_error | turn_error | skipped
    model: str | None = None

    @property
    def judge_errors(self) -> int:
        return sum(1 for s in self.scores if s.judge_error)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "trial": self.trial,
            "passed": self.passed,
            "scores": [s.as_dict() for s in self.scores],
            "usage": self.usage,
            "cache_hit_rate": self.cache_hit_rate,
            "cost_usd": self.cost_usd,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
        }
        if self.error:
            data["error"] = self.error
            data["error_kind"] = self.error_kind
        if self.outcome is not None:
            data["text"] = self.outcome.text
            data["tool_calls"] = [
                {
                    "tool": c.tool,
                    "input": c.input,
                    "status": (r.status if (r := self.outcome.result_for(c)) else None),
                    "reason": (r.reason if r else None),
                }
                for c in self.outcome.tool_calls
            ]
            data["ui_components"] = self.outcome.components()
            data["cart_after"] = self.outcome.cart_after
            data["new_changes"] = self.outcome.new_changes()
        return data


def cache_hit_rate(usage: dict[str, int]) -> float | None:
    prompt = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
    )
    if prompt <= 0:
        return None
    return round(int(usage.get("cache_read_input_tokens") or 0) / prompt, 4)


def estimate_cost_usd(usage: dict[str, int], rates: dict[str, float] | None) -> float | None:
    """USD from per-million-token ``rates`` (``input``, ``output``, ``cache_write``,
    ``cache_read``); None when no rate card applies."""
    if not rates:
        return None
    per_m = 1_000_000
    cost = (
        int(usage.get("input_tokens") or 0) * rates.get("input", 0.0)
        + int(usage.get("output_tokens") or 0) * rates.get("output", 0.0)
        + int(usage.get("cache_creation_input_tokens") or 0) * rates.get("cache_write", 0.0)
        + int(usage.get("cache_read_input_tokens") or 0) * rates.get("cache_read", 0.0)
    ) / per_m
    return round(cost, 6)


# -- snapshot construction -------------------------------------------------------------


def _fixture_mapping(case: Case, fixture_ids: dict[str, str]) -> dict[str, str]:
    """The ``$NAME`` map for this case: fixture ids plus the ids of eval-only records,
    which are written literally and need no mapping. Raises :class:`UnresolvedFixture`
    for a name this mode cannot supply."""
    needed = case.placeholders() - case.aliases()
    missing = sorted(name for name in needed if name not in fixture_ids)
    if missing:
        raise UnresolvedFixture(
            f"fixture ids not available in this mode: {', '.join('$' + m for m in missing)}"
        )
    return dict(fixture_ids)


async def _seed_memory(store: InMemoryMemoryStore, subject: str, facts: list[Any]) -> None:
    if not facts:
        return
    await store.upsert_facts(
        subject,
        [
            MemoryFact(
                key=f.key,
                value=f.value,
                category=MemoryCategory(f.category),
                updated_at=datetime.now(UTC),
            )
            for f in facts
        ],
    )


async def build_shopping_snapshot(
    harness: ShoppingHarness,
    case: Case,
    trial: int,
    mapping: dict[str, str],
    memory: InMemoryMemoryStore,
) -> tuple[ShoppingSessionContext, ShoppingSessionState, dict[str, Any]]:
    assert isinstance(case.state, ShoppingState)
    spec: ShoppingState = ShoppingState.model_validate(resolve(case.state.model_dump(), mapping))
    page = PageContext(**spec.page.model_dump()) if spec.page else PageContext()
    session = ShoppingSessionContext(
        session_id=f"eval-{case.id}-t{trial}-{int(time.time() * 1000) % 100000}",
        user_id=f"eval-user-{case.id}",
        page=page,
        now=session_now(),
    )
    state = ShoppingSessionState()
    backend = harness.backend
    for query in spec.searched:
        state.remember_products(await backend.search_products(session, query, None, 8))
    for product_id in spec.seen_products:
        details = await backend.get_product_details(session, product_id)
        if details is None:
            raise SnapshotError(f"seen_products: backend knows no product {product_id}")
        state.remember_products([details, *details.variants])
    for line in spec.cart:
        if line.product_id not in state.seen_products:
            details = await backend.get_product_details(session, line.product_id)
            if details is None:
                raise SnapshotError(f"cart: backend knows no product {line.product_id}")
            state.remember_products([details, *details.variants])
        try:
            await backend.add_to_cart(session, line.product_id, line.quantity)
        except Exception as error:  # noqa: BLE001
            raise SnapshotError(f"cart: add_to_cart({line.product_id}) failed: {error}") from error
    await _seed_memory(memory, session.user_id, spec.memory)
    cart_before = cart_payload(await backend.get_cart(session))
    return session, state, cart_before


def _search_shape(details: ListingDetails) -> Listing:
    return Listing(
        **details.model_dump(
            exclude={
                "long_description",
                "review_snippets",
                "sales_last_30d",
                "return_rate_pct",
                "missing_attributes",
                "variants",
            }
        )
    )


async def _stage(
    harness: MerchantHarness, session: MerchantSessionContext, spec: Any
) -> StagedChange:
    backend = harness.backend
    try:
        if spec.kind == "price_update":
            return await backend.stage_price_update(
                session, [PriceUpdateItem.model_validate(i) for i in spec.items], spec.note
            )
        if spec.kind == "inventory_action":
            return await backend.stage_inventory_action(
                session, [InventoryActionItem.model_validate(i) for i in spec.items], spec.note
            )
        if spec.kind == "listing_update":
            return await backend.stage_listing_update(
                session, spec.listing_id, spec.fields, spec.note
            )
        return await backend.stage_promotion(session, PromotionDraft.model_validate(spec.promotion))
    except Exception as error:  # noqa: BLE001
        raise SnapshotError(
            f"staged ${spec.alias} ({spec.kind}) refused by the backend: {error}"
        ) from error


def _change_records(changes: list[StagedChange]) -> dict[str, dict[str, Any]]:
    return {c.change_id: c.model_dump(mode="json", exclude_none=True) for c in changes}


async def build_merchant_snapshot(
    harness: MerchantHarness,
    case: Case,
    trial: int,
    mapping: dict[str, str],
    memory: InMemoryMemoryStore,
) -> tuple[MerchantSessionContext, MerchantSessionState, dict[str, str], dict[str, dict[str, Any]]]:
    assert isinstance(case.state, MerchantState)
    spec: MerchantState = MerchantState.model_validate(resolve(case.state.model_dump(), mapping))
    session = MerchantSessionContext(
        session_id=f"eval-{case.id}-t{trial}-{int(time.time() * 1000) % 100000}",
        merchant_id=harness.merchant_id,
        operator=harness.operator,
        now=session_now(),
    )
    state = MerchantSessionState()
    backend = harness.backend
    for listing_id in spec.seen_listings:
        details = await backend.get_listing(session, listing_id)
        if details is None:
            raise SnapshotError(f"seen_listings: backend knows no listing {listing_id}")
        state.remember_listings([_search_shape(details)])
    for listing_id in spec.read_listings:
        details = await backend.get_listing(session, listing_id)
        if details is None:
            raise SnapshotError(f"read_listings: backend knows no listing {listing_id}")
        state.remember_listing_record(details)
    aliases: dict[str, str] = {}
    for staged in spec.staged:
        change = await _stage(harness, session, staged)
        state.remember_change(change)
        aliases[staged.alias] = change.change_id
    for alias in spec.approved:
        state.approved_change_ids.add(aliases[alias])
    for alias in spec.discarded:
        discarded = await backend.discard_change(session, aliases[alias])
        state.remember_change(discarded)
    if spec.latest_snapshot:
        state.remember_snapshot(await backend.get_business_snapshot(session))
    await _seed_memory(memory, session.merchant_id, spec.memory)
    return session, state, aliases, _change_records(harness.all_changes())


# -- running a turn ------------------------------------------------------------------------


def _result_texts(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Full tool result text by ``tool_use_id`` from the conversation the turn wrote."""
    texts: dict[str, str] = {}
    for message in messages:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                texts[str(block.get("tool_use_id"))] = str(body or "")
    return texts


def collect_outcome(suite: str, events: list[Any], messages: list[dict[str, Any]]) -> Outcome:
    outcome = Outcome(suite=suite)
    texts = _result_texts(messages)
    for event in events:
        data = event.data
        if event.type == "text_delta":
            outcome.text += data.get("text", "")
        elif event.type == "tool_call":
            outcome.tool_calls.append(
                ToolCall(tool=data["tool"], id=data["id"], input=dict(data.get("input") or {}))
            )
        elif event.type == "tool_result":
            outcome.tool_results.append(
                ToolResult(
                    tool=data["tool"],
                    id=data["id"],
                    status=data.get("status") or ("error" if data.get("is_error") else "ok"),
                    reason=data.get("reason"),
                    summary=str(data.get("summary") or ""),
                    text=texts.get(data["id"], str(data.get("summary") or "")),
                )
            )
        elif event.type == "ui":
            outcome.ui.append(
                UiEvent(component=data["component"], payload=dict(data.get("payload") or {}))
            )
        elif event.type == "turn_complete":
            outcome.usage = dict(data.get("usage") or {})
            outcome.elapsed_ms = int(data.get("elapsed_ms") or 0)
            outcome.stop_reason = data.get("stop_reason")
        elif event.type == "error":
            outcome.error = str(data.get("message"))
    return outcome


def _agent_messages(case: Case, mapping: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    history = resolve([m.model_dump() for m in case.history], mapping)
    message = resolve(case.message, mapping)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})
    user_texts = [m["content"] for m in history if m["role"] == "user"] + [message]
    return messages, user_texts


async def run_shopping_trial(
    harness: ShoppingHarness,
    case: Case,
    trial: int,
    *,
    client: AsyncAnthropic,
    model: str | None,
    judge_model: str,
    rates: dict[str, float] | None,
) -> TrialResult:
    mapping = _fixture_mapping(case, harness.fixture_ids)
    memory = InMemoryMemoryStore()
    backend = harness.backend
    if not case.fixtures.empty:
        backend = OverlayStorefront(backend, case.fixtures.products, case.fixtures.policies)
        harness = ShoppingHarness(
            backend, harness.config, harness.skills_dir, harness.fixture_ids, harness.mode
        )
    config = harness.config.model_copy(update={"model": model}) if model else harness.config
    agent = ShoppingAgent(
        backend=backend,
        skills_dir=harness.skills_dir,
        config=config,
        memory_store=memory,
        client=client,
    )
    session, state, cart_before = await build_shopping_snapshot(
        harness, case, trial, mapping, memory
    )
    messages, user_texts = _agent_messages(case, mapping)
    started = time.monotonic()
    events = [event async for event in agent.stream_turn(messages, session, state)]
    outcome = collect_outcome("shopping", events, messages)
    outcome.user_texts = user_texts
    outcome.cart_before = cart_before
    outcome.cart_after = cart_payload(await backend.get_cart(session))
    for event in outcome.ui:
        if event.component == DISCLOSURE_COMPONENT and (pid := event.payload.get("product_id")):
            disclosure = await backend.get_disclosure(session, str(pid))
            if disclosure is not None:
                outcome.server_disclosures[str(pid)] = disclosure.model_dump(exclude_none=True)
    return await _score(
        case, trial, outcome, mapping, client, judge_model, rates, config.model, started
    )


async def run_merchant_trial(
    harness: MerchantHarness,
    case: Case,
    trial: int,
    *,
    client: AsyncAnthropic,
    model: str | None,
    judge_model: str,
    rates: dict[str, float] | None,
) -> TrialResult:
    mapping = _fixture_mapping(case, harness.fixture_ids)
    memory = InMemoryMemoryStore()
    backend = harness.backend
    if not case.fixtures.empty:
        backend = OverlayMerchant(backend, case.fixtures.listings, case.fixtures.order_issues)
        harness = MerchantHarness(
            backend,
            harness.config,
            harness.skills_dir,
            harness.fixture_ids,
            harness.merchant_id,
            harness.operator,
            harness.all_changes,
            harness.mode,
        )
    config = harness.config.model_copy(update={"model": model}) if model else harness.config
    agent = MerchantAgent(
        backend=backend,
        skills_dir=harness.skills_dir,
        config=config,
        memory_store=memory,
        client=client,
    )
    session, state, aliases, changes_before = await build_merchant_snapshot(
        harness, case, trial, mapping, memory
    )
    full_mapping = {**mapping, **aliases}
    messages, user_texts = _agent_messages(case, full_mapping)
    started = time.monotonic()
    events = [event async for event in agent.stream_turn(messages, session, state)]
    outcome = collect_outcome("merchant", events, messages)
    outcome.user_texts = user_texts
    outcome.aliases = aliases
    outcome.changes_before = changes_before
    outcome.changes_after = _change_records(harness.all_changes())
    return await _score(
        case, trial, outcome, full_mapping, client, judge_model, rates, config.model, started
    )


async def _score(
    case: Case,
    trial: int,
    outcome: Outcome,
    mapping: dict[str, str],
    client: AsyncAnthropic,
    judge_model: str,
    rates: dict[str, float] | None,
    model: str,
    started: float,
) -> TrialResult:
    scores: list[ScoreResult] = []
    for expectation in case.expect:
        args = resolve(expectation.args, mapping)
        if is_judge(expectation.scorer):
            judge_args = validate_args(expectation.scorer, args)
            assert isinstance(judge_args, JudgeArgs)
            scores.append(await run_judge(outcome, judge_args, client, judge_model))
        else:
            scores.append(score(outcome, expectation.scorer, args))
    passed = all(s.passed for s in scores) and outcome.error is None
    return TrialResult(
        case_id=case.id,
        trial=trial,
        passed=passed,
        scores=scores,
        outcome=outcome,
        usage=outcome.usage,
        cache_hit_rate=cache_hit_rate(outcome.usage),
        cost_usd=estimate_cost_usd(outcome.usage, rates),
        elapsed_ms=outcome.elapsed_ms or int((time.monotonic() - started) * 1000),
        error=outcome.error,
        error_kind="turn_error" if outcome.error else None,
        model=model,
    )


def dump_outcome(outcome: Outcome) -> str:  # for --verbose console output
    return json.dumps(
        {
            "text": outcome.text,
            "tool_calls": [(c.tool, c.input) for c in outcome.tool_calls],
            "results": [(r.tool, r.status, r.reason) for r in outcome.tool_results],
            "ui": outcome.components(),
            "cart_after": outcome.cart_after,
            "new_changes": outcome.new_changes(),
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    )
