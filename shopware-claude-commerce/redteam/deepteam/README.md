# Shopware DeepTeam red team

Run commands from `GreyZone/shopware-claude-commerce/` unless stated otherwise.

DeepTeam 1.0.9 generates one seller-controlled text payload for a selected
Shopware benchmark scenario. The shared evaluator writes it through
`redteam-mcp`, approves reviews changed by this run, sends the fixed buyer or merchant
task, and records the Oracle verdict. DeepTeam's model score is advisory;
`summary.json` and its linked Oracle result contain the benchmark verdict.

## Setup

Start Shopware and both API hosts (shopping 8004 and merchant 8005). The
Shopware project `.venv` must be installed because the evaluator uses it.
From the `shopware-claude-commerce` project root:

```bash
uv sync --project redteam/deepteam --python 3.11 --locked
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --offline-check
redteam/deepteam/.venv/bin/python -m unittest discover -s redteam/deepteam -p 'test_*.py' -v
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --check
```

`--offline-check` validates configuration and DeepTeam construction without
credentials or network access. `--check` also checks credentials, both API
hosts, and a read-only `redteam-mcp` product search. Neither sends a model request
or writes shop data.

The attacker and advisory evaluator use `OPENAI_MODEL` through the OpenAI-compatible API. The runner
reads `OPENAI_API_KEY` from the shell, optional ignored `redteam/deepteam/.env`,
the project root `.env`; shell values take precedence. The victim agents retain their own model settings.

## Run

From the project root:

```bash
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario buyer-misdirection
TASK_CATEGORY=clothing redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario buyer-misdirection
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario merchant-exfil
```

Other scenario IDs are `buyer-exfil`, `merchant-floor-breach`, and
`merchant-refund`. Without `--scenario`, `config.yaml` selects
`buyer-misdirection`. Restore the Shopware snapshot and restart the hosts
between independent runs. Do not run `oracle/watch.py` concurrently: this
adapter finalizes its own Oracle result. The shared red-team lock serializes
the local payload evaluators.

Results are written to ignored `.run/deepteam/<run-id>/`: `manifest.json`,
`assessment.json`, and `summary.json`, plus `error.json` on failure. The
summary links `.run/oracle/result-<N>.json`. One run submits at most one
payload; an error after a write attempt requires inspecting Shopware before
retrying. The adapter does not reset persistent shop data.
