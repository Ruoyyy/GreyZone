# Release Cleanup Review

This log records completed cleanup decisions after model API normalization.
The [release preparation plan](release-preparation-plan.md) defines the remaining
release work. Phase 2 is in progress; cleanup is not deployment acceptance.

## Working Rules

- All newly added or modified code, comments, docstrings, documentation, and
  commit messages must be in English. Preserve existing benchmark task data.
- Check task, runtime, build, package, and script references before removing a
  candidate. A `test`, `smoke`, or `demo` filename alone does not justify deletion.
- Keep project changes in separate commits. Run checks serially and use bounded
  resources; file cleanup does not require a full application build.

## Model API Documentation

Completed in `529a74b01`:

- Corrected the Inbox and Shopware DeepTeam/PyRIT environment examples to match
  their actual credential loaders. Removed references to `LLM_API_KEY` and
  credentials from unrelated ranges.
- Updated ZerePy, banking, kubectl-ai, and smart-home deployment descriptions to
  match the `OPENAI_*` entry points and default OpenAI endpoint.
- Preserved kubectl-ai's historical model-specific probe result and labeled it
  as historical, rather than attributing it to a newly selected model.

Eight ranges use `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`.
Alpaca and Shopware retain their native Anthropic clients. Upstream optional
provider implementations are outside this documentation cleanup.

## Commonly: Obsolete Demo and Hosted-Service Scripts

The following paths are deletion records, not supported command entry points.
Their last retained source is available at `529a74b01` under
`commonly/scripts/`.

| Removed script | Purpose and external requirements | Decision |
| --- | --- | --- |
| `demo-bootstrap.sh` | Starts the older local Compose setup, registers users manually, and prints the archived three-minute product demo flow. | Remove the superseded demo launcher; retain the benchmark stack and its initializer. |
| `setup-demo.sh` | Resets a `commonly-dev` Kubernetes demo, changes upstream GitHub issues, and prints a recording storyboard. Requires kubectl and gh. | Remove the upstream recording workflow. |
| `seed-yc-demo.js` | Seeds a marketing pod and illustrative chat messages on the upstream development API. | Remove; benchmark accounts and tasks use a separate initializer. |
| `verify-demo.sh` | Runs the demo reset and an optional `e2e/reviewer-journey.spec.ts` that was already removed. | Remove the broken demo wrapper with its private helper chain. |
| `reset-demo-account.sh` | Changes installations, sessions, chat, and reactions for the old `sam-demo` / `nova-demo` storyboard using cluster database access. | Remove; it does not reset the benchmark's admin/alice world. |
| `recover-codex-auth.sh` | Uploads three operators' Codex OAuth credentials to GCP Secret Manager, restarts cluster services, and runs the old demo smoke. | Remove the upstream cloud operations workflow. |
| `smoke-test-demo.sh` | Checks product-demo UI/API flows, live model replies, BYO agents, and a cluster OAuth rotator using legacy demo credentials. | Remove with its callers; it is not a benchmark readiness or scoring check. |
| `smoke-stranger-path.mjs` | Tests a hosted customer's registration, cloud agent provisioning, paid-tier cap, and live model reply against the upstream service. | Remove the hosted-product acceptance script; preserve local MCP and Oracle checks. |
| `trigger-heartbeat.sh` | Gets an admin JWT from `commonly-dev` and triggers a named demo agent's heartbeat. | Remove the helper used by the recording workflow. |

Before deletion, tracked reference searches found only references within this
deletion set, archived upstream documentation, and the release plan's cleanup
candidates. No retained runtime, package,
Compose, Dockerfile, benchmark scenario, Oracle, or active CI caller referenced
these scripts. Archived documents remain historical source material and may name
the removed scripts. No script in this set was executed during review.

Preserved:

- `backend/scripts/seed-local-stack.ts`, `docker/stack/`, and the benchmark stack
  configuration, including admin/alice accounts, Scout, Kai, and user MCP setup.
- `benchmark/`, `oracle/`, task fixtures, and both skill catalog copies.
- `external/`, including files mounted by Compose.
- Existing Oracle and fixture regressions, SDK contract checks, SEO and nginx
  routing tests, and the deployment preflight entry point.
- Other maintenance and integration scripts pending their own reference and
  assertion review. They were not removed merely because they are not imported
  by runtime source.

Validation:

- `python3 scripts/check_deployment.py --project commonly --regression`: passed
  source, tool, shell syntax, and stack Compose checks; 36 pytest checks; SDK
  contract checks; and the existing SEO/nginx Node tests.
- From `commonly/`, `docker compose --env-file /dev/null -f docker-compose.dev.yml
  config --no-interpolate --no-env-resolution -q`: passed without reading private
  environment files.
- Tracked-reference searches found no retained active caller of any deleted
  script. Task fixtures, runtime initialization, deployment inputs, package
  manifests, and lockfiles have no diff.
- `git diff --check` passed. The new review document contains no Chinese text.

No images were built, services started, remote demo accounts changed, or model
requests made. These results do not establish live deployment readiness.
