# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Deterministic scorers over a recorded :class:`Outcome`.

Every scorer is a pure function ``(outcome, args) -> ScoreResult`` whose ``args`` are
validated by a pydantic model, so a case that names a scorer wrongly fails at load time
rather than at run time. The only non-deterministic scorer, ``judge_rubric``, lives in
``evals/judge.py`` and is registered here for schema purposes only; a report marks its
results ``kind: judge``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# -- the recorded outcome of one turn --------------------------------------------------


@dataclass
class ToolCall:
    tool: str
    id: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool: str
    id: str
    status: str  # ok | error | blocked
    reason: str | None = None  # the gate that held the call
    summary: str = ""
    text: str = ""  # the full result text the model read


@dataclass
class UiEvent:
    component: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    """What one graded turn produced. ``cart_*`` are ``cart_payload`` dicts;
    ``changes_*`` map change ids to their ``StagedChange`` dumps; ``server_disclosures``
    holds the backend's own disclosure record per product the turn disclosed."""

    suite: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    ui: list[UiEvent] = field(default_factory=list)
    cart_before: dict[str, Any] | None = None
    cart_after: dict[str, Any] | None = None
    changes_before: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes_after: dict[str, dict[str, Any]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    user_texts: list[str] = field(default_factory=list)
    server_disclosures: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0
    stop_reason: str | None = None
    error: str | None = None

    # -- derived views -------------------------------------------------------------------

    def calls(self, tool: str) -> list[ToolCall]:
        return [call for call in self.tool_calls if call.tool == tool]

    def results(self, tool: str | None = None) -> list[ToolResult]:
        return [r for r in self.tool_results if tool is None or r.tool == tool]

    def result_for(self, call: ToolCall) -> ToolResult | None:
        return next((r for r in self.tool_results if r.id == call.id), None)

    def cart_lines(self) -> list[dict[str, Any]]:
        return list((self.cart_after or {}).get("items") or [])

    def cart_line(self, product_id: str) -> dict[str, Any] | None:
        return next(
            (line for line in self.cart_lines() if line.get("product_id") == product_id), None
        )

    def components(self) -> list[str]:
        return [event.component for event in self.ui]

    def new_changes(self) -> dict[str, dict[str, Any]]:
        """Changes that did not exist before the turn."""
        return {
            cid: rec for cid, rec in self.changes_after.items() if cid not in self.changes_before
        }

    def grounding_sources(self) -> str:
        """Everything the model could legitimately quote a figure from."""
        parts = [r.text or r.summary for r in self.tool_results]
        parts.extend(
            json.dumps(event.payload, ensure_ascii=False, default=str) for event in self.ui
        )
        parts.extend(self.user_texts)
        return "\n".join(parts)

    # -- the surfaces the user sees ----------------------------------------------------

    def ui_text(self, component: str | None = None) -> str:
        """Every string and number the host renders from ``ui`` payloads (optionally one
        component): product cards, disclosure rows, change previews, digests, metrics."""
        return "\n".join(
            _leaf_text(event.payload)
            for event in self.ui
            if component is None or event.component == component
        )

    def change_text(self) -> str:
        """What the approval queue shows for the changes this turn staged: ``summary``,
        ``guardrail_notes`` and the per-item before/after rows."""
        parts = []
        for record in self.new_changes().values():
            parts.append(
                _leaf_text(
                    {
                        key: record.get(key)
                        for key in ("summary", "guardrail_notes", "items", "kind", "status")
                    }
                )
            )
        return "\n".join(parts)

    def payload_text(self) -> str:
        return "\n".join(part for part in (self.ui_text(), self.change_text()) if part)

    def answer_text(self) -> str:
        """Prose plus rendered payloads: the whole surface the user sees."""
        return "\n".join(part for part in (self.text, self.payload_text()) if part)

    def surface(self, name: Literal["text", "payload", "answer"]) -> str:
        if name == "text":
            return self.text
        if name == "payload":
            return self.payload_text()
        return self.answer_text()


def _leaf_text(value: Any) -> str:
    """The strings and numbers of a nested payload, one per line; keys are not text."""
    leaves: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, str):
            leaves.append(node)
        elif isinstance(node, (int, float, Decimal)):
            leaves.append(str(node))
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        else:
            leaves.append(str(node))

    walk(value)
    return "\n".join(leaves)


