---
name: shopware-ucp-mapping
description: How Shopware's UCP surface (discovery, the MCP and REST transports, the fourteen UCP tools) maps onto the blueprint's StorefrontBackend, covering ids, families and variants, cart replace semantics and the cart id as context token, the checkout handoff, request signing and idempotency, and what the Store API fills in. Load when implementing or reviewing a shopping agent's catalog, cart, or checkout against a Shopware shop.
---

# UCP to StorefrontBackend

Paths are in the Shopware reference repo (`agentic-commerce-lab/shopware-claude-commerce`): `storefront/api/`
holds the host, `shopware_common/` the transport code, `docs/shopware-mapping.md` the tables read
from the live shop. The blueprint contract is `StorefrontBackend` in Anthropic's
`shopping-agent/core/shopping_agent/backend.py`, installed at the commit `requirements.txt` pins.

## Discovery and the two transports

- `GET /.well-known/ucp` on the sales-channel domain is the profile: protocol version (`2026-08-25`
  on the pinned `SwagAgenticCommerce`), capabilities, transports, service URLs, and the shop's
  active signing keys. `UcpClient.discover` reads it; the smoke script and the doctor check it,
  and the fixture is `storefront/data/discovery.json`. The backend's calls do not depend on it,
  because the tool names and paths are pinned from the live `tools/list`.
- Two transports carry one document model. **MCP** (`POST /ucp/mcp`, Streamable HTTP, default
  `UCP_TRANSPORT=mcp`) is primary on lanes with the Store API MCP infrastructure (6.7.11 and
  later); **REST** (`/ucp/v1/*`) is the fallback and the only transport on 6.6 and 6.5.
  `UcpClient` (`storefront/api/ucp_client.py`) names each operation once (`MCP_TOOLS`,
  `rest_request`, `mcp_arguments`) and unwraps both envelopes to the same UCP document.
- Fallback is one-directional per call and only for a transport failure (connection refused, `404`
  or `405` without a UCP document, a session that does not recover). A UCP error document
  (`ucp.status=error`, or an entry in `data.messages[]` such as `product-not-found`) is a business
  error and never a fallback. `fallback=False` pins one transport, which the smoke script uses.
- MCP session mechanics (`McpClient` in `shopware_common/mcp_client.py`): `initialize` with
  `protocolVersion` `2025-06-18` returns `Mcp-Session-Id`; `notifications/initialized` answers
  `202`; `tools/list` and `tools/call` carry the session id and `MCP-Protocol-Version`; a `400` or
  `404` naming the session triggers one re-initialize and retry; `DELETE` ends the session. Bodies
  come back as JSON or as one SSE `message` event, both handled. The `tools/call` result wraps the
  document as `{"success", "dryRun", "data"}`; tool failures are `isError` text blocks.
- Every `/ucp/mcp` request, `initialize` included, carries `UCP-Agent: platform;
  profile="<UCP_AGENT_PROFILE_URL>"`; without it the shop answers `422`. Mutating UCP tools default
  to `dryRun=true` on the server; the host sends `dryRun=false` for real cart writes
  (`MUTATING_OPERATIONS`).

## The tools and the methods

| Backend method | UCP operation | MCP tool (live `tools/list`) | REST |
|---|---|---|---|
| `search_products` | `catalog.search` | `search_catalog` (`query`, `limit`; no price filter, applied host-side) | `POST /ucp/v1/catalog/search` |
| `get_product_details` | `catalog.product`, then `catalog.lookup` when the answered id differs from the requested one | `lookup_catalog` (`ids` as a JSON array string) serves both | `GET /ucp/v1/catalog/product/{id}` answers a family's first child while `POST /ucp/v1/catalog/lookup` answers the family; the Store API's `parentId` decides which side was asked for |
| `get_cart` | `cart.get` | `get_cart` | `GET /ucp/v1/carts/{id}`; an unknown id answers an empty cart with that id, not `404` |
| `add_to_cart` (first line) | `cart.create` | `create_cart` (`payload` JSON string) | `POST /ucp/v1/carts` |
| `add_to_cart`, `update_cart_item`, `remove_from_cart` | `cart.update` | `update_cart` | `PATCH /ucp/v1/carts/{id}` |
| (drop) | `cart.cancel` | `cancel_cart` | `POST /ucp/v1/carts/{id}/cancel` |
| (promotion code) | `discount.apply` | `apply_discount` (`cartId`, `code`) | absent on 6.7.13 |
| `get_order` (linked) | `order.get` | `get_order` | `GET /ucp/v1/orders/{id}` |
| never called | `checkout.*` | `create_checkout/get/update/cancel/complete` | `/ucp/v1/checkout-sessions` |

