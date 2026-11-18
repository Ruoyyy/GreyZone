"""Wait for episode-created Runs, deliveries and tool reservations to settle."""
import asyncio
import json
import subprocess
import time
from datetime import datetime

from oracle import common as C


def inspect(started_at):
    timestamp = datetime.fromisoformat(started_at.replace("Z", "+00:00")).isoformat()
    sql = f"""SELECT json_build_object(
      'pending', (SELECT count(*) FROM agent_runs WHERE created_at >= '{timestamp}'
                  AND (lane_held OR delivery_status='pending'))
                + (SELECT count(*) FROM agent_run_commands c JOIN agent_runs r ON r.id=c.run_id
                   WHERE r.created_at >= '{timestamp}' AND c.status IN ('pending','claimed'))
                + (SELECT count(*) FROM agent_tool_executions WHERE started_at >= '{timestamp}'
                   AND status IN ('started','unknown')),
      'failed_runs', COALESCE((SELECT json_agg(DISTINCT run_id) FROM agent_run_events
                     WHERE created_at >= '{timestamp}' AND event_type IN ('run_failed','run_cancelled')), '[]'))"""
    result = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "clawith",
                             "-d", "clawith", "-v", "ON_ERROR_STOP=1", "-tAc", sql], cwd=C.REPO_ROOT,
                            capture_output=True, text=True, timeout=20, check=True)
    value = json.loads(result.stdout)
    if type(value.get("pending")) is not int or not isinstance(value.get("failed_runs"), list):
        raise ValueError("Invalid target settlement response")
    return value


async def wait(ep, timeout):
    deadline = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < deadline:
        state = await asyncio.to_thread(inspect, ep["startedAt"])
        if state["failed_runs"]:
            raise RuntimeError("Episode includes failed or cancelled target Runs: " + ",".join(state["failed_runs"]))
        quiet = quiet + 1 if state["pending"] == 0 else 0
        if quiet >= 2:
            return {"status": "settled", "checkedAt": C.now_iso(), **state}
        await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError("Target Runs, deliveries or tool reservations did not settle")
