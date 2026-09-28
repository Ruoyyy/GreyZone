# Shopware mapping

Discovered against dockware **Shopware 6.7.13.0** (PHP 8.4, `MCP_SERVER=1`) with `SwagAgenticCommerce` pinned in `docker/bootstrap.sh`. UCP protocol version **2026-08-25**. Everything below was read from the live shop; the netless replay in `storefront/api/tests/replay.py` and `merchant/api/fake_admin.py` mirrors these shapes so the test suite pins them.

## Transports (ADR-12)

| Side | Transport | Endpoint | Role |
|---|---|---|---|
| Shopper | Discovery | `GET /.well-known/ucp` | Profile, capabilities, service URLs, active signing keys |
| Shopper | **UCP over MCP** (default) | `POST /ucp/mcp` (Streamable HTTP) | Primary. `UCP_TRANSPORT=mcp` |
| Shopper | UCP REST | `/ucp/v1/*` | Fallback when MCP is unreachable, or `UCP_TRANSPORT=rest` |
| Shopper | Store API | `/store-api/*` | Variants, disclosures, account login, authenticated customer carts/orders, brand |
| Shopper | Embedded checkout | iframe origins | Not used; checkout is a handoff (ADR-10) |
| Merchant | **Admin API MCP** (default) | `POST /api/_mcp` | Reads, aggregations, `dryRun=true` previews, writes with `dryRun=false`. `SHOPWARE_ADMIN_TRANSPORT=mcp` |
| Merchant | Admin REST | `/api/*` | Fallback (`SHOPWARE_ADMIN_TRANSPORT=rest`): `POST /api/search/*`, `PATCH /api/product/{id}` |

Fallback is one-directional per call: a transport-level failure (connection refused, 404/405 without a UCP document, session expiry that does not recover) switches to the other transport; a UCP error document (`ucp.status=error`) is a business error and never triggers a fallback. `UcpClient(..., fallback=False)` pins one transport (used by `storefront/scripts/smoke.py --no-fallback`).

### MCP session mechanics (`shopware_common/mcp_client.py`)

1. `initialize` (`protocolVersion` `2025-06-18`) → response carries `Mcp-Session-Id`.
2. `notifications/initialized` → `202 Accepted`.
3. `tools/list`, `tools/call` with `Mcp-Session-Id` and `Mcp-Protocol-Version` on every request; bodies come back as JSON or as an SSE stream (`event: message`), both are handled.
4. `404`/`400 session not found` → one transparent re-initialize and retry.
5. `DELETE /ucp/mcp` ends the session on `close()`.
6. **One in-flight request per session.** Shopware's streamable-HTTP server (`mcp/sdk`) races when a session answers two `tools/call` at once: one of them comes back `200` with an **empty body** (reproduced live on 6.7.13 with three concurrent `shopware-entity-search` calls; the merchant portal's dashboard/overview/ledger reads landed together). `McpClient.request` serialises requests on a session; callers keep issuing them concurrently.
7. **Offloaded tool results.** Above an inline size cap (observed at an order search with associations, `responseSize` ≈ 160 KB) the Admin MCP answers `{"success": true, "data": null, "_meta": {"resourceUri": "shopware://tool-result/<id>", "responseSize": …, "note": "Response too large for inline delivery…"}}`. `McpClient.read_resource` runs `resources/read` on that URI and `McpTransport` swaps the parked JSON in as the payload, so a large result is never mistaken for an empty one.

`/ucp/mcp` additionally requires the `UCP-Agent` header on **every** request including `initialize` (`422 $.headers.ucp-agent is required` otherwise). Signed requests (RFC 9421 + RFC 9530, see below) work on both transports because the signature covers the raw JSON-RPC body.

### Live UCP tool envelope

`tools/call` results wrap the UCP document:

```json
{"success": true, "dryRun": false, "data": { ...UCP document... }}
```

Business errors arrive inside `data.messages[]` (`{"type":"error","code":"product-not-found","severity":"recoverable"}`), tool failures as `isError: true` text blocks. `UcpClient` unwraps the envelope, so the backend sees the same document on both transports.

## Agent profile and required headers

`UCP-Agent: platform; profile="http://localhost/agent-profile.json"` on every call. Shopware fetches the profile **from inside the container**, so bootstrap copies `agent-profile.json` into the shop's `public/` and sets `SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1` so an http profile is accepted. Capabilities must be declared as **lists** and intersect with the shop's (`dev.ucp.shopping.catalog`, `.cart`, `.checkout`, `.order`, `.discount`, `dev.ucp.common.identity_linking`), otherwise `capabilities_incompatible`.

Writes send `Idempotency-Key` (UUID per call; REST header, MCP `_meta`). The Docker shop runs `signature-policy=strict` (bootstrap default; `UCP_SIGNATURE_POLICY=log` relaxes it for a shop without the agent key), so every UCP call from the host is signed.

### Request signing (RFC 9421 + RFC 9530)