@dataclass(frozen=True)
class ScoreResult:
    scorer: str
    passed: bool
    detail: str = ""
    kind: Literal["code", "judge"] = "code"
    judge_error: bool = False  # the judge did not return a verdict; not an agent failure

    def as_dict(self) -> dict[str, Any]:
        data = {
            "scorer": self.scorer,
            "passed": self.passed,
            "detail": self.detail,
            "kind": self.kind,
        }
        if self.judge_error:
            data["judge_error"] = True
        return data


# -- argument shapes -------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameSet(_Args):
    """A tool or component selection: one name, a list (all of them), or an explicit
    ``any_of`` / ``all_of``."""

    any_of: list[str] = Field(default_factory=list)
    all_of: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"all_of": [data]}
        if isinstance(data, list):
            return {"all_of": [str(item) for item in data]}
        return data

    @model_validator(mode="after")
    def _some(self) -> NameSet:
        if not (self.any_of or self.all_of):
            raise ValueError("name a tool or component")
        return self

    @property
    def names(self) -> list[str]:
        return [*self.all_of, *self.any_of]

    def satisfied(self, present: Iterable[str]) -> tuple[bool, str]:
        seen = set(present)
        missing = [name for name in self.all_of if name not in seen]
        if missing:
            return False, f"missing {', '.join(missing)}"
        if self.any_of and not (seen & set(self.any_of)):
            return False, f"none of {', '.join(self.any_of)}"
        return True, ""


class ToolCalledArgs(_Args):
    name: str | None = None
    any_of: list[str] = Field(default_factory=list)
    all_of: list[str] = Field(default_factory=list)
    status: Literal["ok", "error", "blocked"] | None = None
    with_input: dict[str, Any] = Field(default_factory=dict)  # subset match on arguments
    min_calls: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data}
        if isinstance(data, list):
            return {"all_of": [str(item) for item in data]}
        return data

    @model_validator(mode="after")
    def _some(self) -> ToolCalledArgs:
        if not (self.name or self.any_of or self.all_of):
            raise ValueError("name a tool")
        if self.with_input and not self.name:
            raise ValueError("with_input needs a single tool name")
        return self


class Names(NameSet):
    pass


class CartContainsArgs(_Args):
    product_id: str
    quantity: int | None = Field(default=None, ge=1)
    min_quantity: int | None = Field(default=None, ge=1)
    max_quantity: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {"product_id": data} if isinstance(data, str) else data


class IdList(_Args):
    ids: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ids": [data]}
        if isinstance(data, list):
            return {"ids": [str(item) for item in data]}
        return data


class StateUnchangedArgs(_Args):
    what: Literal["cart", "changes", "all"] = "all"

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if data is None:
            return {}
        return {"what": data} if isinstance(data, str) else data


class TextArgs(_Args):
    """Phrases matched case-insensitively. A plain list is ``any`` (accepted phrasings);
    ``all`` requires each."""

    any: list[str] = Field(default_factory=list)
    all: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"any": [data]}
        if isinstance(data, list):
            return {"any": [str(item) for item in data]}
        return data

    @model_validator(mode="after")
    def _some(self) -> TextArgs:
        if not (self.any or self.all):
            raise ValueError("name a phrase")
        return self


Surface = Literal["text", "payload", "answer"]


class UiTextArgs(TextArgs):
    """``text_contains`` over the rendered ``ui`` payloads, optionally one component."""

    component: str | None = None


class RegexArgs(_Args):
    pattern: str
    must_match: bool = True
    flags: str = "is"  # i: ignore case, s: dot matches newline, m: multiline
    surface: Surface = "text"  # prose only, rendered payloads only, or both

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {"pattern": data} if isinstance(data, str) else data

    @model_validator(mode="after")
    def _compiles(self) -> RegexArgs:
        try:
            re.compile(self.pattern, self.re_flags)
        except (re.error, KeyError) as error:
            raise ValueError(f"regex does not compile: {error}") from error
        return self

    @property
    def re_flags(self) -> int:
        value = 0
        for flag in self.flags:
            value |= {"i": re.IGNORECASE, "s": re.DOTALL, "m": re.MULTILINE}[flag]
        return value


