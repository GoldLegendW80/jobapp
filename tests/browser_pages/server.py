"""The pages server of the browser test stack (tests/browser.compose.yml, browser B2): the static
pages of this folder, a resource held back for `?ms=` milliseconds (slow.html) and an echo of the
request headers (the Accept-Language the profile's preferences give), and a 302 to `?to=`.
A folder asked with `?q=<word>`, `?query=<word>` or `?keywords=<word>` answers its file
`<word>.html` when there is one: the search pages of browser B8 (b8/) and of B10's LinkedIn
(b10/linkedin/, reached as b8/linkedin/), one fixture per search word. Run with the browser image's
Python, read only, as an unprivileged user, on the `egress` network: the public side of the test.

It answers HTTP on 8000 and on 8080 (a port the egress proxy allows: by its private address the
proxy must refuse it for the address alone), and counts the UDP datagrams that reach it on 3478
(STUN and TURN): GET /udp answers the count, which the WebRTC test expects to stay 0. GET
/count/<key> counts the requests of each key, which GET /counts answers: the off list host of the
allow list tests (a navigation browserd stops never reaches it).
"""

import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse

ROOT = sys.argv[1]
SEARCH_WORD = re.compile(r"[a-z0-9]{1,40}")
DATAGRAMS = {"count": 0}
COUNTS = {}


def count_datagrams() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 3478))
    while True:
        sock.recvfrom(65536)
        DATAGRAMS["count"] += 1


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
        if url.path.startswith("/count/"):
            key = url.path[len("/count/") :][:40]
            COUNTS[key] = COUNTS.get(key, 0) + 1
            return self.send("<title>Elsewhere</title><p>Elsewhere</p>", "text/html")
        if url.path == "/counts":
            return self.send(json.dumps(COUNTS))
        if url.path == "/udp":
            return self.send(json.dumps(DATAGRAMS))
        if url.path == "/redirect":  # a server side 302 (browser B5's allow list test)
            self.send_response(302)
            self.send_header("Location", query.get("to", ["/"])[0])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        word = (query.get("q") or query.get("query") or query.get("keywords") or [""])[
            0
        ]
        folder = self.translate_path(url.path)
        if SEARCH_WORD.fullmatch(word) and os.path.isfile(
            os.path.join(folder, word + ".html")
        ):
            self.path = url.path.rstrip("/") + "/" + word + ".html"
            return super().do_GET()
        if url.path == "/headers":
            return self.send(
                json.dumps({k.lower(): v for k, v in self.headers.items()})
            )
        return super().do_GET()


threading.Thread(target=count_datagrams, daemon=True).start()
threading.Thread(
    target=http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever,
    daemon=True,
).start()
http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
