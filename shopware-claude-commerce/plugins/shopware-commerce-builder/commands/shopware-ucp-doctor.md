---
description: "Diagnose a Shopware shop's agentic surface for the commerce agents: UCP discovery, the shop's signing keys and the agent's key and profile (fetched from inside the container), the allowlists and signature policy, the MCP handshake on /ucp/mcp and /api/_mcp, the Admin MCP allowlist and ACL, the Store API access key, the checkout handoff round trip, and Identity Linking availability. Use when a host cannot reach the shop, a request is refused, a cart or preview does not appear, or after a bootstrap, an upgrade, or a container recreate."
argument-hint: "[shop URL or symptom, if known]"
---

Diagnose the Shopware surface the user's commerce agents run against. The user said:

$ARGUMENTS

Read the `## Shopware commerce agent decision record` section of the project's `CLAUDE.md` for the
shop URL, sales channel, lane, transports, and signing posture; without it, read `.env` and
`docker/.generated.env` for the same (values are read, never printed). Run every check below in
order, print one line per check (`ok`, `warn`, `fail`, with the fact found), stop after the table
with the fixes, and write the findings to the record under a `### Doctor <date>` line. The Docker
container is `commerce-agents-shopware` unless the record says otherwise; on a shop the user does
not run, skip the container checks and say so.

## Checks

**1. Reachability and domain.** `GET {SHOPWARE_URL}/api/_info/version` answers `401` without a
token, which proves PHP and Shopware are up; `GET {SHOPWARE_URL}/` answering `400` means the
sales-channel domain does not match the published port (the bootstrap rewrites
`sales_channel_domain` and `APP_URL`). The browser must reach the same URL the host uses, because
the handoff posts the browser there.

**2. Discovery.** `GET {SHOPWARE_URL}/.well-known/ucp`: a JSON profile with the protocol version
(`2026-08-25` on the pinned plugin), capabilities containing `dev.ucp.shopping.catalog`, `.cart`,
`.checkout`, `.order`, `.discount`, and `dev.ucp.common.identity_linking`, the transports (`rest`,
`mcp` on 6.7.11 and later, `embedded`), and `signing_keys` with exactly one active key. `404`
means `SwagAgenticCommerce` is not active or UCP is not enabled on this channel
(`docker/enable_ucp.py`; `ucp:channels` and `ucp:config:show --sales-channel=<name>` inside the
container). Two active keys means a bootstrap ran against a recreated container without the
cleanup; `ensure_single_signing_key` in `enable_ucp.py` retires and deletes surplus keys.

**3. Channel config.** Inside the container, `ucp:config:show --sales-channel=<name>` and
`ucp:config:validate`: `signaturePolicy` (`strict` in production, `log` accepts unsigned
requests), `idempotencyRequired`, and the three allowlists (`platformAllowlist`,
`remoteProfileAllowlist`, `agentAllowlist`) containing the host the agent profile is served from
(`localhost` and `127.0.0.1` on the Docker lane). A `capabilities_incompatible` or an allowlist
refusal in the shop's `mcp` or `ucp` log channel points here. Compare with `desired_config` in
`docker/enable_ucp.py`.

