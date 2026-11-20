# Banking Assistant experiment reports

The summarizer reads a batch's `manifest.json`, each attempt's `run.json`, and
its archived `oracle/result.json`. It writes `runs.csv` and `summary.csv` to a
separate output directory. See the [Oracle result contract](../../docs/oracle-results.md)
for the evaluation fields and scoring policies.

Run from `agent-openai-java-banking-assistant/`. For batches already in `experiment/results/`,
omit `--results-root`. The example reads an existing lab workspace batch.

```bash
python3 experiment/scripts/summarize.py --framework promptfoo --batch BATCH_ID \
  --results-root ../../.state/experiments/banking/results \
  --output-dir experiment/reports/promptfoo/BATCH_ID
```

Scores come from completed in-scope Oracle evaluations. Missing or mismatched
evaluations and duplicate completed attempts are excluded. Older results
without an Oracle run ID are labeled `legacy_missing_run_id` in `runs.csv`.
The checked-in research archive is under `experiment/results/`. It contains
batch manifests, run metadata, CSV reports, and Oracle evaluations. Raw
evidence and execution logs remain in the original local archive.
