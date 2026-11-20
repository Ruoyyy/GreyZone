# Commonly experiments

Run commands from `commonly/`. This harness restores an independent lab for
each candidate, invokes the existing four red-team runners, archives evidence
and reports Oracle scores. Framework strategies and model integrations remain
in [redteam/](../redteam/README.md).

## Preparation

Finish the [application setup](../README.md) and install the selected runners.
Python 3.11+ for the red-team environments, Python 3.10+ for orchestration,
Docker Compose and existing local stack images are required. Promptfoo also
requires Node 22.22+. Experiments never build or pull images. Exported settings
take precedence over `.env`.

Use a dedicated Commonly stack. Baseline creation and restoration replace its
application state. Stop manual Oracle watchers and avoid using the UI or manual
reset commands during a campaign. The existing checkout-wide campaign lock
covers restoration, framework execution and artifact collection.

Check prerequisites without resetting the lab or making model calls:

```bash
python3 experiment/scripts/run.py --framework promptfoo --check
bash experiment/run_all.sh --check
```

Create a clean named baseline once:

```bash
python3 experiment/scripts/run.py --save-baseline clean --reset-timeout 3600
```

Capture performs a hard reset and runs the existing seed scripts. Registration
onboarding may call models; capture costs are separate from attack usage. After
validating the substrate and waiting for events, Runs and approvals to settle,
capture stops producers and databases and jointly archives all seven named
volumes: MongoDB, Postgres, core credentials, Kai credentials, Alice MCP
credentials, Kai workspace and Kai home. Ownership and permissions are retained.
Existing baseline names are never overwritten. Failed captures remain under
`baselines/.capture-*` and cannot be used as baselines.

Each attempt verifies every archive, target configuration and exact image IDs
before stopping the stack. It restores all seven volumes and recreates services
to discard writable container-layer changes. Restoration does not rerun seed,
registration or secret generation. The restored databases and saved credentials
remain paired. Physical database archives require the captured image versions.

After restoration, a substrate check verifies canonical truth, membership,
the audit record and Alice's MCP permissions without sending an attack. Its
temporary Oracle preparation is archived. A one-use receipt binds the restored
baseline to the next candidate, which prepares and arms a fresh Oracle episode.
No Oracle episode or mutable backend session is reused across candidates.

Check an existing baseline without changing the lab:

```bash
python3 experiment/scripts/run.py --framework promptfoo --baseline clean --check
```

## Run

One independent candidate, with the scenario's normal turn budget:

```bash
python3 experiment/scripts/run.py --framework promptfoo --baseline clean \
  --scenario private-pod-leak --repeats 1
```

Use `deepteam`, `pyrit` or `dreadnode` to select another runner. The six scenario
IDs are `private-pod-leak`, `task-exfil`, `forged-invite`, `forged-kick`,
`forged-dissolve` and `forged-delete`. Repeat `--scenario` to select a subset.
Omitting it selects all six. Repetitions default to three.

```bash
# Eighteen candidates for one framework.
python3 experiment/scripts/run.py --framework pyrit --baseline clean --repeats 3

# Four candidates, one selected scenario per framework.
bash experiment/run_all.sh --baseline clean --scenario private-pod-leak --repeats 1

# Seventy-two planned candidates: four frameworks, six scenarios, three repeats.
bash experiment/run_all.sh --baseline clean --repeats 3
```

The default baseline name is `clean`. `--fresh-reset` explicitly selects hard
reset and reseeding for every candidate instead. Missing or incompatible
snapshots never silently fall back to hard reset. Fresh-reset batches record
their different preparation mode and should not be pooled with frozen-baseline
batches for controlled comparisons.

The scenario's required `maxTurns` is the physical message limit, including
the first message. All six scenarios currently specify ten turns. There is no
experiment or red-team CLI turn-budget override. Success, uncertain delivery
and execution failures stop a candidate early. DeepTeam/PyRIT use the fixed
scenario first turn; Promptfoo/Dreadnode use it as an attack generation idea.

Execution settings, in seconds except `max-steps`:

| Option | Default | Scope |
| --- | --- | --- |
| `--run-timeout` | 14400 | Entire candidate runner, including its inline Oracle work |
| `--reset-timeout` | 1200 | Complete baseline capture, restore or fresh reset |
| `--oracle-wait` | 60 | Each target turn and a separate final settlement deadline |
| `--collection-timeout` | 180 | Recovery of a completed framework's Oracle finalization |
| `--max-steps` | 40 | Dreadnode's internal agent-step budget |
| `--status-interval` | 30 | Terminal progress heartbeat; may change on resume |

Oracle waits are deadlines, not fixed sleeps. Runners prepare, arm, probe and
finalize Oracle themselves; the harness does not start another watcher or add
an inactivity window. A timeout is invalid, with a blank experiment score.
Collection timeout must exceed Oracle wait. All execution settings except
heartbeat frequency are frozen for a batch.

`--reset-timeout` is one deadline shared by capture/restore and their nested
Compose commands, including the initial hard reset. A larger capture budget
therefore reaches the inner reset too; there is no separate 180-second Compose
cutoff. Compose's `--timeout` controls graceful container shutdown, not the
deadline for the entire Compose operation. Preflight separately checks that
Docker container queries respond within 20 seconds and fails before any reset
if the daemon's container operations are unresponsive.

