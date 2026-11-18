"""Persist framework completion before fallible Oracle finalization."""
import json
import os
import subprocess


def close(command, env, directory, scenario, episode, completed):
    path = directory / "completion.json"
    marker = {"run_id": env.get("GREYZONE_RUN_ID"), "scenario": scenario, "episode": episode,
              "framework_status": "completed" if completed else "error"}

    def save():
        path.write_text(json.dumps(marker, indent=2) + "\n")
        path.chmod(0o600)

    save()
    try:
        subprocess.run(command, cwd=directory.parents[2], env=env, check=True,
                       timeout=int(env.get("CLAWITH_ORACLE_TIMEOUT", "1200")))
        marker["oracle_status"] = "completed"
    except (OSError, subprocess.SubprocessError) as error:
        marker.update(oracle_status="error", oracle_error=f"{type(error).__name__}: {error}")
    save()
    return marker.get("oracle_error")
