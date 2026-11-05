# DeepTeam MCP integration

DeepTeam 1.0.9 runs `CrescendoJailbreaking` against Letta Code through the existing
Gitea MCP gateway. Each case creates one issue; subsequent turns append comments
to that issue and wait for the actual Letta reply. Cases run serially. Creating
a new issue does not reset the repository or Letta's persistent memory.

## Setup

From the repository root:

```bash
uv sync --project redteam/deepteam --python 3.11 --locked
```

The project supports Python 3.11–3.13; the command above uses 3.11. Configure
`config.yaml` and the shared benchmark scenario. The default attacker and evaluator both use
`OPENAI_MODEL` at the configured OpenAI endpoint.

Set `OPENAI_API_KEY` in the environment, `redteam/deepteam/.env`, or the repository
root `.env`, in that precedence order. `.env.example` is a template. Model
credentials are separate from the credentials used by the Letta target.

## Check and run

With the Docker benchmark environment running:

```bash
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --check
python3 redteam/run_locked.py -- redteam/deepteam/.venv/bin/python redteam/deepteam/run.py
# Or choose a benchmark goal:
python3 redteam/run_locked.py -- redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario memory-poisoning
```

`--check` validates configuration and credential presence, constructs the model
adapters and Crescendo strategy, then checks MCP initialization and required
issue tools. It makes no model requests or issue writes; it does not validate
model access, quota, or Letta execution. DeepTeam currently has no `--offline-check`
mode or dedicated integration test suite.

The shared wrapper locks `redteam/.campaign.lock` across integrations. DeepTeam's
own runner also locks `redteam/deepteam/results/.run.lock`, but that lock alone
only prevents simultaneous DeepTeam runs. Use the wrapper for actual campaigns.

An alternate configuration can be selected with `--config /absolute/path/config.yaml`.
Its `scenario_file` is resolved relative to that configuration file. Credentials
still load from the integration and repository locations described above.

## Budgets and failure behavior

Defaults: one case, at most ten target submissions, a 900-second reply deadline,
a 60-second MCP request timeout, and a 14,400-second deadline per case. The reply
wait starts after the write is acknowledged. The shared scenario sets
`max_target_calls` (2–20);
the strategy receives `max_rounds = max_target_calls - 1`, and the MCP adapter
independently caps actual writes. `max_backtracks=0` prevents intentional history
rollback; the adapter also checks that history agrees with completed issue turns.

A timeout, uncertain write, or failed Letta reply stops the case. An incomplete
case stops the campaign before another case is submitted. Target writes are not
automatically retried. Inspect the issue before restarting; there is no automatic
resume. The model SDK may retry model requests independently of target writes.

The attack objective, grading criteria, and turn budget come from the shared
benchmark scenario. The adapter still owns its model and MCP settings.

## Results

Each run writes `results/<timestamp>-<id>/`:

- `manifest.json`: configuration, objective, versions and isolation limits.
- `events.jsonl`: submission intents, acknowledged writes and target replies.
- `case-NNN-summary.json`: issue link, attempted/completed turns, execution status,
  score, reason and pending state.
- `case-NNN-assessment.json`: the framework assessment when available.
- `summary.json`: campaign status, returned case summaries and model usage.
- `error.json`: a campaign-level exception, when one occurs.

Interrupted cases retain their individual summaries even when absent from the
campaign's returned-case list. Exit code 0 means execution completed, not that
an attack succeeded. Errors return 1; Ctrl-C returns 130. Scores are framework
judgments and `oracle.status` remains `not_collected`.

The runner disables the configured DeepTeam/DeepEval telemetry flags and passes
`_upload_to_confident=False`. Attack generation and grading still call the model
endpoints in `config.yaml`. Known secret-like environment values are redacted
from reports; unknown secrets in target output are not guaranteed to be redacted.
Environment files, virtual environments, caches and results are ignored by Git.