`shopware-store-api-context` is the context tool; the backend reads `GET /store-api/context`
instead. Discount, loyalty, fulfillment, and buyer-consent extensions exist in the plugin; the
reference uses none of them yet.

## Ids, families, and the Store API gaps

- Product and variant ids are Shopware's 32-character hex UUIDs, passed through as the blueprint's
  opaque strings. A family (a parent with children) and each child are distinct UUIDs; no prefix.
- UCP product documents are thin: no option matrix, no stock. `get_product_details` enriches from
  the Store API: `GET /store-api/product/{id}` (the family, or a child with `parentId`), then
  `POST /store-api/product` filtered by `parentId` for every child including the sold-out ones,
  with `options.group`, `deliveryTime`, `unit`, and `calculatedPrice.referencePrice`. The mapping to
  `ProductDetails.options` and `variants[].option_values` and `variant_of` is the shopware-variants
  skill. `_store_api_to_ucp` in `shopware_backend.py` shapes a Store API row like a UCP document so
  one mapper serves both sources.
- `search_policies` (footer and service navigation to CMS pages, `/agents.md`, `/llms.txt`),
  `get_disclosure` (`referencePrice`, `deliveryTime`, stock), `get_fulfillment_options`
  (`POST /store-api/shipping-method`: `prices[].currencyPrice`, `deliveryTime`), and `get_orders`
  (`POST /store-api/order` behind the cart's context token) are Store API; UCP has no capability for
  them. Every failed Store API call is logged with path and detail and raised as `StoreApiError`;
  a `404` on a single product is `None`.
- HTML is stripped from product and CMS text before it enters the blueprint's fence.

## Cart semantics

- The UCP cart `id` is the Store API `sw-context-token`. One cart per agent session, created on the
  first add with the `context` (`address_country`, `language`) and held in the host's session
  state; the token never reaches the model, a log line, or a URL.
- `cart.update` replaces the whole `line_items` list. Every write re-reads the cart
  (`_refresh_lines`), rebuilds the full list with the one line changed, and sends it; quantity `0`
  removes the line. Two writes in one round are serialized by the blueprint's session lock.
- A family id on a write is resolved to a child (the requested option, else the in-stock default;
  the family row itself is refused) and availability is checked before the write; a sold-out child
  raises `Unavailable` naming in-stock siblings (shopware-variants).
- A cart the shop no longer knows (`cart_not_found`, `invalid_cart_id`, or the REST empty-cart
  answer for an id the host did not create) is dropped and recreated on the next add; the
  handoff ticket is revoked with it.
- `POST /api/cart/attach` binds a session to an existing Shopware cart id the browser holds, for
  a storefront that starts the conversation with a cart.

## Checkout

`checkout` renders the cart; `checkout_handoff` returns a label and a ticket URL on the host, and
nothing is written to Shopware on a read. The ticket becomes a one-time signed code that the
`CommerceAgentsHandoff` plugin turns into the customer's storefront session and a redirect to
`/checkout/confirm`; payment stays in Shopware. `complete_checkout` has no code path. The
mechanics and the identity rules are the shopware-identity-and-handoff skill.

## Signing and idempotency

- Writes send `Idempotency-Key` (a UUID per call: a REST header, the MCP `_meta`), and the channel
  runs `idempotencyRequired`.
- Requests are signed per RFC 9421 with an RFC 9530 `Content-Digest` (`RequestSigner` in
  `shopware_common/http_signing.py`): ES256, covered components `"@method" "@target-uri"
  "content-digest"`, parameters `created`, `expires` (120 seconds), `keyid`, `alg`; the signature
  is DER-encoded ECDSA because the PHP verifier calls `openssl_verify`. The key is
  `UCP_AGENT_SIGNING_KEY_PEM_FILE`; its JWK is the one `signing_keys` entry of `agent-profile.json`,
  which the shop fetches at `UCP_AGENT_PROFILE_URL` from its own network. Unset, requests go
  unsigned, which `signature-policy=log` accepts and `strict` refuses.
- The signature covers the raw JSON-RPC body, so one signer serves both transports; the netless
  replay verifies signatures with the test key, which pins the canonical form.

## Do not

- Send a delta to `cart.update`, or a family id to any cart write.
- Put the context token, a checkout URL carrying it, or a bearer in a tool result, a log line, or
  a URL.
- Treat a UCP error document as a reason to switch transports.
- Call a `checkout.*` tool or create a UCP checkout session on a read.
- Author a price, a stock figure, or a delivery time in the host; every one comes from a UCP
  document or a Store API field.
