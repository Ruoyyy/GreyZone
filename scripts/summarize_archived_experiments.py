"""Summarize archived benchmark evaluations using only local result files."""

import argparse
import csv
import json
import math
from pathlib import Path
import re
import statistics
import tempfile


FRAMEWORKS = ('promptfoo', 'deepteam', 'pyrit', 'dreadnode', 'giskard', 'pyrit-vision')
RUN_FIELDS = (
    'scenario', 'repeat', 'attempt', 'status', 'score', 'success', 'points', 'max_points',
    'reason', 'path', 'started_at', 'finished_at', 'duration_seconds', 'framework_duration_seconds',
    'token_usage_status', 'token_usage_source', 'framework_input_tokens', 'framework_output_tokens',
    'framework_total_tokens', 'oracle_binding',
)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid scenario or batch identifier')
    return value


def read_json(path):
    with path.open() as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    return payload


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def oracle_score(result, application, scenario, run_id):
    evaluation = result['evaluation']
    if evaluation.get('schemaVersion') != 1 or evaluation.get('scorePolicyVersion') != '2':
        raise ValueError('Unsupported Oracle result contract')
    if evaluation.get('application') != application or evaluation.get('scenarioId') != scenario:
        raise ValueError('Oracle application or scenario mismatch')
    if result.get('executionError') or evaluation.get('status') != 'completed' \
            or type(evaluation.get('success')) is not bool:
        raise ValueError('Oracle evaluation is incomplete')
    oracle_run_id = evaluation.get('runId')
    if not isinstance(run_id, str) or not run_id or (oracle_run_id is not None and oracle_run_id != run_id):
        raise ValueError('Oracle run identity mismatch')

    scoped = [item for item in evaluation['surfaces'] if item['inScope'] is True]
    ids = {item['id'] for item in scoped}
    if not scoped or len(ids) != len(scoped):
        raise ValueError('Missing or duplicate scoring surfaces')
    points = maximum = 0.0
    for surface in scoped:
        score = surface['score']
        earned, available, normalized = (score[key] for key in ('points', 'maxPoints', 'normalized'))
        if not all(finite(value) for value in (earned, available, normalized)) or \
                available <= 0 or not 0 <= earned <= available or \
                abs(normalized - earned / available) > 0.000001:
            raise ValueError('Invalid Oracle surface score')
        points += earned
        maximum += available
    if not finite(points) or not finite(maximum):
        raise ValueError('Invalid total Oracle score')
    return {
        'score': round(points / maximum, 6), 'success': evaluation['success'],
        'points': points, 'max_points': maximum,
        'oracle_binding': 'run_id' if oracle_run_id is not None else 'legacy_missing_run_id',
    }


def read_attempt(path, batch, application, scenario, repeats, framework):
    repeat = int(path.parent.parent.name.removeprefix('repeat-'))
    attempt = int(path.parent.name.removeprefix('attempt-'))
    row = dict(scenario=scenario, repeat=repeat, attempt=attempt, status='invalid',
               path=str(path.parent.relative_to(batch)))
    try:
        run = read_json(path)
        if (run.get('scenario'), run.get('repeat'), run.get('attempt')) != (scenario, repeat, attempt) \
                or not 1 <= repeat <= repeats or attempt < 1 or run.get('framework') != framework:
            raise ValueError('Attempt metadata does not match its directory or batch')
        for key in ('started_at', 'finished_at', 'duration_seconds', 'framework_duration_seconds'):
            if key in run:
                row[key] = run[key]
        usage = run.get('token_usage') or {}
        row['token_usage_status'] = usage.get('status', 'unavailable')
        row['token_usage_source'] = usage.get('source', '')
        for field in ('input_tokens', 'output_tokens', 'total_tokens'):
            value = usage.get(field)
            if finite(value) and value >= 0:
                row[f'framework_{field}'] = value
        if run.get('status') != 'completed':
            row['status'] = 'running' if run.get('status') == 'running' else 'invalid'
            row['reason'] = 'Attempt has no completed evaluation'
            return row
        result = read_json(path.parent / 'oracle' / 'result.json')
        row.update(oracle_score(result, application, scenario, run.get('run_id')), status='completed')
    except ValueError as error:
        row['reason'] = 'Malformed JSON' if isinstance(error, json.JSONDecodeError) else str(error)
    except (KeyError, TypeError, AttributeError, OSError, OverflowError):
        row['reason'] = 'Missing or malformed attempt metadata or Oracle result'
    return row


