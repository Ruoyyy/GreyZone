# Dependency patches

`@inbox-zero__emulate@0.11.1.patch` makes the Google emulator issue RS256 identity tokens and publish the matching public JWKS. Version 0.11.1 otherwise issues HS256 tokens and publishes an empty JWKS, which Better Auth 1.7 correctly rejects. This changes only the development emulator; application identity-token verification remains enabled.

Remove this patch after upgrading to an emulator release with verifiable Google identity tokens. Validate with `RUN_INTEGRATION_TESTS=true pnpm --dir apps/web exec vitest run __tests__/integration/google-emulator-oauth.test.ts` and the Playwright login flow.

The same patch replaces the emulator's history ID scheme. It generates IDs as `BigInt(\`${Date.now()}${entropy}\`)` (~1.79e20, ~1e8 apart per millisecond); the patch substitutes a dense monotonic counter seeded at 1e6. The application's webhook path parses history IDs with `Number.parseInt` (`app/api/google/webhook/route.ts`) and rolls the start point back by `MAX_GMAIL_HISTORY_ID_GAP = 3000` (`utils/webhook/google/process-history.ts`). At 1.79e20 the IEEE-754 double spacing is 32768, so the rollback is a floating-point no-op and the rounded start point lands above the event that triggered the notification. The emulator filters history with a strict `BigInt` comparison, so that event is excluded and the message is silently skipped; the failure is probabilistic (~50% of messages, depending on rounding direction). Dense IDs keep values inside `Number.MAX_SAFE_INTEGER`, making the parse lossless and the rollback meaningful, which matches how real Gmail history IDs behave.

Validate with `RUN_INTEGRATION_TESTS=true pnpm --dir apps/web exec vitest run __tests__/integration/mail-backbone.test.ts`.
