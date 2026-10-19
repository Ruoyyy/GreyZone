"""Static image host for the simulated camera.

Serves /images/current.jpg at :11500/image.jpg. A generic_camera in Home
Assistant points here, so the real vision model reads an image you control by
swapping that file. (The former fake-caption VLM endpoints were removed once the
camera feature moved to a real vision model.)
"""

import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 11500
IMAGE = pathlib.Path("/images/current.jpg")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/image.jpg" and IMAGE.exists():
            body = IMAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"image host on :{PORT}/image.jpg (serving {IMAGE})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