def summarize(batch, application, framework):
    manifest = read_json(batch / 'manifest.json')
    repeats = manifest['repeats']
    scenarios = manifest['scenarios']
    if manifest.get('framework') != framework or manifest.get('run_kind', 'research') != 'research':
        raise ValueError('Expected a research batch for the selected framework')
    if type(repeats) is not int or repeats < 1 or not isinstance(scenarios, list) or not scenarios:
        raise ValueError('Invalid batch plan')
    scenarios = [identifier(scenario) for scenario in scenarios]
    if len(set(scenarios)) != len(scenarios):
        raise ValueError('Duplicate scenarios in batch plan')

    rows, summaries = [], []
    run_framework = 'pyrit' if framework == 'pyrit-vision' else framework
    for scenario in scenarios:
        paths = sorted((batch / scenario).glob('repeat-*/attempt-*/run.json'))
        attempts = [read_attempt(p, batch, application, scenario, repeats, run_framework) for p in paths
                    if re.fullmatch(r'repeat-\d+', p.parent.parent.name)
                    and re.fullmatch(r'attempt-\d+', p.parent.name)]
        by_repeat = {}
        for row in attempts:
            if row['status'] == 'completed':
                by_repeat.setdefault(row['repeat'], []).append(row)
        for candidates in by_repeat.values():
            if len(candidates) > 1:
                for row in candidates:
                    row.update(status='invalid', reason='Multiple completed attempts for one repetition',
                               score='', success='', points='', max_points='')
        for row in attempts:
            candidates = by_repeat.get(row['repeat'], ())
            if row['status'] == 'running' and len(candidates) == 1 and candidates[0]['attempt'] > row['attempt']:
                row.update(status='invalid', reason='Unfinished attempt superseded by a later result')

        valid = [row for row in attempts if row['status'] == 'completed']
        scores = [row['score'] for row in valid]
        successes = sum(row['success'] for row in valid)
        summary = dict(
            scenario=scenario, planned_runs=repeats, valid_runs=len(valid),
            invalid_attempts=sum(row['status'] == 'invalid' for row in attempts),
            status='completed' if len(valid) == repeats else 'incomplete',
            mean_score=statistics.mean(scores) if scores else '',
            max_score=max(scores, default=''), min_score=min(scores, default=''),
            std_score=statistics.stdev(scores) if len(scores) > 1 else '',
            success_count=successes, success_rate=successes / len(valid) if valid else '',
        )
        for field in ('duration_seconds', 'framework_duration_seconds', 'framework_total_tokens'):
            all_values = [row[field] for row in attempts if finite(row.get(field))]
            valid_values = [row[field] for row in valid if finite(row.get(field))]
            label = 'framework_tokens' if field == 'framework_total_tokens' else field
            summary[f'mean_{label}'] = statistics.mean(valid_values) if valid_values else ''
            summary[f'total_{label}'] = sum(all_values) if all_values else ''
        summary.update(
            token_usage_attempts=sum('framework_total_tokens' in row for row in attempts),
            valid_token_runs=sum('framework_total_tokens' in row for row in valid),
            approximate_token_attempts=sum(row['token_usage_status'] == 'approximate' for row in attempts
                                           if 'token_usage_status' in row),
            running_attempts=sum(row['status'] == 'running' for row in attempts),
            legacy_unbound_runs=sum(row['oracle_binding'] == 'legacy_missing_run_id' for row in valid),
        )
        summaries.append(summary)
        rows.extend(attempts)
    return rows, summaries


def write_csv(path, fields, rows):
    with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                     dir=path.parent, prefix=path.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main(application_dir):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--framework', required=True, choices=FRAMEWORKS)
    parser.add_argument('--batch', required=True, type=identifier)
    parser.add_argument('--results-root', type=Path, default=application_dir / 'experiment' / 'results')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    batch = args.results_root / args.framework / args.batch
    if args.output_dir.resolve().is_relative_to(batch.resolve()):
        parser.error('Choose an output directory outside the archived batch')
    try:
        rows, summaries = summarize(batch, application_dir.name, args.framework)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / 'runs.csv', RUN_FIELDS, rows)
    write_csv(args.output_dir / 'summary.csv', tuple(summaries[0]), summaries)
    print(args.output_dir / 'summary.csv')
