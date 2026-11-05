# Clawith Dreadnode red team

This capability lets Dreadnode attack Morty as alice in one bounded group-chat
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

Set `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` in `Clawith/.env`
(or export them in the shell). The default API base is `https://api.openai.com/v1`.
Set a fixed `MCP_API_KEY` matching the bridge. The selected model must support
this adapter's tool calling / structured outputs. Credentials are checked before
preparing an Oracle episode. Explicit `--model` overrides remain available.

Check the capability without starting an episode:

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
