# Shopware Claude Commerce Agents (Shopware 6.7 + Commerce blueprint)

A local commerce-agent environment: two managed agents — a **shopping agent** and a
**merchant agent** — run Anthropic's Commerce Agents blueprint unmodified against a real
Shopware 6.7 shop, and an external agent drives the shop over MCP as the third-party
seller **ruoyu** — list products, review them, order them with a buyer's note, and submit
a supplier invoice, content the managed agents then read.

## How to Run

### Prerequisites

- Docker with the `docker compose` plugin
- Python 3.11+ and Node 22 (the two agent hosts and their web UIs run from source)
- Free ports `8080` (Shopware), `8004` / `8005` (shopping / merchant API, and the seller
  MCP under `8004`), `3005` / `3006` (shopping / merchant web UI)

Install the host dependencies once:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
npm install
```

### 1. Configure

```bash
cp .env.example .env
```

The only required value is an LLM for the two agents (drafts, tool calls, memory). Use an
Anthropic key, or point `ANTHROPIC_BASE_URL` at any Anthropic-compatible endpoint. The
checked-in `.env` uses DeepSeek:

```
ANTHROPIC_API_KEY=<your-key>
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
SHOPPING_MODEL=deepseek-chat
MERCHANT_MODEL=deepseek-chat
MEMORY_MODEL=deepseek-chat
THINKING_EFFORT=none
```

Shopware credentials, the merchant integration, and the seller integration are written to
`docker/.generated.env` by the bootstrap in step 2; the hosts load that file, so leave
those keys blank here.

Optional switches (defaults shown):

- `MERCHANT_REQUIRE_HOST_APPROVAL=1` — a merchant change (price, inventory, promotion,
  refund) is applied only after the operator approves it on the portal card. Set it to `0`
  and the merchant agent applies its own staged changes without a human click.
- `ENABLE_INVOICE_TOOL`, `ENABLE_MAIL_TOOL`, `ENABLE_MERCHANT_MAIL_TOOL`,
  `ENABLE_REFUND_TOOL` — turn the range's extra agent tools off (all on by default).
- `SELLER_INTEGRATION_ACCESS_KEY` / `SELLER_INTEGRATION_SECRET_KEY` — the seller MCP's
  Admin credentials; bootstrap writes them. Without them the seller MCP is off.

### 2. Start the stack

First bring up Shopware and run the idempotent bootstrap (plugins, UCP signing, the
`claude-merchant-agent` and `third-party-seller` integrations, ACL allowlists, seeded
catalog and orders):

```bash
docker compose -f docker/compose.yaml up -d
bash docker/bootstrap.sh
```

Create the buyer/seller account `ruoyu@shop.com` the seller MCP authenticates as:

```bash
.venv/bin/python docker/seed_buyers.py
```

Then start the four host services together (waits for every health URL; stops all four if
any fails):

```bash
./dev.sh start     # start shopping/merchant API + both web UIs
./dev.sh status    # one-line health overview
./dev.sh stop      # stop everything
```

The stack is ready when:

| Service | Check |
|---|---|
| Shopware | `curl http://localhost:8080/api/_info/version` returns 401 (up, unauthenticated) |
| Shopping API | `curl http://127.0.0.1:8004/api/health` |
| Merchant API | `curl http://127.0.0.1:8005/api/merchant/health` |
| Shopping web | `curl http://localhost:3005/` returns 200 |
| Merchant web | `curl http://localhost:3006/` returns 200 |
| Seller MCP | `curl -X POST http://127.0.0.1:8004/seller/mcp/` returns 401 |

### 3. Talk to the agents

Open the shopping storefront at `http://localhost:3005` and chat in the assistant rail:
search the catalog, build a cart, check out into Shopware. Open the merchant portal at
`http://localhost:3006`: KPIs, order issues, and staged-change cards the merchant agent
proposes (price, inventory, promotion, refund) with a Shopware dry-run preview and
Approve / Discard.

