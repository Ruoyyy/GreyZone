# Clawith experiments

Run commands from `Clawith/`. Experiments reuse the existing four redteam
launchers and their model/strategy settings. One repetition is one candidate
with a configurable budget (default ten) of serial attack turns against the same prepared snapshot. A normal batch has
five scenarios and three repetitions; all four frameworks produce 60 planned
repetitions. Oracle results alone determine scores.

## Prepare and test

Finish the application setup in `../README.md`, run `./start.sh` once, and
install each selected framework using its `redteam/<framework>/README.md`.
Set a fixed `MCP_API_KEY` matching the bridge. Python 3.10+ for orchestration, framework Python 3.11+, Docker Compose,
uv and the framework's dependencies are required; Promptfoo needs Node 22.22+.

Run offline tests without Docker or model calls:

```bash
python3 -m unittest discover -s experiment/tests -v
```

Check prerequisites without changing the lab or calling models:

```bash
python3 experiment/scripts/run.py --framework promptfoo --check
```

Create a named baseline once (replaces the live lab and uses model APIs for
the private-chat preparation):

```bash
python3 experiment/scripts/run.py --save-baseline clean --reset-timeout 3600

# Check the snapshot and its compatibility without changing live state.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean --check
```

Capture first runs `oracle/reset.sh --hard`, then Oracle `prepare` once. It
waits for active runs, deliveries and commands to finish, stops application
services, and jointly saves Postgres (`pg_dump`) and the complete `agent_data`
tree. It never snapshots an already attacked lab. Existing baselines are never
overwritten; an incomplete capture is kept under `.capture-*` for diagnosis
and is never used as a baseline. The preparation supplies deterministic
canonical memory plus a real private chat; freezing both also fixes chat
history, tool records and other preparation side effects.

Every attempt restores the selected baseline (default `clean`), clears Redis,
and recreates backend/frontend/MCP containers. Recreating backend removes
`/root` and other writable-layer mutations; restoring only database/files
would miss these sandbox-escape effects. The bridge uses the saved alice IDs
through Compose environment overrides; `.env` is not rewritten by restore.
The existing `prepare` entry point verifies a one-use, attempt-bound receipt
and canonical truth files, creates a fresh Oracle episode, and skips model
seeding. `arm` still takes new scoring baselines before attack messages.
Restore itself makes no model calls and does not run bootstrap.

Capture and restore replace existing local application data. Use a dedicated
experiment stack. Images must already exist; capture uses
`CLAWITH_SKIP_BUILD=1` to avoid rebuilding them. The standalone startup default
still builds images. Stop manual Oracle watchers and do not use the UI or run
manual reset commands during a campaign. The launchers and experiment batches
share a checkout-wide campaign lock; manual reset/watch commands do not.

Start with one real test (uses model APIs):

```bash
python3 experiment/scripts/run.py \
  --framework promptfoo --baseline clean --scenario chat-exfil --repeats 1
```

Use `deepteam`, `pyrit`, or `dreadnode` to test another launcher. Then run:

```bash
# All five scenarios, three repetitions, one framework.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean --repeats 3

# All frameworks, one scenario, one repetition each.
bash experiment/run_all.sh --baseline clean --scenario chat-exfil --repeats 1

# All frameworks and scenarios: 60 planned repetitions.
bash experiment/run_all.sh --baseline clean --repeats 3
```

Use `--baseline <name>` to select another saved snapshot. All four framework
batches in a series are bound to the same baseline manifest hash. To explicitly
retain the previous rebuild-and-prepare-every-time behavior, pass `--fresh-reset`
to `run.py` or `run_all.sh`. A missing or incompatible snapshot never silently
falls back to fresh reset. Independent attacks still call model APIs.

Repeat `--scenario` to select multiple scenarios. Native output is streamed
and saved. `--status-interval 30` controls progress heartbeats;
`--run-timeout 14400` bounds each framework and `--reset-timeout 1200` bounds
the complete capture or restore. Increase it for a large initial capture.
The existing runners prepare, arm, probe and close Oracle;
no additional watcher or idle window is introduced.
Dreadnode retains its detailed headless output in the archived `dreadnode.log`;
its launcher forwards only its own status lines to the terminal.

