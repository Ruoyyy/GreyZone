---
workflow: product-launch-video
flow: automation
storyboard: no
message: "Shopware is the Commerce Operating System for the Agent Economy — the Anthropic commerce-agent blueprint runs on it unmodified."
destination: youtube-embed
aspect: 1920x1080
language: en
audience: developers, Shopware partners, Anthropic / agentic-commerce ecosystem
length: 90-150s
angle: narrative
narration: yes
VO_MODE: restructured
capture: yes
---

## Intent

An English explainer / launch video for `github.com/agentic-commerce-lab/shopware-claude-commerce`: on September 2, 2026
Anthropic published an open-source blueprint for commerce agents; one day later this is Shopware's
implementation — running. Not "a Claude shop" — Shopware as the commerce execution layer any
agent runs on. Sell-and-show: the story is conceptual (architecture, safety, advantages) and the demo
beats feature the real captured screens of the reference storefront (`http://localhost:3005`) and the
Shopware storefront / UCP discovery (`http://localhost:8080`). Modern, dark, technical — terminal /
diagram aesthetic.

## Assets

- capture of http://localhost:3005 — shopping UI (product grid, cart drawer, assistant rail, "Check out on Shopware" button); demo beat footage.
- capture of http://localhost:8080 — Shopware storefront; checkout handoff beat.
- http://localhost:8080/.well-known/ucp — UCP discovery JSON; rendered as a terminal `curl` snippet.

## Customizations

- Kinetic typography on the key lines (hook date line, "the model proposes, a person applies", the closing tagline).
- Animated architecture diagram (Claude → blueprint packages → thin Shopware backends → Shopware surfaces: `/.well-known/ucp`, `/ucp/mcp`, `/api/_mcp`).
- Short terminal / code snippets: `docker compose up`, `curl /.well-known/ucp`, the five-command quick start.
- English voiceover via media-use TTS; captions on.
- Music bed: subtle, license-clean, technical / electronic.

## Notes

- Brand: Shopware blue `#189EFF` as the primary accent; Claude / Anthropic warm `#D97757` sparingly as the secondary accent; clean sans type.
- Story beats (in order): hook (Sept 2, 2026 blueprint; one day later this is Shopware's implementation — running) → the idea (execution layer, not a Claude shop) → two agents (shopping: search → compare → real cart → handoff to Shopware checkout; merchant: analyze → stage → human approves → apply) → wiring (blueprint pinned + unmodified → thin backends → UCP for shoppers, Admin API / MCP with server-side dry-run previews for merchants) → three Shopware advantages (native UCP, dry-run previews from the core, commerce semantics in the core) → safety (model proposes, person or policy applies; provenance gates; checkout completes in Shopware, never in the agent) → demo beats (search → add to cart → checkout on Shopware; staged price change → approve → applied) → close ("Shopware is the Commerce Operating System for the Agent Economy", repo `github.com/agentic-commerce-lab/shopware-claude-commerce` on screen — not narrated, five-command quick start).
- Sources: MASTERPLAN.md §0, §1.1, §2.3, §4.1, §4.2, §6; README.md; progress.md; docs/shopware-mapping.md.
- Deliverables: rendered MP4 at `docs/media/explainer.mp4` (1080p H.264), poster PNG `docs/media/explainer-poster.png`, `docs/media/README.md` with re-render commands and script.
- Autonomous run: no board, no checkpoint questions; visible decisions with reasons instead.
