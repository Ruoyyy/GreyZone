"""Archive only this candidate's files before any subsequent baseline restore."""
import shutil
import subprocess

from experiment_common import ORACLE, ROOT, read_json, write_json
from environment import COMPOSE


def native_path(run):
    return ROOT / ".run" / run["framework"] / f"{run['scenario']}-{run['run_id']}"


def collect(path, run):
    source = native_path(run)
    destination = path / "framework/native"
    if source.exists():
        config = read_json(source / "config.json")
        if config.get("runId") != run["run_id"]:
            raise ValueError("Native candidate run ID mismatch")
        # Cache contents are not evidence and can contain SDK-local credentials.
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("cache", "__pycache__", "*.lock"))
    ep = None
    if (destination / "episode.json").is_file():
        ep = read_json(destination / "episode.json")
    live = ORACLE / "episode.json"
    if live.is_file():
        candidate = read_json(live)
        if candidate.get("redteam", {}).get("runId") == run["run_id"]:
            ep = candidate
    if ep is not None and ep.get("redteam", {}).get("runId") == run["run_id"]:
        write_json(path / "oracle/episode.json", ep)
        alerts = ORACLE / f"alerts-{ep['n']}.jsonl"
        if alerts.is_file():
            (path / "oracle").mkdir(exist_ok=True, mode=0o700)
            shutil.copy2(alerts, path / "oracle/alerts.jsonl")
    if (destination / "oracle-result.json").is_file():
        (path / "oracle").mkdir(exist_ok=True, mode=0o700)
        shutil.copy2(destination / "oracle-result.json", path / "oracle/result.json")
    if not destination.exists():
        raise ValueError("Native candidate artifacts are unavailable")
    marker_file = destination / "completion.json"
    if marker_file.is_file():
        run["framework_status"] = read_json(marker_file).get("frameworkStatus")
    return destination


def optional_diagnostics(path, run):
    directory = path / "diagnostics"
    directory.mkdir(exist_ok=True, mode=0o700)
    native = path / "framework/native"
    rows = {p.stem: read_json(p) for p in native.glob("target-*.json")}
    write_json(directory / "target-runtime.json", rows)
    with (directory / "backend.log").open("wb") as stream:
        subprocess.run([*COMPOSE, "logs", "--no-color", "--since", run["started_at"], "backend"],
                       cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=30)
