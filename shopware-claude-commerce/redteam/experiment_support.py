"""Shared campaign lock and optional private experiment evidence."""
import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".run/oracle/redteam.lock"


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def campaign_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.getenv("SHOPWARE_CAMPAIGN_LOCK_FD")
    descriptor = os.dup(int(inherited)) if inherited else os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        actual, expected = os.fstat(descriptor), LOCK_PATH.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError("Inherited campaign lock belongs to another file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another Shopware campaign is active") from error
        yield descriptor
    finally:
        os.close(descriptor)


def evidence(name, value, *, exclusive=False):
    directory = os.getenv("SHOPWARE_EXPERIMENT_DIR")
    if directory:
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("x" if exclusive else "w") as stream:
            json.dump(value, stream, indent=2, default=str)
            stream.write("\n")
        path.chmod(0o600)