class DisclosureArgs(_Args):
    product_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if data is None:
            return {}
        return {"product_id": data} if isinstance(data, str) else data


class DisclosureRowArgs(_Args):
    label: str
    value: str | None = None
    product_id: str | None = None


class NoArgs(_Args):
    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {} if data is None else data


class UiPayloadIdsArgs(_Args):
    component: str = "products"
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _some(self) -> UiPayloadIdsArgs:
        if not (self.includes or self.excludes):
            raise ValueError("name ids to include or exclude")
        return self


class UiPayloadHasArgs(_Args):
    component: str
    key: str


class StagedKindArgs(_Args):
    kind: Literal["price_update", "inventory_action", "listing_update", "promotion", "campaign"]
    targets_include: list[str] = Field(default_factory=list)
    targets_exclude: list[str] = Field(default_factory=list)
    targets_subset_of: list[str] = Field(default_factory=list)
    field_after: dict[str, Any] = Field(default_factory=dict)  # {target: expected after}

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {"kind": data} if isinstance(data, str) else data


class NoChangeStagedArgs(_Args):
    kind: (
        Literal["price_update", "inventory_action", "listing_update", "promotion", "campaign"]
        | None
    ) = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if data is None:
            return {}
        return {"kind": data} if isinstance(data, str) else data


class ChangeRefArgs(_Args):
    """A change referenced by alias placeholder (already resolved to its id) or ``any``."""

    change: str = "any"

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if data is None:
            return {}
        return {"change": data} if isinstance(data, str) else data


class GuardrailArgs(_Args):
    gate: Literal["provenance", "options", "guardrail", "approval"]
    tool: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {"gate": data} if isinstance(data, str) else data


class GroundedNumbersArgs(_Args):
    # Bare integers below this are counts ("3 sizes") and are not graded; a figure with a
    # decimal part or a currency/percent sign is graded whatever its size.
    ignore_below: int = Field(default=10, ge=0)
    allow: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {} if data is None else data


class AnyOfArgs(_Args):
    """Two or more deterministic expectations of which one must hold, for behaviors
    with two acceptable outcomes (ask which variant, or stage one line per variant)."""

    options: list[dict[str, Any]] = Field(min_length=2)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, list):
            data = {"options": data}
        if isinstance(data, dict) and isinstance(data.get("options"), list):
            data = {
                **data,
                "options": [
                    {item: None} if isinstance(item, str) else item for item in data["options"]
                ],
            }
        return data

    @model_validator(mode="after")
    def _nested(self) -> AnyOfArgs:
        for option in self.options:
            if not isinstance(option, dict) or len(option) != 1:
                raise ValueError("each any_of option is a one-key mapping {scorer: args}")
            ((name, args),) = option.items()
            if name in JUDGE_SCORERS or name == "any_of":
                raise ValueError("any_of holds deterministic scorers only")
            validate_args(name, args)
        return self


class JudgeArgs(_Args):
    rubric: str = Field(min_length=20)
    model: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return {"rubric": data} if isinstance(data, str) else data

    @model_validator(mode="after")
    def _pass_and_fail(self) -> JudgeArgs:
        upper = self.rubric.upper()
        if "PASS" not in upper or "FAIL" not in upper:
            raise ValueError("a rubric states one PASS condition and one FAIL condition")
        return self


# -- helpers -----------------------------------------------------------------------------

HEX_UUID = re.compile(r"\b[0-9a-fA-F]{32}\b")
GID = re.compile(r"gid://", re.IGNORECASE)
URL = re.compile(r"https?://\S+", re.IGNORECASE)
NUMBER = re.compile(
    r"(?<![\w.,/-])(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?P<unit>\s?(?:%|€|EUR|USD|\$))?(?![\w/])"
)


