"""Archive exact target checkpoints before the next attempt restores the database."""
import json
import subprocess
import uuid

from experiment_common import ROOT, read_json, write_json
from redteam.target_runtime import failure_reply

EXPORT = r'''
import asyncio, json, sys, uuid
from sqlalchemy import select
from app.database import async_session
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.checkpointer import create_checkpointer, runtime_thread_config

async def main():
    output = []
    async with async_session() as db, create_checkpointer() as saver:
        for value in sys.argv[1:]:
            run = await db.get(AgentRun, uuid.UUID(value))
            if run is None:
                raise RuntimeError('Target Run is no longer available: ' + value)
            events = (await db.execute(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id)
                      .order_by(AgentRunEvent.created_at))).scalars().all()
            checkpoints = []
            async for item in saver.alist(runtime_thread_config(run.runtime_thread_id),
                    filter={'clawith_run_id': str(run.id)}, limit=10):
                values = item.checkpoint.get('channel_values', {})
                lifecycle = values.get('lifecycle', {})
                messages = []
                for message in values.get('messages', []):
                    if hasattr(message, 'model_dump'):
                        message = message.model_dump(mode='json')
                    if isinstance(message, dict):
                        messages.append({k: message[k] for k in ('id','type','role','content',
                            'tool_calls','additional_kwargs','runtime_intent') if k in message})
                checkpoints.append({'id': item.checkpoint['id'], 'status': lifecycle.get('status'),
                    'error': lifecycle.get('error'), 'repairs': lifecycle.get('model_protocol_repairs'),
                    'messages': messages})
            output.append({'run_id': value, 'thread_id': run.runtime_thread_id,
                'events': [{'type': event.event_type, 'summary': event.summary, 'payload': event.payload}
                           for event in events], 'checkpoints': checkpoints})
    print(json.dumps({'runs': output}, default=str))

asyncio.run(main())
'''


def target_errors(events):
    errors = []
    for event in events:
        if event.get("kind") != "result":
            continue
        error = event.get("target_error")
        if not error and "target_runs" not in event:
            error = failure_reply(event.get("reply"))
        if not error:
            failed = next((run for run in event.get("target_runs", [])
                           if run.get("status") in {"failed", "cancelled"}), None)
            if failed:
                error = {"code": "target_" + failed["status"], "run_id": failed["run_id"]}
        if error:
            errors.append(error)
    return errors


def read_events(path):
    file = path / "events.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()] if file.exists() else []


def archive_diagnostics(path, started_at):
    ids = set()
    for event in read_events(path):
        ids.update(run["run_id"] for run in event.get("target_runs", []))
        ids.update(event.get("target_run_ids", []))
        if error := failure_reply(event.get("reply")):
            ids.add(error["run_id"])
    for file in (path / "framework").glob("*/attack-state.json"):
        ids.update(read_json(file).get("target_run_ids", []))
    ids = sorted(str(uuid.UUID(value)) for value in ids)
    if ids:
        result = subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c", EXPORT, *ids],
                                cwd=ROOT, capture_output=True, text=True, check=True, timeout=120)
        write_json(path / "target-runtime.json", json.loads(result.stdout.strip().splitlines()[-1]))
    with (path / "backend.log").open("wb") as output:
        subprocess.run(["docker", "compose", "logs", "--no-color", "--since", started_at, "backend"],
                       cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=30)