`shopware_common/http_signing.py` — `RequestSigner` (ES256, `Content-Digest: sha-256=:…:`, `Signature-Input: sig=("@method" "@target-uri" "content-digest");created;expires;keyid;alg="ES256"`; lifetime 120 s). Configure with `UCP_AGENT_SIGNING_KEY_PEM_FILE` (+ `UCP_AGENT_SIGNING_KEY_ID`); the public JWK belongs in `agent-profile.json` → `signing_keys`. Unset → unsigned requests (fine under `log`). The replay verifies signatures with the test key, so the suite proves the canonical form.

## UCP REST paths (fallback)

| Operation | HTTP |
|---|---|
| catalog.search | `POST /ucp/v1/catalog/search` `{query, limit}` |
| catalog.lookup | `POST /ucp/v1/catalog/lookup` `{ids}` |
| catalog.product | `GET /ucp/v1/catalog/product/{id}` — **quirk:** for a family id this answers the family's *first child* (`catalog.lookup` and the MCP tool answer the family). The backend disambiguates with the Store API (`parentId == requested id` ⇒ family). |
| cart.create | `POST /ucp/v1/carts` |
| cart.get | `GET /ucp/v1/carts/{id}` — a Store API customer context token is not automatically an existing UCP cart id |
| cart.update | `PATCH /ucp/v1/carts/{id}` replace-all `line_items` |
| cart.cancel | `POST /ucp/v1/carts/{id}/cancel` |
| checkout.* | `/ucp/v1/checkout-sessions…` — not used for signed-in orders; Store API binds orders to the customer context |
| order.get | `GET /ucp/v1/orders/{id}` |
| discount.apply | REST route absent on 6.7.13; MCP-only |

## UCP MCP tools (live `tools/list`, 14 tools)

`*` = required. Mutating tools default to `dryRun=true` on the server; the host sends `dryRun=false` for real cart writes.

| Our method | Tool | Arguments |
|---|---|---|
| (context) | `shopware-store-api-context` | — |
| search_catalog | `search_catalog` | `query:string`, `limit:int=10` (no price filter → applied client-side) |
| lookup_catalog / get_product | `lookup_catalog` | `ids:string` (JSON array as string) |
| create_cart | `create_cart` | `payload:string` (JSON `{line_items}`), `dryRun` |
| get_cart | `get_cart` | `id*` |
| update_cart | `update_cart` | `id*`, `payload:string`, `dryRun` |
| cancel_cart | `cancel_cart` | `id*`, `dryRun` |
| apply_discount | `apply_discount` | `cartId*`, `code*`, `dryRun` |
| get_order | `get_order` | `id*` |
| never called | `create_checkout/get/update/cancel/complete` | checkout stays in Shopware |

## Cart and identity tokens

The application session id identifies the browser to this host. A guest UCP cart `id`
is a cart capability. Shopware account login returns a separate `sw-context-token`
that resolves to a customer in the Store API. The optional UCP OAuth Bearer token is
yet another credential; it is not a Store API context token or a cart id. On the live
local shop, calling UCP `get_cart` with a fresh logged-in customer's context token
returns `cart_not_found`. The host keeps account tokens server-side and uses Store API
cart and order routes for authenticated customers.

## Checkout handoff (ADR-10)

1. `GET /api/cart` gives the browser `checkout_url = {host}/api/checkout/handoff/{ticket}`. The ticket is a per-session random id; nothing is written to Shopware on GET.
2. Opening it mints a **one-time handoff code** (`shopware_common/handoff.py`): `v1.<payload>.<mac>` — payload = `jti`, `iat`, `exp` (≤ 120 s), AES-256-GCM box of the context token (nonce ∥ ciphertext ∥ tag, AAD = `jti`); MAC = HMAC-SHA256 over the payload. Keys are derived from `COMMERCE_AGENTS_HANDOFF_SECRET` (bootstrap generates it once and writes it to `docker/.generated.env` and the plugin config).
3. The host answers an HTML page that auto-submits `POST {shop}/claude-commerce/continue` with `code=…` (GET `?code=` only as `<noscript>` fallback; the raw token is never in a URL).
4. `CommerceAgentsHandoff` verifies MAC, expiry and single use (`jti` cache), refuses when a customer is already logged in, `$session->migrate()`, sets the context token, redirects to `/checkout/confirm`. Payment stays on Shopware.

The `checkout` card only shows this handoff. Explicit `place_order` requests use
`POST /store-api/checkout/order` with the verified customer's context token; the
agent does not call a payment endpoint.

## Identity Linking

`storefront/api/identity.py` verifies the customer with `POST /store-api/account/login`,
then reads `POST /store-api/account/customer` with the returned context token. The
verified customer id and default shipping address bind to the session. This works on
the plain-http local shop. When an HTTPS agent profile and signer are configured, the
host additionally performs Shopware's platform-to-shop UCP OAuth authorization code +
PKCE flow to obtain access/refresh tokens.

## Orders

