# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Every case file validates; ids are unique and well-formed; every positive has a
negative; every safety case is in the CI set; every scorer invocation validates; every
``$NAME`` resolves against the mode's fixture names; the mandatory Shopware-specific
behaviors from MASTERPLAN §5 item 3.3 are covered; and the tag coverage report prints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evals import CASES_DIR, GATES_PATH
from evals.backends import MERCHANT_FIXTURES, SHOPPING_FIXTURES
from evals.cases import (
    CASE_ID_PATTERN,
    TAGS,
    Case,
    CaseError,
    coverage_report,
    load_case_file,
    load_cases,
    pairing_report,
    resolve,
)
from evals.ci import GatePolicyError, evaluate_gates, load_gates, rates_for, select_ci_cases
from evals.overlay import OVERLAY_ID_PREFIX
from evals.scorers import MERCHANT_ONLY, SHOPPING_ONLY, is_judge, validate_args

MIN_SHOPPING = 40
MIN_MERCHANT = 30


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return load_cases("all", "all")


def test_every_case_file_loads_and_ids_are_unique(cases):
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))
    assert all(CASE_ID_PATTERN.match(case_id) for case_id in ids)
    assert all(case.path is not None and case.path.parent.name == case.suite for case in cases)


def test_case_counts_meet_the_floor(cases):
    shopping = [c for c in cases if c.suite == "shopping"]
    merchant = [c for c in cases if c.suite == "merchant"]
    assert len(shopping) >= MIN_SHOPPING, f"{len(shopping)} shopping cases"
    assert len(merchant) >= MIN_MERCHANT, f"{len(merchant)} merchant cases"


def test_every_case_is_in_a_positive_negative_pair(cases):
    report = pairing_report(cases)
    assert report == {"unpaired": [], "dangling": [], "cross_suite": []}, report


def test_every_safety_case_is_in_the_ci_set(cases):
    ci = select_ci_cases(cases)  # raises when a safety case is outside
    assert all("safety" not in c.tags or c.set == "ci" for c in cases)
    assert ci and all(c.set == "ci" for c in ci)


def test_every_expectation_validates_and_fits_the_suite(cases):
    judged = 0
    for case in cases:
        for expectation in case.expect:
            validate_args(expectation.scorer, expectation.args)
            if case.suite == "shopping":
                assert expectation.scorer not in MERCHANT_ONLY, (
                    f"{case.id} uses {expectation.scorer}"
                )
            else:
                assert expectation.scorer not in SHOPPING_ONLY, (
                    f"{case.id} uses {expectation.scorer}"
                )
            judged += is_judge(expectation.scorer)
        # Every case grades at least one deterministic thing; the judge alone never decides.
        assert any(not is_judge(e.scorer) for e in case.expect), case.id
    # The judge is used sparingly.
    assert judged <= len(cases) * 0.15, f"{judged} judge scorers over {len(cases)} cases"


def test_placeholders_resolve_against_fixture_names(cases):
    for case in cases:
        allowed = (
            set(SHOPPING_FIXTURES if case.suite == "shopping" else MERCHANT_FIXTURES)
            | case.aliases()
        )
        unknown = case.placeholders() - allowed
        assert not unknown, f"{case.id}: unknown placeholders {sorted(unknown)}"
        mapping = {name: f"resolved-{name}" for name in allowed}
        assert "$" not in json.dumps(
            resolve(case.model_dump(mode="json", exclude={"path"}), mapping)
        )


def test_eval_only_fixture_ids_carry_the_overlay_prefix(cases):
    for case in cases:
        for record in [*case.fixtures.products, *case.fixtures.listings]:
            key = "product_id" if "product_id" in record else "listing_id"
            assert str(record[key]).startswith(OVERLAY_ID_PREFIX), f"{case.id}: {record[key]}"
        for issue in case.fixtures.order_issues:
            assert str(issue["issue_id"]).startswith(OVERLAY_ID_PREFIX), case.id


def test_history_ends_on_assistant_and_message_is_the_user_turn(cases):
    for case in cases:
        assert not case.history or case.history[-1].role == "assistant", case.id


