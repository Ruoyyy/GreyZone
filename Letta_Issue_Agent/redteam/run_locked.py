#!/usr/bin/env python3
"""Shared local campaign lock. Held across exec, including child failure/signals."""
import fcntl
import os
import sys
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / ".campaign.lock"


def acquire():
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SystemExit("Another red-team campaign is active in this checkout")
    return fd


if __name__ == "__main__":
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        raise SystemExit("Usage: run_locked.py -- command [args...]")
    descriptor = acquire()
    os.set_inheritable(descriptor, True)
    os.execvp(command[0], command)
