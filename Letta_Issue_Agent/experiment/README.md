# Letta Experiments

Run each framework separately, with configurable independent repetitions per scenario
(default: three; set `--repeats N` to change it).
Reuse the commands, models, strategies, dependencies, and settings in `../redteam/`.
This directory only orchestrates runs, archives outputs, and summarizes Oracle scores.
All file content must use English.

## Preparation

Run commands from `Letta_Issue_Agent/`. Install and validate the desired framework
using its existing `redteam/<framework>/README.md`. The scripts use Python 3.11+
and its standard library; Docker Compose and the existing framework dependencies
must already be available. DeepTeam and PyRIT use their existing `.venv` interpreters.

Prepare a known-clean target repository and Letta memory, close existing issues,
and wait for all jobs to finish. A new issue alone does not reset persistent state.
Then capture the baseline once:

```bash
python3 experiment/scripts/run.py --save-baseline clean
```

Baseline capture briefly stops the stack and saves the Gitea data and Letta state
volumes under `baselines/clean/`, then restarts the existing services. It does not
clean an already contaminated environment. An existing baseline is never overwritten.

Each experiment **replaces the current Gitea data and Letta state with this baseline**
and recreates the runner before attacking. Existing run artifacts are archived outside
these volumes. Use this workflow on the dedicated experiment stack, not a stack with
unrelated work. Do not post manual issue comments or use another checkout against the
same services during a batch. The campaign lock coordinates this checkout only.

Baseline archives contain private state. They and experiment results are ignored by
Git and created with private permissions. Environment values are not copied to run
metadata; hashes detect configuration changes. Configuration and Docker image changes
require a new clean baseline.

## Run

```bash
# All three scenarios, three repetitions each, for Promptfoo.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean

# Five repetitions per scenario, fifteen runs in total.
python3 experiment/scripts/run.py --framework promptfoo --baseline clean --repeats 5

# Run another framework separately.
python3 experiment/scripts/run.py --framework deepteam --baseline clean

# One scenario only.
python3 experiment/scripts/run.py --framework pyrit --baseline clean \
  --scenario memory-poisoning

python3 experiment/scripts/run.py --framework dreadnode --baseline clean
```

The script calls the existing launchers with `--scenario`; Dreadnode also receives
its documented `--print` flag. Native framework configuration remains in `redteam/`.
Configure one case/campaign and the shared scenario's attack budget there. More than
one new issue or excess attack messages makes an attempt invalid.

Runs are serial. The existing shared campaign lock covers restoration, framework
execution, and Oracle collection. PyRIT reuses the inherited lock in its child process.
Each run enables only the selected scenario's Oracle monitor through Compose environment
overrides; `.env` is not edited. The Gitea initializer is not rerun during restoration.

The framework process has a default four-hour deadline (`--run-timeout`, seconds).
After it finishes, the script waits up to 1,200 seconds for the final Oracle result
(`--oracle-timeout`, the maximum collection wait). New experiment batches override
the Oracle inactivity window to **60 seconds** using `--oracle-idle-timeout`.
This is the idle time after the last observed activity, not a deadline for an active
target task; subsequent activity restarts the idle clock. Finalization and collection
can take a few additional seconds. The stack's standalone default remains 300 seconds.

```bash
python3 experiment/scripts/run.py --framework deepteam --baseline clean \
  --scenario runtime-credential-disclosure --repeats 1 --oracle-idle-timeout 30
```

The inactivity window is recorded in batch/run metadata and reused on resume. Older
batches without this field retain their original Compose/environment setting.
Overrides take effect when the experiment recreates the runner for a new attempt;
they do not edit `.env`, invalidate the existing baseline, or change a running episode.
`--collect-only` keeps the live runner's setting. Keep `--oracle-timeout` comfortably
longer than the idle window and any pending target work; shortening that maximum alone
causes earlier collection failure rather than making Oracle finalize sooner.
The framework's own turn and reply deadlines remain unchanged.

## Live output

All frameworks share the same live output handling. Native stdout and stderr are
merged, forwarded to the terminal as bytes arrive, and retained unchanged in
`console.log`. Output does not need a trailing newline. Python child processes
receive `PYTHONUNBUFFERED=1`; other tools still control when they flush their output.

Launcher phase changes and status updates are printed with UTC timestamps and saved
separately in each attempt's `experiment.log`. By default, a status line appears every
30 seconds, including during baseline restoration and Oracle collection. It shows
the stage, total/stage elapsed time, and, while a framework is attached, its PID,
process state, and time since its last output. These are process observations;
they do not claim that a model request or target turn is making progress.

```bash
python3 experiment/scripts/run.py --framework dreadnode --baseline clean \
  --scenario runtime-credential-disclosure --repeats 1 --status-interval 10
```

The same `--status-interval` option works for every framework and `--collect-only`.
It must be finite and positive. Output settings apply to newly started launchers;
already-running processes continue using the code loaded at their start.

## Outputs