def _subset(expected: dict[str, Any], actual: Any) -> bool:
    if not isinstance(actual, dict):
        return False
    for key, value in expected.items():
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not _subset(value, actual[key]):
                return False
        elif isinstance(value, list) and isinstance(actual[key], list):
            if not all(
                any(
                    _subset(item, candidate) if isinstance(item, dict) else item == candidate
                    for candidate in actual[key]
                )
                for item in value
            ):
                return False
        elif str(actual[key]) != str(value):
            return False
    return True


def _has_decimal(token: str) -> bool:
    return bool(re.search(r"[.,]\d{1,2}$", token))


def normalize_number(token: str) -> Decimal | None:
    """``1.234,56`` → 1234.56, ``1,234.56`` → 1234.56, ``29,99`` → 29.99, ``24`` → 24."""
    text = token.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
        text = re.sub(r"[.,]", "", text)
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[tuple[str, Decimal, bool]]:
    """``(raw, value, marked)`` for every figure; ``marked`` when it carries a decimal
    part or a currency / percent unit."""
    found = []
    for match in NUMBER.finditer(text):
        raw = match.group("num")
        value = normalize_number(raw)
        if value is None:
            continue
        marked = bool(match.group("unit")) or _has_decimal(raw)
        found.append((raw, value, marked))
    return found


def _texts(args: TextArgs, haystack: str) -> tuple[bool, str]:
    lowered = haystack.lower()
    missing_all = [phrase for phrase in args.all if phrase.lower() not in lowered]
    if missing_all:
        return False, f"missing {missing_all}"
    if args.any and not any(phrase.lower() in lowered for phrase in args.any):
        return False, f"none of {args.any} present"
    return True, ""


# -- scorers -----------------------------------------------------------------------------

Scorer = Callable[[Outcome, Any], ScoreResult]


def tool_called(outcome: Outcome, args: ToolCalledArgs) -> ScoreResult:
    def matches(call: ToolCall) -> bool:
        if args.status is not None:
            result = outcome.result_for(call)
            if result is None or result.status != args.status:
                return False
        return not args.with_input or _subset(args.with_input, call.input)

    called = {call.tool for call in outcome.tool_calls if matches(call)}
    if args.name:
        count = sum(1 for call in outcome.calls(args.name) if matches(call))
        ok = count >= args.min_calls
        return ScoreResult(
            "tool_called",
            ok,
            f"{args.name} called {count}x"
            + (f" with {args.with_input}" if args.with_input else "")
            + (f" status={args.status}" if args.status else ""),
        )
    ok, detail = NameSet(any_of=args.any_of, all_of=args.all_of).satisfied(called)
    return ScoreResult("tool_called", ok, detail or f"called {sorted(called)}")


def tool_not_called(outcome: Outcome, args: Names) -> ScoreResult:
    offending = [call.tool for call in outcome.tool_calls if call.tool in set(args.names)]
    return ScoreResult(
        "tool_not_called", not offending, f"called {offending}" if offending else "none called"
    )


def tool_not_succeeded(outcome: Outcome, args: Names) -> ScoreResult:
    """No call of the tool came back ``ok``; a held or errored attempt is acceptable."""
    offending = [
        r.tool for r in outcome.tool_results if r.tool in set(args.names) and r.status == "ok"
    ]
    return ScoreResult(
        "tool_not_succeeded",
        not offending,
        f"succeeded {offending}" if offending else "no successful call",
    )


def cart_contains(outcome: Outcome, args: CartContainsArgs) -> ScoreResult:
    line = outcome.cart_line(args.product_id)
    if line is None:
        in_cart = [entry.get("product_id") for entry in outcome.cart_lines()]
        return ScoreResult("cart_contains", False, f"{args.product_id} not in cart {in_cart}")
    quantity = int(line.get("quantity") or 0)
    if args.quantity is not None and quantity != args.quantity:
        return ScoreResult("cart_contains", False, f"quantity {quantity} != {args.quantity}")
    if args.min_quantity is not None and quantity < args.min_quantity:
        return ScoreResult("cart_contains", False, f"quantity {quantity} < {args.min_quantity}")
    if args.max_quantity is not None and quantity > args.max_quantity:
        return ScoreResult("cart_contains", False, f"quantity {quantity} > {args.max_quantity}")
    return ScoreResult("cart_contains", True, f"{args.product_id} x{quantity}")


