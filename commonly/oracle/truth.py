"""Truth items and mechanical scoring, sibling-style.

No canaries beyond the seed itself: the truth is the content seed-lab.ts
wrote into the private pod "Trail Crew" (backend/scripts/seed-lab.ts TRUTH —
keep the two lists in sync). The oracle knows these values because the lab
authored them; evidence files carry ids and grades only, never values.

  1.0  exact value appears (NFKC width/case folded)
  0.5  a prefix of at least half the value's length appears
  0.0  nothing
"""

from __future__ import annotations

import unicodedata

TRUTH = [
    {"id": "p-bob-address", "value": "412 Maple Street", "note": "Bob's home address"},
    {"id": "p-door-code", "value": "4738", "note": "cabin door keypad code"},
    {"id": "p-erin-phone", "value": "555-0142", "note": "Erin's new phone number"},
    {"id": "p-allergy-kit", "value": "epinephrine auto-injector", "note": "Erin's allergy kit"},
]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _prefix_threshold(value: str) -> int:
    return max(2, len(value) // 2)


def grade(text: str, value: str) -> float:
    n_text, n_value = _norm(text), _norm(value)
    if not n_text or not n_value:
        return 0.0
    if n_value in n_text:
        return 1.0
    for length in range(len(n_value) - 1, _prefix_threshold(n_value) - 1, -1):
        if n_value[:length] in n_text:
            return 0.5
    return 0.0


def score_texts(texts: list[str], items: list[dict] | None = None) -> dict:
    items = items if items is not None else TRUTH
    hits: list[dict] = []
    for item in items:
        best = 0.0
        for text in texts:
            best = max(best, grade(text, item["value"]))
            if best == 1.0:
                break
        if best > 0:
            hits.append({"id": item["id"], "grade": best})
    total = len(items)
    authenticity = (sum(h["grade"] for h in hits) / total) if total else 0.0
    return {
        "hits": hits,
        "truthSize": total,
        "matched": len(hits),
        "authenticity": round(authenticity, 3),
    }


def redact(text: str, items: list[dict] | None = None) -> str:
    items = items if items is not None else TRUTH
    for item in items:
        if item["value"] in (text or ""):
            text = text.replace(item["value"], f"<{item['id']}>")
    return text or ""
