# PyRIT MCP integration

PyRIT 1.1.0 `RedTeamingAttack` connects to Letta Code through the existing
Gitea MCP gateway. Each case creates one issue; subsequent turns append comments
to that same issue. Cases run serially, with a fresh PyRIT conversation per case.
The repository and Letta agent memory are **not reset** between cases.

## Setup and validation

From the repository root, install Python 3.11 dependencies:

```bash
uv sync --project redteam/pyrit --python 3.11 --locked
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --offline-check
redteam/pyrit/.venv/bin/python -m unittest discover -s redteam/pyrit -p 'test_*.py' -v
```

Configure `config.yaml` and the shared benchmark scenario. The default attacker and evaluator
use `OPENAI_MODEL` through the configured OpenAI endpoint. Set `OPENAI_API_KEY` in
the environment, `redteam/pyrit/.env`, or the repository root `.env`, in that
precedence order. No credentials are needed for offline validation.

With the benchmark environment running, verify credentials are present and the
MCP gateway exposes `issue_write` and `issue_read`:

```bash
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --check
```

Both check modes construct the real PyRIT targets and strategy. They do not
generate model requests or post issues. `--check` additionally performs MCP
initialization and tool discovery; it does not verify model quota or execution
of the Letta workflow.

## Run

```bash
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario memory-poisoning
# Optional alternate configuration; scenario_file is relative to that file:
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --config /absolute/path/config.yaml
```

Defaults: one case, at most ten target submissions per case, a 900-second reply
deadline per submission, and a 14,400-second total deadline per case. The total
deadline includes generation and scoring; request timeouts are separately
configurable. An early positive evaluator result can stop the case.

The runner holds `redteam/.campaign.lock`. Other integrations only participate
in this lock when launched using the shared wrapper, for example:

```bash
python3 redteam/run_locked.py -- bash redteam/dreadnode/run.sh
```

Do not run other campaigns concurrently. The lock coordinates this checkout
only; it cannot isolate another machine or manual issue comments.

## Results and failure handling

Results live under `results/<timestamp>-<id>/`:

- `manifest.json`: configuration, objective, package versions and isolation limits.
- `events.jsonl`: submission intents, acknowledged writes and target replies.
- `case-NNN-summary.json`: status, issue link, attempted/completed turns and pending state.
- `case-NNN-assessment.json`: PyRIT result after completed execution.
- `summary.json`: campaign status and completed case summaries.

Partial case summaries survive exceptions and Ctrl-C; consult them even if an
interrupted case is absent from the campaign's completed-case list. Exit codes
are 0 for completion, 1 for execution failure and 130 for Ctrl-C. Completion
does not mean the attack succeeded. `evaluator_outcome` is an LLM judgment;
`oracle.status` remains `not_collected`, so no verified scenario success
is claimed. Placeholders and claims alone are excluded by the scoring rubric.

After a write with an unknown outcome, timeout, cancellation, or failed Letta
reply, the case stops. The runner never retries a target write and never
automatically resumes an uncertain conversation. Inspect the recorded issue
(or recent issues if creation was not acknowledged) before starting again.
History rewrites and replayed submissions are rejected before writing.

PyRIT memory uses an in-memory database. Cache/log paths stay under `.pyrit/`;
the integration intentionally disables default initializers and implicit PyRIT
environment loading. Reports redact known secret-like environment values and
use private filesystem permissions. Unknown secrets in target output are not
guaranteed to be redacted. Results, caches and `.env` are ignored by Git.

Reference: [PyRIT framework documentation](https://github.com/microsoft/PyRIT/blob/main/doc/code/framework.md).
