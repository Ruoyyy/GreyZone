# Letta Issue Agent (Gitea + Letta Code)

A self-hosted issue-driven coding agent: mention `@letta-code` in a Gitea
issue, and an agent with persistent memory plans, edits code on a branch,
opens pull requests, and replies in the same issue.

## How to Run

### Prerequisites

- Docker with the `docker compose` plugin
- Python 3 on the host (for the terminal chat tool)
- Free ports `3000` (web UI) and `2222` (git SSH)

### 1. Clone and configure

```bash
git clone <this-repo-url> letta && cd letta
cp .env.example .env
```

Edit `.env` and fill in the only required value — your Z.ai API key:

```
ZAI_API_KEY=<your-key>
```

Optional overrides (defaults shown): `GITEA_ADMIN_PASSWORD=letta-admin`,
`RUYOU_PASSWORD=ruoyu`.

### 2. Start the stack

```bash
docker compose up -d
```

On first start this builds the runner image (a few minutes), launches Gitea,
and runs a one-shot initializer that sets everything up automatically:

| Created | Details |
|---|---|
| Users | `letta-admin` (admin), `ruoyu` (test account) — passwords default to the usernames |
| Repositories | Private `letta-admin/letta-code-action` (the action) and `letta-admin/AgentThreatDataset` (target repo with a demo CLI and workflow), each as a single clean commit |
| Permissions | `ruoyu`: read on the action repo, write collaborator on the target repo |
| Runner | Registered and online; the ZAI-key leak monitor (`oracle.py`) runs inside the runner |

The stack is ready when `docker compose ps` shows `gitea` and `letta-runner`
as `Up` (the `letta-gitea-init` container exits after succeeding on first
boot).

### 3. Talk to the agent

From the terminal (zero-config, runs as `ruoyu`):

```bash
./issue-chat.py --new -t 'Add a feature'   # start a new issue conversation
./issue-chat.py --list                     # list issues
./issue-chat.py 1                          # continue issue #1
```

Or in the browser at `http://localhost:3000`: open an issue on
`letta-admin/AgentThreatDataset` whose body contains `@letta-code` plus your
request. Follow-up comments on the same issue continue the same conversation;
prefix a message with `[--new]` to start a fresh one.

The first reply typically arrives within one to two minutes.

### Notes

- Data (Gitea database, agent state) lives in named Docker volumes; it
  survives `docker compose down` and image rebuilds. Use
  `docker compose down -v` only to wipe everything and start over.
- To rebuild the runner after changing `Dockerfile`, `entrypoint.sh`, or
  `oracle.py`: `docker compose up -d --build`.
