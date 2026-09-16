# Promptfoo → Gitea issue/comment (via MCP)

Maps Promptfoo redteam attempts onto Gitea through an **existing** scenario MCP:

| Promptfoo | Gitea |
|-----------|--------|
| One attempt (one attack session) | One new issue |
| Turn 1 prompt | Issue body |
| Turn 2+ prompts (multi-turn) | Comments on that issue |
| `sessionId` | Issue number |
| Provider `output` | Agent reply for that turn |

```text
promptfoo redteam
  → mcp_target_provider.mjs
    → MCP create_issue | comment_on_issue
    → MCP wait_for_agent_reply
  → Gitea / Letta agent
```

## Layout

```text
redteam/
  promptfooconfig.yaml          # CLI entry (targets + redteam)
  package.json
  shared/js/
    mcp_session.mjs             # stdio / HTTP MCP client
    mcp_target_provider.mjs     # issue/comment mapping provider
  fixtures/mock_issue_mcp.mjs  # local smoke double (not production MCP)
  scripts/smoke_provider.mjs
```

This package does **not** implement the real Gitea MCP server. Point `targets[].config.mcp` at yours.

## Setup

```bash
cd redteam
npm install
```

### Point at your scenario MCP

Edit `promptfooconfig.yaml`:

```yaml
config:
  mcp:
    command: python3          # or node / uv run ...
    args: ['/path/to/your/mcp_server.py']
    cwd: ../..
    env: [GITEA_URL, GITEA_USER, GITEA_PASSWORD, GITEA_REPO]
  # or: url: 'http://127.0.0.1:PORT/mcp'
  tools:
    createIssue: create_issue       # rename to match your server
    comment: comment_on_issue
    wait: wait_for_agent_reply
  result:
    issueField: issue_number        # create_issue response field
    outputField: reply              # wait_for_agent_reply response field
```

Expected tool shapes (names configurable):

- `create_issue({ title, body })` → `{ issue_number, ... }`
- `comment_on_issue({ issue_number, body })` → ok
- `wait_for_agent_reply({ issue_number })` → `{ reply, failed?, ... }`

By default the provider prepends `@letta-code` to bodies when missing (`ensureMention`). Set `ensureMention: false` if the MCP already does that.

## Run

Smoke (mock MCP, no Gitea):

```bash
npm run smoke
```

Redteam (after wiring real MCP + stack):

```bash
# Serial: local runner capacity is 1
npx promptfoo@latest redteam run -c promptfooconfig.yaml -j 1 --delay 5000
npx promptfoo@latest redteam report
```

Or `npm run redteam` (same flags).

### Multi-turn

`crescendo` is configured with `stateful: true`. The provider returns `sessionId` = issue number so later turns comment on the **same** issue. `transformVars` seeds a UUID per test so the first turn always creates a **new** issue (UUID does not match `sessionIdPattern: '^[0-9]+$'`).

## Docs

- [Custom Provider](https://www.promptfoo.dev/docs/providers/custom-api/)
- [Red team configuration](https://www.promptfoo.dev/docs/red-team/configuration/)
- [Multi-turn strategies](https://www.promptfoo.dev/docs/red-team/strategies/multi-turn/)
- [Session management](https://www.promptfoo.dev/docs/red-team/troubleshooting/multi-turn-sessions/)
