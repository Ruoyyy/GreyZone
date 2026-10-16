"""Mock VLM speaking the Ollama API.

Every /api/chat request is answered with the contents of caption.txt (re-read on
each request), regardless of the image. Each request is logged to stdout.
"""
import json
import pathlib
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAPTION = pathlib.Path(__file__).with_name("caption.txt")
IMAGE = pathlib.Path("/images/current.jpg")
MODEL = "mock-vlm:latest"
PORT = 11500


def now():
    return datetime.now(UTC).isoformat()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": MODEL, "model": MODEL, "size": 0,
                                    "modified_at": now(), "digest": "mock",
                                    "details": {"family": "mock"}}]})
        elif self.path == "/api/ps":
            self._json({"models": []})
        elif self.path == "/api/version":
            self._json({"version": "0.0.0-mock"})
        elif self.path.split("?")[0] == "/image.jpg":
            # Serve images/current.jpg so a generic_camera can point here and a
            # real VLM gets a JPG you control. 404 until the file exists.
            if IMAGE.exists():
                body = IMAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "images/current.jpg not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/show":
            self._json({"details": {"family": "mock"}, "model_info": {},
                        "capabilities": ["completion", "vision"]})
            return
        if self.path != "/api/chat":
            self._json({"error": "not found"}, 404)
            return

        caption = CAPTION.read_text(encoding="utf-8").strip()
        msgs = req.get("messages", [])
        print(json.dumps({
            "time": now(),
            "model": req.get("model"),
            "user_text": [m.get("content") for m in msgs if m.get("role") == "user"],
            "images": sum(len(m.get("images") or []) for m in msgs),
            "reply": caption,
        }, ensure_ascii=False), flush=True)

        msg = {"model": req.get("model") or MODEL, "created_at": now(),
               "message": {"role": "assistant", "content": caption}, "done": False}
        final = {"model": req.get("model") or MODEL, "created_at": now(),
                 "message": {"role": "assistant", "content": ""}, "done": True,
                 "done_reason": "stop", "total_duration": 1, "load_duration": 0,
                 "prompt_eval_count": 1, "eval_count": 1}
        if req.get("stream", True):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk in (msg, final):
                self.wfile.write((json.dumps(chunk) + "\n").encode())
        else:
            self._json({**final, "message": msg["message"]})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"mock VLM on :{PORT}, caption file {CAPTION}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
