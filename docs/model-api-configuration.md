# Benchmark model API configuration

Prefer each agent's supported native client. Benchmark entry points using an
OpenAI-compatible client expose the same three variables:

```sh
OPENAI_API_KEY=<your-key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=<model-id>
```

Choose a model available to your account that supports the range's tools and
structured outputs. The home range also needs image input. The model is explicit
so configuration cleanup does not silently select a different experimental model.
A compatible service can use its own matching key, model and base URL.

| Range | Configuration file copied from the example | Model connection |
| --- | --- | --- |
| Clawith | `.env` | Native OpenAI-compatible provider; bootstrap configures chat and platform model rows |
| Inbox | `apps/web/.env` | OpenAI-compatible client; the default model chain derives from `OPENAI_MODEL`; explicit role chains still take precedence |
| ZerePy | `.env` | Native OpenAI client; `OPENAI_MODEL` overrides the agent JSON default |
| banking | `app/.env` | Existing OpenAI-compatible Java client |
| commonly | `.env`, using `docker/stack/.env.example` | Existing stack model gateway routes to the configured upstream model |
| kubectl-ai | `.env`, exported before `sim/run-agent.sh` | Existing native OpenAI provider |
| smart-home | `sim/secrets.env` | Existing OpenAI-compatible HGA provider, configured by `sim/configure_hga.py` |
| Letta | `.env` | Native OpenAI provider configured by the pinned CLI; action inherits `openai/${OPENAI_MODEL}` |
| Alpaca | `sim/.env` | Claude Code's native API, using `ANTHROPIC_API_KEY`; native model selection remains optional |
| Shopware | `.env` | Native Messages API / Agent SDK, using `ANTHROPIC_API_KEY`; existing role model defaults remain available |

Alpaca and Shopware keep their native API paths as requested; Letta supports OpenAI natively. This change adds
no protocol gateway. Commonly continues to use its pre-existing gateway. Retained
upstream provider implementations and their own reference documentation remain
available; this page describes benchmark deployment entry points.

## Red-team model configuration

DeepTeam, PyRIT and Promptfoo adapters that already use OpenAI-compatible clients
use `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` for attacker/evaluator
models. Use the adapter's entry script so its generated configuration receives the
selected model and URL. Offline preparation may use a model placeholder and does
not make model requests; executing an attack requires a configured model.

Use the adapter `.env` / shell, or the owning range's documented model file.
Shopware adapters load their own project configuration rather than another
range's credentials. A native target and an OpenAI-compatible attacker may require
separate credentials. Platform login keys, mailbox transport tokens and simulated
service credentials retain their existing meaning.

## Existing deployments

Private `.env` files and running services are not rewritten by this source change.
Compare your configuration with the new example and migrate the three model fields
together, keeping the key matched to its service URL and model. For Clawith rerun
the environment bootstrap to apply model rows; reconfigure HGA using its existing
setup script. Letta also needs the updated seeded Gitea action/workflow (a fresh
benchmark initialization or an explicit update of those repositories); recreating
only its runner does not replace existing repository contents. Compose-based
services receive changed values when recreated.

Record the selected provider, endpoint and model with experimental results.
Changing a model/provider is an experimental change even when the task and Oracle
are unchanged. Deployment preflight and mocked requests do not establish successful
live model execution.