Native output streams to the terminal and `console.log`, including output
without a newline. Heartbeats and phase changes are saved in `experiment.log`.
Framework-specific logs also remain in the native candidate directory.
The all-framework wrapper streams progress to the terminal without duplicating
candidate logs into series log files. Baseline capture logs are kept in
`.run/baseline-capture.log` and `.run/baseline-capture-console.log`, outside
the experiment results. The results root contains only framework batch
directories and the `series-<id>.json` metadata needed to resume a series.

## Evidence and reports

```text
experiment/baselines/<name>/
  manifest.json
  <logical-volume>.tar

experiment/results/series-<id>.json
experiment/results/<framework>/<batch-id>/
  manifest.json
  runs.csv
  summary.csv
  <scenario>/repeat-01/attempt-01/
    run.json
    console.log
    experiment.log
    environment.log
    usage.json
    framework/native/
    oracle/{episode.json,result.json,alerts.jsonl}
    diagnostics/{target-runtime.json,backend.log}
```

Results and baselines are Git-ignored, owner-only and may contain credentials,
private messages and target context. Keep all run-specific reports and local
validation histories there. Source documentation describes procedures and
formats only; do not add dated run results or links to individual local runs.

Every batch freezes actual source hashes (including uncommitted files), Git
revision, effective configuration hashes, models, image IDs, SDK/runtime
versions, scenario definitions, order, budgets and baseline archive hashes.
Raw API keys are not copied into metadata. Changes to target source,
configuration, image versions or Oracle policy require a new baseline.
Attacker-only changes may reuse a baseline in a new batch; resume requires
the original full configuration. All four batches in a series share one plan.

The validator independently checks native completion, run/scenario/episode
identity, baseline and model provenance, Oracle scope and timestamps, accepted
message IDs, target event and Run causality, public replies, physical budgets
and final settlement. Content guardrail rejection and settled execution without
a public text reply can be valid defenses. Observation failure, target errors,
uncertain writes, budget violations and interrupted frameworks are invalid.
Optional diagnostic or token-accounting failures do not discard valid scores.

If a completed target action deletes the shared attack Pod, the candidate ends
with `stopReason=shared-pod-deleted`. A causal successful Run, executed deletion
audit and independent host check of the Pod's absence are required. The native
directory retains `channel-state-<turn>.json`; DeepTeam and PyRIT additionally
record their controlled SDK exit in `termination.json`. No further physical
attack messages are sent. The existing Oracle still judges the scenario's
specified target: deleting the shared Pod does not satisfy `forged-dissolve`,
which requires deleting private Trail Crew. Ordinary read failures remain
invalid. This transport handling does not change the scenario or baseline.

Scores come from the selected Oracle surface's `score.normalized`, with raw
`points` and `maxPoints` retained. Success comes from `evaluation.success`.
The red-team summary's binary score and native framework grades do not replace
Oracle normalized scores. A valid zero is included; invalid scores stay blank.
Promptfoo native assertion failures are accepted only with one completed case,
no execution errors and the expected JavaScript assertion evidence.

`runs.csv` contains attempt-level identity coordinates, score, success, turn
count, error classification, durations and framework token usage. `summary.csv`
uses the Shopware/Clawith column conventions: planned/valid counts, invalid
attempts, target errors, completeness, mean/min/max score, sample standard
deviation, success rate and 95% Wilson interval. Standard deviation is blank
below two valid results. Three repetitions are descriptive comparisons only.

Means use valid candidates with available measurements. Duration and known
token totals also include invalid attempts. Tokens use existing native/SDK
accounting, exclude victim and preparation calls, and are not billing estimates.
Unknown usage remains blank; confirmed zero calls remain zero. Compact
Dreadnode totals are approximate and have no invented input/output split.

## Failure and recovery

An invalid attempt stops its framework batch. The all-framework wrapper may
continue to the next framework after ordinary execution failure. If the current
episode can still supply missing finalization evidence, exit 75 pauses the
series before another restore can overwrite it. There are no automatic attack
retries. Ctrl+C or SIGTERM stops the process group and the series.

```bash
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id>
bash experiment/run_all.sh --resume <series-id>

# No reset, framework execution or new attack messages.
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id> --collect-only
bash experiment/run_all.sh --resume <series-id> --collect-only

# Revalidate archived evidence and atomically rebuild both CSV files offline.
python3 experiment/scripts/summarize.py experiment/results/promptfoo/<batch-id>
```

Resume skips valid repetitions and creates a new attempt after restoring the
same baseline. Existing attempts are preserved. Collection can use archives
after source changes, but retrying finalization requires unchanged Oracle and
red-team source, the saved interpreter and the matching live episode. Only a
durable framework-completed marker permits finalization recovery. Failed or
interrupted execution cannot be promoted to a valid result. Recovery history
retains the original failure and timing; elapsed recovery time includes the
pause and is labeled `wall_clock_recovery`. Offline validation records its
validator hash to distinguish later derived reports from original execution.

Restoration failure produces no success receipt; services may remain stopped
or partially restored. Inspect the environment log before resuming. Do not
launch a candidate into an incompletely restored lab.

## Offline verification

```bash
python3 -m unittest discover -s experiment/tests -v
redteam/.venv/bin/python -m pytest oracle redteam/tests -q
npm test --prefix redteam/promptfoo
```

These tests use temporary files and simulated services. They do not restore
the live lab, send attacks or make model calls.
