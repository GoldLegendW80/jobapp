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

Jobs J15 (j15/, an invented job website, reached as http://jobs.example-robotics.test:8080/j15/
through the service's alias): GET /j15/search answers results only for the words "engineer" in
Paris, the relevant posting on their second page alone; every request under /j15/ is logged with
its method and time, and GET /j15/log answers that log (read from inside the container by the
journey: what the site saw, a POST included, which it refuses with 405).

Browser B17 (web/e2e/browser-b17.spec.ts): the same website under the name
members.example-robotics.test is for members only. Every page under /j15/ there answers only to
a member's cookie, else a 302 to /j15/login?next=<path>; its sign in page (b17/login.html) sends
a name by GET to /j15/session, which sets the cookie (30 days, HttpOnly) and goes back to `next`.
GET /b17/log answers what that website saw (method, path, time, whether a member's cookie came
with it), and GET /b17/expire ends every login made so far (the cookie names an epoch), so the
next search meets the sign in page again.
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
J15_LOG = []
J15_LOCK = threading.Lock()
MEMBERS_HOST = "members.example-robotics.test"
MEMBER_NAME = re.compile(r"[a-z0-9@._-]{1,64}")
MEMBERS = {"epoch": 1}
B17_LOG = []
J15_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
    "<body><main><h1>{title}</h1>{body}</main></body></html>"
)


def j15_search(query) -> str:
    """The results of the invented job website: none unless the words and the location are the
    journey's, the posting on page 2 only."""
    words = query.get("q", [""])[0]
    place = query.get("location", [""])[0]
    page = query.get("page", ["1"])[0]
    if (words, place) != ("engineer", "Paris"):
        return J15_PAGE.format(
            title="Search results", body="<p>No jobs match this search.</p>"
        )
    if page == "2":
        body = (
            '<ul><li><a href="/j15/jobs/robot-engineer.html">Robot Engineer</a></li></ul>'
            "<p>Page 2 of 2</p>"
        )
    else:
        body = (
            "<p>Robotics Intern, Paris: this role is filled.</p>"
            '<p>Page 1 of 2</p><a href="/j15/search?q=engineer&amp;location=Paris&amp;page=2">'
            "Next page</a>"
        )
    return J15_PAGE.format(title="Search results", body=body)


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

    def members(self) -> bool:
        """The request is for the members only website (B17)."""
        return self.headers.get("Host", "").split(":")[0].lower() == MEMBERS_HOST

    def member(self) -> bool:
        """A member's cookie of the current epoch came with the request."""
        jar = {}
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            jar[name] = value
        who, _, epoch = jar.get("b17_member", "").rpartition(".")
        return bool(MEMBER_NAME.fullmatch(who)) and epoch == str(MEMBERS["epoch"])

    def b17(self, url) -> bool:
        """The members only website: answered here (True), or served as J15's for a member."""
        signed = self.member()
        with J15_LOCK:
            B17_LOG.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "at": time.time(),
                    "member": signed,
                }
            )
        query = urllib.parse.parse_qs(url.query)
        back = query.get("next", ["/j15/"])[0]
        if not back.startswith("/j15/") or back.startswith("//"):
            back = "/j15/"
        if url.path == "/j15/login":
            self.path = "/b17/login.html"
            super().do_GET()
            return True
        if url.path == "/j15/session":
            who = query.get("name", [""])[0].strip()
            if not MEMBER_NAME.fullmatch(who):
                self.redirect("/j15/login?next=" + urllib.parse.quote(back, safe="/"))
                return True
            cookie = f"b17_member={who}.{MEMBERS['epoch']}; Max-Age=2592000; Path=/; HttpOnly"
            self.redirect(back, cookie)
            return True
        if not signed:
            self.redirect("/j15/login?next=" + urllib.parse.quote(self.path, safe="/"))
            return True
        return False

    def redirect(self, to: str, cookie: str | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def j15_note(self, path: str) -> None:
        if self.members():
            if path.startswith("/j15/"):
                with J15_LOCK:
                    B17_LOG.append(
                        {"method": self.command, "path": self.path, "at": time.time()}
                    )
            return
        if path.startswith("/j15/") and path != "/j15/log":
            with J15_LOCK:
                J15_LOG.append(
                    {"method": self.command, "path": self.path, "at": time.time()}
                )

    def refuse(self):
        self.j15_note(urllib.parse.urlsplit(self.path).path)
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_PATCH = do_DELETE = refuse

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/b17/log":
            with J15_LOCK:
                return self.send(json.dumps(B17_LOG))
        if url.path == "/b17/expire":
            MEMBERS["epoch"] += 1
            return self.send(json.dumps(MEMBERS))
        if self.members() and url.path.startswith("/j15/") and self.b17(url):
            return None
        if not self.members():
            self.j15_note(url.path)
        if url.path == "/j15/log":
            with J15_LOCK:
                return self.send(json.dumps(J15_LOG))
        if url.path == "/j15/search":
            return self.send(j15_search(query), "text/html; charset=utf-8")
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
