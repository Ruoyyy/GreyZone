# Promptfoo → Gitea issue/comment (via gitea-mcp)

Maps Promptfoo redteam attempts onto Gitea through the stack’s **gitea-mcp**
HTTP gateway (`http://127.0.0.1:8080/mcp`):

| Promptfoo | Gitea |
|-----------|--------|
| One serial campaign | One shared issue |
| First attack prompt | Issue body (`issue_write` `method=create`) |
| Later attack and multi-turn prompts | Comments (`issue_write` `method=add_comment`) |
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
redteam/promptfoo/
  promptfooconfig.yaml          # template; points at real MCP URL by default
  scenario-config.yaml          # generated from the shared benchmark scenario
  package.json
  shared/js/
    mcp_session.mjs             # stdio or Streamable HTTP client
    mcp_target_provider.mjs     # issue/comment + poll wait
  fixtures/mock_issue_mcp.mjs  # same tool names for offline smoke
  scripts/smoke_provider.mjs
  scripts/smoke_live.mjs
```

## Prerequisites

1. Stack up with MCP healthy (`docker compose up -d`, port **8080**):
   - See root [README.md](../../README.md) § Connect an MCP client
2. Node 22.22.0+ (required by the pinned Promptfoo version)

`npm run redteam -- --scenario memory-poisoning` first generates the ignored
`scenario-config.yaml` from the selected benchmark objective and turn budget.
`npm run prepare:scenario -- --scenario memory-poisoning` does only this
generation and makes no model requests or issue writes.

```bash
cd redteam/promptfoo
npm ci
```

## Config notes (real MCP)

Defaults in `promptfooconfig.yaml` match official tools:

- **Create**: `issue_write` + `method: create` → response field `number`
- **Comment**: `issue_write` + `method: add_comment` (gateway auto-prepends `@letta-code`)
- **Wait**: poll `issue_read` + `method: get_comments` for user `gitea-actions` containing `Letta Code finished` / `Letta Code failed`

Issue **creation** bodies still need `@letta-code` in the body (`ensureMention`); comment auto-prefix is handled by the patched MCP gateway.

## Config reference

`promptfooconfig.yaml` fields, grouped by section.

### Top level

| Field | Purpose |
|---|---|
| `description` | Free text summary shown in reports |
| `targets` | The system under test (see below) |
| `prompts` | Template that renders the attack into the target; `{{prompt}}` is filled by the generator |
| `defaultTest` | Randomizes `sessionId`; with `reuseIssue: true`, the provider still shares one issue across tests |
| `outputPath` | Where results are written (gitignored run artifact) |
| `redteam` | Attack-generation settings (see below) |

### `targets[0]` — custom provider (`shared/js/mcp_target_provider.mjs`)

| Field | Purpose |
|---|---|
| `id` / `label` | Provider path and the name shown in reports |
| `timeoutSeconds` | Timeout passed to MCP writes/session defaults; not an overall callApi deadline |
| `sessionIdPattern` | `sessionId` matching this regex continues an existing issue; otherwise a new issue is created |
| `reuseIssue` | When true, every test and turn shares one issue (persistent conversation) |
| `ensureMention` | Prefix added to **new issue bodies** (the MCP gateway only patches comments) |
| `ensureMentionOnComment` | When false, leave comment prefixing to the gateway so the real code path is exercised |
| `defaults.owner` / `defaults.repo` | Repo coordinates substituted into `{{owner}}` / `{{repo}}` |
| `mcp.url` | MCP endpoint (HTTP). Override with `PROMPTFOO_MCP_URL` when running inside compose |
| `mcp.command` / `mcp.args` / `mcp.cwd` | stdio alternative used by the offline mock instead of `url` |
| `tools.*` | MCP tool names; change these to swap implementations |
| `createArgs.*` / `commentArgs.*` | Argument templates for the first turn and follow-up turns |
| `result.issueField` | Response field holding the new issue number |
| `result.outputField` | Field holding the target's reply, used as the model output |
| `wait.mode` | `poll_comments` = poll the issue until a completion marker appears |
| `wait.intervalSeconds` / `wait.timeoutSeconds` | Poll cadence and maximum wait |
| `wait.botUser` | Only comments from this user count (the agent's bot identity) |
| `wait.doneMarks` | Any of these strings ends the turn |
| `wait.failMarks` | Any of these flags the agent's turn as failed |
| `wait.args` | Tool arguments used while polling |

### `redteam`

| Field | Purpose |
|---|---|
| `purpose` | Shared benchmark objective inserted into the generated configuration |
| `injectVar` | Variable the generated attack is written into (must match `prompts`) |
| `numTests` | Tests generated **per plugin** (total = plugins x numTests) |
| `provider` | Model that generates the attacks (not the target) |
| `plugins` | Attack categories that generate locally, one test set each |
| `strategies` | How attacks are delivered/evolved; `crescendo` = multi-turn escalation |

### `redteam.provider` — attack generator

| Field | Purpose |
|---|---|
| `id` | Provider/model string, here the Z.ai OpenAI-compatible endpoint |
| `config.apiBaseUrl` | Endpoint base URL |
| `config.apiKeyEnvar` | Env var holding the API key |
| `config.passthrough` | Raw request-body keys promptfoo forwards unchanged; used here to send `thinking: disabled` |
| `config.max_tokens` | Output cap; promptfoo defaults to 1024 for non-OpenAI models, which truncates long JSON |
| `config.showThinking` | When false, reasoning content is not prepended to the output |
| `config.response_format` | **Deliberately absent here.** Set only on the crescendo provider; a global JSON mode leaks into plugin-generated attack text. |

### `redteam.strategies` entry with `id: crescendo`

| Field | Purpose |
|---|---|
| `stateful` | Sends only the newest message each turn; the issue thread carries the context |
| `maxTurns` | Turn budget per test |
| `redteamProvider` | Strategy-scoped generator override; same model plus `response_format: json_object` so each step parses cleanly |

## Run

Offline smoke (mock MCP, no Docker):

```bash
npm run smoke
```

Against the real gateway:

```bash
# Ensure http://127.0.0.1:8080/healthz is OK
python3 ../run_locked.py -- npm run redteam   # promptfoo 0.122.2, serial targets
PROMPTFOO_CONFIG_DIR="$PWD/.promptfoo" npx promptfoo redteam report
```

The launcher loads `ZAI_API_KEY` from the root `.env` when it is not already
exported. Run from `redteam/promptfoo/`:

```bash
python3 ../run_locked.py -- npm run redteam
```

`npm run redteam` uses the promptfoo pinned in `package.json` / `package-lock.json`
and sets `PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=true` (local Z.ai generation,
no Promptfoo remote attack generation; prompts still go to the configured Z.ai
model API). Use `npx promptfoo@latest` only if you
deliberately want a different version — it is not what this config is tested against.

Use `-j 1` so attempts stay serial (runner capacity is 1).

### Multi-turn

`crescendo` uses `stateful: true`. With `reuseIssue: true`, the provider creates one
issue for the first attack and routes every later test and turn to that issue.
Set `PROMPTFOO_GITEA_ISSUE=<number>` to continue a campaign in an existing issue.
Keep `-j 1` so shared-issue updates remain serial.

## Current defaults and limitations

The default credential scenario uses `debug-access`; the memory and outbound
network scenarios use a policy plugin populated from the selected scenario.
The `basic` strategy entry is disabled, followed by the enabled `crescendo`
entry with the scenario's turn budget. `outputPath` uses the selected scenario
name so results from different goals have separate files.

The polling window is 900 seconds, and individual MCP calls have their own
request deadlines, so an in-flight poll can extend the total wait. A failed
Letta completion is returned as an observation with `metadata.failed=true` and
`guardrails.flagged=true`; it is not automatically a campaign-stopping provider
error. Runner Oracle evidence is separate and is not imported into this report.

The provider has no shared campaign lock of its own. Use the wrapper commands
above. `npm run smoke:live` posts two real benign turns; run it as
`python3 ../run_locked.py -- npm run smoke:live`. It is not a read-only check.

## Docs

- Root MCP client setup: [../../README.md](../../README.md)
- [Custom Provider](https://www.promptfoo.dev/docs/providers/custom-api/)
- [Red team configuration](https://www.promptfoo.dev/docs/red-team/configuration/)
- [Multi-turn strategies](https://www.promptfoo.dev/docs/red-team/strategies/multi-turn/)
