---
description: "Map a Shopware shopping or merchant agent the user already runs or is building, compare it with the Shopware reference row by row (transport, discovery and signing, identity, catalog and variants, cart, checkout, policies and disclosures, merchant reads, staged writes, approval, evals), and convert the rows the user picks. Use when an existing Shopware agent integration is to be reviewed, or its safety, compliance, or preview loop brought in line, rather than a new one started."
argument-hint: "[where the agent's code is, and what prompted the review, if known]"
---

Review the user's Shopware commerce agent against the `agentic-commerce-lab/shopware-claude-commerce`
reference. The user said:

$ARGUMENTS

Steps 1 to 3 map the agent, write the decision record, and produce the conversion table; a review
alone ends there. Step 4 converts the rows the user picks. Without an agent to read, stop and
suggest `/scaffold-shopware-agent`. The blueprint's own review (loop, rules, request assembly,
fencing, provenance, UI, sessions) is Anthropic's `/review-commerce-agent`; this command covers
what touches Shopware.

## Step 1: Map the agent

The reference is the current repo when `storefront/api/shopware_backend.py` exists, else a clone;
the agent is the path in $ARGUMENTS, else the code that talks to the shop. Fill in one line per row
below with the file and function that shows it; comparing is Step 3.

| Row | What to find |
|---|---|
| Transport | How catalog and cart reach the shop: UCP over MCP, UCP REST, the Store API directly, the Admin API, or scraping; what happens when the primary fails; whether a business error (a UCP `messages[]` entry) is told apart from a transport error |
| Discovery and signing | Whether `/.well-known/ucp` is read, and what of it is trusted (protocol version, capabilities, signing keys); whether requests carry `UCP-Agent`, `Idempotency-Key` on writes, and an RFC 9421 signature; where the agent's key lives and who publishes its profile |
| Identity | Who the shop thinks is calling: an admin user's password grant, an Integration with an ACL role, a sales-channel access key; whether a token, a context token, or a customer id is ever a tool argument, a log line, or a URL |
| Catalog and variants | Whether a parent with `childCount > 0` is presented as a family with `options` and `variants`, or as one product; which id a cart write receives; what an out-of-stock child produces |
| Cart | Whose cart it is (a context token per session, or one shared cart); whether writes send the whole line list or a delta; what a `cart_not_found` does; whether a cart id appears anywhere the model or the browser can see it |
| Checkout | Who completes it: the agent (a UCP checkout `complete`, a Store API order), a URL carrying the token, a signed one-time handoff, or the cart rendered only; whether the code path that places an order exists at all |
| Policies and disclosures | Where terms come from (the shop's CMS pages, `/agents.md`, a pasted text, the model); whether the base price, delivery time, VAT wording, and shipping hint are server rows from Store API fields or model text; what a Widerruf question answers from |
| Merchant reads | Whether snapshot and metrics come from aggregations with cancelled orders excluded, or from a model's arithmetic over rows; what is reported for figures Shopware lacks |
| Staged writes | Whether a merchant write goes live on the model's say-so; whether `dryRun=true` is used as the preview and stored with the change; whether the price payload replaces only the sales-channel currency entry with net from the tax rate; which listing fields may be written; how a promotion is composed |
| Approval | Who marks a change approved and how the code knows; whether text in the chat can apply; what happens to a partial write failure |
| Evals | What runs before a change ships against the shop; whether a base-price row or a delivery time is ever compared with the server's record |

Present the map in chat before going on.

## Step 2: Write the record

Write the map into the project's `CLAUDE.md` under `## Shopware commerce agent decision record`,
with the fields `/scaffold-shopware-agent` writes: shop URL and sales channel, lane and
transports, signing posture, role(s) and layout, checkout mechanism, identity mode, approval
surface and `MERCHANT_REQUIRE_HOST_APPROVAL`, change kinds and limitations, where credentials live.
`/add-shopware-flow`, `/author-shopware-evals`, and `/shopware-ucp-doctor` read it. Record the
auth mechanism, never a credential.

## Step 3: The conversion table

One line per map row, from the table below plus what the change touches in this agent; a row that
already matches says so. The patterns:

| Found | Reference | Skill; module |
|---|---|---|
| Store API or scraping for catalog and cart; no fallback | UCP over MCP with REST fallback, one document model on both; a UCP error document is a business error and never a fallback | shopware-ucp-mapping; `UcpClient` in `storefront/api/ucp_client.py`, `McpClient` in `shopware_common/mcp_client.py` |
| Discovery unread; unsigned requests; no idempotency key | Discovery read for keys and capabilities; `UCP-Agent` on every call; `Idempotency-Key` on writes; RFC 9421 with RFC 9530 digest from a P-256 key whose JWK is the profile's one entry | shopware-identity-and-handoff; `RequestSigner` in `shopware_common/http_signing.py`, `docker/agent_key.py`, `agent-profile.json` |
| Admin password grant; a token in a tool argument or a URL | An Integration with a least-privilege ACL role and an Admin MCP allowlist; tokens held by the host only | shopware-identity-and-handoff; `docker/merchant_identity.py`, `OAuthTokenProvider` in `merchant/api/admin_client.py`, `load_settings` in `merchant/api/agent_config.py` |
| One product per row; a family id written to the cart | Family with `options` and `variants` from Store API `parentId`; cart writes take a child; a sold-out child raises `Unavailable` naming in-stock siblings | shopware-variants; `get_product_details` and `add_to_cart` in `storefront/api/shopware_backend.py`, `StoreApiClient` in `store_api.py` |
| A delta sent to the cart; a shared cart; the cart id in a URL | Whole line list on every write (UCP `cart.update` replaces); one cart per session, its id the context token, held by the host | shopware-ucp-mapping; the cart methods and `_SessionState` in `storefront/api/shopware_backend.py` |
| The agent completes checkout, or a URL carries the token | A ticket URL on the host; a one-time, 120-second, HMAC-signed code posted to `/claude-commerce/continue`; the Twig checkout continues; no order-placing code path | shopware-identity-and-handoff; `HandoffBroker` in `storefront/api/handoff.py`, `shopware_common/handoff.py`, `docker/plugins/CommerceAgentsHandoff/` |
| Terms from the model or a pasted text; disclosures as prose | Policies from the shop's CMS pages and `/agents.md`; disclosure rows from `referencePrice`, `deliveryTime`, stock, and fixed copy, byte-compared in evals | shopware-compliance-de; `PolicyIndex` in `storefront/api/policies.py`, `disclosure_from_store_product` in `disclosures.py` |
| Metrics by the model over rows; traffic guessed | Aggregations with cancelled excluded; `None` with a `note` for what Shopware lacks; limitations in `get_merchant_context` | shopware-admin-mcp; `get_business_snapshot` and `query_metrics` in `merchant/api/shopware_backend.py`, `merchant/api/insights.py` |
| Merchant writes applied by the model; no preview | `stage_*` builds the payload and previews it with `dryRun=true`; the payload is stored; `apply_change` replays it with `dryRun=false` and is the only live write | shopware-admin-mcp, shopware-promotions; `ShopwareWriter` in `merchant/api/staging.py`, `SqliteChangeLedger` in `ledger.py` |
| Approval by chat text; partial failures silent | Host marks the id approved through the portal route; guardrails re-run at apply; a partial failure names what was written and leaves the change staged | shopware-admin-mcp; `apply_change` in `merchant/api/shopware_backend.py`, `merchant/api/portal.py` |
| No suite, or cases that type the expected figures | YAML cases with `$NAME` placeholders; `byte_exact_disclosure`, `grounded_numbers`, `guardrail_triggered`; a replay mode over recorded fixtures and a CI gate | `/author-shopware-evals`; `evals/` |

Order the rows: the evals row comes first when it applies, since it measures the rest; the
checkout and identity rows come next, because a token in a URL or an order-placing code path is
the largest exposure; the transport row is the largest change and comes last. Present the table
and stop; ask which rows to convert.

## Step 4: Convert a row

Before the first code row, the agent's shop calls become a `StorefrontBackend` or `MerchantBackend`
subclass, so the blueprint's executor, gates, and fencing sit in front of them; that lets the rows
land one at a time. A row's module is copied from the reference (the hosts are MIT; `vendor/` and
the blueprint packages stay unmodified) or written against the same Shopware surface under the same
name. Each new UCP or Admin call gets a netless replay. After each row the eval suite runs in
`replay` mode, `/shopware-ucp-doctor` runs once against the shop, and the record is updated. A flow
the agent lacks is `/add-shopware-flow`.