MANDATORY = {
    # MASTERPLAN §5 item 3.3 and the review findings, by case id.
    "shopping": [
        "shop-variant-001-family-without-size-asks",
        "shop-variant-002-variant-chosen-adds-variant-id",
        "shop-variant-003-out-of-stock-variant-names-siblings",
        "shop-disclosure-001-grundpreis-byte-exact",
        "shop-disclosure-003-vat-and-shipping-rows-from-server-copy",
        "shop-delivery-001-time-from-product-data",
        "shop-promo-001-code-request-not-invented",
        "shop-b2b-001-customer-group-price-not-shown-to-guest",
        "shop-policy-001-return-period-from-policy-text",
        "shop-policy-002-no-policy-found-says-so",
        "shop-injection-001-user-authored-ignore-rules",
        "shop-injection-003-data-plane-description-ignored",
        "shop-provenance-001-random-uuid-no-write",
        "shop-provenance-002-first-one-from-last-list",
        "shop-cart-003-cap-clamps-fifty",
        "shop-cart-004-parallel-adds-cannot-exceed-cap",
        "shop-checkout-001-handoff-card-no-url-no-order",
    ],
    "merchant": [
        "merch-price-001-family-raise-asks-or-expands",
        "merch-approval-003-discarded-then-apply-refused",
        "merch-approval-001-apply-without-approval-held",
        "merch-price-003-cap-exceeded-guardrail",
        "merch-inventory-001-restock-paused-listing-says-paused",
        "merch-inventory-003-pause-family-covers-all-children",
        "merch-promo-001-depth-cap-guardrail",
        "merch-campaign-001-not-managed-here",
        "merch-snapshot-001-grounded-and-traffic-unavailable",
        "merch-multi-001-markdown-and-stock-both-halves",
        "merch-injection-001-hostile-customer-comment-ignored",
    ],
}


def test_mandatory_shopware_cases_exist_and_are_in_ci(cases):
    by_id = {case.id: case for case in cases}
    for suite, ids in MANDATORY.items():
        for case_id in ids:
            assert case_id in by_id, f"missing mandatory case {case_id}"
            assert by_id[case_id].suite == suite
            assert by_id[case_id].set == "ci", f"{case_id} must be in the ci set"


def test_tag_coverage_report(cases, capsys):
    report = coverage_report(cases)
    for suite in ("shopping", "merchant"):
        tags = report["suites"][suite]["tags"]
        assert all(tag in tags for tag in TAGS)
        assert all(tags[tag] > 0 for tag in TAGS), (
            f"{suite}: every tag has at least one case: {tags}"
        )
    with capsys.disabled():
        print("\ncase coverage:", json.dumps(report, indent=2))


# -- schema rejections -----------------------------------------------------------------------


def _write(tmp_path: Path, name: str, data) -> Path:
    path = tmp_path / "shopping" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


BASE = {
    "id": "shop-x-001-example",
    "title": "example case",
    "tags": ["core"],
    "set": "ci",
    "message": "hi",
    "expect": ["no_internal_ids_in_text"],
}


def test_schema_rejects_bad_shapes(tmp_path):
    good = _write(tmp_path, "good.yaml", BASE)
    assert load_case_file(good)[0].suite == "shopping"
    for bad in (
        {**BASE, "id": "merch-x-001-example"},  # suite prefix mismatch
        {**BASE, "tags": []},
        {**BASE, "tags": ["core", "core"]},
        {**BASE, "set": "nightly"},
        {**BASE, "expect": []},
        {**BASE, "expect": [{"tool_called": "x", "tool_not_called": "y"}]},
        {**BASE, "negative_of": "shop-x-001-example"},
        {**BASE, "history": [{"role": "user", "content": "dangling user turn"}]},
        {**BASE, "state": {"seen_listings": ["$SHIRT"]}},  # merchant state on a shopping case
        {**BASE, "unknown_key": 1},
    ):
        path = _write(tmp_path, "bad.yaml", bad)
        with pytest.raises(CaseError):
            load_case_file(path)


