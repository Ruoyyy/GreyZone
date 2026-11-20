"""Retry only a completed candidate's Oracle finalization; never send an attack."""
import argparse
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

from redteam.common import run_lock, scenario_data
from redteam.runner import Session
from oracle import common as C


async def recover(directory, run_id):
    import json
    def read(name):
        return json.loads((directory / name).read_text())
    config, marker, state = read("config.json"), read("completion.json"), read("attack-state.json")
    if (config["runId"] != run_id or marker.get("frameworkStatus") != "completed" or marker.get("error")
            or not state.get("events") or len(state["events"]) != state["turns"]
            or any(e["status"] not in {"continue", "oracle-success"} for e in state["events"])):
        raise ValueError("Only a durably completed framework can be finalized")
    ep = C.load_episode()
    if not ep or ep["n"] != state["episode"] or ep.get("redteam", {}).get("runId") != run_id:
        raise ValueError("Matching live Oracle episode is unavailable")
    session = Session.__new__(Session)
    session.args = SimpleNamespace(scenario=config["scenario"], model=config["model"], oracle_wait=config["oracleWaitSeconds"])
    session.scenario, session.objective = scenario_data(config["scenario"])
    session.framework, session.run_id = config["framework"], run_id
    session.max_turns, session.baseline = config["maxTurns"], config.get("baseline")
    session.directory, session.path, session.episode = directory, directory / "attack-state.json", ep
    os.environ.update(GREYZONE_RUN_ID=run_id, GREYZONE_ATTACKER_MODEL=config["model"])
    if config.get("revision"):
        os.environ["GREYZONE_REVISION"] = config["revision"]
    return await session.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    with run_lock():
        return asyncio.run(recover(args.directory, args.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
