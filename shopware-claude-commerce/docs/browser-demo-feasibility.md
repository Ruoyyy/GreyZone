# Feasibility: a zero-install, in-browser demo on FriendsOfShopware/shopware-playground

Date: 2026-09-03. Scope: can the Shopware × Claude Commerce Agents demo (shopper + merchant agent over UCP / Store API / Admin API MCP) run entirely in a visitor's browser on top of [shopware-playground](https://github.com/FriendsOfShopware/shopware-playground) (PHP WASM + MariaDB WASM)? Everything below was measured on a clone in `/tmp` (commit `c86f241`, 2026-09-03); nothing in this repository was changed except this file.

## Verdict in one paragraph

**Feasible now for the Shopware side, with four small playground-level patches; the agent host needs a rewrite of the thin FastAPI layer (TypeScript) or a Pyodide port, plus a tiny hosted proxy for the Anthropic key.** The playground already ships **Shopware 6.7.13.1** as its default tree (6.6.10.23 is the secondary toggle), so no rebuild for 6.7 is needed. In PHP WASM I verified: storefront rendering, Store API, Admin API OAuth + REST, **Admin MCP `/api/_mcp` incl. `dryRun` upserts**, **`SwagAgenticCommerce` install, `/.well-known/ucp`, UCP REST and `/ucp/mcp` (14 tools)**, ES256 signing-key generation, and `bin/console` commands. The blockers found are all fixable in the playground tree (PHP Fibers in `mcp/sdk`, the UCP SDK's private `pdo_mysql` connection, `openssl.cnf`, `composer require` on plugin install). Cold boot to a rendered storefront is ~8 s on an M3 Ultra over localhost with ~155 MB of downloads; expect 20–40 s on a typical laptop and CDN. The unsolved parts are product decisions, not technical ones: the Anthropic API key cannot live in the browser, so one serverless proxy stays.

---

## 1. What the playground is (static analysis)

| Aspect | Finding |
|---|---|
| PHP runtime | `@php-wasm/web` 3.1.52 (WordPress Playground's php-wasm), **PHP 8.4.23 asyncify build** + `intl` extension. 19.0 MB wasm + 5.3 MB `intl.so` + **29.4 MB `icu.dat`**. Extensions present: openssl 1.1.1t, curl, zip, mbstring, gd, imagick, intl, pdo_mysql/pdo_sqlite, sodium **absent**, Fibers **not functional**. |
| Database | [`lite4mariadb`](https://github.com/shyim/lite4mariadb) (shyim) — MariaDB server with InnoDB compiled to WASM, 16.9 MB, **GPL-2.0-only**. pthreads → needs `crossOriginIsolated` (COOP/COEP headers, SharedArrayBuffer). Persistence: IndexedDB (`idb://shopware-playground-<version>`), debounced flush. |
| SQL bridge | Shopware's DBAL connection is replaced by a custom driver (`App\Playground\MariadbLiteDriver`) that ships SQL to JS via `post_message_to_js`; native `pdo_mysql` is compiled in but has no server to reach. |
| Request routing | Service Worker intercepts same-origin PHP routes → `postMessage` to the page → Dedicated Worker running PHP + MariaDB on one thread (asyncify, strictly serialized: one PHP request at a time). Static assets (`/bundles`, `/theme`, `/media`) served per version from `/versions/<v>/assets/`. |
| Hosting | Local: Vite dev server or `src/serve.mjs`; public `playground.fos.gg`: Cloudflare Worker (`worker/index.mjs`) + Workers Static Assets + **R2 for the big binaries** (Cloudflare Pages has a 25 MB/file limit). `pack-deploy.mjs` produces a plain static tree with `_headers` (COOP/COEP) for any static host. **No Cloudflare account needed to run it.** |
| Versions | Default tree `shopware/` pins **`shopware/core v6.7.13.1`** (PHP ^8.2–8.5, `symfony/mcp-bundle 0.11`, `mcp/sdk 0.7.1`, `swag/demo-data 2.1.0`). `versions-src/6.6.10.23/` is a second tree for the toggle. Adding a version = `composer create-project` into `versions-src/<v>/shopware`, copy `overrides/` + `config/`, run `SHOPWARE_DIR=… npm run prepare-install && npm run build`. |
| Seeding | `prepare-install.mjs` runs the real Shopware installer + 1,692 migrations + `theme:change` + `SwagPlatformDemoData` **inside Node PHP WASM**, then dumps SQL (194 KB gzip). Browsers import the dump once into IndexedDB. Shop URLs are rewritten to `location.origin` at boot. |
| Outbound HTTP from PHP | Node runtime: `file_get_contents('http://…')` works, curl fails (no CA bundle). Browser: `@php-wasm/web` has `tcpOverFetch` (TCP-over-fetch with a JS TLS 1.2 stack + generated CA), **not enabled by the playground**; its SW stubs `/api/_action/update/check` and `/api/_action/store/*` because they hang otherwise. |
| Known playground patches | `overrides/*.php` replace installer/DB factories; `patch-installer.php` injects the SQL bridge into `public/index.php`, neuters Composer's `proc_open` (git probes), `plugin:refresh` cannot `composer require`; `enable_admin_worker: false` (single PHP instance). |

## 2. What I ran and what happened

Environment: macOS, Apple M3 Ultra, Node 25.6, PHP 8.5.6 (host, for `composer install`), Composer 2.9.8. All commands executed in `/tmp/shopware-playground`.

### 2.1 Build pipeline

| Step | Command | Result |
|---|---|---|
| Clone + deps | `git clone --depth 1 … && npm ci` | 223 packages, 4 s |
| Vendor tree | `cd shopware && composer install --no-scripts && php overrides/patch-installer.php` | 165 packages, 10 s (cached). `composer.lock` already contains `symfony/mcp-bundle` + `mcp/sdk` |
| Install in WASM | `node src/prepare-install.mjs` | **43.7 s** total: DB config 1 s, 1,692 migrations ~31 s, shop config, `theme:refresh`/`theme:change`, `plugin:install --activate SwagPlatformDemoData` (16 products, 12 media), SQL dump 2.1 MB → 194 KB gzip |
| Bundle | `npm run build` | 7 s → `public/versions/6.7.13.1/shopware.zip` 76 MB (85 MB with SwagAgenticCommerce; 239 MB / 37,903 files unzipped into MEMFS), assets 57 MB (bundles 42, theme 15, media 0.4) |
| Dev server | `npm run dev` (Vite, 127.0.0.1:4177, COOP/COEP set) | ready in 0.4 s |

### 2.2 Headless probe (Node runtime, `dataDir: memory://`, same PHP/DB code path as the browser worker)

| Request | Status | Time |
|---|---|---|
| boot (MariaDB WASM + PHP WASM + dump import) | – | 0.9 s |
| `GET /` storefront, cold kernel (container compile) | 200 "Catalogue #1" | 4.5 s |
| `GET /` warm | 200 | 0.36 s |
| `GET /Clothing/` category listing | 200 | 0.44–0.56 s |
| `GET /store-api/context` (`sw-access-key`) | 200 | 0.25 s |
| `POST /store-api/product` limit=3 | 200, total 3, `calculatedPrice` 19.99 | 0.25 s |
| `POST /store-api/product-listing/{navCategoryId}` | 200, total 5 | 0.26 s |
| `POST /store-api/checkout/cart/line-item` ×2 | 200, total 1600 (cart works; "Cash on delivery blocked" cart error as in a fresh install) | 0.32 s |
| `POST /store-api/shipping-method?onlyAvailable=1` | 200, total 1 | 0.21 s |
| `POST /store-api/search?search=Shirt` | 200, total 0 (demo data has no "shirt"; keyword index has 165 rows) | 0.24 s |
| `POST /api/oauth/token` password grant `admin`/`Shopware123!` | 200, `expires_in` 600 | 0.73 s |
| `GET /api/_info/version` | 200 `{"version":"6.7.13.1"}` | 0.17 s |
| `POST /api/search/product`, `POST /api/search/order` with `sum` aggregation | 200 | 0.16–0.18 s |
| `bin/console about`, `system:config:get`, `dal:refresh:index`, `cache:clear` (via the kernel, as `prepare-install` does) | ok | 0.08–0.65 s |
| `GET /agents.md` (core discovery) | 404 without plugin; **500 (512 MB memory exhausted in Twig)** with the plugin's fallback renderer — needs a look, not WASM-specific per se | 1.1 s |

First run only: `POST /store-api/product` and `/Clothing/` returned **500 "Cannot assign false to property …$cheapestPrice"** — lite4mariadb 0.1.1 truncated `LONGTEXT` values at the first NUL byte (serialized `CheapestPriceContainer`). **`lite4mariadb@0.1.2` (published 2026-09-03) fixes it**; after `npm i lite4mariadb@0.1.2` and a re-dump everything above passed. The lockfile still pins 0.1.1.

### 2.3 Browser (Playwright Chromium 1228, headless, localhost)

| Measurement | Result |
|---|---|
| Cold boot: shell DOM → storefront rendered in the iframe | **8.0 s** (status timeline: 0.4 s "Starting MariaDB WASM + PHP WASM", 4.2 s "Opening Shopware", 4.4 s "GET /index.php", render at 8.0 s) |
| Warm reload (IndexedDB already seeded, zip from HTTP cache) | **6.1 s** |
| `crossOriginIsolated` / `SharedArrayBuffer` | true / true |
| `GET /store-api/context` from the page (through SW → worker) | 200, 0.20 s |
| `POST /store-api/product` limit=3 | 200, "Variant product" 19.99, 0.19 s |
| `POST /store-api/checkout/cart/line-item` | 200, 1 item, 19.99, 0.27 s |
| `POST /store-api/shipping-method?onlyAvailable=1` | 200, 0.22 s |
| `POST /api/oauth/token` | 200, 0.89 s |
| `GET /api/_info/version` | 200 `6.7.13.1`, 0.15 s |
| `POST /api/search/product` | 200, 0.15 s |
| `GET /Clothing/` HTML | 200, 128 KB, 0.49 s |
| Page errors | none |

Note: the storefront sales-channel access key is not embedded in the HTML; a demo must read it from the seed (`sales_channel.access_key`) or the shell's SQL console.

Screenshot after cold boot (Demostore home, "Catalogue #1", version toolbar with Admin/Database/Files/Logs/Export/Reset): `/tmp/pg-probe/playground-boot.png` (scratch, not committed).

### 2.4 MCP servers in PHP WASM

| Test | Unpatched | With patch |
|---|---|---|
| `POST /api/_mcp initialize` (`MCP_SERVER=1` in `.env.local`) | **PHP WASM abort**: `zend_fiber_init_context → missing function` — `mcp/sdk` `Server/Protocol.php` wraps every handler in `new \Fiber(...)`; php-wasm has no Fiber context switching | 200, `protocolVersion 2025-11-25`, session id |
| `tools/list` | – | **11 tools**: `shopware-entity-aggregate/-delete/-read/-schema/-search/-upsert`, `shopware-media-upload`, `shopware-order-state`, `shopware-system-config-read/-write`, `shopware-theme-config` |
| `tools/call shopware-entity-aggregate` (sum stock) | – | 200 `{"stock_sum":710}` |
| `tools/call shopware-entity-upsert dryRun=true` | – | 200, returns the would-be writes (product + product_translation) |
| `POST /store-api/_mcp` initialize + `tools/list` | abort | 200, 1 tool (`shopware-store-api-context`) as on 6.7.13 |

Patch (3 lines, `vendor/mcp/sdk/src/Server/Protocol.php`): replace `new \Fiber(fn () => $handler->handle(...))->start()` + `getReturn()` with a direct `$handler->handle($request, $session)`. Only server-initiated elicitation/sampling round-trips (which Shopware's tools do not use) lose their suspension point. Without `MCP_SERVER=1` both endpoints are 404, exactly as in Docker.

### 2.5 `SwagAgenticCommerce` (pinned `20bd3df3`, 1.3.0) + `ucp-php-sdk/symfony-bundle` 0.0.5

| Step | Result |
|---|---|
| `git clone` into `custom/plugins/`, `composer require "ucp-php-sdk/symfony-bundle:>=0.0.5 <0.1.0"` on the host | ok (7 s) |
| `plugin:refresh` in WASM | ok, plugin listed |
| `plugin:install --activate SwagAgenticCommerce` in WASM | **fails**: "Could not execute composer require" (Shopware shells out) → fixed by `composer require shopware/agentic-commerce:1.3.0` **at build time** (path repo, already configured). `executeComposerRequireWhenNeeded` then skips. Alternative: `shopware.deployment.cluster_setup: true`. After that: **installed + activated in 1.7–3.9 s**, plugin migrations ran |
| `ucp:channels`, `ucp:config:set/show`, `ucp:signing-keys:generate/list` via console | ok once the console harness boots `.env` (`Dotenv::bootEnv`), otherwise "Environment variable not found: DATABASE_URL" — harness issue, not WASM |
| `ucp:signing-keys:generate` (ES256, `openssl_pkey_new`) | **fails by default**: `fopen: No such file or directory` — OpenSSL 1.1.1 in WASM has no `openssl.cnf`. Fixed by `emscriptenOptions.ENV.OPENSSL_CONF=/internal/openssl.cnf` (2-line cnf written to MEMFS) → EC P-256 and RSA-2048 keygen, `openssl_sign`/`openssl_verify` (72-byte ES256 signatures) all work. `putenv()` at script time is too late |
| `PUT /api/_admin/ucp/sales-channels/{id}/config` (same call as `docker/enable_ucp.py`) | 200, capabilities catalog/cart/discount/checkout/order/identity_linking, transports rest/mcp/embedded |
| `GET /.well-known/ucp` | 200, `version 2026-08-25`, 6 capabilities, `signing_keys` 1, 3.4 s (cold, includes container rebuild after `cache:clear`) |
| `GET /.well-known/ai-catalog.json` | 200 |
| `GET /.well-known/oauth-authorization-server` | 200 (identity linking AS) |
| UCP requests with `UCP-Agent: platform; profile="…"` | **424 `agent_profile_unreachable`** — the SDK fetches the agent profile with Symfony HttpClient/curl; no network from WASM |
| Root cause discovered on the way | The UCP SDK opens its **own DBAL connection** from `DATABASE_URL` with native `pdo_mysql` (`ucp_sdk.connection` → `ConnectionFactory::create`), bypassing the playground's bridged driver. In Node it silently connected to this repo's Docker MariaDB on `localhost:3306` (one stray `ucp_signing_keys` row, removed again). In a browser this connection simply fails. Fix: 4-line patch in `ConnectionFactory::create` → `driverClass => App\Playground\MariadbLiteDriver` |
| After the connection patch + pre-seeding `ucp_platform_profile_cache` (`expires_at = NULL` = never expires) | `POST /ucp/v1/catalog/search` **200**; second call with a reused `Idempotency-Key` and a different body → 409 (idempotency store works); `POST /ucp/mcp initialize` **200**, `tools/list` **14 tools** (`search_catalog/-lookup`, `-cart-create/-get/-update/-cancel`, `-checkout-*`, `-discount-apply`, `-order-get`, `shopware-store-api-context`), `tools/call search_catalog` **200** with product documents |

So the whole UCP stack our `storefront/api` talks to runs in WASM; the agent profile must come from the DB cache (or same-origin `tcpOverFetch`, untested) instead of an HTTP fetch.

## 3. Mapping against our needs

### 3.1 Shopware side

| Need (MASTERPLAN §3/§4, `docs/shopware-mapping.md`) | On the playground's 6.7.13.1 tree | On the 6.6.10.23 tree |
|---|---|---|
| UCP `SwagAgenticCommerce` REST `/ucp/v1/*`, `/.well-known/ucp` | **Works** (verified) with 3 patches: composer-managed install, bridged SDK connection, `OPENSSL_CONF` | Plugin supports 6.6; same patches; JWT keys already stubbed by the playground |
| UCP over MCP `/ucp/mcp` (our default transport) | **Works** (verified, 14 tools) with the Fiber patch + `MCP_SERVER=1` | Not available (needs 6.7.11+ Store-API-MCP infrastructure) → REST fallback only |
| Store API for gaps (context, product, listing, cart, shipping, navigation) | **Works** (verified) | Works (playground's own test target) |
| Admin API OAuth + REST (`/api/search/*`, aggregations, `PATCH`) | **Works** (verified) | Works |
| Admin MCP `/api/_mcp` incl. `dryRun` previews (`shopware-entity-upsert`) | **Works** (verified) with the Fiber patch | Not available |
| `MCP_SERVER` flag / progressive discovery (6.7.14) | Flag needed on 6.7.13 (`.env.local`); **no 6.7.14.x release exists** (see `docs/version-matrix.md`), so "rebuild on 6.7.14" is moot today; when it ships: `composer create-project` into `versions-src/6.7.14.x`, copy the 6.7 overrides/config, `prepare-install` — ~0.5 day | – |
| Plugin preinstalled in the WASM bundle incl. composer deps (`ucp-php-sdk`) | Yes: `composer require` on the host puts it in `vendor/` (symlink to `custom/plugins`, zipped as files); `plugin:install` can run at `prepare-install` time so the dump already has it active | Same |
| RFC 9421 verification (shop side) / ES256 key generation | Verification uses `openssl_verify` — works. Key generation needs `OPENSSL_CONF` (verified) | Same |
| Identity linking (OAuth AS per sales channel) | Metadata endpoint works; PKCE flow untested; requires `https` `client_id` → works only when the demo is served over https (GitHub/Cloudflare Pages are) | – |
| Checkout handoff (`CommerceAgentsHandoff` plugin, ADR-10) | Nothing WASM-specific; another composer-managed plugin in the bundle | Same |
| `/agents.md`, `/llms.txt` | Plugin fallback renderer OOMs (512 MB) in WASM — investigate; our host has fallback copy | – |
| Outbound HTTP from PHP (profile fetch, webhooks, store API) | Off. Options: (a) seed `ucp_platform_profile_cache` at build time (verified), (b) enable php-wasm `tcpOverFetch` for same-origin URLs (SW-served `agent-profile.json`) — plausible, untested, (c) `SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1` is still needed for a non-https profile URL | Same |
| Cron / message queue / `admin_worker` | None; indexers must run synchronously (`dal:refresh:index` took 0.6 s) | Same |

### 3.2 Agent host side

Our hosts are FastAPI apps built from the pinned Anthropic blueprint packages (`shopping-agent-runtime`, `merchant-agent-runtime`, `commerce-common`, `demo_common`), `anthropic==0.122.0` (httpx), `httpx`, `pydantic`, `mcp`, `sqlalchemy` (ledger), `cryptography`/`pyjwt` (signing, handoff).

| Option | What it is | Effort | What breaks / caveats |
|---|---|---|---|
| **(a) Pyodide** | Run the Python hosts unchanged in a Web Worker; serve `/api/*` by calling the FastAPI app in-process (`httpx.ASGITransport(app)`) from a small JS shim | 1–2 weeks | Pyodide 314.x (CPython 3.14) ships **pydantic 2.12.5 / pydantic_core 2.41.5, jiter, httpx 0.28.1 (fetch-backed async transport built in since 0.27.2), anyio, cryptography 47, sqlalchemy, rpds-py, jsonschema, fastapi, starlette**; `mcp`, `pyjwt`, `python-dotenv`, `httpx-sse`, `sse-starlette` are pure Python (micropip). `mcp` already excludes `uvicorn` on emscripten. Blueprint packages are pure Python. Pin drift: our `pydantic==2.13.4`/`pydantic-core==2.46.4` would have to follow Pyodide's build. `claude-agent-sdk` (subprocess-based) must stay out of the import path (it is only an `[examples]` extra). Anthropic streaming = SSE over fetch `ReadableStream`, works in httpx's Pyodide transport; prompt caching is server-side and unaffected; `InMemoryMemoryStore` lives in the worker, `JsonFileMemoryStore`/SQLite ledger persist only via IDBFS. Cold start: Pyodide core ~10 MB + packages ~15–25 MB, 3–6 s. Sync `httpx.Client` needs JSPI (Chrome 137+, Firefox ≥ 139 flag) or XHR. |
| **(b) TypeScript port of the thin backends** | Re-implement `ShopwareStorefrontBackend` / `ShopwareMerchantBackend` + the tool loop in TS; talk to Shopware with `fetch` (same origin, through the SW); call Anthropic from the browser | 2–3 weeks (shopper), +2 weeks (merchant staging/ledger) | Diverges from ADR-1 ("blueprint packages unchanged") — the harness rules (`docs/safety.md` gates, fencing, provenance) would have to be re-implemented and kept in sync; not the same code we ship. Streaming/tool-use via the Anthropic TS SDK is trivial. Memory store in IndexedDB. RFC 9421 signing with WebCrypto (ES256) is straightforward. |
| **(c) Tiny hosted proxy for the model call only** | Keep (a) or (b) in the browser; route `POST /v1/messages` through a Worker that holds the key | 1–2 days | Required in any case (see below). Streaming passes through (`ReadableStream`). Adds Turnstile + per-IP/per-visitor rate limits + model/max_tokens allow-list. |

**The API-key problem.** Anthropic allows browser calls with `anthropic-dangerous-direct-browser-access: true`, but the key is readable in DevTools, so it is only acceptable for bring-your-own-key. Identity-linked keys additionally need the workspace header, and neither can be rotated per visitor. Practical choices for a public demo:

1. **BYOK**: visitor pastes their own key (stored in `sessionStorage`), direct browser call — zero backend, but a high hurdle for prospects.
2. **Proxy with short-lived visitor tokens**: Cloudflare Worker verifies a Turnstile token, mints a signed 15-minute session token, enforces ~30 requests / 100k tokens per session and a daily budget, forwards to Anthropic with the real key (`stream: true` passthrough). ~150 lines. **Recommended default**, with a BYOK toggle.
3. Anthropic Workload Identity Federation / per-visitor keys via the Admin API are not designed for anonymous visitors.

The merchant demo also needs Shopware credentials — they are public anyway in a local WASM shop (admin `admin`/`Shopware123!`), so the "integration + ACL role" from ADR-14 becomes purely illustrative; the bootstrap should still create it in the seed so the demo shows least privilege.

### 3.3 Proposed "zero-install demo" architecture

```text
 Browser tab (single, crossOriginIsolated)                             Static host (Pages) + 1 Worker
 ┌──────────────────────────────────────────────────────────────┐      ┌────────────────────────────────┐
 │ Demo shell (Next.js export or plain TS)                      │      │ /            shell, app.js     │
 │  ├─ Storefront UI (web-shared)     ├─ Merchant portal UI      │ ───► │ /assets/*    php_8_4.wasm 19MB │
 │  │      │                              │                      │      │              icu.dat 29MB      │
 │  ▼      ▼                              ▼                      │      │ /mariadb/*   lite4mariadb 17MB │
 │ Agent host worker                                            │      │ /versions/*  shopware.zip 85MB │
 │  (a) Pyodide + FastAPI app via ASGITransport   or            │      │              shopware.sql.gz   │
 │  (b) TS backends + tool loop                                 │      │              assets/ (lazy)    │
 │  ├─ UcpClient (MCP/REST, RFC 9421 via WebCrypto)             │      │ /pyodide/*   (option a)        │
 │  ├─ StoreApiClient, AdminMcpClient (fetch, same origin) ─┐   │      │ /agent-profile.json            │
 │  └─ Anthropic Messages API ──────────────────────────────┼───┼─────►│ Worker: /anthropic proxy       │
 │                                                          │   │      │   Turnstile, rate limit, key   │
 │ Service Worker (playground) intercepts /ucp/* /store-api/*   │      └────────────────────────────────┘
 │  /api/* /index.php … ────────────────────────────────────┘   │                 │
 │       │ postMessage                                          │                 ▼
 │       ▼                                                      │         api.anthropic.com
 │ Dedicated Worker: PHP 8.4 WASM (asyncify) + MariaDB WASM     │
 │   Shopware 6.7.13.1 + SwagAgenticCommerce + ucp-php-sdk      │
 │   + CommerceAgentsHandoff, MCP_SERVER=1, IndexedDB persist   │
 └──────────────────────────────────────────────────────────────┘
```

| Component | Runs where | Notes |
|---|---|---|
| Shopware + plugins | Dedicated Worker (PHP WASM), DB in MariaDB WASM / IndexedDB | one PHP request at a time → agent tool calls and the storefront iframe compete for the same PHP instance; keep the tool loop sequential |
| Agent host | Web Worker (Pyodide) or main thread (TS) | talks to Shopware via same-origin `fetch` → SW → PHP; talks to Anthropic via the proxy |
| Anthropic proxy | Cloudflare Worker (or Vercel Edge) | the only server-side component; holds the key, streams |
| Static payloads | GitHub Pages / Cloudflare Pages + R2 / Netlify | must set `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`. GitHub Pages cannot set headers → needs the `coi-serviceworker` trick merged into the playground SW; 100 MB/file limit. Cloudflare Pages: 25 MB/file → big files in R2 (what fos.gg does) |

**Cold-start budget (measured / estimated):** downloads ≈ 155 MB (php 19 + intl 5 + icu 29 + mariadb 17 + zip 85 + dump 0.2), plus Pyodide ≈ 25–35 MB for option (a); boot 8 s on M3 Ultra/localhost, realistic **20–40 s** on a laptop over a CDN (zip is already compressed; wasm/icu brotli-compress ~30 %); warm reload 6 s + HTTP cache. Per request: Store API 0.2–0.3 s, Admin API 0.15–0.2 s, UCP MCP 0.3–0.4 s, storefront page 0.4–0.6 s; a shopper turn with 3–4 tool calls ≈ 1–2 s of PHP time plus model latency.

**Browser requirements:** WebAssembly threads + SharedArrayBuffer (crossOriginIsolated), `DecompressionStream`, Service Workers (no private-mode Safari), ~1–1.5 GB RAM headroom (PHP `memory_limit` 512 MB + MEMFS 239 MB + MariaDB buffers). Chrome/Edge/Firefox current; Safari 17+ likely but untested here. Mobile: not sensible.

**Limits:** no outbound HTTP from PHP (agent profile from DB cache; webhooks, Shopware Store, update checks stubbed); no cron/queue (`enable_admin_worker: false`, run indexers synchronously); one tab per origin (IndexedDB + single worker; a second tab corrupts state); single PHP instance (no parallel requests); Fibers unavailable (patched out of `mcp/sdk`); `sodium` absent (not needed: SDK uses openssl); demo shop identity linking needs https.

## 4. Verdict and plan

**Feasible now** for a Store-API/Admin-API demo; **feasible in ~3–5 weeks** for the full UCP + Admin-MCP demo that mirrors this repository's architecture; **not sensible** to try to run the Python hosts unchanged without a proxy, or to target mobile.

| Phase | Content | Effort | Depends on |
|---|---|---|---|
| **A — playground fork with our Shopware** | Fork `shopware-playground`; keep 6.7.13.1; add build-time `composer require shopware/agentic-commerce` + `CommerceAgentsHandoff`; overrides for the 4 patches (Fiber-free `mcp/sdk` Protocol, SDK `ConnectionFactory` → bridged driver, `OPENSSL_CONF` in runtime, console harness `bootEnv`); extend `prepare-install` with our bootstrap steps (plugin install, `ucp:config`, signing key, integration + ACL role, profile cache seed, `dal:refresh:index`, order/customer seed from `docker/seed_*.py` logic); `MCP_SERVER=1` in `.env.local`; bump `lite4mariadb` ≥ 0.1.2; fix `/agents.md` OOM. Publish to Pages + R2 | **1 week** | upstream PRs to shyim/FriendsOfShopware are the right home for the generic fixes (lite4mariadb pin, Fiber patch, OPENSSL_CONF, `cluster_setup`) |
| **B — Anthropic proxy + shopper demo in the browser** | Cloudflare Worker (Turnstile, session token, budget, streaming passthrough, BYOK bypass); agent host in the browser: **Pyodide first** (keeps ADR-1: unchanged blueprint packages), fallback TS if Pyodide startup or package pins prove painful; `UcpClient` signing via WebCrypto/`cryptography`; storefront UI from `web-shared` as static export; handoff into the WASM storefront's `/claude-commerce/continue` | **2 weeks** | A |
| **C — merchant agent in the browser** | Merchant portal UI + `ShopwareMerchantBackend` over `/api/_mcp` (dryRun previews, writes), ledger in SQLite-in-IDBFS or IndexedDB, `system:config`/ACL demo, "reset demo" button (playground already has reset/export) | **1–2 weeks** | A, B |
| Later | 6.7.14 tree when released (progressive MCP discovery, no flag); `tcpOverFetch` for same-origin profile fetch; multi-tab lock UX; `SwagMcpMerchantTools` | 0.5 week each | – |

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| WASM performance on visitor hardware: 155 MB download, 8 s boot here → 20–40 s elsewhere; PHP asyncify is ~3–5× slower than native; low-end laptops may OOM | High | Loading screen with progress (playground has one), pre-warm the kernel cache in the zip, strip `administration` bundles (already), lazy assets, "reset" instead of reload, minimum-hardware note |
| DB persistence: IndexedDB debounced flush; closing the tab mid-write or two tabs → corrupt state; browser eviction | Medium | Playground wipes and re-seeds on seed hash mismatch; add a "reset demo" CTA; treat state as ephemeral |
| Patches to vendor code (`mcp/sdk`, `ucp-php-sdk`) drift with upstream versions | Medium | Keep them in `overrides/` like the playground does; upstream a `Fiber`-optional mode to `mcp/sdk` and a configurable `ucp_sdk.storage.dsn`/driver to the SDK (the SDK already defaults to SQLite when unconfigured) |
| API key / abuse of the proxy | High | Turnstile + budgets + model allow-list; BYOK toggle; kill switch |
| Licensing of bundled bits: `lite4mariadb` is **GPL-2.0-only** (MariaDB) — shipping the wasm is fine, but a derived work must stay GPL-compatible; Shopware core/`swag/demo-data`/`SwagAgenticCommerce` are MIT; demo images ship with `swag/demo-data` (shopware AG); Cloudflare Turnstile ToS | Medium | Attribution page; keep our shell MIT and the MariaDB wasm as an unmodified separately-fetched artifact |
| Browser support gaps (Safari SW/SAB quirks, iOS impossible) | Medium | Detect and show requirements; recommend Chrome/Firefox |
| The demo fakes nothing but still differs from production (no https identity linking unless hosted on https, no webhooks, single PHP instance) | Low | Label the limitations in the UI |

## Appendix A — patches required in the playground tree (all verified in the spike)

1. `vendor/mcp/sdk/src/Server/Protocol.php`: run request handlers inline instead of in a `\Fiber` (3 lines).
2. `vendor/ucp-php-sdk/symfony-bundle/src/Bridge/DoctrineDbal/ConnectionFactory.php`: for `mysql://` DSNs return `DriverManager::getConnection(['driverClass' => App\Playground\MariadbLiteDriver::class, 'dbname' => 'shopware'])` (4 lines).
3. `src/runtime.mjs` / `src/php-web-runtime.mjs`: `emscriptenOptions.ENV.OPENSSL_CONF = '/internal/openssl.cnf'` and write a minimal cnf (`[req]\ndistinguished_name = dn\n[dn]\n`) into MEMFS (2 lines each).
4. `src/frontend-assets.mjs` `runShopwareConsole`: `(new Symfony\Component\Dotenv\Dotenv())->usePutenv()->bootEnv('/shopware/.env')` before creating the kernel (1 line).
5. `shopware/composer.json`: `composer require shopware/agentic-commerce:1.3.0 ucp-php-sdk/symfony-bundle:"^0.0.5"` (path repo already present) so `plugin:install` skips `composer require`; `.env.local`: `MCP_SERVER=1`, `SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1`.
6. `package.json`: `lite4mariadb` ≥ 0.1.2 (NUL-byte truncation fix).
7. Seed: `INSERT INTO ucp_platform_profile_cache (uri, payload, expires_at) VALUES (<profile url>, <agent-profile.json>, NULL)`.

## Appendix B — scratch artefacts (outside the repo)

`/tmp/shopware-playground` (patched clone, built bundle), `/tmp/pg-probe/*.mjs` (probe scripts: `php-ext-probe.mjs`, `api-probe.mjs`, `api-probe2.mjs`, `ucp-probe.mjs`, `browser-boot.mjs`), `/tmp/pg-*.log` (raw outputs), `/tmp/pg-probe/playground-boot.png`. The only side effect outside `/tmp` — one `ucp_signing_keys` row the SDK's stray `pdo_mysql` connection wrote into this repo's Docker MariaDB — was deleted again; the Docker shop is unchanged.
