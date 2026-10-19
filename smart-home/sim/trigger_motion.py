"""Fire a motion event on a demo camera to trigger HGA's proactive analysis.

Usage (from sim/):
    python3 trigger_motion.py [motion_sensor] [on_seconds]

Defaults: binary_sensor.demo_camera_motion, 6 seconds on.
Reads HA_TOKEN from secrets.env. After motion clears, HGA runs the VLM over the
captured frames and writes the result to the camera's image.*_last_event summary
attribute, which this script prints.
"""

import json
import pathlib
import sys
import time
import urllib.request

HA = "http://localhost:8123"


def secrets():
    out = {}
    for line in (pathlib.Path(__file__).parent / "secrets.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def call(method, path, body=None):
    req = urllib.request.Request(
        HA + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {secrets()['HA_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or "null")


def set_state(entity, state):
    call("POST", f"/api/states/{entity}", {"state": state, "attributes": {"device_class": "motion"}})


if __name__ == "__main__":
    sensor = sys.argv[1] if len(sys.argv) > 1 else "binary_sensor.demo_camera_motion"
    on_s = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    camera = "image." + sensor.replace("binary_sensor.", "").replace("_motion", "") + "_last_event"

    set_state(sensor, "off")
    time.sleep(1)
    set_state(sensor, "on")
    print(f"{sensor}: on ({on_s}s)")
    time.sleep(on_s)
    set_state(sensor, "off")
    print(f"{sensor}: off; waiting for analysis...")
    time.sleep(15)

    try:
        st = call("GET", f"/api/states/{camera}")
        print(f"{camera}: {st['attributes'].get('summary')}")
    except urllib.error.HTTPError:
        print(f"{camera}: no result (check: docker compose logs homeassistant)")
