# Red-team integrations

Each integration keeps its configuration, dependencies, scripts, and run artifacts
in its own directory:

The shared attack goals, success criteria, and turn budgets live in the
[benchmark scenarios](../benchmark/README.md). Pass `--scenario NAME` to select
one for a run; the default is `runtime-credential-disclosure`.

- [Promptfoo](promptfoo/README.md): Gitea issue/comment testing through the MCP gateway.
- [Dreadnode](dreadnode/README.md): An autonomous attacker capability using the same gateway.
- [DeepTeam](deepteam/README.md): Crescendo attacks through the same issue/comment gateway.
- [PyRIT](pyrit/README.md): Multi-turn RedTeamingAttack through the same issue/comment gateway.
- [Check Point / Lakera Red](lakera/README.md): Cloud-generated attacks relayed locally through MCP.

Run campaigns serially; do not run integrations concurrently. PyRIT and Lakera hold the
shared `redteam/.campaign.lock`; launch other campaigns through
`python3 redteam/run_locked.py -- <command> [args...]` to share that lock.

## Promptfoo

From the repository root:

```bash
cd redteam/promptfoo
npm ci
npm run smoke
python3 ../run_locked.py -- npm run redteam
# Select another scenario:
python3 ../run_locked.py -- npm run redteam -- --scenario memory-poisoning
```

Use Node 22.22.0 or newer. The launcher loads `OPENAI_API_KEY` from the repository
root `.env` when it is not already exported in the shell.

Promptfoo previously ran from `redteam/`; its working directory is now
`redteam/promptfoo/`. Internal provider and fixture paths remain relative to that
directory. Local state (`.promptfoo/`), generated tests (`redteam.yaml`), and
results (`promptfoo-results/`) also live there.

## Dreadnode

From the repository root:

```bash
python3 redteam/run_locked.py -- bash redteam/dreadnode/run.sh
# Select another scenario:
python3 redteam/run_locked.py -- bash redteam/dreadnode/run.sh --scenario outbound-network-traffic
```

See the integration README for installation and configuration.

## DeepTeam

From the repository root:

```bash
uv sync --project redteam/deepteam --python 3.11 --locked
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --check
python3 redteam/run_locked.py -- redteam/deepteam/.venv/bin/python redteam/deepteam/run.py
# Select another scenario:
python3 redteam/run_locked.py -- redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario memory-poisoning
```

Set `OPENAI_API_KEY` in the environment, `redteam/deepteam/.env`, or the root `.env`.
`--check` performs read-only validation and requires credentials to be present;
it does not call models. DeepTeam has no `--offline-check` mode. Its internal
lock only covers DeepTeam, so use the shared wrapper for campaigns.
See [DeepTeam's README](deepteam/README.md) for configuration and report details.

## PyRIT

From the repository root:

```bash
uv sync --project redteam/pyrit --python 3.11 --locked
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --offline-check
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --check
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py
# Select another scenario:
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario memory-poisoning
```

See the integration README for configuration, offline tests and result semantics.

## Check Point / Lakera Red

From the repository root:

```bash
uv sync --project redteam/lakera --python 3.11 --locked
redteam/lakera/.venv/bin/python redteam/lakera/run.py --check
redteam/lakera/.venv/bin/python redteam/lakera/run.py
# Select another scenario:
redteam/lakera/.venv/bin/python redteam/lakera/run.py --scenario outbound-network-traffic
```

Set `LAKERA_RED_API_KEY` in `redteam/lakera/.env` or the environment before
running a scan. The target reply deadline is 300 seconds. The adapter uses the
same MCP issue tools as PyRIT; no public endpoint is required. See the integration
README for SDK compatibility tests, session isolation and result semantics.

## Current behavior across integrations

These are the shipped configurations, not a claim that all services enforce the
same budgets or produce comparable scores automatically:

| Integration | Conversation mapping | Target reply wait | Result / Oracle handling |
| --- | --- | --- | --- |
| Promptfoo | One shared issue per campaign (`reuseIssue: true`) | 900s polling window; an in-flight MCP call can extend it | Promptfoo report; runner Oracle evidence is separate |
| Dreadnode | One issue per campaign, managed by the attacker | At most 60 × 15s pauses plus request latency; instruction-enforced | Agent observations; no structured Oracle verdict |
| DeepTeam | One new issue per case | 900s | Local framework assessment; Oracle not collected |
| PyRIT | One new issue per case | 900s | Local framework assessment; Oracle not collected |
| Lakera Red | One new issue per Red session | 300s maximum; SDK relay idle timer separately defaults to 480s | Cloud assessment plus local session reports; Oracle not collected |

All use the MCP gateway at `http://127.0.0.1:8080/mcp`; the default Gitea web UI
is `http://127.0.0.1:3000`. New issues do not reset the repository or persistent
agent state. Runner Oracles are separately enabled by `ORACLE_ENABLE`; none of
these adapters automatically imports their final verdicts into its assessment.

The documented check modes do not validate paid model/cloud access or quotas.
Promptfoo's `smoke` is an offline fixture test; its `smoke:live` creates a real
issue and performs two Letta turns. Treat it as a campaign and hold the shared
lock. Dreadnode capability validation checks the manifest, not target behavior.
