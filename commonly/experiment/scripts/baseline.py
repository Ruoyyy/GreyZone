"""Consistent cold snapshots of all seven Commonly volumes, including credentials."""
import argparse
import os
from pathlib import Path
import subprocess
import signal
import uuid

from experiment_common import (BASELINES, ORACLE, PYTHON, ROOT, child_env, configuration,
                               digest, now, read_json, run_lock, safe_name, write_json, reset_budget, reset_time_remaining)
from environment import (SERVICES, VOLUMES, compose, images, verify_substrate,
                         volumes, wait_quiet, quiet)

RECEIPT = ORACLE / "restored-baseline.json"


def archive_episode():
    path = ORACLE / "episode.json"
    if path.exists():
        ep = read_json(path)
        path.rename(ORACLE / f"episode-{ep['n']}-{uuid.uuid4().hex[:8]}.json")
    RECEIPT.unlink(missing_ok=True)


def volume_command(volume, image, restore=False):
    mount = f"type=volume,src={volume},dst=/volume" + ("" if restore else ",readonly")
    command = ["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
               "--user", "0:0", "--mount", mount]
    if restore:
        return [*command, "-i", "--entrypoint", "sh", image, "-c",
                "find /volume -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xpf - -C /volume"]
    return [*command, "--entrypoint", "tar", image, "-cpf", "-", "-C", "/volume", "."]


def transfer(volume, image, stream, restore=False):
    name = "commonly-experiment-volume-" + uuid.uuid4().hex
    command = volume_command(volume, image, restore)
    command[3:3] = ["--name", name]
    try:
        subprocess.run(command, **{"stdin" if restore else "stdout": stream}, check=True, timeout=reset_time_remaining(default=600))
    except BaseException:
        # Killing the docker client alone does not guarantee the helper stopped.
        try:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, check=False)
        except Exception:
            pass
        raise


def check_volume_ownership(mapping):
    for logical, volume in mapping.items():
        import json
        labels = json.loads(subprocess.check_output(
            ["docker", "volume", "inspect", volume, "--format", "{{json .Labels}}"], text=True,
            timeout=reset_time_remaining(default=30)))
        if labels.get("com.docker.compose.project") != "commonly" or labels.get("com.docker.compose.volume") != logical:
            raise ValueError(f"Refusing volume outside the Commonly project: {logical}")


def verify(name, compatible=True):
    path = BASELINES / safe_name(name)
    manifest = read_json(path / "manifest.json")
    expected = {f"{logical}.tar" for logical in VOLUMES}
    if manifest.get("schema_version") != 1 or manifest.get("name") != name or set(manifest.get("files", {})) != expected:
        raise ValueError("Incomplete or unsupported Commonly baseline")
    for filename in sorted(expected):
        if digest(path / filename) != manifest["files"][filename]:
            raise ValueError(f"Baseline archive changed: {filename}")
    if compatible and (manifest["target_configuration"] != configuration(target_only=True) or manifest["images"] != images()):
        raise ValueError("Target source, configuration or images changed; create a new baseline")
    return {"name": name, "manifest_sha256": digest(path / "manifest.json")}


def save(name, descriptor):
    destination = BASELINES / safe_name(name)
    if destination.exists():
        raise ValueError("Baseline already exists; choose a new name")
    BASELINES.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = BASELINES / (".capture-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    print("[baseline] Hard reset, seed and verify a fresh Commonly lab", flush=True)
    subprocess.run([str(PYTHON), "redteam/reset.py", "--hard"], cwd=ROOT,
                   env=child_env(descriptor), pass_fds=(descriptor,), check=True, timeout=reset_time_remaining())
    wait_quiet()
    verify_substrate(descriptor)
    archive_episode()
    wait_quiet()
    mapping, frozen_images = volumes(), images()
    check_volume_ownership(mapping)
    # Stop producers first, then recheck settlement while Mongo remains available.
    producers = ("agent-kai", "redteam-mcp", "frontend", "backend")
    compose("stop", "--timeout", "30", *producers)
    try:
        if not quiet():
            raise ValueError("Activity appeared while stopping Commonly")
        compose("stop", "--timeout", "60", "litellm", "postgres", "mongo")
        for logical, volume in mapping.items():
            file = temporary / f"{logical}.tar"
            with file.open("wb") as stream:
                file.chmod(0o600)
                transfer(volume, frozen_images["secrets"], stream)
        write_json(temporary / "manifest.json", {"schema_version": 1, "name": name,
            "created_at": now(), "kind": "commonly-cold-volumes-v1", "images": frozen_images,
            "target_configuration": configuration(target_only=True),
            "files": {f"{v}.tar": digest(temporary / f"{v}.tar") for v in VOLUMES}})
        temporary.rename(destination)
    finally:
        compose("up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "150", *SERVICES)
    print(f"[baseline] Saved {destination}", flush=True)


def restore(name, run_id, descriptor, expected_manifest=None):
    uuid.UUID(run_id)
    reference = verify(name)  # All archive and compatibility checks precede mutation.
    if expected_manifest and reference["manifest_sha256"] != expected_manifest:
        raise ValueError("Baseline differs from the frozen batch")
    manifest = read_json(BASELINES / name / "manifest.json")
    mapping = volumes()
    check_volume_ownership(mapping)
    archive_episode()
    compose("stop", "--timeout", "60", *SERVICES)
    compose("rm", "-f", *SERVICES)
    # Databases are stopped too: archives from different moments are never mixed.
    for logical, volume in mapping.items():
        with (BASELINES / name / f"{logical}.tar").open("rb") as stream:
            transfer(volume, manifest["images"]["secrets"], stream, restore=True)
    # Explicit --no-deps avoids re-running secrets or seed initializers.
    compose("up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "150", *SERVICES)
    wait_quiet()
    verify_substrate(descriptor)
    archive_episode()
    wait_quiet()
    write_json(RECEIPT, {"schema_version": 1, "runId": run_id, "baseline": reference, "restoredAt": now()})
    print(f"[baseline] Restored {name}; receipt bound to {run_id}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save", "restore", "check"))
    parser.add_argument("name", type=safe_name)
    parser.add_argument("--run-id")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--timeout", type=float, default=1200, help="total capture/restore deadline in seconds")
    args = parser.parse_args()
    os.umask(0o077)
    with run_lock() as descriptor, reset_budget(args.timeout):
        if args.action == "save":
            save(args.name, descriptor)
        elif args.action == "restore":
            if not args.run_id:
                parser.error("restore requires --run-id")
            restore(args.name, args.run_id, descriptor, args.manifest_sha256)
        else:
            print(verify(args.name))


if __name__ == "__main__":
    def terminate(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")
    signal.signal(signal.SIGTERM, terminate)
    main()
