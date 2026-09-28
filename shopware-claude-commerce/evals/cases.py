# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The case format: one YAML document per case, validated here.

A case is a precondition (``state``: what the session has already seen, what is in the
cart or the change queue), an optional ``history`` of earlier messages, the ``message``
that drives the turn, and ``expect``: a list of scorer invocations graded against the
final state and the rendered response. Ids in a case are written as ``$NAME``
placeholders (``$OIL``, ``$SHIRT_M``, ``$chg1``) and resolved by the harness against the
backend's fixture ids and the aliases of the changes it staged, so the same case runs
against the recorded fixtures and the live shop.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import CASES_DIR

Suite = Literal["shopping", "merchant"]
Tag = Literal["core", "context", "safety", "interface", "multi-capability"]
CaseSet = Literal["ci", "full"]

SUITES: tuple[Suite, ...] = ("shopping", "merchant")
TAGS: tuple[Tag, ...] = ("core", "context", "safety", "interface", "multi-capability")
CASE_ID_PATTERN = re.compile(r"^(shop|merch)-[a-z0-9]+-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
SUITE_PREFIX: dict[Suite, str] = {"shopping": "shop", "merchant": "merch"}


class CaseError(ValueError):
    """A case file that does not validate; the message names the file."""


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class MemoryFactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=64)
    value: str = Field(max_length=200)
    category: Literal["preference", "constraint", "context"] = "preference"


class CartLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    quantity: int = Field(default=1, ge=1)


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_type: Literal["home", "search", "product", "cart", "orders", "other"] = "home"
    product_id: str | None = None
    query: str | None = None


class ShoppingState(BaseModel):
    """What the shopping session already holds before the graded turn."""

    model_config = ConfigDict(extra="forbid")

    # Product ids read through get_product_details (their variants enter provenance too).
    seen_products: list[str] = Field(default_factory=list)
    # Search queries run before the turn (search rows enter provenance, families included).
    searched: list[str] = Field(default_factory=list)
    cart: list[CartLine] = Field(default_factory=list)
    memory: list[MemoryFactSpec] = Field(default_factory=list)
    page: PageSpec | None = None


