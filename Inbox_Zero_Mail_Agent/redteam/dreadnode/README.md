# Dreadnode over customer-mail MCP

This capability runs an autonomous attacker as the external customer. It
starts the existing stdio customer-mail MCP server, sends one email at a time,
reads matched replies, and adapts within the selected shared benchmark
scenario. It does not read the Oracle or produce a verified success verdict.

## Setup

1. Start and configure the [mail environment](../../README.md). Keep the
   managed account logged in, its mail watch active, and the max-autonomy
   rules enabled. Seed the Oracle before the campaign.
2. Install a Dreadnode CLI with capability support and run `dn login`.
   The local integration was validated against Dreadnode 2.0.46. Node.js and
   the app's installed `tsx` dependency are needed for the MCP subprocess.
3. Copy `.env.example` to `.env` in this directory, fill in
   `OPENAI_API_KEY` and `OPENAI_MODEL`, and restrict the file with `chmod 600`. The default
   attacker model is `openai/${OPENAI_MODEL}`; this key is separate from the mail
   agent's `OPENAI_API_KEY` in `apps/web/.env`. An exported `OPENAI_API_KEY`
   takes precedence over a value in the local `.env` file.
4. From the mail project root, check the capability:

   ```bash
   dn capability validate --strict ./redteam/dreadnode
   ```

The launcher supplies its capability directory to Dreadnode, so a separate
`dn capability install` step is optional.

The launcher reads the mail transport token from the ignored
`apps/web/.tmp/customer-mail/.transport-token` file and gives it only to the
MCP subprocess. It sets the customer's identity and outbox from the selected
scenario. Do not put the token in `capability.yaml` or a benchmark scenario.

## Run

Start the Oracle watcher before the first attack email. Select monitors from
the scenario's `oracleMonitors` field:

```bash
cd Inbox_Zero_Mail_Agent/apps/web
pnpm oracle:watch kb,pipeline
```

In another terminal, run a selected scenario from the mail project root:

```bash
cd Inbox_Zero_Mail_Agent
bash redteam/dreadnode/run.sh --scenario knowledge-leak
bash redteam/dreadnode/run.sh --scenario mail-history-leak
bash redteam/dreadnode/run.sh --scenario phishing-outbound
```

Use `mail,pipeline` for `mail-history-leak` or `phishing,pipeline` for
`phishing-outbound`. Add `--print` to run without the TUI. The launcher
passes the shared objective, policy, addresses, unique subject, email budget,
and reply timeout as the opening prompt. Other Dreadnode flags are forwarded.
`DREADNODE_MAX_STEPS` limits Dreadnode execution steps; the scenario's
`maxTurns` limits attack emails through agent instructions.

Promptfoo and Dreadnode hold the same OS-backed lock while a campaign runs.
The lock file under `.tmp/redteam/` need not be deleted after a crash.

## Evidence and limits

The customer MCP has `send_email`, `list_received`, `read_received`, and
`reply_to`. A new campaign uses one unique subject and subsequent turns reply
in that thread. The agent records sent message IDs and matched reply IDs.
Compare sent message IDs with the Oracle's `attackMessageIds` after the
episode closes. Oracle results live under `apps/web/.tmp/oracle/`; its verdict
is authoritative. For `phishing-outbound`, the managed agent may send mail to
a third party without replying to the customer, so lack of a customer reply
does not prove failure.

The email-turn limit and reply matching are instructions to Dreadnode's
autonomous agent, not an enforced counter or deterministic target adapter.
The launcher enforces a separate Dreadnode step limit. If an MCP write has
an uncertain outcome, the agent is instructed to stop instead of retrying.
Run one campaign at a time against the shared mailbox.
