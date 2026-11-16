"""Code-enforced, single-threaded Dreadnode customer-mail turns."""
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
from mail_protocol import MailTarget, NoReply, connect, target_error
from scenario import load_scenario


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
        self.status = "running"
        self.error = None
        self.save()

    @classmethod
    def from_environment(cls):
        selected = os.getenv("MAIL_DREADNODE_SCENARIO", "knowledge-leak")
        spec, _ = load_scenario(selected)
        run_id = os.getenv("MAIL_DREADNODE_RUN_ID") or uuid4().hex
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
            raise ValueError("Invalid campaign ID")
        report = Report(HERE.parent.parent / ".tmp/dreadnode" / run_id)
        config = SimpleNamespace(attacker_email=spec["attackerEmail"], managed_email=spec["managedEmail"],
            subject=spec["subject"] + " [dn-" + run_id + "]", transport_url=os.getenv("TRANSPORT_URL", "http://127.0.0.1:4005"),
            request_timeout_seconds=60, reply_timeout_seconds=spec["replyTimeoutSeconds"], poll_seconds=spec["pollIntervalSeconds"])
        report.write("manifest.json", {"scenario": selected, "max_target_calls": spec["maxTurns"], "protocol": "send-and-wait-v1"})
        return cls(config, spec["maxTurns"], report)

    def save(self):
        target = self.target
        self.report.write("case-001-summary.json", {"status": self.status, "error": self.error,
            "submissions_attempted": target.calls if target else 0,
            "replies_completed": target.completed if target else 0,
            "sent_message_ids": target.sent_message_ids if target else [],
            "pending_or_uncertain": bool(target and target.poisoned), "protocol": "send-and-wait-v1"})

    def record(self, event):
        self.report.event(event)
        self.save()

    async def send(self, message):
        if self.busy:
            self.report.event({"event": "concurrent_submission_rejected"})
            return {"status": "pending", "error": "A turn is already pending; do not send another message"}
        if self.error or (self.target and self.target.calls >= self.budget):
            return {"status": "stopped", "error": self.error or "turn_budget_exhausted"}
        self.busy = True
        try:
            async with self.connector(self.config) as transport:
                if self.target is None:
                    self.target = MailTarget(transport, self.config, self.budget, self.config.subject, self.record)
                else:
                    self.target.transport = transport
                reply = await self.target.send(message)
            self.status = "completed"
            return {"status": "completed", "turn": self.target.completed,
                    "remaining_turns": self.budget - self.target.calls, "reply": reply}
        except asyncio.CancelledError:
            self.status, self.error = "interrupted", "Turn cancelled; do not retry this campaign"
            raise
        except Exception as exc:
            failure = target_error(exc) or exc
            self.status = "no_reply" if isinstance(failure, NoReply) else "error"
            self.error = f"{type(failure).__name__}: {failure}"
            self.report.event({"event": "campaign_stopped", "error": self.error})
            return {"status": "stopped", "error": self.error}
        finally:
            self.busy = False
            self.save()
