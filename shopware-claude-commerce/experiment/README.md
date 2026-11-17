# Shopware experiments

Run commands from `shopware-claude-commerce/`. Complete the application setup,
fill `.env`, start the Docker shop, and pass `bash docker/verify.sh` first.
Stop any standalone `oracle/watch.py` process. Do not use the shop interactively
while an experiment runs: each attempt replaces its database and media with
`docker/snapshot/`, clears host state, and restarts the application hosts.

## Test and run

Check prerequisites without resetting the shop or calling models:

```bash
.venv/bin/python experiment/scripts/run.py --framework promptfoo --check
```

Run one real test (uses model APIs):

```bash
.venv/bin/python experiment/scripts/run.py \
  --framework promptfoo --scenario buyer-misdirection --repeats 1
```

Use `deepteam`, `pyrit`, or `dreadnode` in place of `promptfoo` to test the other
launchers. Existing `redteam/` configuration and model parameters are reused.
Each attempt generates one seller attack and executes one fixed victim task;
this does not introduce additional attack turns.

Run all five scenarios three times for one framework:

```bash
.venv/bin/python experiment/scripts/run.py --framework promptfoo --repeats 3
```

Run all four frameworks serially (60 planned runs):

```bash
bash experiment/run_all.sh --repeats 3
```

To smoke-test all four launchers with one scenario and one repetition:

```bash
bash experiment/run_all.sh --scenario buyer-misdirection --repeats 1
```

Native output is streamed to the terminal. A heartbeat every 30 seconds reports
the current stage, elapsed time, PID and time since the last output. Change it
with `--status-interval 15`. `--run-timeout 3600` bounds each framework execution;
`--reset-timeout 900` bounds each reset/check command. The Oracle finalizes inline
after the victim task, so no standalone watcher or idle timeout is needed.

## Results and recovery

```text
experiment/results/<framework>/<batch-id>/
  manifest.json
  runs.csv
  summary.csv
  <scenario>/repeat-01/attempt-01/
    run.json
    console.log
    experiment.log
    framework/
    oracle/
```

`framework/` preserves native reports, generated attacks and the victim reply.
`oracle/` contains `result.json`, `alerts.jsonl` and before/after state evidence.
`console.log` records native framework output; `experiment.log` records reset,
verification and progress output. Results may contain private shop data and are
ignored by Git. Files are owner-only. Keep them private when exporting data.
Existing native `.run/<framework>/` output is also retained by its launcher.

`manifest.json` freezes the repeat/scenario plan, revision, configuration and
snapshot hashes, actual models and container image. No API keys are recorded.
Every attempt receives a separate run ID and UTC timestamps. A shared lock
prevents overlapping campaigns, including standalone redteam evaluations.

An invalid attempt stops that framework batch. `run_all.sh` continues with the
next framework and prints the incomplete frameworks. Ctrl+C stops the series.
There are no automatic retries. Resume using the ID printed by the runner:

```bash
.venv/bin/python experiment/scripts/run.py --framework promptfoo --resume <batch-id>
bash experiment/run_all.sh --resume <series-id>
```

Resume keeps the saved plan, skips valid repetitions, and creates a new attempt
for each missing/invalid repetition. It rejects changed configuration or a changed
container image; start a new batch after modifying code, snapshot or model settings.
Previously saved attempts are preserved. To rebuild both CSV files offline:

```bash
.venv/bin/python experiment/scripts/summarize.py experiment/results/promptfoo/<batch-id>
```

## Statistics

`runs.csv` and `summary.csv` have fixed column order defined in
`scripts/summarize.py`. These are the common schema for new experiment harnesses;
existing Letta and Inbox harnesses are unchanged.

A valid attempt requires one completed fixed victim task, a completed native
framework run, matching Oracle provenance and the scenario's selected scoring
surface. Promptfoo safety assertion failures are allowed when the target ran
successfully. Framework grades do not determine experiment scores.

Scores use the Oracle surface's `normalized` value (0 to 1), with raw `points`
and `max_points` retained. Missing results, execution errors and incomplete tasks
are invalid and have blank scores. Invalid attempts never count as zero scores.
Per-scenario summaries report the valid count, invalid attempts, mean, maximum,
minimum, sample standard deviation and success rate. Standard deviation is blank
with fewer than two valid runs. A batch is complete only when every planned
repetition is valid.

Timing uses seconds: total attempt time includes reset and collection;
framework time includes attack generation, the victim task and framework grading.
`oracle_wait_seconds` measures final artifact collection; the inline Oracle work
is already included in framework time. Means include valid runs only, while totals
include invalid attempts too.

Tokens cover framework-side model calls only, excluding victim model usage:
Promptfoo aggregate generation/attacker/assertion buckets, DeepTeam model usage,
PyRIT response usage, and Dreadnode's displayed total. Abbreviated Dreadnode totals
are marked approximate. Missing usage is blank, never substituted with zero.
Token coverage columns identify how many attempts supplied usable totals;
totals with incomplete coverage are measured subtotals, not billing estimates.

Run offline harness tests:

```bash
.venv/bin/python -m pytest experiment/tests -q
```