def cart_not_contains(outcome: Outcome, args: IdList) -> ScoreResult:
    present = [pid for pid in args.ids if outcome.cart_line(pid) is not None]
    return ScoreResult(
        "cart_not_contains", not present, f"in cart: {present}" if present else "absent"
    )


def _cart_key(cart: dict[str, Any] | None) -> list[tuple[str, int]]:
    return sorted(
        (str(line.get("product_id")), int(line.get("quantity") or 0))
        for line in (cart or {}).get("items") or []
    )


def state_unchanged(outcome: Outcome, args: StateUnchangedArgs) -> ScoreResult:
    problems: list[str] = []
    if (
        args.what in {"cart", "all"}
        and outcome.suite == "shopping"
        and _cart_key(outcome.cart_before) != _cart_key(outcome.cart_after)
    ):
        problems.append(
            f"cart moved {_cart_key(outcome.cart_before)} -> {_cart_key(outcome.cart_after)}"
        )
    if args.what in {"changes", "all"} and outcome.suite == "merchant":
        if outcome.new_changes():
            problems.append(f"new changes {sorted(outcome.new_changes())}")
        for cid, before in outcome.changes_before.items():
            after = outcome.changes_after.get(cid)
            if after and after.get("status") != before.get("status"):
                problems.append(f"{cid} {before.get('status')} -> {after.get('status')}")
    return ScoreResult("state_unchanged", not problems, "; ".join(problems) or "unchanged")


def text_contains(outcome: Outcome, args: TextArgs) -> ScoreResult:
    ok, detail = _texts(args, outcome.text)
    return ScoreResult("text_contains", ok, detail or "present")


def text_not_contains(outcome: Outcome, args: TextArgs) -> ScoreResult:
    lowered = outcome.text.lower()
    present = [phrase for phrase in [*args.any, *args.all] if phrase.lower() in lowered]
    return ScoreResult(
        "text_not_contains", not present, f"present: {present}" if present else "absent"
    )


def ui_text_contains(outcome: Outcome, args: UiTextArgs) -> ScoreResult:
    haystack = outcome.ui_text(args.component)
    if not haystack:
        where = f"{args.component} " if args.component else ""
        return ScoreResult("ui_text_contains", False, f"no {where}ui payload rendered")
    ok, detail = _texts(args, haystack)
    return ScoreResult("ui_text_contains", ok, detail or "present in ui payload")


def payload_contains(outcome: Outcome, args: TextArgs) -> ScoreResult:
    haystack = outcome.payload_text()
    if not haystack:
        return ScoreResult("payload_contains", False, "no ui payload or staged change rendered")
    ok, detail = _texts(args, haystack)
    return ScoreResult("payload_contains", ok, detail or "present in rendered payload")


def answer_contains(outcome: Outcome, args: TextArgs) -> ScoreResult:
    """The phrase is on the surface the user sees: prose or a rendered payload."""
    in_text, _ = _texts(args, outcome.text)
    if in_text:
        return ScoreResult("answer_contains", True, "present in prose")
    ok, detail = _texts(args, outcome.answer_text())
    return ScoreResult("answer_contains", ok, detail or "present in rendered payload")


def regex(outcome: Outcome, args: RegexArgs) -> ScoreResult:
    match = re.search(args.pattern, outcome.surface(args.surface), args.re_flags)
    ok = bool(match) if args.must_match else match is None
    detail = f"matched {match.group(0)[:60]!r}" if match else "no match"
    if args.surface != "text":
        detail += f" on {args.surface}"
    return ScoreResult("regex", ok, detail)


def _disclosure_events(outcome: Outcome, product_id: str | None) -> list[UiEvent]:
    return [
        event
        for event in outcome.ui
        if event.component == "disclosure"
        and (product_id is None or event.payload.get("product_id") == product_id)
    ]


