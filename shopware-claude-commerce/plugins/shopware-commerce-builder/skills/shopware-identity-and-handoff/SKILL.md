---
name: shopware-identity-and-handoff
description: Who each host is to Shopware and how the customer gets from the agent's cart to Shopware's checkout, covering the merchant Integration and least-privilege ACL role, the sales-channel access key, the agent's signing key and published profile, UCP Identity Linking (OAuth authorization code with PKCE, https client_id), the signed one-time handoff code into the Twig checkout, and the rule that the agent never places an order. Load when wiring credentials, sessions, sign-in, or checkout for a Shopware commerce agent, or when a token or a customer id is about to appear where it should not.
---

# Identity and the checkout handoff

Paths are in the Shopware reference repo: `storefront/api/handoff.py` and `identity.py`,
`shopware_common/handoff.py` and `http_signing.py`, `merchant/api/agent_config.py` and
`admin_client.py`, `docker/merchant_identity.py`, `docker/agent_key.py`,
`docker/plugins/CommerceAgentsHandoff/`. The blueprint's identity rules (the principal bound at
session start, no user or operator in a tool argument, `checkout` charges nothing) are
Anthropic's `commerce-trust-safety` skill, rules 7, 12, and 13; this skill says how Shopware
supplies each principal.

## Four principals

| Who | Credential | Where it lives | Scope |
|---|---|---|---|
| Shopper (guest) | the Store API `sw-context-token` that is the UCP cart id | the storefront host's session state | one cart; orders placed with it |
| Shopper (linked) | UCP Identity Linking access and refresh tokens, plus the customer's context token | the host's `ShopwareIdentityLinking` map, keyed by session | `dev.ucp.shopping.cart:manage`, `dev.ucp.shopping.order:read` |
| Merchant host | a Shopware **Integration** (`SHOPWARE_INTEGRATION_ACCESS_KEY`, `SHOPWARE_INTEGRATION_SECRET_KEY`, OAuth `client_credentials`) bound to the ACL role `claude-merchant-agent`, with an Admin MCP allowlist | `.env` or `docker/.generated.env`; the bearer in `OAuthTokenProvider` | exactly the reads and writes the backend makes |
| Agent toward UCP | a P-256 signing key (`UCP_AGENT_SIGNING_KEY_PEM_FILE`) whose JWK is the one entry in the published `agent-profile.json` | `secrets/` locally; the profile where the shop can fetch it | RFC 9421 signatures on every UCP request |

The sales-channel access key (`SHOPWARE_SALES_CHANNEL_ACCESS_KEY`) is a fifth value: it scopes
Store API reads to the channel and is public by design, but it still stays on the host. The admin
user's password is used by the bootstrap scripts once and by no host; `load_settings` refuses it.

## Least privilege for the merchant

- `docker/merchant_identity.py` creates the role with `ACL_PRIVILEGES` and the Integration bound
  to it, sets the Admin MCP allowlist to the six `shopware-entity-*` tools, and verifies that a
  `dryRun` upsert on `product` passes while a search on `user` is refused with
  `Missing privilege: user:read`. The role holds no `system_config:*`, no `order:update`, no
  `user:*`, no `media:*` writes; a new write kind adds its privilege and nothing else.
- The Integration's secret is known at creation only; a re-run without it in
  `docker/.generated.env` rotates it. A leaked secret is rotated the same way and the old one
  stops working at once.
- `MerchantSessionContext.operator` is `MERCHANT_OPERATOR`, stamped on every staged, applied, and
  discarded change; an admin-module surface later supplies the signed-in user from
  `/api/_info/me`. The approval mark comes from the portal route, never from chat text.

## The shopper: guest, then linked

- A session starts as a guest. The first cart write creates the UCP cart with the channel's
  `context`; the returned id is the context token, held in `_SessionState`, and every later cart
  call carries it as the cart id. `get_orders` behind that token answers the orders the cart
  placed, or an empty list (`403 CHECKOUT__CUSTOMER_NOT_LOGGED_IN` is not an error).
