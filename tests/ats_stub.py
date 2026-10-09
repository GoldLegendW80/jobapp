"""A stand-in for the Ashby, Greenhouse and Lever posting APIs, for the CI e2e job only.

The api's board check (api/app/profile/boards.py) calls the real services; in CI the api gets
ATS_BASE_URL=http://atsstub:8080 (tests/ats-stub.compose.yml) and asks this server instead, so a
slow or rate-limited ATS never turns the e2e job red. Local and Jetson runs keep the real calls.

Paths are the real ones under /<ats>: /ashby/posting-api/job-board/<org>,
/greenhouse/v1/boards/<org>/jobs, /lever/v0/postings/<org>. The orgs in BOARDS exist (made up
postings); every other org answers 404. /healthz answers 200 for the Compose healthcheck.

    python3 tests/ats_stub.py [port]        default port 8080, standard library only
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

POSTINGS = [
    {"title": "Applied AI Engineer", "isListed": True, "company_name": "Example AI"},
    {"title": "Research Engineer", "isListed": True, "company_name": "Example AI"},
    {
        "title": "Forward Deployed Engineer",
        "isListed": True,
        "company_name": "Example AI",
    },
]
# (ats, org) pairs that exist; the e2e specs check mistral.ai on Ashby.
BOARDS = {("ashby", "mistral.ai"), ("greenhouse", "anthropic"), ("lever", "example")}

ROUTES = [
    ("ashby", re.compile(r"^/ashby/posting-api/job-board/([^/]+)$")),
    ("greenhouse", re.compile(r"^/greenhouse/v1/boards/([^/]+)/jobs$")),
    ("lever", re.compile(r"^/lever/v0/postings/([^/]+)$")),
]


def answer(path: str) -> tuple[int, Any]:
    """Status and JSON body for a request path (query ignored)."""
    path = urlsplit(path).path
    if path == "/healthz":
        return 200, {"ok": True}
    for ats, pattern in ROUTES:
        match = pattern.match(path)
        if match is None:
            continue
        org = unquote(match.group(1)).lower()
        if (ats, org) not in BOARDS:
            return 404, {"error": "not found"}
        if ats == "lever":
            return 200, [{"text": p["title"]} for p in POSTINGS]
        return 200, {"jobs": POSTINGS}
    return 404, {"error": "unknown path"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = answer(self.path)
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"ats_stub: {format % args}\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