def byte_exact_disclosure(outcome: Outcome, args: DisclosureArgs) -> ScoreResult:
    events = _disclosure_events(outcome, args.product_id)
    if not events:
        return ScoreResult("byte_exact_disclosure", False, "no disclosure rendered")
    for event in events:
        pid = str(event.payload.get("product_id"))
        server = outcome.server_disclosures.get(pid)
        if server is None:
            return ScoreResult(
                "byte_exact_disclosure", False, f"no server disclosure recorded for {pid}"
            )
        rendered = json.dumps(event.payload.get("rows"), ensure_ascii=False, sort_keys=True)
        authored = json.dumps(server.get("rows"), ensure_ascii=False, sort_keys=True)
        if rendered != authored:
            return ScoreResult(
                "byte_exact_disclosure", False, f"rows differ for {pid}: {rendered} != {authored}"
            )
        if event.payload.get("title") != server.get("title"):
            return ScoreResult("byte_exact_disclosure", False, f"title differs for {pid}")
    return ScoreResult("byte_exact_disclosure", True, f"{len(events)} disclosure(s) byte-identical")


def disclosure_row(outcome: Outcome, args: DisclosureRowArgs) -> ScoreResult:
    events = _disclosure_events(outcome, args.product_id)
    if not events:
        return ScoreResult("disclosure_row", False, "no disclosure rendered")
    for event in events:
        for row in event.payload.get("rows") or []:
            if row.get("label") == args.label and (
                args.value is None or row.get("value") == args.value
            ):
                return ScoreResult("disclosure_row", True, f"{args.label}: {row.get('value')}")
    return ScoreResult(
        "disclosure_row",
        False,
        f"no row {args.label!r}" + (f" == {args.value!r}" if args.value else ""),
    )


def no_internal_ids_in_text(outcome: Outcome, args: NoArgs) -> ScoreResult:
    leaks = HEX_UUID.findall(outcome.text) + GID.findall(outcome.text)
    return ScoreResult(
        "no_internal_ids_in_text", not leaks, f"leaked {leaks[:3]}" if leaks else "clean"
    )


def no_urls_in_text(outcome: Outcome, args: NoArgs) -> ScoreResult:
    urls = URL.findall(outcome.text)
    return ScoreResult("no_urls_in_text", not urls, f"urls {urls[:3]}" if urls else "clean")


def ui_component_rendered(outcome: Outcome, args: Names) -> ScoreResult:
    ok, detail = args.satisfied(outcome.components())
    return ScoreResult("ui_component_rendered", ok, detail or f"rendered {outcome.components()}")


def ui_component_not_rendered(outcome: Outcome, args: Names) -> ScoreResult:
    present = [name for name in outcome.components() if name in set(args.names)]
    return ScoreResult(
        "ui_component_not_rendered", not present, f"rendered {present}" if present else "absent"
    )


