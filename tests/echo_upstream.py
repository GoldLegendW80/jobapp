"""Stand-in for the api in tests/compose_smoke.sh: answers every request on :8000 with the
request line, headers and body size it received, as JSON. Reads the whole body, so Caddy's
streaming body limit is exercised. Standard library only."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Echo(BaseHTTPRequestHandler):
    def handle_any(self) -> None:
        size = 0
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                chunk = int(line.split(b";")[0].strip() or b"0", 16)
                if chunk == 0:
                    self.rfile.readline()
                    break
                size += len(self.rfile.read(chunk))
                self.rfile.readline()
        else:
            remaining = int(self.headers.get("Content-Length") or 0)
            while remaining:
                data = self.rfile.read(min(remaining, 65536))
                if not data:
                    break
                size += len(data)
                remaining -= len(data)
        body = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body_bytes": size,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = handle_any

    def log_message(self, *args: object) -> None:
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Echo).serve_forever()
