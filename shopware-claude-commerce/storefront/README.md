# Storefront shopping agent

FastAPI host implementing Anthropic's `StorefrontBackend` against a live Shopware UCP surface (MCP first, REST fallback, RFC 9421 signed), plus a Next.js grid / cart / assistant UI on the blueprint's `web-shared`.

```
storefront/api/
  ucp_client.py       UcpClient — MCP (UCP spec tools: create_cart, search_catalog, …) primary, /ucp/v1 REST fallback, signing, idempotency
  shopware_backend.py StorefrontBackend: catalog, variants, cart, handoff, order placement/history, policies, disclosures, fulfillment
  store_api.py        Store API client (typed StoreApiError): product/children, shipping methods, navigation, CMS, orders, context
  handoff.py          HandoffBroker — per-session ticket → one-time signed code → auto-POST page for the plugin
  identity.py         Shopware Store API account login; optional UCP OAuth linking when configured
  order_tool.py       explicit agent place_order tool, separate from checkout summary
  policies.py         PolicyIndex over CMS footer/service pages, /agents.md, /llms.txt, fallback copy
  disclosures.py      PAngV base price, delivery time, VAT rows from Store API data + fixed copy
  brand.py            shop name / locale / currency for the web UI
  catalog_warmup.py   grid products + live category/title notes at session start
  main.py             FastAPI host (routes below)
  tests/              netless replay of UCP over MCP + REST, Store API, OAuth AS; runs every test on both transports
storefront/data/      recorded UCP fixtures, discovery document, disclosure_copy.de.json
storefront/scripts/smoke.py   live checks over both transports
storefront/web/       Next.js UI on :3005 (see web/README.md)
```

## API

```bash
# from repo root, venv active; docker/.generated.env is read for every variable .env leaves empty
uvicorn storefront.api.main:app --port 8004
```

| Route | Purpose |
|---|---|
| `GET /api/health` | process check (no Anthropic key) |
| `POST /api/session` | start a shopping session; warm-up grid products enter provenance |
| `POST /api/session/focus` | storefront PDP navigation — `get_product_details` through the executor (H7), then an app-event note so chat knows the current product |
| `POST /api/session/sync-catalog` | customer-demo grid → session: listing search through the executor so visible titles enter provenance |
| `GET /api/products` | display cache (filled by search / warm-up) |
| `POST /api/cart/add` | direct add — runs `get_product_details` through the executor first, so the provenance gate holds |
| `POST /api/cart/attach` | adopt an existing Shopware context token as this session's cart |
| `GET /api/cart` | cart plus `checkout_url` (a ticket URL on this host; **no write to Shopware on GET**) |
| `GET /api/checkout/handoff/{ticket}` | mints a one-time handoff code (≤ 120 s, HMAC-signed, AES-GCM-boxed context token) and answers an HTML page that auto-submits `POST {shop}/claude-commerce/continue` |
| `GET /api/auth/shopware/start`, `POST /api/auth/shopware/login`, `GET /api/auth/status`, `POST /api/auth/signout` | Shopware Store API account login verifies credentials, reads the customer and default shipping address, and binds that customer to the session. OAuth linking is used as well when its HTTPS profile and signer are configured; local HTTP login works without it. Sign-out ends the linked session and starts a new guest session. |
| `GET /api/brand` | shop name, locale, currency, accent for the web UI |
| `GET /agent-profile.json` | the UCP agent profile (with the published signing key) |
| `POST /api/chat` | SSE turn; needs `ANTHROPIC_API_KEY` (identity-linked keys also `ANTHROPIC_WORKSPACE_ID`, sent as `anthropic-workspace-id` by `shopware_common/anthropic_client.py`) |

The agent uses `checkout` for a read-only summary card ("结算") and `place_order` for an explicit order request ("结账"/"下单"). Placing an order requires Shopware sign-in and a complete default shipping address. It submits the authenticated customer's Store API cart with that customer's `sw-context-token`, clears the cart, and reports "已下单". The agent does not process payment or report "已付款". Shopware's order database is the only source of order history; reads require the signed-in customer token.

Anonymous sessions receive a distinct server-generated memory key. The `user_id` supplied to `POST /api/session` cannot select a customer's data. After login, the server maps the verified Shopware customer id to an internal `shopware:<id>` key and checks the customer token on subsequent requests. The customer token is never returned as `cart_id` to the browser.

