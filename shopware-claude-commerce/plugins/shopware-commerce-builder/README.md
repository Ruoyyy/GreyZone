# shopware-commerce-builder (Claude Code plugin)

Six skills and five commands for running Anthropic's shopping agent or merchant agent against a
Shopware 6 shop on the hosts in this repo ([`../../README.md`](../../README.md)), or bringing an
existing Shopware agent integration onto them. Skills and commands alike load when a conversation
matches their description, and a command can also be typed; the commands read the reference repo
(this checkout, a local clone, or a fresh one) while they run. The plugin runs no code of its own;
the shop-side setup it describes is the repo's own `docker/` helpers.

The blueprint's own rules (one model per conversation, cache-stable prompt bytes, fenced tool
results, provenance-gated writes, staged merchant changes under host approval, the eval method)
are Anthropic's [`commerce-builder`](https://github.com/anthropics/commerce-agents/tree/main/plugins/commerce-builder)
plugin and hold unchanged; this plugin states what Shopware adds. Installing both is the intended
setup.

## Install

```bash
claude plugin marketplace add agentic-commerce-lab/shopware-claude-commerce     # or the path of a local clone
claude plugin install shopware-commerce-builder@shopware-claude-commerce
```

## Commands

| Command | Does |
|---|---|
| [`/scaffold-shopware-agent`](commands/scaffold-shopware-agent.md) | Interviews you about the shop (URL, sales channel, lane, transports, agents), plays the plan back, sets up Integration, ACL role, UCP exposure, and signing key through the repo's bootstrap helpers, instantiates the hosts, writes `.env` |
| [`/add-shopware-flow <flow>`](commands/add-shopware-flow.md) | Adds one shopping or merchant flow: the vendored skill, the Shopware surfaces its tools need with their netless replays, and the first eval cases in the repo's YAML format |
| [`/author-shopware-evals`](commands/author-shopware-evals.md) | Extends the eval suite in `evals/`: the case shape, the scorers, the Shopware-specific first cases (base price byte-exact, delivery time from data, variant choice, family id held, price cap, apply without approval), the CI gate |
| [`/review-shopware-agent`](commands/review-shopware-agent.md) | Maps an existing Shopware agent integration row by row (transport, signing, identity, variants, cart, checkout, disclosures, merchant reads and writes, approval, evals), compares it with the reference, and converts the rows you pick |
| [`/shopware-ucp-doctor`](commands/shopware-ucp-doctor.md) | Diagnoses the shop's agentic surface: discovery, signing keys, allowlists, the agent profile fetched from inside the container, the MCP handshakes, the Admin MCP allowlist and ACL, the handoff round trip, Identity Linking |

The scaffold and the review write what they learned to your project's `CLAUDE.md` under
`## Shopware commerce agent decision record`; the other three commands read that section instead
of asking again, and the doctor appends its findings to it.

## Skills

| Skill | Rules for |
|---|---|
| [`shopware-ucp-mapping`](skills/shopware-ucp-mapping/) | Discovery, the MCP and REST transports, the UCP tools against `StorefrontBackend`, ids, cart replace semantics and the cart id as context token, the Store API gaps, signing and idempotency |
| [`shopware-admin-mcp`](skills/shopware-admin-mcp/) | The Admin MCP handshake and progressive discovery, the entity tools, `dryRun` previews into `StagedChange`, payload replay on apply, partial failures, the allowlist and ACL, aggregations for metrics |
| [`shopware-promotions`](skills/shopware-promotions/) | Promotion, discount, rule, and sales-channel binding; `PromotionDraft` to one nested payload; price updates per currency entry with net from tax; caps and floors |
| [`shopware-variants`](skills/shopware-variants/) | Parent and child, `configuratorSettings` and options, the Store API `parentId` reads, variant resolution for cart writes, sold-out siblings, price and stock inheritance |
| [`shopware-compliance-de`](skills/shopware-compliance-de/) | PAngV base price, delivery time, VAT wording, shipping hint, Widerruf; server-authored rows from Store API fields and fixed copy; policies from the shop's CMS pages; byte-exact evals |
| [`shopware-identity-and-handoff`](skills/shopware-identity-and-handoff/) | Integration and ACL least privilege, UCP Identity Linking, the signed one-time handoff code into the Twig checkout, where each token may and may not appear, never placing an order in the agent |

## Path

1. `/scaffold-shopware-agent`, then `/shopware-ucp-doctor` against the shop it configured; with an
   agent already running, `/review-shopware-agent` and the rows it converts.
2. `/add-shopware-flow` for each flow in your v1 index.
3. `/author-shopware-evals`, and re-run after every prompt, skill, backend, or fixture change.
4. Read [`../../docs/security.md`](../../docs/security.md) before exposing a host; switch the
   channel to `signature-policy=strict` and drop the profile development mode on any public shop.

## Validate

```bash
python plugins/shopware-commerce-builder/scripts/validate.py    # manifests, frontmatter, bodies, links
claude plugin validate --strict .                               # the marketplace manifest
claude plugin validate --strict plugins/shopware-commerce-builder
```

Authentication on the host routes, rate limits, memory retention, and log hygiene are the
deployment's; [`../../docs/security.md`](../../docs/security.md) lists them. Where this plugin's
text and the reference code disagree, the code is right.
