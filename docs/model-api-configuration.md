# Benchmark model API configuration

Prefer each agent's supported native client. Benchmark entry points using an
OpenAI-compatible client expose the same three variables:

```sh
OPENAI_API_KEY=<your-key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini-2025-04-14
```

The benchmark baseline for all eight OpenAI-backed victim applications is the
fixed GPT-4.1 mini snapshot `gpt-4.1-mini-2025-04-14`, including each application's
chat, planning, summary and vision roles that use this provider. Use the snapshot
rather than a moving alias. It supports tool calling, structured outputs and
image input; see [OpenAI's model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
A model override is a separate experimental configuration, not the baseline.
Keys and endpoints remain configurable. Retained upstream providers do not
change the benchmark's selected provider.

As of November 6, 2026, OpenAI lists this snapshot as deprecated with shutdown
scheduled for January 14, 2027. Preserve the baseline when comparing results;
future baseline changes must be explicit. See [OpenAI's deprecation schedule](https://developers.openai.com/api/docs/deprecations).

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
| Alpaca | `sim/.env` | Claude Code's native API; `ANTHROPIC_MODEL=claude-3-5-haiku-20241022` |
| Shopware | `.env` | Native Messages API / Agent SDK; all roles default to `ANTHROPIC_MODEL=claude-3-5-haiku-20241022` |

The two Claude-backed victim applications use the fixed Claude 3.5 Haiku
snapshot `claude-3-5-haiku-20241022`. Shopware uses it for buyer, merchant,
memory extraction and merchant analysis, with thinking disabled. Alpaca maps
Claude Code's model aliases to the same configured model. These are victim
baselines; attacker and evaluator model settings remain separate.

Anthropic retired this snapshot from its official API on February 19, 2026.
This requested historical baseline therefore requires a compatible endpoint
that still serves it; it will not run against the current official Anthropic API.
Set `ANTHROPIC_BASE_URL` for such an endpoint, or explicitly select a different
experimental model. Never silently substitute another model. See
[Anthropic's retirement schedule](https://platform.claude.com/docs/en/about-claude/model-deprecations).

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
