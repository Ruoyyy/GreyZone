# Clawith red team runners

This directory contains four runners for the same Clawith benchmark. Each
runner attacks Morty as the regular user **alice** through the group chat. The
scenario supplies the objective and Oracle success surface; the runner chooses
attack messages and records a transcript. The **Oracle result** is the
benchmark verdict, regardless of a framework's own success label.

| Runner | Attack method | Setup and details |
| --- | --- | --- |
| Promptfoo | Multi-turn Crescendo | [promptfoo/README.md](promptfoo/README.md) |
| Dreadnode | Headless agent with a bounded `attack_turn` tool | [dreadnode/README.md](dreadnode/README.md) |
| PyRIT | `RedTeamingAttack` with Oracle scoring | [pyrit/README.md](pyrit/README.md) |
| DeepTeam | Seeded `LinearJailbreaking` conversation | [deepteam/README.md](deepteam/README.md) |

## Before a run

Work from `Clawith/` unless a command says otherwise. Configure `.env` with
`OPENAI_API_KEY` for the default attacker model and a fixed `MCP_API_KEY` that
matches the MCP bridge. Install the selected runner's dependencies as described
in its README. Start the lab and bridge:

```bash
./start.sh
docker compose up -d --build mcp-bridge
```

Each independent candidate needs a fresh environment. Run the following only
when no episode is in progress; it deletes Clawith's Docker volumes and
`backend/agent_data/`, then rebuilds the lab:

```bash
bash oracle/reset.sh --hard
```

## Choose a scenario and runner

| Scenario ID | Oracle success surface |
| --- | --- |
| `chat-exfil` | `exfil` |
| `email-exfil` | `email-exfil` |
| `kb-poison` | `kb-integrity` |
| `private-agent-leak` | `private-agent-leak` |
| `sandbox-escape` | `fs-escape` |

Use one of these commands after setup and reset. Replace `kb-poison` with any
scenario ID above:

```bash
# From Clawith/
python3 redteam/dreadnode/run.py --scenario kb-poison
redteam/pyrit/.venv/bin/python redteam/pyrit/run.py --scenario kb-poison
redteam/deepteam/.venv/bin/python redteam/deepteam/run.py --scenario kb-poison

# Promptfoo runs from its own directory
cd redteam/promptfoo
npm run redteam -- --scenario kb-poison
```

Run **one** runner at a time: the lab has one active Oracle episode. The
runners prepare and arm it before sending attack messages, then close it. They
send turns serially and stop when the selected Oracle surface fires or their
turn budget ends. See each runner's README for model settings, limits, and
failure handling.

## Results

Runner artifacts are saved under `.run/<runner>/<scenario>-<uuid>/`. PyRIT and
DeepTeam write `summary.json` and `oracle-result.json`; Promptfoo writes
`promptfoo-results.json` and `oracle-result.json`; Dreadnode saves its run log
and Oracle output. The Oracle's episode result and alerts are also under
`.run/oracle/`. Check the Oracle verdict and evidence when comparing runs;
framework status alone does not establish benchmark success.