def test_merchant_state_alias_rules(tmp_path):
    path = tmp_path / "merchant" / "m.yaml"
    path.parent.mkdir(parents=True)
    data = {
        **BASE,
        "id": "merch-x-001-example",
        "state": {
            "staged": [
                {
                    "alias": "chg1",
                    "kind": "price_update",
                    "items": [{"listing_id": "$OIL", "new_price": 1}],
                }
            ],
            "approved": ["chg2"],
        },
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(CaseError, match="chg2"):
        load_case_file(path)
    data["state"]["approved"] = ["chg1"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    case = load_case_file(path)[0]
    assert case.aliases() == {"chg1"} and case.placeholders() == {"OIL"}


def test_load_cases_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "a.yaml", BASE)
    _write(tmp_path, "b.yaml", BASE)
    with pytest.raises(CaseError, match="already used"):
        load_cases("shopping", "all", root=tmp_path)


def test_full_set_includes_ci_cases_and_ci_set_is_the_subset(tmp_path):
    _write(tmp_path, "a.yaml", {**BASE, "id": "shop-a-001-pr", "set": "ci"})
    _write(tmp_path, "b.yaml", {**BASE, "id": "shop-b-001-nightly", "set": "full"})
    assert [c.id for c in load_cases("shopping", "ci", root=tmp_path)] == ["shop-a-001-pr"]
    assert [c.id for c in load_cases("shopping", "full", root=tmp_path)] == [
        "shop-a-001-pr",
        "shop-b-001-nightly",
    ]
    assert len(load_cases("shopping", "all", root=tmp_path)) == 2


def test_placeholder_resolution_is_strict():
    assert resolve("add $OIL now", {"OIL": "x"}) == "add x now"
    assert resolve({"a": ["$OIL", 1]}, {"OIL": "x"}) == {"a": ["x", 1]}
    with pytest.raises(KeyError):
        resolve("$NOPE", {})
    assert resolve("$NOPE", {}, strict=False) == "$NOPE"


# -- gates -----------------------------------------------------------------------------------


def test_gates_policy_loads_and_prices_models():
    gates = load_gates(GATES_PATH)
    assert gates["pass_rate"]["safety"] == 1.0
    assert rates_for("claude-sonnet-5", gates)["input"] == 2.0
    assert rates_for("claude-opus-5", gates)["input"] == 5.0
    assert rates_for("some-unknown-model", gates) == gates["pricing"]["default"]


def _trial(
    passed: bool, trial: int = 1, cache: float = 0.9, cost: float = 0.01, scores=None
) -> dict:
    return {
        "trial": trial,
        "passed": passed,
        "cache_hit_rate": cache,
        "cost_usd": cost,
        "scores": scores or [],
    }


def test_evaluate_gates_thresholds():
    gates = load_gates(GATES_PATH)
    report = {
        "cases": [
            {
                "id": "shop-a-001-x",
                "suite": "shopping",
                "tags": ["core"],
                "trials": [_trial(True), _trial(True, 2)],
            },
            {
                "id": "shop-a-002-y",
                "suite": "shopping",
                "tags": ["safety"],
                "trials": [_trial(True), _trial(True, 2)],
            },
            {
                "id": "merch-a-001-z",
                "suite": "merchant",
                "tags": ["core"],
                "trials": [_trial(True, cost=0.2), _trial(True, 2, cost=0.2)],
            },
            {
                "id": "shop-a-003-skipped",
                "suite": "shopping",
                "tags": ["core"],
                "skipped": "fixture",
                "trials": [],
            },
        ]
    }
    result = evaluate_gates(report, gates)
    assert result.passed, result.failures
    assert result.metrics["pass_rate.core"] == 1.0 and result.metrics["cache_hit_rate"] == 0.9

    report["cases"][1]["trials"][1] = _trial(False, 2)  # one safety failure
    result = evaluate_gates(report, gates)
    assert not result.passed and any("pass_rate.safety" in f for f in result.failures)

    report["cases"][1]["trials"][1] = _trial(True, 2, cache=0.1)
    result = evaluate_gates(report, gates)
    assert any("cache_hit_rate" in f for f in result.failures)

    report["cases"][1]["trials"][1] = _trial(True, 2)
    report["cases"][2]["trials"] = [_trial(True, cost=5.0)]
    result = evaluate_gates(report, gates)
    assert any("cost_usd_per_turn.merchant" in f for f in result.failures)

    report["cases"][2]["trials"] = [
        _trial(True, cost=0.1, scores=[{"kind": "judge", "judge_error": True, "passed": False}])
    ]
    result = evaluate_gates(report, gates)
    assert any("judge_error_share" in f for f in result.failures)

    report["cases"][2]["trials"] = [
        {"trial": 1, "passed": False, "error_kind": "setup_error", "scores": []}
    ]
    result = evaluate_gates(report, gates)
    assert any("setup errors" in f for f in result.failures)


def test_select_ci_cases_rejects_safety_outside_ci():
    case = Case.model_validate({**BASE, "suite": "shopping", "tags": ["safety"], "set": "full"})
    with pytest.raises(GatePolicyError):
        select_ci_cases([case])


def test_cases_dir_has_both_suites():
    assert (CASES_DIR / "shopping").is_dir() and (CASES_DIR / "merchant").is_dir()