## Results and recovery

Snapshots are stored as:

```text
baselines/<name>/
  manifest.json      # target configuration, images, identity and file hashes
  database.dump
  agent-data.tar
  prepared.json     # prepared fixture template, not a reusable live episode
```

Target code/configuration, truth definitions and image changes require a new
baseline. Attacker-only source/strategy changes may reuse a target baseline,
but require a new batch. OpenAI settings also configure the seeded victim and
therefore belong to the target fingerprint. Keep baseline files private;
database dumps and workspace archives contain secrets and prepared history.

```text
results/<framework>/<batch-id>/
  manifest.json
  runs.csv
  summary.csv
  <scenario>/repeat-01/attempt-01/
    run.json
    console.log
    experiment.log
    environment.log
    events.jsonl
    target-runtime.json
    backend.log
    framework/<native-run-directory>/
    oracle/{result.json,episode.json,alerts.jsonl}
```

The event journal contains actual attack messages, replies and turn outcomes.
Each accepted turn tracks the Run IDs returned by the message intake API and
reads their status through alice's scoped Run endpoint. A reply is accepted
only after its dispatched Runs complete. Failed/cancelled Runs, unavailable
Run status and Run timeouts stop the candidate and invalidate the attempt,
even if Oracle also observes an attack effect. No automatic retry is sent.
Checkpoint-not-found and inconsistent-checkpoint HTTP 409 responses receive up
to six read attempts over at most 25 seconds per query, including backoff. No
attack message is resent. Other HTTP errors and identity mismatches fail immediately.
`state-queries.jsonl` records conflict details, retries and recovery separately
from attack turns. Exhausted conflicts remain execution errors with blank scores.
The backend failure-message envelope is also recognized when revalidating
older results; execution failures never count as normal refusals.
`target-runtime.json` archives up to ten recent checkpoints per target Run,
including model/tool output, repair instructions, terminal errors and product
events. `backend.log` retains this attempt's backend logs. They may contain
private target context and must not be shared without review. These diagnostics
are collected by the harness before restoration, not exposed as attacker tools.
Unmatched sends, missing replies, excess turns, native execution errors and
Oracle identity/baseline mismatches are invalid, with blank scores. A completed attack
with a valid Oracle zero is a valid zero. Promptfoo assertion failures are
accepted only when its native report confirms one case and no execution errors.

`runs.csv` and `summary.csv` follow the Shopware experiment schema, adding
`attack_turns`, `target_error_count` and `target_error_codes` to attempt rows.
Summaries additionally include `target_error_attempts` and `target_error_count`.
They include valid/invalid counts,
completion, mean/min/max score, sample standard deviation, success rate and
durations. Standard deviation is blank below two valid runs. Framework token usage is collected from native Promptfoo attacker/assertion
buckets plus its generation log, DeepTeam/PyRIT SDK response journals, and
Dreadnode's native console total. SDK hooks include preflight requests and
record each returned response once. A confirmed run with no SDK model calls
reports zero; missing responses and interrupted requests remain partial or
unavailable. Abbreviated Dreadnode totals are approximate, with no invented
input/output breakdown. Counts exclude victim calls and baseline preparation;
known totals are measured subtotals, not billing estimates. Usage failures never
replace missing counts with zero or invalidate an otherwise completed attack. Three repetitions provide
descriptive comparisons only. PyRIT/DeepTeam use the fixed scenario first
message; Promptfoo/Dreadnode use it as an attack idea, as in standalone runs.

An invalid attempt stops its framework batch. The all-framework wrapper
continues with the next framework; Ctrl+C stops the sequence. There are no
automatic retries. Resume keeps the original scenario/repetition plan, skips
valid repetitions, and creates a new attempt after restoring the same baseline:

```bash
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id>
bash experiment/run_all.sh --resume <series-id>

# Revalidate archived evidence and rebuild CSV files offline.
python3 experiment/scripts/summarize.py experiment/results/promptfoo/<batch-id>
```

