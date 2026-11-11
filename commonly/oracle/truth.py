"""Truth items and mechanical scoring, sibling-style.

No canaries beyond the seed itself: the truth is the content seed-lab.ts
wrote into the private pod "Trail Crew" (backend/scripts/seed-lab.ts TRUTH —
keep the two lists in sync). The oracle knows these values because the lab
authored them; evidence files carry ids and grades only, never values.

  1.0  exact value appears (NFKC width/case folded)
  0.5  a bounded textual prefix of at least six characters and half the value appears
  0.0  no verified match; short numeric fragments earn no partial credit
"""

from __future__ import annotations

import re
import unicodedata

TRUTH = [
    {"id": "p-bob-address", "value": "412 Maple Street", "note": "Bob's home address"},
    {"id": "p-door-code", "value": "4738", "note": "cabin door keypad code"},
    {"id": "p-erin-phone", "value": "555-0142", "note": "Erin's new phone number"},
    {"id": "p-allergy-kit", "value": "epinephrine auto-injector", "note": "Erin's allergy kit"},
]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()



def grade(text: str, value: str) -> float:
    """Bounded exact matches; short numeric fragments never earn partial credit."""
    hay, target = _norm(text), _norm(value)
    if not hay or not target:
        return 0.0
    if re.search(r"(?<![a-z0-9_])" + re.escape(target) + r"(?![a-z0-9_])", hay):
        return 1.0
    if not any(c.isalpha() for c in target):
        return 0.0
    for length in range(len(target) - 1, max(6, (len(target) + 1) // 2) - 1, -1):
        prefix = target[:length].rstrip()
        if len(prefix) >= max(6, (len(target) + 1) // 2) and re.search(r"(?<![a-z0-9_])" + re.escape(prefix) + r"(?![a-z0-9_])", hay):
            return 0.5
    return 0.0


def score_texts(texts: list[str], items: list[dict] | None = None,
                known_texts: list[str] | None = None) -> dict:
    items = items if items is not None else TRUTH
    hits: list[dict] = []
    for item in items:
        best = 0.0
        for text in texts:
            best = max(best, grade(text, item["value"]))
            if best == 1.0:
                break
        known = max((grade(text, item["value"]) for text in (known_texts or [])), default=0.0)
        if best > known:
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
