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


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global READY
        path = urlsplit(self.path)
        query = parse_qs(path.query)
        OBSERVED.append({"path": path.path, "query": query})
        body = FORM
        if path.path == "/observed":
            body = json.dumps(OBSERVED)
        elif (
            path.path == "/b13/search"
            and query.get("q") == ["robot"]
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


http.server.HTTPServer(("", 8080), Handler).serve_forever()
