"""One customer-mail thread per PyRIT case; uncertain writes are never retried."""

import asyncio
import hashlib
import os
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from time import monotonic

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from settings import APP_ROOT, HERE

MCP_START = HERE.parent / "shared/start_mail_mcp.sh"
REQUIRED_TOOLS = {"send_email", "list_received", "read_received", "reply_to"}


class TargetError(RuntimeError):
    pass


class NoReply(TargetError):
    pass


class WriteOutcomeUnknown(TargetError):
    pass


def decode_result(result):
    if result.isError:
        raise TargetError("Customer-mail MCP tool returned an error (see server logs)")
    value = result.structuredContent
    if not isinstance(value, dict):
        raise TargetError("Customer-mail MCP tool did not return structuredContent")
    return value


class MCPTransport:
    def __init__(self, session, timeout):
        self.session, self.timeout = session, timeout

    async def call(self, name, arguments):
        async with asyncio.timeout(self.timeout):
            return decode_result(await self.session.call_tool(name, arguments))


@asynccontextmanager
async def connect(config):
    token_path = APP_ROOT / ".tmp/customer-mail/.transport-token"
    try:
        token = token_path.read_text().strip()
    except FileNotFoundError as exc:
        raise TargetError(f"Customer-mail transport token is missing: {token_path}") from exc
    if not token:
        raise TargetError("Customer-mail transport token is empty")
    if not MCP_START.is_file():
        raise TargetError("Customer-mail MCP launcher is missing")
    params = StdioServerParameters(command=str(MCP_START), args=[], env={
        "PATH": os.environ.get("PATH", ""),
        "TRANSPORT_TOKEN": token,
        "CUSTOMER_EMAIL": config.attacker_email,
        "CUSTOMER_OUTBOX": str(APP_ROOT / ".tmp/customer-mail"),
        "TRANSPORT_URL": config.transport_url,
    })
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write,
            read_timeout_seconds=timedelta(seconds=config.request_timeout_seconds)) as session:
            await session.initialize()
            available = set()
            cursor = None
            while True:
                page = await session.list_tools(cursor=cursor)
                available.update(tool.name for tool in page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            missing = REQUIRED_TOOLS - available
            if missing:
                raise TargetError(f"Missing customer-mail MCP tools: {sorted(missing)}")
            yield MCPTransport(session, config.request_timeout_seconds)


def same_address(left, right):
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def normalized_subject(value):
    return re.sub(r"^(?:(?:re|fw|fwd):\s*)+", "", str(value or ""), flags=re.I).strip().casefold()


class MailTarget:
    def __init__(self, transport, config, budget, subject, record=lambda event: None):
        self.transport, self.config, self.budget, self.subject = transport, config, budget, subject
        self.record = record
        self.calls = 0
        self.completed = 0
        self.last_reply_index = None
        self.sent_message_ids = []
        self.poisoned = False
        self.lock = asyncio.Lock()

    async def send(self, text):
        async with self.lock:
            if self.poisoned:
                raise TargetError("Thread stopped after an incomplete or uncertain turn")
            if self.calls >= self.budget:
                raise TargetError("Target submission budget exhausted")
            if not isinstance(text, str) or not text.strip():
                raise TargetError("Empty attack text")
            baseline = await self.transport.call("list_received", {})
            messages = baseline.get("messages")
            if not isinstance(messages, list):
                raise TargetError("Invalid list_received result")
            after_index = max((int(mail["index"]) for mail in messages), default=0)
            first = self.last_reply_index is None
            name = "send_email" if first else "reply_to"
            arguments = ({"to": self.config.managed_email, "subject": self.subject, "body": text}
                         if first else {"index": self.last_reply_index, "body": text})
            self.calls += 1
            self.poisoned = True
            self.record({"event": "submit_intent", "turn": self.calls, "input": text,
                         "subject": self.subject, "tool": name})
            try:
                sent = await self.transport.call(name, arguments)
                message_id = sent["messageId"]
                rfc_id = sent["rfcMessageId"]
                delivery_id = sent["deliveryId"]
                if not isinstance(rfc_id, str) or not rfc_id.startswith("<"):
                    raise TargetError("Send result lacks RFC Message-ID")
            except asyncio.CancelledError:
                self.record({"event": "write_outcome_unknown", "turn": self.calls})
                raise
            except Exception as exc:
                self.record({"event": "write_outcome_unknown", "turn": self.calls})
                raise WriteOutcomeUnknown("Mail write outcome unknown; do not resend automatically") from exc
            self.sent_message_ids.append(message_id)
            self.record({"event": "submitted", "turn": self.calls, "message_id": message_id,
                         "delivery_id": delivery_id, "rfc_message_id": rfc_id})
            print(f"  Turn {self.calls}/{self.budget}: waiting for customer-visible reply", flush=True)
            deadline = monotonic() + self.config.reply_timeout_seconds
            seen = set()
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self.record({"event": "no_reply", "turn": self.calls, "message_id": message_id})
                    raise NoReply("No matching customer reply before the scenario deadline")
                async with asyncio.timeout(remaining):
                    listing = await self.transport.call("list_received", {})
                    messages = listing.get("messages")
                    if not isinstance(messages, list):
                        raise TargetError("Invalid list_received result")
                    for mail in sorted(messages, key=lambda item: int(item["index"])):
                        index = int(mail["index"])
                        if index <= after_index or index in seen:
                            continue
                        seen.add(index)
                        if not same_address(mail.get("from"), self.config.managed_email):
                            continue
                        recipients = mail.get("recipients")
                        if not isinstance(recipients, list) or not any(
                                same_address(recipient, self.config.attacker_email) for recipient in recipients):
                            continue
                        received = await self.transport.call("read_received", {"index": index})
                        threaded = (received.get("inReplyTo") == rfc_id
                                    or rfc_id in str(received.get("references") or ""))
                        same_subject = normalized_subject(received.get("subject")) == normalized_subject(self.subject)
                        if not threaded and not same_subject:
                            continue
                        body = received.get("body")
                        if not isinstance(body, str):
                            raise TargetError("Invalid read_received body")
                        self.last_reply_index = index
                        self.completed += 1
                        self.poisoned = False
                        self.record({"event": "reply", "turn": self.calls, "reply_index": index,
                                     "reply_delivery_id": received.get("deliveryId"),
                                     "reply_message_id": received.get("messageId"),
                                     "body_sha256": hashlib.sha256(body.encode()).hexdigest()})
                        print(f"  Turn {self.calls}: matched reply #{index}", flush=True)
                        return body
                await asyncio.sleep(min(self.config.poll_seconds, max(0, deadline - monotonic())))
