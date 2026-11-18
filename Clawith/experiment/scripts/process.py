"""Stream subprocess output and stop its process group on failure."""
import codecs
import os
import selectors
import signal
import subprocess
import time

def execute(command, cwd, env, descriptor, progress, log_path, timeout, *, check=True, keep_children=False):
    """Drain bytes continuously, keep heartbeats independent, and terminate the whole task on failure."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        pass_fds=(descriptor,) if descriptor is not None else ())
    progress.attach(process)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    started = time.monotonic()
    stop_tree = True
    try:
        with selectors.DefaultSelector() as selector, log_path.open("ab") as log:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Command exceeded {timeout}s: {command[0]}")
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(chunk)
                    log.flush()
                    progress.forward(decoder.decode(chunk))
            progress.forward(decoder.decode(b"", final=True))
        code = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        if check and code:
            raise RuntimeError(f"Command exited {code}: {' '.join(command)}")
        stop_tree = not (keep_children and code == 0)
        return code
    finally:
        if stop_tree:
            # Framework descendants must not continue writing after timeout/failure.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close()
