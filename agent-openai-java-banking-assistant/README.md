# Banking Assistant Threat-Lab (multi-agent Java banking copilot + external MCP for third-party agents)

A local, fully-Dockerized multi-agent banking assistant. A **supervisor** agent
routes each request to one of three domain agents — **Account**, **Transactions**,
and **Payments** — built on Langchain4j, which call business microservices through
**MCP** tools. The business APIs (account, payment, transaction history) ship with
in-memory mock data, so the stack runs end-to-end with no cloud dependency; the LLM
is **DeepSeek** (an OpenAI-compatible endpoint). There is no login — every request
runs as the default user **Bob** (`bob.user@contoso.com`, account `1010`, EUR).

A separate **external MCP** server lets an outside agent interact with the workspace
copilot: it can **plant invoices** (`submitInvoice`) and **record incoming payments**
(`receivePayment`) that the workspace agents later read and act on (look up the bill,
check history, pay it). That cross-agent path — attacker-controlled data entering the
copilot's context through tool results — is the project's indirect prompt-injection
surface. The two external tools are **decoupled** from the workspace Payments agent:
they live only on the external MCP server, which the copilot does not connect to.

## Architecture

```
  browser ──► frontend (nginx :80) ──/api──► copilot (Spring Boot)
                                               │  Supervisor agent (Langchain4j + DeepSeek)
                                               ├──► Account agent ─────┐
                                               ├──► Transactions agent ├─MCP(SSE)─► account / transaction / payment
                                               └──► Payments agent ────┘             (business microservices, mock data)

  external agent ──MCP(SSE :8086)──► external-mcp ──REST──► payment
                                      submitInvoice / receivePayment     (data lands in the shared stores the
                                                                          copilot reads via getInvoice / history)
```

- **frontend** — React + Fluent UI chat app (published on `:80`). Also serves an
  **Account** page (`/#/account`) showing balance, payment methods, beneficiaries,
  transaction history and receipts, plus a panel to submit an invoice and test the agent.
- **copilot** — the Spring Boot agent backend. Supervisor + Account/Transactions/Payments
  agents over Langchain4j, using DeepSeek as the chat model and MCP to reach the business APIs.
- **account / transaction / payment** — Spring Boot business microservices, each exposing a
  REST API and an MCP server. Data is in-memory mock (three accounts: `1000` Alice/USD,
  `1010` Bob/EUR, `1020` Charlie/EUR). `payment` is published on `:8083` for its REST API.
- **external-mcp** — a thin MCP server for outside agents (published on `:8086`). It exposes
  only `submitInvoice` and `receivePayment`, forwarding to `payment`'s REST so the data lands
  in the same stores the copilot reads from. The workspace copilot does **not** connect to it.

## How to Run

### Prerequisites

- Docker with the `docker compose` plugin
- A DeepSeek API key (or any OpenAI-compatible endpoint)
- Free ports `80` (web UI), `8083` (payment REST + MCP), `8086` (external MCP)

### 1. Configure

```bash
cp app/.env.example app/.env
```

Fill in your key — it is the only required value:

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com   # any OpenAI-compatible endpoint
DEEPSEEK_MODEL=deepseek-chat
```

`app/.env` is gitignored; keep your key out of the repo.

### 2. Start the stack

```bash
cd app
docker compose up -d --build
```

The first build downloads Maven and npm dependencies and takes a few minutes. The stack
is ready when:

| Service | Check |
|---|---|
| Web UI | `curl http://localhost/` returns 200 |
| Copilot | `curl -s -XPOST http://localhost/api/chat -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}],"stream":false}'` returns JSON |
| External MCP | `curl -s -o /dev/null -w "%{http_code}" -H 'Accept: text/event-stream' http://localhost:8086/sse` returns 200 |

> If you rebuild or restart a business service (account / payment / transaction), restart
> `copilot` afterwards (`docker compose restart copilot`) so it re-establishes its MCP
> connections — otherwise its cached MCP sessions go stale (404).

### 3. Talk to the assistant

Open <http://localhost> (no login). Try:

```
What is my account balance?
Show me my last transactions
I want to pay the bill with invoice id INV-2026-0098142
```

The **Account** page at <http://localhost/#/account> shows the account details and
transaction history, and has a panel to submit an invoice and send a prompt to the agent.

### 4. Connect an external MCP client (as an outside agent)

The `external-mcp` server speaks MCP over SSE at `http://localhost:8086/sse` (message
endpoint `POST /mcp/message?sessionId=...`). It exposes two tools:

| Tool | Effect |
|---|---|
| `submitInvoice` | Register an invoice (seven fields: `InvoiceId`, `VendorName`, `InvoiceTotal`, `InvoiceDate`, `VendorAddress`, `CustomerName`, `CustomerAddressRecipient`; extra fields are preserved). The copilot can then look it up with `getInvoice` and pay it. |
| `receivePayment` | Record an incoming payment/transfer into an account. It appears in that account's transaction history. |

Whatever an external agent plants this way is read back by the workspace copilot as tool
results — the cross-agent interaction (and injection) surface.

## Notes

- Mock data lives in memory and resets when a business service restarts.
- There is no authentication; the business REST APIs and the external MCP server accept
  unauthenticated calls (this is a local lab, not a production deployment).
- Invoice "recognition" is supplied as structured data via the REST/MCP API, not real OCR.
