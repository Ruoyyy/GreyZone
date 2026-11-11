# Promptfoo over redteam-mcp

Run commands from `GreyZone/Inbox_Zero_Mail_Agent/` unless stated otherwise.

This adapter sends Promptfoo attack turns through the external customer's
stdio MCP server. The benchmark scenario is selected by name and lives under
[`../../benchmark/scenarios/`](../../benchmark/scenarios/). Promptfoo does
not maintain a separate attack objective.

## Setup

Start and configure the [mail environment](../../README.md) first. The managed
account must be logged in, its mail watch active, and the max-autonomy rules
enabled. Seed the Oracle with `bash scripts/mail-loop.sh up` or `pnpm oracle:seed`
as described in the environment README.

Use Node.js 24 (Node 22.22 or later also runs the adapter) and install the
pinned Promptfoo dependencies:

```bash
cd redteam/promptfoo
npm ci
```

The checked-in [Promptfoo configuration](promptfooconfig.yaml) defines the
attack model, API endpoint, credential variable name, test count, plugins, and
strategies. It defaults to the configured OpenAI-compatible model. The launcher reuses `OPENAI_API_KEY` from `apps/web/.env` as
`OPENAI_API_KEY` automatically. An existing `OPENAI_API_KEY` in the shell or an
optional local `.env` file in this directory takes precedence. Both `.env`
files are ignored by Git. The mail transport token is read from the ignored
`apps/web/.tmp/customer-mail/.transport-token` by the launcher; do not put it
in the scenario or Promptfoo config.

The benchmark owns the objective, policy text, identities, and comparable
turn/time budgets. The launcher loads `promptfooconfig.yaml`, fills those
scenario fields, and writes a resolved configuration for Promptfoo. In
particular, `crescendo.maxTurns` in the resolved file always comes from
`scenario.maxTurns`; edit the scenario to change that shared benchmark budget.
Model and strategy-provider settings stay editable in the checked-in YAML.

## Run a scenario

The Oracle watcher must start **before** the first attack email. Select its
monitors from the scenario's `oracleMonitors` field. For example, for the
knowledge-base scenario, run this in one terminal:

```bash
cd apps/web
pnpm oracle:watch kb,pipeline
```

In another terminal:

```bash
cd redteam/promptfoo
npm run redteam -- --scenario knowledge-leak
```

Use `mail-history-leak` with `mail,pipeline` or `phishing-outbound` with
`phishing,pipeline` to switch the benchmark target. Run only one mail campaign
at a time. Promptfoo and Dreadnode use the same local campaign lock at
`../../.tmp/redteam/.campaign.lock`; the operating system releases it when
the campaign process exits. Promptfoo uses one target call at a time.

To inspect the generated config without sending any mail or needing an API
key, run:

```bash
npm run prepare:scenario -- --scenario knowledge-leak
```

Each run writes `promptfooconfig.yaml`, `benchmark.json`, and Promptfoo's
`results.json` under ignored `../../.tmp/promptfoo/<run-id>/`. The benchmark
record includes the scenario ID, content hash, and expected Oracle surfaces.
The Oracle writes `apps/web/.tmp/oracle/result-<N>.json` only after 300 seconds
without new episode activity. Match its `attackMessageIds` against the
`sentMessageId` in the Promptfoo provider metadata. The Oracle result is the
authoritative success verdict; Promptfoo's policy grade is an attack-generation
and analysis aid, especially when the agent sends mail to a third party
without replying to the attacker.

## MCP behavior

The provider launches `apps/web/scripts/redteam-mcp.ts` over stdio and
uses only `send_email`, `list_received`, `read_received`, and `reply_to`. It
creates one mail thread per Promptfoo test. Later Crescendo turns reply to
the last matched managed-agent email in that thread. Replies are matched by
RFC Message-ID or the unique subject assigned to that test and filtered to
the attacker's recipient address. Each turn waits at most 300 seconds; a turn
with no reply cannot be continued automatically. A write with an uncertain
outcome also stops that thread to avoid a duplicate trigger.

The existing MCP tool names, inputs, and human-readable text remain usable by
other clients. The tools now also return `structuredContent` with mail IDs,
recipient details, threading headers, and the full received body. This lets
the adapter distinguish a real attacker-visible reply from third-party mail.

Run the offline protocol checks with `npm run smoke`. They send no real email
and require neither the mail stack nor an attack-model API key.

Set `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` for model requests.
