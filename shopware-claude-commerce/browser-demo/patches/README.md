# Patches applied at build time

Every change to third-party code lives here as an explicit patch. `build/fetch-playground.mjs`
applies the `playground-*` set to the cloned playground, `build/install-shop.mjs` applies the
`vendor-*` set to the Composer `vendor/` tree of the Shopware image. `apply-patches.sh` is
idempotent (already-applied patches are detected with `patch --dry-run -R` and skipped).

```bash
patches/apply-patches.sh playground playground/            # playground-*.patch, -p1 on the git tree
patches/apply-patches.sh vendor     playground/shopware/    # vendor-*.patch,     -p1 on the Shopware tree
```

| Patch | Target | Why |
|---|---|---|
| `playground-c86f241.patch` | FriendsOfShopware/shopware-playground @ `c86f241` | See below — WASM runtime fixes plus the hooks the demo shell needs |
| `vendor-mcp-sdk-0.7.1-no-fibers.patch` | `vendor/mcp/sdk/src/Server/Protocol.php` | php-wasm has no Fiber support; the MCP server handled every request inside a `Fiber`. The patch runs the handler directly (same result, no Fiber). Shopware's `/api/_mcp` (merchant agent) and `/ucp/mcp` (shopping agent) depend on it. |
| `vendor-ucp-php-sdk-symfony-bundle-0.0.6-bridged-connection.patch` | `vendor/ucp-php-sdk/symfony-bundle/src/Bridge/DoctrineDbal/ConnectionFactory.php` | The UCP bundle opened its own PDO connection from `DATABASE_URL`. In WASM there is no MySQL socket; the patch routes it through Shopware's DBAL connection, i.e. the lite4mariadb bridge. |

## `playground-c86f241.patch` in detail

| File | Change |
|---|---|
| `package.json` | `lite4mariadb ^0.1.2` — 0.1.1 truncated `LONGTEXT` at the first NUL byte, which broke `cheapestPrice` on product listings. |
| `src/app-route.mjs` | More static extensions (`md`, `whl`, `tar`, `py`); `/demo/` (shell, Pyodide, wheels, host) and `/api/anthropic/` (proxy) are never routed to PHP; public-base helpers for sub-path hosting (GitHub project Pages). Route classification uses logical paths (`withoutPublicBase`); `assemble-site` must not rewrite those prefixes in `service-worker.js` or Pages deadlocks at "mounting backends". `postToWindowClient` uses `{ transfer }` so the SW → page PHP bridge does not throw in Chromium embeds. `phpRequestHeaders` drops hop-by-hop / `X-Forwarded-*` / `Accept-Encoding` so iframe navigations do not break Shopware domain mapping. |
| `src/browser-runtime.mjs`, `src/runtime.mjs`, `src/php-web-runtime.mjs` | `OPENSSL_CONF=/internal/openssl.cnf` in the Emscripten `ENV` plus a minimal `openssl.cnf` written to MEMFS — OpenSSL 1.1.1 in WASM otherwise fails in `openssl_pkey_new()` (UCP agent keys, JWT). Dump/zip/prepend URLs honour the public base. |
| `src/frontend-assets.mjs` | The in-WASM Shopware console boots `/shopware/.env` through Symfony Dotenv (`usePutenv`), so `bin/console`-style commands (theme compile, plugin install) see the same environment as web requests. |
| `src/service-worker.mjs` | Adds `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp` and `Cross-Origin-Resource-Policy: same-origin` to every response it serves (the `coi-serviceworker` style fallback for static hosts without header control) and bypasses `/api/anthropic/*` so proxy streams reach the network untouched. PHP routing uses `postToWindowClient` (transfer options, client retry), the absolute request URL, and `phpRequestHeaders`. |
| `src/sql-dump.mjs` | `shopUrlVariants` keeps a URL path so GitHub project Pages (`https://host/repo`) is written into `sales_channel_domain`, not just the host origin. |
| `php/auto_prepend.php` | On a public-base prefix, rewrite `/index.php` after that prefix and set `SCRIPT_NAME` so Symfony's base path matches the sales-channel domain. Close a leftover PHP session so the next WASM request does not 400 with "session already started". |
| `src/front-controller.mjs` | Patches Shopware `public/index.php` so `APP_URL` keeps the Pages path (`https://host/repo`). Without that, RequestTransformer cannot map the sales-channel domain (storefront 400) and theme CSS is emitted at `/theme/…/all.css` (404). The compiled `all.css` / media files themselves are published from `ci-fixtures/<v>/public-assets.tar.gz` (CI skips `theme:compile`). |

## Things that are *not* patches

- **`plugins/DemoOverlay/`** is a regular Shopware plugin (Twig overlay, context route, chunked
  MCP result cache). The chunked cache works around a MariaDB-WASM limit — statements with
  literals above ~100 KB fail with *Thread stack overrun* — by inserting large tool results in
  96 KB pieces. It is a service decoration, not a change to Shopware.
- **Runtime rewrites** in `app/src/engine/demo.ts` (`rewriteUcpOrigin`) adjust the seeded UCP
  configuration (`profileDomain`, continue URL) to the shop public URL the demo actually runs on;
  `embeddedAllowedOrigins` / `embeddedFrameAncestors` stay pathless origins (a Pages path there
  throws `UcpConfigException` and the storefront never renders). The seed is made under a
  placeholder origin at build time. `frameLoaded` treats a Symfony Oops document as a failed
  storefront step so agents do not start on an error page.
- **`build/prepare-shop.mjs`** seeds the agent profile into `ucp_platform_profile_cache`
  (`expires_at NULL`), so the UCP plugin never needs outbound HTTP from WASM — the blocker
  measured in `docs/browser-demo-feasibility.md`.

## Upstream candidates

The `mcp/sdk` Fiber bypass and the `ucp-php-sdk` bridged connection are generic WASM/embedded
fixes and could be offered upstream behind a feature flag. The `OPENSSL_CONF` and
`lite4mariadb ^0.1.2` changes belong in the playground. Re-generate a patch after editing the
fetched tree with `git -C playground diff > patches/playground-<commit>.patch` (playground) or
`diff -u` against a clean Composer install (vendor).
