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
next search meets the sign in page again. Its member pages (the same J15 pages, JavaScript on
in a signed in search) run B17_PROBE_JS: on load it tries a fetch POST, a sendBeacon, a keepalive
POST, a WebSocket, a WebSocketStream, a Worker and an image at /j15/logout, each on its own path,
then asks GET /j15/b17-ran?<what each attempt did>, and adds a hidden inline sandboxed frame whose
script tries a POST, a WebSocket and a WebSocketStream at /j15/b17-sbx-*; the log tells the
journey which of them reached the site. Nothing it adds shows in the page's snapshot. J15's own
pages, on its own name, stay script free.

Browser B19 (api/tests/browser/test_b19_native.py), real job websites searched through their
own search boxes, each on the `.test` stand in names of its platform's family (reserved, RFC
2606): `www.indeed.test` sends `/` to its country site `fr.indeed.test` with a query, which has
a native GET form with Indeed's own field names (b19/indeed.html), result lists at `/jobs`,
postings at `/viewjob?jk=<key>` and a link whose page sends the browser to the sign in host
`secure.indeed.test`; `www.welcometothejungle.test/fr/jobs` (b19/jungle.html) has a script
search box outside any form whose Search button and Enter POST to the search service
`abc-dsn.algolia.test` (answered here with CORS) and render posting links with a tracking query,
an Apply button and an Easy Apply link beside it. Results are listed only for the words "genai
engineer" (any case). GET /b19/log answers what those hosts saw (method, host, path).

Browser B10 on a job website (web/e2e/browser-b10.spec.ts): a LinkedIn search of the job websites
opens LinkedIn's start page here (b8/linkedin/, BROWSER_SOURCES_BASE_URL). GET
/b10/next?wall=checkpoint or ?wall=signin, asked from inside the container only (the browser's
request is refused), makes the next request of that start page, and only that one, answer
LinkedIn's security check (b10/linkedin/checkpoint/challenge/) or its sign in page in place (a
website search refuses every redirect); any other value clears it. Every other request of the
start page answers as before.
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
# Browser B10 on a job website: the wall LinkedIn's start page shows next, once (see the doc).
LINKEDIN_WALLS = {
    "checkpoint": "b10/linkedin/checkpoint/challenge/index.html",
    "signin": "b10/linkedin/jobs/search/signin.html",
}
LINKEDIN_NEXT = {"wall": None}
LINKEDIN_STARTS = ("/b8/linkedin/", "/b10/linkedin/")
B17_LOG = []
# Browser B19 (see the doc): the stand in hosts of the platforms' families, and what they saw.
B19_HOSTS = {
    "www.indeed.test",
    "fr.indeed.test",
    "secure.indeed.test",
    "www.welcometothejungle.test",
    "abc-dsn.algolia.test",
}
B19_LOG = []
B19_JOB = "/fr/companies/example-robotics/jobs/genai-engineer_paris"
B19_KEY = "3f2a9c1d0b8e7a65"


def b19_words(text: str) -> bool:
    return " ".join(text.casefold().split()) == "genai engineer"


