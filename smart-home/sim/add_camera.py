"""Add a generic_camera that pulls its still image from mock-vlm (/image.jpg).

From sim/:  python3 add_camera.py
The camera entity will be camera.<slug of url>; HGA then analyzes a JPG you
control (sim/images/current.jpg) instead of HA's built-in demo images.
"""
import json
import pathlib
import urllib.request

HA = "http://localhost:8123"
STILL_URL = "http://localhost:11500/image.jpg"


def token():
    for line in (pathlib.Path(__file__).parent / "secrets.env").read_text().splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HA_TOKEN not in secrets.env")


def call(method, path, body=None):
    req = urllib.request.Request(
        HA + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or "null")


# Already added? (match by title slug)
for e in call("GET", "/api/config/config_entries/entry?domain=generic"):
    if STILL_URL in json.dumps(e.get("options") or e.get("data") or {}):
        print("generic camera already configured:", e["title"])
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
