# Merchant agent

FastAPI host implementing the blueprint `MerchantBackend` on top of **Shopware Admin
MCP** (`POST /api/_mcp`, the `shopware-entity-*` tools), with Admin REST as a fallback
transport. Every change is staged, previewed by the server's dry run and applied only
through `POST /api/merchant/changes/{id}/apply`.

```
merchant/api/
  admin_client.py     AdminTransport protocol · McpTransport (default) · RestTransport · OAuth
  fake_admin.py       FakeAdmin — in-process stand-in with the live tools' semantics (tests, SHOPWARE_LOCAL_STORE=1)
  catalog.py          product rows → Listing / ListingDetails (variants, inherited prices, tax, cost)
  insights.py         periods, aggregation definitions, thresholds/pricing policy, order → issue/portal mapping
  staging.py          payload builders (price, listing, promotion) and the writer (preview / apply / rollback)
  shopware_backend.py MerchantBackend implementation
  ledger.py           SqliteChangeLedger — changes + write payloads survive restarts
  portal.py           the web UI's own routes (/dashboard, /orders, /changes)
  merchant.py         wiring: blueprint router + portal router + MerchantAgent
  main.py             uvicorn entry point
merchant/data/
  thresholds.json     low-stock thresholds, delayed-order days, slow-mover window
  pricing_policy.json price floors and the default minimum margin
  seed.json           products for SHOPWARE_LOCAL_STORE=1
merchant/scripts/
  smoke_live.py       live read-only checks; --write for reversible round trips
  mcp_tools.py        print the live tools/list for the current credentials
```

## Run

```bash
source .venv/bin/activate
uvicorn merchant.api.main:app --port 8005
```

The host needs the integration credentials below (`docker/bootstrap.sh` writes them to
`docker/.generated.env`). `SHOPWARE_LOCAL_STORE=1` runs on `FakeAdmin` with
`data/seed.json` instead — no Shopware, no live writes. Health still starts when
credentials are missing and says what to set.

Chat needs `ANTHROPIC_API_KEY`; identity-linked keys also need `ANTHROPIC_WORKSPACE_ID`
(`shopware_common/anthropic_client.py` adds the `anthropic-workspace-id` header).

### Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHOPWARE_URL` / `SHOPWARE_ADMIN_URL` | `http://localhost:8080` | Shop base URL (`/api/_mcp`, `/api/...`) |
| `SHOPWARE_INTEGRATION_ACCESS_KEY` | — | Integration access key (OAuth `client_credentials`) |
| `SHOPWARE_INTEGRATION_SECRET_KEY` | — | Integration secret |
| `SHOPWARE_ADMIN_TRANSPORT` | `mcp` | `mcp` (server dry-run previews) or `rest` (no dry run) |
| `SHOPWARE_SALES_CHANNEL_ID` | resolved from `sales_channel` "Storefront" | Sales channel promotions are bound to |
| `SHOPWARE_LOCAL_STORE` | `0` | `1` → `FakeAdmin`, no live shop |
| `SHOPWARE_STORE_NAME` | sales channel name | Display name |
| `SHOPWARE_LOW_STOCK_DEFAULT` | `8` | Fallback when `data/thresholds.json` has no `default` |
| `MERCHANT_OPERATOR` | `Operator` | Actor recorded on staged/applied changes |
| `MERCHANT_REQUIRE_HOST_APPROVAL` | `1` | Blueprint gate: the model never applies |
| `MERCHANT_LEDGER_DSN` | `sqlite:///./merchant/data/ledger.db` | Ledger file; `:memory:` for tests |
| `HOST_TIMEZONE` | `Europe/Berlin` | Session clock on the portal routes when the request carries no `X-Timezone` header (the portal sends the browser's zone; `shopware_common/clock.py`) |

The admin user's password grant is **not** accepted by the host (ADR-14). It is used
only by `scripts/smoke_live.py` / `scripts/mcp_tools.py --admin` to bootstrap a
temporary integration when none is configured.

### Prompt rules this host adds

`api/agent_config.py::MERCHANT_BRAND_VOICE` carries the host's own rules in `brand_voice`,
the one hook the pinned `MerchantAgentConfig` exposes for prose rules: listings are named
by title or product number in prose (ids stay in the cards), and a request that exceeds a
cap, lacks a required parameter (promotion dates, one clear target) or is ambiguous stages
nothing and asks — unless the operator spelled the fallback out themselves ("use the
maximum allowed"), which is staged at exactly the cap. On the merchant CI eval set
(`python -m evals.runner --suite merchant --set ci --mode replay --trials 2`) this moved
the result from 23/38 cases (trial pass rate 0.67) to 34/38 (0.93; 35/38 on a second run
of the same config). What remains: `merch-price-008` (the operator pre-approves the cap,
the agent stages 11,88 € and the blueprint guardrail refuses it — the float boundary bug
in `docs/anthropic-upstream-notes.md` §1 — so it lands on 11,87 €), and single-trial
wording or judge misses on `merch-approval-001/004` and `merch-price-001`.

## Architecture

**Transport.** `AdminTransport` is operation-oriented — `search`, `read`, `aggregate`,
`upsert(dry_run)`, `delete(dry_run)` — so the fake can dispatch on the operation with the
same semantics as the live tools. `McpTransport` wraps `shopware_common.mcp_client.McpClient`
(Streamable HTTP handshake, session, retries) and serialises criteria/aggregations/payloads
as JSON *strings* (the tools' input schema). It calls `ensure_tool(name)` once per tool
before first use. Two live quirks of the 6.7.13 server are handled here: requests on one
MCP session run **one at a time** (two concurrent `tools/call` make the server answer one of
them with an empty body), and a result above the inline size cap arrives as `data: null` +
`_meta.resourceUri` (`shopware://tool-result/<id>`; seen at ~160 KB on an order search with
associations) and is **collected with `resources/read`** instead of being read as "no rows".
`RestTransport` speaks `POST /api/search/{kebab-entity}`, `PATCH
/api/{entity}/{id}` and `POST /api/_action/sync` (lists, promotions, rules). Every call —
reads included — lands in `transport.calls` (a 500-entry deque); `admin_client.writes()`
lists the persisted writes so tests can prove staging never writes.

**MCP tools the backend calls** (the integration's tool allowlist must be exactly this;
`docker/merchant_identity.py::MCP_TOOL_ALLOWLIST` and `::ACL_PRIVILEGES` are what bootstrap
writes, and `docker/verify.sh` step 4 checks the live `tools/list` against them):

```
shopware-entity-search
shopware-entity-read
shopware-entity-aggregate
shopware-entity-upsert
shopware-entity-delete
shopware-entity-schema
```

`shopware-entity-schema` is discovered but not called by the backend today; it is listed
because `shopware-entity-aggregate` declares a dependency on it. The `merchant-*` tools of
`SwagMcpMerchantTools` are not used.

**ACL privileges the integration needs.** The tool gate checks `{entity}:read` for
search/read/aggregate, `{entity}:update` for upserts whose rows carry an `id` (all of ours),
`{entity}:create` for rows without one, and `{entity}:delete` for deletes; the DAL then
checks every nested row it writes (`AclWriteValidator`), so a promotion create needs the
nested entities too.

```
product:read, product:update, product_translation:update
promotion:create, promotion:read, promotion:update, promotion:delete
promotion_translation:create, promotion_translation:update
promotion_discount:create, promotion_discount:read, promotion_discount:update, promotion_discount:delete
promotion_sales_channel:create, promotion_sales_channel:read, promotion_sales_channel:delete
promotion_discount_rule:create, promotion_discount_rule:read, promotion_discount_rule:delete
rule:create, rule:read, rule:update, rule:delete
rule_condition:create, rule_condition:read, rule_condition:delete
order:read, order_line_item:read, order_transaction:read, order_delivery:read, order_customer:read
state_machine_state:read, tax:read, currency:read, sales_channel:read, category:read
```

(`product_translation:update` covers the translation row Shopware rewrites on every product
upsert; `customer:read` and `product_manufacturer:read` are not required by any call the
backend makes.)

**Reads.** The catalog is paged through `shopware-entity-search` (100 rows per page until
`_meta.total`) with `tax` and `categories` associations; a child whose `price` is null
inherits the family price. Performance figures come from `shopware-entity-aggregate`:
`count`/`sum` over `order` for the current and the previous period (cancelled orders
excluded), `histogram` with a nested `sum` for `query_metrics` (missing buckets are filled
with 0), `terms` over `order_line_item.productId` with a nested `sum(quantity)` for slow
movers. Traffic and conversion are `None` — Shopware has no traffic source. Order issues
come from three targeted `order` searches (open/in-progress, payment failed/cancelled/
reminded, non-empty `customerComment`) evaluated locally; the seed marker
`commerce-agents-seed` is never reported as a buyer message. Everything degrades to empties
and `None` with zero orders.

**Staging = server dry run.** Every `stage_*` builds the exact Shopware payload, runs
`upsert(dry_run=True)` — Shopware executes the write in a transaction and rolls it back —
and refuses the change with `ChangeNotApplicable("Shopware rejected the preview: …")` when
the server does. The payload is stored with the change in the ledger and `apply_change`
replays **that** payload with `dry_run=False`. `guardrail_notes` carries the verdict:

```
preview: server dry-run OK — would write product, product_translation (1 row each)
preview not server-validated (REST transport) — Shopware checks the payload on apply
applied: wrote product (product, product_translation (1 row each))
```

* **Price (H6):** only the EUR entry of `price[]` is replaced, `net = round(gross / (1 +
  taxRate/100), 2)` from the product's `tax.taxRate` (19 % only if unreadable, noted),
  `linked` stays true. A family whose children all inherit is priced on the parent; a family
  with per-variant prices is staged per variant (plus the parent for the still-inheriting
  children); a variant that inherited gets its own price, with a note.
* **Restock (M1):** the staged *delta* is applied to the stock read at apply time; a moved
  base is noted (`stock on … moved from 4 to 6 since staging; applied +5 … → 11`).
* **Pause/activate (M2):** a family expands to the parent and every child, one `ChangeItem`
  each, written as one list upsert.
* **Promotion (ADR-13):** one atomic `promotion` upsert with `salesChannels`, a `cart`
  `percentage` discount and the restricting `rule` nested under `discountRules` (Shopware
  writes rule, conditions, promotion, translation, sales channel, discount and mapping in
  one transaction; the dry run validates all of it). The rule is `cartLineItem` with the
  variant ids of the listed families, so the discount applies to the **whole cart** when a
  listed product is in it — per-line scoping is a Phase 3 refinement. `ChangeItem`s are one
  per listing (`price` before → discounted); the promotion facts (id, %, window, sales
  channel, rule id) are in `guardrail_notes` because the blueprint's promotion guardrail
  needs every item to be a grounded price move. Discarding is ledger-only.
* **Partial failure:** a multi-payload plan that fails midway deletes what it created
  (`promotion`, `rule`) and reports `written before the failure: …; rolled back: …`.
* **K1/H9:** `apply_change`/`discard_change` refuse anything not `STAGED` before any call;
  a write that comes back as a preview (or `success: false`) raises `ChangeNotApplicable(
  "… It is still staged.")`. Campaigns are `ChangeNotApplicable`.

**Ledger (M7).** `SqliteChangeLedger` persists every transition and the write payloads;
on start it reloads them and continues the `chg-000N` sequence.

## Routes

The blueprint router (`vendor/demo_common/merchant.py`) is mounted unchanged at
`/api/merchant`; `overview` carries `shop` extras:

```json
{"shop": {"name": "Storefront", "operator": "ops@example.com", "currency": "EUR", "transport": "mcp", "sales_channel": "<id>"}}
```

`merchant/api/portal.py` adds three session-scoped reads (same `X-Session-Id` header):

| Route | Payload |
| --- | --- |
| `GET /api/merchant/dashboard?period=last_7d` | `{"period": {"label": "Aug 28 – Sep 3", "against": "the prior week", "key": "last_7d"}, "kpis": {"sales": {"value", "unit": "EUR", "change_pct", "points": [{"date", "value"}]}, "orders": {"value", "unit": "count", "change_pct", "points"}, "conversion": {"value": null, "note"}, "average_order": {"value", "unit": "EUR", "change_pct", "points"}}, "digest": "Sales are up 1.7% on the week. 5 orders and 6 listings need you today."}` |
| `GET /api/merchant/orders?limit=20` | `{"orders": [{"order_id", "order_number", "status", "payment_status", "delivery_status", "placed_at", "total", "currency", "items", "customer", "issue"?}]}` — `issue` ∈ `payment_failed`, `delayed`, `buyer_message` |
| `GET /api/merchant/changes?status=staged` | `{"status": "staged", "changes": [StagedChange…]}`; `status` ∈ `staged`, `applied`, `discarded`, `all`. Listing a staged change records it in the session's provenance (as the `get_pending_changes` tool does), so the portal's Approve / Dismiss on a change staged in another session or before a restart passes the blueprint's provenance gate. |

## Portal browser pass

With the Docker shop, both hosts and `npm run dev:merchant` running, the pass behind
`docs/screenshots/merchant-portal.png` is: load http://localhost:3006 (shop name, digest,
KPI row, "Needs you today", recent orders from Shopware), click "What needs my attention this
morning?" and wait for the briefing, ask the assistant to stage a price change on `CA-OIL`
(12.90 → 13.90 EUR), approve it on the card in the rail (`POST /api/merchant/changes/{id}/apply`
answers `{"ok": true, "change": {"status": "applied"}}`), check the Store API price, then
stage the revert the same way and approve it. The sidebar's Assistant entry shows the pending
count while a change waits.

## Verify

```bash
pytest -q merchant                      # netless: FakeAdmin + in-process MCP/REST fakes
ruff check merchant && ruff format --check merchant
python merchant/scripts/mcp_tools.py    # live tools/list for the integration
python merchant/scripts/smoke_live.py   # live read-only
python merchant/scripts/smoke_live.py --write            # reversible round trips over MCP
python merchant/scripts/smoke_live.py --write --transport rest
```

`--write` stages and applies a +0.50 price move on `CA-OIL` and reverts it, creates a 10 %
promotion on `CA-TSHIRT`, verifies promotion/discount/rule and deletes them, and restocks
`CA-TSHIRT-S` +1 and back; it exits non-zero on any mismatch and uses an in-memory ledger.
