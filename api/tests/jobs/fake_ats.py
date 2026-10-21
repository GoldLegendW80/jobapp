"""A fake ATS server for the sweep tests: the three posting APIs, with every failure mode.

Standard library only, so it also runs alone in a container of the scratch Compose stack
(tests/compose.j5-e2e.yml): `python fake_ats.py --port 8099`. The app reaches it through
ATS_BASE_URL with ATS_BASE_URL_ORG_PREFIX=fake- (test only), as {base}/{ats}/{the ATS's own
path}, for orgs starting "fake-".

Paths: /ashby/posting-api/job-board/{org}, /greenhouse/v1/boards/{org}/jobs,
/lever/v0/postings/{org}. The org decides the answer:
- fake-ok: 3 postings (2 match a typical AI engineer search in Paris, 1 does not)
- fake-ok2: 2 other matching postings
- fake-404: 404; fake-403: 403
- fake-429: 429 (Retry-After: 1) on the first request of each server life, then fake-ok's body
- fake-429-always: always 429; fake-500: always 500; fake-503-once: 503 once, then ok
- fake-timeout: answers after SLOW_S seconds (longer than the client's timeout in tests)
- fake-malformed: 200 with a body that is not JSON; fake-shape: 200 with JSON of the wrong shape
- fake-big: 5,000 postings, about 2 KB of text each, every one matching
- fake-unicode: titles and locations in Japanese, Arabic and with emoji, NUL and accents
- fake-dup: the same postings as fake-ok (duplicates across boards)
- fake-variant: fake-ok's first posting with its URL written differently (http, trailing slash,
  a utm parameter): the same posting by the app's duplicate rules
- fake-security: one matching title that also matches a never_apply rule ("security")
- fake-redirect: 302 to fake-ok on the same server; fake-redirect-away: 302 to another host
- fake-pay (opportunities O3): a posting whose ATS states its pay and work mode (Ashby's
  compensation and workplaceType, Lever's salaryRange and workplaceType; Greenhouse states
  none), a freelance mission whose text states a daily rate, one that states no pay
- fake-pay-bad (opportunities O3, review of PR #498): a posting whose ATS states a pay of NaN and
  Infinity (json.dumps writes both), one permanent role whose only figure is a meal voucher
- fake-b10: the posting that browser B10's journey also finds on its LinkedIn fixture
  (tests/browser_pages/b10/), the same Apply address: one row naming both sources
- anything else: 404
Published dates are relative to today, so the days window keeps them. Every request is counted
(GET /_stats answers {path: count}); POST /_reset clears the counters.
"""

import argparse
import contextlib
import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SLOW_S = 3.0
BIG = 5_000
FILLER = (
    "We build agents and LLM tools for customers. You have experience with Python, evaluation "
    "and production systems. "
) * 18


def _day(offset: int = 0) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


PAY_COMPENSATION = {
    "compensationTierSummary": "€60K to €70K",
    "summaryComponents": [
        {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "EUR",
         "minValue": 60000, "maxValue": 70000},
        {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None,
         "minValue": 0.1, "maxValue": 0.2},
    ],
}  # fmt: skip


