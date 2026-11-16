#!/usr/bin/env python3
"""Hold one lock for every mail red-team campaign in this checkout."""
import fcntl
import os
import sys
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent / ".tmp/redteam/.campaign.lock"


def acquire():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.getenv("MAIL_CAMPAIGN_LOCK_FD")
    if inherited:
        descriptor = int(inherited)
        if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != (LOCK_PATH.stat().st_dev, LOCK_PATH.stat().st_ino):
            raise SystemExit("Inherited campaign lock does not match this checkout")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return os.dup(descriptor)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise SystemExit("Another mail red-team campaign is active in this checkout")
    return descriptor


if __name__ == "__main__":
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        raise SystemExit("Usage: run_locked.py -- command [args...]")
    descriptor = acquire()
    os.set_inheritable(descriptor, True)
    os.execvp(command[0], command)
