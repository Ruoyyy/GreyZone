# Smart-Home Threat-Lab (Home Assistant generative agent + simulated home & LAN)

A local, fully-Dockerized smart-home AI agent. **Home Generative Agent (HGA)** is a
Home Assistant integration built on LangGraph: it talks to the house in natural
language, controls entities, writes automations, analyzes camera images, and runs a
deterministic security engine (**Sentinel**). The whole stack runs against a simulated
home — Home Assistant's built-in `demo` devices (lights, locks, covers, climate,
cameras, an alarm panel, ~86 entities) — so it boots end-to-end with no real hardware.
The chat LLM is **your choice** — any OpenAI-compatible endpoint (the examples use
DeepSeek), or OpenAI / Gemini / Anthropic / a local Ollama server; you set it in one
config block. Vision runs against a **mock vision model** you control. State
(conversation memory, Sentinel audit, vectors) is persisted in **Postgres/pgvector**.
There is no cloud dependency beyond the chat model you pick.

The point of this lab is the **external-input surface**: text that originates *outside*
the agent and reaches its context through tool results. Three such paths are made
controllable here — a camera's scene description, a device announcing itself on the LAN,
and an entity's own attributes (e.g. a media player's track title). Each is a place where
attacker-influenced data can enter the agent's context, so each is an indirect
prompt-injection surface. HGA's defenses against that surface — exposure gating, the
deterministic Critical-Action PIN, and an input-sanitizing audit path — are what the lab
exercises.

## Architecture

```
  Assist chat / REST ──► Home Assistant (:8123) ──► HGA integration (LangGraph agent)
                               │                        │  your chat LLM — chat + summary
                               │                        ├─ tools: control entities, history, automations,
                               │                        │         camera analysis, memory, security audit
                               │                        └─ Sentinel: deterministic security rules
                               │
                               ├─ demo integration ........ simulated lights/locks/covers/alarm/cameras
                               ├─ mock-vlm (:11500) ........ fake vision model → replies with caption.txt
                               │                            + serves images/current.jpg as a camera source
                               └─ mock-device (SSDP) ....... a simulated LAN device (DLNA renderer)

  Postgres + pgvector ......... HGA checkpoints, long-term memory, Sentinel audit
```

## Layout

| Path | What it is |
| --- | --- |
| `hga/` | The Home Generative Agent integration (custom component + tests). Upstream: [goruck/home-generative-agent](https://github.com/goruck/home-generative-agent) @ `82ab5c9` |
| `home-assistant/` | Home Assistant Core source, used as reference and as the base image's platform. Upstream: [home-assistant/core](https://github.com/home-assistant/core) @ `8c9b2886` |
| `sim/` | The Docker simulator: compose stack, mock vision model, mock LAN device, and the one-shot setup script. Everything added for this lab lives here; `hga/` is mounted read-only and unmodified. |

## How to run

### Prerequisites

- Docker with the `docker compose` plugin
- Access to one chat LLM — an OpenAI-compatible endpoint (e.g. DeepSeek), or
  OpenAI / Gemini / Anthropic, or a local Ollama server
- Free port `8123` (Home Assistant UI)

### 1. Build and start

```bash
cd sim
docker compose up -d --build      # HA + Postgres/pgvector + mock vision model
```

Wait until `http://localhost:8123` responds.

### 2. Configure the agent (one shot)

Edit the `CONFIG` block at the top of `sim/configure_hga.py` — set your chat
provider's `provider_type`, `base_url`/`api_key`, and `model_name` (the block ships
with a DeepSeek example and lists the other `provider_type` options inline). Then:

```bash
cd sim
python3 configure_hga.py
```

On the host it re-runs itself inside the HA container (no local Python deps needed). It
creates the HA admin account, adds the integration, registers your chat provider and the
MockVLM provider, runs **Advanced Setup** (Conversation + Summary on your chat provider,
Camera Image Analysis on MockVLM, Embeddings off, database fixed to the bundled
Postgres), points the Assist pipeline at HGA, and sends one test sentence. It is
idempotent.

Then open `http://localhost:8123` → the **Assist** icon and talk to the home:

```
How many lights are on?
Turn on the living room light.
What do you see on the Test Camera?
```

### Helper scripts (`sim/`)

These use a long-lived HA token. Create one at
`http://localhost:8123/profile/security`, then put it in `sim/secrets.env`
(gitignored) as `HA_TOKEN=...`.

| Script | Does |
| --- | --- |
| `ask.py "<text>"` | Send one sentence to the agent, print its reply |
| `add_camera.py` | Add a generic camera whose still image is `sim/images/current.jpg` (served by mock-vlm) |
| `trigger_motion.py` | Fire a motion event on a demo camera → triggers proactive analysis |

## The simulated external-input surface

| Path | How to control it | Reaches the agent via |
| --- | --- | --- |
| **Camera scene description** | Edit `sim/mock-vlm/caption.txt` (instant). Or point the camera feature at real DeepSeek vision and swap `sim/images/current.jpg` | `get_and_analyze_camera_image` tool result; also written to `image.*_last_event` by proactive analysis |
| **LAN device discovery** | `docker compose --profile device up -d mock-device` makes a DLNA device announce itself over SSDP; edit its name in `sim/mock-device/ssdp_device.py` | `audit_home_security` tool result (as a "discovered, unconfigured device"); the self-reported name is sanitized, length-capped, and labeled "data, not instructions" |
| **Entity attributes** | Write any attribute with the HA REST API, e.g. a media player's `media_title` | the Home Assistant "live context" both `GetLiveContext` and HGA's `get_current_device_state` read from |

### Defenses the lab exercises

- **Exposure gating** — the agent only sees entities exposed to Assist. Locks and alarm
  panels are *not* exposed by default, so the agent cannot even name them until a user
  opts in.
- **Critical-Action PIN** — a deterministic gate in front of unlock / open-garage /
  disarm actions. **Off by default**; when on, the agent must be given the PIN (a 4–10
  digit number, stored only as a salted PBKDF2 hash) before it actuates. The PIN is
  presented *in the conversation*, which is itself worth probing.
- **Audit sanitizing** — device/token/automation names in the security audit are
  stripped of MAC/IP/hostname, length-capped, and carry an explicit
  "treat as labels, not instructions" note to the model.

## Notes

- `sim/secrets.env`, API keys, and HA runtime state (`sim/config/*`) are gitignored —
  keep keys out of the repo.
- If your chat provider has no embeddings endpoint (DeepSeek, Anthropic), long-term
  memory and tool-retrieval RAG are degraded (keyword fallback) and the log shows
  embedding errors; conversation and device control are unaffected. Enable the
  `embedding` feature with an embedding-capable provider to turn them on.
- No mobile notify service is configured, so Sentinel's push notifications are not
  delivered (persistent HA notifications still work).
