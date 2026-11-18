"""One checkout-wide lock, inherited by experiment launcher children."""
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def campaign_lock():
    path = ROOT / ".run/campaign.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.get("CLAWITH_CAMPAIGN_LOCK_FD")
    if inherited is not None:
        descriptor = int(inherited)
        if os.fstat(descriptor).st_ino != path.stat().st_ino:
            raise RuntimeError("Invalid inherited campaign lock")
        yield descriptor
        return
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Clawith campaign is running") from None
        yield stream.fileno()


def locked(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with campaign_lock():
            return function(*args, **kwargs)
    return wrapper


if __name__ == "__main__":
    with campaign_lock() as descriptor:
        raise SystemExit(subprocess.call(sys.argv[1:], env={**os.environ,
            "CLAWITH_CAMPAIGN_LOCK_FD": str(descriptor)}, pass_fds=(descriptor,)))
