---
format: 1920x1080
duration: 150s
message: "Shopware is the Commerce Operating System for the Agent Economy — the Anthropic commerce-agent blueprint runs on it unmodified."
arc: Future Pacing with feature-benefit progression — hook (the moment) → name the idea → the two agents → mechanism (wiring, discovery) → advantages → safety → proof (two demos) → CTA
audience: developers, Shopware partners, the Anthropic / agentic-commerce ecosystem
mode: autonomous
music: dark minimal technical electronic underscore, steady pulse, no drums until late, confident
language: en
---

## Video direction

- **Palette system (from `frame.md`, inverted register):** ground = `cream` `#0B1220` on every frame (painted as a full-duration background clip); half-step surfaces `tile` `#111A2E` / `tile-strong` `#1B2740` for cards and panels; text = `ink` `#F3F6FA`, secondary text `muted` `#8A94A6`; the ONE voltage per frame = `coral` = Shopware blue `#189EFF` (a rule, an underline, a kicker ✱, a CTA, or one highlighted word); `warm` `#D97757` only marks the Claude / Anthropic / blueprint side of a diagram or one word — never a fill, never beside the blue on the same element. Terminal / code surfaces sit on `navy` `#060B16` with a `navy-elev` `#141F36` title bar and an ink@14% hairline. Syntax: keys in `ink`, strings teal `#5DB8A6`, numbers amber `#E8A55A`, success `#5DB872`. Hairline 1px ink@12% is the only border; one soft dark shadow max on floating windows. A faint 1px grid (ink@4%, 96px cells) may sit on the ground for the terminal/diagram aesthetic — static, never animated.
- **Type by role:** `display` / `headline` Inter 400 sentence case, negative tracking, for every statement; `kicker` JetBrains Mono uppercase 0.16em with the blue ✱ opens each frame's region; `code` JetBrains Mono for terminals, endpoints, JSON, repo names; `body` / `lead` Inter 400. Weights 400 and 700 only. Legibility floor 1.4cqw for load-bearing text.
- **Motion grammar + reveal model:** long-tail `power3` settles (`expo.out` on a fast arrival); no overshoot, no bounce. Every frame reveals on the VO: at t=0 only what the voice is saying; each further element enters on its spoken cue, most of them in the back half; the final window is a held read. During a hold: stillness, or subtle jitter / a live SVG internal at most — no breathing, no back-half pan or push. Entrances are `fromTo`; internal seams are velocity-matched cuts (`cut-catalog.md`). Between-frame transitions are story's `transition_in`, injected by the harness.
- **Rhythm / held frames:** Frame 2 (statement) and Frame 7 (safety relay) are the deliberate held reads — few elements, long holds; Frame 12 holds its lockup for the tail. Frames 4, 5, 8, 9, 10, 11 are the dense, continuously developing shots.
- **Live-capture slots (v3 final):** Frame 8 (`#f08-slot-chat`) and Frame 9 (`#f09-slot-portal`) are filled with real captures taken 2026-09-03 (`data-slot-state="live"`): `localhost:3005` assistant turn ("I'm looking for a t-shirt in size M" → card → Add to cart → cart drawer → Shopware checkout) and `localhost:3006` merchant portal (dashboard + briefing → "Raise the price of the olive oil to 13.90 €" → staged card from Shopware's dry-run preview → Approve → applied). The draft's HTML recreations were replaced **without changing the frame durations or cue times** — the voice is locked.
- **Caption band:** bottom ~17% (below y≈896px) stays clear on every frame; content lives in the top ~83%.
- **Negative list:** no pure white / pure black grounds, no purple-blue "AI" gradients, bokeh, glows over 0.45 opacity, or decorative shapes standing in for a real asset; no browser chrome, real cursors, scrollbars, nav bars; no slideshow (front-load then freeze) and no screensaver (elements floating independently); no `repeat: -1`, no `Math.random`, no CSS transitions on animated elements; no fabricated numbers — every figure on screen traces to the repo or the captured shop (prices, totals, endpoint names, capability names, version `2026-04-08`).

## Frame 1 — The moment

- scene: Kinetic date line — "September 2, 2026" — then three claims stack: Anthropic publishes the blueprint · One day later. · "This is Shopware's." with a green `● RUNNING` status tag.
- voiceover: "September 2nd, 2026. Anthropic publishes an open-source blueprint for commerce agents. One day later — this is Shopware's implementation. Running."
- duration: 11.584s
- transition_in: cut
- status: animated
- src: compositions/frames/01-the-moment.html
- type: hook
- persuasion: Urgency by association — a dated industry moment the viewer already knows, resolved on "this is ours"
- beat: tension → intrigue
- blueprint: kinetic-type-beats (Adapt)
- focal: (typography-only)
- roles: none
- sfx: none

narrativeRole: Plant the stakes in outcome language (a race is on; Shopware answered) before any technology appears.
keyMessage: The blueprint is real, the race is on, and Shopware has an implementation.

Adapt: keep the signature — the words ARE the motion, each claim landing as its own full-frame beat by hard cut — but the beats stack into a left-aligned ledger instead of swapping in place, so the three claims can be read together when the payoff lands.
Scene 1 (0.0–1.6s): ground only, faint static grid. A mono kicker `✱ 2026-09-02` sits upper-left; the date "September 2, 2026" enters as `display` via **per-word staggered reveal** (`waterfall-entry`, word granularity) with a long-tail settle — left-aligned, upper third, ~60% width; nothing else on screen.
Scene 2 (3.2–7.3s): as the voice says "Anthropic publishes", the date shrinks to `headline` scale and rises (a velocity-matched slow-fast-slow nudge, power3); line 1 "Anthropic publishes an open-source blueprint for commerce agents." arrives as a **waterfall entry** (`waterfall-entry`) beneath it, `headline` size, with a small `warm` word-mark tag "anthropics/commerce-agents" in mono to its right — the only warm element of the frame.
Scene 3 (7.3–8.6s): on "One day later", line 2 "One day later." waterfalls in under line 1 (`waterfall-entry`); a thin ink@20% **section rule draws on** between the lines (`svg-path-draw`). Ledger layout, asymmetric 70/30, the right 30% empty.
Scene 4 (8.6–11.6s): on "this is Shopware's implementation", lines 1–2 dim to `muted`; line 3 "This is Shopware's." slams in at `display` scale (a single scale-slam arrival via `waterfall-entry`, no overshoot) and a **blue underline draws on** beneath "Shopware's" (`svg-path-draw`, `coral`) — the frame's one voltage. On "Running." a small mono status tag `● RUNNING` (success green `#5DB872`) lands to the right of the underline. Hold still to the cut.

## Frame 2 — Not a Claude shop

- scene: A single statement builds: "Not a Claude shop." strikes through → "Shopware is the commerce execution layer any agent runs on." Blue underline draws on under "execution layer".
- voiceover: "This is not a Claude shop. It's Shopware as the commerce execution layer — the system any agent runs on."
- duration: 7.275s
- transition_in: crossfade
- status: animated
- src: compositions/frames/02-execution-layer.html
- type: product_intro
- persuasion: Negative contrast — reject the obvious framing, then name the real one
- beat: clarity
- blueprint: kinetic-type-beats (Reproduce)
- focal: (typography-only)
- roles: none
- sfx: none

narrativeRole: Land the message (the brief's `message`) by beat 2; everything after is evidence.
keyMessage: Shopware is the execution layer, not another chatbot storefront.

Held read. Two statements, centered, one strike-through, one underline.
Scene 1 (0.0–2.4s): ground + grid. Kicker `✱ THE IDEA`. "Not a Claude shop." enters centered as `display-italic` via **per-word staggered reveal** (`waterfall-entry`, word granularity); on "not" a **strike-through line draws on** through the words in `muted` (`svg-path-draw`) — rejection read.
Scene 2 (2.4–5.6s): the struck line **nudges** up (slow-fast-slow, power3) and dims to `muted` at `headline` size; as the voice says "Shopware as the commerce execution layer", the statement "Shopware is the commerce execution layer" arrives centered at `display` scale via **waterfall entry** (`waterfall-entry`); centered template, ~70% width, upper-middle.
Scene 3 (5.6–8.0s): on "any agent runs on", the tail "— any agent runs on." waterfalls in as the second line; a **blue underline draws on** under "execution layer" (`svg-path-draw`, `coral`). Hold, still.

## Frame 3 — Two agents

- scene: Two columns open like a book: SHOPPING AGENT (search → compare → real cart → hand off to Shopware checkout) and MERCHANT AGENT (analyze → stage → human approves → apply). Steps light up as the voice names them — fast.
- voiceover: "Two agents. Shopping: search, compare, a real cart, hand off to Shopware checkout. Merchant: analyze, stage, a human approves, apply."
- duration: 9.323s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/03-two-agents.html
- type: feature_showcase
- persuasion: Rule of two — two complementary capabilities weighed at once
- beat: clarity + control
- blueprint: comparison-split (Adapt)
- focal: (typography-only)
- roles: none
- sfx: none

narrativeRole: Introduce the two products the repo ships, each as a four-step loop the viewer can hold in mind.
keyMessage: One repo, two agents: shopper-facing and merchant-facing, both ending in Shopware.

Adapt: two equal-weight cards entering from opposite wings with mirrored book-open tilts; the cards' four steps reveal one by one on the VO; no float after the settle. Tightened to the shorter line: the headline is "Two agents. One repo." and both cards open faster.
Scene 1 (0.0–0.8s): ground + grid; kicker `✱ TWO AGENTS`; headline "Two agents. One repo." enters upper-third via per-word staggered reveal, then nudges up-left to `card-title` scale.
Scene 2 (0.8–1.5s): on "Shopping", the LEFT card slides in from the left wing with a rotateY tilt and settles flat; kicker `✱ SHOPPING AGENT`, sub-label `storefront/api · UCP over MCP`.
Scene 3 (1.5–5.0s): four rows on the cues — search @1.53 · compare @2.21 · a real cart @2.83 · hand off to Shopware checkout @3.57 — mono index 01–04, a thin tick between rows; row 04 carries the frame's one blue ("Shopware").
Scene 4 (5.0–6.7s): on "Merchant", the RIGHT card enters from the right wing with the opposite tilt — kicker `✱ MERCHANT AGENT`, sub-label `merchant/api · Admin MCP`.
Scene 5 (6.7–9.0s): rows on the cues — analyze @6.72 · stage @7.16 · a human approves @7.83 (pill `approval gate`) · apply @8.86.
Scene 6 (8.9–9.3s): a section rule draws on across the bottom with the label `both end in Shopware`. Still.

## Frame 4 — How it's wired

- scene: Animated architecture diagram (MASTERPLAN §4.1, ADR-12 revised) draws top-down: Claude → `anthropics/commerce-agents` (pinned, unmodified) → `storefront/api` + `merchant/api` → the SHOPWARE band. MCP is first-class on both sides: `/ucp/mcp` (UCP over MCP: initialize → Mcp-Session-Id → tools/call) and `/api/_mcp` (Admin MCP, `dryRun=true` previews); `/ucp/v1/*` and `/api/*` are the REST fallback.
- voiceover: "Anthropic's blueprint packages — pinned, unmodified. Two thin Shopware backends. MCP on both sides: shoppers over UCP-MCP, merchants over the Admin MCP — dry-run previews computed on the server. REST is the fallback."
- duration: 16.277s
- transition_in: crossfade
- status: animated
- src: compositions/frames/04-wiring.html
- type: feature_showcase
- persuasion: Show-don't-tell mechanism — the real layer names and endpoints, drawn in the order the request travels
- beat: trust
- blueprint: spatial-pan-stations (Adapt)
- focal: (diagram, typography-only)
- roles: none
- sfx: none

narrativeRole: The mechanism beat: prove "runs unmodified" and land the ADR-12 news — MCP as the transport on both sides, REST as fallback.
keyMessage: Blueprint untouched on top, thin adapters in the middle, MCP into Shopware underneath.

Adapt: labeled stations on one locked canvas, revealed in traversal order by connectors drawing on top-down; each station reveals on its cue.
Scene 1 (0.0–1.3s): ground + grid; kicker `✱ HOW IT'S WIRED`; the TOP station "Claude" (Messages API · Agent SDK) with a `warm` dot — the frame's only warm.
Scene 2 (0.0–3.6s): on "Anthropic's blueprint packages" (@0.02) a connector draws into station 2: the wide card `anthropics/commerce-agents` with chips `shopping_agent · StorefrontBackend`, `merchant_agent · MerchantBackend`, `commerce_common`; on "pinned, unmodified" (@1.99) the badge `pinned @ fd4d5922 · 0 changes` pops at its right edge.
Scene 3 (3.6–5.6s): on "Two thin Shopware backends" (@3.60) two connectors draw in parallel into `storefront/api` (`ucp_client.py · shopware_backend.py · identity.py`) and `merchant/api` (`admin_transport.py · shopware_backend.py · staging.py`) under the mono label `github.com/agentic-commerce-lab/shopware-claude-commerce`.
Scene 4 (5.6–9.7s): on "MCP on both sides" (@5.64) both connectors draw into the SHOPWARE band (blue top rule — the frame's one blue), each connector tagged `MCP`; on "shoppers over UCP-MCP" (@7.52) the chip `/ucp/mcp` lights and a mono session strip `initialize → Mcp-Session-Id → tools/call` types beneath it; `/.well-known/ucp` sits to its left, unlit.
Scene 5 (9.7–14.7s): on "merchants over the Admin MCP" (@9.72) the chip `/api/_mcp` lights; on "dry-run previews computed on the server" (@11.92) the chip `dryRun=true` lights and the callout `preview computed server-side → StagedChange {before, after}` pops beside it.
Scene 6 (14.7–16.3s): on "REST is the fallback" (@14.72) two `muted` chips `/ucp/v1/*` and `/api/*` fade up with the tag `REST · fallback`; band label `Shopware 6.7 · SwagAgenticCommerce · core MCP` fades up. Hold.

## Frame 5 — One curl

- scene: Terminal surface. `curl -s http://localhost:8080/.well-known/ucp | jq .` types in; the live discovery JSON streams back — version, transports rest/mcp/embedded, the capability block, ES256 signing keys. Faster than v2: one breath.
- voiceover: "One curl to the well-known endpoint — and Shopware announces what it speaks, and the keys it signs with."
- duration: 6.528s
- transition_in: crossfade
- status: animated
- src: compositions/frames/05-curl-ucp.html
- type: feature_showcase
- persuasion: Show-don't-tell proof — real output from the running shop
- beat: confidence
- blueprint: prompt-type-submit-generate (Reproduce)
- focal: assets/ucp-discovery.json
- roles: ucp-discovery.json = supporting (source text for the streamed JSON; rendered as typed code, not embedded as a file)
- sfx: none
- asset_candidates: assets/ucp-discovery.json — live discovery document from the running Shopware (source text for the streamed JSON)

narrativeRole: One concrete, verifiable proof of "native UCP" — the viewer could run this line.
keyMessage: Discovery is live, standard, and signed.

Reproduce: the command types into a real terminal surface, the machine answers, the answer streams in.
Scene 1 (0.0–1.9s): ground + grid; terminal `code-surface` centered (~78% width); `$` prompt with a blinking caret; the command types on at a human pace and lands as "endpoint" is said (@1.85).
Scene 2 (1.9–2.7s): on "and Shopware announces" (@2.54) the JSON begins to stream line by line (binary reveals): version, the three transports.
Scene 3 (2.7–5.0s): on "what it speaks" (@3.76) the `capabilities` block streams in fast — catalog · cart · checkout · discount · order · identity_linking — every capability key highlighted `coral` as it lands (the block, not single words, is the cue now).
Scene 4 (5.0–6.5s): on "the keys it signs with" (@5.16) the body steps up to reveal `signing_keys` (ES256, P-256); the status strip `200 OK · application/json · ucp 2026-04-08 · signature-policy=log` lands. Caret blinks.

## Frame 6 — Three advantages

- scene: Three cards assemble left→right with mono kickers 01 · 02 · 03: NATIVE UCP (discovery · signatures · identity linking) — DRY-RUN PREVIEWS from the core (`dryRun=true` → before/after) — COMMERCE SEMANTICS in the core (promotions · rules · variants · price disclosures). Each card lands as named.
- voiceover: "Three things a bolt-on can't do: native UCP, dry-run previews from the core, and commerce semantics — promotions, rules, variants, price disclosures."
- duration: 10.752s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/06-three-advantages.html
- type: benefit_highlight
- persuasion: Rule of three + feature-to-benefit translation (structural advantages a competitor cannot bolt on)
- beat: confidence → aspiration
- blueprint: grid-card-assemble (Reproduce)
- focal: (typography-only)
- roles: none
- sfx: none

narrativeRole: Turn the mechanism into the "why Shopware" argument from MASTERPLAN §0.
keyMessage: UCP, dry-run, and commerce semantics are in the core — that is the differentiator.

Reproduce: N=3 cards self-assemble in a staggered cascade into a triptych and hold; each card's sub-items cascade in on the VO.
Scene 1 (0.0–2.1s): ground + grid; kicker `✱ THREE ADVANTAGES`; headline "What a bolt-on can't do." per-word reveal (0.1→1.5), then nudges up.
Scene 2 (2.1–3.6s): on "native UCP" (@2.25) card 01 rises into the left third; rows discovery · signatures (RFC 9421) · identity linking (OAuth) cascade quickly (0.3s apart).
Scene 3 (3.6–5.6s): on "dry-run previews" (@3.65) card 02 rises into the center; on "from the core" (@4.64) the row, the diff `before 29.99 → after 27.99` and the chip `dryRun=true` (the frame's one blue) cascade in.
Scene 4 (5.6–9.9s): on "commerce semantics" (@5.69) card 03 rises into the right column; rows on the cues — promotions @7.21 · rules @8.05 · variants @8.60 · price disclosures @9.34.
Scene 5 (9.9–10.7s): section rule + label `in the core — not bolted on`. Hold.

## Frame 7 — The model proposes

- scene: Kinetic statements on a bare ground: "The model proposes." / "A person — or a policy — applies." / "Provenance gates: only IDs this session has seen." / then a hardening ledger of three rows — signed requests (RFC 9421 + 9530), one-time handoff code (HMAC, ≤120 s, never a token in a URL), least-privilege Integration (ACL role) — plus a fourth, unspoken row "Identity Linking · UCP OAuth" / finale "Checkout completes in Shopware. Never in the agent."
- voiceover: "The model proposes; a person — or a policy — applies. Provenance gates accept only IDs the session has seen. Signed requests, a one-time handoff code, a least-privilege Integration. And checkout completes in Shopware — never in the agent."
- duration: 16.341s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/07-safety.html
- type: benefit_highlight
- persuasion: Risk reversal — name the failure modes the architecture makes impossible, then the hardening that backs it (MASTERPLAN §6, ADR-10/14)
- beat: peace of mind
- blueprint: kinetic-type-beats (Adapt)
- focal: (typography-only)
- roles: none
- sfx: none

narrativeRole: Address the objection every merchant has about agents: who is allowed to change what — and what stops a forged or replayed request.
keyMessage: Enforcement lives in the harness and in Shopware, not in a prompt; the transport is signed, the handoff is one-time, the merchant identity is least-privilege.

Adapt: a statement relay — each line lands alone at center by hard cut — with one inserted ledger beat: the three hardening items stack as mono rows instead of relaying, so they read together.
Scene 1 (0.0–1.4s): bare ground, no grid; kicker `✱ ENFORCED, NOT PROMPTED`; "The model proposes." lands at `display` on the cue (@0.27), scale-slam 1.08→1, no overshoot.
Scene 2 (1.4–4.2s): hard cut to "A person — or a policy — applies." (@1.46); on "policy" (@2.51) the chip `Rule Builder · Flow Builder · host approval` fades up beneath.
Scene 3 (4.2–7.7s): hard cut to "Provenance gates: only IDs this session has seen." at `headline` scale (@4.27); a hairline frame draws around it — a gate.
Scene 4 (7.7–13.1s): hard cut to the hardening ledger — three mono rows land on their cues: `signed requests · RFC 9421 + RFC 9530 · ES256` (@7.75), `one-time handoff code · HMAC-SHA256 · ≤ 120 s · never a token in a URL` (@9.15), `least-privilege Integration · ACL role claude-merchant-agent · Admin MCP allowlist` (@10.99); a fourth row `identity linking · UCP OAuth · PKCE` fades up `muted` at @12.3 (on screen only).
Scene 5 (13.1–16.3s): hard cut to the locked finale — "Checkout completes in Shopware." (@13.28) / "Never in the agent." (@15.23), "Never" in `coral`. Hold.

## Frame 8 — Demo: shop to checkout

- scene: Captured screens held as hero in a floating window: the product grid with the assistant rail showing the real turn (**live-capture slot `#f08-slot-chat`, live**: "I'm looking for a t-shirt in size M" → "We've got the Claude Commerce T-Shirt in size M, in stock for €29.99" + product card → Add to cart → "Added the Claude Commerce T-Shirt (M) to your cart — subtotal is now €29.99") → the cart drawer with "Checkout in Shopware" → cut to the real Shopware checkout showing the same cart (CA-TSHIRT-M ×1, €29.99).
- voiceover: "Watch it run. Search the live catalog. Add to a real Shopware cart. Then — Check out on Shopware. Same cart, same total — in the shop's own checkout."
- duration: 9.92s
- transition_in: crossfade
- status: animated
- src: compositions/frames/08-demo-shopping.html
- type: feature_showcase
- persuasion: Show-don't-tell proof on real screens
- beat: excitement → trust
- blueprint: device-surface-showcase (Adapt)
- focal: assets/ui-cart-open.png
- roles: ui-grid-reply.png / ui-grid-added.png = cutout (the floating window's first screen, live chat turn in two states) · ui-cart-open.png = cutout (second screen, the hero) · shopware-checkout.png = cutout (third screen, the payoff)
- sfx: none
- asset_candidates: assets/ui-grid-reply.png — storefront grid + assistant reply with the size-M card; assets/ui-grid-added.png — same, after "Add to cart" (subtotal €29.99); assets/ui-cart-open.png — cart drawer with Claude Commerce T-Shirt — M and "Checkout in Shopware"; assets/shopware-checkout.png — real Shopware checkout with the same cart (CA-TSHIRT-M, €29.99)

narrativeRole: The shopping proof: the loop from Frame 3 on real screens, ending in Shopware.
keyMessage: The cart the agent built is the cart Shopware checks out.

Adapt: a floating window held as hero while its screens cycle through a real flow, cursorless. The slot `#f08-slot-chat` is the window's first screen in two captured states (`ui-grid-reply.png` → `ui-grid-added.png`), same cues as the draft (@1.03 reply on screen, @2.9 the "Added" turn fades in). Camera: one push-in onto the cart drawer at the handoff, then a hard cut to the checkout screen.
Scene 1 (0.0–2.6s): kicker `✱ DEMO · SHOPPING AGENT`; the floating window pops in with `ui-grid-reply.png`; label `localhost:3005 · storefront/web`.
Scene 2 (2.6–5.4s): the reply with the size-M card is on screen for "Search the live catalog" (@1.03); on "Add to a real Shopware cart" (@2.75) a blue ring draws around the product card and `ui-grid-added.png` fades in ("Added … subtotal is now €29.99").
Scene 3 (5.4–8.6s): hard cut inside the window to `ui-cart-open.png`; zoom toward the drawer; on "Check out on Shopware" (@5.14) a blue ring draws around the "Checkout in Shopware" button — the frame's one blue.
Scene 4 (8.6–9.9s): on "Same cart, same total" (@6.65) inverse zoom-through to `shopware-checkout.png`; label swaps to `localhost:8080 · Shopware checkout`; callouts `Total €29.99 — same cart` (@7.70) and `CA-TSHIRT-M · quantity 1` (@8.54). Hold.

## Frame 9 — Demo: stage, approve, apply

- scene: The merchant proof on the real portal (**live-capture slot `#f09-slot-portal`, live**: `localhost:3006` window left — dashboard + assistant rail after the morning briefing → "1 change awaiting approval" → applied; the staged-change card from the rail pulled out as a zoom on the right — `Awaiting approval · 12,90 € → 13,90 € · SHOPWARE PREVIEW: CA-OIL 12.90 → 13.90 EUR · preview: server dry-run OK · Approve / Dismiss · Nothing applies until you approve` → `Approved · applied: wrote product · Approved by ops@example.com`). Transport chips under the window: `POST /api/_mcp · tools/call shopware-entity-upsert · dryRun=true → 0 writes` and `POST /api/merchant/changes/{id}/apply · 200 applied`.
- voiceover: "A price change is staged as a server-side dry run over the Admin MCP — before and after become the diff. Nothing touches the shop until someone approves. Then one call replays it."
- duration: 13.013s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/09-demo-merchant.html
- type: feature_showcase
- persuasion: Show-don't-tell of the approval gate itself — and of the dry run that produces the diff
- beat: control
- blueprint: agent-progress-theater (Adapt)
- focal: assets/portal-card-staged.png
- roles: portal-briefing.png / portal-staged.png / portal-applied.png = cutout (the portal window in three captured states) · portal-card-staged.png / portal-card-applied.png = cutout (the rail card zoom, the hero)
- sfx: none
- asset_candidates: assets/portal-briefing.png — merchant dashboard with KPI row, "Needs you today" and the assistant's morning briefing; assets/portal-staged.png — same, with "1 change awaiting approval" and the staged card in the rail; assets/portal-applied.png — after Approve; assets/portal-card-staged.png — the staged card (Awaiting approval, 12,90 → 13,90, Shopware preview, Approve/Dismiss); assets/portal-card-applied.png — the card after apply (Approved, applied: wrote product)

narrativeRole: The merchant proof: stage (server dry run) → diff → approve → replay, using the real tool, change shape and route names (docs/shopware-mapping.md, ADR-12).
keyMessage: Staging writes nothing — the dry run returns the diff; only an approved apply replays the payload for real.

Adapt: a trigger hands the frame to the machine, status theater, then a receipt — all on the real portal. Everything inside `#f09-slot-portal` is captured footage (portal window left, rail-card zoom right); the draft's cues were kept exactly. Real values from the live shop (CA-OIL 12.90 → 13.90 EUR); the price was reverted afterwards.
Scene 1 (0.0–1.9s): kicker `✱ DEMO · MERCHANT AGENT`; label `localhost:3006 · merchant/web · live capture`; the merchant's request `› Raise the price of the olive oil to 13.90 €` types on; the portal window (dashboard + briefing in the rail, `portal-briefing.png`) settles in.
Scene 2 (1.9–5.7s): on "server-side dry run over the Admin MCP" (@1.89) the transport chip lands under the window: `POST /api/_mcp · tools/call shopware-entity-upsert · dryRun=true → 0 writes`.
Scene 3 (5.7–8.0s): on "before and after become the diff" (@5.73) the window shows `portal-staged.png` ("1 change awaiting approval"); a lead line pulls the staged card out of the rail as a zoom (`portal-card-staged.png`): `12,90 € → 13,90 €`, `SHOPWARE PREVIEW · server dry-run OK — would write product, product_translation`.
Scene 4 (8.0–11.2s): on "Nothing touches the shop until someone approves" (@7.99) hold on the card — `Awaiting approval`, `Approve / Dismiss`, "Nothing applies until you approve." — with a slow push-in.
Scene 5 (11.2–13.0s): on "Then one call replays it" (@11.19) the second chip types `POST /api/merchant/changes/{id}/apply` → `200 applied`; the card flips to `portal-card-applied.png` (`Approved · applied: wrote product · Approved by ops@example.com`); the window shows `portal-applied.png`; the window's top rule turns `coral` — the frame's one blue. Hold.

## Frame 10 — Build your own

- scene: Terminal-style frame for the Claude Code plugin `shopware-commerce-builder`: three lines type on — `claude plugin marketplace add agentic-commerce-lab/shopware-claude-commerce` · `claude plugin install shopware-commerce-builder@shopware-claude-commerce` · `/scaffold-shopware-agent` — then two columns fill: FIVE COMMANDS (scaffold · add-shopware-flow · author-shopware-evals · review-shopware-agent · shopware-ucp-doctor) and SIX SKILLS (ucp-mapping · admin-mcp · promotions · variants · compliance-de · identity-and-handoff).
- voiceover: "Build your own with Claude Code: add the marketplace, install the plugin, run scaffold. Five commands, six Shopware skills, from UCP mapping to German compliance."
- duration: 11.669s
- transition_in: crossfade
- status: animated
- src: compositions/frames/10-claude-code-plugin.html
- type: feature_showcase
- persuasion: Friction reduction — the path from this repo to your own shop is three typed lines
- beat: empowerment
- blueprint: prompt-type-submit-generate (Adapt)
- focal: (terminal + typography)
- roles: none
- sfx: none

narrativeRole: Turn the reference into a tool: the plugin is how a partner brings their own Shopware agent onto this stack (plugins/shopware-commerce-builder/README.md).
keyMessage: Three commands to install, one to scaffold; the Shopware knowledge ships as six skills.

Adapt: the typed-command signature — a terminal, three lines typing in sequence with a caret — then the frame splits: the terminal demotes to the upper half and two chip columns cascade in beneath on the cues.
Scene 1 (0.0–2.3s): ground + grid; kicker `✱ BUILD YOUR OWN · CLAUDE CODE PLUGIN`; a terminal (`code-surface`, ~78% width) with title `claude — zsh`; headline chip `shopware-commerce-builder` at its right.
Scene 2 (2.3–6.3s): on "add the marketplace" (@2.28) line 1 types `$ claude plugin marketplace add agentic-commerce-lab/shopware-claude-commerce`; on "install the plugin" (@3.52) line 2 `$ claude plugin install shopware-commerce-builder@shopware-claude-commerce`; on "run scaffold" (@4.78) line 3 `> /scaffold-shopware-agent` with a blinking caret.
Scene 3 (6.3–7.5s): on "Five commands" (@6.36) the terminal demotes upward; a left column of five mono chips cascades in: `/scaffold-shopware-agent` · `/add-shopware-flow` · `/author-shopware-evals` · `/review-shopware-agent` · `/shopware-ucp-doctor`.
Scene 4 (7.5–11.7s): on "six Shopware skills" (@7.48) a right column of six chips cascades in: `shopware-ucp-mapping` · `shopware-admin-mcp` · `shopware-promotions` · `shopware-variants` · `shopware-compliance-de` · `shopware-identity-and-handoff`; on "UCP mapping" (@9.24) the first chip lights `coral`; on "German compliance" (@10.21) `shopware-compliance-de` lights — the two blues of the frame, one at a time. Hold.

## Frame 11 — Tested like software

- scene: Eval suite + CI on one canvas: headline "107 eval cases" with `64 shopping · 43 merchant`; a positive/negative pair (`shop-variant-001-family-without-size-asks` ↔ `shop-variant-002-…-adds-variant-id`, `negative_of`); four Shopware case rows light as named; then a CI strip: `ci.yml` (ruff · pytest · web builds · PHP) and the gates `core ≥ 0.90 · safety = 1.00 · cache-hit ≥ 0.80 · cost/turn ≤ 0.10 / 0.30`, with `integration.yml · nightly · Docker Shopware · smokes` in `muted`.
- voiceover: "Tested like software: a hundred and seven eval cases, every positive with a negative twin — family without a size, discard then apply refused, Grundpreis byte-exact, injection in a product description. CI gates pass rate, cache hits and cost per turn."
- duration: 17.643s
- transition_in: crossfade
- status: animated
- src: compositions/frames/11-evals-ci.html
- type: benefit_highlight
- persuasion: Credibility through method — deterministic scorers and a CI gate, not a demo that worked once
- beat: trust
- blueprint: grid-card-assemble (Adapt)
- focal: (typography + ledger)
- roles: none
- sfx: none

narrativeRole: Answer "does it keep working?" with the eval method (evals/README.md) and the two workflows (.github/workflows/ci.yml, integration.yml).
keyMessage: 107 snapshot cases with deterministic scorers, paired positive/negative, gated in CI on pass rate, cache-hit rate and cost per turn.

Adapt: a ledger assembles in three bands — the count, the pair, the four Shopware rows — then a CI strip lands at the foot; each element on its cue, binary reveals for the case ids.
Scene 1 (0.0–3.6s): ground + grid; kicker `✱ TESTED LIKE SOFTWARE`; on "a hundred and seven" (@1.56) the count `107` lands at `display` with `eval cases` beside it and the split `64 shopping · 43 merchant` in mono; a sub-line `snapshot state + one message → one real turn → deterministic scorers`.
Scene 2 (3.6–5.9s): on "every positive with a negative twin" (@3.67) a pair card: `shop-variant-001-family-without-size-asks` ✓ and `shop-variant-002-variant-chosen-adds-variant-id` with a `negative_of` arrow between them.
Scene 3 (5.9–13.8s): four case rows land on the cues, each with its id in mono and the expectation in `ink`: family without a size (@5.89, `asks which variant · cart unchanged`) · discard then apply refused (@7.05, `merch-approval-003 · change_not_applied`) · Grundpreis byte-exact (@9.14, `shop-disclosure-001 · byte_exact_disclosure`) · injection in a product description (@10.83, `shop-injection-003 · data plane fenced`).
Scene 4 (13.8–17.4s): on "CI gates" (@13.84) a strip lands at the foot: `ci.yml — ruff · pytest · web builds · PHP` and the gates lighting one by one — pass rate (@14.43: `core ≥ 0.90 · safety = 1.00`) · cache hits (@15.35: `≥ 0.80`) · cost per turn (@16.32: `≤ 0.10 / 0.30 USD`); a `muted` second line `integration.yml · nightly 03:17 UTC · Docker Shopware boots · smokes`. The lit gate values are the frame's blue. Hold.

## Frame 12 — Commerce Operating System

- scene: Statement lands — "Shopware is the Commerce Operating System for the Agent Economy." — then a roadmap row labelled IN PROGRESS: `SwagCommerceAgentTools` (Store API MCP tools · `swag_agent_staged_change` + `agent-change-*` · Flow Builder events); then the repo lockup `github.com/agentic-commerce-lab/shopware-claude-commerce` (on screen only) and a terminal pill types the five-command quick start. Holds on the lockup.
- voiceover: "Shopware is the Commerce Operating System for the Agent Economy. Next, in progress: SwagCommerceAgentTools, agent tools and staged changes inside Shopware. Repository link in the description. Five commands — and it runs."
- duration: 19.013s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/12-close.html
- type: cta
- persuasion: Friction reduction — the whole install fits on one screen; the roadmap says where it goes next
- beat: inevitability → motivation
- blueprint: prompt-type-submit-generate (Adapt)
- focal: (typography + terminal)
- roles: none
- sfx: none

narrativeRole: Land the thesis line verbatim, name the next step honestly (in progress), then hand over the concrete next action.
keyMessage: The tagline, the roadmap, the repo, five commands.

Adapt: the install-command end card — headline demotes, a terminal springs in, commands type and hold with a blinking caret — with a roadmap row inserted between headline and lockup.
Scene 1 (0.0–4.0s): ground + grid; kicker `✱ SHOPWARE × CLAUDE COMMERCE AGENTS`; the thesis enters centered at `display`, each word on its beat (Shopware @0.05 … Economy @3.20); "Commerce Operating System" carries the blue.
Scene 2 (4.0–11.7s): on "Next, in progress" (@4.09) the thesis nudges up to `headline`; a roadmap row pops beneath: pill `IN PROGRESS` (amber hairline) + `SwagCommerceAgentTools` + mono sub `Store API MCP tools · swag_agent_staged_change · agent-change-stage/apply/discard/list · Flow Builder events` (@4.9, sub @7.9).
Scene 3 (11.7–14.0s): on "Repository link" (@11.73) the mono lockup `github.com/agentic-commerce-lab/shopware-claude-commerce` with the pill `MIT` pops beneath the roadmap row.
Scene 4 (14.0–17.8s): on "Five commands" (@14.05) the terminal springs in; the five README commands type in cascade; the status line `→ shopping agent on :8004 · UI on :3005 · portal on :3006` fades up on "and it runs" (@15.13 → lands after the last command).
Scene 5 (17.8–19.0s): caret blinks; everything else still — the film's final held frame.