def postings(org: str) -> list[dict[str, Any]]:
    """Neutral postings: title, location, url, published (YYYY-MM-DD), text, and the fields one
    ATS adds to its own shape (`ashby`, `lever`)."""
    base = f"https://jobs.example.org/{org}"
    if org in ("fake-ok", "fake-dup", "fake-429", "fake-503-once", "fake-redirect"):
        org_key = "fake-ok"
        base = f"https://jobs.example.org/{org_key}"
        return [
            {"title": "AI Engineer", "location": "Paris, France", "url": f"{base}/1",
             "published": _day(1), "text": "Requirements: Python, agents, LLM evaluation."},
            {"title": "Applied AI Engineer, Agents", "location": "London, United Kingdom",
             "url": f"{base}/2", "published": _day(3), "text": "Build agents with Python."},
            {"title": "Account Executive", "location": "Paris, France", "url": f"{base}/3",
             "published": _day(2), "text": "Sales role."},
        ]  # fmt: skip
    if org == "fake-ok2":
        return [
            {"title": "Forward Deployed Engineer", "location": "Paris", "url": f"{base}/1",
             "published": _day(1), "text": "Agents and customers."},
            {"title": "GenAI Engineer", "location": "Remote, Europe", "url": f"{base}/2",
             "published": _day(40), "text": "Too old for a 30 day window."},
        ]  # fmt: skip
    if org == "fake-variant":
        return [
            {"title": "AI Engineer", "location": "Paris, France",
             "url": "http://jobs.example.org/fake-ok/1/?utm_source=board", "published": _day(1),
             "text": "Requirements: Python, agents, LLM evaluation."},
        ]  # fmt: skip
    if org == "fake-unicode":
        return [
            {"title": "AI Engineer 東京 🤖", "location": "Paris, Île-de-France", "url": f"{base}/1",
             "published": _day(1), "text": "Ingénieur IA, agents. Null\x00byte. مرحبا"},
            {"title": "مهندس AI Engineer", "location": "Paris", "url": f"{base}/2",
             "published": _day(1), "text": "نص عربي"},
        ]  # fmt: skip
    if org == "fake-security":
        return [
            {"title": "AI Engineer, Security Agents", "location": "Paris", "url": f"{base}/1",
             "published": _day(1), "text": "Agents."},
        ]  # fmt: skip
    if org == "fake-pay":
        return [
            {"title": "AI Engineer, Pay Stated", "location": "Paris, France", "url": f"{base}/1",
             "published": _day(1), "text": "Build agents with Python and LLM evaluation.",
             "ashby": {"compensation": PAY_COMPENSATION, "workplaceType": "Hybrid"},
             "lever": {"salaryRange": {"min": 50000, "max": 60000, "currency": "GBP",
                                       "interval": "per-year-salary"},
                       "workplaceType": "hybrid"}},
            {"title": "GenAI Engineer, Freelance", "location": "Paris, Télétravail",
             "url": f"{base}/2", "published": _day(1),
             "text": "Mission freelance de 6 mois. TJM 600 € HT. Agents et Python.",
             "lever": {"workplaceType": "unspecified"}},
            {"title": "AI Engineer, Pay Not Stated", "location": "Paris", "url": f"{base}/3",
             "published": _day(2), "text": "Competitive salary, selon profil. Agents."},
        ]  # fmt: skip
    if org == "fake-pay-bad":
        nan, inf = float("nan"), float("inf")
        return [
            {"title": "AI Engineer, Pay Broken", "location": "Paris, France", "url": f"{base}/1",
             "published": _day(1), "text": "Build agents with Python and LLM evaluation.",
             "ashby": {"compensation": {"summaryComponents": [
                 {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "EUR",
                  "minValue": nan, "maxValue": inf}]}, "workplaceType": "Hybrid"},
             "lever": {"salaryRange": {"min": nan, "max": -inf, "currency": "EUR",
                                       "interval": "per-year-salary"},
                       "workplaceType": "on-site"}},
            {"title": "AI Engineer, Meal Vouchers", "location": "Paris", "url": f"{base}/2",
             "published": _day(1),
             "text": "CDI. Tickets restaurant 9 € par jour. Agents et Python.",
             "lever": {"workplaceType": "unspecified"}},
        ]  # fmt: skip
    if org == "fake-b10":
        return [
            {"title": "AI Engineer, Agents", "location": "Paris, France", "url": f"{base}/1",
             "published": _day(1), "text": "Build agents with Python and LLM evaluation."},
        ]  # fmt: skip
    if org == "fake-big":
        return [
            {"title": f"AI Engineer {n}", "location": "Paris, France", "url": f"{base}/{n}",
             "published": _day(n % 20), "text": f"Posting {n}. {FILLER}"}
            for n in range(BIG)
        ]  # fmt: skip
    return []


