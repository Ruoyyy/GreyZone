# CI seed fixtures

GitHub-hosted runners hit PHP WASM traps during Shopware installer migrations. The Pages workflow copies these files into the playground bundle so `prepare-shop.mjs` skips the in-WASM install (same seeded catalog, plugins, and demo credentials as a local `prepare-shop`).

Regenerate when bootstrap, plugins, or seed scripts change:

```bash
cd browser-demo
FORCE_INSTALL=1 node build/prepare-shop.mjs
cp playground/public/versions/6.7.13.1/shopware.sql.gz ci-fixtures/6.7.13.1/
cp app/public/demo/shop-config.json ci-fixtures/6.7.13.1/
# compiled theme + media the SQL dump's themeSeed points at (xxh128 path)
tar -C playground/shopware/public -czf ci-fixtures/6.7.13.1/public-assets.tar.gz \
  theme/63704ac15ed895d93fd0231a1370e8b2 \
  theme/01a067b953df72cb9cd499353fab7d5d \
  media
```

| File | Why CI needs it |
|---|---|
| `shopware.sql.gz` | Seeded catalog, plugins, `storefront.themeSeed` — skips the in-WASM installer |
| `shop-config.json` | **Demo-only** integration/handoff values for the public WASM shop, not production secrets |
| `public-assets.tar.gz` | Compiled `theme/<hash>/css/all.css`, `storefront.js`, and `media/…` matching that dump. Composer install does not compile the theme; without this archive Pages 404s `all.css` / logos while HTML is 200 |

`seed-ci-fixtures.mjs` extracts the archive into `shopware/public/` and writes `.env.local` with `MCP_SERVER=1` so `/api/_mcp` is registered when prepare-shop is skipped. `bundle-shop.mjs` refuses to publish a site that is missing those files.
