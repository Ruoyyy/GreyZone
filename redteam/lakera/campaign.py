"""Session-to-issue mapping and partial evidence independent of SDK verdicts."""
import asyncio
from hashlib import sha256

from issue_transport import IssueTarget, TargetError, TargetTimeout, WriteOutcomeUnknown
from reporting import now


class SessionFailure(RuntimeError):
    """Sanitized error forwarded to Red by the SDK."""


class Campaign:
    def __init__(self, settings, transport, report):
        self.settings, self.transport, self.report = settings, transport, report
        self.sessions = {}
        self.failed = False
        self.active_session = None

    async def handle(self, session):
        if self.failed or session.id in self.sessions or self.active_session is not None:
            self.failed = True
            self.report.event("campaign", {"event": "session_rejected", "session_id": session.id,
                                          "reason": "failed, replayed or overlapping session"})
            raise SessionFailure("Session rejected; inspect local campaign report")
        if len(self.sessions) >= self.settings.campaign.cases:
            self.failed = True
            raise SessionFailure("Local session budget exhausted")
        case_id = f"case-{len(self.sessions) + 1:03d}"
        state = {"case_id": case_id, "session_id": session.id, "status": "running",
                 "started_at": now(), "oracle": {"status": "not_collected"},
                 "responses_submitted": 0}
        self.sessions[session.id] = state
        self.active_session = session.id
        target = IssueTarget(self.transport, self.settings.target, self.settings.campaign.max_target_calls,
                            lambda event: self.report.event(case_id, {"session_id": session.id, **event}))
        submitted_prompts = set()
        self.save(state, target)
        try:
            async for message in session:
                if self.failed:
                    raise SessionFailure("Campaign stopped after another session failed")
                fingerprint = sha256(message.attack.encode()).hexdigest()
                if fingerprint in submitted_prompts:
                    raise SessionFailure("Repeated attack text rejected to avoid ambiguous duplicate writes")
                submitted_prompts.add(fingerprint)
                # send() enforces the hard reply deadline and never retries writes.
                reply = await target.send(message.attack)
                state["status"] = "submitting_response"
                self.save(state, target)
                async with asyncio.timeout(self.settings.red.response_timeout_seconds):
                    await message.respond(reply)
                state["responses_submitted"] += 1
                state["status"] = "running"
                self.report.event(case_id, {"event": "response_submitted", "turn": target.calls,
                                           "session_id": session.id})
                self.save(state, target)
            if not target.completed or target.poisoned:
                raise SessionFailure("Session ended without a complete target exchange")
            state["status"] = "completed"
        except asyncio.CancelledError:
            self.failed = True
            state.update(status="interrupted", error="SDK or local runner cancelled the session")
            raise
        except Exception as exc:
            self.failed = True
            if isinstance(exc, TargetTimeout):
                status = "target_timeout"
            elif isinstance(exc, WriteOutcomeUnknown):
                status = "write_outcome_unknown"
            elif state["status"] == "submitting_response":
                status = "response_outcome_unknown"
            elif isinstance(exc, TargetError):
                status = "execution_error"
            else:
                status = "error"
            state.update(status=status, error=f"{type(exc).__name__}: {exc}")
            # SDK swallows handler errors: keep local status authoritative.
            raise SessionFailure(f"{status}; case {case_id}; inspect local evidence before restarting") from exc
        finally:
            self.active_session = None
            state["finished_at"] = now()
            self.save(state, target)

    def save(self, state, target):
        state.update(issue_number=target.issue, issue_url=target.url,
                     submissions_attempted=target.calls, replies_completed=target.completed,
                     pending_or_uncertain=target.poisoned)
        self.report.write(f"{state['case_id']}-summary.json", state)

    @property
    def complete(self):
        return (not self.failed and len(self.sessions) == self.settings.campaign.cases
                and all(s["status"] == "completed" for s in self.sessions.values()))


async def collect_results(scan, report, deadline_seconds, poll_seconds=2):
    async with asyncio.timeout(deadline_seconds):
        while True:
            results = await scan.get_results()
            report.write("red-results.json", results.to_json())
            if results.ready:
                return results
            await asyncio.sleep(poll_seconds)


def validate_results(results, settings):
    entries = results.results or []
    return (results.ready and bool(entries)
            and all(e.objective_id == settings.campaign.objective_key
                    and not e.error and e.evaluation is not None
                    and e.evaluation.attack_success_score is not None for e in entries))
