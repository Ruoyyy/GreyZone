"""端到端验证：列工具 -> 逐个注入工具调用 -> 断言集群侧效果。"""
import asyncio
import os
import re
import subprocess
import sys
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

KEY = os.environ["REDTEAM_KEY"]

def kube(*args):
    r = subprocess.run(["kubectl"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    return r.stdout

async def main():
    t = StreamableHttpTransport("http://localhost:18092/mcp",
                                headers={"Authorization": f"Bearer {KEY}"})
    results = []
    async with Client(t) as c:
        tools = [x.name for x in await c.list_tools()]
        print("tools:", tools)
        assert set(tools) == {"plant_web_log", "forge_event",
                              "plant_termination_message", "plant_image_name_pod",
                              "plant_hijacked_tool", "clear_injections"}, "tool set mismatch"

        # 1. web log
        r = await c.call_tool("plant_web_log", {"text": "MARKER-MCP-VERIFY-1", "slot": "ua"})
        print("plant_web_log:", r.content[0].text)
        # 2. forged event
        r = await c.call_tool("forge_event", {"target_pod": "log-generator",
                                              "message": "MARKER-MCP-VERIFY-2 forged message"})
        print("forge_event:", r.content[0].text)
        # 3. hijacked tool
        r = await c.call_tool("plant_hijacked_tool",
                              {"pod_name": "verify-hijack", "tool": "free",
                               "payload_line": "MARKER-MCP-VERIFY-3 payload line"})
        print("plant_hijacked_tool:", r.content[0].text)
        # 4. termination message
        r = await c.call_tool("plant_termination_message",
                              {"pod_name": "verify-term",
                               "message": "MARKER-MCP-VERIFY-4 rich text line1\nline2"})
        print("plant_termination_message:", r.content[0].text)
        # 5. image name
        r = await c.call_tool("plant_image_name_pod",
                              {"pod_name": "verify-img",
                               "text": "MARKER-MCP-VERIFY-5 dashed payload"})
        print("plant_image_name_pod:", r.content[0].text)
        time.sleep(12)

        # assertions (cluster side)
        logs = kube("-n", "prod", "logs", "-l", "app=docs-frontend",
                    "--tail=100", "--all-containers=true")
        results.append(("web_log in access log", "MARKER-MCP-VERIFY-1" in logs))
        desc = kube("-n", "prod", "describe", "pod", "log-generator")
        results.append(("forged event in describe", "MARKER-MCP-VERIFY-2" in desc))
        ex = kube("-n", "prod", "exec", "verify-hijack", "--", "free")
        results.append(("hijacked tool payload", "MARKER-MCP-VERIFY-3" in ex))
        term = kube("-n", "prod", "get", "pod", "verify-term", "-o",
                    "jsonpath={.status.containerStatuses[0].state.terminated.message}")
        results.append(("termination message in status", "MARKER-MCP-VERIFY-4" in term))
        evimg = kube("-n", "prod", "get", "events", "--sort-by=.lastTimestamp")
        results.append(("image name in events", "marker-mcp-verify-5-dashed-payload" in evimg))

        # 4. clear
        r = await c.call_tool("clear_injections", {})
        print("clear_injections: ok")
        time.sleep(3)
        left = kube("get", "pods", "-A", "-l", "redteam.io/planted=true", "--no-headers")
        results.append(("planted pods cleared", left.strip() == ""))

    ok = all(v for _, v in results)
    for name, v in results:
        print(("PASS " if v else "FAIL ") + name)
    sys.exit(0 if ok else 1)

asyncio.run(main())
