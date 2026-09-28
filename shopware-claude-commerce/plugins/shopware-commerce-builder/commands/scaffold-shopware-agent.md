---
description: "Interview the user about their Shopware shop (URL, sales channel, lane, transports, which agents), play the plan back, and scaffold a shopping agent, a merchant agent, or both on the Shopware reference hosts: Integration and ACL role, UCP exposure, agent signing key, the host packages instantiated, .env written. Use when a commerce agent for a Shopware 6 shop is being started; one that exists already is /review-shopware-agent."
argument-hint: "[shop URL, Shopware version, and which agent(s), if known]"
---

Scaffold a commerce agent for a Shopware 6 shop on the `agentic-commerce-lab/shopware-claude-commerce`
reference, which runs Anthropic's `commerce-agents` blueprint unmodified against Shopware. The
shopping agent serves a customer over `ShopwareStorefrontBackend` (UCP for catalog and cart, the
Store API for what UCP lacks, a one-time handoff code into Shopware's own checkout); the merchant
agent serves an operator over `ShopwareMerchantBackend` (Admin API MCP reads and aggregations,
every write a `dryRun=true` preview the host applies later with `dryRun=false`). The user said:

$ARGUMENTS

Ask for the role first when that leaves it open: it selects Step 1's reads and how often Step 3
runs.

## Step 1: Locate the reference and read it

1. The current repo is the reference when `storefront/api/shopware_backend.py` and
   `merchant/api/shopware_backend.py` exist; otherwise use a local clone, or clone
   `https://github.com/agentic-commerce-lab/shopware-claude-commerce.git` under `/tmp`. Note its commit, and
   the Anthropic blueprint commit its `requirements.txt` pins (the `UPSTREAM` line), for Step 2b.
2. Read these before writing anything:
   - `README.md` (the mapping table, the safety model, the configuration table), then
     `docs/shopware-mapping.md` (live tool names, REST paths, cart-id semantics, handoff, Store
     API surfaces, Admin MCP tools, write payloads) and `.env.example`.
   - `docker/bootstrap.sh` and the helpers it sequences under `docker/*.py`: `enable_ucp.py` (UCP
     exposure and the shop's signing key), `agent_key.py` (the agent's P-256 key and its JWK in
     `agent-profile.json`), `merchant_identity.py` (ACL role, Integration, Admin MCP allowlist,
     end-to-end check), `seed_catalog.py`, `write_credentials.py` (`docker/.generated.env`), and
     `_bootstrap_lib.py` (the Admin API client and the env-file upsert they share).
   - Shopping agent: `storefront/api/main.py` (how `build_storefront_host` from the vendored
     `demo_common` is composed with the backend, the agent, and the extra routes), `ucp_client.py`,
     `store_api.py`, `shopware_backend.py`, `handoff.py`, `identity.py`, `policies.py`,
     `disclosures.py`, `agent_config.py`; `storefront/data/`; `storefront/api/tests/replay.py`.
   - Merchant agent: `merchant/api/main.py` and `merchant.py`, `agent_config.py` (settings and
     credentials), `admin_client.py` (the transport protocol, MCP and REST), `shopware_backend.py`,
     `staging.py` (payload builders, preview and apply), `ledger.py`, `catalog.py`, `insights.py`,
     `portal.py`, `fake_admin.py`; `merchant/data/`.
   - `shopware_common/` (`mcp_client.py`, `http_signing.py`, `handoff.py`, `anthropic_client.py`)
     and `docker/plugins/CommerceAgentsHandoff/`.
   - `vendor/skills/shopping/` and `vendor/skills/merchant/`: the blueprint's five flows per role,
     vendored verbatim.
3. The blueprint's own rules (one model per conversation, cache-stable prompt bytes, fenced tool
   results, provenance-gated writes, host approval) hold unchanged; when Anthropic's
   `commerce-builder` plugin is installed its skills state them, and this plugin's skills state
   only what Shopware adds.

## Step 2: Interview

Ask everything in one message, prefilled from $ARGUMENTS and the repo, saying what you inferred.
A skipped question takes the default in parentheses, listed as an assumption in Step 2b. Local
lane (the user says Docker, local, or demo): ask 1, 3, 5, and 9; default the rest.

1. Shop: the sales-channel domain that serves `/.well-known/ucp` (`SHOPWARE_URL`), and the Admin
   API base when it differs (`SHOPWARE_ADMIN_URL`). Reachability from where the hosts run, and
   from the browser (the handoff posts the browser to the shop). (Default: the Docker shop at
   `http://localhost:8080`, started by `docker compose -f docker/compose.yaml up -d`.)
