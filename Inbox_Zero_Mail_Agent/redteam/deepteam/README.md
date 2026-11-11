# DeepTeam over redteam-mcp

Run commands from `GreyZone/Inbox_Zero_Mail_Agent/` unless stated otherwise.

DeepTeam 1.0.9 runs `CrescendoJailbreaking` through the external customer's
stdio MCP server. Each case starts a uniquely titled email thread; later turns
reply in that thread and wait for a real customer-visible response. Cases run
serially. The managed mailbox, mail agent, and Oracle fixtures are not reset
between cases.

## Setup and read-only checks

Start the [mail environment](../../README.md), log into the managed account,
enable its max-autonomy rules, and ensure its mail watch is active. From the
mail project root:

```bash
uv sync --project redteam/deepteam --python 3.11 --locked
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --offline-check
redteam/deepteam/.venv/bin/python -m unittest discover -s redteam/deepteam -p 'test_*.py' -v
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --check
```

`--offline-check` validates the DeepTeam strategy without credentials or MCP
access. `--check` also loads credentials, initializes the redteam-mcp,
confirms its tools, and lists the customer inbox. Neither sends mail or calls a
model; they do not prove that the mail watch or model quota is healthy.

The attacker and evaluator use `OPENAI_MODEL` through the configured OpenAI endpoint. The runner
reads `OPENAI_API_KEY` from the shell, optional `redteam/deepteam/.env`, then
`OPENAI_API_KEY` from `apps/web/.env`, in that order. The transport token is read
only from the ignored `apps/web/.tmp/customer-mail/.transport-token` file. Do
not put secrets in `config.yaml` or a benchmark scenario.

## Run a scenario

Seed the Oracle and start its watcher before the first attack email. Choose
monitors from the selected scenario's `oracleMonitors` field. For example, in
one terminal:

```bash
cd apps/web
pnpm oracle:watch kb,pipeline
```

From the mail project root, run one of the shared benchmark scenarios:

```bash
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario knowledge-leak
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario mail-history-leak
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario phishing-outbound
```

Use `mail,pipeline` for `mail-history-leak` or `phishing,pipeline` for
`phishing-outbound`. Without `--scenario`, `config.yaml` selects
`knowledge-leak`. DeepTeam shares the `.tmp/redteam/.campaign.lock` with the
other mail red-team launchers, so only one campaign can run at a time.

The scenario supplies the objective, policy, identities, email-turn limit,
reply timeout, polling interval, and Oracle monitors. `config.yaml` supplies
the case count, total case deadline, model settings, and MCP timeout. The
scenario's `maxTurns` must be at least two for this multi-turn strategy.

## Results and limits

Results are written to `.tmp/deepteam/<run-id>/`: a manifest, event log,
per-case summary and assessment, and campaign summary. `events.jsonl` records
the submitted message IDs and matched reply indexes. Match
`sent_message_ids` with the Oracle result's `attackMessageIds` after the
episode closes. DeepTeam's score is a visible-behavior judgment; the separate
Oracle is authoritative, and `oracle.status` remains `not_collected` in
DeepTeam reports.

For `phishing-outbound`, the agent can send to a third party without replying
to the customer. A `no_reply` case therefore does not establish that the
attack failed. A timeout or uncertain write stops that case without retrying
the email. Inspect the existing thread before starting another campaign.

Telemetry flags and Confident upload are disabled. Reports redact known
environment secrets and use owner-only file permissions, but an unknown
secret in a model assessment may still appear in results. Keep `.tmp/deepteam/`
private.
