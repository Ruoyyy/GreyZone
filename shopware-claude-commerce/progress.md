# Progress

Shopware × Claude Commerce Agents — phases 0–2 complete and stabilized (2026-09-03).

## Final truth table (acceptance pass, 2026-09-03)

Every finding of the stabilization brief, with the file(s) that carry the fix and the
command that proved it on this date. All commands were re-run for this table:
`pytest -q` (183 passed; `evals/tests` 83 passed), `ruff check storefront merchant
shopware_common docker evals` (clean; the two remaining `ruff check .` findings are in
`browser-demo/`, another agent's tree), `python storefront/scripts/smoke.py` (MCP and REST,
signed), `python merchant/scripts/smoke_live.py --read-only` and `--write` (7 reversible
writes), `npm run build -w storefront/web` and `-w merchant/web`, `./docker/bootstrap.sh`
twice then `curl -s http://localhost:8080/.well-known/ucp | jq '.signing_keys | length'` → `1`,
`docker/verify.sh` (all six sections pass).

| # | Finding | Status | Where | Proof |
|---|---|---|---|---|
| K1 | `apply_change` / `discard_change` on a non-STAGED change | fixed | `merchant/api/shopware_backend.py` (`_require_staged`), `merchant/api/ledger.py` | `test_staging.py::test_apply_refuses_anything_not_staged`, `::test_failed_write_leaves_change_staged` |
| K2 | Raw context token in the handoff URL | fixed (ADR-10) | `shopware_common/handoff.py`, `storefront/api/handoff.py`, `docker/plugins/CommerceAgentsHandoff` | `shopware_common/tests/test_handoff.py`, `test_shopware_backend.py::test_the_handoff_code_decrypts_to_the_cart_token_and_is_single_use`, PHPUnit `HandoffCodeVerifierTest`, `verify.sh` §5 (POST → `/checkout/confirm`, replay and GET-with-used-code → `/checkout/cart`) |
| H1/H2 | Variants: parent row as SKU, children missing, out-of-stock silent | fixed | `storefront/api/shopware_backend.py` (`_fetch_family_record`, `_enrich_variants`, `_resolve_variant`) | `test_shopware_backend.py::test_the_parent_row_in_variants_is_not_a_child_sku`, `::test_details_list_real_children_including_out_of_stock`, `::test_out_of_stock_variant_raises_unavailable_with_siblings`, smoke "seeded T-shirt lists size variants" |
| H3 | Orders from a UCP checkout-session ledger | fixed | `storefront/api/shopware_backend.py::get_orders/get_order` over `POST /store-api/order` with the cart token | `test_shopware_backend.py::test_orders_come_from_the_store_api_behind_the_cart_token`, smoke "orders behind the cart token" |
| H4/H9 | Ad-hoc UCP-MCP fallback / admin "MCP mode" without a client | fixed (ADR-12 rev.: MCP first-class) | `shopware_common/mcp_client.py`, `storefront/api/ucp_client.py`, `merchant/api/admin_client.py` (`McpTransport`, `RestTransport`) | `shopware_common/tests/test_mcp_client.py`, `test_ucp_client.py`, `test_transports.py::test_backend_over_mcp_transport_stages_and_applies`, `::test_rest_transport_reads_and_writes`; smoke over both transports |
| H5 | Promotions ledger-only | fixed (ADR-13) | `merchant/api/staging.py::promotion_payload`, `merchant/api/shopware_backend.py::stage_promotion` + apply path | `test_staging.py::test_applying_a_promotion_creates_promotion_discount_and_rule`, `smoke_live.py --write` §2 (promotion + discount + rule written, then deleted) |
| H6 | Price write overwrote all currencies / gross-net guess | fixed | `merchant/api/staging.py` (`price_payload`, `net_of`, `current_price_entries`, `tax_rate_for`) | `test_staging.py::test_price_write_replaces_only_the_eur_entry_with_net_from_tax` |
| H7 | `host.sessions.start` monkeypatch; direct add bypassed provenance | fixed | `storefront/api/main.py::cart_add` (details through the executor first) | `test_host_app.py::test_the_add_button_reads_details_first_so_a_variant_enters_provenance`, `::test_the_add_button_on_a_family_is_held_with_the_route_to_a_variant` |
| H8 | Admin password grant in the merchant host | fixed (ADR-14) | `merchant/api/agent_config.py`, `merchant/api/admin_client.py::OAuthTokenProvider`, `docker/merchant_identity.py` | `load_settings` refuses without integration keys (`test_transports.py`), `verify.sh` §4 (`user:read` refused, allowlist == `tools/list`), `--read-only` smoke shows `transport: mcp` under the integration |
| M1 | Restock wrote an absolute level | fixed | `merchant/api/shopware_backend.py` (delta at apply time) | `test_staging.py::test_restock_applies_the_delta_to_the_current_stock`, `smoke_live.py --write` §3 |
| M2 | Pause/activate on a family left children | fixed | same | `test_staging.py::test_pausing_a_family_covers_parent_and_variants_in_one_write` |
| M3 | Performance insights stubbed | fixed | `merchant/api/insights.py`, `shopware_backend.py` (`shopware-entity-aggregate`) | `test_reads.py::test_snapshot_uses_aggregations_and_compares_periods`, `::test_metrics_series_period_granularity_segment`, `::test_inventory_alerts_thresholds_and_slow_movers`, `::test_order_issues_kinds_and_seed_marker`, `::test_pricing_context_cost_margin_and_floor` |
| M4 | `GET /api/cart` created UCP checkout sessions | fixed | `storefront/api/shopware_backend.py::checkout_url_for` (ticket URL on the host, no shop call) | `test_shopware_backend.py::test_checkout_handoff_points_at_the_ticket_and_never_completes`, `test_host_app.py::test_a_fresh_session_has_an_empty_cart_and_no_checkout_url` |
| M5 | Fulfillment without fees / ETA | fixed | `storefront/api/shopware_backend.py::get_fulfillment_options`, `docker/seed_catalog.py` (delivery times, shipping prices) | `test_shopware_backend.py::test_fulfillment_options_carry_fee_and_eta_from_shipping_methods`, smoke `fulfillment: Standard 4.9 / Express 9.9` |
| M6 | Policies from a static file | fixed | `storefront/api/policies.py` (CMS walk over footer/service navigation), `docker/seed_catalog.py` | `test_shopware_backend.py::test_policies_come_from_the_shops_cms_pages`, smoke `policies: live=True` |
| M7 | Ledger in memory | fixed | `merchant/api/ledger.py` (`SqliteChangeLedger`, `MERCHANT_LEDGER_DSN`) | `test_ledger.py::test_changes_and_payloads_survive_a_restart` |
| M8 | `vendor/` drifted, NOTICE untrue | fixed | `vendor/demo_common`, `vendor/web-shared`, `vendor/skills`, `NOTICE` | `diff -rq` against the upstream clone at `fd4d5922` is empty for all three; NOTICE lists the adapted `merchant/web` files (they keep their Anthropic Apache-2.0 header) |
| M9 | Store API errors swallowed | fixed | `storefront/api/store_api.py` (`StoreApiError`, logged) | host log shows `Store API POST … → 404` lines; `test_shopware_backend.py::test_a_dropped_cart_reads_as_empty_and_unbinds` |
| M10 | Bootstrap with raw SQL | fixed | `docker/bootstrap.sh`, `docker/enable_ucp.py` (`ucp:config:set`), `docker/verify_state.py` | `verify.sh` §6 (every seeded entity counted once after two runs) |
| Order history | No prior period for metrics | fixed | `docker/seed_orders.py` (40 orders over two months) | `verify.sh` "seeded orders total: 40", dashboard `against: the prior week` (`test_portal.py::test_dashboard_payload_shape`) |
| Low | `_money` heuristic | partial | `storefront/api/shopware_backend.py::_money` | dict `amount` integers are read as minor units per the UCP schema; bare numbers still use the ≥ 100 heuristic because Shopware's UCP adapter emits both shapes |
| Low | dead code (`_ = session`, duplicated totals, identity scaffold, `shop_signin`) | fixed | `storefront/api/*.py`, `storefront/api/identity.py` is the working implementation, `storefront/web/components/StoreShell.tsx` | `rg "_ = session|shop_signin" storefront` finds nothing; `ruff check` clean |
| Low | `admin_client.calls` unbounded | fixed | `merchant/api/admin_client.py` (`deque(maxlen=CALL_LOG_LIMIT)`) | `test_transports.py` |
| Low | catalog paging beyond 100 | open | `storefront/api/catalog_warmup.py`, vendor `GET /api/products` (`limit ≤ 100`) | the grid is a display cache filled by warm-up (24) and searches; a catalog of > 100 products would need a paged grid route, not present in the vendored host — low value for the demo shop (7 products) |
| Low | `disclosures.py`: `availableStock or stock` falsy bug, duplicated delivery text, shipping-costs page link | fixed (this pass) | `storefront/api/disclosures.py`, `storefront/data/disclosure_copy.de.json` | `test_shopware_backend.py::test_disclosure_rows_read_zero_stock_and_do_not_repeat_the_delivery_range` |
| Low | header cart count stale after attach | fixed | `storefront/web/components/StoreShell.tsx` (`landCart` after `attachCart`) | `test_host_app.py::test_attach_binds_the_session_to_a_shopware_cart` (host side), browser pass |
| P1/2 | RFC 9421 + 9530 signer, `signature-policy=strict` | fixed | `shopware_common/http_signing.py`, `storefront/api/ucp_client.py`, `docker/agent_key.py` | `test_http_signing.py`, `test_ucp_client.py::test_mcp_signature_is_verified_by_the_shop_and_unsigned_is_refused`, `verify.sh` §3 (signed 200 / unsigned 401) |
| P1/2 | Identity Linking | partial | `storefront/api/identity.py`, routes in `main.py` | `test_host_app.py::test_identity_linking_runs_the_signed_pkce_flow_and_adopts_the_customer_cart` (netless AS); live: `GET /api/auth/shopware/start` → 503 with the reason (Shopware requires an https `client_id`; the Docker shop is http) |
| P1/2 | Real promotions | fixed | see H5 | see H5 |
| P1/2 | Analytics with prior period | fixed | see M3 | see M3 |
| P1/2 | Order history seed | fixed | see "Order history" | see above |
| P1/2 | SQLite ledger | fixed | see M7 | see M7 |
| P1/2 | `vendor/` re-vendored + truthful NOTICE | fixed | see M8 | see M8 |
| P1/2 | MIT headers on repo-owned code | fixed | `storefront/api/**`, `merchant/api/**`, `shopware_common/**`, `docker/**`, `requirements.txt` | `rg "SPDX-License-Identifier: Apache-2.0"` outside `vendor/` hits only the adapted Anthropic files in `merchant/web` and the Shopify-headed `storefront/web` files named in NOTICE |
| P1/2 | Bootstrap idempotent + pinned plugin commits | fixed (ADR-11) | `docker/bootstrap.sh` (`PLUGIN_REF=df0ae723…`, `MERCHANT_TOOLS_REF=01e2082e…`) | two consecutive runs, `signing_keys | length == 1`, `verify.sh` §6 |
| P1/2 | Integration + ACL role + MCP allowlist | fixed (ADR-14) | `docker/merchant_identity.py` | `verify.sh` §4 and §6 (`integration 1`, `acl role 1`, effective tools == allowlist) |
| Docker | `docker/verify.sh` exists and passes | fixed | `docker/verify.sh`, `docker/verify_state.py`, `docker/ucp_signed_check.py`, `docker/handoff_check.py` | `./docker/verify.sh` → `all checks passed` |
| Punch a | ids in prose; "nearest compliant thing" | fixed (host prompt rules) | `merchant/api/agent_config.py::MERCHANT_BRAND_VOICE`, `storefront/api/agent_config.py::SHOPPING_BRAND_VOICE` | `python -m evals.runner --suite merchant --set ci --mode replay --trials 2`: 23/38 cases (0.67) → 34/38 (0.93); remaining: `merch-price-008` (blueprint float cap bug), single-trial misses on `approval-001/004`, `price-001` |
| Punch b | naive `datetime.now()` in `ClockContext` | partial | `shopware_common/clock.py`, `merchant/api/portal.py::context`, `storefront/api/main.py::shopping_context`, `X-Timezone` from both web apps (`lib/api.ts`), `HOST_TIMEZONE` | `test_clock.py`, `test_portal.py::test_dashboard_context_carries_the_operators_clock`, `test_host_app.py::test_the_add_button_runs_under_the_customers_clock`; the vendored `demo_common` chat routes still pass the server's naive clock (no hook; upstream note 5) |
| Punch c | hosts restarted on :8004/:8005 with `.env`, chat turn on :3005, :3006 up | done | — | health on both, `UCP transport mcp … signing on, handoff on`; one storefront turn answered "Sizes S and M are in stock … L is currently out of stock" with chips; portal :3006 → 200 |

