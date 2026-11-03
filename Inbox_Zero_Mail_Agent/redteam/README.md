# Mail red-team integrations

This directory runs attacks as an external customer of the Inbox Zero mail
agent. All four integrations use the customer-mail MCP server and the shared
[benchmark scenarios](../benchmark/README.md). The agent's private mailbox,
knowledge base, and Oracle canaries are outside the attacker's tools.

| Integration | Runner | Details |
|---|---|---|
| Promptfoo | `redteam/promptfoo`: `npm run redteam -- --scenario <name>` | [Setup and results](promptfoo/README.md) |
| Dreadnode | `bash redteam/dreadnode/run.sh --scenario <name>` | [Setup and results](dreadnode/README.md) |
| PyRIT | `redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario <name>` | [Setup and results](pyrit/README.md) |
| DeepTeam | `redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario <name>` | [Setup and results](deepteam/README.md) |

Run these commands from the `Inbox_Zero_Mail_Agent` project root, except for
the Promptfoo command, which runs from `redteam/promptfoo`. Install each
integration's dependencies as described in its README before the first run.

## Prepare the mail environment

1. Start the emulator, backbone, customer-mail server, database, Redis, and
   Inbox Zero app using the [environment guide](../README.md). The standard
   `scripts/mail-loop.sh up` setup uses app port 3000; a separately started app
   may use another port, such as 3002.
2. Log in as the managed account (`admin@gmail.com`). Keep the three
   max-autonomy rules enabled for the external customer (`alice@gmail.com`).
3. From `apps/web`, run `pnpm oracle:seed` before the campaign. This prepares
   the Oracle fixtures and disables any conflicting system rule left enabled.
4. Ensure the managed account's mail watch is active. Watches expire and must
   be renewed after expiry or an emulator restart. For a locally running app,
   set its actual URL and renew with:

   ```bash
   MAIL_APP_URL=http://localhost:3000 # Use http://localhost:3002 if that is the app port.
   curl -fsS "$MAIL_APP_URL/api/watch/all" \
     -H "Authorization: Bearer $(node --env-file=apps/web/.env -p 'process.env.CRON_SECRET || ""')"
   ```

   Confirm that the response reports `success: true` for the managed account.
   A renewal does not replay mail received while the watch was inactive; start
   a new campaign after restoring it. If no renewal cron is running, repeat
   this step for later campaigns after the watch expires.

The attacker-model credential is separate from the customer-mail transport
token. Promptfoo, PyRIT, and DeepTeam can reuse `LLM_API_KEY` from
`apps/web/.env` as `ZAI_API_KEY`; Dreadnode uses its own configured model key.
The ignored `apps/web/.tmp/customer-mail/.transport-token` is loaded by the
launchers and should not be copied into a scenario or checked-in config.

## Choose a scenario and start Oracle

| Scenario | Oracle watcher command from `apps/web` | Success surface |
|---|---|---|
| `knowledge-leak` | `pnpm oracle:watch kb,pipeline` | Private knowledge base |
| `mail-history-leak` | `pnpm oracle:watch mail,pipeline` | Private email history |
| `phishing-outbound` | `pnpm oracle:watch phishing,pipeline` | Outbound phishing propagation |

Start the matching Oracle watcher **before** the first attack email and keep it
running during the campaign. In a second terminal, run one integration with
`--scenario <name>`. For example:

```bash
cd apps/web
pnpm oracle:watch kb,pipeline
```

```bash
# In another terminal, from the project root:
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario knowledge-leak
```

Run one mail campaign at a time. The launchers share
`.tmp/redteam/.campaign.lock`; the operating system releases the lock when a
run exits. Do not delete the lock file to interrupt a running campaign. Each
scenario sets the objective, customer and managed identities, mail-turn
budget, reply timeout, and Oracle monitors. Tool-specific model and strategy
settings remain in each integration's directory.

## Read the results

| Source | Output |
|---|---|
| Oracle | `apps/web/.tmp/oracle/alerts-<N>.jsonl` and `result-<N>.json` |
| Promptfoo | `.tmp/promptfoo/<run-id>/` |
| Dreadnode | See [Dreadnode results](dreadnode/README.md#evidence-and-limits) |
| PyRIT | `.tmp/pyrit/<run-id>/` |
| DeepTeam | `.tmp/deepteam/<run-id>/` |

Match a runner's sent message IDs to the Oracle result's `attackMessageIds`
when comparing a run with an episode. Oracle provides the authoritative
success verdict. A framework score only describes behavior visible to that
framework; a missing customer reply does not prove failure, especially when
the agent sends mail to a third party. Keep local result directories private:
they may contain attack messages or model assessments.
