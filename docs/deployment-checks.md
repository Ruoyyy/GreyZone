# Maintainer Deployment Checks

`scripts/check_deployment.py` checks deployment prerequisites before a release. It is separate from benchmark tasks, attack interactions, and Oracle scoring. Running the benchmark does not require this tool. Range runtime code does not call it, and it does not produce episode verdicts.

## Usage

Run from the repository root:

```bash
# Default: read-only deployment preflight for all 10 ranges
python3 scripts/check_deployment.py

# Select one or more ranges by their top-level directory names
python3 scripts/check_deployment.py --project commonly --project smart-home

# Save a machine-readable maintenance report in a Git-ignored directory
python3 scripts/check_deployment.py --report .deployment-checks/preflight.json

# Also run existing offline regressions; deployment environments remain stopped
python3 scripts/check_deployment.py --regression

# Test the maintenance tool's failure reporting and process isolation
python3 -m unittest discover -s scripts -p test_check_deployment.py
```

The script locates the repository relative to its own file, so it can also be called by absolute path from another working directory. The `--report` path is relative to the caller's working directory. `--timeout` sets the per-command timeout in seconds, defaulting to 120. Commands run serially, Node child processes have a 512 MB heap limit, and Inbox uses one worker. A timeout stops the command's process group; the tool then continues collecting other results.

## What the Checks Establish

By default, the tool checks:

- The presence of listed deployment entry points, dependency locks, key source files, and fixture source files.
- Deployment shell-script syntax with `bash -n`, without executing the scripts.
- The availability of the Docker CLI and required host tools. For kubectl-ai, this also includes Go, kind, and kubectl. This checks availability; pinned versions are verified during release builds.
- Parsing of 11 configurations with Docker Compose, plus the presence of declared local build contexts, Dockerfiles, and additional contexts.

Compose checks explicitly use an empty environment file, disable interpolation, and skip service `env_file` resolution. They do not depend on a developer's local `.env` or `secrets.env`. Compose must support `--no-interpolate`, `--no-env-resolution`, and JSON output. This step does not require the Docker daemon or pull images.

**A preflight PASS establishes only that these static checks passed.** Unresolved runtime variables, actual environment files, bind mounts, Dockerfile COPY inputs, image retrieval, dependency installation, builds, service readiness, MCP interactions, and real model tasks still require deployment acceptance checks. Shell scripts manage kubectl-ai deployment; this tool checks only its entry points, host tools, and listed source files.

Reports record `scope: deployment-preflight` and always mark builds, startup/readiness, local network integration, and model episodes as `NOT_RUN`. The initial tool has no options to start, reset, or destroy environments. Those steps are performed individually in phase 5 of the release preparation plan.

## Status and Dependencies

| Status | Meaning |
| --- | --- |
| PASS | The check ran and passed. |
| FAIL | A source file is missing, a configuration or assertion is invalid, or a command timed out. |
| BLOCKED | A required tool or known dependency is unavailable, or a build context cannot currently be verified locally. |
| NOT_RUN | An optional regression was not selected, or the current phase does not execute this deployment step. |

Any selected check reporting FAIL or BLOCKED produces exit code 1; all selected checks passing produces exit code 0. Unselected regressions do not prevent a successful read-only preflight. Reports retain commands, durations, and error summaries, without exporting the full environment or resolved Compose configurations.

Preflight requires only the Python standard library, Bash, Docker Compose, and the host tools listed below. It does not install dependencies.

Optional regressions reuse each project's test environment: the tool prefers `.venv/bin/python` in the test working directory, falling back to the Python interpreter running the checker. Prepare declared test dependencies, including pytest and Clawith's httpx, in the respective environments. Inbox requires its pinned workspace dependencies to be installed first. The tool does not download dependencies. Dependency probing failures report BLOCKED; test collection or execution errors report FAIL with diagnostics.

## Deployment Entry Points and Preparation

This table is a maintenance index. Use each project's existing entry points for actual deployment commands. Default preflight does not initialize or reset environments.

