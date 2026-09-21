"""One persistent issue per case; writes are never automatically retried."""
import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from time import monotonic

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class TargetError(RuntimeError):
    pass


class WriteOutcomeUnknown(TargetError):
    pass


def decode_result(result):
    if result.isError:
        raise TargetError("MCP tool returned an error (see server logs)")
    if result.structuredContent is not None:
        return result.structuredContent
    text = "\n".join(x.text for x in result.content if x.type == "text")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise TargetError("MCP tool did not return JSON") from exc


class MCPTransport:
    def __init__(self, session, timeout):
        self.session, self.timeout = session, timeout

    async def call(self, name, arguments):
        async with asyncio.timeout(self.timeout):
            return decode_result(await self.session.call_tool(name, arguments))


@asynccontextmanager
async def connect(config):
    # Open and close anyio context managers in the same task/event loop.
    async with streamablehttp_client(
        config.mcp_url, timeout=timedelta(seconds=config.request_timeout_seconds)
    ) as (read, write, _):
        async with ClientSession(read, write,
            read_timeout_seconds=timedelta(seconds=config.request_timeout_seconds)) as session:
            await session.initialize()
            available = set()
            cursor = None
            while True:
                page = await session.list_tools(cursor=cursor)
                available.update(t.name for t in page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            missing = {"issue_write", "issue_read"} - available
            if missing:
                raise TargetError(f"Missing MCP tools: {sorted(missing)}")
            yield MCPTransport(session, config.request_timeout_seconds)


def comments_from(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("comments", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
        if "id" in raw and "body" in raw:
            return [raw]
    raise TargetError("Unexpected MCP comments response")


def clean_reply(body):
    body = re.sub(r"<!-- letta-metadata[\s\S]*?-->", "", body)
    lines = body.splitlines()
    if lines and lines[0].startswith("**Letta Code"):
        lines.pop(0)
    return "\n".join(lines).split("\n🤖")[0].strip().strip("-").strip()


class IssueTarget:
    def __init__(self, transport, config, budget, record=lambda event: None):
        self.transport, self.config, self.budget = transport, config, budget
        self.record = record
        self.issue = None
        self.calls = 0
        self.completed = 0
        self.after_id = 0
        self.poisoned = False
        self.lock = asyncio.Lock()

    @property
    def url(self):
        if self.issue is None:
            return None
        return f"{self.config.web_url}/{self.config.owner}/{self.config.repo}/issues/{self.issue}"

    async def send(self, text, turns=None):
        async with self.lock:
            if self.poisoned:
                raise TargetError("Case stopped after an incomplete/uncertain turn; inspect issue before resuming")
            if self.calls >= self.budget:
                raise TargetError("Target submission budget exhausted")
            # A fresh or rewound history after a completed turn is a new case or
            # a rollback, neither of which may silently reuse this issue.
            if turns is not None:
                assistants = sum(t.role == "assistant" for t in turns)
                if assistants != self.completed:
                    raise TargetError("History diverged from the persistent issue")
            self.calls += 1
            self.poisoned = True
            common = {"owner": self.config.owner, "repo": self.config.repo}
            args = dict(common)
            if self.issue is None:
                args.update(method="create", title=self.config.title,
                            body=text if "@letta-code" in text else "@letta-code " + text)
            else:
                args.update(method="add_comment", issue_number=self.issue, body=text)
            self.record({"event": "submit_intent", "turn": self.calls,
                         "issue": self.issue, "input": text})
            try:
                result = await self.transport.call("issue_write", args)
                if self.issue is None:
                    self.issue = int(result["number"])
                else:
                    self.after_id = int(result["id"])
            except asyncio.CancelledError:
                self.record({"event": "write_outcome_unknown", "turn": self.calls,
                             "issue": self.issue})
                raise
            except Exception as exc:
                self.record({"event": "write_outcome_unknown", "turn": self.calls,
                             "issue": self.issue})
                raise WriteOutcomeUnknown("Write outcome unknown; inspect recent issues/comments before retrying") from exc
            self.record({"event": "submitted", "turn": self.calls, "issue": self.issue,
                         "comment_id": self.after_id or None, "url": self.url})
            print(f"  Turn {self.calls}/{self.budget}: waiting on {self.url}", flush=True)
            deadline = monotonic() + self.config.reply_timeout_seconds
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TargetError(f"Reply timeout on issue #{self.issue}; do not resend automatically")
                async with asyncio.timeout(remaining):
                    raw = await self.transport.call("issue_read", dict(common,
                        method="get_comments", issue_number=self.issue))
                matches = []
                for comment in comments_from(raw):
                    user = comment.get("user", {})
                    user = user if isinstance(user, str) else user.get("login", user.get("username"))
                    body = comment.get("body", "")
                    if (user == "gitea-actions" and int(comment["id"]) > self.after_id
                        and any(x in body for x in ("Letta Code finished", "Letta Code failed",
                                                   "Letta Code encountered an error"))):
                        matches.append(comment)
                if matches:
                    comment = max(matches, key=lambda x: int(x["id"]))
                    body = comment["body"]
                    failed = any(x in body for x in ("Letta Code failed", "Letta Code encountered an error"))
                    self.record({"event": "reply", "turn": self.calls, "issue": self.issue,
                                 "comment_id": comment["id"], "failed": failed,
                                 "output": clean_reply(body)})
                    if failed:
                        raise TargetError(f"Letta execution failed on issue #{self.issue}")
                    self.after_id = int(comment["id"])
                    self.completed += 1
                    self.poisoned = False
                    print(f"  Turn {self.calls}: completed", flush=True)
                    return clean_reply(body)
                await asyncio.sleep(min(self.config.poll_seconds, max(0, deadline - monotonic())))
