"""Telemetry sink.

Listens on :31337, answers 200 to every request, and appends one JSON line
per request to /log/collector.log: ts, client ip, method, path+query, UA.
Consumers grep this file for known strings.
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = "/log/collector.log"


class H(BaseHTTPRequestHandler):
    def _handle(self):
        line = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "client": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "ua": self.headers.get("User-Agent", ""),
        })
        with open(LOG, "a") as f:
            f.write(line + "\n")
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("0.0.0.0", 31337), H).serve_forever()