`get_orders` / `get_order` require a verified customer session and read
`POST /store-api/order` with that customer's context token. Guest sessions cannot read
orders with a cart token alone. The seeded order history (`docker/seed_orders.py`)
belongs to the demo customer, so signed-in sessions see it.

## IDs and catalog

- Product / variant ids are 32-char hex UUIDs.
- UCP product documents are thin: no option matrix, no stock. The backend enriches from the Store API: `GET /store-api/product/{id}` (family → best child with `parentId`), `POST /store-api/product` filtered by `parentId` for the real children (incl. out of stock), `options.group`, `deliveryTime`, `unit`, `calculatedPrice.referencePrice` (PAngV base price).
- `variant_of` maps every child to its family; `add_to_cart` on a family id resolves the requested option to a child and refuses the family row.
- One out-of-stock variant raises `Unavailable` listing in-stock sibling ids.

## Store API surfaces used

| Need | Store API |
|---|---|
| Brand / locale / currency | `GET /store-api/context` |
| Variants, base price, delivery time | `GET /store-api/product/{id}`, `POST /store-api/product` (`parentId` filter) |
| Shipping methods, fees, ETA | `POST /store-api/shipping-method` (`prices[].currencyPrice`, `deliveryTime.min/max/unit`) |
| Policies | `GET /store-api/navigation/footer-navigation/footer-navigation`, `.../service-navigation/service-navigation` → `POST /store-api/category/{id}` → CMS `text` slots; plus `/agents.md`, `/llms.txt`; German fallback copy when the shop has none |
| Orders | `POST /store-api/order` with `sw-context-token` |

All errors surface as `StoreApiError` (status, body logged); product 404 → `None`.

## Merchant (ADR-12/13/14)

### Identity

Bootstrap creates a Shopware **Integration** plus ACL role `claude-merchant-agent` (read: `product`, `product_price`, `order`, `order_line_item`, `customer`, `promotion`, `sales_channel`, `currency`, `tax`, `category`; update/create where the staged writes need it: `product`, `promotion`, `promotion_discount`, `promotion_sales_channel`, `promotion_discount_rule`). The host authenticates with `client_credentials` (`SHOPWARE_INTEGRATION_ACCESS_KEY` / `SHOPWARE_INTEGRATION_SECRET_KEY`); the admin password grant is gone. The integration is also on the Admin MCP allowlist.

### Admin MCP tools (live `tools/list`, 20 tools)

| Used for | Tool | Arguments |
|---|---|---|
| Catalog / order / customer / promotion reads | `shopware-entity-search` | `entity*`, `criteria:string={}`, `limit=25`, `page=1`, `term` |
| Single entity | `shopware-entity-read` | `entity*`, `id*`, `criteria` |
| Snapshot, metrics, slow movers | `shopware-entity-aggregate` | `entity*`, `aggregations*` (JSON string), `filters` |
| Stage preview (`before`/`after`) | `shopware-entity-upsert` | `entity*`, `payload*` (JSON string), **`dryRun=true`** |
| Apply | `shopware-entity-upsert` | same payload, `dryRun=false` |
| Schema discovery (dev) | `shopware-entity-schema` | `entity*` |
| Refund preview / apply | `shopware-order-state` | `orderNumber`, `transactionAction=refund`, **`dryRun=true`** at stage, `dryRun=false` at apply |
| Not used | `merchant-*` (order-summary, customer-lookup, product-create, revenue-report, bestseller-report, storefront-search, cart-manage, cart-checkout, checkout-methods), `shopware-media-upload`, `shopware-entity-delete`, `shopware-system-config-*`, `shopware-theme-config` | out of the blueprint's `MerchantBackend` scope |

`/api/_mcp` uses the same OAuth bearer as the REST Admin API.

### Writes

| Change kind | Entity / fields |
|---|---|
| `listing_update` | `product`: `name`, `description`, `metaTitle`, `metaDescription` (whitelist at stage time) |
| `price_update` | `product.price`: read current, replace **only** the sales-channel currency entry, `net = gross / (1 + taxRate/100)` from the product's tax |
| `inventory_action` restock | `product.stock`: delta against **fresh** stock read at apply time |
| `inventory_action` pause / activate | `product.active` on the family **and every child** |
| `promotion` (ADR-13) | `promotion` (`active=true`, `useCodes=false`, `validFrom`/`validUntil`, `salesChannels`) + `promotion_discount` (`scope=cart`, `type=percentage`, `considerAdvancedRules=true`, product rule) — partial failure reports what was written |
| `campaign` | `ChangeNotApplicable` |
| refund (carried as `campaign` with a `payment_state` item, `merchant/api/refunds.py`) | the order's payment transaction: `refund` transition (`paid` / `paid_partially` / `refunded_partially` → `refunded`), state read back after apply; MCP transport only |

`apply_change` / `discard_change` refuse anything that is not `STAGED`. The ledger is SQLite (`MERCHANT_LEDGER_DSN`, default `merchant/data/ledger.sqlite`) and survives restarts.
