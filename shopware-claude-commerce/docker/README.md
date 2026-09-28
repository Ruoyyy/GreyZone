# Docker Shopware

Local Shopware 6.7 used by the shopping (storefront) and merchant agents. Everything in this
folder is owned by the bootstrap workstream (ADR-10 handoff plugin, ADR-11 pinned/idempotent
bootstrap, ADR-14 merchant identity).

## Image and version lane

**`dockware/shopware:6.7.13.0`** — one container with Shopware preinstalled, MariaDB and a demo
catalog; `linux/arm64` + `linux/amd64` tags, so it boots on Apple Silicon without qemu. Not used:
`shopware/docker-dev` (generates a project first, slower to first HTTP 200), the production
`ghcr.io/shopware/docker-base` (no Shopware inside), the deprecated `dockware/play|dev`.

We stay on the **6.7.13 lane**: `6.7.13.1` (2026-08-25) is a drop-in patch (change the tag in
`compose.yaml`, `docker compose up -d`, re-run bootstrap); **no 6.7.14.x release exists yet**.
6.7.11–6.7.13 gate the MCP server (`POST /api/_mcp`, UCP MCP proxy) behind the `MCP_SERVER=1`
feature flag, which compose (`environment`) and bootstrap (container `.env`) both set. 6.7.14+
removes the flag — the server is always on and switches to *progressive tool discovery*
(`toolsets/list` + `toolsets/enable`; `shopware_common.mcp_client.McpClient.ensure_tool`
already handles both). See `docs/version-matrix.md`.

## Ports and credentials

| What | Value |
|---|---|
| Storefront / Admin | http://localhost:8080 — `/admin`, `admin` / `shopware` (setup only) |
| MySQL | `localhost:3306`, `root` / `root`, database `shopware` |
| UCP discovery | http://localhost:8080/.well-known/ucp |
| Admin MCP | `POST http://localhost:8080/api/_mcp` (integration token, see below) |
| Host credentials | `docker/.generated.env` (git-ignored) |
| Agent signing key | `secrets/ucp-agent-signing-key.pem` (git-ignored, mode 600) |

The admin password is used by `bootstrap.sh` / `verify.sh` only. The hosts never see it: they
authenticate with the integration in `.generated.env` (ADR-14).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
docker compose -f docker/compose.yaml up -d
./docker/bootstrap.sh        # ~25 s on re-run, a few minutes on a fresh volume
./docker/verify.sh           # exit 0 = everything below holds
```

`bootstrap.sh` needs the repo venv (`cryptography`, `httpx`) — override with `PYTHON=...`.

## What bootstrap does (in order, every step idempotent)

1. **Wait** for the Admin API (`/api/_info/version` → 401/200) and MySQL.
2. **Container `.env`**: `APP_URL`, `MCP_SERVER=1`,
   `SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1` (allows the http
   `localhost` agent profile; local only) and `COMMERCE_AGENTS_HANDOFF_SECRET`
   (`openssl rand -hex 32`, generated once; existing value in the container or in
   `.generated.env` is reused). No console command edits `.env`, so this is `sed`.
3. **Sales-channel domain → `http://localhost:8080`** via Admin API
   `PATCH /api/sales-channel-domain/{id}` (`docker/shop_domain.py`).
   `bin/console sales-channel:update:domain` only swaps the host and cannot add the port.
4. **`SwagAgenticCommerce` pinned** to `SWAG_AGENTIC_COMMERCE_REF`
   (default `df0ae7231e2b2090ccfbf3a31e7a462b40b69a0f`): clone if missing, `git checkout` if
   HEAD differs, skipped when already at the ref. `ucp-php-sdk/symfony-bundle 0.0.6`
   via composer (skipped when present; path-repository fallback when Packagist fails). Plugin
   installed+activated or updated (`plugin:update` when a newer version is detected).
5. **`SwagMcpMerchantTools` pinned** to `SWAG_MCP_MERCHANT_TOOLS_REF`
   (default `01e2082e99a4e9a2e56cdfd69faa38cd7c988efe`), same procedure.
6. **`CommerceAgentsHandoff`** (`docker/plugins/CommerceAgentsHandoff`, ours) copied into the
   container and installed / updated (the plugin migration creates
   `commerce_agents_handoff_code`). See the plugin README.