class StagedSpec(BaseModel):
    """A change staged through the backend before the turn; ``alias`` names it in the
    message and the expectations (``$chg1``)."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["price_update", "inventory_action", "listing_update", "promotion"]
    # price_update / inventory_action
    items: list[dict[str, Any]] = Field(default_factory=list)
    # listing_update
    listing_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    # promotion
    promotion: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> StagedSpec:
        if self.kind in {"price_update", "inventory_action"} and not self.items:
            raise ValueError(f"a staged {self.kind} needs items")
        if self.kind == "listing_update" and not (self.listing_id and self.fields):
            raise ValueError("a staged listing_update needs listing_id and fields")
        if self.kind == "promotion" and not self.promotion:
            raise ValueError("a staged promotion needs the promotion draft")
        return self


class MerchantState(BaseModel):
    """What the merchant session already holds before the graded turn."""

    model_config = ConfigDict(extra="forbid")

    # Listing ids in search shape (a family carries options; price writes are held).
    seen_listings: list[str] = Field(default_factory=list)
    # Listing ids read in full through get_listing (variants enter provenance).
    read_listings: list[str] = Field(default_factory=list)
    staged: list[StagedSpec] = Field(default_factory=list)
    approved: list[str] = Field(default_factory=list)  # aliases the host marked approved
    discarded: list[str] = Field(default_factory=list)  # aliases discarded before the turn
    latest_snapshot: bool = False
    memory: list[MemoryFactSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _aliases(self) -> MerchantState:
        known = {spec.alias for spec in self.staged}
        if len(known) != len(self.staged):
            raise ValueError("staged aliases must be unique")
        for name in [*self.approved, *self.discarded]:
            if name not in known:
                raise ValueError(f"alias {name!r} is not staged in this case")
        return self


class Fixtures(BaseModel):
    """Eval-only records the harness overlays onto the backend for the run: poisoned
    listings, benign counterparts, hostile buyer messages. Their ids never appear in
    demo data, seeds, or recorded captures."""

    model_config = ConfigDict(extra="forbid")

    products: list[dict[str, Any]] = Field(default_factory=list)  # shopping ProductDetails
    policies: list[dict[str, Any]] = Field(default_factory=list)  # shopping Policy
    listings: list[dict[str, Any]] = Field(default_factory=list)  # merchant ListingDetails
    order_issues: list[dict[str, Any]] = Field(default_factory=list)  # merchant OrderIssue

    @property
    def empty(self) -> bool:
        return not (self.products or self.policies or self.listings or self.order_issues)


class Expectation(BaseModel):
    """One scorer invocation: ``{scorer_name: args}``."""

    model_config = ConfigDict(extra="forbid")

    scorer: str
    args: Any = None

    @classmethod
    def from_yaml(cls, raw: Any) -> Expectation:
        if isinstance(raw, str):
            return cls(scorer=raw, args=None)
        if isinstance(raw, dict) and len(raw) == 1:
            ((name, args),) = raw.items()
            return cls(scorer=str(name), args=args)
        raise ValueError(f"an expectation is a scorer name or a one-key mapping, got {raw!r}")


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=CASE_ID_PATTERN.pattern)
    title: str = Field(min_length=3, max_length=120)
    suite: Suite
    tags: list[Tag] = Field(min_length=1)
    set: CaseSet
    negative_of: str | None = None
    skip: str | None = None
    state: ShoppingState | MerchantState = Field(default_factory=ShoppingState)
    fixtures: Fixtures = Field(default_factory=Fixtures)
    history: list[HistoryMessage] = Field(default_factory=list)
    message: str = Field(min_length=1)
    expect: list[Expectation] = Field(min_length=1)
    notes: str | None = None
    path: Path | None = Field(default=None, exclude=True)

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, tags: list[Tag]) -> list[Tag]:
        if len(set(tags)) != len(tags):
            raise ValueError("tags repeat")
        return tags

    @model_validator(mode="before")
    @classmethod
    def _shape_state_and_expect(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        suite = data.get("suite")
        state = data.get("state") or {}
        if isinstance(state, dict):
            data["state"] = (
                MerchantState.model_validate(state)
                if suite == "merchant"
                else ShoppingState.model_validate(state)
            )
        raw_expect = data.get("expect")
        if isinstance(raw_expect, list):
            data["expect"] = [
                item if isinstance(item, Expectation) else Expectation.from_yaml(item)
                for item in raw_expect
            ]
        return data

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if not self.id.startswith(SUITE_PREFIX[self.suite] + "-"):
            raise ValueError(f"id {self.id!r} does not start with {SUITE_PREFIX[self.suite]}-")
        if self.negative_of == self.id:
            raise ValueError("a case cannot be its own negative")
        if self.suite == "shopping" and not isinstance(self.state, ShoppingState):
            raise ValueError("a shopping case carries a shopping state")
        if self.suite == "merchant" and not isinstance(self.state, MerchantState):
            raise ValueError("a merchant case carries a merchant state")
        if self.history and self.history[-1].role == "user":
            raise ValueError("history ends on an assistant message; the user's turn is `message`")
        return self

    # -- placeholders --------------------------------------------------------------------

    def placeholders(self) -> set[str]:
        """Every ``$NAME`` the case mentions, in state, fixtures, history, message, expect."""
        found: set[str] = set()
        _collect_placeholders(self.model_dump(mode="json", exclude={"path"}), found)
        return found

    def aliases(self) -> set[str]:
        if isinstance(self.state, MerchantState):
            return {spec.alias for spec in self.state.staged}
        return set()


def _collect_placeholders(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        found.update(PLACEHOLDER.findall(value))
    elif isinstance(value, dict):
        for key, child in value.items():
            _collect_placeholders(key, found)
            _collect_placeholders(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_placeholders(child, found)


def resolve(value: Any, mapping: dict[str, str], *, strict: bool = True) -> Any:
    """``$NAME`` placeholders replaced throughout ``value`` (strings, dicts, lists). With
    ``strict`` an unknown name raises; otherwise it is left as written."""
    if isinstance(value, str):

        def replacement(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in mapping:
                return mapping[name]
            if strict:
                raise KeyError(f"unknown placeholder ${name}")
            return match.group(0)

        return PLACEHOLDER.sub(replacement, value)
    if isinstance(value, dict):
        return {
            resolve(key, mapping, strict=strict): resolve(child, mapping, strict=strict)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [resolve(child, mapping, strict=strict) for child in value]
    return value


# -- loading ---------------------------------------------------------------------------


def _validate_case(raw: Any, path: Path, suite: Suite | None) -> Case:
    if not isinstance(raw, dict):
        raise CaseError(f"{path}: every case is a mapping, got {type(raw).__name__}")
    raw.setdefault("suite", suite or path.parent.name)
    try:
        case = Case.model_validate(raw)
    except (ValidationError, ValueError) as error:
        raise CaseError(f"{path} ({raw.get('id', '?')}): {error}") from error
    case.path = path
    return case


def load_case_file(path: Path, suite: Suite | None = None) -> list[Case]:
    """The cases in one YAML file: a single mapping, or a list of mappings grouped by
    area (``variants.yaml`` holds the variant cases)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return [_validate_case(raw, path, suite)]
    if isinstance(raw, list):
        return [_validate_case(item, path, suite) for item in raw]
    raise CaseError(f"{path}: a case file holds one mapping or a list of mappings")


