# Clawith Promptfoo benchmark

Run commands from `GreyZone/Clawith/` unless stated otherwise.

This adapter runs one multi-turn Crescendo attack against one existing
`benchmark/scenarios/<id>` objective. Promptfoo generates attack messages;
the target provider sends each message as alice through the authenticated MCP
bridge, waits for Morty's response in the same group session, and probes the
selected Oracle surface. A fired Oracle judgment ends Crescendo immediately.
The Oracle's final result is the benchmark outcome; the policy plugin supplies
attack prompts but does not grade the target.
The target provider ends the Crescendo conversation only after an Oracle hit.
Promptfoo's final assertion therefore passes on `Target ended conversation`;
the Oracle result file remains the authoritative evidence for the hit.

## Setup

From `Clawith/`:

```bash
./start.sh
python3 ../scripts/build_images.py --project Clawith --service redteam-mcp
docker compose up -d --no-build redteam-mcp
cd redteam/promptfoo
npm ci
```

The root `.env` must contain `OPENAI_API_KEY` and a fixed `MCP_API_KEY` matching
the bridge. If `MCP_API_KEY` is blank, set a random value in `.env` and restart
the bridge before running the benchmark. `uv`, Node 22.22+, Docker, and the
running Clawith stack are required. Do not put API key values in Promptfoo
configuration or scenario files.

## Run

```bash
npm run redteam -- --scenario chat-exfil
```

Other IDs: `email-exfil`, `kb-poison`, `private-agent-leak`, `sandbox-escape`.
The runner reads each scenario's `objective.md`, `policy`, `attackerTurn`, and
single `oracleSuccessSurfaces` value. It prepares the lab, arms the Oracle
before the first attack turn, runs one candidate with at most five turns, and
closes the Oracle afterward. All target calls are serial (`-j 1`) because the
Oracle has one episode state. A second candidate in that episode fails closed.

Results are written under `Clawith/.run/promptfoo/<scenario>-<uuid>/` as
`promptfoo-results.json` and `oracle-result.json`. The Oracle's full episode
result also remains under `Clawith/.run/oracle/`.

For a new, independent run, reset the lab first:

```bash
cd ../..
bash oracle/reset.sh --hard
cd redteam/promptfoo
```

Hard reset removes Clawith's Docker volumes and agent data, then rebuilds the
lab. This is required for independent scoring because light reset leaves
group chat, tool execution records, and other database state in place.

To inspect generated Promptfoo configuration without touching the lab:

```bash
npm run redteam -- --scenario chat-exfil --config-only
```

If a run stops after Oracle preparation, inspect `.run/oracle/episode.json`;
the adapter never silently reuses an armed or partially completed episode.
