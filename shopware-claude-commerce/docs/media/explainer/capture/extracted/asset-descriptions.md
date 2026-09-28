# Asset inventory

Captured from the live reference deployment on 2026-09-03 (no vision key; descriptions written from DOM context and manual inspection). All screenshots are 1920x1080 PNG, device scale 1.

## Screenshots (`capture/assets/`)

- `ui-grid-cart.png` — Reference shopping UI (`http://localhost:3005`), light theme. Left/center: blue hero banner "Storefront — Shop with an assistant that knows the store." above a 4-column product grid (7 real Shopware products: Claude Commerce T-Shirt €29.99, Extra Virgin Olive Oil 500 ml €12.90, Main product €495.95, Main product with advanced prices €950.00, Main product with properties €19.99, Main product free shipping €20.00, Variant product €19.99; each card has a blue "+" add button). Right: the Assistant rail open with "Shopping assistant" intro card, four suggestion chips ("What's in the catalog?", "Find me a gift under 50 €.", "Compare your two most popular products.", "What's in my cart?"), the message box and the footnote "The assistant searches the live shop, edits the cart, and hands checkout to Shopware." Top bar: Storefront, Sign in, Activity, Cart 2, Assistant. Assistant rail spans approx. x 1500–1920.
- `ui-cart-open.png` — Same UI with the Cart drawer open on the right (approx. x 1540–1920, dimmed backdrop over the grid). Drawer shows "Cart · 2 items", line item "Claude Commerce T-Shirt €59.98 (€29.99 each) − 2 + Remove", "Subtotal €59.98", the blue primary button **"Check out on Shopware"** (approx. x 1555–1905, y 985–1025) and the footnote "Payment happens on the shop's own checkout page — nothing is charged here."
- `shopware-checkout.png` — The real Shopware 6.7 storefront ("Demostore") checkout register page reached through the handoff URL `/claude-commerce/continue?token=…`: heading "Shipping information", personal-details / address form on the left, on the right the "Summary" box (Total €59.98, Shipping €0.00, Grand total €59.98, Net €50.40, 19% VAT €9.58) and "Shopping cart" with the line item Claude Commerce T-Shirt, product number CA-TSHIRT, delivery period 04/09/2026–06/09/2026, quantity 2, €59.98. Proves the cart handed off by the agent is the same cart in Shopware. Cookie bar and Symfony debug toolbar removed before capture.

## Text / data (`capture/extracted/`)

- `ucp-discovery.json` — Live `GET http://localhost:8080/.well-known/ucp` response (pretty-printed): `ucp.version 2026-04-08`, service `dev.ucp.shopping` over transports `rest` (`/ucp/v1`), `mcp` (`/ucp/mcp`), `embedded` (`/ucp/embedded`); capabilities `dev.ucp.shopping.cart`, `.catalog`, `.checkout`, `.discount`, `.order`, `dev.ucp.common.identity_linking`; two ES256 `signing_keys`. Use as the source for the terminal `curl` beat.
- `visible-text.txt` — DOM text of the shopping UI.
- `tokens.json` — brand tokens; Shopware blue `#189EFF` present in the captured palette; Claude warm accent `#D97757` added from the brief.

## Not captured

- Assistant chat turns: the deployment has no `ANTHROPIC_API_KEY`, so the rail shows the credential notice instead of a reply. The "search → compare" beat therefore uses the real grid + rail screenshot with an HTML-built agent turn overlay (labelled as a recreation in the storyboard), never a fabricated screenshot.
- Merchant portal: this version ships the merchant host as an API only (`POST /api/merchant/changes/{id}/apply`), no web UI. The merchant demo beat is an HTML-built staged-change card using the real `StagedChange.items[] {target, field, before, after}` shape and the real route names.
- Shopware storefront home (`http://localhost:8080/`) is an empty demo catalogue; not useful as footage.
