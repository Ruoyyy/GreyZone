# `shopware_common`

Code both hosts share. Nothing here knows about the shopping or merchant agent; each module
is one protocol or contract the Shopware side dictates, used by `storefront/api` and
`merchant/api` alike. Tests live in `tests/` and reach no network.

| module | what it is | used by |
|---|---|---|
| `mcp_client.py` | MCP Streamable-HTTP client (spec 2025-06-18): `initialize` handshake, `Mcp-Session-Id`, `notifications/initialized`, `tools/list` (paged), `tools/call`, `resources/read`, `DELETE` to end the session, JSON and single-event SSE bodies, one re-initialise on a forgotten session, `MCP-Protocol-Version` echo. One in-flight request per session (Shopware's server answers a concurrent second request with an empty body) and collection of results the server parks behind `shopware://tool-result/<id>` (`_meta.resourceUri`). | `storefront/api/ucp_client.py` (`/ucp/mcp`), `merchant/api/admin_client.py` (`/api/_mcp`) |
| `http_signing.py` | RFC 9421 HTTP Message Signatures + RFC 9530 `Content-Digest` in the exact profile `ucp-php-sdk` verifies: components `@method @target-uri content-digest`, `created`/`expires`, `keyid`, `alg=ES256`, DER-encoded ECDSA. `RequestSigner`, `verify` (the reference the tests pin both ways), `public_jwk` / `key_id_for` (the `signing_keys` entry of `agent-profile.json`, `kid` derived from the key so bootstrap is idempotent), `signer_from_env` (`UCP_AGENT_SIGNING_KEY_PEM_FILE`, repo-relative). | `storefront/api/ucp_client.py`, `docker/agent_key.py`, `docker/ucp_signed_check.py` |
| `handoff.py` | The handoff-code contract of ADR-10: `HandoffCodeIssuer` mints a one-time, ≤ 120 s, HMAC-SHA256-signed code whose payload carries the Store API context token AES-256-GCM-encrypted; `HandoffCodeVerifier` is the Python reference of what the `CommerceAgentsHandoff` PHP plugin checks (signature, expiry, single use, decryption). `secret_from_env` reads `COMMERCE_AGENTS_HANDOFF_SECRET`. | `storefront/api/handoff.py`, `docker/handoff_check.py`, the plugin's PHP tests (same vectors) |
| `anthropic_client.py` | `build_anthropic_client`: the `AsyncAnthropic` both Messages API runtimes get, with the `anthropic-workspace-id` header from `ANTHROPIC_WORKSPACE_ID` when set (identity-linked keys are refused without it). `sdk_workspace_env`: the same header for the Agent SDK runtimes, which build no client — the Claude Code CLI reads `ANTHROPIC_CUSTOM_HEADERS` (`Name: Value` lines) from `ClaudeAgentOptions.env`, and this merges the workspace line into whatever pairs the operator already set. | `storefront/api/main.py`, `merchant/api/merchant.py`, `evals/backends.py`, `storefront/agent-sdk`, `merchant/agent-sdk` |
| `clock.py` | The session clock: `host_clock(request)` yields `{"timezone", "now"}` for a `ClockContext` — the browser's IANA zone from the `X-Timezone` header (or `tz` query parameter), else `HOST_TIMEZONE`, else `Europe/Berlin`; `now` is aware and in that zone. Unknown zones fall through to the default. | `merchant/api/portal.py`, `storefront/api/main.py`; the web apps send the header from `Intl.DateTimeFormat().resolvedOptions().timeZone` |

## Conventions

- Errors are typed (`McpError`, `McpSessionLost`, `McpToolError`, `HandoffCodeError`) and
  logged where they are raised; callers decide whether to fall back (the UCP client falls
  back from MCP to REST, the admin client does not).
- Secrets never enter log lines or error messages: a rejected handoff code reports the
  reason class, not the token; the signer exposes `kid`, never key material; the MCP
  client logs the session URL and the request id, not bodies.
- Constants over literals (`TIMEZONE_HEADER`, `DEFAULT_TIMEZONE`, `PROTOCOL_VERSION`, …);
  the contracts the Shopware side dictates are documented at the top of each module with
  the wire format.

## Tests

```bash
python -m pytest -q shopware_common/tests
```

`test_mcp_client.py` drives the client against an in-process fake server (handshake, SSE
bodies, session loss, concurrency, offloaded resources); `test_http_signing.py` pins the
`Signature-Input` profile, round-trips sign/verify, rejects body changes and expired
windows, and checks the deterministic `kid`; `test_handoff.py` pins the wire format with
the vector the PHP plugin tests share, single use, expiry and tampering;
`test_anthropic_client.py` checks the header; `test_clock.py` the zone resolution order.
