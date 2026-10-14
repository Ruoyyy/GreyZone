"""Minimal MCP-over-SSE client for the external attacker server (:8086).

Just enough protocol for the runner: connect, initialize, call tools.
The server writes JSON-RPC responses on the SSE stream as `data:` lines
(no space after the colon).
"""
from __future__ import annotations

import json
import urllib.request


class McpClient:
    def __init__(self, sse_url: str = "http://localhost:8086/sse", timeout: int = 180):
        self.sse_url = sse_url
        self.timeout = timeout
        self._stream = None
        self._endpoint = None
        self._id = 0

    def connect(self) -> str:
        req = urllib.request.Request(self.sse_url, headers={"Accept": "text/event-stream"})
        self._stream = urllib.request.urlopen(req, timeout=self.timeout)
        while True:
            line = self._stream.readline().decode()
            if line.startswith("data:") and "sessionId=" in line:
                self._endpoint = line[5:].strip()
                return self._endpoint
            if not line:
                raise RuntimeError("SSE stream ended before the endpoint event")

    def _post(self, payload: dict):
        url = self.sse_url.rsplit("/", 1)[0] + self._endpoint
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=self.timeout).read()

    def _read_until(self, want_id: int) -> dict:
        while True:
            line = self._stream.readline().decode()
            if not line.startswith("data:"):
                continue  # event:/comment/keepalive lines
            try:
                msg = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg

    def initialize(self):
        self._id += 1
        rid = self._id
        self._post({"jsonrpc": "2.0", "id": rid, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "oracle-runner", "version": "1.0"}}})
        self._read_until(rid)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Blocking tool call; returns the JSON-RPC result object."""
        self._id += 1
        rid = self._id
        self._post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
        return self._read_until(rid)

    def close(self):
        try:
            self._stream.close()
        except Exception:
            pass


def tool_ok(result: dict) -> bool:
    return bool(result.get("result")) and not result["result"].get("isError")
