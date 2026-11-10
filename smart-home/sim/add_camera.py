"""Add a generic_camera that pulls its still image from mock-vlm (/image.jpg).

From sim/:  python3 add_camera.py
The camera is named Test Camera to match the benchmark tasks. HGA analyzes
the JPG you control (sim/images/current.jpg) instead of the demo images.
"""
import asyncio
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

HA = "http://localhost:8123"
STILL_URL = "http://localhost:11500/image.jpg"
CAMERA_NAME = "Test Camera"


def token():
    if value := os.environ.get("HA_TOKEN"):
        return value
    for line in (pathlib.Path(__file__).parent / "secrets.env").read_text().splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HA_TOKEN not in secrets.env")


# Reuse Home Assistant's aiohttp installation instead of adding host dependencies.
if importlib.util.find_spec("homeassistant") is None:
    here = pathlib.Path(__file__).resolve().parent
    with open(__file__, "rb") as source:
        raise SystemExit(subprocess.call(
            ["docker", "compose", "exec", "-T", "-e", "HA_TOKEN", "homeassistant", "python3", "-"],
            stdin=source, cwd=here, env={**os.environ, "HA_TOKEN": token()},
        ))

import aiohttp


def call(method, path, body=None):
    req = urllib.request.Request(
        HA + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or "null")


def configured_camera():
    for state in call("GET", "/api/states"):
        if state["entity_id"].startswith("camera.") and state.get("attributes", {}).get("friendly_name") == CAMERA_NAME:
            return state["entity_id"]
    return None


def wait_camera():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if entity_id := configured_camera():
            return entity_id
        time.sleep(1)
    raise SystemExit("Test Camera did not become available within 30 seconds")


async def set_camera_name(entry_id):
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA.replace("http", "ws", 1) + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token()})
            if (await ws.receive_json())["type"] != "auth_ok":
                raise SystemExit("Home Assistant websocket authentication failed")
            request_id = 0

            async def request(kind, **values):
                nonlocal request_id
                request_id += 1
                await ws.send_json({"id": request_id, "type": kind, **values})
                while (result := await ws.receive_json()).get("id") != request_id:
                    pass
                if not result["success"]:
                    raise SystemExit(f"{kind} failed: {result.get('error')}")
                return result["result"]

            await request("config_entries/update", entry_id=entry_id, title=CAMERA_NAME)
            for _ in range(30):
                entities = await request("config/entity_registry/list")
                entity = next((e for e in entities if e.get("config_entry_id") == entry_id
                               and e["entity_id"].startswith("camera.")), None)
                if entity:
                    await request("config/entity_registry/update", entity_id=entity["entity_id"],
                                  name=CAMERA_NAME, new_entity_id="camera.test_camera")
                    return
                await asyncio.sleep(1)
            raise SystemExit("Camera did not register within 30 seconds")


# Entry metadata is available without exposing private configuration options.
for entry in call("GET", "/api/config/config_entries/entry?domain=generic"):
    if entry["title"] == CAMERA_NAME:
        if not configured_camera():
            asyncio.run(set_camera_name(entry["entry_id"]))
        print("generic camera already configured:", wait_camera())
        raise SystemExit

flow = call("POST", "/api/config/config_entries/flow",
            {"handler": "generic", "show_advanced_options": True})
res = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {
    "still_image_url": STILL_URL,
    "advanced": {"framerate": 2, "verify_ssl": False},
})
if res["type"] == "form" and res["step_id"] == "user" and res.get("errors"):
    raise SystemExit(f"generic user step rejected: {res['errors']}")
if res.get("step_id") != "user_confirm":
    raise SystemExit(f"unexpected step: {res.get('step_id')} / {res.get('errors')}")
res = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"confirmed_ok": True})
if res["type"] != "create_entry":
    raise SystemExit(f"confirm failed: {res}")
print("created camera entry:", res["title"])
entry_id = res["result"]["entry_id"]
asyncio.run(set_camera_name(entry_id))
call("POST", f"/api/config/config_entries/entry/{entry_id}/reload", {})
print("camera ready:", wait_camera())
