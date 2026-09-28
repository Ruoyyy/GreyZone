# Browser demo — Shopware × Claude Commerce Agents in the browser

**[Open the live demo](https://agentic-commerce-lab.github.io/shopware-claude-commerce/)** — click that link; nothing to install.

Shopware 6.7.13.1 runs in the tab (PHP WASM + MariaDB WASM via [FriendsOfShopware/shopware-playground](https://github.com/FriendsOfShopware/shopware-playground)). The blueprint shopping and merchant hosts run on **Pyodide**. GitHub Actions builds the gitignored WASM shop and publishes the static tree to GitHub Pages.

Cold boot downloads ≈ 150 MB and takes ~20–40 s on a typical laptop. A reload reuses the browser cache and the seeded database in IndexedDB. Use a current desktop Chromium or Firefox (service workers + SharedArrayBuffer). Mobile is not sensible.

## Highlights (whole repo)

| Piece | Link |
|---|---|
| Live demo (this folder) | [agentic-commerce-lab.github.io/shopware-claude-commerce](https://agentic-commerce-lab.github.io/shopware-claude-commerce/) |
| Claude Code plugin | [`shopware-commerce-builder`](../plugins/shopware-commerce-builder/) |
| Shopware MCP / staging plugin | [`SwagCommerceAgentTools`](../shopware-plugins/SwagCommerceAgentTools/) |
| Checkout handoff plugin | [`CommerceAgentsHandoff`](../docker/plugins/CommerceAgentsHandoff/) |
| Docker + FastAPI stack | [Root README](../README.md#quick-start) |


## What the Pages build does

| Piece | On GitHub Pages |
|---|---|
| Demo shell (boot UI, shop iframe, agent panels) | Yes |
| Shopware storefront / admin / Store API / UCP / MCP in WASM | Yes, after the boot download (same pins as `docker/bootstrap.sh`) |
| Catalog, cart, seeded products and orders | Yes |
| Chat (Claude) | Only if you paste an `ANTHROPIC_API_KEY` in the UI. Pages cannot run the Node proxy. The tab calls `api.anthropic.com` with `anthropic-dangerous-direct-browser-access`. If Anthropic rejects that browser request, chat fails; the shop still works. |
| Checkout handoff (agent cart → in-browser Shopware checkout) | Yes — verified end to end by `npm run e2e` against the local server (dev and static build); not yet re-verified on a fresh Pages build |
| Cross-origin isolation | GitHub Pages cannot set COOP/COEP. The playground service worker adds those headers; the shell reloads once so `SharedArrayBuffer` works. The assembled `service-worker.js` gets a `__DEMO_PUBLIC_BASE__` banner only — rewriting its `/demo/` and `/php/` route constants would send those files to PHP WASM and deadlock boot. UCP `embeddedAllowedOrigins` stay pathless (`https://host`, not `https://host/repo`) or Shopware throws and the storefront never renders. Shopware `APP_URL` and `sales_channel_domain` keep the repo path so theme CSS (`all.css`) and storefront routing resolve under `/<repo>/`. |

The [pages.yml](../.github/workflows/pages.yml) workflow uses a **checked-in SQL seed plus compiled theme/media** on GitHub Actions (full WASM install runs on contributors' machines — see [`ci-fixtures/README.md`](ci-fixtures/README.md)). Theme CSS is served statically at `/versions/6.7.13.1/assets/theme/<hash>/css/all.css` (the service worker maps `/theme/…`). First successful deploy often takes **10–30+ minutes**. Until then, the live URL may 404.

Feasibility measurements: [`docs/browser-demo-feasibility.md`](../docs/browser-demo-feasibility.md).

## Run locally (contributors)

The local Node server is optional. It sets COOP/COEP without a service-worker reload and can proxy Anthropic from the repo `.env` so the key never enters the tab.

```bash
cd browser-demo
npm install
npm run build          # first time: PHP, Composer, Python, network (~10–30+ min)
cp ../.env.example ../.env   # ANTHROPIC_API_KEY for the local proxy (optional)
npm start              # → http://127.0.0.1:4188
```

`npm run dev` is Vite HMR against an already-built playground. `npm start` needs `dist/site/` from `npm run build`. Individual steps: `npm run build:*` in `package.json`.

A Pages-shaped local tree (project path prefix):

```bash
DEMO_BASE_PATH=/shopware-claude-commerce/ npm run build:app
DEMO_BASE_PATH=/shopware-claude-commerce/ npm run build:site
```

That prefix is what [pages.yml](../.github/workflows/pages.yml) sets from `actions/configure-pages`.

| What | Local URL |
|---|---|
| Demo shell | http://127.0.0.1:4188/ |
| Shopware storefront (iframe) | `/index.php` after boot |
| Anthropic proxy (local only) | `/api/anthropic/messages` |
| Proxy status | `/api/anthropic/status` |

Bind address: `--host` / `--port` or `HOST` / `PORT` (default `127.0.0.1:4188`).

## What you can do in the demo

1. **Storefront** — the real Shopware storefront and administration (`Admin` button; login
   `admin` / `Shopware123!`, see `build/config.mjs`) in an iframe. The blue **Agentic Commerce demo** launcher at the bottom
   right (plugin `plugins/DemoOverlay/`) shows boot progress and opens the two demos.
2. **Shopping assistant** — the blueprint shopping agent (`storefront/api` + `StorefrontBackend`)
   over UCP/MCP and the Store API. It searches the live catalog (UCP, then Store API search,
   then `POST /store-api/product` listing) and edits *the visitor's own cart*
   (same `sw-context-token` as the storefront). Opening a product in the Shopware iframe
   focuses that PDP in the shopping session (`POST /api/session/focus` → `get_product_details`).
   Opening the shopping view also syncs the grid catalog into the session
   (`POST /api/session/sync-catalog`) so exact titles such as "Main product" resolve.
   `Checkout in Shopware` hands off into the in-browser Shopware checkout through the
   `CommerceAgentsHandoff` plugin.
3. **Merchant portal** — the blueprint merchant agent (`merchant/api` + `MerchantBackend`) over
   `/api/_mcp` with the `SwagCommerceAgentTools` write tools: dashboard from Admin MCP, staged
   changes with a Shopware dry-run preview, approve → applied (visible on the storefront) or dismiss.

`Claude: …` in the bar switches between the local proxy and **BYOK** (your own key, kept in memory
for this tab only — the mode is remembered, the key is not — and sent straight to
`api.anthropic.com` with `anthropic-dangerous-direct-browser-access`). `Reset` drops the IndexedDB
snapshot and reboots from the seed.

## Architecture

```text
 browser tab (crossOriginIsolated)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ demo shell (React/Vite)  app/src/engine/demo.ts                              │
 │   ├─ <iframe> Shopware storefront / admin  ← service worker → browser worker │
 │   │     PHP 8.4 WASM (@php-wasm) + MariaDB WASM (lite4mariadb, SharedArray-  │
 │   │     Buffer) + DemoOverlay plugin  (playground/, patches/)                 │
 │   ├─ vendored storefront/web + merchant/web (Next.js apps under Vite shims)   │
 │   └─ agent host Web Worker (host/worker.ts → Pyodide → host/bootstrap.py)    │
 │         FastAPI apps of storefront/api and merchant/api run unchanged;        │
 │         httpx → fetch bridge; requests to the virtual hosts                   │
 │         shopping.agent-host.invalid / merchant.agent-host.invalid are routed  │
 │         in-page, Shopware URLs go to the WASM shop, Anthropic to the proxy    │
 └──────────────────────────────────────────────────────────────────────────────┘
        │ /api/anthropic/* (streaming passthrough, per-session budget)
        ▼
 server/index.mjs (Node, no framework) — static files + COOP/COEP + key from ../.env
```

Build pipeline (`npm run build` = the `build:*` scripts in order): clone and patch the
playground → Composer-install Shopware with `SwagAgenticCommerce`, `ucp-php-sdk`,
`CommerceAgentsHandoff`, `SwagCommerceAgentTools`, `DemoOverlay` → boot the shop once in Node
(PHP WASM + MariaDB WASM), install/activate plugins, configure UCP, seed products/variants/orders
with the repo's own seed scripts, dump the DB → zip the image → sync backends, build wheels, fetch
Pyodide → Vite build → assemble `dist/site/`.

## Deploying the static build

`npm run build` leaves a self-contained tree in `dist/site/` (≈ 290 MB, see sizes below). Any
static host works if it can set the isolation headers; without them the playground's service
worker adds COOP/COEP itself and the shell reloads once (that is what GitHub Pages uses). Chat on
a static host is BYOK only — nothing there can hold the Anthropic key.

nginx:

```nginx
location / {
  root /srv/browser-demo/dist/site;
  try_files $uri $uri/ /index.html;
  add_header Cross-Origin-Opener-Policy same-origin always;
  add_header Cross-Origin-Embedder-Policy require-corp always;
  add_header Cross-Origin-Resource-Policy same-origin always;
  types { application/wasm wasm; application/octet-stream so dat; application/zip zip; application/x-tar tar; application/zip whl; }
  location ~* \.(wasm|so|dat|zip|whl|tar)$ { expires 1y; add_header Cache-Control "public, immutable"; }
  location = /service-worker.js { add_header Service-Worker-Allowed /; add_header Cache-Control no-cache; }
}
```

Caddy:

```caddyfile
demo.example.com {
  root * /srv/browser-demo/dist/site
  header Cross-Origin-Opener-Policy same-origin
  header Cross-Origin-Embedder-Policy require-corp
  header Cross-Origin-Resource-Policy same-origin
  header /service-worker.js Service-Worker-Allowed /
  try_files {path} /index.html
  file_server
}
```

Large artifacts in `dist/site/` (all git-ignored, produced by the build):

| Artifact | Size | Notes |
|---|---|---|
| `versions/6.7.13.1/shopware.zip` | 91 MB | Shopware image incl. vendor and plugins, unpacked into MEMFS at boot |
| `versions/6.7.13.1/assets/…` | ≈ 70 MB | Theme/admin assets served statically (three theme hashes, 9.7 MB each for the CAD import worker) |
| `assets/icu-*.dat` | 29 MB | ICU data for `intl` |
| `assets/php_8_4-*.wasm` (×2) | 19 MB each | PHP runtime (with and without JSPI) |
| `mariadb/lite4mariadb.wasm` | 16 MB | MariaDB |
| `demo/pyodide/` | 20 MB | Python runtime + packages |
| `demo/wheels/`, `demo/host/repo-tree.tar` | 2.5 MB | Agents |

A cold boot downloads ≈ 190 MB (compressible; enable gzip/brotli on the host), of which the
browser caches everything and keeps the seeded database in IndexedDB.

## Layout

```text
browser-demo/
├── app/                 React shell (Vite): boot UI, iframe, shopping/merchant views, Next.js shims
│   └── src/vendor/      Read-only copies of storefront/web, merchant/web, vendor/web-shared (generated)
├── build/               Pipeline scripts (pins in build/config.mjs) + their unit tests
├── e2e/                 Playwright spec + config (README inside)
├── host/                Pyodide bootstrap, agent-host worker; synced Python backends + wheels (generated)
├── patches/             Every change to third-party code, applied at build time (README inside)
├── plugins/DemoOverlay/ Shopware plugin: overlay, context route, chunked MCP result cache (README inside)
├── server/              Local Node server: static files, COOP/COEP, /api/anthropic proxy + tests
├── scripts/             sync-backends.sh (copies the Python backends and web UIs from the repo)
├── THIRD_PARTY.md       Licenses of the shipped WASM/Python components (lite4mariadb is GPL-2.0)
└── dist/site/           Assembled demo (gitignored; produced by npm run build / CI)
```

## Keeping the backends in sync

The agents are the repo's own packages, copied — not forked. `npm run sync-backends`
(`scripts/sync-backends.sh`, also part of `npm run build:host`) refreshes:

- `host/repo-tree/` → `repo-tree.tar`: `storefront/api`, `merchant/api`, `shopware_common`,
  `vendor/demo_common`, `vendor/skills`, `agent-profile.json`, with `SYNC_INFO.json` (git SHA,
  dirty count) baked in;
- `app/src/vendor/{storefront-web,merchant-web,web-shared}`: the Next.js UIs, with `@/` imports
  rewritten to relative paths (`build/relativize-imports.mjs`) and `globals.css` scoped per app
  (`build/scope-css.mjs`) so both design systems coexist in one document.

After a backend change: `npm run build:host` (wheels are rebuilt only when pins change), then
`npm run dev` picks the new tar up on the next boot. Plugin or seed changes need
`npm run build:shop && npm run build:prepare && npm run build:bundle`.

## Testing

```bash
npm test                 # server + build unit tests (31; Node only, no WASM boot)
npm run typecheck        # TypeScript for app/, host/ and the vendored UIs
npm run e2e              # Playwright against the local server: boot, overlay, both flows, screenshots
```

`npm run e2e` starts `server/index.mjs --dev` on port 4189, writes screenshots to
`e2e/test-results/screenshots/` and the measured boot/turn timings to `docs/timings.local.json`;
`DEMO_E2E_MODE=static` runs the same spec against `dist/site/` (see `e2e/README.md`). Last local
run: both agents ready 12.7 s after navigation (empty browser profile, localhost), shopping turn
11 s, merchant staging turn 14 s, 3/3 passing in dev and static mode.

Repo-wide offline tests (`pytest`, `ruff`) do not cover this tree; `ruff check .` may report findings under `host/`.

## Configuration

The local server reads `../.env` (repo root) then `browser-demo/.env`. Relevant variables:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables the local proxied chat path (otherwise paste a key in the UI) |
| `ANTHROPIC_WORKSPACE_ID` | Identity-linked keys |
| `DEMO_PROXY_*` | Per-session / per-IP budgets — see `server/anthropic-proxy.mjs` |
| `PORT`, `HOST` | Listen address (default `4188`, `127.0.0.1`) |
| `DEMO_BASE_PATH` | Public path for the assembled site (`/` locally, `/shopware-claude-commerce/` on Pages) |

Build-time pins (playground commit, plugin refs, Pyodide version): `build/config.mjs`.
Third-party code changes: `patches/README.md`. Shipped licenses: `THIRD_PARTY.md`.

## Limits and risks

- **Browser support**: desktop Chromium/Firefox with `SharedArrayBuffer`; Safari and mobile are
  not targets. PHP, MariaDB and Pyodide each hold their heap in the tab — close other heavy tabs.
- **Single-threaded PHP**: one request at a time inside the worker; the two agents plus the
  storefront share it, so parallel tool calls serialize (the bar counts the PHP requests; a
  merchant dashboard load is several dozen). MariaDB WASM rejects statements with literals > ~100 KB; large MCP results are
  chunked by `DemoOverlay`.
- **Model calls leave the browser** — via the local proxy (key in `.env`, per-session budget,
  `DEMO_PROXY_*`) or BYOK (`anthropic-dangerous-direct-browser-access`; Anthropic may reject
  browser requests for some key types). Everything else stays in the tab.
- **Demo-only routes**: `/commerce-agents-demo/context` exposes the storefront context token to
  the same origin; acceptable here only because shop, database and caller are one tab.
- **Seeded state is per browser**: the database lives in IndexedDB; `Reset` restores the seed.
  Nothing is shared between visitors or persisted server-side.
- **Turns are non-deterministic**: prompts in the e2e spec are unambiguous on purpose; the agents
  may ask back on vague requests (that is the blueprint behaviour, not a bug).
- **Not in this demo**: identity linking, the full eval gate / nightly CI on WASM, a hosted proxy
  for the static deployment (GitHub Pages cannot run one; chat there is BYOK only), separate
  origins for shop and portal.
