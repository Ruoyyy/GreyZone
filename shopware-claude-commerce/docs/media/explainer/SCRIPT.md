# SCRIPT — Shopware × Claude Commerce Agents explainer (github.com/agentic-commerce-lab/shopware-claude-commerce)

**Voice:** Kokoro `bm_george` (offline; British male, measured) — HeyGen not signed in
**Voice settings:** speed 1.0
**Voice direction:** Calm, technical, confident. Developer keynote, not an ad. Short pauses at the em-dashes; let the product names land cleanly.
**Runtime budget:** ≤ 150 s total (v3: twelve lines, ≈ 149 s including the 2.8 s closing hold).

---

## Line 1 — The moment (Frame 1)

**Time:** 0.0 – 11.6s
**Delivery:** Date as a headline. Two flat claims, then a small lift on "this is Shopware's implementation" and a clipped, final "Running."

    September 2nd, 2026. Anthropic publishes an open-source blueprint for commerce agents. One day later — this is Shopware's implementation. Running.

## Line 2 — Not a Claude shop (Frame 2)

**Time:** 11.6 – 18.9s
**Delivery:** Reject, then define. Weight on "execution layer".

    This is not a Claude shop. It's Shopware as the commerce execution layer — the system any agent runs on.

## Line 3 — Two agents (Frame 3)

**Time:** 18.9 – 28.2s
**Delivery:** Two lists, each read as four clean cues. Fast.

    Two agents. Shopping: search, compare, a real cart, hand off to Shopware checkout. Merchant: analyze, stage, a human approves, apply.

## Line 4 — How it's wired (Frame 4)

**Time:** 28.2 – 44.5s
**Delivery:** Top to bottom, like reading a diagram. "Pinned, unmodified" is the point; "MCP on both sides" is the news.

    Anthropic's blueprint packages — pinned, unmodified. Two thin Shopware backends. MCP on both sides: shoppers over UCP-MCP, merchants over the Admin MCP — dry-run previews computed on the server. REST is the fallback.

## Line 5 — One curl (Frame 5)

**Time:** 44.5 – 51.0s
**Delivery:** Matter-of-fact, one breath.

    One curl to the well-known endpoint — and Shopware announces what it speaks, and the keys it signs with.

## Line 6 — Three advantages (Frame 6)

**Time:** 51.0 – 61.8s
**Delivery:** Count them off. Small pause before each item.

    Three things a bolt-on can't do: native UCP, dry-run previews from the core, and commerce semantics — promotions, rules, variants, price disclosures.

## Line 7 — The model proposes (Frame 7)

**Time:** 61.8 – 78.1s
**Delivery:** Slower. Each sentence its own beat. The three hardening items are a list, not a sentence. "Never in the agent" is final.

    The model proposes; a person — or a policy — applies. Provenance gates accept only IDs the session has seen. Signed requests, a one-time handoff code, a least-privilege Integration. And checkout completes in Shopware — never in the agent.

## Line 8 — Demo: shop to checkout (Frame 8)

**Time:** 78.1 – 88.0s
**Delivery:** Narrate the screen. Lift on "Check out on Shopware".

    Watch it run. Search the live catalog. Add to a real Shopware cart. Then — Check out on Shopware. Same cart, same total — in the shop's own checkout.

## Line 9 — Demo: stage, approve, apply (Frame 9)

**Time:** 88.0 – 101.0s
**Delivery:** Even, procedural; "Nothing touches the shop" is the reassurance.

    A price change is staged as a server-side dry run over the Admin MCP — before and after become the diff. Nothing touches the shop until someone approves. Then one call replays it.

## Line 10 — Build your own (Frame 10)

**Time:** 101.0 – 112.7s
**Delivery:** Terminal pace. The command names are on screen — do not spell them.

    Build your own with Claude Code: add the marketplace, install the plugin, run scaffold. Five commands, six Shopware skills, from UCP mapping to German compliance.

## Line 11 — Tested like software (Frame 11)

**Time:** 112.7 – 130.1s
**Delivery:** The four case names are a list; "refused" and "byte-exact" are the stresses.

    Tested like software: a hundred and seven eval cases, every positive with a negative twin — family without a size, discard then apply refused, Grundpreis byte-exact, injection in a product description. CI gates pass rate, cache hits and cost per turn.

## Line 12 — Commerce Operating System (Frame 12)

**Time:** 130.1 – 149.1s
**Delivery:** The thesis, stated plainly. The roadmap line is labelled "in progress" out loud and on screen. The URL is on screen, not spoken. Leave air at the end.

    Shopware is the Commerce Operating System for the Agent Economy. Next, in progress: SwagCommerceAgentTools, agent tools and staged changes inside Shopware. Repository link in the description. Five commands — and it runs.