## Phase 0 — Docker Shopware + UCP

- [x] Repo layout (`storefront/`, `merchant/`, `shopware_common/`, `vendor/`, `docker/`)
- [x] `docker/compose.yaml` + `docker/bootstrap.sh` (dockware/shopware 6.7.13.0, `MCP_SERVER=1`)
- [x] Shopware container healthy on `:8080`
- [x] Install + activate `SwagAgenticCommerce` and `SwagMcpMerchantTools` — **pinned to commits** (ADR-11, see `docs/version-matrix.md`)
- [x] Enable UCP on Storefront (catalog, cart, discount, checkout, order, identity_linking; rest + mcp + embedded) via `ucp:config:set`
- [x] Shop signing key: exactly one active key after repeated bootstraps
- [x] Agent signing key `secrets/ucp-agent-signing-key.pem`, JWK published in `agent-profile.json`, `signature-policy=strict`
- [x] Seed catalog (CA-TSHIRT S/M/L with L OOS, CA-OIL Grundpreis), delivery times, shipping prices, CMS policy pages, order history
- [x] Merchant identity: integration + ACL role `claude-merchant-agent` + Admin MCP allowlist (ADR-14)
- [x] `docker/verify.sh`; bootstrap re-runnable without side effects (M10)
- [x] `curl /.well-known/ucp` returns a profile (2026-08-25)
- [x] Live facts documented in `docs/shopware-mapping.md`

