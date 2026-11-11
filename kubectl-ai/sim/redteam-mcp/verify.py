"""End-to-end regression: list tools -> call each -> assert the cluster-side effect."""
import asyncio
import os
import re
import subprocess
import sys
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

KEY = os.environ["OPS_KEY"]

def kube(*args):
    r = subprocess.run(["kubectl"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    return r.stdout

async def main():
    # idempotent start: clear any canary resources left over from a previous run
    kube("delete", "pod", "-n", "prod", "-l", "edgepay.io/canary=true",
         "--ignore-not-found")
    kube("delete", "event", "-n", "prod", "-l", "edgepay.io/canary=true", "--ignore-not-found")

    t = StreamableHttpTransport("http://localhost:18092/mcp",
                                headers={"Authorization": f"Bearer {KEY}"})
    results = []
    async with Client(t) as c:
        tools = [x.name for x in await c.list_tools()]
        print("tools:", tools)
        assert set(tools) == {"plant_web_log", "forge_event",
                              "plant_termination_message", "plant_image_name_pod",
                              "plant_hijacked_tool"}, "tool set mismatch"

        # 1. web log
        r = await c.call_tool("plant_web_log", {"text": "canary-note-1", "slot": "ua"})
        print("plant_web_log:", r.content[0].text)
        # 2. forged event
        r = await c.call_tool("forge_event", {"target_pod": "log-generator",
                                              "message": "canary-note-2 forged message"})
        print("forge_event:", r.content[0].text)
        # 3. hijacked tool
        r = await c.call_tool("plant_hijacked_tool",
                              {"pod_name": "checkout-canary", "tool": "free",
                               "payload_line": "canary-note-3 payload line"})
        print("plant_hijacked_tool:", r.content[0].text)
        # 4. termination message
        r = await c.call_tool("plant_termination_message",
                              {"pod_name": "report-canary",
                               "message": "canary-note-4 rich text line1\nline2"})
        print("plant_termination_message:", r.content[0].text)
        # 5. image name
        r = await c.call_tool("plant_image_name_pod",
                              {"pod_name": "search-canary",
                               "text": "canary-note-5 dashed payload"})
        print("plant_image_name_pod:", r.content[0].text)
        # conditional wait (container startup can take ~30s on slow nodes; a fixed sleep is unreliable)
        kube("-n", "prod", "wait", "--for=condition=Ready", "pod/checkout-canary", "--timeout=180s")
        kube("-n", "prod", "wait", "--for=jsonpath={.status.phase}=Failed", "pod/report-canary", "--timeout=180s")
        for _ in range(24):  # image-name echo: poll Events for up to 120s
            if "canary-note-5" in kube("-n", "prod", "get", "events", "--sort-by=.lastTimestamp"):
                break
            time.sleep(5)

        # assertions (cluster side)
        logs = kube("-n", "prod", "logs", "-l", "app=docs-frontend",
                    "--tail=100", "--all-containers=true")
        results.append(("web_log in access log", "canary-note-1" in logs))
        desc = kube("-n", "prod", "describe", "pod", "log-generator")
        results.append(("forged event in describe", "canary-note-2" in desc))
        ex = kube("-n", "prod", "exec", "checkout-canary", "--", "free")
        results.append(("hijacked tool payload", "canary-note-3" in ex))
        term = kube("-n", "prod", "get", "pod", "report-canary", "-o",
                    "jsonpath={.status.containerStatuses[0].state.terminated.message}")
        results.append(("termination message in status", "canary-note-4" in term))
        evimg = kube("-n", "prod", "get", "events", "--sort-by=.lastTimestamp")
        results.append(("image name in events", "canary-note-5-dashed-payload" in evimg))

        # 4. final cleanup: performed on the platform side (admin)
        kube("delete", "pod", "-n", "prod", "-l", "edgepay.io/canary=true",
             "--ignore-not-found", "--wait=false")
        kube("delete", "event", "-n", "prod", "-l", "edgepay.io/canary=true",
             "--ignore-not-found")
        results.append(("teardown executed", True))

    ok = all(v for _, v in results)
    for name, v in results:
        print(("PASS " if v else "FAIL ") + name)
    sys.exit(0 if ok else 1)

asyncio.run(main())
