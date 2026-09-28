# CommerceAgentsHandoff

Shopware 6.7 storefront plugin (namespace `CommerceAgents\Handoff`, version 1.1.0) that
continues an agent cart in the Twig checkout — ADR-10.

UCP carts are Store API context tokens. The shopping agent cannot set the `sw-context-token`
cookie on the shop origin and the token must never travel in a URL, so the storefront host mints
a **one-time, short-lived handoff code** (`shopware_common/handoff.py`,
`HandoffCodeIssuer`) and the shopper's browser posts it here.

## Routes

| Route | Purpose |
|---|---|
| `POST /claude-commerce/continue` (form field `code`) | primary — the host page auto-submits it |
| `GET /claude-commerce/continue?code=` | noscript fallback; the code never contains the raw token |

Route scope `storefront`, `csrf_protected: false` (cross-origin POST from the host page; the code is
the proof). Name: `frontend.claude_commerce.continue`.

## Code format (v1) and checks

```
code     = b64url(payload) "." b64url(HMAC-SHA256(mac_key, payload))
payload  = {"exp","iat","jti","tok","v":1}   compact JSON, sorted keys
tok      = b64url(nonce(12) || AES-256-GCM(enc_key, nonce, aad=jti, token) || tag(16))
enc_key  = HMAC-SHA256(secret, "commerce-agents-handoff:enc")
mac_key  = HMAC-SHA256(secret, "commerce-agents-handoff:mac")
```

`Service/HandoffCodeVerifier` mirrors `HandoffCodeVerifier.verify` in Python check for check:
constant-time MAC compare → JSON → `v === 1` → `0 < exp − iat ≤ 120` → `exp ≥ now` →
`iat ≤ now + 60` → `jti` is 32 hex → AES-GCM decrypt with `aad = jti` → token matches
`^[A-Za-z0-9_-]{16,128}$` → `jti` recorded once. PHP argument order:
`hash_hmac('sha256', $info, $secret)` (data second, key third) equals Python's
`hmac.new(secret, info)`. Test vectors for the secret `0123…cdef` ×4: `enc_key`
`fe15d237…c39b43`, `mac_key` `1c8b5115…7f1912`.

Single use: `commerce_agents_handoff_code (jti BINARY(16) PK, expires_at DATETIME(3))`,
created by `Migration1756900000CreateHandoffCodeTable`. `DatabaseConsumedCodeStore` purges
rows with `expires_at < now` and inserts the `jti`; a duplicate key means replay → refused.
`InMemoryConsumedCodeStore` has the same semantics for the tests.

## Controller flow

1. Logged-in customer (`$context->getCustomer() !== null`) → flash *danger*
   ("please log out or continue in your own cart"), redirect to `frontend.checkout.cart.page`.
   The code is not consumed, so the shopper can log out and retry.
2. Verify the code. Any failure → generic flash *danger*, `info` log line with the reason
   (never shown to the shopper), redirect to the cart page.
3. `$session->migrate()` (fresh session id — fixation defence), store the token under
   `sw-context-token` in the session, set the `sw-context-token` cookie (HttpOnly, SameSite=Lax),
   redirect to `frontend.checkout.confirm.page`.

## Configuration

`COMMERCE_AGENTS_HANDOFF_SECRET` (container `.env`, ≥ 32 bytes; `docker/bootstrap.sh` generates it
once with `openssl rand -hex 32` and writes the same value to `docker/.generated.env` for the host).
Resolved via `%env(default:...:COMMERCE_AGENTS_HANDOFF_SECRET)%`, so the container compiles without
it; the verifier throws `HandoffConfigurationException` on the first request to the route when the
secret is missing or too short — the rest of the shop keeps running.

Snippets: `Resources/snippet/{en_GB,de_DE}/commerce-agents-handoff.*.json`
(`commerceAgentsHandoff.invalidCode`, `commerceAgentsHandoff.loggedIn`).

## Tests

Standalone PHPUnit (no kernel, no database; `tests/bootstrap.php` registers the plugin's PSR-4
prefix on top of the shop's autoloader). Includes a code minted by the Python issuer
(`now=1800000000`, verified at `now=1800000010`), replay, expiry, future `iat`, tampered MAC and
payload, wrong secret, bad version, bad token alphabet, lifetime > 120 s, AAD mismatch, malformed
input, short secret.

```bash
docker exec -u www-data commerce-agents-shopware bash -lc \
  'cd /var/www/html && php vendor/bin/phpunit -c custom/plugins/CommerceAgentsHandoff/phpunit.xml'
```

Live round trip: `docker/verify.sh` (section 5, `docker/handoff_check.py`) and
`python storefront/scripts/smoke.py` (`handoff:` lines).

## Install

`docker/bootstrap.sh` copies this folder to `custom/plugins/CommerceAgentsHandoff`, runs
`plugin:install --activate` or `plugin:update` (migrations), and `cache:clear`. Do not bind-mount
the folder read-only over `custom/` — dockware `chown`s on boot. Uninstalling without
"keep user data" drops the table.
