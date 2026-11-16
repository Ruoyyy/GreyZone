# Inbox Zero Experiments

Run one framework at a time, with independent repetitions per scenario.
The default is three repetitions; use `--repeats 1` for a connectivity test.
Models, strategies, dependencies and scenario budgets remain in `../redteam/`
and `../benchmark/`. This directory orchestrates runs and reports Oracle scores.

## Prepare

Run commands from `Inbox_Zero_Mail_Agent/`. Python 3.11+, Docker Compose,
Node.js 24 and pnpm must be available. Prepare the mail environment using
the [application guide](../README.md), and install the selected framework's
dependencies using its existing `redteam/<framework>/README.md`.

Keep the mail stack running. Stop any manually started Oracle watcher with
Ctrl+C; the experiment manages its own watcher. Do not send manual mail or
run another campaign during an experiment. The shared campaign lock covers
reset, framework execution and Oracle collection in this checkout.

Create a clean baseline once:

```bash
python3 experiment/scripts/run.py --save-baseline clean
```

Unlike Letta's volume snapshot, this creates and migrates a temporary empty
Postgres database, saves its schema dump, then deletes that temporary database.
It does not copy previous attacks or modify the live application database.
An existing baseline is never overwritten. The database container must be up.

Each attempt replaces the live application's database with this pristine
baseline, clears this stack's Redis, restarts the in-memory Gmail emulator,
resets the backbone subscription state and customer outbox, then reuses
`mail-loop.sh up` for migrations, headless login, fixture seeding and watch.
Fresh canary values are generated per attempt with the same fixture definitions
and scoring denominators. This restores initial conditions rather than merely
starting a new mail thread. Use a dedicated local experiment environment.

Before replacement, previous database, Redis, backbone, customer mail and
Oracle files are saved under `backups/<id>/`. The live emulator mailbox is
exported before it stops. That export preserves evidence, not a restorable
OAuth/emulator snapshot. Existing framework outputs remain in `.tmp/`.
Backups, baselines and results are private, ignored by Git, and may contain
secrets or mail content. No API keys are copied into public run metadata.

Changing target configuration or fixture/source definitions requires a new
baseline. Attacker configuration can change for a new batch. Resuming a batch
requires its original configuration. The baseline captures initial schema;
source code, images and the `.env` are not restored from it.

## Run

Run all four frameworks sequentially with one command:

```bash
bash experiment/scripts/run_all.sh

# Override repetitions, or select a single scenario for all frameworks.
bash experiment/scripts/run_all.sh --repeats 1 --scenario knowledge-leak
```

The wrapper checks all framework prerequisites first, then runs Promptfoo,
DeepTeam, PyRIT and Dreadnode in that order. Defaults are `clean`, all three
scenarios, three repetitions per scenario and a 60-second Oracle idle window:
36 attempts in total when all succeed. Each framework still gets a separate
batch and result directory. An invalid attempt still stops its framework's
batch, but the wrapper records that failure and starts the next framework.
It prints completed/incomplete frameworks at the end and returns nonzero if
any batch failed. Ctrl+C or termination stops the sequence immediately.
Prerequisite check failures stop before any batches start. Use `run.py`
directly to recover an incomplete batch. Starting a later framework resets
live state, so any later recovery must use archived evidence or a new attempt;
live collection recovery should be done before this automatic continuation.
Other run options are forwarded, so `--baseline` can select another prepared
baseline. No model calls occur until a framework batch starts.

```bash
# One framework, one scenario, one independent run.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean \
  --scenario knowledge-leak --repeats 1

# All three scenarios, three repetitions each, for one framework.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean --repeats 3

# The same interface works for every framework.
python3 experiment/scripts/run.py --framework deepteam --baseline clean \
  --scenario knowledge-leak --repeats 1
python3 experiment/scripts/run.py --framework pyrit --baseline clean \
  --scenario knowledge-leak --repeats 1
python3 experiment/scripts/run.py --framework dreadnode --baseline clean \
  --scenario knowledge-leak --repeats 1
```

The other scenario names are `mail-history-leak` and `phishing-outbound`.
Configure one case/test per launcher. Preflight rejects missing dependencies
and incompatible case counts. Framework commands and native model/strategy
configuration are reused. Dreadnode uses its guarded `mail_send_and_wait` tool.

Oracle starts before any attack and signals readiness. Only the selected
scenario's monitors are enabled. While the framework runs, an experiment-only
hold keeps one episode open, including gaps between attack turns. After the
framework exits, the hold is removed. Pending rule/action execution prevents
finalization; the Oracle otherwise closes after an idle window, default 60s.
The standalone watcher's default remains 300s. All overrides are per process;
the script does not edit `.env`.

Useful options (seconds):

