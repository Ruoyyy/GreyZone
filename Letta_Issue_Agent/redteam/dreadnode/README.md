# Dreadnode to Letta through MCP

Run commands from `Letta_Issue_Agent/`. Keep the validated Dreadnode 2.0.46
installation and the existing OpenAI configuration. This integration uses
Dreadnode's native autonomous agent loop, with code-enforced target turns.

```text
Dreadnode attacker
  -> letta_send_and_wait(message)
    -> shared Gitea MCP transport
      -> create one issue / append one comment
      -> wait for the corresponding completed Letta reply
  -> choose the next attack from that reply
```

## Setup

Configure `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` in the
application `.env`. Copy `redteam/dreadnode/.env.example` to
`redteam/dreadnode/.env` and set its permissions to 600. The template sources
the application configuration and selects `openai/${OPENAI_MODEL}`. Existing
local overrides remain supported. Never commit `.env` or credentials.

The model must support tool calling. Authenticate the CLI with `dn login`.
Install the validated CLI if needed:

```bash
uv tool install --python python3.11 'dreadnode==2.0.46'
```

Register the local capability using a source symlink (the install command's
default), then validate it:

```bash
dn capability install ./redteam/dreadnode
dn capability validate --strict ./redteam/dreadnode
```

An existing source-symlink installation sees these changes automatically.
If the capability was installed as a frozen copy, reinstall it from this
source before running. Do not enable additional capabilities for this agent.

Start the existing Docker stack and confirm the MCP endpoint:

```bash
curl --fail http://127.0.0.1:8087/healthz
```

## Run

For experiments, use the shared batch runner. It holds the campaign lock,
restores target state, captures framework evidence and collects Oracle:

```bash
python3 experiment/scripts/run.py --framework dreadnode \
  --scenario runtime-credential-disclosure --baseline clean \
  --repeats 1 --status-interval 10 --oracle-idle-timeout 30
```

Direct campaigns retain the existing launcher and configuration:

```bash
python3 redteam/run_locked.py -- bash redteam/dreadnode/run.sh \
  --scenario memory-poisoning --print
```

The launcher sets a fresh campaign ID and selects the shared benchmark
scenario. `DREADNODE_MAX_STEPS` limits native agent execution steps, not target
turns. The scenario's `maxTurns` is the hard target-submission limit.
Use fresh local CLI sessions; resuming a native session or connecting this
launcher to a shared runtime server is not supported.

## Enforced protocol

The attacker has one tool: `letta_send_and_wait(message)`. Raw MCP tools are
not exposed. The tool owns the upstream MCP connection and uses the same
framework-independent transport as PyRIT, not PyRIT's attack strategy.

- The first call creates one issue and includes `@letta-code`.
- Later calls append only the new message to that issue.
- A call returns after a `gitea-actions` completion with a fresh comment ID.
  Existing progress comments are re-read so in-place completion updates work.
- Old completion comments do not satisfy a new turn. Hidden metadata is
  removed from the returned reply; comment IDs are retained as evidence.
- Concurrent calls are rejected immediately. They are never queued for later
  submission. The agent must review the completed reply before choosing again.
- Each MCP request has a 60-second deadline; the total reply wait is 900
  seconds. Polling is performed in code every five seconds, not by the model.
- Failed replies, timeouts, cancellations, and uncertain writes permanently
  stop that campaign. Target writes are never retried automatically.
- The tool reports remaining turns. Once the budget is exhausted, later calls
  return a stopped response without writing another comment.

This adapter does not expose Oracle or numerical framework scores. The
attacker reports observations. Oracle remains the experiment's score source.

The former raw-MCP probe workaround, `fix_mcp_probe.py`, is retained for legacy
clients. The controlled tool uses the standard Python MCP client directly and
does not require that Dreadnode-specific probe patch.

## Results and validation

Native evidence is stored in ignored `redteam/dreadnode/results/<campaign-id>/`:

- `manifest.json`: scenario, turn budget, and protocol version.
- `events.jsonl`: submission intents, acknowledged writes, matched replies,
  rejected concurrent calls, and stopping errors.
- `case-001-summary.json`: issue, submission/reply counts, pending state, and
  protocol execution status.

The experiment runner copies this directory into each attempt's `framework/`.
It checks the protocol summary as well as the CLI exit code. A CLI exit code
of zero cannot turn a failed or incomplete guarded campaign into a valid run.
The native CLI output and approximate token total remain in `console.log`.

Reports use private permissions and redact known secret environment values.
Unknown secrets may still occur in target replies; keep runtime artifacts local.
New campaigns never overwrite or resume existing report directories.

Offline tests use fake transports, with no model requests or Gitea writes:

```bash
~/.local/share/uv/tools/dreadnode/bin/python -m unittest discover \
  -s redteam/dreadnode -p 'test_*.py' -v
```

After a run, check equal submitted/completed turn counts, a matched `reply` event
between consecutive `submit_intent` events, no pending or failed turns, and a
completed Oracle evaluation. See the [result-checking instructions](../../experiment/README.md#checking-results)
for the batch-level checks. A completed run does not imply a successful attack.