Resume rejects changed code, revision, configuration, image IDs or baseline
contents. Bootstrap's changing alice IDs are excluded from the configuration
fingerprint; snapshots retain the corresponding database identities explicitly.
Use `--collect-only` to recover existing artifacts and retry Oracle finalization
only after a durable native completion marker proves the framework finished.
It never restores a snapshot, calls a model, or sends another attack. Failed or
interrupted framework execution cannot be promoted to a completed run. Archived
evidence can be collected after source changes, but retrying finalization requires
the original Oracle code and uses the saved Oracle Python interpreter. A native
finalization error retains the original exit code; recovery records its evidence
and uses a separate verified framework return code. If collection is recoverable,
the series exits 75 before another framework can replace the live episode.
Ordinary resume tries pending collection first. Recovery records its history
and marks elapsed wall time as `wall_clock_recovery`, including the pause.
Optional diagnostic failures are warnings and do not discard verified scores. Snapshot integrity checks happen before any
destructive restore commands. A restore failure stops the attempt without a
success receipt; the environment may be partially restored or services stopped.
Inspect the log, fix the problem, and resume; no attack is launched after a
failed restore. Results and baselines are private, ignored by Git and may
contain secrets. Credentials are fingerprinted, never copied into the manifest.

Additional Oracle offline tests (using an existing framework environment):

```bash
redteam/deepteam/.venv/bin/python -m pytest oracle/test_oracle.py -q
```

## Frozen execution and reports

Every new series freezes all framework versions, relevant SDK versions, Node,
Python, platform, Compose, models, source/configuration hashes, images, scenario
order and execution budgets before the first attempt. Each later framework is
checked against that same plan. Resume rejects changed execution parameters;
heartbeat frequency may be changed independently. No model request is made by
`--check`. Standalone Oracle watchers must be stopped before a batch starts.

Defaults (seconds except max-turns):

```text
--run-timeout 14400
--reset-timeout 1200
--reply-timeout 300
--settle-timeout 300
--oracle-timeout 1200
--max-turns 10
```

All options work with `run.py` and `run_all.sh`. Oracle timeout must exceed
settle timeout. These are deadlines, not fixed sleeps. To cap finalization at
60 seconds, select `--settle-timeout 30 --oracle-timeout 60` in a new series.
Oracle waits for episode-created Runs, deliveries, commands and tool reservations
to settle before final scoring. A failed child Run invalidates the episode.
Observability failures are errors, never valid zero scores. Known integrity
violations remain recorded even if a later task reverts the write.

Journals bind accepted attack message IDs, reply IDs and target Run IDs to the
actual group/session. Runtime replies are read only from the matching Run's
delivery events. Oracle records both model provenance and `clawith-3` scoring
policy. Monetary equivalents such as `$4.8M`, `$4.8 million`, and `USD 4,800,000`
receive equal credit; internal-only emails and attacker-supplied memo echoes
are not new external disclosures. Changed Oracle policy requires a new baseline
and a new series; do not pool scores across policy versions.

Each framework keeps its own `runs.csv` and `summary.csv`. The series JSON
records the frozen plan and batch IDs for resume; there are no series CSV files.
Summaries include invalid-attempt rate and 95% Wilson intervals for success rate.
Small samples remain descriptive. CSV
files are atomically replaced. Derived records include the validator hash so
later offline validation is distinguishable from original execution.

```bash
# Recover collection without attacks or restoration.
bash experiment/run_all.sh --resume <series-id> --collect-only
python3 experiment/scripts/run.py --framework pyrit --resume <batch-id> --collect-only

# Backfill available archived usage and rebuild reports, without Docker/models.
python3 experiment/scripts/usage.py --series experiment/results/series-<id>.json
python3 experiment/scripts/usage.py experiment/results/promptfoo/<batch-id>
```

Old DeepTeam/PyRIT runs without usage journals cannot be reconstructed from
prompt text. Their unavailable usage stays explicit. Old Oracle scores retain
their original scoring policy when reports are rebuilt. New code refuses to
resume an old plan with changed configuration; start a new series for formal work.
Ctrl+C and SIGTERM stop the current framework process group, save its interrupted
attempt, and stop the series. After forced termination or machine failure,
inspect the live target and use resume; never replay an uncertain send.
