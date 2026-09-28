# DemoOverlay — Shopware plugin for the in-browser demo

`commerce-agents/demo-overlay` is installed into the WASM Shopware image by
`build/install-shop.mjs` (path repository) and activated by `build/prepare-shop.mjs`. It is
demo-only: it is not part of the Docker quick start and must not be installed on a real shop.

## What it adds

| Piece | Path | Purpose |
|---|---|---|
| Storefront overlay | `src/Resources/views/storefront/base.html.twig` → `component/commerce-agents-demo-overlay.html.twig` | Floating **Agentic Commerce demo** launcher (#189EFF) at the bottom right of every storefront page: boot status, two entries (*Shop with the assistant*, *Run the store with the merchant agent*) with per-agent readiness. |
| Administration overlay | `src/Resources/views/administration/index.html.twig` | The same component on the administration (login page and app), rendered with the admin CSP nonce. |
| Context route | `src/Storefront/Controller/DemoContextController.php` — `GET /commerce-agents-demo/context` | Returns the storefront session's `sw-context-token` as JSON so the shell can attach the shopping agent to the **same cart** the visitor sees in Shopware. Twig cannot expose `context.token` (Shopware blocks it deliberately); this route is acceptable only because PHP, database and caller all run inside one browser tab. |
| Chunked MCP result cache | `src/Mcp/ChunkedToolResultCacheStorage.php` (decorates `Shopware\Core\Framework\Mcp\ToolResultCacheStorage`, see `src/Resources/config/services.xml`) | MariaDB WASM fails with *Thread stack overrun* on single statements with literals above ~100 KB. Large tool results (merchant dashboard reads) are inserted as a first 96 KB chunk plus `CONCAT` updates; the read path is unchanged. |

## Shell ↔ overlay protocol

The overlay never calls the agents itself; it talks to the embedding shell (`window.parent`) over
`postMessage`, same origin only:

```text
overlay → shell   { type: 'commerce-agents-demo', action: 'ready' | 'open-shopping' | 'open-merchant',
                    surface: 'storefront' | 'administration', path,
                    productId?, productName? }   # product fields on a PDP (`page.product`)
shell → overlay   { type: 'commerce-agents-demo-status', text, phase: 'booting' | 'ready' | 'error',
                    agents: { shopping: 'idle'|'loading'|'ready'|'error', merchant: … } }
```

Without a parent frame (e.g. the storefront opened directly), the entries explain that the demo
needs the shell instead of doing nothing.

## Changing the plugin

After editing, rebuild the image and snapshot: `npm run build:shop && npm run build:prepare &&
npm run build:bundle` (the plugin files are copied into the Shopware tree at install time), then
`npm run build:site` for `dist/site/`. `npm run e2e` checks that the launcher renders in storefront and administration
and that both entries open the right demo view.
