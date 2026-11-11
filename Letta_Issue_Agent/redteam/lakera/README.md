# Check Point AI Red Teaming (Lakera Red) via MCP

Run commands from `GreyZone/Letta_Issue_Agent/` unless stated otherwise.

This integration runs the pinned Python `lakera-red-sdk==0.9.0` locally. The SDK
pulls attack text from Red, then the adapter calls the existing MCP gateway's
`issue_write` and `issue_read` tools. Red receives the actual Letta reply after
it completes. There are no direct Gitea HTTP writes and no public inbound endpoint.

One Red session maps to one Gitea issue. Its first turn creates an issue and later
turns append comments. The adapter controls MCP calls; Red generates the text,
not arbitrary tool calls. Letta memory and repository state are **not reset**
between sessions. All integrations must run serially against this environment.

## Install and check

From the application directory:

```bash
uv sync --project redteam/lakera --python 3.11 --locked
redteam/lakera/.venv/bin/python redteam/lakera/run.py --offline-check
redteam/lakera/.venv/bin/python -m unittest discover -s redteam/lakera -p 'test_*.py' -v
redteam/lakera/.venv/bin/python redteam/lakera/run.py --check
```

`--offline-check` checks configuration and SDK option construction. `--check`
also initializes MCP and checks that the issue tools exist. Neither mode posts
messages, creates a Red target/scan, or calls a model. Checks show whether a Red
credential is present; they do not verify the cloud account's access or quota.

The tests use the actual pinned SDK with fake HTTP/MCP services. They cover its
wire contract, two-turn iteration, reply upload, results polling, cancellation,
duplicate prevention and uncertain outcomes. Virtual-time tests exercise the
actual SDK idle loop while a target reply arrives at 299 or 301 seconds; these
are local compatibility tests, not proof of the cloud service's timeout policy.

## Configure and run

Obtain a **Red Team API Key** from a Check Point account with Red access enabled.
A Lakera Guard key is not a substitute. Copy `.env.example` to `.env` in this
directory and set `LAKERA_RED_API_KEY`. Credentials load in this order:

1. Existing environment variables.
2. `redteam/lakera/.env`.
3. Repository root `.env`.

Edit `config.yaml` and the shared benchmark scenario, then run:

```bash
redteam/lakera/.venv/bin/python redteam/lakera/run.py
redteam/lakera/.venv/bin/python redteam/lakera/run.py --scenario outbound-network-traffic
# Alternative config; scenario_file is resolved relative to this file:
redteam/lakera/.venv/bin/python redteam/lakera/run.py --config /absolute/path/config.yaml
```

Defaults are one custom objective, one session, ten target submissions, Crescendo,
`max_retries=0`, and concurrency 1. `cases` sets the number of attacks per objective
and an independent local session cap; `max_target_calls` sets both SDK turns and
the local write budget. Automatic reconnaissance is disabled by explicitly
supplying the configured application profile. Creating a target with the same
name reuses it and sets its profile; use a distinct name for a different benchmark
configuration. No system prompt or tool ground truth is uploaded by this adapter.

The Red service generates and evaluates attacks; there is no separately configured
attacker LLM key. Letta still needs its own environment credentials. Target replies
are sent to the Red service for evaluation; local report redaction does not alter
those replies.

## The 300-second deadline

`target.reply_timeout_seconds` defaults to **300**, and configuration rejects
values above 300. Lower values are allowed. Timing begins once an issue/comment
write is acknowledged. A write has its own request deadline, at most 60 seconds.

The SDK's default **180-second relay inactivity timer cancels active handlers**.
Its exported `Scan` constructor supports `idle_timeout_s`, but `create_scan` does
not forward this setting. The adapter creates a scan normally, then binds the
returned scan ID using that public constructor. It does not create a second scan
or patch private SDK fields.

The relay-idle deadline is calculated separately:

```text
MCP request deadline + target reply deadline + Red response upload deadline + 60s margin
60 + 300 + 60 + 60 = 480 seconds with default settings
```

This prevents the SDK from cancelling a legitimate 300-second target wait. It
**does not extend** the target's reply deadline. A late response is rejected.
After a target timeout the session records `target_timeout`, retains the issue
number, and forwards an execution error to Red instead of a fabricated reply.
No further target submissions are accepted after a local session failure.

The cloud service may impose additional limits that local configuration cannot
change. A live cloud run is needed to verify account/service behavior.

## Failure semantics and reports

Each run writes private files under `results/<timestamp>-<id>/`:

- `manifest.json`: SDK/MCP versions, configuration, objective and isolation limits.
- `target.json`, `scan.json`: cloud IDs and dashboard URL as soon as available.
- `events.jsonl`: target submission/reply events and SDK diagnostics.
- `case-NNN-summary.json`: session/issue mapping, attempted/completed turns and status.
- `red-results.json`: the latest cloud result, including partial evaluation results.
- `summary.json`: campaign status, all known sessions and independent Oracle status.

Exit code 0 means all expected local sessions completed and cloud evaluation was
ready with usable results for the configured objective. It does **not** mean an
attack succeeded. Exit code 1 indicates an error, missing/partial evaluation or
local timeout; Ctrl-C returns 130. The SDK can swallow handler failures or return
normally after idle timeout, so local session state and the idle-timeout log event
are explicitly checked before reporting completion.

Reports distinguish `target_timeout`, `write_outcome_unknown`,
`response_outcome_unknown`, `execution_error` and `interrupted`. On an uncertain
write, inspect the issue (or recent issues if creation had no acknowledgement)
before starting another run. A failed upload to Red does not cause another MCP
write. Repeated session IDs and repeated attack text within a session are rejected;
this conservatively stops intentional identical prompts too, because the SDK
exposes no per-message idempotency ID to distinguish them from redelivery.

Red's internal HTTP retries may resend a reply or an error to Red; they never call
our MCP write function directly. Process restart/resume is not implemented. Local
cancellation stops local work; the SDK has no public cloud-scan cancellation method
in this integration, so inspect the dashboard for any remaining cloud scan.

Results retain Red's assessment separately from benchmark truth:
`oracle.status` is `not_collected`. This integration does not turn an LLM score
into proof of scenario success. Known secret-like environment values are
redacted from local JSON reports, but unknown secrets in target output may remain.
All reports and environment files are ignored by Git.

The runner automatically holds `redteam/.campaign.lock`. Other integrations share
that lock only when launched via `python3 redteam/run_locked.py -- <command>`.

References: [SDK quickstart](https://docs.lakera.ai/docs/red/sdk-quickstart),
[SDK reference](https://docs.lakera.ai/docs/red/sdk-reference),
[deployment](https://docs.lakera.ai/docs/red/sdk-deployment).