2. Sales channel: name or id of the channel whose domain answers discovery; it supplies the Store
   API `sw-access-key` and scopes promotions. (Default: `Storefront`.)
3. Lane: 6.7.11 and later carry both MCP servers (`/ucp/mcp` and `/api/_mcp`; the `MCP_SERVER=1`
   flag is needed on 6.7.11 to 6.7.13 and gone from 6.7.14); 6.6 and 6.5 carry
   `SwagAgenticCommerce` over REST and the Store API only, so the shopper runs `UCP_TRANSPORT=rest`
   and the merchant `SHOPWARE_ADMIN_TRANSPORT=rest`, which has no server dry run: a staged change
   then carries the host-computed `before` and `after` only. Read the version from
   `bin/console --version` inside the container or `GET /api/_info/version` with a token.
   (Default: 6.7.13 as pinned in `docker/compose.yaml`.)
4. Transports: `UCP_TRANSPORT` (`mcp` with REST fallback, or `rest`); `SHOPWARE_ADMIN_TRANSPORT`
   (`mcp` for previews, `rest` as fallback); and the signing posture. Production runs
   `signature-policy=strict` with a P-256 agent key (`UCP_AGENT_SIGNING_KEY_PEM_FILE`) whose JWK
   is the one entry in a published `agent-profile.json`; the shop fetches that profile from its
   own network, and an `http` profile URL needs the SDK's development mode, which never goes to a
   public shop. (Default: `mcp` on both; `strict` with the bootstrap's generated key; the profile
   served from the shop's `public/` as the Docker lane does.)
5. Role: shopping agent, merchant agent, or both. Both means two hosts on two ports, as the
   reference runs them (`:8004` and `:8005`).
6. Shopping agent: the checkout: the `CommerceAgentsHandoff` plugin in the shop (a one-time code
   posted to `/claude-commerce/continue`, the Twig checkout continues; the reference's way), the
   plugin's `continue-url-template` with a UCP checkout session (not used by the reference), or a
   host that renders the cart only. Identity: guests only, or UCP Identity Linking, which needs
   an `https` agent-profile `client_id` and a Shopware customer login. Locale for the cart context
   (`address_country`, `language`) and the disclosure copy language (`storefront/data/disclosure_copy.*.json`).
   (Default: the handoff plugin; guests, with `GET /api/auth/status` saying why linking is off;
   `DE`, `de`, German copy.)
7. Merchant agent: the approval surface (the portal rail and `POST /api/merchant/changes/{id}/apply`
   are the reference's; `MERCHANT_REQUIRE_HOST_APPROVAL=1` stays on), the operator stamped on changes
   (`MERCHANT_OPERATOR`), the ledger (`MERCHANT_LEDGER_DSN`, SQLite by default), the thresholds in
   `merchant/data/thresholds.json` and floors in `pricing_policy.json`, and which change kinds
   the store wants: listing, price, inventory, promotion; campaigns stay `ChangeNotApplicable`.
   (Default: all four kinds; portal approval; `ops@example.com`; SQLite beside the data.)
8. Credentials: who creates the Integration and the ACL role. With an admin credential the
   bootstrap helpers do it (the admin password is used by the scripts once and never lands in a
   host's `.env`); without one, the merchant creates them in the Administration from the list
   Step 3 prints and pastes `SHOPWARE_INTEGRATION_ACCESS_KEY` and `SHOPWARE_INTEGRATION_SECRET_KEY`.
   (Default: the helpers, with the Docker `admin` / `shopware`.)
9. Layout: the reference checkout itself (a fork; hosts run in place), or a new project that
   copies the host packages. Anthropic access: `ANTHROPIC_API_KEY`, plus `ANTHROPIC_WORKSPACE_ID`
   for an identity-linked key; a cloud platform is the blueprint's `docs/deployment.md`.
   (Default: the fork; the Anthropic API.)
10. Web UIs: the Next.js storefront (`:3005`) and portal (`:3006`) on the vendored `web-shared`,
    or headless hosts. (Default: both UIs; Node 22.)

Do not ask about model tier, cost, or scale; the blueprint config defaults hold until evals say
otherwise.

## Step 2b: Plan back and record

Play the plan back in one message and get a yes before writing anything:

- Shop URL, Admin base, sales channel, lane, and the two transports with their fallbacks.
- Signing posture: policy, key file, profile URL, and who publishes the profile.
- Role(s); per agent the port, the layout (fork or copied packages), and the UI.
- Shopping: checkout mechanism, identity mode, locale, disclosure copy.
- Merchant: approval surface, operator, ledger, change kinds, the limitations
  `get_merchant_context` will name (traffic, conversion, campaigns).
- Credentials: Integration name and ACL role, who creates them, where the values land
  (`docker/.generated.env` or `.env`); never a value.
- Assumptions taken for skipped questions; the reference commit and the blueprint commit.

Name the skill behind each line where one applies. On yes, write the plan into the project's
`CLAUDE.md` under `## Shopware commerce agent decision record` (a subsection per agent when
both); record the auth mechanism and where each credential lives, never a credential.
`/add-shopware-flow`, `/author-shopware-evals`, and `/shopware-ucp-doctor` read this section;
update it when a decision changes.

## Step 3: Set up the shop

**Local lane.** `docker compose -f docker/compose.yaml up -d && ./docker/bootstrap.sh` from the
repo root. The bootstrap is idempotent: one shop signing key, one agent key, one Integration, one
ACL role, no duplicate seed rows; re-run it after every `compose down` and `up`, because a
recreated container drops the plugin files while MySQL survives in its volume.

**A shop you do not run.** Run the helpers one by one against the shop, each with `--shop-url`;
they call the Admin API only, and each prints what it changed:

| Helper | Does | Needs |
|---|---|---|
| `docker/enable_ucp.py --signature-policy strict` | Writes the channel's UCP config through the plugin's Admin route (`/api/_admin/ucp/sales-channels/{id}/config`: capabilities, transports, allowlists, `idempotencyRequired`, policy), keeps exactly one active shop signing key, purges the profile cache | an admin credential; console access for `ucp:signing-keys:*` |
| `docker/agent_key.py write-profile` | Derives the JWK of the agent's PEM and writes it as the single `signing_keys` entry of `agent-profile.json` | the PEM (`openssl ecparam -genkey -name prime256v1`) |
| `docker/merchant_identity.py` | Creates the ACL role (`ACL_PRIVILEGES`: reads on the order, product, promotion, and reference entities; update on `product`; full access on `promotion*` and `rule*`), the Integration bound to it, sets the Admin MCP allowlist to the six `shopware-entity-*` tools the backend calls, and verifies `tools/list` with the new credential | an admin credential |
| `docker/write_credentials.py` | Upserts the host-side values into `docker/.generated.env`; removes the admin user and password from it | the sales-channel id and access key it reads back |

Without console access, the merchant runs the CLI: `ucp:config:set` with `--signature-policy`,
`--idempotency`, and the three allowlists, and `ucp:signing-keys:generate`; print the exact
flags from `desired_config` in `enable_ucp.py`. Without an admin credential, print the ACL
privilege list and the allowlist for the Administration, and take the Integration keys as
answer 8 says. Publish `agent-profile.json` where the shop can fetch it over `https`; the Docker
lane copies it into the container's `public/` and allows `http` with
`SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1`, which is local-only.

The `CommerceAgentsHandoff` plugin (`docker/plugins/CommerceAgentsHandoff/`) is installed in the
shop and configured with the same `COMMERCE_AGENTS_HANDOFF_SECRET` the storefront host holds; the
bootstrap does both. Without it, `checkout_url` is absent from the cart payload and the shopping
agent renders the cart only.

## Step 3b: Instantiate the hosts

**Fork.** The hosts run in place. Python 3.11+, `python3 -m venv .venv && pip install -r
requirements-dev.txt` installs the blueprint packages from Anthropic's repository at the pinned
commit plus `vendor/` (never an editable path into a blueprint clone); `npm install` for the UIs.

**New project.** Copy, keeping the names so the module paths in the README and the scripts stay
true: `storefront/` (`api/`, `data/`, `scripts/`, `web/` when a UI), `merchant/` (`api/`, `data/`,
`scripts/`, `web/`), `shopware_common/`, `vendor/` with its `NOTICE`, `agent-profile.json`,
`requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `ruff.toml`, `package.json`, and the
`docker/` directory when the local lane is wanted. The tests and their replay fixtures come along
(`storefront/api/tests/`, `merchant/api/tests/`, `shopware_common/tests/`), so the suite stays
offline. Drop `linkedin/`, `docs/media/`, and the screenshots. Nothing under `vendor/` or in a
blueprint package is edited; Shopware-specific code lives in `storefront/`, `merchant/`,
`shopware_common/`, and `docker/`.

**`.env`.** Start from `.env.example`. Both hosts read `docker/.generated.env` for every variable
`.env` leaves empty, so on the local lane `.env` holds `ANTHROPIC_API_KEY` and little else. Write:

| Variable | From |
|---|---|
| `SHOPWARE_URL`, `SHOPWARE_ADMIN_URL`, `SHOPWARE_SALES_CHANNEL_ID`, `SHOPWARE_SALES_CHANNEL_ACCESS_KEY` | answers 1 and 2; `write_credentials.py` on the local lane |
| `UCP_TRANSPORT`, `SHOPWARE_ADMIN_TRANSPORT` | answers 3 and 4 |
| `UCP_AGENT_PROFILE_URL`, `UCP_AGENT_SIGNING_KEY_PEM_FILE` | answer 4; the profile URL is the one the shop fetches, not the one the browser sees |
| `STOREFRONT_API_PUBLIC_URL`, `WEB_APP_URL` | how the browser reaches the host and the UI (ticket URL, OAuth redirect, CORS) |
| `COMMERCE_AGENTS_HANDOFF_SECRET` | the bootstrap, or the value set in the plugin's configuration; at least 32 bytes |
| `SHOPWARE_INTEGRATION_ACCESS_KEY`, `SHOPWARE_INTEGRATION_SECRET_KEY` | `merchant_identity.py`, or pasted per answer 8 |
| `MERCHANT_REQUIRE_HOST_APPROVAL=1`, `MERCHANT_OPERATOR`, `MERCHANT_LEDGER_DSN` | answer 7 |
| `SHOPWARE_UCP_OAUTH_CLIENT_ID`, `SHOPWARE_OAUTH_REDIRECT_URI` | answer 6, only with Identity Linking |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` | answer 9 |

`SHOPWARE_ADMIN_USERNAME` and `SHOPWARE_ADMIN_PASSWORD` are read by the bootstrap scripts only;
the merchant host refuses the password grant (`load_settings` in `merchant/api/agent_config.py`).
`.env` and `docker/.generated.env` stay out of version control.

**Rules for what is generated or changed.**

- The two backend classes keep the blueprint contracts; a Shopware surface that lacks a figure
  returns `None` with a `note` (traffic, conversion) and a store-wide gap goes in
  `get_merchant_context().limitations`; nothing is invented (shopware-admin-mcp skill).
- Ids are Shopware's 32-character hex UUIDs, passed through unchanged; a family (parent) row is
  never a cart or price target, its child is (shopware-variants skill).
- Cart writes send the whole line list, because UCP `cart.update` replaces it; the cart id is the
  Store API context token and never reaches the model or a URL (shopware-ucp-mapping skill).
- No checkout completion: `complete_checkout` has no code path; the handoff code is minted on the
  host and consumed once by the plugin (shopware-identity-and-handoff skill).
- Every `stage_*` builds the exact payload, previews it with `dryRun=true`, and stores it with the
  change; `apply_change` replays it with `dryRun=false` and is the only live write
  (shopware-admin-mcp and shopware-promotions skills).
- Disclosure rows are server-authored from Store API fields and fixed copy; the model never writes
  a price, a delivery time, or a VAT line (shopware-compliance-de skill).
- Every new UCP or Admin call gets a netless replay (`storefront/api/tests/replay.py`,
  `merchant/api/fake_admin.py`) so `pytest` runs without Docker.
- Skills are the vendored copies, unchanged; prompt, tools, and index are built once per process.

## Step 4: Verify and hand off

1. `ruff check . && ruff format --check . && pytest -q` (netless).
2. `/shopware-ucp-doctor` against the configured shop: discovery, one active shop key, the agent
   profile fetched from inside the shop, the MCP handshake, the Admin MCP allowlist, the handoff
   round trip.
3. Start each host (`uvicorn storefront.api.main:app --port 8004`,
   `uvicorn merchant.api.main:app --port 8005`) and read `GET /api/health` and
   `GET /api/merchant/health`. Shopping: `POST /api/session`, then `GET /api/cart` shows
   `checkout_url` and a `cart_id` only after a first add. Merchant: `GET /api/merchant/dashboard`
   with the session header answers with figures from aggregations.
4. `python storefront/scripts/smoke.py` (signed, MCP and REST, handoff round trip) and
   `python merchant/scripts/smoke_live.py --read-only`; `--write` runs reversible round trips
   only when the user says so.
5. With an API key, one chat turn per host and the reply shown; a turn with cache reads on the
   second message proves the prompt bytes are stable.
6. Grep the hosts: after session start, requests carry the session id alone; no request field or
   tool argument names a customer, an operator, a token, or a cart id.
7. Print what to do next:
   - commit the scaffold with the decision record; confirm `.env`, `docker/.generated.env`, and
     `secrets/` are ignored;
   - `/add-shopware-flow` for the first flow, then `/author-shopware-evals` once a flow works;
   - read `docs/security.md` and the blueprint's `docs/safety.md` before exposing a host; switch
     `signature-policy` to `strict` and drop the profile development mode on any public shop.

Both agents: repeat 1 to 6 per agent.