J15_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
    "<body><main><h1>{title}</h1>{body}</main></body></html>"
)
# Every way out of a page that B17's signed in search must hold, tried once per member page.
B17_PROBE_JS = """<script>
(() => {
  const tried = {}, kept = (window.__b17Probe = []);  // kept, so no socket is collected unopened
  const attempt = (name, act) => {
    try {
      const out = act();
      kept.push(out);
      tried[name] = typeof out === "boolean" ? String(out) : "made";
    } catch (e) { tried[name] = "err:" + e.name; }
  };
  attempt("post", () => fetch("/j15/b17-post", {method: "POST", body: "x"}).catch(() => 0));
  attempt("beacon", () => navigator.sendBeacon("/j15/b17-beacon", "x"));
  attempt("keepalive", () =>
    fetch("/j15/b17-keepalive", {method: "POST", body: "x", keepalive: true}).catch(() => 0));
  attempt("ws", () => new WebSocket("ws://" + location.host + "/j15/b17-ws"));
  attempt("wss", () => new WebSocketStream("ws://" + location.host + "/j15/b17-wss"));
  attempt("worker", () => new Worker("/j15/b17-worker.js"));
  attempt("img", () => { const img = new Image(); img.src = "/j15/logout"; return img; });
  fetch("/j15/b17-ran?" + new URLSearchParams(tried)).catch(() => 0);
  // An inline sandboxed frame (its own process unless the sweep lease keeps it in the page's),
  // hidden, so the snapshot and its refs stay those of the page: a POST and two sockets.
  const at = "//" + location.host + "/j15/b17-sbx-", http = location.protocol + at;
  const box = document.createElement("div"), frame = document.createElement("iframe");
  box.hidden = true;
  frame.setAttribute("sandbox", "allow-scripts");
  frame.srcdoc = "<script>" + [
    `fetch("${http}post", {method: "POST", body: "x", mode: "no-cors"}).catch(() => 0)`,
    `window.ws = new WebSocket("ws:${at}ws")`,
    `window.wss = new WebSocketStream("ws:${at}wss")`,
  ].map((act) => `try { ${act}; } catch (e) {}`).join(" ") + "</" + "script>";
  box.append(frame);
  document.body.append(box);
})();
</script>"""


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

    def member_page(self, url, query) -> str | None:
        """A J15 page as a member sees it on the members only website: with B17_PROBE_JS."""
        if url.path == "/j15/search":
            html = j15_search(query)
        else:
            path = self.translate_path(url.path)
            if os.path.isdir(path):
                path = os.path.join(path, "index.html")
            if not path.endswith(".html") or not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as page:
                html = page.read()
        return html.replace("</body>", B17_PROBE_JS + "</body>", 1)

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

    def b19(self) -> str:
        """The host of a B19 stand in name, else ""; its request is logged."""
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in B19_HOSTS:
            return ""
        with J15_LOCK:
            B19_LOG.append({"method": self.command, "host": host, "path": self.path})
        return host

    def b19_page(self, host: str, url, query) -> None:
        """A page of a B19 stand in host (see the doc)."""
        if host == "www.indeed.test" and url.path == "/":
            return self.redirect("http://fr.indeed.test:8080/?r=us")
        if host == "fr.indeed.test" and url.path == "/":
            self.path = "/b19/indeed.html"
            return super().do_GET()
        if host == "fr.indeed.test" and url.path == "/myjobs":
            return self.redirect(
                "http://secure.indeed.test:8080/auth?continue=%2Fmyjobs"
            )
        if host == "fr.indeed.test" and url.path == "/jobs":
            # A recent search after the login expired: Indeed's sign in host.
            if query.get("from") == ["relogin"]:
                return self.redirect(
                    "http://secure.indeed.test:8080/auth?continue=%2Fjobs"
                )
            found = b19_words(query.get("q", [""])[0])
            body = (
                f'<ul><li><a href="/viewjob?jk={B19_KEY}&amp;from=serp&amp;vjs=3">'
                "GenAI Engineer</a></li></ul>"
                if found
                else "<p>No jobs match this search.</p>"
            )
            return self.send(J15_PAGE.format(title="Jobs", body=body), "text/html")
        if host == "fr.indeed.test" and url.path == "/viewjob":
            if query.get("jk") != [B19_KEY]:
                return self.send(
                    J15_PAGE.format(title="Not found", body=""), "text/html"
                )
            body = (
                "<p>Employer: Example Robotics</p><p>Build agents with customers.</p>"
            )
            return self.send(
                J15_PAGE.format(title="GenAI Engineer", body=body), "text/html"
            )
        if host == "secure.indeed.test" and url.path == "/auth":
            self.path = "/b19/signin.html"
            return super().do_GET()
        if host == "www.welcometothejungle.test" and url.path == "/fr/jobs":
            self.path = "/b19/jungle.html"
            return super().do_GET()
        if host == "www.welcometothejungle.test" and url.path == B19_JOB:
            body = (
                "<p>Employer: Example Robotics</p><p>Build agents with customers.</p>"
            )
            return self.send(
                J15_PAGE.format(title="GenAI Engineer", body=body), "text/html"
            )
        return self.send(J15_PAGE.format(title="Not found", body=""), "text/html")

    def do_POST(self):
        host = self.b19()
        url = urllib.parse.urlsplit(self.path)
        if host == "abc-dsn.algolia.test" and url.path == "/1/indexes/*/queries":
            size = min(int(self.headers.get("Content-Length") or 0), 65536)
            try:
                asked = json.loads(self.rfile.read(size))["requests"][0]["params"]
                words = urllib.parse.parse_qs(asked).get("query", [""])[0]
            except (ValueError, KeyError, IndexError, TypeError):
                words = ""
            hits = (
                [{"title": "GenAI Engineer", "url": f"{B19_JOB}?q=0f3e2a&o=12"}]
                if b19_words(words)
                else []
            )
            data = json.dumps({"results": [{"hits": hits}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self.refuse()

    do_PUT = do_PATCH = do_DELETE = refuse

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        host = self.b19()
        if host:
            return self.b19_page(host, url, query)
        if url.path == "/b19/log":
            with J15_LOCK:
                return self.send(json.dumps(B19_LOG))
        if url.path == "/b17/log":
            with J15_LOCK:
                return self.send(json.dumps(B17_LOG))
        if url.path == "/b17/expire":
            MEMBERS["epoch"] += 1
            return self.send(json.dumps(MEMBERS))
        if url.path == "/b10/next":  # from inside the container only, never the browser
            if self.client_address[0] != "127.0.0.1":
                return self.refuse()
            wall = query.get("wall", [""])[0]
            with J15_LOCK:
                LINKEDIN_NEXT["wall"] = wall if wall in LINKEDIN_WALLS else None
                return self.send(json.dumps(LINKEDIN_NEXT))
        if url.path in LINKEDIN_STARTS:
            with J15_LOCK:
                wall, LINKEDIN_NEXT["wall"] = LINKEDIN_NEXT["wall"], None
            if wall is not None:  # in place: a website search refuses every redirect
                with open(
                    os.path.join(ROOT, LINKEDIN_WALLS[wall]), encoding="utf-8"
                ) as page:
                    return self.send(page.read(), "text/html; charset=utf-8")
        if self.members() and url.path.startswith("/j15/") and self.b17(url):
            return None
        if self.members() and url.path.startswith("/j15/"):
            page = self.member_page(url, query)
            if page is not None:
                return self.send(page, "text/html; charset=utf-8")
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