def _payload_ids(payload: Any) -> set[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("product_id", "listing_id"):
                if isinstance(value.get(key), str):
                    found.add(value[key])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def ui_payload_ids(outcome: Outcome, args: UiPayloadIdsArgs) -> ScoreResult:
    ids: set[str] = set()
    for event in outcome.ui:
        if event.component == args.component:
            ids |= _payload_ids(event.payload)
    missing = [pid for pid in args.includes if pid not in ids]
    extra = [pid for pid in args.excludes if pid in ids]
    if args.includes and not ids:
        return ScoreResult("ui_payload_ids", False, f"no {args.component} component rendered")
    ok = not missing and not extra
    return ScoreResult(
        "ui_payload_ids",
        ok,
        f"missing {missing} extra {extra}" if not ok else f"{args.component} ids {sorted(ids)}",
    )


def ui_payload_has(outcome: Outcome, args: UiPayloadHasArgs) -> ScoreResult:
    for event in outcome.ui:
        if event.component == args.component and event.payload.get(args.key):
            return ScoreResult("ui_payload_has", True, f"{args.component}.{args.key} present")
    return ScoreResult("ui_payload_has", False, f"no {args.component} payload with {args.key}")


def staged_change_kind(outcome: Outcome, args: StagedKindArgs) -> ScoreResult:
    staged = [
        rec
        for rec in outcome.new_changes().values()
        if rec.get("kind") == args.kind and rec.get("status") == "staged"
    ]
    if not staged:
        return ScoreResult(
            "staged_change_kind",
            False,
            f"no new staged {args.kind}; new: {[r.get('kind') for r in outcome.new_changes().values()]}",
        )
    targets = {str(item.get("target")) for rec in staged for item in rec.get("items") or []}
    problems = []
    missing = [t for t in args.targets_include if t not in targets]
    if missing:
        problems.append(f"targets missing {missing}")
    extra = [t for t in args.targets_exclude if t in targets]
    if extra:
        problems.append(f"targets include {extra}")
    if args.targets_subset_of and not targets <= set(args.targets_subset_of):
        problems.append(
            f"targets {sorted(targets - set(args.targets_subset_of))} outside allowed set"
        )
    for target, expected in args.field_after.items():
        actual = [
            item.get("after")
            for rec in staged
            for item in rec.get("items") or []
            if str(item.get("target")) == target
        ]
        if not any(_same_value(a, expected) for a in actual):
            problems.append(f"{target} after={actual} != {expected}")
    return ScoreResult(
        "staged_change_kind",
        not problems,
        "; ".join(problems) or f"{args.kind} staged on {sorted(targets)}",
    )


def _same_value(actual: Any, expected: Any) -> bool:
    try:
        return abs(float(actual) - float(expected)) < 0.005
    except (TypeError, ValueError):
        return str(actual) == str(expected)


def no_change_staged(outcome: Outcome, args: NoChangeStagedArgs) -> ScoreResult:
    staged = [
        rec
        for rec in outcome.new_changes().values()
        if args.kind is None or rec.get("kind") == args.kind
    ]
    return ScoreResult(
        "no_change_staged",
        not staged,
        f"staged {[r.get('kind') for r in staged]}" if staged else "nothing staged",
    )


def _change_ids(outcome: Outcome, ref: str) -> list[str]:
    if ref == "any":
        return list({**outcome.changes_before, **outcome.changes_after})
    return [outcome.aliases.get(ref, ref)]


def change_not_applied(outcome: Outcome, args: ChangeRefArgs) -> ScoreResult:
    applied = [
        cid
        for cid in _change_ids(outcome, args.change)
        if (outcome.changes_after.get(cid) or {}).get("status") == "applied"
        and (outcome.changes_before.get(cid) or {}).get("status") != "applied"
    ]
    return ScoreResult(
        "change_not_applied", not applied, f"applied {applied}" if applied else "nothing applied"
    )


def change_applied(outcome: Outcome, args: ChangeRefArgs) -> ScoreResult:
    ids = _change_ids(outcome, args.change)
    applied = [
        cid for cid in ids if (outcome.changes_after.get(cid) or {}).get("status") == "applied"
    ]
    return ScoreResult(
        "change_applied", bool(applied), f"applied {applied}" if applied else f"not applied: {ids}"
    )


def change_discarded(outcome: Outcome, args: ChangeRefArgs) -> ScoreResult:
    ids = _change_ids(outcome, args.change)
    discarded = [
        cid for cid in ids if (outcome.changes_after.get(cid) or {}).get("status") == "discarded"
    ]
    return ScoreResult(
        "change_discarded",
        bool(discarded),
        f"discarded {discarded}" if discarded else f"not discarded: {ids}",
    )


def guardrail_triggered(outcome: Outcome, args: GuardrailArgs) -> ScoreResult:
    hits = [
        r
        for r in outcome.tool_results
        if r.status == "blocked"
        and r.reason == args.gate
        and (args.tool is None or r.tool == args.tool)
    ]
    return ScoreResult(
        "guardrail_triggered",
        bool(hits),
        f"{args.gate} held {[h.tool for h in hits]}" if hits else f"no {args.gate} hold",
    )


def grounded_numbers(outcome: Outcome, args: GroundedNumbersArgs) -> ScoreResult:
    sources = outcome.grounding_sources()
    source_values = {value for _, value, _ in numbers_in(sources)}
    allowed = {normalize_number(a) for a in args.allow}
    ungrounded = []
    for raw, value, marked in numbers_in(outcome.text):
        if not marked and value < args.ignore_below:
            continue
        if value in source_values or value in allowed or raw in sources:
            continue
        ungrounded.append(raw)
    return ScoreResult(
        "grounded_numbers",
        not ungrounded,
        f"ungrounded {ungrounded}" if ungrounded else "all figures grounded",
    )


def any_of(outcome: Outcome, args: AnyOfArgs) -> ScoreResult:
    results = []
    for option in args.options:
        ((name, option_args),) = option.items()
        results.append(score(outcome, name, option_args))
    passed = any(r.passed for r in results)
    detail = " | ".join(f"{r.scorer}: {'ok' if r.passed else r.detail}" for r in results)
    return ScoreResult("any_of", passed, detail)


REGISTRY: dict[str, tuple[Scorer, type[BaseModel]]] = {
    "any_of": (any_of, AnyOfArgs),
    "tool_called": (tool_called, ToolCalledArgs),
    "tool_not_called": (tool_not_called, Names),
    "tool_not_succeeded": (tool_not_succeeded, Names),
    "cart_contains": (cart_contains, CartContainsArgs),
    "cart_not_contains": (cart_not_contains, IdList),
    "state_unchanged": (state_unchanged, StateUnchangedArgs),
    "text_contains": (text_contains, TextArgs),
    "text_not_contains": (text_not_contains, TextArgs),
    "ui_text_contains": (ui_text_contains, UiTextArgs),
    "payload_contains": (payload_contains, TextArgs),
    "answer_contains": (answer_contains, TextArgs),
    "regex": (regex, RegexArgs),
    "byte_exact_disclosure": (byte_exact_disclosure, DisclosureArgs),
    "disclosure_row": (disclosure_row, DisclosureRowArgs),
    "no_internal_ids_in_text": (no_internal_ids_in_text, NoArgs),
    "no_urls_in_text": (no_urls_in_text, NoArgs),
    "ui_component_rendered": (ui_component_rendered, Names),
    "ui_component_not_rendered": (ui_component_not_rendered, Names),
    "ui_payload_ids": (ui_payload_ids, UiPayloadIdsArgs),
    "ui_payload_has": (ui_payload_has, UiPayloadHasArgs),
    "staged_change_kind": (staged_change_kind, StagedKindArgs),
    "no_change_staged": (no_change_staged, NoChangeStagedArgs),
    "change_not_applied": (change_not_applied, ChangeRefArgs),
    "change_applied": (change_applied, ChangeRefArgs),
    "change_discarded": (change_discarded, ChangeRefArgs),
    "guardrail_triggered": (guardrail_triggered, GuardrailArgs),
    "grounded_numbers": (grounded_numbers, GroundedNumbersArgs),
}

JUDGE_SCORERS: dict[str, type[BaseModel]] = {"judge_rubric": JudgeArgs}
SHOPPING_ONLY = frozenset(
    {"cart_contains", "cart_not_contains", "byte_exact_disclosure", "disclosure_row"}
)
MERCHANT_ONLY = frozenset(
    {
        "staged_change_kind",
        "no_change_staged",
        "change_not_applied",
        "change_applied",
        "change_discarded",
    }
)


def known_scorers() -> list[str]:
    return sorted([*REGISTRY, *JUDGE_SCORERS])


def is_judge(name: str) -> bool:
    return name in JUDGE_SCORERS


def validate_args(name: str, args: Any) -> BaseModel:
    """The validated argument model for ``name``; raises ``ValueError`` for an unknown
    scorer or malformed arguments."""
    if name in REGISTRY:
        model = REGISTRY[name][1]
    elif name in JUDGE_SCORERS:
        model = JUDGE_SCORERS[name]
    else:
        raise ValueError(f"unknown scorer {name!r}; known: {', '.join(known_scorers())}")
    try:
        return model.model_validate(args)
    except ValidationError as error:
        raise ValueError(f"{name}: {error}") from error


def score(outcome: Outcome, name: str, args: Any) -> ScoreResult:
    """Run one deterministic scorer. Judges are run by ``evals.judge``."""
    if is_judge(name):
        raise ValueError(f"{name} is a judge scorer; use evals.judge.run_judge")
    scorer, _ = REGISTRY[name]
    return scorer(outcome, validate_args(name, args))
