"""Snapshot and restore the existing Letta volumes without deleting volumes."""
import subprocess

from common import EXPERIMENT, compose, configuration_hashes, digest, now, read_json, write_json

VOLUMES = {"gitea": "/data", "runner": "/home/runner/letta-state"}
SERVICES = ("runner", "redteam-mcp", "gitea")


def image_ids():
    names = compose("config", "--images", capture_output=True, text=True).stdout.splitlines()
    return {name: subprocess.check_output(
        ["docker", "image", "inspect", name, "--format", "{{.Id}}"], text=True).strip()
        for name in sorted(set(names))}


def baseline_path(name):
    return EXPERIMENT / "baselines" / name


def save_baseline(name):
    path = baseline_path(name)
    path.mkdir(parents=True, exist_ok=False)
    compose("stop", *SERVICES)
    try:
        for service, mount in VOLUMES.items():
            with (path / f"{service}.tar").open("wb") as stream:
                compose("run", "--rm", "--no-deps", "-T", "--user", "0",
                        "--entrypoint", "tar", service, "-C", mount, "-cpf", "-", ".",
                        stdout=stream)
        write_json(path / "manifest.json", {"created_at": now(),
            "configuration_hashes": configuration_hashes(),
            "images": image_ids(),
            "archives": {p.name: digest(p) for p in path.glob("*.tar")}})
    finally:
        # Start existing containers; do not rerun the Gitea initializer.
        compose("start", "gitea", "runner", "redteam-mcp")
    return path


def verify_baseline(name):
    path = baseline_path(name)
    manifest = read_json(path / "manifest.json")
    if manifest["configuration_hashes"] != configuration_hashes():
        raise ValueError("Configuration changed since baseline capture; create a new baseline")
    if manifest["images"] != image_ids():
        raise ValueError("Docker images changed since baseline capture; create a new baseline")
    for service in VOLUMES:
        archive = path / f"{service}.tar"
        if digest(archive) != manifest["archives"][archive.name]:
            raise ValueError(f"Baseline archive changed: {archive.name}")
    return path


def restore_baseline(name, env):
    path = verify_baseline(name)
    compose("stop", *SERVICES, env=env)
    for service, mount in VOLUMES.items():
        # The mount paths are fixed constants, never user input. Remove old files
        # before extracting so mutations from previous experiments cannot survive.
        command = (f"find {mount} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} + "
                   f"&& tar -xpf - -C {mount}")
        with (path / f"{service}.tar").open("rb") as stream:
            compose("run", "--rm", "--no-deps", "-T", "--user", "0",
                    "--entrypoint", "sh", service, "-c", command, stdin=stream,
                    stdout=subprocess.DEVNULL, env=env)
    compose("start", "gitea", env=env)
    # Recreate the runner to clear writable-layer and anonymous-volume job state.
    # Named volumes retain the restored state. Never rerun gitea-init here.
    compose("up", "-d", "--no-deps", "--force-recreate", "--renew-anon-volumes",
            "runner", "redteam-mcp", env=env)
