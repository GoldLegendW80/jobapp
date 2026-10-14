"""The pages server of the browser test stack (tests/browser.compose.yml, browser B2): the static
pages of this folder, a resource held back for `?ms=` milliseconds (slow.html) and an echo of the
request headers (the Accept-Language the profile's preferences give). Run with the browser image's
Python, read only, as an unprivileged user, on the `egress` network: the public side of the test.
"""

import http.server
import json
import sys
import time
import urllib.parse

ROOT = sys.argv[1]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):
        pass

    def send(self, body, ctype="application/json"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/slow":
            time.sleep(min(int(query.get("ms", ["3000"])[0]), 20000) / 1000)
            if query.get("type", ["js"])[0] == "js":
                return self.send(
                    "window.__slowLoaded = (window.__slowLoaded || 0) + 1;",
                    "text/javascript",
                )
            return self.send(json.dumps({"ok": True}))
        if url.path == "/headers":
            return self.send(
                json.dumps({k.lower(): v for k, v in self.headers.items()})
            )
        return super().do_GET()


http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