## Phase 1 — Storefront shopping agent

- [x] Blueprint packages pinned in `requirements.txt`
- [x] `vendor/` with NOTICE (vendored Anthropic/Shopify material stays Apache-2.0; this repo's own code is MIT, `Copyright 2026 shopware AG`)
- [x] `storefront/api` FastAPI host + Shopware `StorefrontBackend`
- [x] UCP client: **MCP primary, REST fallback** (ADR-12 rev.), real Streamable-HTTP client in `shopware_common/mcp_client.py`
- [x] RFC 9421 + RFC 9530 request signing (`shopware_common/http_signing.py`), verified by the netless replay and the strict shop
- [x] Variant handling: family vs child, real children incl. out of stock, `Unavailable` with siblings (H1/H2)
- [x] Orders from the Store API behind the cart token / linked customer (H3)
- [x] Store API gaps: policies from CMS pages (M6), disclosures, fulfillment with fees + ETA (M5), variants by `parentId`
- [x] Checkout handoff via one-time HMAC-signed code, POST auto-submit, never the raw token (ADR-10, K2); no `complete_checkout` path; no writes on `GET /api/cart` (M4)
- [x] Direct add establishes provenance through the executor (H7)
- [x] Typed `StoreApiError` with logging (M9)
- [x] Disclosures: `availableStock: 0` reads as sold out, delivery range not repeated, shipping hint names the "Versand & Lieferzeit" page (Low)
- [x] Session clock: `shopware_common/clock.py` (`X-Timezone` → `HOST_TIMEZONE` → `Europe/Berlin`, aware `now`) on this repo's routes; the web app sends the browser zone
- [x] Host prompt rule in `brand_voice`: a date the customer names is compared with local time (eval finding 5)
- [x] Identity Linking (OAuth code + PKCE) implemented; 503 with reason on the http-only Docker shop
- [x] Next.js storefront on `web-shared` (grid, cart, assistant rail, checkout button, brand from the shop)
- [x] pytest with netless replay of UCP (MCP + REST), Store API, OAuth AS
- [x] `storefront/scripts/smoke.py` against the live shop over both transports (search → details/variants → cart → fulfillment → policies → orders → handoff)
- [x] Browser: grid → add to cart → Checkout in Shopware (`docs/screenshots/storefront.png`)

## Phase 2 — Merchant agent

- [x] `AdminTransport` protocol: `McpTransport` (default) + `RestTransport` (fallback) (ADR-12 rev., H4/H9)
- [x] Integration `client_credentials` only; admin password removed from the hosts (ADR-14, H8)
- [x] `apply_change` / `discard_change` refuse non-STAGED changes (K1)
- [x] Stage = `shopware-entity-upsert dryRun=true` → `ChangeItem {before, after}` + `guardrail_notes`; apply replays with `dryRun=false`
- [x] Real promotions: `promotion` + `promotion_discount` + rule + sales channel; partial failures reported (ADR-13, H5)
- [x] Price write replaces only the sales-channel currency, net from tax rate (H6)
- [x] Restock as delta on fresh stock (M1); pause/activate expands to children (M2)
- [x] Performance insights from aggregations: snapshot, metrics, inventory alerts, order issues, pricing context (M3)
- [x] SQLite ledger via `MERCHANT_LEDGER_DSN`, survives restarts (M7)
- [x] Portal routes `/api/merchant/dashboard`, `/orders`, `/changes`; listing staged changes admits them to the session's provenance so the portal's Approve / Dismiss works across sessions and restarts
- [x] Merchant portal `merchant/web` on `:3006` mirroring the reference portal (KPI row, Needs you today, assistant rail with staged-change cards, pending count on the sidebar's Assistant entry)
- [x] MCP client hardening from the live pass: one in-flight request per session (Shopware answers one of two concurrent calls with an empty body); offloaded results (`_meta.resourceUri` → `resources/read`) collected instead of read as empty — this is what fills the portal's recent-orders feed
- [x] Host prompt rules in `MERCHANT_BRAND_VOICE`: no ids in prose; caps / missing dates / ambiguous targets → stage nothing and ask (merchant CI evals 23/38 → 34/38 cases)
- [x] Portal routes build the session context with the operator's clock (`X-Timezone`, `HOST_TIMEZONE`)
- [x] pytest (transports, staging, ledger, portal) + `merchant/scripts/smoke_live.py` (read-only and `--write` round trips over MCP and REST)
- [x] Browser: dashboard → briefing → stage price → approve → verified in Shopware → reverted (`docs/screenshots/merchant-portal.png`)

## Housekeeping

- [x] `vendor/demo_common`, `vendor/web-shared`, skills re-vendored byte-identical from `fd4d5922`; NOTICE lists the adapted `merchant/web` files (M8)
- [x] `.env.example` cleaned (no admin password, no `SHOPWARE_AGENT_COMPLETE_CHECKOUT`, MCP defaults)
- [x] MASTERPLAN §4.2 records ADR-10 … ADR-14 (ADR-11 notes that 6.7.14 is unreleased and the 6.7.13 lane is current); `docs/shopware-mapping.md` carries the live tool names and schemas
- [x] `shopware_common/README.md`; `HOST_TIMEZONE` in `.env.example` (the hosts do not run in compose, so no passthrough is needed)
- [x] `docs/anthropic-upstream-notes.md`: float cap boundary, `commerce-builder` frontmatter, ids-in-prose rule, missing follow-through rule, no clock hook in `demo_common`
- [x] `pytest -q`, `ruff check` (own trees), `ruff format --check`, `npm run build` green

## Open, with reasons

- Identity Linking live: Shopware refuses an http `client_id`; needs the agent profile on https (code path complete and tested netless).
- Shared `/api/chat` clock: `demo_common` (vendored, unmodified by policy) passes naive `datetime.now()`; needs an upstream hook (upstream note 5).
- `merch-price-008`: staging exactly at the cap is refused by the blueprint's float comparison (upstream note 1); no host workaround short of a fractional cap.
- Catalog grid beyond 100 products: the vendored `GET /api/products` caps at 100; the demo shop has 7.
- `_money` bare-number heuristic: Shopware's UCP adapter emits both minor-unit integers and major floats; the dict form follows the schema.
- `ruff check .` reports two findings in `browser-demo/host/bootstrap.py` (another agent's tree, not touched).

## Browser demo (in progress)

- [x] Feasibility measured — [`docs/browser-demo-feasibility.md`](docs/browser-demo-feasibility.md) (2026-09-03)
- [x] Build pipeline in `browser-demo/`: playground fetch, Shopware 6.7.13.1 + pinned plugins, Node WASM seed, MEMFS bundle, Pyodide wheels, site assembly
- [x] Local server (`server/index.mjs`): COOP/COEP, static `dist/site`, Anthropic proxy + BYOK (contributor fallback)
- [x] Demo shell: React boot UI, Shopware iframe, shopping/merchant panels, `DemoOverlay` plugin
- [x] GitHub Pages: `pages.yml` builds the gitignored WASM tree and deploys to https://agentic-commerce-lab.github.io/shopware-claude-commerce/ (COI via service worker; project path prefix; no Cloudflare)
- [x] Pages storefront boot: UCP `embeddedAllowedOrigins` stay pathless; sales-channel domains keep the repo prefix; SW → page PHP bridge uses `{ transfer }` (`build/ucp-origin.mjs`)
- [x] Pages storefront after boot: `APP_URL` keeps the repo path (otherwise Shopware maps no sales channel → Oops 400 and `all.css` 404); leftover PHP sessions are closed between WASM requests; iframe Oops is not treated as storefront-ready
- [x] Pages theme/media + lazy chunks: CI publishes compiled `theme/<hash>/all.css` + seed media from `ci-fixtures/…/public-assets.tar.gz`; Vite emits absolute `/<repo>/demo/assets/…` URLs for MerchantView / shopping chunks; `.env.local` `MCP_SERVER=1` is written when prepare-shop is skipped
- [x] Shopping chat catalog blindness — empty UCP/Store API search while the grid showed products; Pages PHP URLs dropped `/shopware-claude-commerce`; iframe PDP never entered session provenance. Fixed: listing/cache search fallback, `POST /api/session/sync-catalog` + `/focus`, `phpRequestUrl`, live category notes. Overlay `data-product-*` needs a WASM rebuild to ship on Pages; buy-widget DOM extract works without it.
- [ ] End-to-end acceptance on a fresh Pages build (cold boot, chat turn, cart, handoff) documented and gated in CI
- [ ] Hosted Anthropic proxy — not on Pages (static). Chat is BYOK against `api.anthropic.com` and may fail if Anthropic blocks the browser call

## Phase 3 — not started

- [ ] Claude Code plugin, evals, Managed Agents manifests
- [ ] CI workflow
- [ ] Identity Linking end to end (needs an https agent profile; the code path exists)
- [ ] Per-line-item promotion scoping