**4. Agent profile and key.** The shop fetches `UCP_AGENT_PROFILE_URL` from its own network:
`docker exec commerce-agents-shopware curl -fsS http://localhost/agent-profile.json` must answer
the profile; from the host machine the same file is `GET {STOREFRONT_API_PUBLIC_URL}/agent-profile.json`.
The profile declares capabilities as lists that intersect the shop's, and `signing_keys` holds one
JWK that equals the PEM's: `python docker/agent_key.py check --pem <UCP_AGENT_SIGNING_KEY_PEM_FILE>
--profile agent-profile.json` exits 0. An `http` profile URL is accepted only with
`SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1` in the shop's `.env`, which is
local-only; a public shop needs `https`. After a profile change, purge the cache
(`purge_profile_cache` in `enable_ucp.py`: `DELETE /api/_admin/ucp/platform-profiles/{id}`),
otherwise the shop keeps the old keys.

**5. UCP over MCP.** `POST {SHOPWARE_URL}/ucp/mcp` with `initialize` (`protocolVersion`
`2025-06-18`) and the `UCP-Agent: platform; profile="<UCP_AGENT_PROFILE_URL>"` header answers with
`Mcp-Session-Id`; `notifications/initialized` answers `202`; `tools/list` with the session id lists
the fourteen `shopware-ucp-*` tools plus `shopware-store-api-context`. `422 $.headers.ucp-agent
is required` means the header is missing on `initialize`; `404` or `405` means the lane has no
`/ucp/mcp` (6.6, or 6.7.11 to 6.7.13 without `MCP_SERVER=1`), and `UCP_TRANSPORT=rest` is the
answer; a `401` naming the signature means the policy is `strict` and the request was unsigned
or signed with a key the fetched profile does not carry (check 4). `python storefront/scripts/smoke.py
--transport mcp --no-fallback` runs the signed handshake, search, details with variants, cart, and
fulfillment end to end; `--transport rest` does the same over `/ucp/v1/*`.

**6. Store API.** `GET {SHOPWARE_URL}/store-api/context` with `sw-access-key:
{SHOPWARE_SALES_CHANNEL_ACCESS_KEY}` answers the sales-channel context (currency, language);
`401` means the key is not this channel's. `POST /store-api/shipping-method` lists methods with
`prices` and `deliveryTime`; the footer and service navigation answer the CMS categories the
policy index reads. `GET {SHOPWARE_URL}/agents.md` and `/llms.txt` are optional sources.

**7. Admin MCP and identity.** `POST {SHOPWARE_ADMIN_URL}/api/oauth/token` with
`grant_type=client_credentials` and the Integration keys answers a token; `POST /api/_mcp` with
that bearer and `initialize` answers a session; `tools/list` equals the allowlist in
`MCP_TOOL_ALLOWLIST` (`docker/merchant_identity.py`: the six `shopware-entity-*` tools). A
`shopware-entity-upsert` on `product` with `dryRun: true` succeeds; a `shopware-entity-search` on
`user` is refused with `Missing privilege: user:read`, which proves the ACL role is in force.
`verify_mcp` in `merchant_identity.py` runs exactly this, and `python merchant/scripts/smoke_live.py
--read-only` runs the backend's reads on top. Extra tools in `tools/list` mean the allowlist was
not set or the Integration is an admin; a missing tool means the allowlist is stale after a rename.
`404` on `/api/_mcp` means the lane has no Admin MCP, and `SHOPWARE_ADMIN_TRANSPORT=rest` is the
answer, without server previews.

**8. Handoff.** The `CommerceAgentsHandoff` plugin is active (`plugin:list`), its configured
secret equals `COMMERCE_AGENTS_HANDOFF_SECRET` (at least 32 bytes), and a round trip works: with the
storefront host running, `POST /api/session`, add one item, `GET /api/cart` carries `checkout_url`,
and opening it answers a page that posts a `v1.` code to `{SHOPWARE_URL}/claude-commerce/continue`,
which redirects to `/checkout/confirm` with the same lines. Replaying the code is refused
(`jti` cache), so a second open of the same ticket mints a new code. `checkout_url` absent from the
cart means the secret is unset on the host; a redirect to `/checkout/cart` with a flash message
instead of `/checkout/confirm` means the secrets differ, the code expired (120 seconds), the code
was already used, or a customer is already logged in on that browser session.
`storefront/scripts/smoke.py` includes the round trip.

**9. Identity Linking.** With the storefront host running, `GET /api/auth/status` says whether
linking is available and, when not, why: an `http` agent-profile `client_id` (Shopware requires
`https`), no signing key, or no `SHOPWARE_UCP_OAUTH_CLIENT_ID`. Guest mode is unaffected. When
available, `GET /.well-known/oauth-authorization-server` on the shop lists `/ucp/v1/oauth/authorize`
and `/ucp/v1/oauth/token` with `token_endpoint_auth_methods_supported: ["none"]`.

**10. Hosts.** `GET {STOREFRONT_API_PUBLIC_URL}/api/health` and `GET :8005/api/merchant/health`;
a second chat turn shows cache reads in `turn_complete.usage`. `docker/verify.sh`, when the
checkout has it, runs the shop-side subset of these checks in one go.

## Symptoms

| Symptom | Cause | Fix |
|---|---|---|
| `422 $.headers.ucp-agent is required` on `initialize` | `UCP-Agent` header missing on the MCP handshake | every `/ucp/mcp` request carries it, `initialize` included (`McpClient` headers) |
| `capabilities_incompatible` | the profile declares capabilities as strings, or a set disjoint from the shop's | lists, intersecting check 2's set; purge the profile cache |
| `401` with a signature error | policy `strict`, request unsigned or `keyid` unknown | `UCP_AGENT_SIGNING_KEY_PEM_FILE` set; JWK in the profile equals the PEM (check 4); cache purged |
| profile fetch refused, `http` scheme | development mode off | `https` profile in production; the flag only on the Docker lane |
| `404 session not found` mid-conversation | MCP session expired | `McpClient` re-initializes once and retries; a persistent one is a proxy dropping `Mcp-Session-Id` |
| `GET /ucp/v1/carts/{id}` returns an empty cart for an unknown id | UCP REST semantics, not an error | the backend treats a cart it did not create as gone (`UcpCartGoneError` path) |
| `catalog.product` answers a child for a family id over REST | REST quirk; MCP and `catalog.lookup` answer the family | the backend disambiguates with Store API `parentId` (shopware-variants) |
| `403 CHECKOUT__CUSTOMER_NOT_LOGGED_IN` on `POST /store-api/order` | a guest cart that never ordered | an empty order list, not an error; Identity Linking for history |
| two active shop signing keys | bootstrap re-run without cleanup | `ensure_single_signing_key`; discovery lists one |
| `tools/list` on `/api/_mcp` shows twenty tools | allowlist not set on the Integration | `set_allowlist` in `merchant_identity.py`; the host needs six |
| `Missing privilege: product:update` on apply | ACL role lacks the write | `ACL_PRIVILEGES` in `merchant_identity.py`; re-run `ensure_acl_role` |
| `checkout_url` missing from the cart payload | handoff secret unset on the host | `COMMERCE_AGENTS_HANDOFF_SECRET` from `docker/.generated.env` |
| `/checkout/confirm` shows an empty cart after the handoff | secrets differ, code expired, or the plugin is inactive | check 8; the plugin verifies MAC, expiry, single use |
| `/` answers `400` | `sales_channel_domain` does not match the URL | the bootstrap's domain rewrite; `APP_URL` in the shop's `.env` |
| Store API `401` | access key belongs to another channel | `SHOPWARE_SALES_CHANNEL_ACCESS_KEY` from `write_credentials.py` |

## Report

Print the ten checks as a table, the symptom rows that matched with their fixes, and the next
command to run (`./docker/bootstrap.sh` after a container recreate; `docker/enable_ucp.py` or
`merchant_identity.py` for one surface; `/scaffold-shopware-agent` when the record is missing).
Append the date, the failing checks, and the fixes applied to the decision record. Never print a
key, a token, a secret, or a context token; print their variable names and whether they are set.