7. **Agent signing key**: `secrets/ucp-agent-signing-key.pem` (P-256, PKCS#8) generated with
   `openssl` **only when missing**; `docker/agent_key.py write-profile` derives the JWK exactly
   like `shopware_common.http_signing.public_jwk` (`kid` =
   base64url(sha256(uncompressed point))[:32]) and writes it as the **single** `signing_keys`
   entry of `agent-profile.json` (replace, not append). The profile is copied to
   `/var/www/html/public/agent-profile.json` — Shopware fetches it from *inside* the container
   as `http://localhost/agent-profile.json`; do not bind-mount it (dockware `chown`s `public/`
   on boot and a read-only mount exits the container).
8. **UCP exposure** (`docker/enable_ucp.py`): config via the plugin's Admin API
   `PUT /api/_admin/ucp/sales-channels/{id}/config` (same endpoint as the Administration —
   capabilities catalog/cart/discount/checkout/order/identity_linking, transports
   rest+mcp+embedded, loopback allowlists, `idempotencyRequired=true`,
   **`signaturePolicy=strict`**; `UCP_SIGNATURE_POLICY=log` to relax), written only when it
   differs; shop signing key: `ucp:signing-keys:generate` only when no active key exists,
   surplus active keys are retired + deleted so discovery publishes exactly one; the
   platform-profile cache is purged through `DELETE /api/_admin/ucp/platform-profiles/{id}`
   (no console command exists for that cache, but the Admin API does — no SQL needed).
9. **Seed catalog** (`docker/seed_catalog.py`, lookups by `productNumber`/name):
   `CA-TSHIRT` with S/M/L variants (L out of stock), `CA-OIL` with Grundpreis, delivery time
   "2-4 Tage" on all seeded products; Standard shipping 4.90 € gross, Express 9.90 € gross with
   delivery time "1-2 Tage", both assigned to the Storefront channel; CMS pages (type `page`,
   one text block) *Widerrufsbelehrung / Rückgabe*, *Versand & Lieferzeit*, *AGB*,
   *Datenschutz* under footer → "Rechtliches & Service", *Kontakt* under the service
   navigation; `footerCategoryId` / `serviceCategoryId` are created and set on the channel
   when missing.
10. **Seed orders** (`docker/seed_orders.py`): 40 guest checkouts through the Store API,
    backdated over 60 days (`PATCH /api/order/{id} orderDateTime`) and driven through the state
    machines; marker `customerComment = "commerce-agents-seed"`, skipped once ≥ 40 exist.
    Mix: 29 `completed`/`paid`/`shipped`, 4 `open`/`open`/`open` older than 3 days,
    2 `open` with transaction `failed`, 2 `cancelled`/`cancelled`, 3 `in_progress`/`paid`
    unshipped (`order_delivery` `open`). Then `dal:refresh:index`.
11. **`docker/.generated.env`** (`docker/write_credentials.py`, Admin API): `SHOPWARE_URL`,
    `SHOPWARE_ADMIN_URL`, `SHOPWARE_SALES_CHANNEL_ID`, `SHOPWARE_SALES_CHANNEL_ACCESS_KEY`,
    `UCP_AGENT_PROFILE_URL`, `UCP_TRANSPORT=mcp`, `SHOPWARE_ADMIN_TRANSPORT=mcp`,
    `UCP_AGENT_SIGNING_KEY_PEM_FILE=secrets/ucp-agent-signing-key.pem`,
    `COMMERCE_AGENTS_HANDOFF_SECRET`. `SHOPWARE_ADMIN_USERNAME/PASSWORD` are removed.
12. **Merchant identity** (`docker/merchant_identity.py`, ADR-14): ACL role and Integration
    `claude-merchant-agent` (`admin: false`), looked up by name/label; MCP allowlist set with
    `POST /api/_action/integration/{id}/mcp-allowlist` to exactly `MCP_TOOL_ALLOWLIST`
    (the single source of truth, also referenced by `merchant/README.md`):

    ```
    shopware-entity-search, shopware-entity-read, shopware-entity-aggregate,
    shopware-entity-upsert, shopware-entity-delete, shopware-entity-schema
    ```

    resources `[]`, prompts `[]`. Privileges (`ACL_PRIVILEGES`): `:read` on product_price,
    tax, currency, sales_channel, category, product_manufacturer, property_group,
    property_group_option, media, order, order_line_item, order_transaction, order_delivery,
    order_customer, state_machine, state_machine_state, customer, language, unit,
    delivery_time; `:read` + `:update` on product, product_translation; `:read/:create/
    :update/:delete` on promotion, promotion_translation, promotion_discount,
    promotion_discount_rule, promotion_sales_channel, rule, rule_condition (52 total).
    `SHOPWARE_INTEGRATION_ACCESS_KEY/SECRET_KEY` go to `.generated.env`; the secret is only
    known at creation, so when the integration exists but the file lacks the secret (or names
    another key) it is **rotated** via `PATCH /api/integration/{id}` and the script says so.
    Verified with a `client_credentials` token: `tools/list` on `/api/_mcp` equals the
    allowlist, `shopware-entity-search product` and a dryRun `shopware-entity-upsert product`
    succeed, `shopware-entity-search user` is refused (`Missing privilege: user:read`).
    Admin users are never restricted by allowlists; only the integration's tokens are.
13. **Signed vs unsigned** UCP request (`docker/ucp_signed_check.py`): RFC 9421 signed
    `POST /ucp/v1/catalog/search` → 200, unsigned → 401.
14. **Discovery** summary (`/.well-known/ucp`: transports, shop signing keys).

Running it twice produces identical output (only timings differ) and `verify.sh` counts
one integration, one role, one of each seeded product/CMS page/category/delivery time, one
active shop signing key, one agent signing key.

## verify.sh

`docker/verify.sh` (exit 1 on failure): discovery shows UCP active with transports
rest+mcp+embedded and **one** shop signing key; `agent-profile.json` carries exactly one
`signing_keys` entry matching the PEM and the container copy is byte-identical (`md5sum`);
signed 200 / unsigned 401; integration token `tools/list` == allowlist plus the three tool
calls above; handoff round trip (POST fresh code → 302 `/checkout/confirm` with
`sw-context-token` cookie, replay → `/checkout/cart`, GET same code → cart, GET fresh code →
confirm, garbage → cart); idempotency counts (`docker/verify_state.py`).

## Handoff plugin (ADR-10)

`docker/plugins/CommerceAgentsHandoff` — `POST /claude-commerce/continue` (form field `code`,
primary; `GET ?code=` noscript fallback). The code is minted by the storefront host
(`shopware_common.handoff.HandoffCodeIssuer`): HMAC-SHA256 signed, the context token
AES-256-GCM encrypted inside, 120 s lifetime, single use (DB table
`commerce_agents_handoff_code`). Logged-in customers are refused (their cart is never swapped),
the session id is migrated before the token is stored, then redirect to `/checkout/confirm`.
Secret: `COMMERCE_AGENTS_HANDOFF_SECRET` (≥ 32 bytes) in the container `.env` **and**
`.generated.env`. PHPUnit (21 tests, incl. a code minted by the Python issuer):

```bash
docker exec -u www-data commerce-agents-shopware bash -lc \
  'cd /var/www/html && php vendor/bin/phpunit -c custom/plugins/CommerceAgentsHandoff/phpunit.xml'
```

## Commands inside the shop

```bash
docker exec -u www-data commerce-agents-shopware bash -c 'php /var/www/html/bin/console plugin:list'
docker exec -u www-data commerce-agents-shopware bash -c 'php /var/www/html/bin/console ucp:channels'
docker exec -u www-data commerce-agents-shopware bash -c 'php /var/www/html/bin/console ucp:config:show --sales-channel=Storefront'
docker exec -u www-data commerce-agents-shopware bash -c 'php /var/www/html/bin/console ucp:config:validate'
docker exec -u www-data commerce-agents-shopware bash -c 'php /var/www/html/bin/console ucp:signing-keys:list --sales-channel=Storefront'
```

Quirks worth knowing: `/ucp/mcp` needs the `UCP-Agent` header on `initialize` too and rejects
`clientInfo.version: "0"` (PHP `empty()`); until the domain is rewritten the storefront `/`
answers 400 (the Admin API 401 is the health signal).

## Store API shapes the hosts rely on

* `GET /store-api/navigation/footer-navigation/footer-navigation?depth=3` → list of category
  nodes `{id, name, type, cmsPageId, children[]}` (folder "Rechtliches & Service" → the four
  policy pages); `service-navigation` → `[Kontakt]`.
* `POST /store-api/category/{id}` → `{name, translated.name, breadcrumb[], cmsPage:
  {type: "page", sections[]: {type: "default", blocks[]: {type: "text", slots[]: {type: "text",
  slot: "content", config.content: {source: "static", value: "<h2>…</h2><p>…</p>"},
  data.content: "<h2>…</h2><p>…</p>", translated.config.content.value}}}}}`.
* `POST /store-api/shipping-method` with `associations.prices/deliveryTime` →
  `elements[]: {name, deliveryTime: {name, min, max, unit}, prices[]: {currencyPrice[]:
  {currencyId, gross, net}}}` — Standard 4.90/4.12, Express 9.90/8.32.

## Stop / reset

```bash
docker compose -f docker/compose.yaml down        # keeps the MySQL volume
docker compose -f docker/compose.yaml down -v     # drops it: full reinstall + reseed on next bootstrap
```

A recreate of the container drops the plugin files under `/var/www/html` (they live in the
container layer); MySQL (catalog, UCP config, integration, orders) survives in the
`shopware-mysql` volume. Re-run `./docker/bootstrap.sh` — it restores the plugins at the
pinned refs, keeps the existing handoff secret and agent key, and rotates the integration
secret only if `.generated.env` lost it.
