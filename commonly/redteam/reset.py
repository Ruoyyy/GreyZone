"""Explicit clean lab reset using existing images; never builds or touches Clawith."""
import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from redteam.common import ROOT, configure, run_lock, reset_budget, reset_time_remaining
configure()
from oracle import common as C


def compose(*args):
    log = ROOT / ".run/reset.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    timeout = reset_time_remaining()
    print(f"[reset] compose {args[0]} (remaining budget: {timeout:.0f}s)", flush=True)
    with log.open("a") as output:
        log.chmod(0o600)
        output.write(f"\n[{C.now_iso()}] compose {args[0]}; remaining budget {timeout:.1f}s\n")
        output.flush()
        process = None
        try:
            process = subprocess.Popen(["docker", "compose", "-p", "commonly", "-f", "docker-compose.stack.yml", *args],
                           cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            code = process.wait(timeout=timeout)
            if code:
                raise subprocess.CalledProcessError(code, process.args)
        except (subprocess.SubprocessError, OSError) as exc:
            reason = "reset deadline exceeded" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
            raise RuntimeError(f"Commonly compose {args[0]} failed ({reason}); inspect {log}") from exc
        finally:
            if process is not None:
                # Compose may spawn a CLI plugin; stop the entire command group.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


def _reset(hard):
    if hard:
        compose("down", "-v", "--timeout", "10")
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "150",
                "secrets", "mongo", "postgres", "litellm", "backend", "frontend")
        compose("run", "--rm", "--no-deps", "seed")
        compose("run", "--rm", "--no-deps", "--entrypoint", "sh", "seed", "-c",
                'eval "$CORE_SECRETS" && exec node dist/scripts/seed-lab.js')
        # `run` above seeded the volumes; no second seed service execution is needed.
        compose("up", "-d", "--no-build", "--no-deps", "agent-kai", "redteam-mcp")
    state = C.RUN_DIR / "episode.json"
    if state.exists():
        ep = json.loads(state.read_text())
        state.rename(C.RUN_DIR / f"episode-{ep['n']}.json")
    print("Clean lab restored." if hard else "Episode archived; product state was retained.")


def reset(hard, timeout=1200):
    with reset_budget(timeout):
        _reset(hard)


if __name__ == "__main__":
    def terminate(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard", action="store_true", help="delete only Commonly lab volumes and reseed")
    parser.add_argument("--timeout", type=float, default=1200, help="total reset deadline in seconds")
    args = parser.parse_args()
    try:
        with run_lock():
            reset(args.hard, args.timeout)
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Commonly reset interrupted", file=sys.stderr)
        raise SystemExit(130)
