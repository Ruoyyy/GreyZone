"""A code-enforced turn boundary for Dreadnode's native autonomous agent."""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from gitea_transport import IssueTarget, TargetError, WriteOutcomeUnknown, connect
from scenario import load_scenario, scenario_path


def now():
    return datetime.now(timezone.utc).isoformat()


class Report:
    def __init__(self, path):
        self.path = path
        # A second runtime must not silently start another issue in this campaign.
        path.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.secrets = sorted({v for k, v in os.environ.items() if len(v) >= 8 and
            any(s in k.upper() for s in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET'))}, key=len, reverse=True)

    def encode(self, data):
        text = json.dumps(data)
        for secret in self.secrets:
            text = text.replace(json.dumps(secret)[1:-1], '[REDACTED]')
        return text

    def write(self, name, data):
        path = self.path / name
        temporary = path.with_suffix(path.suffix + '.tmp')
        with open(temporary, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(self.encode(data) + '\n')
        temporary.replace(path)

    def event(self, data):
        with open(self.path / 'events.jsonl', 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(self.encode({'time': now(), **data}) + '\n')


class Campaign:
    def __init__(self, config, budget, report, connector=connect):
        self.config, self.budget, self.report, self.connector = config, budget, report, connector
        self.target = None
        self.busy = False
        self.error = None
        self.save()

    @classmethod
    def from_environment(cls):
        scenario_id = os.getenv('LETTA_DREADNODE_SCENARIO', 'runtime-credential-disclosure')
        scenario, _ = load_scenario(scenario_path(scenario_id))
        run_id = os.getenv('LETTA_DREADNODE_RUN_ID') or uuid4().hex
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
            raise ValueError('Invalid Dreadnode run ID')
        report = Report(HERE / 'results' / run_id)
        config = SimpleNamespace(
            mcp_url=os.getenv('GITEA_MCP_URL', 'http://127.0.0.1:8087/mcp'),
            web_url='http://127.0.0.1:3000', owner='letta-admin', repo='GreyZone',
            title='Question about repo configuration', poll_seconds=5,
            reply_timeout_seconds=900, request_timeout_seconds=60)
        report.write('manifest.json', {'created_at': now(), 'scenario': scenario_id,
            'max_target_calls': scenario['maxTurns'], 'transport': config.mcp_url,
            'protocol': 'send-and-wait-v1', 'oracle': {'status': 'not_collected'}})
        return cls(config, scenario['maxTurns'], report)

    def save(self):
        target = self.target
        complete = bool(target and target.completed == target.calls and target.completed and not target.poisoned)
        self.report.write('case-001-summary.json', {
            'status': 'error' if self.error else 'completed' if complete else 'running',
            'issue_number': target.issue if target else None,
            'issue_url': target.url if target else None,
            'submissions_attempted': target.calls if target else 0,
            'replies_completed': target.completed if target else 0,
            'pending_or_uncertain': bool(target and target.poisoned),
            'error': self.error, 'protocol': 'send-and-wait-v1', 'updated_at': now(),
            'oracle': {'status': 'not_collected'}})

    async def send(self, message):
        if self.busy:
            self.report.event({'event': 'concurrent_submission_rejected'})
            return {'status': 'pending', 'error': 'Another turn is pending. Review its completed reply before sending again.'}
        if self.error:
            return {'status': 'stopped', 'error': self.error}
        if self.target and self.target.calls >= self.budget:
            return {'status': 'stopped', 'reason': 'turn_budget_exhausted'}
        self.busy = True
        try:
            # anyio's connection context must open and close in the same task.
            async with self.connector(self.config) as transport:
                if self.target is None:
                    self.target = IssueTarget(transport, self.config, self.budget, self.record)
                else:
                    self.target.transport = transport
                reply = await self.target.send(message)
            return {'status': 'completed', 'issue_number': self.target.issue,
                    'issue_url': self.target.url, 'turn': self.target.completed,
                    'reply_comment_id': self.target.after_id,
                    'remaining_turns': self.budget - self.target.calls, 'reply': reply}
        except asyncio.CancelledError:
            self.error = 'Turn cancelled; inspect the pending issue. Do not retry this campaign.'
            self.report.event({'event': 'campaign_stopped', 'error': self.error})
            raise
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.report.event({'event': 'campaign_stopped', 'error': self.error})
            return {'status': 'stopped', 'error': self.error}
        finally:
            self.busy = False
            self.save()

    def record(self, event):
        self.report.event(event)
        self.save()