Before each agent turn, the backend supplies account context with the verified sign-in state, customer email, and cart/order scope, alongside the current cart. It never puts the Store API context token or UCP Bearer token in the prompt. Linked credentials are held in process memory; restarting the API requires the customer to sign in again. If a running API still answers login with an OAuth/HTTPS error, restart that process so it loads the current Store API login code.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `SHOPWARE_URL` | `http://localhost:8080` | sales-channel domain serving `/.well-known/ucp` |
| `SHOPWARE_SALES_CHANNEL_ACCESS_KEY` | from `docker/.generated.env` | Store API `sw-access-key` |
| `UCP_TRANSPORT` | `mcp` | `mcp` or `rest`; the other one is the fallback for transport-level failures |
| `UCP_AGENT_PROFILE_URL` | `http://localhost/agent-profile.json` | what the shop fetches (from inside the container) |
| `UCP_AGENT_SIGNING_KEY_PEM_FILE` | from `docker/.generated.env` | P-256 key for RFC 9421 + 9530 signatures (repo-root relative) |
| `COMMERCE_AGENTS_HANDOFF_SECRET` | from `docker/.generated.env` | shared with the `CommerceAgentsHandoff` plugin; unset disables the handoff (logged) |
| `STOREFRONT_API_PUBLIC_URL` | `http://localhost:8004` | how the browser reaches this host (ticket URL, OAuth redirect) |
| `SHOPWARE_UCP_OAUTH_CLIENT_ID`, `SHOPWARE_OAUTH_REDIRECT_URI` | profile URL / `{public}/api/auth/shopware/callback` | Optional UCP Identity Linking client id (must be https) and AS redirect URI; not required for Store API account login |
| `WEB_APP_URL`, `BRAND_TAGLINE`, `CATALOG_WARMUP` | `http://localhost:3005`, tagline, `1` | web UI wiring |
| `HOST_TIMEZONE` | `Europe/Berlin` | session clock when the request carries no `X-Timezone` header (the web app sends the browser's zone; `shopware_common/clock.py`) |

### Prompt rules this host adds

`storefront/api/agent_config.py` carries the host's own rules in `brand_voice` (the one hook
the pinned `ShoppingAgentConfig` exposes for prose rules): compare a date the customer names
against the conversation's local time and say so when it has passed, instead of promising
delivery by it (eval `shop-clock-002`). The clock itself comes from `shopware_common.clock`
on this repo's routes (`POST /api/cart/add`); the shared `/api/chat` route in the vendored
`demo_common` still passes the server's naive `datetime.now()` — see
`docs/anthropic-upstream-notes.md` §5.

`domain_search_notes` tells the model not to invent aisles (home goods, lamps, a generic
mall). On this host it also separates checkout preview from explicit order submission.
After warm-up it names live categories and titles from the Shopware catalog.
`search_products` uses UCP, then Store API search, then the same `POST /store-api/product`
listing as the grid, then the in-memory cache. `get_product_details` accepts a hex id or
an exact grid title ("Main product").

### Shopware facts the backend relies on

- A guest UCP cart id can serve as a cart context token. A logged-in customer's Store API `sw-context-token` is a separate credential: UCP `get_cart` does not accept it as an existing cart id. Authenticated carts and orders therefore use the Store API, while optional UCP OAuth Bearer tokens remain separate. Customer tokens are kept server-side.
- MCP tool results are wrapped in `{"success", "dryRun", "data"}`; the client unwraps them so both transports yield the same document.
- `GET /ucp/v1/catalog/product/{family}` and `GET /store-api/product/{family}` both answer a *child* (the Store API child carries `parentId == family`); the backend uses that to tell families from variants. Real children (including out of stock) come from `POST /store-api/product` filtered by `parentId`.
- A guest cart is not an order identity. Order queries require a verified Shopware customer session.

## Web

```bash
npm install
npm run dev:storefront
```

Opens http://localhost:3005 against `NEXT_PUBLIC_API_URL` (default http://localhost:8004). "Checkout in Shopware" opens the ticket URL in the same tab, which lands on Shopware's confirm page with the same cart. Details in [`web/README.md`](web/README.md).

For the local Docker shop, `.venv/bin/python docker/seed_buyers.py` creates and verifies two test customers: `admin@shop.com` / `admin` and `ruoyu@shop.com` / `ruoyu`. This local-only seed handles Shopware's registration password-length rule for these two requested fixtures without changing the store-wide rule.

## Tests and smoke

```bash
pytest storefront/api/tests                       # netless; every backend test runs over MCP and over REST
python storefront/scripts/smoke.py                # live: both transports, signed when the key is configured
python storefront/scripts/smoke.py --transport mcp --no-fallback
```

`smoke.py` runs discovery, `tools/list`, search, details with variants, add / update / remove on a real cart, fulfillment options, policy search, orders behind the cart token, and the handoff round trip (mint → POST to the plugin → 302 to `/checkout/confirm`; replay refused).
