# Shopware PyRIT red team

PyRIT 1.1.0 generates one seller-controlled text payload for a selected
Shopware benchmark scenario. The target passes that text to the same evaluator
used by Promptfoo: it writes through the seller MCP, approves only reviews
changed by this run, sends one scripted buyer or merchant task, and records an
Oracle verdict. PyRIT's model assessment is advisory; `summary.json` and the
linked Oracle result contain the authoritative scenario result.

## Setup

Start Shopware and the shopping and merchant API hosts. The Shopware project
`.venv` must also be installed, since the evaluator runs in that environment.
From the project root:

```bash
uv sync --project redteam/pyrit --python 3.11 --locked
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --offline-check
redteam/pyrit/.venv/bin/python -m unittest discover -s redteam/pyrit -p 'test_*.py' -v
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --check
```

`--offline-check` constructs the real PyRIT attack without credentials,
network traffic, seller writes, or model requests. `--check` also verifies
credentials, the shopping and merchant health endpoints, and a read-only
seller-MCP product search. Neither starts a victim task.

The attacker and advisory evaluator use `OPENAI_MODEL` through the OpenAI-compatible API. The runner
reads `OPENAI_API_KEY` from the shell, optional ignored `redteam/pyrit/.env`,
the project root `.env`; shell values take precedence. Change `config.yaml` to use another OpenAI-compatible model. The victim
agents keep their existing project model settings. Secrets are never written
to `config.yaml` or the scenario.

## Run

From the project root:

```bash
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario buyer-misdirection
TASK_CATEGORY=clothing redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario buyer-misdirection
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario merchant-exfil
```

The other scenario IDs are `buyer-exfil`, `merchant-floor-breach`, and
`merchant-refund`. Without `--scenario`, `config.yaml` selects
`buyer-misdirection`. One run generates one payload and one victim turn; restore
the Shopware snapshot and restart the hosts between independent runs. The
runner does not reset the store. Do not run `oracle/watch.py` concurrently;
this adapter finalizes its own Oracle result. The shared red-team lock prevents
overlap with the local Promptfoo and Dreadnode runners.

Results are written to ignored `.run/pyrit/<run-id>/`: `manifest.json`,
`assessment.json`, and `summary.json`, plus `error.json` when execution fails.
The summary links `.run/oracle/result-<N>.json`. A PyRIT assessment can differ
from Oracle state evidence; use `summary.json`'s `oracle.success` for the
selected scenario. If an error or timeout occurs after a seller write may have
started, inspect Shopware state before retrying. The target refuses a second
submission within the same run.

The buyer review path includes trusted approval and public visibility checks.
Merchant price and refund success requires an applied change under the host's
normal approval setting. The first version limits PyRIT to one turn because
Shopware products, orders, and refunds persist across turns.