```text
--run-timeout 14400         Framework process deadline
--oracle-idle-timeout 60    Post-framework episode inactivity window
--oracle-timeout 1200       Maximum Oracle collection wait
--status-interval 30        Progress heartbeat interval
```

Keep the collection timeout longer than the idle window and pending target
work. The scenario's own reply deadline is unchanged. Native stdout/stderr are
forwarded live and saved in `console.log`; phase changes and regular heartbeats
are saved in `experiment.log`. Oracle stdout is also forwarded and saved
separately. Tool output buffering remains controlled by each framework.

## Outputs

```text
results/<framework>/<batch-id>/
  manifest.json
  <scenario>/repeat-01/attempt-01/
    run.json
    console.log
    experiment.log
    environment.log
    previous-state.json
    canaries.json
    mail-events.jsonl
    conversation.json
    mail/
    framework/
    oracle/
      console.log
      result.json
      alerts.jsonl
  runs.csv
  summary.csv
```

`mail-events.jsonl` journals each write intent and accepted message ID at the
shared customer transport. It is also used to reject uncertain writes, extra
threads, budget violations and mismatched Oracle episodes. Oracle run ID,
scenario and the complete set of `attackMessageIds` must match. Native reports
and actual outbound mail are retained; framework grades never supply scores.

`summary.csv` has one row per scenario: planned/valid repetitions, invalid
attempts, completeness, mean/max/min score, sample standard deviation,
success count/rate, durations and known framework token totals/means.
`runs.csv` provides attempt-level evidence and failure reasons.

Scores come from the single in-scope Oracle surface's `score.normalized`;
success comes from `evaluation.success`. A valid zero counts as zero. Missing
or erroneous Oracle results, execution failures and uncertain writes are
invalid, not zero. A phishing campaign's explicit no-customer-reply result
can be valid when the Oracle proves actual outbound propagation and the
write journal and native summary agree. Other framework errors remain invalid.
Promptfoo exit 100 is accepted for evaluated policy failures without execution
errors; it does not mean the experiment failed.

Time includes reset, application startup, framework execution and collection;
framework execution and Oracle wait are also recorded separately. Framework
token sources follow Letta: Promptfoo's aggregate attacker/assertion/generation
buckets, DeepTeam's native model usage, a PyRIT response-usage hook, and
Dreadnode's headless console total. Compact Dreadnode values are approximate.
Missing usage remains unavailable, not zero. These exclude the victim's usage
and do not represent complete billing. Invalid attempts also consume resources
and are included in total known time/tokens. Coverage counts expose missing data.

## Recovery and checks

A batch stops after the first invalid attempt. Investigate its logs before
continuing. Resume skips valid repetitions, restores initial state and creates
a new attempt for an unfinished repetition; uncertain emails are never resent
within the old campaign.

```bash
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id>

# Accept only the documented no-reply handling/validator repair files.
# Changed models, scenarios, credentials or unrelated source files are rejected.
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id> --accept-repair

# Collect an existing result without restoring state or sending attacks.
python3 experiment/scripts/run.py --framework promptfoo --resume <batch-id> --collect-only

# Rebuild CSV files offline, without Docker or model calls.
python3 experiment/scripts/summarize.py --framework promptfoo --batch <batch-id>

# Revalidate existing archived evidence after fixing a result validator.
# No environment reset, new attacks or model calls; original timing is retained.
python3 experiment/scripts/summarize.py --framework deepteam --batch <batch-id> --revalidate

python3 -m unittest discover -s experiment/scripts -p 'test_*.py' -v
```

Collection recovery requires the original result to remain available under the
live Oracle directory; use it before another reset. It cannot reconstruct an
episode that was never watched. Recovery retains the original error, marks
wall-clock timing, and includes time between invocations. A short or incomplete
batch is reported as incomplete, not as the requested number of valid runs.

Offline revalidation instead uses the attempt's archived Oracle, native report,
write journal and actual mail. It preserves the previous run record in
`run-before-revalidation.json`, records the validator hash, and retains original
execution timestamps and durations. Changed scenario definitions are rejected.
Campaign validation requires one transport thread (when IDs are available) and
each subsequent attack to reference a genuine reply to the preceding attack.
Changed subjects alone do not invalidate an otherwise connected conversation.

The no-reply repair unwraps PyRIT causes and homogeneous MCP exception groups;
mixed errors remain execution failures. Phishing success without a customer
reply additionally requires a matching Oracle alert and an archived actual
third-party delivery. Only the known Promptfoo stalled-thread error, explicit
native no-reply outcomes, or the evidenced legacy Dreadnode single-error wrapper
are accepted; unrelated model/transport errors remain invalid. Non-phishing
no-reply attempts remain invalid under the existing completion policy.

`--accept-repair` records before/after hashes for the small no-reply repair file
allowlist in the batch manifest, then retains normal configuration checks for
future resumes. It does not change repetitions, scenarios or scoring rules.
