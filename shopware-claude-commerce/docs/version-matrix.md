# Version matrix

Pins of this reference repo, the Shopware lane matrix (what each Shopware line offers to the two
agents), and the measurements behind the recommendation to stay on 6.7.13.x for now. Researched on
2026-09-03 against Docker Hub, GitHub (`shopware/shopware`, `shopware/agentic-commerce`), the running
6.7.13.0 stack, and a throwaway 6.7.13.1 stack booted side by side.

## Pins

| Piece | Version / pin |
|---|---|
| Shopware (Docker) | `dockware/shopware:6.7.13.0` (`docker/compose.yaml`) |
| PHP | 8.4 (image default) |
| Anthropic blueprint | `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf` |
| UCP protocol | 2026-08-25 |
| Python | 3.11+ (CI: 3.11 and 3.12) |
| Node | 22 (web UI) |
| `SwagAgenticCommerce` | commit `df0ae7231e2b2090ccfbf3a31e7a462b40b69a0f` (`SWAG_AGENTIC_COMMERCE_REF` in `docker/bootstrap.sh`), plugin version 1.3.0 — head of the upstream `integration/all-open-prs` release branch (shopware/agentic-commerce#224), **not a tag**; re-point at the `1.3.0` tag once it ships |
| `SwagMcpMerchantTools` | commit `01e2082e99a4e9a2e56cdfd69faa38cd7c988efe` (`SWAG_MCP_MERCHANT_TOOLS_REF`) |
| `ucp-php-sdk/symfony-bundle` | `0.0.6` |

## Lane matrix

| Lane | `MCP_SERVER` flag | Progressive discovery | Store API MCP (`/store-api/_mcp`) | UCP over MCP via plugin (`/ucp/mcp`) | `dryRun` on Admin MCP writes | `sales_channel_file` discovery files | `shopware/agentic-commerce` | dockware tag (arm64) | Boot to first HTTP 401 |
|---|---|---|---|---|---|---|---|---|---|
| **6.5.x** | no MCP in core | – | – | – (plugin advertises MCP only when the core has Store API MCP) | – (Admin REST transport, local diff) | no core primitive; plugin serves its own fallback `llms.txt` / `agents.md` / `ai-catalog.json` | yes (`~6.5.0`) | `6.5.8.19` (amd64 + arm64, 2026-06-10) | not measured |
| **6.6.x** | no MCP in core | – | – | – | – (Admin REST transport) | plugin fallback files only | yes (`~6.6.0`) | `6.6.10.23` (amd64 + arm64, 2026-08-26) | not measured |
| **6.7.11 – 6.7.13.1** (current) | present, default off; **must be set in `/var/www/html/.env`** — the compose `environment: MCP_SERVER=1` reaches the CLI only, Apache/PHP requests still answer 404 until `.env` has it (measured on 6.7.13.1; `docker/bootstrap.sh` does exactly this) | **no** — `tools/list` is a flat list; the three discovery tools are absent | yes; core alone exposes 1 tool (`shopware-store-api-context`), with `SwagAgenticCommerce` 14 (13 UCP spec-named, e.g. `create_cart`) | yes (`/ucp/mcp`, protocol 2025-11-25 negotiated) | yes — `shopware-entity-upsert`, `shopware-entity-delete`, `shopware-order-state`, `shopware-system-config-write`, `shopware-theme-config` carry `dryRun` (default true) | **core primitive present** (`Migration1780062008CreateSalesChannelFile`, Admin API `/api/_action/sales-channel-file/{family}/{salesChannelId}`, core templates `llms.txt`, `AGENTS.md`, `.well-known/ai-catalog.json`); served as 404-fallback **only after a `sales_channel_file` row is enabled** — bare 6.7.13.1 answers 404, the plugin enables the three rows (`/llms.txt`, `/agents.md` 200 on the 6.7.13.0 stack) | yes (`~6.7.0`); README: "trunk/current 6.7+", MCP only advertised when the core has Store API MCP | `6.7.13.0` (2026-08-06), `6.7.13.1` (2026-08-26), `6.7-latest`; all amd64 + arm64 | **70 s** for 6.7.13.1 (arm64, Apple Silicon, image pre-pulled; measured `docker compose up -d` → first `401` on `/api/_info/version`) |
| **6.7.14+** (unreleased) | **removed** — `chore(framework): remove MCP_SERVER feature flag gate` (#18463, trunk 2026-07-20); `feature.yaml` on trunk has no `MCP_SERVER` | **yes** — `shopware-tool-search` (#17996), session toolsets `shopware-toolsets-list` / `shopware-toolset-enable` (#17997), tool groups (#17995), paginated allowlists (#17994), `tools/listChanged` notifications (#17998); domain tools are hidden until a toolset is enabled | yes, **with progressive discovery** (#18298, trunk 2026-07-25) | expected yes (plugin's own endpoint; re-verify `tools/list` once a build exists) | yes (unchanged tool set + discovery tools) | yes (same primitive) | yes | **none** — no Shopware 6.7.14.0 release exists as of 2026-09-03 (latest `v6.7.13.1`, 2026-08-25); no dockware tag; `ghcr.io/shopware/docker-dev` / `docker-base` are multi-arch (amd64 + arm64) but need a composer project (`dev-trunk`) plus asset build, not a quick pull | not measured (no image) |

Read from source, not measured: everything in the 6.7.14+ row. The commits are on `trunk`, whose
`Kernel::SHOPWARE_FALLBACK_VERSION` is still `6.7.9999999-dev`, i.e. trunk is the 6.7.14 line.

### Observed details worth knowing

- Unauthenticated `POST /api/_mcp` answers **HTTP 500** with JSON-RPC `-32000 "The resource owner or
  authorization server denied the request."` on both 6.7.13.0 and 6.7.13.1 (not a plain 401).
  Unauthenticated `POST /store-api/_mcp` (no `sw-access-key`) answers **401**.
- With an admin password-grant token, `initialize` on `/api/_mcp` negotiates protocol `2025-11-25`,
  server `Shopware 1.0.0`, capabilities `tools.listChanged`, `resources.subscribe`, `prompts`,
  `completions`, `logging`. `tools/list` on bare 6.7.13.1: 11 tools (`shopware-entity-aggregate`,
  `-delete`, `-read`, `-schema`, `-search`, `-upsert`, `shopware-media-upload`, `shopware-order-state`,
  `shopware-system-config-read`, `-write`, `shopware-theme-config`). On the 6.7.13.0 stack with
  `SwagMcpMerchantTools`: 20 (plus 9 `merchant-*`).
- `initialize` on `/store-api/_mcp` with the sales-channel access key: server `Shopware Store API`,
  1 core tool on bare 6.7.13.1; 14 with `SwagAgenticCommerce` (the UCP spec-named set, all cart/
  checkout/discount tools with `dryRun`).
- 6.7.13.0 → 6.7.13.1 changes nothing in the MCP surface (same tool names, same protocol).

## dockware/shopware tags ≥ 6.7.12 (Docker Hub, 2026-09-03)

| Tag | Published | Platforms |
|---|---|---|
| `6.7.12.1` | 2026-07-08 | amd64, arm64 (+ `-amd64` / `-arm64` variants) |
| `6.7.12.2` | 2026-07-28 | amd64, arm64 |
| `6.7.13.0` | 2026-08-06 | amd64, arm64 |
| `6.7.13.1` | 2026-08-26 | amd64, arm64 |
| `6.7-latest` | 2026-08-26 | amd64, arm64 (= 6.7.13.1) |

83 tags match `6.7`; nothing above `6.7.13.1`. `dockware/dev` stops at `6.7.1.2` for 6.7. Official
`ghcr.io/shopware/docker-dev` tags are PHP/Node based (`php8.4.25-node22-caddy`, …) and mount your own
project — usable for a `dev-trunk` lane on Apple Silicon, but `composer create-project` + admin/
storefront build is far slower than the 70 s dockware boot.

## Recommendation

**Stay on `dockware/shopware:6.7.13.0` (optionally patch-bump to `6.7.13.1`) — do not target 6.7.14
yet.**

- 6.7.14 has no release, no dockware tag, and nothing to smoke against; the repo already runs both MCP
  servers and `dryRun` previews on 6.7.13 with the flag set by `docker/bootstrap.sh`.
- `6.7.13.1` is a drop-in: identical MCP surface, same plugin compatibility, boots in 70 s on arm64.
  Bumping it is a one-line change in `docker/compose.yaml` and a re-pull; the only reason to do it is
  the security/bugfix content of the patch release.
- What changes when 6.7.14 ships (prepare now, switch later):
  - `MCP_SERVER` handling in `bootstrap.sh` is already conditional ("when present") — keep it.
  - **Merchant `McpTransport` must handle progressive discovery**: on 6.7.14 the first `tools/list`
    returns only the discovery tools; `shopware-entity-*` appear after `shopware-toolset-enable` (or
    are found via `shopware-tool-search`), and the server emits `notifications/tools/list_changed`.
    The client should call `shopware-toolsets-list` → `shopware-toolset-enable` when a required tool
    is missing, then refresh `tools/list`.
  - Store API MCP gets the same discovery flow (#18298); the shopper side uses the plugin's `/ucp/mcp`,
    which should be unaffected — verify `tools/list` there on the first 6.7.14 build.
  - Add the `6.7.14.x` dockware tag to the nightly matrix as soon as it exists (see below).

## Running a second lane side by side

The throwaway lane used for the measurements above. It keeps the 6.7.13.0 stack on `:8080`/`:3306`
untouched: different compose project name (→ separate network and MySQL volume), different container
name, different host ports.

`lane.override.yaml` (any location outside `docker/`):

```yaml
# docker compose -p lane67131 -f docker/compose.yaml -f lane.override.yaml up -d
services:
  shopware:
    image: dockware/shopware:6.7.13.1        # or the future 6.7.14.x tag
    container_name: lane67131-shopware
    ports: !override
      - "8090:80"
      - "3307:3306"
```

```bash
docker compose -p lane67131 -f docker/compose.yaml -f lane.override.yaml up -d
# wait for the Admin API
until curl -s -o /dev/null -w '%{http_code}' http://localhost:8090/api/_info/version | grep -qE '^(200|401)$'; do sleep 2; done
# feature flag + domain (what bootstrap.sh does for the main stack)
docker exec -u root lane67131-shopware bash -lc "grep -q '^MCP_SERVER=' /var/www/html/.env || echo 'MCP_SERVER=1' >> /var/www/html/.env; \
  sed -i 's|^APP_URL=.*|APP_URL=http://localhost:8090|' /var/www/html/.env; \
  mysql -uroot -proot shopware -e \"UPDATE sales_channel_domain SET url='http://localhost:8090' WHERE url LIKE 'http://localhost%';\""
docker exec -u www-data lane67131-shopware bash -lc 'cd /var/www/html && php bin/console cache:clear --no-warmup'
# tear down (volume included)
docker compose -p lane67131 -f docker/compose.yaml -f lane.override.yaml down -v
```

`-p` replaces the `name: commerce-agents` in `compose.yaml`, so the volume becomes
`lane67131_shopware-mysql`. `docker/bootstrap.sh` cannot be pointed at the lane yet: it hard-codes
`docker compose -f docker/compose.yaml` and `up -d`s the main project. Running the full bootstrap
(plugins, UCP exposure, seed) against a lane needs `bootstrap.sh` to accept an extra compose file /
project name (`SHOPWARE_CONTAINER` and `SHOPWARE_PUBLIC_URL` are already overridable).

## CI matrix implication

- `.github/workflows/ci.yml` is netless (ruff, pytest, Next.js builds, plugin PHPUnit) — no Shopware
  version dimension.
- `.github/workflows/integration.yml` (nightly + manual) boots whatever `docker/compose.yaml` pins,
  today `6.7.13.0`, on `ubuntu-latest` (amd64 tag of the same multi-arch image). Adding lanes needs
  the image to be parameterised — `image: dockware/shopware:${DOCKWARE_TAG:-6.7.13.0}` in
  `docker/compose.yaml` — after which the job can carry
  `strategy.matrix.dockware_tag: ["6.7.13.0", "6.7.13.1"]` with `env: DOCKWARE_TAG: ${{ matrix.dockware_tag }}`
  (one runner per lane, ~10–15 min each; do not run them inside one job because container name and
  ports collide).
- A 6.7.14 lane cannot be in CI until a `dockware/shopware:6.7.14.x` tag exists. A `dev-trunk` lane
  via `ghcr.io/shopware/docker-dev` + `composer create-project` is possible but adds 20+ minutes per
  run — acceptable as a manual `workflow_dispatch` lane, not as the nightly default.
- 6.5 / 6.6 lanes (Admin REST transport, UCP REST only) would exercise the fallbacks; both have
  current dockware tags with arm64. Worth adding once the merchant `RestTransport` and the UCP REST
  path have their own smoke assertions.