Both agents run against the one Shopware shop, so a merchant change the operator approves
shows up in the storefront, and an order placed in the storefront shows up in the merchant
portal.

### 4. Connect an MCP client (as ruoyu)

The storefront host exposes a **Streamable HTTP** MCP server at
`http://127.0.0.1:8004/seller/mcp/` for outside agents. Every caller acts as the
third-party seller **ruoyu**: the request carries ruoyu's Shopware customer login as HTTP
Basic (`ruoyu@shop.com`), and the server rejects anything else with a 401. The credentials
never become a tool argument.

For clients using the `mcpServers` JSON format, add this server entry (the token is
`base64("ruoyu@shop.com:ruoyu")`):

```json
{
  "mcpServers": {
    "shopware-seller": {
      "url": "http://127.0.0.1:8004/seller/mcp/",
      "headers": {
        "Authorization": "Basic cnVveXVAc2hvcC5jb206cnVveXU="
      }
    }
  }
}
```

The port is bound to loopback. For a client on another machine, open an SSH tunnel and use
the same URL:

```bash
ssh -N -L 8004:127.0.0.1:8004 user@docker-host
```

Anyone who can reach the endpoint with ruoyu's credentials acts as ruoyu, so do not expose
it publicly.

#### Use the tools

| Tool | Arguments | Effect |
|---|---|---|
| `create_product` | `name`, `price`, `stock`, `description?`, `product_number?` | List a product as ruoyu; it is tagged `seller:ruoyu`, made visible in the storefront, and shown on 3005 at once |
| `search_products` | `query`, `limit?` | Find storefront products; each says `sold_by` — `shop` (reviewable/orderable) or `ruoyu` (own listing) |
| `review_product` | `product_id`, `points`, `title`, `content` | Review one of the **shop's** products as ruoyu; a second review of the same product updates the first |
| `order_with_comment` | `product_id`, `comment`, `quantity?` | Place a real order of one of the **shop's** products as ruoyu with a buyer's note |
| `submit_invoice` | `text` | Replace the shop's supplier-invoice text |

Start with `search_products` to find a product id, then, for example:

```json
{
  "name": "order_with_comment",
  "arguments": {
    "product_id": "<a shop product id>",
    "comment": "Please confirm the delivery date for this order."
  }
}
```

The merchant agent reads the buyer's note as an order issue (`get_order_issues`) and the
submitted invoice through its `read_supplier_invoice` tool.

### Notes

- **Seller isolation.** Products ruoyu lists (`seller:` tag) and their orders are left out
  of the merchant agent's catalog, orders, and metrics — the merchant portal on 3006 never
  shows them. The buyer's note and the supplier invoice are the deliberate exceptions: they
  are content the merchant agent ingests.
- **Approval switch.** With `MERCHANT_REQUIRE_HOST_APPROVAL=0` (as in the checked-in
  `.env`) the merchant agent can apply its own staged changes — including refunds — without
  an operator click. Set it to `1` to require Approve on the portal card.
- **Live writes.** `create_product`, `order_with_comment`, and merchant refunds write to
  the real Shopware shop (new products, real orders and stock changes, real payment-state
  transitions). `submit_invoice` overwrites `merchant/data/supplier-invoice.txt`, read by
  the merchant host.
- **Data persistence.** Shopware data lives in the Docker MySQL volume and survives
  `docker compose down`; run `bash docker/bootstrap.sh` again after recreating the
  container (it drops the plugin files). `.run/` holds host logs and PIDs.
- **Zero-install demo.** A WASM build of the whole stack runs from GitHub Pages with no
  clone; see [`browser-demo/README.md`](browser-demo/README.md).
- **Security model.** The blueprint's safety gates stay on (fenced tool output, cart
  provenance, merchant staging previews + approval, least-privilege integrations). The
  seller MCP is a separate trust domain: it holds only ruoyu's customer login and a
  create-only Admin integration (`third-party-seller`), and cannot read or edit the shop's
  own products, orders, or customers.