def body(ats: str, org: str) -> Any:
    items = postings(org)
    if ats == "ashby":
        return {
            "apiVersion": "1",
            "jobs": [
                {"title": p["title"], "location": p["location"], "secondaryLocations": [],
                 "isRemote": "remote" in p["location"].lower(), "isListed": True,
                 "jobUrl": p["url"], "publishedAt": f"{p['published']}T08:00:00.000+00:00",
                 "descriptionPlain": p["text"], **p.get("ashby", {})}
                for p in items
            ],
        }  # fmt: skip
    if ats == "greenhouse":
        return {
            "jobs": [
                {"title": p["title"], "location": {"name": p["location"]},
                 "absolute_url": p["url"], "first_published": f"{p['published']}T08:00:00-04:00",
                 "updated_at": f"{p['published']}T08:00:00-04:00",
                 "content": "&lt;p&gt;" + p["text"].replace("&", "&amp;amp;") + "&lt;/p&gt;"}
                for p in items
            ],
            "meta": {"total": len(items)},
        }  # fmt: skip
    return [
        {"text": p["title"], "categories": {"location": p["location"]}, "hostedUrl": p["url"],
         "createdAt": int(datetime.fromisoformat(p["published"]).replace(hour=12, tzinfo=UTC)
                          .timestamp() * 1000),
         "descriptionPlain": p["text"], "workplaceType": "onsite", **p.get("lever", {})}
        for p in items
    ]  # fmt: skip


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def hit(self, path: str) -> int:
        with self.lock:
            self.counts[path] = self.counts.get(path, 0) + 1
            return self.counts[path]


def parse_path(path: str) -> tuple[str, str] | None:
    parts = path.split("?", 1)[0].strip("/").split("/")
    if len(parts) == 4 and parts[0] == "ashby" and parts[1:3] == ["posting-api", "job-board"]:
        return "ashby", parts[3]
    if len(parts) == 5 and parts[0] == "greenhouse" and parts[1:3] == ["v1", "boards"]:
        return "greenhouse", parts[3]
    if len(parts) == 4 and parts[0] == "lever" and parts[1:3] == ["v0", "postings"]:
        return "lever", parts[3]
    return None


def make_handler(state: State, slow_s: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: Any) -> None:  # quiet
            return

        def send(self, status: int, payload: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload)

        def do_POST(self) -> None:
            if self.path == "/_reset":
                with state.lock:
                    state.counts.clear()
                self.send(200, b"{}")
            else:
                self.send(404, b"{}")

        def do_GET(self) -> None:
            if self.path == "/_stats":
                with state.lock:
                    self.send(200, json.dumps(state.counts).encode())
                return
            if self.path == "/_health":
                self.send(200, b'{"ok":true}')
                return
            found = parse_path(self.path)
            if found is None:
                self.send(404, b'{"error":"not found"}')
                return
            ats, org = found
            n = state.hit(f"{ats}/{org}")
            if org == "fake-404" or (not postings(org) and org not in SPECIAL):
                self.send(404, b'{"error":"not found"}')
            elif org == "fake-403":
                self.send(403, b"{}")
            elif org == "fake-429" and n == 1:
                self.send(429, b"{}", {"Retry-After": "1"})
            elif org == "fake-429-always":
                self.send(429, b"{}", {"Retry-After": "0"})
            elif org == "fake-500":
                self.send(500, b"{}")
            elif org == "fake-503-once" and n == 1:
                self.send(503, b"{}")
            elif org == "fake-timeout":
                time.sleep(slow_s)
                self.send(200, json.dumps(body(ats, "fake-ok")).encode())
            elif org == "fake-malformed":
                self.send(200, b'{"jobs": [ {"title": "AI Engineer", ')
            elif org == "fake-shape":
                self.send(200, b'{"postings": 3}')
            elif org == "fake-redirect":
                target = self.path.replace("fake-redirect", "fake-ok")
                self.send(302, b"", {"Location": target})
            elif org == "fake-redirect-away":
                self.send(302, b"", {"Location": "https://evil.example.net/x"})
            else:
                self.send(200, json.dumps(body(ats, org), ensure_ascii=False).encode())

    return Handler


SPECIAL = {
    "fake-403",
    "fake-429-always",
    "fake-500",
    "fake-timeout",
    "fake-malformed",
    "fake-shape",
    "fake-redirect-away",
}


class FakeAts:
    """The server on 127.0.0.1 (or `host`), in a daemon thread: `with FakeAts() as ats:`."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, slow_s: float = SLOW_S) -> None:
        self.state = State()
        self.server = ThreadingHTTPServer((host, port), make_handler(self.state, slow_s))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeAts":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="fake ATS server for sweep tests")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104, a test container
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--slow", type=float, default=30.0)
    args = parser.parse_args()
    server = FakeAts(args.host, args.port, args.slow)
    print(f"fake ATS on {server.url}", flush=True)
    server.server.serve_forever()


if __name__ == "__main__":
    main()
