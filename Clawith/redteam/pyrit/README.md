# Clawith PyRIT red team

This adapter runs one PyRIT `RedTeamingAttack` against one Clawith benchmark
scenario. PyRIT chooses attack messages. The target sends each message as alice
through the existing bounded Dreadnode turn gateway, waits for Morty's reply,
and records the selected Oracle judgment on that reply. The PyRIT scorer reads
that judgment; the final Oracle episode result is authoritative.

## Setup

From `Clawith/`, start the stack and rebuild the MCP bridge:

```bash
./start.sh
docker compose up -d --build mcp-bridge
python3.11 -m venv redteam/pyrit/.venv
redteam/pyrit/.venv/bin/python -m pip install -r redteam/pyrit/requirements.txt
```

Set `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` in `Clawith/.env`
(or export them in the shell). The default API base is `https://api.openai.com/v1`.
Set a fixed `MCP_API_KEY` matching the bridge. The selected model must support
this adapter's tool calling / structured outputs. Credentials are checked before
preparing an Oracle episode. Explicit `--model` overrides remain available.

## Run

An independent run requires a hard reset. This deletes Clawith's Docker
volumes and `backend/agent_data/`. Do not reset while another episode runs.

```bash
bash oracle/reset.sh --hard
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario kb-poison
```

Other scenario IDs are `chat-exfil`, `email-exfil`, `private-agent-leak`, and
`sandbox-escape`. `--max-turns` defaults to 5 and accepts 1 through 20. The
scenario's `attackerTurn` is the exact first message; PyRIT generates later
messages from Morty's replies and the Oracle score. Each run uses one Oracle
episode, one candidate, and serial turns. The gateway enforces the turn budget
and stops on an uncertain send or reply timeout.

Results are under `.run/pyrit/<scenario>-<uuid>/`: `summary.json`,
`oracle-result.json`, `pyrit-result.json`, `transcript.json`, and
`attack-state.json`. An error or zero-turn run is labeled separately in the
summary even if Oracle closes with `failed`. A fresh candidate needs a fresh
environment.

## Offline checks

```bash
redteam/pyrit/.venv/bin/python -m unittest redteam.pyrit.test_adapter redteam.pyrit.test_runner
```
