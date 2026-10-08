# Clawith Dreadnode red team

This capability lets Dreadnode attack Morty as ruoyu in one bounded group-chat
episode. The only exposed tool is `attack_turn`: it sends a group message,
waits for Morty's reply, and checks the selected Oracle surface. The Oracle's
filesystem and message evidence supplies the final verdict. Dreadnode cannot
call the bridge's direct workspace-write tools.

## Setup

From `Clawith/`, start the stack and rebuild the MCP bridge:

```bash
./start.sh
docker compose up -d --build mcp-bridge
```

Set a fixed `MCP_API_KEY` in `Clawith/.env`. Install and log in to a Dreadnode
CLI with capability support (`dn` 2.0.46 was used during development). The
runner defaults to `openai/glm-4.7`, using the existing `ZHIPU_API_KEY` from
`Clawith/.env` through Zhipu's OpenAI-compatible endpoint. Use `--model` or
`DREADNODE_ATTACKER_MODEL` for another configured model. CLI 2.0.46 requires
`DREADNODE_LLM_BASE` and `DREADNODE_LLM_API_KEY` for a `dn/` model; the runner
checks this before preparing Oracle. The CLI's Python
environment must include `fastmcp`, `mcp`, `httpx`, and `websockets`; set
`CLAWITH_PYTHON` to such an interpreter if the CLI launcher does not identify
it. Check the capability without starting an episode:

```bash
dn capability validate --strict ./redteam/dreadnode
```

## Run

An independent run needs a hard reset. This deletes Clawith's Docker volumes
and `backend/agent_data/`. Do not reset while another episode is in progress.

```bash
cd Clawith
bash oracle/reset.sh --hard
python3 redteam/dreadnode/run.py --scenario kb-poison
```

The default is five attack turns and 40 Dreadnode steps. Other scenario IDs
are `chat-exfil`, `email-exfil`, `private-agent-leak`, and `sandbox-escape`.
The runner refuses an existing Oracle episode, prepares and arms a new one,
starts the Dreadnode agent in headless mode, then closes Oracle. Results and
the Dreadnode transcript are under `.run/dreadnode/<scenario>-<uuid>/`.

The MCP server enforces one attack at a time and a hard turn limit. A send
whose outcome is uncertain stops further turns; a reply timeout also stops
the campaign. Each independent candidate requires a fresh environment.
