# Third-party components of the browser demo

Everything listed here is fetched or built at `npm run build` time and shipped inside `dist/site/`
(none of it is committed). Versions are pinned in `build/config.mjs` and `playground/package.json`.

| Component | Version / pin | License | Role in the demo |
|---|---|---|---|
| [FriendsOfShopware/shopware-playground](https://github.com/FriendsOfShopware/shopware-playground) | commit `c86f241` (+ `patches/playground-c86f241.patch`) | MIT | Browser worker, service worker, Shopware image installer |
| [lite4mariadb](https://github.com/shyim/lite4mariadb) (MariaDB compiled to WebAssembly) | 0.1.2 | **GPL-2.0-only** | The shop database, in a Web Worker (`/mariadb/lite4mariadb.wasm`, 16 MB) |
| [@php-wasm/web](https://github.com/WordPress/wordpress-playground) (PHP 8.4 + ICU + intl) | 3.1.52 | GPL-2.0-or-later | PHP runtime for Shopware (`/assets/php_8_4-*.wasm`, `/assets/icu-*.dat`, `/assets/intl-*.so`) |
| [Shopware 6](https://github.com/shopware/shopware) | 6.7.13.1 | MIT | Storefront, administration, Store API, Admin API, MCP server (`/versions/6.7.13.1/shopware.zip`) |
| [shopware/agentic-commerce](https://github.com/shopware/agentic-commerce) (`SwagAgenticCommerce`) | `df0ae72` | MIT | UCP profile, `/ucp/mcp`, checkout capabilities |
| [ucp-php-sdk](https://github.com/agentic-commerce-alliance/ucp-php-sdk) (+ `patches/vendor-ucp-php-sdk-…patch`) | `0.0.6` | Apache-2.0 | UCP protocol implementation used by the plugin |
| [mcp/sdk](https://github.com/modelcontextprotocol/php-sdk) (+ `patches/vendor-mcp-sdk-…patch`) | 0.7.1 | MIT | Shopware's MCP transport (Fiber-free in WASM) |
| [Pyodide](https://github.com/pyodide/pyodide) | 314.0.6 | MPL-2.0 | Python runtime for both agent hosts (`/demo/pyodide/`, 20 MB) |
| Anthropic blueprint packages (`commerce_common`, `merchant_agent_*`, `shopping_agent_*`) and this repo's backends | pinned in `build/build-wheels.sh` / `requirements.txt` | Apache-2.0 | The agents themselves (`/demo/wheels/`, `/demo/host/repo-tree.tar`) |
| `anthropic`, `httpx`, `fastapi`, `pydantic`, `mcp`, `tzdata`, … (pure-Python wheels) | `requirements.txt` pins | MIT / BSD / Apache-2.0 | Agent runtime dependencies inside Pyodide |
| React, Vite, Tailwind CSS (demo shell) | `package.json` | MIT | Boot UI, view switcher, vendored Next.js apps |
| Playwright (tests only) | `package.json` devDependency | Apache-2.0 | `npm run e2e` |

## GPL notice

`lite4mariadb` and `@php-wasm/*` are GPL-licensed. They are delivered as separate WebAssembly and
JavaScript files that the browser loads at runtime; the demo shell, the Shopware plugins and the
agents in this repository are not derived from them and keep their own licenses (MIT / Apache-2.0).
When you redistribute `dist/site/`, keep these components' license texts available
(`playground/node_modules/lite4mariadb/LICENSE`; the `@php-wasm/*` packages carry the license in
their `package.json` and the WordPress Playground repository) and point to their source repositories
as done above.

## Patched components

`patches/` holds every change made to third-party code (see `patches/README.md`). The patches are
applied to the fetched copies during `npm run build`; the upstream repositories are not modified.
