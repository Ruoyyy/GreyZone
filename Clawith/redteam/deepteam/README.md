# Clawith DeepTeam red team

This runner drives one seeded `LinearJailbreaking` conversation against one
Clawith benchmark scenario. The scenario's `attackerTurn` is sent verbatim as
alice's first group message. DeepTeam generates later messages from Morty's
replies. The existing bounded turn gateway sends messages and probes Oracle;
the final Oracle episode result is the benchmark verdict.

DeepTeam 1.0.9's public `progress` method generates its own baseline prompt,
so the runner calls its `_a_get_turns` seed entry point. The version is pinned
and an offline test checks the exact first turn, follow-up generation, and
Oracle stop. The adapter rejects replayed or branched history. One run owns one
candidate and one Oracle episode.

## Setup

From `Clawith/`:

```bash
./start.sh
docker compose up -d --build mcp-bridge
python3.11 -m venv redteam/deepteam/.venv
redteam/deepteam/.venv/bin/python -m pip install -r redteam/deepteam/requirements.txt
```

Set a fixed `MCP_API_KEY` and `ZHIPU_API_KEY` in `Clawith/.env`. The default
attacker model is `glm-4.7` through Zhipu's OpenAI-compatible endpoint. For a
different endpoint, set `DEEPTEAM_ATTACKER_BASE_URL` and
`DEEPTEAM_ATTACKER_API_KEY`; set `DEEPTEAM_ATTACKER_MODEL` or `--model` for its
model name. The runner checks the package version, credentials, and one
structured model response before preparing Oracle. Telemetry from DeepTeam
and DeepEval is disabled by the runner.

## Run

A new independent run needs a hard reset. This deletes Clawith's Docker
volumes and `backend/agent_data/`. Do not reset while another episode runs.

```bash
bash oracle/reset.sh --hard
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario kb-poison
```

Other IDs: `chat-exfil`, `email-exfil`, `private-agent-leak`, and
`sandbox-escape`. `--max-turns` defaults to 5 and accepts 1 through 20. The
runner performs one candidate serially, with a hard turn limit in the gateway.
An uncertain send, missing reply, or missing Oracle evidence is a run error,
not a defended target. The gateway also stops further sends after Oracle fires.

Results are under `.run/deepteam/<scenario>-<uuid>/`: `summary.json`,
`oracle-result.json`, `turns.json`, `attack-state.json`, and, for a completed
DeepTeam progression, `deepteam-transcript.json`. A zero-turn run is labeled
invalid even if Oracle closes with `failed`.

## Offline checks

```bash
redteam/deepteam/.venv/bin/python -m unittest redteam.deepteam.test_adapter redteam.deepteam.test_runner
```