- Identity Linking is Shopware's platform-to-shop OAuth 2.0 authorization code flow with PKCE
  S256 (`/.well-known/oauth-authorization-server`): the customer signs in through
  `POST /store-api/account/login` on the host's `/api/auth/shopware/login`, the host calls
  `GET /ucp/v1/oauth/authorize` with that customer's `sw-context-token`, signed per RFC 9421, and
  receives the `code` in the JSON body (no browser hop), then `POST /ucp/v1/oauth/token`
  (`token_endpoint_auth_methods_supported: ["none"]`) for the tokens. The `client_id` is the
  agent profile URL and **must be `https`**; without that, or without a signing key,
  `unavailable_reason` says why and `GET /api/auth/status` reports it, `GET /api/auth/shopware/start`
  answers `503`, and sessions stay guests.
- When linked, the customer's context token becomes the session's cart and `get_orders` reads the
  customer's history; `POST /api/auth/signout` drops the link. Tokens refresh before expiry and are
  never logged; a `401` from the shop drops the link (`on_auth_failure`) rather than retrying with
  a stale token.
- A guest who signs in starts over as that customer; memory keyed by the guest principal does not
  follow, which is the blueprint's rule.

## The handoff

The agent builds a Shopware cart, and the customer must finish in Shopware's checkout with that
cart. The browser cannot set `sw-context-token` on the shop's origin, and the token must not travel
in a URL, so:

1. `GET /api/cart` gives the browser `checkout_url`, a ticket URL on the host
   (`/api/checkout/handoff/{ticket}`); the ticket is random, bound to one session, revoked when
   the cart binding changes; nothing is written to Shopware on the read.
2. Opening it mints a **one-time handoff code** (`HandoffCodeIssuer`): `v1.<payload>.<mac>`, the
   payload holding `jti`, `iat`, `exp` (at most 120 seconds) and an AES-256-GCM box of the
   context token with `jti` as associated data, the MAC an HMAC-SHA256 over the payload; both keys
   derived from `COMMERCE_AGENTS_HANDOFF_SECRET` (at least 32 bytes) by HMAC-SHA256 over a fixed
   label each. The host answers an
   HTML page that auto-submits `POST {shop}/claude-commerce/continue` with `code=`; a `<noscript>`
   link does the same with the code only.
3. `CommerceAgentsHandoff` verifies the MAC, the expiry with a bounded clock skew, and single use
   (a `jti` cache), refuses when a customer is already logged in on that browser session (the
   agent's cart never replaces a signed-in customer's), migrates the session, adopts the token, and
   redirects to `/checkout/confirm`. A refusal redirects to the cart with a flash message.
   Payment, the order, and the legal confirmation happen in Shopware.
4. `complete_checkout` has no code path in the host; no UCP `checkout.*` tool is called; no
   `place_order`, `charge`, or order-state write exists in either backend. The model can render the
   cart and hand off, and nothing else.

The Python issuer and verifier in `shopware_common/handoff.py` are the reference the PHP verifier
mirrors; both test suites pin the wire format, so a change to one is a change to both.

## Where a value may and may not appear

| Value | May appear in | Never in |
|---|---|---|
| context token, UCP cart id | the host's session state, the `Mcp-Session-Id`-scoped calls, the encrypted handoff box | a tool result, a URL, a log line, the model's context |
| Identity Linking tokens | the host's memory | anything the browser or the model sees |
| Integration keys, bearer | env, `OAuthTokenProvider` | a tool result, a log line, `CLAUDE.md` |
| signing key PEM | `secrets/`, the file named by env | the repository, the profile (the JWK is public, the PEM is not) |
| handoff code | one POST body, once | a GET URL by default, a log line, a second request |
| handoff secret | env and the plugin config | anywhere else |
| customer email, order number | the fenced order result the customer asked for | a tool argument, a memory fact (the write filter refuses identifier-shaped values) |

`session_id` is the only thing a request carries after `POST /api/session`; the routes read the
principal from the session record.

## Do not

- Put the admin user's password grant in a host, or widen the ACL role for a read the backend
  does not make.
- Place an order, complete a UCP checkout, or build a URL that carries the context token.
- Accept a handoff code over GET as the default path, reuse one, or let its lifetime exceed 120 s.
- Treat a customer's `sw-context-token`, email, or number as a tool argument the model can supply.
- Run Identity Linking with an `http` profile or an unsigned authorize request; say it is
  unavailable and why.
