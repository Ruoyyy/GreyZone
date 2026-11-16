"""Live experiment status, independent of framework output or newline buffering."""
import sys
import threading
import time

from common import now


class Progress:
    def __init__(self, path, interval=30, output=None):
        self.path = path
        self.interval = interval
        self.output = output if output is not None else sys.stdout
        self.started = time.monotonic()
        self.phase_started = self.started
        self.phase = "starting"
        self.process = None
        self.last_output = None
        self.line_start = True
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join()

    def _message(self, kind, text):
        line = f"[{now()}] [{kind}] {text}\n"
        if not self.line_start:
            self.output.write("\n")
        self.output.write(line)
        self.output.flush()
        self.line_start = True
        with self.path.open("a") as stream:
            stream.write(line)

    def message(self, text):
        with self.lock:
            self._message("experiment", text)

    def stage(self, name, details=""):
        with self.lock:
            self.phase = name
            self.phase_started = time.monotonic()
            self.process = None
            self._message("experiment", name + (f" | {details}" if details else ""))

    def attach(self, process):
        with self.lock:
            self.process = process

    def forward(self, text):
        """Only native text goes to the terminal; execute() saves its raw bytes."""
        if not text:
            return
        with self.lock:
            self.output.write(text)
            self.output.flush()
            self.line_start = text.endswith(("\n", "\r"))
            self.last_output = time.monotonic()

    def heartbeat(self):
        with self.lock:
            current = time.monotonic()
            fields = [f"stage={self.phase}", f"elapsed={current - self.started:.0f}s",
                      f"stage_elapsed={current - self.phase_started:.0f}s"]
            if self.process is not None:
                code = self.process.poll()
                fields += [f"pid={self.process.pid}",
                           "process=running" if code is None else f"process=exited({code})"]
                fields.append(f"last_output={current - self.last_output:.0f}s ago"
                              if self.last_output is not None else "last_output=none")
            self._message("status", " | ".join(fields))

    def _watch(self):
        while not self.stopped.wait(self.interval):
            self.heartbeat()