def load_case(path: Path, suite: Suite | None = None) -> Case:
    cases = load_case_file(path, suite)
    if len(cases) != 1:
        raise CaseError(f"{path}: expected exactly one case, found {len(cases)}")
    return cases[0]


def load_cases(
    suite: Suite | Literal["all"] = "all",
    case_set: CaseSet | Literal["all"] = "all",
    *,
    root: Path = CASES_DIR,
    ids: set[str] | None = None,
    tags: set[str] | None = None,
) -> list[Case]:
    """Every case under ``root/<suite>/*.yaml`` that matches the filters, sorted by id.
    Duplicate ids raise."""
    suites: tuple[Suite, ...] = SUITES if suite == "all" else (suite,)
    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for name in suites:
        for path in sorted((root / name).glob("*.yaml")):
            for case in load_case_file(path, name):
                if case.id in seen:
                    raise CaseError(f"{path}: id {case.id!r} already used by {seen[case.id]}")
                seen[case.id] = path
                cases.append(case)
    # ``ci`` is the pull-request subset; ``full`` (the nightly set) is every case, the ci
    # ones included — a case's ``set: full`` marks it as nightly-only.
    if case_set == "ci":
        cases = [case for case in cases if case.set == "ci"]
    if ids:
        cases = [case for case in cases if case.id in ids]
    if tags:
        cases = [case for case in cases if tags & set(case.tags)]
    return sorted(cases, key=lambda case: case.id)


def pairing_report(cases: list[Case]) -> dict[str, list[str]]:
    """Which cases stand alone: ``unpaired`` (neither a negative nor the positive of one),
    ``dangling`` (``negative_of`` names an id that is not in the set), ``cross_suite``."""
    by_id = {case.id: case for case in cases}
    negatives_of: dict[str, list[str]] = {}
    dangling: list[str] = []
    cross: list[str] = []
    for case in cases:
        if case.negative_of is None:
            continue
        positive = by_id.get(case.negative_of)
        if positive is None:
            dangling.append(case.id)
            continue
        if positive.suite != case.suite:
            cross.append(case.id)
        negatives_of.setdefault(positive.id, []).append(case.id)
    unpaired = [
        case.id for case in cases if case.negative_of is None and case.id not in negatives_of
    ]
    return {"unpaired": unpaired, "dangling": dangling, "cross_suite": cross}


def coverage_report(cases: list[Case]) -> dict[str, Any]:
    """Counts by suite, tag, and set; the shape the CLI and the schema test print."""
    report: dict[str, Any] = {"total": len(cases), "suites": {}}
    for suite in SUITES:
        subset = [case for case in cases if case.suite == suite]
        report["suites"][suite] = {
            "total": len(subset),
            "ci": sum(1 for case in subset if case.set == "ci"),
            "full": sum(1 for case in subset if case.set == "full"),
            "skipped": sum(1 for case in subset if case.skip),
            "tags": {tag: sum(1 for case in subset if tag in case.tags) for tag in TAGS},
            "negatives": sum(1 for case in subset if case.negative_of),
        }
    return report
