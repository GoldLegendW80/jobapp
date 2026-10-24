"""Only native query, filter, GET submission and pagination expose the detail link."""

import http.server
import json
from urllib.parse import parse_qs, urlsplit

OBSERVED = []
READY = False
FORM = """<!doctype html><title>Native jobs</title><main><h1>Open roles</h1>
<form method="get" action="/b13/search">
<label>Query <input type="search" name="q"></label>
<label>Remote <select name="remote"><option value="0">All</option>
<option value="1">Remote only</option></select></label>
<button type="submit">Search</button></form>
<form method="post" action="/apply"><button>Search application</button></form>
<a href="http://elsewhere:8080/escape">Unrelated</a></main>"""
PROBE = """<!doctype html><title>Native probe</title><main>
<form method="get" action="/b13/search"><input type="search" name="q" value="robot">
<button id="search">Search</button></form><div id="neighbor"></div>{script}</main>"""
HANDLER = """<script>
document.querySelector('#search').addEventListener('click', event => {
  event.preventDefault(); location.assign('/b13/custom-action');
});
{shadow}
document.currentScript.remove();
</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global READY
        path = urlsplit(self.path)
        query = parse_qs(path.query)
        OBSERVED.append({"path": path.path, "query": query, "host": self.headers.get("Host")})
        body = FORM
        if path.path == "/observed":
            body = json.dumps(OBSERVED)
        elif path.path in {"/b13/handler", "/b13/shadow"}:
            shadow = (
                "document.querySelector('#neighbor').attachShadow({mode:'open'}).innerHTML = "
                "'<button>Custom action</button>';"
                if path.path.endswith("shadow")
                else ""
            )
            body = PROBE.replace("{script}", HANDLER.replace("{shadow}", shadow))
        elif path.path in {
            "/b13/redirect-off",
            "/b13/redirect-write",
            "/b13/named",
            "/b13/hidden",
            "/b13/default",
            "/b13/base",
        }:
            body = PROBE.replace("{script}", "")
            if path.path.endswith("named"):
                body = body.replace('id="search"', 'id="search" name="q" value="other"')
            elif path.path.endswith("hidden"):
                body = body.replace(
                    "</form>", '<input type="hidden" name="q" value="other"></form>'
                )
            elif path.path.endswith("default"):
                body = body.replace('value="robot"', 'value="other"')
            elif path.path.endswith("base"):
                body = '<base target="_blank">' + body
            else:
                target = "off" if path.path.endswith("off") else "write"
                body = body.replace('action="/b13/search"', f'action="/b13/{target}/search"')
        elif path.path in {"/b13/off/search", "/b13/write/search"}:
            self.send_response(302)
            self.send_header(
                "Location", "http://elsewhere:8080/escape" if "/off/" in path.path else "/remove"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif (
            path.path == "/b13/search"
            and query.get("q", [""])[-1] == "robot"
            and query.get("remote") == ["1"]
        ):
            if query.get("page") == ["2"]:
                READY = True
                body = '<main><a href="/b13/jobs/robot">Robot Engineer</a></main>'
            else:
                body = (
                    '<main><a href="/b13/search?q=robot&amp;remote=1&amp;page=2">'
                    "Next page</a></main>"
                )
        elif path.path == "/b13/jobs/robot":
            body = (
                "<main><h1>Robot Engineer</h1><p>Observed employer and duties</p></main>"
                if READY
                else "<main>No observed search yet</main>"
            )
        data = body.encode()
        self.send_response(200)
        self.send_header(
            "Content-Type", "application/json" if path.path == "/observed" else "text/html"
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        OBSERVED.append({"path": urlsplit(self.path).path, "method": "POST"})
        self.send_response(204)
        self.end_headers()


http.server.HTTPServer(("", 8080), Handler).serve_forever()
