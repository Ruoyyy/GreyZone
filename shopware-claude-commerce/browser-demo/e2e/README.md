# End-to-end verification (Playwright)

`demo.spec.ts` drives the complete demo in one headless Chromium tab against the **local Node
server** (`server/index.mjs`): boot, the overlay, the shopping flow and the merchant flow, with
real Claude turns through the same-origin proxy.

```bash
npm run e2e                                   # starts `node server/index.mjs --dev` on :4189
DEMO_E2E_MODE=static npm run e2e              # serves dist/site (run `npm run build` first)
DEMO_E2E_BASE_URL=http://127.0.0.1:4188 npm run e2e   # reuse a server you already run
DEMO_E2E_NO_CHAT=1 npm run e2e                # boot + UI only, no model calls
npx playwright install chromium               # once, if the browser build is missing
```

The Anthropic key is read by the server from the repo `.env`; the model-driven steps are skipped
(not failed) when `/api/anthropic/status` reports `configured: false`.

| Test | Verifies |
|---|---|
| boots Shopware + both agents | `crossOriginIsolated`, storefront rendered in the frame, `#commerce-agents-demo` launcher visible and `ca-demo--ready`, both view tabs enabled; records the boot marks |
| overlay → shopping demo | Launcher entry opens the shopping view; catalog from the Store API; turn *"add the size M, White Variant product"* → cart badge `1 item`; the same line item on Shopware's `/checkout/cart` (shared context token); `Checkout in Shopware` → in-browser `/checkout/register` |
| overlay (administration) → merchant demo | Launcher in `/admin` opens the merchant view; dashboard (*Needs you today*, *Recent orders*) from Admin MCP; price change staged by chat with a Shopware dry-run preview → **Approve** → `applied:` and `product.price` = 11.90 in the WASM DB and on the storefront; a second change → **Dismiss** leaves the price untouched |

## Output

- `e2e/test-results/screenshots/01…12-*.png` — one screenshot per step (git-ignored)
- `docs/timings.local.json` — boot marks (ms since navigation) and turn durations of the last run
- `e2e/playwright-report/` — HTML report; traces are kept for failed tests only

Measured on an Apple-silicon laptop, server on localhost, empty browser profile (September 2026):
`engine-ready` ≈ 4.4 s, `storefront-rendered` ≈ 8.8 s, both agents ready ≈ 12.7 s; shopping turn
≈ 11 s, merchant staging turn ≈ 14 s, dismiss turn ≈ 8 s. The three tests take ≈ 75 s in total,
in dev and in static mode.

## Notes for maintainers

- Model turns are non-deterministic; prompts are deliberately unambiguous (size **and** colour)
  and the spec prints the visible transcript when a turn does not produce the expected effect.
- Approve/Dismiss wait for the composer to leave its *Working…* state first. The blueprint session
  store persists a turn's staged change ids when the turn ends; clicking during the stream is held
  by the provenance gate (*was not staged or listed in this session*).
- The per-test timeout is 12 min (a cold boot from an empty HTTP cache downloads ≈ 190 MB); the
  default expectation timeout is 60 s, long waits pass explicit timeouts.