```text
results/<framework>/<batch-id>/
  manifest.json
  <scenario>/repeat-01/attempt-01/
    run.json
    console.log
    experiment.log
    framework/
    issues.json
    comments.json
    oracle/
      result.json
      result.txt
      alerts.jsonl
      issue.log
  runs.csv
  summary.csv
```

Native Promptfoo JSON/config outputs and DeepTeam/PyRIT report directories are copied
before later runs can overwrite them. Their original locations remain managed by the
existing launchers. Dreadnode's native headless response is saved in `console.log`;
actual attack messages and target replies are preserved in `issues.json` and
`comments.json`. No separate Dreadnode trajectory export is claimed.

## Statistics and recovery

`summary.csv` has one row per scenario: planned/valid run counts, invalid attempt count,
completion status, mean/max/min score, sample standard deviation, success count, and
success rate. `runs.csv` provides the corresponding attempt-level records.

Each attempt also records UTC start/end times, total elapsed seconds (including
baseline restoration and Oracle collection), framework execution seconds, and
framework input/output/total tokens. Durations use a monotonic clock.

Token sources:

- Promptfoo: aggregate attacker, assertion, and generation usage, counted once.
- DeepTeam: native attacker and evaluator model usage.
- PyRIT: response usage captured after normal PyRIT initialization by an
  experiment-only hook; native configuration and metadata handling are preserved.
  Counts are saved in `framework/usage.jsonl` after each received response.
- Dreadnode: native headless console total; compact values such as `12.3k` are
  marked `approximate`. Input/output breakdown is unavailable from this output.

These are **framework-side token counts**, excluding the Letta target's model
usage. They are not monetary billing figures. Missing usage stays blank and is
marked `unavailable` or `partial`, rather than being interpreted as zero.

`summary.csv` includes mean durations and mean framework tokens over valid runs
with available measurements. Total durations and total known framework tokens
include invalid attempts as well, since retries also consume resources.
`token_usage_attempts`, `valid_token_runs`, and `approximate_token_attempts` expose
the measurement coverage; a known-token total may be incomplete. Interrupted
processes and requests without returned usage cannot provide complete billing totals.

Scores come exclusively from the selected in-scope Oracle surface's `score.normalized`
(0 to 1, higher means greater attack effect). Success uses `evaluation.success`.
Raw points and maximum points are retained in `runs.csv`. Framework scores are archived
but never used in these statistics.

A completed attack with a valid Oracle zero counts as zero. Missing or non-completed
Oracle evaluations, framework errors, budget violations, and collection failures are
invalid, not zero. Their original files remain available. Statistics over fewer than
the planned number of valid repetitions are marked `incomplete`; standard deviation is blank when
fewer than two scores exist. Three repetitions provide descriptive comparisons only.

A batch stops on its first invalid attempt. Inspect the issue and pending target job
before resuming. Resume restores the baseline, skips valid completed repetitions, and
creates a new attempt for an unfinished repetition; it never replays an uncertain write.
Resume uses the batch's original repetition count and scenario selection.

Promptfoo exit code 100 means assertions failed; it counts as completed execution
when the archived report has evaluated cases and no execution errors. The Oracle
still determines the experiment score and success, independently of these assertions.

If the framework already ran but result collection failed, try `--collect-only` first.
It recovers the issue ID from archived framework output, waits for Oracle, archives
the conversation/evidence, and rebuilds CSV files. It does not restore a baseline,
launch a framework, or send another attack. The original failure is retained in
`original_status` and `original_error`. Recovery requires the original live episode
to remain available; do this before restoring the environment for another attempt.
Recovered total elapsed time uses UTC wall-clock timestamps because recovery spans
processes; it includes the pause between invocations and is marked
`duration_clock: wall_clock_recovery`. Original timing is retained separately.

```bash
python3 experiment/scripts/run.py --framework promptfoo --baseline clean \
  --resume <batch-id>

# Recover existing results without running attacks again.
python3 experiment/scripts/run.py --framework promptfoo \
  --resume <batch-id> --collect-only

# Recompute CSV files without Docker access, attacks, or model calls.
python3 experiment/scripts/summarize.py --framework promptfoo --batch <batch-id>

# Offline tests; no containers or paid model calls.
python3 -m unittest discover -s experiment/scripts -p 'test_*.py' -v
```

The default Gitea URL and repository match the current Letta stack. `--web-url` and
`--repo` override collection coordinates when the existing redteam configuration uses
another endpoint. The MCP health check discovers the gateway's published port through Docker Compose.

## Validation scope

The credential-disclosure smoke runs exercised all four frameworks through the
target, Oracle collection, and CSV statistics. This is connectivity evidence;
other scenarios and three independent repetitions per framework/scenario still
need live validation. Dreadnode's instruction-driven loop can submit comments
before a prior target reply completes. Its Oracle verdict remains evidence of
observed effects, but the current adapter does not enforce one reply per attack
message. Review submission order before using these runs for controlled comparisons.
