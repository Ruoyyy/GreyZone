# SwagCommerceAgentTools

Shopware 6.7 plugin that moves the agent capabilities of the Python reference hosts (`storefront/api`, `merchant/api`) into Shopware as MCP tools. This is Phase 4 of the masterplan (§5, items 4.1, 4.2, 4.4, 4.5): every agent that can speak MCP (Claude, ChatGPT, Gemini, custom) gets the same shop policies, compliance rows, fulfillment options, staged-change workflow and analytics, without re-implementing them in a host.

Status: first increment, unit-tested and statically analysed, **not yet installed into the shared Docker container on :8080** (see [Install](#install)).

## Contents

| Area | What |
|---|---|
| Store API MCP tools (`/store-api/_mcp`, group `agent-shopping`) | `shopping-policy-search`, `shopping-disclosure`, `shopping-fulfillment-options` |
| Admin API MCP tools (`/api/_mcp`, group `agent-merchant`) | `agent-change-stage`, `agent-change-list`, `agent-change-apply`, `agent-change-discard`, `agent-business-snapshot`, `agent-metrics-series` |
| Entity | `swag_agent_staged_change` (ledger of proposed changes, migration `Migration1788434320CreateAgentStagedChange`) |
| Flow Builder triggers | `swag.agent.change.staged`, `swag.agent.change.applied` (`FlowEventAware` + `MailAware` + `ScalarValuesAware`), published to `shopware://business-events` |
| Config (`Resources/config/config.xml`) | shipping page URL, policy search limits, approver e-mail/name, max items per change |
| Snippets | `de-DE`, `en-GB` disclosure copy (`swag-commerce-agent-tools.disclosure.*`) |
| ACL | `agent_change:read|create|update` + entity privileges; role templates in `Resources/config/acl-role-template.json` |

## Layout

```text
SwagCommerceAgentTools/
├── composer.json                     swag/commerce-agent-tools, shopware/core ~6.7.11
├── phpstan.neon.dist                 level max (src clean; tests ignore json_decode "mixed" noise)
├── phpunit.xml                       unit suite, no kernel/DB
├── src/
│   ├── SwagCommerceAgentTools.php    plugin class (drops the ledger table on uninstall unless keepUserData)
│   ├── Migration/                    swag_agent_staged_change
│   ├── Resources/config/             services.xml, config.xml, acl-role-template.json
│   ├── Resources/snippet/            de_DE, en_GB
│   ├── Mcp/StoreApi/                 PolicySearchTool, DisclosureTool, FulfillmentOptionsTool
│   ├── Mcp/Admin/                    Change{Stage,List,Apply,Discard}Tool, BusinessSnapshotTool, MetricsSeriesTool
│   ├── Mcp/Support/                  IdListParser (JSON array or CSV of UUIDs)
│   ├── Shopping/Policy/              PolicyTextExtractor (CMS → text), PolicyScorer, PolicyDocument/Match
│   ├── Shopping/Disclosure/          DisclosureFormatter, DisclosureInput, MoneyFormatter
│   ├── Shopping/Fulfillment/         ShippingFeeResolver, DeliveryTimeFormatter, ShippingFee
│   ├── StagedChange/                 entity, ChangePlanner, StagedChangeService, StagedChangeStateMachine,
│   │                                 ProductSnapshotLoader, ActorResolver, AgentChangePrivileges, enums
│   ├── Analytics/                    ReportingPeriod, OrderMetricsRepository, MetricName, Granularity, MetricsSegment
│   └── Event/                        AgentChangeStagedEvent, AgentChangeAppliedEvent, BusinessEventCollectorSubscriber
└── tests/
    ├── TestBootstrap.php             autoload discovery, McpToolGroup shim for < 6.7.14
    ├── compat/McpToolGroup.php
    └── Unit/                         149 tests
```

## Store API tools (shopping agents)

All three run in the sales-channel context resolved by `StoreApiMcpContextProvider` and read exclusively through Store API route abstractions (`AbstractNavigationRoute`, `AbstractCategoryRoute`, `AbstractLandingPageRoute`, `AbstractProductDetailRoute`, `AbstractProductListRoute`, `AbstractShippingMethodRoute`), so visibility, pricing rules and language follow the shopper's context. The Store API MCP server has no ACL and no allowlist; every tool validates its input and caps its output.

Authentication: `sw-access-key` (+ optional `sw-context-token` to share the shopper's cart/context). The toolset must be enabled per MCP session (`shopware-toolsets-list` → `shopware-toolset-enable agent-shopping`) on 6.7.14+.

### `shopping-policy-search {query, limit=5}`

Searches the plain text of the sales channel's footer and service navigation pages (CMS text slots, navigation depth 2, `buildTree=false`) plus active landing pages. Returns

```json
{"success": true, "data": [{"policy_id": "…", "title": "Widerrufsbelehrung", "category": "footer-navigation", "content": "…excerpt…", "url": null, "score": 9.4}],
 "_meta": {"query": "Widerruf", "pagesSearched": 7, "salesChannelId": "…"}}
```

HTML is stripped, whitespace collapsed, excerpts windowed around the first hit and capped (`policyExcerptLength`, default 1200 chars). Ranking is a deterministic keyword scorer (title hits × 3, body hits, token coverage bonus). At most 40 pages are read per call; `limit` is capped by `policySearchMaxResults`. No index is persisted yet (see [Deferred](#deferred)).

### `shopping-disclosure {productId}`

Server-authored German price-indication rows for one product, copy from snippets only:

| key | de-DE `text` | source |
|---|---|---|
| `price` | `24,90 €` | `calculatedPrice.unitPrice` |
| `base_price` | `Grundpreis: 2,49 € / 1 l` | `calculatedPrice.referencePrice` (price, referenceUnit, unitName); omitted without reference price |
| `delivery_time` | `Lieferzeit: 2-5 Tage` | `product.deliveryTime` name, fallback `min-max unit` |
| `tax` | `Alle Preise inkl. MwSt.` / `zzgl. MwSt.` / `ohne MwSt.` | `SalesChannelContext::getTaxState()` |
| `shipping` | `Alle Preise zzgl. Versandkosten` (+ `url`) or `Versandkostenfrei` | `product.shippingFree`, config `shippingInfoUrl` |

Each row is `{key, label, value, text, url}`; the agent relays `text` verbatim. Number format is locale-driven and intentionally not ICU-based (`1.234,56 €` for German-style locales, `€1,234.56` otherwise) so eval snapshots are byte-stable. The locale comes from `LanguageLocaleCodeProvider` for the context language.

### `shopping-fulfillment-options {productIds}`

`productIds` is a JSON array or comma-separated list of up to 20 product UUIDs. Returns every available shipping method (`onlyAvailable=1`) as

```json
{"method": "shipping", "shippingMethodId": "…", "name": "Standard", "selected": true,
 "eta": {"min": 2, "max": 5, "unit": "day", "text": "2-5 Tage"},
 "fee": {"amount": 4.9, "currency": "EUR", "estimated": true},
 "location": null,
 "products": [{"productId": "…", "eta": {…}}]}
```

ETA per product is the product's delivery time, else the method's; the option-level ETA is the widest range. Fee: exact from `cart.deliveries[]` when the shopper's cart (via `sw-context-token`) already carries a delivery for that method, otherwise estimated from the shipping-method price matrix using the same rule matching as `DeliveryCalculator` (calculation rule and availability rule must match the context; lowest quantity tier; default-currency price converted with the currency factor). `estimated` tells the agent which one it got. Products that are all `shippingFree` report a 0.00 fee. `_meta.unknownProductIds` lists IDs that are not visible in the channel.

## Admin API tools (merchant agents)

Authentication: integration or user token on `/api/_mcp`; the integration's MCP allowlist and ACL role apply (see [ACL](#acl-and-allowlist)). All writes default to `dryRun=true`.

### Staged-change workflow

```text
agent-change-stage (dryRun=true)   → preview only, nothing stored
agent-change-stage (dryRun=false)  → ledger row "staged" + event swag.agent.change.staged   (no product write)
agent-change-apply (dryRun=true)   → re-runs the write in a rolled-back transaction
agent-change-apply (dryRun=false)  → product write + row "applied" + event swag.agent.change.applied
agent-change-discard (dryRun=false)→ row "discarded"
```

State machine: `staged → applied`, `staged → discarded`; applied and discarded are terminal. Apply/discard on a terminal change is refused with `Change "…" is "applied" and cannot become "applied"…`, and nothing is written.

#### `agent-change-stage {kind, items, summary, note='', guardrailNotes='', salesChannelId='', currency='', margins='', dryRun=true}`

`kind` ∈ `listing_update | price_update | inventory_action` (`promotion` and `campaign` are refused, see Deferred). `items` is a JSON array:

| kind | item | resulting product payload |
|---|---|---|
| `listing_update` | `{productId, field, value}` with `field` ∈ `name, description, metaTitle, metaDescription, keywords` | `{id, <field>: value}` |
| `price_update` | `{productId, gross, net?, currencyId?}` | `{id, price: [...existing currencies, {currencyId, gross, net, linked}]}`; `net` defaults to `gross / (1 + taxRate/100)` |
| `inventory_action` | `{productId, action: restock, quantity}` / `{productId, action: pause|activate}` | `{id, stock: current + quantity}` / `{id, active: false|true}` |

Any other listing field is refused at staging time. The tool loads the referenced products (DAL, ACL applies), builds the payload, and validates it by executing the real upsert inside `executeWithDryRun()` (transaction + rollback, `SKIP_TRIGGER_FLOW`), exactly like core `shopware-entity-upsert dryRun=true`. The response carries `items[] {target, targetLabel, field, before, after}`; `price.*` rows also carry `currencyId`. `guardrailNotes` (JSON list) and `margins` (`{"before": 20, "after": 31.5, "min": 15}`, percent) are stored for the approver. Max items per change: config `maxItemsPerChange` (default 50).

#### `agent-change-list {status='staged', limit=25, page=1}`

`status` ∈ `staged | applied | discarded | all`. Rows come back as `toToolArray()`: change ID, kind, status, summary, note, preview `items`, guardrail notes, who staged/applied/discarded it and when, error message of a failed apply, sales channel, currency, margin fields.

#### `agent-change-apply {changeId, dryRun=true}` / `agent-change-discard {changeId, dryRun=true}`

Apply additionally requires `<targetEntity>:update` (currently `product:update`). `applied_by` / `discarded_by` is stamped from the context source (`AdminApiSource` user ID or integration ID; `created_by_kind` ∈ `user | integration | system`). A failing live write records `error_message` on the row and leaves it `staged`.

### Analytics

Both tools aggregate `order` / `order_line_item` through the DAL (`limit 0`, cancelled orders excluded) and share the period vocabulary of `ReportingPeriod`: rolling `7d`, `30d`, `90d`, … (≤ 730d), calendar `today`, `yesterday`, `this_week`, `last_week`, `this_month`, `last_month`, `this_quarter`, `last_quarter`, `this_year`, `last_year`, `ytd`, or explicit `YYYY-MM-DD..YYYY-MM-DD`. Ranges are half-open `[from, to)`; the comparison period is the previous calendar unit for calendar tokens and the same number of days before for rolling ones (ytd compares the same span of the previous year).

#### `agent-business-snapshot {period='30d', salesChannelId=''}`

`metrics.{sales, orders, aov, units}` as `{current, previous, deltaPct}`, `metrics.{traffic, conversion}` as `null` with a `note` (Shopware core measures neither). Revenue sums `amountTotal` in each order's own currency; the response says so.

#### `agent-metrics-series {metric, period='30d', granularity='day', segment='', salesChannelId=''}`

`metric` ∈ `sales | orders | aov | units | traffic | conversion`; `granularity` ∈ `day | week | month` (daily series ≤ 400 days); `segment` ∈ `category:<uuid>` (filters `lineItems.product.categoriesRo.id`) or `sales_channel:<uuid>`. Returns `series[] {date, value}`; traffic/conversion return an empty series with the note.

## Flow Builder

`AgentChangeStagedEvent` and `AgentChangeAppliedEvent` are dispatched with the change's `Context` right after the ledger row is written/updated (never during dry runs). They expose `changeId`, `kind`, `status`, `summary`, `itemCount`, `actorId`, `actorKind`, `targetEntity`, `salesChannelId` as scalar flow data (usable in mail templates and rule conditions) and implement `MailAware` with recipients from the plugin config `approverEmail`/`approverName`, so "Send e-mail" works without a custom action. `BusinessEventCollectorSubscriber` registers both in the `BusinessEventCollector`, which feeds the Flow Builder trigger list and the MCP resource `shopware://business-events`.

Suggested flow: trigger `swag.agent.change.staged` → action "Send e-mail" (recipient: default) to the approver with `{{ summary }}` and a link to the approval surface.

## ACL and allowlist

Privileges checked by the tools (`requirePrivilege`) and enforced by the DAL:

| Tool | Tool-level check | DAL / additional |
|---|---|---|
| `agent-change-stage` | `agent_change:create`, `swag_agent_staged_change:create`, `swag_agent_staged_change:read` | `product:read`, `product:update` (dry-run write), `tax:read` |
| `agent-change-list` | `agent_change:read`, `swag_agent_staged_change:read` | – |
| `agent-change-apply` | `agent_change:update`, `swag_agent_staged_change:update`, `swag_agent_staged_change:read`, `<targetEntity>:update` | – |
| `agent-change-discard` | `agent_change:update`, `swag_agent_staged_change:update`, `swag_agent_staged_change:read` | – |
| `agent-business-snapshot`, `agent-metrics-series` | `order:read`, `order_line_item:read` | – |

`Resources/config/acl-role-template.json` ships three role templates and the matching MCP allowlists:

- `claude-merchant-agent` – the agent's integration: analytics + `agent-change-stage` + `agent-change-list`. It deliberately lacks `agent_change:update`, so the agent can never apply its own proposals (maker-checker).
- `agent-change-approver` – human approver or approval portal: `agent-change-list`, `agent-change-apply`, `agent-change-discard`.
- `agent-change-viewer` – read-only.

Create the roles under Settings → Users & permissions → Roles (or `POST /api/acl-role` with the privilege lists), assign them to the integration/user, and restrict the integration's MCP allowlist (Settings → Integrations → Edit MCP Tools) to the listed tools. The Store API tools have no allowlist; the sales channel access key is the boundary.

## Install

Not performed against the shared container yet. Steps for a Shopware 6.7.11+ project:

```bash
# 1. make the plugin visible to Shopware
ln -s "$(pwd)/shopware-plugins/SwagCommerceAgentTools" <shop>/custom/plugins/SwagCommerceAgentTools
#    (or add a path repository in the shop's composer.json and `composer require swag/commerce-agent-tools`)

# 2. install, activate (runs the migration), rebuild the container
cd <shop>
bin/console plugin:refresh
bin/console plugin:install --activate SwagCommerceAgentTools
bin/console cache:clear

# 3. verify (Admin tools only; Store API tools do not appear in debug:mcp)
bin/console debug:mcp | grep agent-
```

For the project's dockware container (`commerce-agents-shopware`, 6.7.13.0) the same steps run inside the container after copying/mounting the folder to `/var/www/html/custom/plugins/SwagCommerceAgentTools`; on 6.7.11–6.7.13 the MCP endpoints additionally need `MCP_SERVER=1` in the environment. Then configure the plugin (Settings → Extensions → Commerce Agent Tools): shipping page URL, approver e-mail.

Store API smoke test after install:

```bash
curl -s -X POST http://localhost:8080/store-api/_mcp -H 'sw-access-key: <KEY>' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"shopping-policy-search","arguments":{"query":"Widerruf"}}}'
```

## Development

The unit suite needs no Shopware kernel or database; it needs `shopware/core` and PHPUnit on the autoloader.

```bash
# inside a Shopware project (plugin at custom/plugins/SwagCommerceAgentTools): autoload is found automatically
composer -d custom/plugins/SwagCommerceAgentTools ci

# standalone: point at any vendor/ that contains shopware/core ~6.7 + phpunit + phpstan
SWAG_AGENT_TOOLS_AUTOLOAD=/path/to/vendor/autoload.php vendor/bin/phpunit --testsuite unit
SWAG_AGENT_TOOLS_AUTOLOAD=/path/to/vendor/autoload.php vendor/bin/phpstan analyse   # copy phpstan.neon.dist → phpstan.neon
```

What ran for this increment: `php -l` on all 65 PHP files, PHPUnit 11.5 (149 tests, 805 assertions) on PHP 8.5 (host) and PHP 8.3 (`php:8.3-cli` container), PHPStan 2.1 level max, all against `shopware/core` v6.7.13.1 installed via Composer in a throwaway directory. No Shopware kernel, database or the shared Docker container was involved.

## How the Python hosts switch to these tools

Nothing in the hosts changes until the plugin is installed; the switch is per backend method and keeps the Blueprint contracts untouched.

| Host method | Today | With the plugin |
|---|---|---|
| `storefront/api/shopware_backend.py::search_policies` | `policies.py` builds a CMS/agents.md index in the host | `tools/call shopping-policy-search {query, limit}` on `/store-api/_mcp` via `shopware_common/mcp_client.py`; map rows to `Policy(policy_id, title, category, content)` |
| `…::get_disclosure` | `disclosures.py` formats rows from `data/disclosure_copy.de.json` | `shopping-disclosure {productId}`; relay `rows[].text` byte for byte into `Disclosure` |
| `…::get_fulfillment_options` | `store_api.py` shipping methods + product delivery time | `shopping-fulfillment-options {productIds}`; map `options[]` to `FulfillmentOption(method, eta, fee, location)`; pass the session's `sw-context-token` so `fee.estimated` is `false` when a cart exists |
| `merchant/api/shopware_backend.py::stage_listing_update/stage_price_update/stage_inventory_action` | `staging.py` ledger (SQLite) + `shopware-entity-upsert dryRun=true` preview | `agent-change-stage {kind, items, summary, guardrailNotes, margins, dryRun=false}`; `StagedChange.items` ← response `items[]`, `StagedChange.id` ← `changeId` |
| `…::get_pending_changes` | ledger | `agent-change-list {status: "staged"}` |
| `…::apply_change` (after host approval) | guardrails → `shopware-entity-upsert dryRun=false` → ledger | guardrails → `agent-change-apply {changeId, dryRun=false}` with the **approver** credentials |
| `…::discard_change` | ledger | `agent-change-discard {changeId, dryRun=false}` |
| `…::get_business_snapshot`, `query_metrics` | `insights.py` over `shopware-entity-aggregate` | `agent-business-snapshot {period, salesChannelId}`, `agent-metrics-series {metric, period, granularity, segment}` |

The Blueprint's provenance and approval gates stay in the host (`require_host_approval=True`); the plugin adds the second, server-side gate (ACL split between stager and approver).

## Deferred

- `promotion` / `campaign` change kinds (masterplan 4.6): `agent-promotion-stage` with Dynamic Product Groups / Rule Builder as target set and SKU/margin preview. `agent-change-stage` refuses these kinds with a message the agent can relay; the host keeps its current `stage_promotion` path.
- Admin module `sw-agent-changes` (4.3): list, diff, approve/discard in the Administration, plus the Administration privilege mapping (`agent_change.viewer|approver`) that turns the JSON role template into UI checkboxes.
- `shopping-customer-preferences` (4.1), `agent-inventory-alerts`, `agent-order-issues` (4.5), pre-computed aggregates / Shopware Analytics as a metrics source.
- Persistent policy index (indexer or cache) instead of reading CMS pages per call; Elasticsearch/OpenSearch when available.
- Flow Builder action "Auto-approve if within limits" (4.4) and Admin snippets for the trigger labels.
- Integration tests against a running Shopware (MCP registration, `debug:mcp`, live tool calls) and a `store-release.yml` (4.8).