| Range | Configuration or entry points checked | Initialization and generated files | Deployment acceptance considerations |
| --- | --- | --- | --- |
| Clawith | `docker-compose.yml`; `setup.sh` / `start.sh` / `restart.sh` | `.env.example`; backend entrypoint and setup; runtime data in the respective volumes/directories | Database migrations, agent data, MCP bridge, and application readiness |
| Inbox | Root `docker-compose.yml`; `docker/docker-compose.local.yml`; `docker-compose.mail-loop.yml` | `setup.sh`; `scripts/mail-loop-init.sh` generates simulators and bundles in `.tmp`; application `.env` | Root Compose mainly provides dependency services; local Compose uses prebuilt images; the mail loop needs generated files and webhook configuration |
| Letta | `docker-compose.yml`; `init-gitea.sh` | Compose's gitea-init seeds `repo/` and generates runner/MCP credentials in `shared/` | Gitea, runner registration, and MCP permission levels; task material includes target repository code |
| ZerePy | `docker-compose.yml`; `scripts/init.sh`; `chain/entrypoint.sh` | Initialization generates `.env`, `chain/users.keys.json`, `mock/users.tokens.json`, and related files; chain and mock seed sources are tracked | Services ready after chain seeding; reset must restore simulated chain and mock state |
| banking | `app/compose.yaml`; `app/start-compose.sh` | `app/.env.example`; business services initialize in-memory data | Model configuration, MCP business services, and copilot startup; see the existing Oracle flow for reset and session cleanup |
| Alpaca | `sim/docker-compose.yml` | `sim/.env.example`; `sim/oracle/seed.py` is the existing task preparation tool | Environment/MCP/console services and optional `agent` / `oracle` profiles; simulated world reset |
| commonly | `docker-compose.stack.yml`; stack secrets and agent entrypoint | `docker/stack/.env.example`; `init-secrets.sh` writes separate volumes; `seed-local-stack.ts`; skill catalog JSON | Application, model proxy, agent seat, and user MCP; fixture packaging and reset |
| kubectl-ai | `sim/up.sh` / `sim/up-ops-mcp.sh` / `sim/run-agent.sh` | Go produces `bin/kubectl-ai`; scripts generate kubeconfig, keys, and other files in `sim/.build/` | Docker, Go, kind, and kubectl; actual cluster, RBAC, ops MCP, and collector verification |
| Shopware | `docker/compose.yaml`; `docker/bootstrap.sh` / `docker/verify.sh` / `dev.sh` | Snapshot and bootstrap generate environment configuration, signatures, and integration data; host applications also need Python/Node dependencies | After the Shopware container is ready, verify host applications, MCP, signatures, and handoff; use existing reset tools |
| smart-home | `sim/docker-compose.yaml` | HA configuration; initial setup and `sim/secrets.env`; HGA source as an additional context | HA onboarding, model/database, mock and redteam MCP; optional profiles and state restoration |

Without a corresponding environment and initialization record, this table establishes only that entry points exist. It does not establish that deployment succeeded. Generate and validate missing files through the actual initialization flow instead of creating empty placeholders.

## Optional Regressions

These checks detect whether maintenance changes broke existing logic. Their results appear separately from deployment preflight and do not contribute to official benchmark scoring.

| Project | Checks currently included |
| --- | --- |
| Clawith | `oracle/test_oracle.py` |
| Inbox | Oracle library, emulate seed, fixture adapter, client redirects, and two LLM patch regressions: 7 files in total |
| Letta / ZerePy / banking / Alpaca / Shopware | Each project's existing `oracle/test_oracle.py`, using its existing directory and invocation |
| commonly | Oracle, skill fixture consistency, SDK contract, SEO generation, and nginx route helper tests |
| kubectl-ai | Self-checks run directly through `oracle/test_oracle.py` |
| smart-home | Oracle and runtime file consistency |

The recorded reference baseline is 155 pytest checks across 8 ranges, 16 kubectl self-checks, and an initial 53 Inbox offline tests (3 added during API configuration cleanup, bringing the current count to 56), plus commonly's SDK checks and two native Node tests. These counts are historical records, not quotas.

Inbox's 9 additional `mail-backbone.test.ts` cases are local network integration tests excluded from default regressions. Run and record them separately during deployment acceptance. Existing redteam adapter smoke checks, Shopware live-environment checks, and author-created scripts still require individual review in phase 2 of the release preparation plan. Absence from this entry point alone is not a reason to delete them.

The offline SDK request check for API configuration cleanup is `python3 -m unittest discover -s scripts -p test_openai_config.py`. It requires ZerePy's OpenAI/httpx dependencies and is not included in default deployment preflight.
