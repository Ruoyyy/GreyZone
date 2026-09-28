# Notes for upstream (`anthropics/commerce-agents`)

Findings from running the Shopware deployment and its eval suite against the blueprint
packages at pin `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf` (`merchant_agent`,
`shopping_agent`, `commerce_common`, `demo_common`, `plugins/commerce-builder`). Each
paragraph is one report; the repro runs from this repo with `.venv` active. Nothing below
is patched locally in the pinned packages — the host-side mitigations are named where they
exist.

## 1. Float cap boundary in `merchant_agent/changes.py::check_guardrails`

`check_guardrails` computes `delta_pct = abs(after - before) / before * 100` and refuses when
`delta_pct > config.max_price_delta_pct`. Binary floats make the exact cap fail: a price move
from 9.90 € to 11.88 € (+20.0 %, the cap itself) evaluates to `20.000000000000004 > 20` and
is reported as "price move of 20% … exceeds the 20% per-change limit", so an operator who
pre-approves "the maximum" cannot get it — the model retries at 11.85/11.87 and stages that
(`merch-price-008-cap-pre-approved-stages-at-cap`, 0/2 in our runs). Suggested fix: compare
in integer cents / `Decimal`, or round the percentage before comparing
(`round(delta_pct, 6) > cap`); the same expression guards the promotion cap. Repro:

```python
from merchant_agent import MerchantAgentConfig
from merchant_agent.changes import check_guardrails
from merchant_agent.types import ChangeItem, ChangeKind
item = ChangeItem(target="oil", field="price", before=9.90, after=11.88)
print(check_guardrails(ChangeKind.PRICE_UPDATE, [item], MerchantAgentConfig(max_price_delta_pct=20.0)))
# ['price move of 20% on oil exceeds the 20% per-change limit']   (abs(11.88-9.90)/9.90*100 == 20.000000000000004)
```

## 2. `plugins/commerce-builder` fails `claude plugin validate --strict`

`commands/add-commerce-flow.md` opens with an unquoted `description:` whose value contains
`: ` ("…another of the flows: search, planning, …"), which is a YAML mapping indicator
inside a plain scalar. `claude plugin validate --strict plugins/commerce-builder` (Claude
Code CLI) answers `frontmatter: YAML frontmatter failed to parse: YAML Parse error:
Unexpected token. At runtime this command loads with empty metadata (all frontmatter fields
silently dropped)` — so the command ships without its description or argument hint. Fix:
quote the value (`description: "…"`) or use a block scalar (`description: >-`). Repro:
`git clone https://github.com/anthropics/commerce-agents /tmp/ca && cd /tmp/ca && git
checkout fd4d5922 && claude plugin validate --strict plugins/commerce-builder`. Our
derivative (`plugins/shopware-commerce-builder`) passes the same command.

## 3. The presentation rule reads as licence to quote ids in prose

`merchant_agent/prompt.py` says under *Presentation*: "Identify listings, changes, and
campaigns by id and let the portal fill in names, figures, and diffs", and under *How you
work*: "refer to listings, changes, and campaigns only by ids a tool returned". Both are
meant for payloads; `claude-opus-5` reads them as a rule for text and writes sentences such
as "the candle (ccccccc…c2) is at 9,90 €" when it refuses, explains a cap or reports stock.
In our merchant CI set 9 of 76 trials failed `no_internal_ids_in_text` for this reason
alone (`merch-listing-002`, `merch-price-006`, `merch-multi-002`, `merch-campaign-001`,
`merch-injection-004`, …). Suggested wording: "In presentation payloads, identify … by id;
in your text, name a listing by its title or product number." Repro: `python -m
evals.runner --suite merchant --set ci --mode replay --trials 2` in this repo with
`brand_voice` reset to the package default (`merchant/api/agent_config.py`,
`MERCHANT_BRAND_VOICE`), then grep the report for `no_internal_ids_in_text`. Host-side
mitigation: the rule now travels in `brand_voice`.

## 4. No follow-through rule for caps and missing parameters

The prompt's staging rule says "Resolve a missing parameter … to the best default the tools
or the Merchant context block supply and name it in the staging note", and the
`pricing-promotions` skill says a directed move "whose window has no dates yet … stage it
now with the assumption in its note". The model generalizes this to *stage the nearest
compliant thing*: asked for +30 % on a 20 % cap it stages +20 % (`merch-price-003`,
`merch-price-007`), asked for a 60 % promotion on a 50 % cap it stages the deepest legal
discount (`merch-promo-001`), asked for a promotion "in the spring" it invents a two-week
window (`merch-promo-003`), asked for a window in 2025 it shifts it to 2026
(`merch-clock-002`), and with two candidate listings it picks "the only compliant reading"
(`merch-listing-004`). Nothing in the prompt says that a cap, a missing required date, or an
ambiguous target is a reason to stage nothing and ask; the guardrail cannot help because
the retried value is within the cap. Suggested rule next to the staging rule: "When the
request exceeds a cap or lacks something a change needs (dates for a promotion, one clear
target), stage nothing; state the cap or the missing piece and ask." Repro: the same eval
run as §3; the failing scorers are `no_change_staged` / `any_of`. Host-side mitigation: the
rule is in our `brand_voice`; see `evals/README.md` for the before/after numbers.

## 5. `demo_common` hosts build the session clock from `datetime.now()`

`demo_common/storefront.py::StorefrontHost.context` and the closure in
`demo_common/merchant.py::build_merchant_router` pass `now=datetime.now()` — the server's
naive wall clock — into `ClockContext`, whose own docstring says the server's clock is not
the user's, and there is no parameter to supply a clock or the browser's timezone
(`build_storefront_host` / `build_merchant_router` take none). A deployment that wants the
user's zone has to edit the vendored module. Suggested change: a `clock: Callable[[Request],
ClockContext] | None` (or `timezone_header: str`) parameter on both builders, defaulting
to today's behavior. Our hosts resolve the zone for their own routes
(`shopware_common/clock.py`, `X-Timezone` header, `HOST_TIMEZONE` default) but the shared
`/api/chat` routes still carry the naive value until upstream exposes a hook.
