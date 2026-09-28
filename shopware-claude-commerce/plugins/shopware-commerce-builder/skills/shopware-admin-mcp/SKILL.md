---
name: shopware-admin-mcp
description: How the blueprint's MerchantBackend runs over Shopware's Admin API MCP server, covering the handshake and progressive tool discovery, the entity tools, dryRun previews turned into StagedChange items, the payload replay on apply, partial failures, the Integration's allowlist and ACL, and aggregations for snapshot and metrics. Load when implementing or reviewing a merchant agent's reads, staged writes, or approval loop against a Shopware shop.
---

# Admin API MCP to MerchantBackend

Paths are in the Shopware reference repo: `merchant/api/` holds the host, `shopware_common/mcp_client.py`
the MCP client, `docker/merchant_identity.py` the identity bootstrap, `docs/shopware-mapping.md`
the live tool list. The blueprint contract is `MerchantBackend` in Anthropic's
`merchant-agent/core/merchant_agent/backend.py`; its staged-change contract (every write a
`stage_*`, `apply_change` on the host's approval mark, guardrails at stage and at apply) is
Anthropic's `commerce-merchant-operations` skill and holds unchanged.

## The server and the handshake

- `POST /api/_mcp` is the Admin API MCP server (6.7.11 and later; `MCP_SERVER=1` on 6.7.11 to
  6.7.13). It takes the same OAuth bearer as the REST Admin API. The host authenticates as an
  **Integration** with `client_credentials` (`OAuthTokenProvider` in `admin_client.py`, refreshed
  before expiry); the admin user's password grant is refused by `load_settings` in `agent_config.py`.
- Handshake as on `/ucp/mcp` (`McpClient`): `initialize`, `notifications/initialized`, `tools/list`,
  `tools/call` with `Mcp-Session-Id`; one transparent re-initialize on a lost session. Results
  over 100 KB come back as a `shopware://tool-result/{id}` resource.
- Progressive discovery: from 6.7.14 the server lists toolsets first (`shopware-toolsets-list`,
  `shopware-toolset-enable`); `McpClient.ensure_tool` enables the toolset carrying a tool before
  calling it, and on 6.7.11 to 6.7.13 it only checks that the tool is listed. Rate limits are 300
  per minute and 1000 per ten minutes; the audit trail is the shop's `mcp` log channel.
- `AdminTransport` is a protocol with two implementations: `McpTransport` (default,
  `SHOPWARE_ADMIN_TRANSPORT=mcp`) and `RestTransport` (`POST /api/search/{entity}`, `PATCH
  /api/{entity}/{id}`, `POST /api/_action/sync`), the fallback on 6.6 and when `/api/_mcp` is
  absent. Only MCP has the server dry run; on REST a staged change carries the host-computed
  `before` and `after` and no server note. `FakeAdmin` (`fake_admin.py`) implements the same
  protocol in process with the live tools' criteria, aggregation, and dry-run semantics, for tests
  and `SHOPWARE_LOCAL_STORE=1`.

## The tools

Criteria, aggregations, payloads, and id lists travel as JSON strings, which is the tools' input
schema. Of the twenty tools the server lists, the host calls six and the allowlist holds exactly
those (`MCP_TOOL_ALLOWLIST` in `merchant_identity.py`):

| Used for | Tool | Arguments |
|---|---|---|
| catalog, order, customer, promotion reads | `shopware-entity-search` | `entity`, `criteria` (filters, sort, associations, includes), `limit`, `page`, `term` |
| one record with associations | `shopware-entity-read` | `entity`, `id`, `criteria` |
| snapshot, metrics, slow movers | `shopware-entity-aggregate` | `entity`, `aggregations` (JSON), `filters` |
| stage preview | `shopware-entity-upsert` | `entity`, `payload` (JSON), `dryRun: true` |
| apply | `shopware-entity-upsert` | the same payload, `dryRun: false` |
| rollback of a created entity after a partial apply | `shopware-entity-delete` | `entity`, `ids`, `dryRun` |
| schema discovery while developing | `shopware-entity-schema` | `entity` |

`merchant-*` tools from `SwagMcpMerchantTools` (order summary, revenue and bestseller reports,
product create, cart and checkout) sit outside the blueprint's contract and are not called; so do
`shopware-order-state`, `shopware-system-config-*`, `shopware-media-upload`, and `shopware-theme-config`.

## Reads and aggregations

- `get_business_snapshot` and `query_metrics` aggregate `order` (`count` on `id`, `sum` on
  `amountTotal`, a `histogram` on `orderDateTime` by day, week, or month with a nested `sum`) and
  `order_line_item` (`sum` on `quantity`, `terms` on `productId` for units by product), filtered
  to the sales channel and to orders whose state is not `cancelled`, the way Shopware's own
  revenue figures are (`insights.py`). The previous period is the same aggregation over the
  earlier window. `average_order_value` is sum over count.
- Traffic and conversion do not exist in Shopware core: each is `None` with a `note`, never zero
  or an estimate; `get_merchant_context().limitations` names them, the missing campaign object, and
  the history horizon. `query_metrics` on an unknown metric answers an empty series with a `note`.
- `search_listings` and `get_listing` read `product` into `CatalogCache` (`catalog.py`), families
  with inherited prices, tax, and purchase prices; `status` derives from `active` and stock;
  `content_quality` from the description. `get_inventory_alerts` compares `availableStock` with
  `merchant/data/thresholds.json` (a default and per-product-number overrides) and finds slow
  movers from the 30-day line-item aggregation; `get_order_issues` reads `order` with deliveries,
  transactions, and `customerComment` for delayed deliveries, payment problems, and buyer
  messages; `get_pricing_context` reads `price`, `purchasePrices`, and tax, with floors from
  `pricing_policy.json` and the guardrail caps mirrored from the config.
- Every row passes through the blueprint's fence; `apiAlias` and `extensions` are stripped first.

## Staged writes: preview, store, replay

1. Each `stage_*` builds the exact Shopware payload once (`staging.py`: `price_payload`,
   `listing_payload`, `promotion_payload`, the restock and active payloads) and previews it with
   `shopware-entity-upsert` `dryRun: true`. Shopware runs the write in a transaction and rolls it
   back, so type, required-field, and privilege errors arrive before anything persists; the
   response's `_meta.dryRun` confirms nothing was written.
2. The server's verdict becomes the `StagedChange`: `items[]` with `target`, `field`, `before`
   (read fresh), and `after` (from the payload), plus a `guardrail_notes` line from `preview_note`
   recording the server's answer. A rejected preview raises `PreviewRejected` and nothing is staged.
   The payload is stored with the change in the `SqliteChangeLedger` (`ledger.py`,
   `MERCHANT_LEDGER_DSN`), so a restart loses no staged change and no id is reused.
3. `apply_change` refuses anything not `STAGED`, re-runs the blueprint guardrails under the config
   in force, and replays the stored payload with `dryRun: false` (`ShopwareWriter.apply`). It is
   the only code path that sends `dryRun: false`. Restocks are the one exception to "replay as
   staged": the staged delta is applied to the stock read at apply time, so two restocks staged
   against the same level both count.
4. A payload spanning several entities (a promotion with its discount, rule, and sales channel) is
   written in order; on a failure the entities created so far are deleted again and `WriteFailed`
   names what completed and what was rolled back. The change stays staged with that note; nothing
   is silently half-applied.
5. `discard_change` marks the ledger; `stage_campaign` and `get_campaign_performance` raise
   `ChangeNotApplicable`, and the tool stays registered.

The write kinds and their payload shapes:

| Kind | Entity and fields |
|---|---|
| `listing_update` | `product`: `name`, `description`, `metaTitle`, `metaDescription`; any other field is refused at stage time (`LISTING_FIELDS`) |
| `price_update` | `product.price`: the current entries read, only the sales-channel currency entry replaced, `net = gross / (1 + taxRate / 100)` from the product's tax, `linked` kept; a family id is held by the blueprint gate, children are the targets |
| `inventory_action` restock | `product.stock`: delta on fresh stock |
| `inventory_action` pause, activate | `product.active` on the family and every child |
| `promotion` | `promotion`, `promotion_discount`, a `rule` on the target products, `promotion_sales_channel`, in one payload (shopware-promotions) |

## Identity and least privilege

- `docker/merchant_identity.py` creates the ACL role `claude-merchant-agent` with `ACL_PRIVILEGES`
  (reads on `order*`, `product_price`, `customer`, `category`, `tax`, `currency`, `sales_channel`,
  `property_group*`, `media`, `unit`, `delivery_time`, `state_machine*`; read and update on
  `product` and `product_translation`; full access on `promotion*`, `rule`, `rule_condition`), the
  Integration bound to it, and the Admin MCP allowlist of the six tools. Re-running it rotates the
  secret when `docker/.generated.env` lacks it. `verify_mcp` proves the result: `tools/list`
  equals the allowlist, a `dryRun` upsert on `product` succeeds, a search on `user` is refused with
  `Missing privilege: user:read`.
- A new write kind adds its privilege there and, when it needs a tool the allowlist lacks, the
  tool; nothing else widens the role. No `system_config:*`, no `order:update`, no `user:*`.
- The approval mark is set by the portal's `POST /api/merchant/changes/{id}/apply` route only
  (`MERCHANT_REQUIRE_HOST_APPROVAL=1`); the operator stamped on a change is `MERCHANT_OPERATOR`
  until an admin-module surface supplies `/api/_info/me`.

## Do not

- Send `dryRun: false` from anywhere but `apply_change`, or apply a change whose status is not
  `STAGED`.
- Compute a metric in the model or the host from rows when an aggregation exists; report a
  figure Shopware lacks as zero.
- Replace the whole `price` array, or write a gross without recomputing net from the tax rate.
- Stage a listing field outside the whitelist, or widen the ACL role for a read the backend does
  not make.
- Use the admin user's credentials in a host, or put a bearer in a tool result or a log line.
