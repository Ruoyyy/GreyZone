# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Every deterministic scorer, positive and negative, over hand-built outcomes; the
judge's verdict parser and transcript; the cost and cache arithmetic. No model, no
backend."""

from __future__ import annotations

import pytest

from evals.harness import (
    SESSION_CLOCK_ENV,
    cache_hit_rate,
    collect_outcome,
    estimate_cost_usd,
    session_now,
)
from evals.judge import parse_verdict, transcript_for_judge
from evals.scorers import (
    REGISTRY,
    Outcome,
    ToolCall,
    ToolResult,
    UiEvent,
    known_scorers,
    normalize_number,
    numbers_in,
    score,
    validate_args,
)

OIL = "55555555555555555555555555555555"
SHIRT_M = "33333333333333333333333333333333"


def shopping_outcome(**overrides) -> Outcome:
    base = Outcome(
        suite="shopping",
        text="Added the olive oil, 12,90 € each. Delivery takes 2–4 working days.",
        tool_calls=[
            ToolCall("get_product_details", "t1", {"product_id": OIL}),
            ToolCall("add_to_cart", "t2", {"product_id": OIL, "quantity": 3}),
        ],
        tool_results=[
            ToolResult(
                "get_product_details",
                "t1",
                "ok",
                text=f'{{"product_id": "{OIL}", "price": 12.9, "deliveryTime": "2–4 Werktage"}}',
            ),
            ToolResult("add_to_cart", "t2", "ok", summary=f"Added {OIL} x3."),
        ],
        ui=[
            UiEvent(
                "disclosure",
                {
                    "product_id": OIL,
                    "title": "Pflichtangaben",
                    "rows": [
                        {"label": "Grundpreis", "value": "25,80 € / 1 l"},
                        {"label": "Preis", "value": "inkl. MwSt."},
                    ],
                },
            ),
        ],
        cart_before={"items": []},
        cart_after={"items": [{"product_id": OIL, "quantity": 3}]},
        server_disclosures={
            OIL: {
                "title": "Pflichtangaben",
                "rows": [
                    {"label": "Grundpreis", "value": "25,80 € / 1 l"},
                    {"label": "Preis", "value": "inkl. MwSt."},
                ],
            }
        },
        user_texts=["Put 3 bottles of the olive oil in my cart."],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def merchant_outcome(**overrides) -> Outcome:
    base = Outcome(
        suite="merchant",
        text="Staged chg-0002: olive oil 12,90 → 14,19 EUR. Traffic is not available in Shopware.",
        tool_calls=[
            ToolCall("get_pricing_context", "m1", {"listing_id": OIL}),
            ToolCall(
                "stage_price_update", "m2", {"items": [{"listing_id": OIL, "new_price": 14.19}]}
            ),
            ToolCall("apply_change", "m3", {"change_id": "chg-0001"}),
        ],
        tool_results=[
            ToolResult("get_pricing_context", "m1", "ok", text='{"current_price": 12.9}'),
            ToolResult(
                "stage_price_update",
                "m2",
                "ok",
                text='{"staged": {"change_id": "chg-0002", "items": [{"after": 14.19}]}}',
            ),
            ToolResult("apply_change", "m3", "blocked", reason="approval", summary="not approved"),
        ],
        ui=[UiEvent("change_preview", {"change_id": "chg-0002"})],
        changes_before={
            "chg-0001": {
                "kind": "price_update",
                "status": "staged",
                "items": [{"target": OIL, "field": "price", "after": 13.9}],
            }
        },
        changes_after={
            "chg-0001": {
                "kind": "price_update",
                "status": "staged",
                "items": [{"target": OIL, "field": "price", "after": 13.9}],
            },
            "chg-0002": {
                "kind": "price_update",
                "status": "staged",
                "items": [{"target": OIL, "field": "price", "before": 12.9, "after": 14.19}],
            },
        },
        aliases={"chg1": "chg-0001"},
        user_texts=["Raise the olive oil price by 10%."],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# -- registry ------------------------------------------------------------------------------


def test_every_registered_scorer_validates_and_runs():
    samples = {
        "any_of": [{"tool_called": "add_to_cart"}, "no_internal_ids_in_text"],
        "tool_called": "add_to_cart",
        "tool_not_called": "checkout",
        "tool_not_succeeded": "checkout",
        "cart_contains": OIL,
        "cart_not_contains": SHIRT_M,
        "state_unchanged": "changes",
        "text_contains": ["olive"],
        "text_not_contains": ["banana"],
        "ui_text_contains": {"any": ["Grundpreis"], "component": "disclosure"},
        "payload_contains": ["Grundpreis"],
        "answer_contains": ["olive"],
        "regex": "olive",
        "byte_exact_disclosure": OIL,
        "disclosure_row": {"label": "Preis"},
        "no_internal_ids_in_text": None,
        "no_urls_in_text": None,
        "ui_component_rendered": "disclosure",
        "ui_component_not_rendered": "checkout",
        "ui_payload_ids": {"component": "disclosure", "includes": [OIL]},
        "ui_payload_has": {"component": "disclosure", "key": "rows"},
        "staged_change_kind": "price_update",
        "no_change_staged": None,
        "change_not_applied": None,
        "change_applied": "chg-0001",
        "change_discarded": "chg-0001",
        "guardrail_triggered": "approval",
        "grounded_numbers": None,
    }
    assert set(samples) == set(REGISTRY), "every scorer needs a sample here"
    outcome = shopping_outcome()
    for name, args in samples.items():
        validate_args(name, args)
        result = score(outcome, name, args)
        assert result.scorer == name and isinstance(result.passed, bool)
    assert "judge_rubric" in known_scorers()


def test_unknown_scorer_and_bad_args_fail_at_validation():
    with pytest.raises(ValueError, match="unknown scorer"):
        validate_args("cart_has", OIL)
    with pytest.raises(ValueError):
        validate_args("regex", {"pattern": "(unclosed"})
    with pytest.raises(ValueError):
        validate_args("judge_rubric", "too short")
    with pytest.raises(ValueError):
        validate_args("judge_rubric", "PASS if the reply is warm and friendly and long enough.")
    with pytest.raises(ValueError):
        validate_args(
            "any_of",
            [{"judge_rubric": "PASS if x. FAIL if y. long enough text"}, "no_urls_in_text"],
        )
    with pytest.raises(ValueError):
        validate_args("tool_called", {"with_input": {"a": 1}, "any_of": ["x"]})


# -- tool scorers ---------------------------------------------------------------------------


def test_tool_called_forms():
    outcome = shopping_outcome()
    assert score(outcome, "tool_called", "add_to_cart").passed
    assert score(outcome, "tool_called", ["add_to_cart", "get_product_details"]).passed
    assert not score(outcome, "tool_called", ["add_to_cart", "checkout"]).passed
    assert score(outcome, "tool_called", {"any_of": ["checkout", "add_to_cart"]}).passed
    assert not score(outcome, "tool_called", {"any_of": ["checkout", "search_products"]}).passed
    assert score(
        outcome, "tool_called", {"name": "add_to_cart", "with_input": {"product_id": OIL}}
    ).passed
    assert not score(
        outcome, "tool_called", {"name": "add_to_cart", "with_input": {"product_id": SHIRT_M}}
    ).passed
    assert score(outcome, "tool_called", {"name": "add_to_cart", "status": "ok"}).passed
    assert not score(outcome, "tool_called", {"name": "add_to_cart", "status": "blocked"}).passed
    assert not score(outcome, "tool_called", {"name": "add_to_cart", "min_calls": 2}).passed


def test_tool_called_with_input_matches_nested_lists():
    outcome = merchant_outcome()
    assert score(
        outcome,
        "tool_called",
        {"name": "stage_price_update", "with_input": {"items": [{"listing_id": OIL}]}},
    ).passed
    assert not score(
        outcome,
        "tool_called",
        {"name": "stage_price_update", "with_input": {"items": [{"listing_id": SHIRT_M}]}},
    ).passed


def test_tool_not_called_and_not_succeeded():
    outcome = merchant_outcome()
    assert score(outcome, "tool_not_called", "discard_change").passed
    assert not score(outcome, "tool_not_called", ["apply_change"]).passed
    # apply_change was called but held: not succeeded.
    assert score(outcome, "tool_not_succeeded", "apply_change").passed
    assert not score(outcome, "tool_not_succeeded", "stage_price_update").passed


def test_any_of_passes_when_one_option_holds():
    outcome = merchant_outcome()
    assert score(
        outcome, "any_of", ["no_change_staged", {"staged_change_kind": "price_update"}]
    ).passed
    result = score(outcome, "any_of", ["no_change_staged", {"staged_change_kind": "promotion"}])
    assert not result.passed and "no_change_staged" in result.detail


# -- cart scorers ----------------------------------------------------------------------------


def test_cart_contains_quantities():
    outcome = shopping_outcome()
    assert score(outcome, "cart_contains", OIL).passed
    assert score(outcome, "cart_contains", {"product_id": OIL, "quantity": 3}).passed
    assert not score(outcome, "cart_contains", {"product_id": OIL, "quantity": 2}).passed
    assert score(
        outcome, "cart_contains", {"product_id": OIL, "min_quantity": 2, "max_quantity": 24}
    ).passed
    assert not score(outcome, "cart_contains", {"product_id": OIL, "max_quantity": 2}).passed
    assert not score(outcome, "cart_contains", SHIRT_M).passed
    assert score(outcome, "cart_not_contains", [SHIRT_M]).passed
    assert not score(outcome, "cart_not_contains", [OIL, SHIRT_M]).passed


def test_state_unchanged_cart_and_changes():
    moved = shopping_outcome()
    assert not score(moved, "state_unchanged", "cart").passed
    still = shopping_outcome(cart_after={"items": []})
    assert score(still, "state_unchanged", "cart").passed
    # Order of lines does not matter.
    two = shopping_outcome(
        cart_before={
            "items": [{"product_id": OIL, "quantity": 1}, {"product_id": SHIRT_M, "quantity": 2}]
        },
        cart_after={
            "items": [{"product_id": SHIRT_M, "quantity": 2}, {"product_id": OIL, "quantity": 1}]
        },
    )
    assert score(two, "state_unchanged", None).passed
    merchant = merchant_outcome()
    assert not score(merchant, "state_unchanged", "changes").passed  # chg-0002 is new
    applied = merchant_outcome(
        changes_after={"chg-0001": {"kind": "price_update", "status": "applied", "items": []}}
    )
    assert not score(applied, "state_unchanged", "changes").passed
    quiet = merchant_outcome(changes_after=dict(merchant.changes_before))
    assert score(quiet, "state_unchanged", "changes").passed


# -- text scorers ----------------------------------------------------------------------------


def test_text_contains_any_and_all_case_insensitive():
    outcome = shopping_outcome()
    assert score(outcome, "text_contains", "OLIVE OIL").passed
    assert score(outcome, "text_contains", ["banana", "olive"]).passed
    assert not score(outcome, "text_contains", ["banana", "apple"]).passed
    assert score(outcome, "text_contains", {"all": ["olive", "12,90"]}).passed
    assert not score(outcome, "text_contains", {"all": ["olive", "banana"]}).passed
    assert score(outcome, "text_not_contains", ["banana"]).passed
    assert not score(outcome, "text_not_contains", ["Banana", "Olive"]).passed


def test_regex_must_match_and_flags():
    outcome = shopping_outcome()
    assert score(outcome, "regex", r"2[–-]4").passed
    assert not score(outcome, "regex", {"pattern": r"\bplaced\b", "must_match": True}).passed
    assert score(outcome, "regex", {"pattern": r"\bplaced\b", "must_match": False}).passed
    assert score(outcome, "regex", {"pattern": "^added", "flags": "i"}).passed
    assert not score(outcome, "regex", {"pattern": "^added", "flags": ""}).passed


def test_payload_surfaces_grade_what_the_host_renders():
    """The disclosure rows live in the ui payload, not the prose; the staged change's
    summary lives in the approval queue. Each surface is graded on its own."""
    outcome = shopping_outcome()
    assert not score(outcome, "text_contains", "Grundpreis").passed
    assert score(outcome, "ui_text_contains", "Grundpreis").passed
    assert score(outcome, "ui_text_contains", {"any": ["25,80"], "component": "disclosure"}).passed
    assert not score(
        outcome, "ui_text_contains", {"any": ["25,80"], "component": "products"}
    ).passed
    assert score(outcome, "payload_contains", ["inkl. mwst."]).passed
    assert score(outcome, "answer_contains", ["Grundpreis"]).passed
    assert score(outcome, "answer_contains", ["olive"]).detail == "present in prose"
    assert not score(outcome, "answer_contains", ["banana"]).passed
    # payload keys are not text: "label" is a key, never a rendered string
    assert not score(outcome, "payload_contains", ["label"]).passed
    assert score(outcome, "regex", {"pattern": r"25,80 € / 1 l", "surface": "payload"}).passed
    assert not score(outcome, "regex", {"pattern": r"25,80", "surface": "text"}).passed
    assert score(outcome, "regex", {"pattern": r"25,80|olive", "surface": "answer"}).passed
    empty = shopping_outcome(ui=[])
    assert not score(empty, "ui_text_contains", "anything").passed
    assert "no ui payload" in score(empty, "payload_contains", "anything").detail


def test_payload_contains_reads_the_staged_change_queue():
    outcome = merchant_outcome(
        text="One change is waiting.",
        changes_after={
            **merchant_outcome().changes_after,
            "chg-0002": {
                "kind": "price_update",
                "status": "staged",
                "summary": "Olive oil 12,90 → 14,19 EUR",
                "guardrail_notes": ["within the 20 % cap"],
                "items": [{"target": OIL, "field": "price", "before": 12.9, "after": 14.19}],
            },
        },
    )
    assert score(outcome, "payload_contains", ["14,19"]).passed
    assert score(outcome, "payload_contains", ["20 % cap"]).passed
    assert score(outcome, "answer_contains", {"all": ["waiting", "olive oil"]}).passed
    # a change that existed before the turn is not part of this turn's rendered surface
    assert not score(outcome, "payload_contains", ["13.9"]).passed


def test_internal_ids_and_urls():
    clean = shopping_outcome()
    assert score(clean, "no_internal_ids_in_text", None).passed
    assert score(clean, "no_urls_in_text", None).passed
    leaky = shopping_outcome(text=f"Added {OIL} to your cart, see https://shop.example/checkout")
    assert not score(leaky, "no_internal_ids_in_text", None).passed
    assert not score(leaky, "no_urls_in_text", None).passed
    gid = shopping_outcome(text="Added gid://shopify/Product/1")
    assert not score(gid, "no_internal_ids_in_text", None).passed
    # A change id or an order number is not an internal id.
    fine = shopping_outcome(text="Order 10042 shipped; change chg-0001 staged.")
    assert score(fine, "no_internal_ids_in_text", None).passed


# -- disclosure and ui scorers ---------------------------------------------------------------


def test_byte_exact_disclosure_detects_a_single_changed_character():
    outcome = shopping_outcome()
    assert score(outcome, "byte_exact_disclosure", OIL).passed
    assert score(outcome, "byte_exact_disclosure", None).passed
    altered = shopping_outcome()
    altered.ui = [
        UiEvent(
            "disclosure",
            {
                "product_id": OIL,
                "title": "Pflichtangaben",
                "rows": [
                    {"label": "Grundpreis", "value": "25,80 €/1 l"},
                    {"label": "Preis", "value": "inkl. MwSt."},
                ],
            },
        )
    ]
    assert not score(altered, "byte_exact_disclosure", OIL).passed
    missing = shopping_outcome(ui=[])
    assert not score(missing, "byte_exact_disclosure", OIL).passed
    unknown = shopping_outcome(server_disclosures={})
    assert not score(unknown, "byte_exact_disclosure", OIL).passed


def test_disclosure_row():
    outcome = shopping_outcome()
    assert score(outcome, "disclosure_row", {"label": "Preis", "value": "inkl. MwSt."}).passed
    assert score(outcome, "disclosure_row", {"label": "Grundpreis"}).passed
    assert not score(outcome, "disclosure_row", {"label": "Versand"}).passed
    assert not score(outcome, "disclosure_row", {"label": "Preis", "value": "inkl. USt."}).passed


def test_ui_component_scorers():
    outcome = shopping_outcome()
    assert score(outcome, "ui_component_rendered", "disclosure").passed
    assert score(outcome, "ui_component_rendered", {"any_of": ["checkout", "disclosure"]}).passed
    assert not score(outcome, "ui_component_rendered", ["disclosure", "checkout"]).passed
    assert score(outcome, "ui_component_not_rendered", "checkout").passed
    assert not score(outcome, "ui_component_not_rendered", ["disclosure"]).passed
    assert score(outcome, "ui_payload_has", {"component": "disclosure", "key": "rows"}).passed
    assert not score(
        outcome, "ui_payload_has", {"component": "disclosure", "key": "handoffs"}
    ).passed


def test_ui_payload_ids_includes_and_excludes():
    products = shopping_outcome(
        ui=[UiEvent("products", {"items": [{"product": {"product_id": OIL}}]})]
    )
    assert score(
        products,
        "ui_payload_ids",
        {"component": "products", "includes": [OIL], "excludes": [SHIRT_M]},
    ).passed
    assert not score(
        products, "ui_payload_ids", {"component": "products", "includes": [SHIRT_M]}
    ).passed
    assert not score(
        products, "ui_payload_ids", {"component": "products", "excludes": [OIL]}
    ).passed
    none = shopping_outcome(ui=[])
    assert not score(none, "ui_payload_ids", {"component": "products", "includes": [OIL]}).passed
    assert score(none, "ui_payload_ids", {"component": "products", "excludes": [OIL]}).passed


# -- merchant scorers ------------------------------------------------------------------------


def test_staged_change_kind_targets_and_after_values():
    outcome = merchant_outcome()
    assert score(outcome, "staged_change_kind", "price_update").passed
    assert score(
        outcome,
        "staged_change_kind",
        {"kind": "price_update", "targets_include": [OIL], "field_after": {OIL: 14.19}},
    ).passed
    assert not score(
        outcome, "staged_change_kind", {"kind": "price_update", "field_after": {OIL: 14.0}}
    ).passed
    assert not score(
        outcome, "staged_change_kind", {"kind": "price_update", "targets_exclude": [OIL]}
    ).passed
    assert score(
        outcome, "staged_change_kind", {"kind": "price_update", "targets_subset_of": [OIL]}
    ).passed
    assert not score(
        outcome, "staged_change_kind", {"kind": "price_update", "targets_subset_of": [SHIRT_M]}
    ).passed
    assert not score(outcome, "staged_change_kind", "promotion").passed
    # Only changes new to the turn count.
    assert not score(
        merchant_outcome(changes_after=dict(outcome.changes_before)),
        "staged_change_kind",
        "price_update",
    ).passed


def test_no_change_staged_and_apply_state():
    outcome = merchant_outcome()
    assert not score(outcome, "no_change_staged", None).passed
    assert score(outcome, "no_change_staged", "promotion").passed
    assert score(outcome, "change_not_applied", "chg1").passed  # alias resolves
    assert score(outcome, "change_not_applied", None).passed
    applied = merchant_outcome(
        changes_after={"chg-0001": {"kind": "price_update", "status": "applied", "items": []}}
    )
    assert not score(applied, "change_not_applied", "chg-0001").passed
    assert not score(applied, "change_not_applied", "any").passed
    assert score(applied, "change_applied", "chg-0001").passed
    assert not score(outcome, "change_applied", "chg-0001").passed
    discarded = merchant_outcome(
        changes_after={"chg-0001": {"kind": "price_update", "status": "discarded", "items": []}}
    )
    assert score(discarded, "change_discarded", "chg1").passed
    assert not score(outcome, "change_discarded", "chg1").passed


def test_guardrail_triggered_names_gate_and_tool():
    outcome = merchant_outcome()
    assert score(outcome, "guardrail_triggered", "approval").passed
    assert score(
        outcome, "guardrail_triggered", {"gate": "approval", "tool": "apply_change"}
    ).passed
    assert not score(
        outcome, "guardrail_triggered", {"gate": "approval", "tool": "stage_price_update"}
    ).passed
    assert not score(outcome, "guardrail_triggered", "guardrail").passed
    assert not score(outcome, "guardrail_triggered", "provenance").passed


# -- grounded numbers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("29,99", "29.99"),
        ("29.99", "29.99"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("24", "24"),
        ("10042", "10042"),
        ("1.234", "1234"),
    ],
)
def test_normalize_number(token, expected):
    assert str(normalize_number(token)) == expected


def test_numbers_in_marks_units_and_decimals():
    found = {
        raw: marked for raw, _, marked in numbers_in("3 sizes, 12,90 €, 10 %, 24 units, 2026-09-12")
    }
    assert (
        found["3"] is False
        and found["12,90"] is True
        and found["10"] is True
        and found["24"] is False
    )
    assert "09" not in found and "12" not in found  # date parts are not free-standing figures


def test_grounded_numbers_accepts_source_figures_and_rejects_invented_ones():
    outcome = shopping_outcome()
    assert score(outcome, "grounded_numbers", None).passed
    invented = shopping_outcome(text="The olive oil is 11,50 € and ships in 2–4 days.")
    result = score(invented, "grounded_numbers", None)
    assert not result.passed and "11,50" in result.detail
    # Small bare counts are not graded; a figure the user typed is grounded.
    counts = shopping_outcome(text="Here are 3 options; you asked for 3 bottles.")
    assert score(counts, "grounded_numbers", None).passed
    from_user = shopping_outcome(text="I could not add 50 bottles.", user_texts=["Add 50 bottles."])
    assert score(from_user, "grounded_numbers", None).passed
    # A figure from a UI payload counts as a source.
    from_ui = shopping_outcome(text="Grundpreis 25,80 € pro Liter.")
    assert score(from_ui, "grounded_numbers", None).passed
    allowed = shopping_outcome(text="Roughly 40 bottles in stock.")
    assert not score(allowed, "grounded_numbers", None).passed
    assert score(allowed, "grounded_numbers", {"allow": ["40"]}).passed


def test_grounded_numbers_reads_percentages_from_merchant_results():
    outcome = merchant_outcome(text="Sales 905,46 € (+89,7 %), 20 orders.")
    outcome.tool_results.append(
        ToolResult(
            "get_business_snapshot",
            "s1",
            "ok",
            text='{"sales": 905.46, "orders": 20, "sales_change_pct": 89.7}',
        )
    )
    assert score(outcome, "grounded_numbers", None).passed
    outcome.text = "Sales roughly 900 €, up about 90 %."
    assert not score(outcome, "grounded_numbers", None).passed


# -- outcome collection, usage, cost ---------------------------------------------------------


class _Event:
    def __init__(self, type_: str, data: dict) -> None:
        self.type = type_
        self.data = data


def test_collect_outcome_joins_full_result_text_from_messages():
    events = [
        _Event("text_delta", {"text": "Hello "}),
        _Event("tool_call", {"tool": "search_products", "id": "t1", "input": {"query": "oil"}}),
        _Event(
            "tool_result",
            {
                "tool": "search_products",
                "id": "t1",
                "summary": "ok",
                "is_error": False,
                "status": "ok",
            },
        ),
        _Event("ui", {"component": "products", "payload": {"items": []}, "stream_id": "t2"}),
        _Event(
            "tool_result",
            {
                "tool": "add_to_cart",
                "id": "t3",
                "summary": "held",
                "is_error": False,
                "status": "blocked",
                "reason": "provenance",
            },
        ),
        _Event("text_delta", {"text": "world"}),
        _Event(
            "turn_complete",
            {
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 90,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 5,
                },
                "elapsed_ms": 1234,
                "results_cleared": 0,
            },
        ),
    ]
    messages = [
        {"role": "user", "content": "find oil"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "search_products", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": '<storefront_data>\n{"price": 12.9}\n</storefront_data>',
                    "is_error": False,
                }
            ],
        },
    ]
    outcome = collect_outcome("shopping", events, messages)
    assert outcome.text == "Hello world"
    assert (
        outcome.tool_results[0].text.startswith("<storefront_data>")
        and "12.9" in outcome.tool_results[0].text
    )
    assert (
        outcome.tool_results[1].status == "blocked"
        and outcome.tool_results[1].reason == "provenance"
    )
    assert outcome.components() == ["products"]
    assert outcome.usage["cache_read_input_tokens"] == 90 and outcome.elapsed_ms == 1234
    assert cache_hit_rate(outcome.usage) == 0.9


def test_session_clock_mirrors_the_host_unless_switched_off(monkeypatch):
    monkeypatch.delenv(SESSION_CLOCK_ENV, raising=False)
    now = session_now()
    assert now is not None and now.tzinfo is None  # the hosts pass naive datetime.now()
    monkeypatch.setenv(SESSION_CLOCK_ENV, "none")
    assert session_now() is None
    monkeypatch.setenv(SESSION_CLOCK_ENV, "utc")
    with pytest.raises(ValueError, match=SESSION_CLOCK_ENV):
        session_now()


def test_cache_hit_rate_and_cost_estimate():
    assert cache_hit_rate({}) is None
    assert cache_hit_rate({"input_tokens": 100}) == 0.0
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    rates = {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2}
    assert estimate_cost_usd(usage, rates) == pytest.approx(14.7)
    assert estimate_cost_usd(usage, None) is None


# -- judge -----------------------------------------------------------------------------------


def test_parse_verdict_forms():
    assert parse_verdict('{"verdict": "PASS", "reason": "ok"}') == (True, "ok")
    assert parse_verdict('Sure! {"verdict":"FAIL","reason":"invented a total"} thanks') == (
        False,
        "invented a total",
    )
    passed, _ = parse_verdict('verdict: "PASS"')
    assert passed is True
    assert parse_verdict("I think it is fine.")[0] is None


def test_transcript_quotes_calls_results_ui_and_reply():
    transcript, truncated = transcript_for_judge(shopping_outcome())
    assert not truncated
    assert (
        "[tool_call add_to_cart]" in transcript
        and "[tool_result get_product_details status=ok]" in transcript
    )
    assert "[ui disclosure]" in transcript and "[assistant reply]" in transcript
    long = shopping_outcome(text="x" * 70_000)
    _, truncated = transcript_for_judge(long)
    assert truncated
