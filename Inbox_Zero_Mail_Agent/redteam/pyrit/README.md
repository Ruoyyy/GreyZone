# PyRIT over customer-mail MCP

PyRIT 1.1.0 `RedTeamingAttack` sends multi-turn attacks through the existing
customer-mail stdio MCP server. Each case starts one uniquely titled email
thread and continues it with `reply_to`. Cases run serially; the managed
mailbox, mail agent, and Oracle fixtures are not reset between cases.

## Setup and checks

Start and configure the [mail environment](../../README.md) first. Install the
app's Node dependencies so the customer-mail MCP can run with `tsx`. From the
mail project root:

```bash
uv sync --project redteam/pyrit --python 3.11 --locked
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --offline-check
redteam/pyrit/.venv/bin/python -m unittest discover -s redteam/pyrit -p 'test_*.py' -v
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --check
```

`--offline-check` builds the real PyRIT attack and target without credentials
or MCP access. `--check` additionally loads credentials, initializes the
customer-mail MCP, confirms its four required tools, and lists the customer
inbox. Neither sends mail or requests a model response.

The attacker and evaluator use `OPENAI_MODEL` through the configured OpenAI endpoint. The runner
reads `OPENAI_API_KEY` from the shell, optional `redteam/pyrit/.env`, then
`OPENAI_API_KEY` from `apps/web/.env`, in that order. The transport token is read
from `apps/web/.tmp/customer-mail/.transport-token` only for MCP connection.
Do not put either secret in `config.yaml` or a scenario.

## Run a scenario

Seed the Oracle and start its watcher before the first attack email. Select
the watcher monitors from the scenario's `oracleMonitors` field, for example:

```bash
cd apps/web
pnpm oracle:watch kb,pipeline
```

From the mail project root, select one of the shared benchmark scenarios:

```bash
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario knowledge-leak
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario mail-history-leak
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario phishing-outbound
```

Use `mail,pipeline` for `mail-history-leak` or `phishing,pipeline` for
`phishing-outbound`. Without `--scenario`, `config.yaml` selects
`knowledge-leak`. Configuration can be changed with `--config`.

The runner holds the shared `.tmp/redteam/.campaign.lock`, so it cannot run
alongside the local Promptfoo or Dreadnode launcher. The scenario sets the
email-turn budget, reply timeout, polling interval, customer and managed
addresses, and base subject. `config.yaml` sets the case count, total
per-case deadline, model endpoints, and MCP request timeout.

## Results and limits

Private results live under `.tmp/pyrit/<run-id>/`: a manifest, event log,
per-case summary and assessment, and campaign summary. The event log records
sent message IDs and matched reply IDs. Match `sent_message_ids` with the
Oracle result's `attackMessageIds` after the episode closes. PyRIT's
evaluator is a visible-behavior judgment; `oracle.status` stays
`not_collected`, and the separate Oracle supplies the authoritative verdict.

A customer reply may never arrive even when the mail agent sends to a third
party. The runner then records `no_reply`, stops that thread, and does not
claim the `phishing-outbound` scenario failed. A write with uncertain outcome
also stops without retrying. The target enforces the scenario's maximum
number of sent attack emails and rejects replayed or rewritten PyRIT history.
Reports redact known environment secret values and use owner-only file
permissions, but an unknown secret in target output may still appear in a
PyRIT assessment; keep `.tmp/pyrit/` private.
