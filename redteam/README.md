# Promptfoo → Gitea issue/comment (via gitea-mcp)

Maps Promptfoo redteam attempts onto Gitea through the stack’s **gitea-mcp**
HTTP gateway (`http://127.0.0.1:8080/mcp`):

| Promptfoo | Gitea |
|-----------|--------|
| One attempt (one attack session) | One new issue |
| Turn 1 prompt | Issue body (`issue_write` `method=create`) |
| Turn 2+ prompts (multi-turn) | Comments (`issue_write` `method=add_comment`) |
| Wait for agent | Poll `issue_read` `method=get_comments` until `gitea-actions` posts finished/failed |
| `sessionId` | Issue `number` |
| Provider `output` | Cleaned bot reply body |

```text
promptfoo redteam
  → mcp_target_provider.mjs
    → HTTP MCP :8080/mcp
      → issue_write / issue_read
  → Gitea → Letta Actions agent
```

## Layout

```text
redteam/
  promptfooconfig.yaml          # points at real MCP URL by default
  package.json
  shared/js/
    mcp_session.mjs             # stdio or Streamable HTTP client
    mcp_target_provider.mjs     # issue/comment + poll wait
  fixtures/mock_issue_mcp.mjs  # same tool names for offline smoke
  scripts/smoke_provider.mjs
```

## Prerequisites

1. Stack up with MCP healthy (`docker compose up -d`, port **8080**):
   - See root [README.md](../README.md) § Connect an MCP client
2. Node 18+

```bash
cd redteam
npm install
```

## Config notes (real MCP)

Defaults in `promptfooconfig.yaml` match official tools:

- **Create**: `issue_write` + `method: create` → response field `number`
- **Comment**: `issue_write` + `method: add_comment` (gateway auto-prepends `@letta-code`)
- **Wait**: poll `issue_read` + `method: get_comments` for user `gitea-actions` containing `Letta Code finished` / `Letta Code failed`

Issue **creation** bodies still need `@letta-code` in the body (`ensureMention`); comment auto-prefix is handled by the patched MCP gateway.

## Run

Offline smoke (mock MCP, no Docker):

```bash
npm run smoke
```

Against the real gateway:

```bash
# Ensure http://127.0.0.1:8080/healthz is OK
npx promptfoo@latest redteam run -c promptfooconfig.yaml -j 1 --delay 5000
npx promptfoo@latest redteam report
```

Use `-j 1` so attempts stay serial (runner capacity is 1).

### Multi-turn

`crescendo` uses `stateful: true`. The provider returns `sessionId` = issue number so later turns comment on the **same** issue. `transformVars` seeds a UUID per test so the first turn always **creates** a new issue.

## Docs

- Root MCP client setup: [../README.md](../README.md)
- [Custom Provider](https://www.promptfoo.dev/docs/providers/custom-api/)
- [Red team configuration](https://www.promptfoo.dev/docs/red-team/configuration/)
- [Multi-turn strategies](https://www.promptfoo.dev/docs/red-team/strategies/multi-turn/)
