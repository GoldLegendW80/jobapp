"""The fake feeds server of opportunities O7: recorded shapes of the three freelance and project
sources the sweep reads, so no test and no journey ever reads Remote OK, We Work Remotely or
Hacker News.

The shapes were written on 2026-10-20 from each source's public documentation (the Remote OK API's
list with its legal notice first, the We Work Remotely RSS item, the Hacker News API's user and
item objects), never copied from an answer; every company, person and address is invented.

Standard library only, so it runs alone in a container of a scratch stack
(tests/feeds-stub.compose.yml): `python feeds_stub.py --port 8098`; the api tests run it in
process (`with FeedsStub() as stub:`). It refuses to start when APP_ENV is production. The app
reaches it through FEEDS_STUB_BASE_URL (refused in production) as {base}/{source}/{the source's own
path}; the first segment of the path names the variant, so a base of {server}/404 answers 404 to
every feed:

  /{variant}/remoteok/api
  /{variant}/weworkremotely/remote-jobs.rss
  /{variant}/hackernews/v0/user/whoishiring.json
  /{variant}/hackernews/v0/item/{id}.json

Variants:
- ok: the recorded feeds (below), dated relative to today so the days window keeps them
- empty: no posting (Remote OK's notice alone, an empty channel, a thread without comments)
- one: one posting per feed
- many: 500 postings per feed (500 comments on the threads)
- huge: 5,000 postings per feed (5,000 comments on the threads)
- 404, 403, 500: that status for every path; 429: always 429 with Retry-After: 1
- 429-once: 429 on the first request of each path in the server's life, then `ok`
- timeout: answers after SLOW_S seconds
- malformed: a body that is not JSON (Remote OK, Hacker News) or not XML (We Work Remotely)
- shape: JSON or XML of another shape (an object, an HTML page, a user without submissions)
- redirect: 302 to the same path under `ok` on this host (followed)
- redirect-off: 302 to the same path under `ok` on another host name of this server, localhost
  (refused: not the source's host)
- redirect-loop: 302 to the same address, again and again (refused past the redirect limit)
- anything else: 404

What `ok` holds (the journeys and the api tests read these):
- Remote OK: a contract "AI Engineer" mission at Quillfeather Example Labs (tags contract), a
  permanent "GenAI Engineer" at Brightmoor Example Co with a stated salary, the "Applied AI
  Engineer" of Larkspur Example Studio that We Work Remotely also lists, a "Cybersecurity AI
  Engineer" contract, a "Security AI Engineer" written with a soft hyphen and a "Threat AI
  Engineer" contract (the never apply rule, read folded as for a board: stored `skipped`,
  never in the deck), a "Sales Lead", and a posting
  whose address is off remoteok.com (refused).
- We Work Remotely: Larkspur's "Applied AI Engineer" again, a contract "Forward Deployed
  Engineer" at Tamberlin Example, an "Ingénieur Cybersécurité" (never in the deck).
- Hacker News: the account's three threads of the month; the freelance thread holds a client's
  "SEEKING FREELANCER" mission with a daily rate, a "SEEKING WORK" post (skipped), a deleted, a
  dead and a reply comment (skipped), a "Security AI engineer" mission (stored `skipped`) and a
  50,000 character post (cut); the hiring thread holds a permanent and a contract role.

GET /_stats answers {path: count of requests}; POST /_reset clears it. GET /healthz answers ok.
"""

import argparse
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from xml.sax.saxutils import escape

SLOW_S = 3.0
TEXT = (
    "<p>We build agents and LLM tools for our customers. You have experience with Python, "
    "evaluation and production systems.</p><p>Remote within Europe.</p>"
)
LEGAL = (
    "API Terms of Service: Please link back to the URL on Remote OK and mention Remote OK as a "
    "source, so we get traffic back from your site. (Invented wording of the fake server.)"
)
HN_THREADS = {"hired": 3003, "freelance": 3002, "hiring": 3001}
HN_TITLES = {
    "hired": "Ask HN: Who wants to be hired? (October 2026)",
    "freelance": "Ask HN: Freelancer? Seeking freelancer? (October 2026)",
    "hiring": "Ask HN: Who is hiring? (October 2026)",
}
LONG = 50_000


def _when(days: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(days=days, hours=1)


# --- Remote OK -------------------------------------------------------------------------------------


def remoteok_item(
    n: int,
    position: str,
    company: str,
    tags: list[str],
    *,
    location: str = "Europe",
    salary: tuple[int, int] = (0, 0),
    url: str | None = None,
    days: int = 1,
) -> dict[str, Any]:
    when = _when(days)
    return {
        "slug": f"remote-{n}",
        "id": str(100_000 + n),
        "epoch": int(when.timestamp()),
        "date": when.isoformat(),
        "company": company,
        "company_logo": "",
        "position": position,
        "tags": tags,
        "description": TEXT,
        "location": location,
        "salary_min": salary[0],
        "salary_max": salary[1],
        "apply_url": f"https://remoteok.com/remote-jobs/{100_000 + n}/apply",
        "url": url or f"https://remoteok.com/remote-jobs/remote-{n}-{100_000 + n}",
    }


def remoteok_ok() -> list[dict[str, Any]]:
    return [
        {"last_updated": int(time.time()), "legal": LEGAL},
        remoteok_item(1, "AI Engineer", "Quillfeather Example Labs", ["ai", "contract"]),
        remoteok_item(
            2, "GenAI Engineer", "Brightmoor Example Co", ["python", "full time"],
            salary=(90_000, 120_000),
        ),
        remoteok_item(3, "Applied AI Engineer", "Larkspur Example Studio", ["ai"]),
        remoteok_item(4, "Cybersecurity AI Engineer", "Example Shield Works", ["contract"]),
        remoteok_item(5, "Sales Lead", "Brightmoor Example Co", ["sales"]),
        remoteok_item(
            6, "AI Engineer, Platform", "Offhost Example", ["ai"],
            url="https://jobs.example.org/remote-6",
        ),
        remoteok_item(7, "Secu\u00adrity AI Engineer", "Example Bastion", ["ai"]),
        remoteok_item(8, "Threat AI Engineer", "Example Watchtower", ["ai", "contract"]),
    ]  # fmt: skip


def remoteok_many(count: int) -> list[dict[str, Any]]:
    return [{"legal": LEGAL}] + [
        remoteok_item(n, f"AI Engineer {n}", f"Example Company {n}", ["ai"])
        for n in range(count)
    ]


# --- We Work Remotely -------------------------------------------------------------------------------


def wwr_item(
    n: int,
    title: str,
    kind: str = "Full-Time",
    category: str = "Programming",
    days: int = 1,
) -> str:
    link = f"https://weworkremotely.com/remote-jobs/example-{n}"
    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<region>Anywhere in the World</region>"
        f"<category>{escape(category)}</category>"
        f"<type>{escape(kind)}</type>"
        f"<description>{escape(TEXT)}</description>"
        f"<pubDate>{format_datetime(_when(days))}</pubDate>"
        f"<guid>{link}</guid><link>{link}</link>"
        "</item>"
    )


def wwr_feed(items: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>We Work Remotely (fake)</title>'
        "<link>https://weworkremotely.com/</link>" + "".join(items) + "</channel></rss>"
    )


def wwr_ok() -> str:
    return wwr_feed(
        [
            wwr_item(1, "Larkspur Example Studio: Applied AI Engineer"),
            wwr_item(
                2, "Tamberlin Example: Forward Deployed Engineer", kind="Contract"
            ),
            wwr_item(3, "Example Rempart: Ingénieur Cybersécurité", kind="Contract"),
        ]
    )


def wwr_many(count: int) -> str:
    return wwr_feed(
        [wwr_item(n, f"Example Company {n}: AI Engineer {n}") for n in range(count)]
    )


# --- Hacker News ------------------------------------------------------------------------------------


def hn_comment(
    item_id: int, parent: int, first_line: str, **extra: Any
) -> dict[str, Any]:
    return {
        "by": f"example{item_id}",
        "id": item_id,
        "parent": parent,
        "text": f"{first_line}<p>{TEXT}</p><p>Write to jobs@example.org</p>",
        "time": int(_when().timestamp()),
        "type": "comment",
        **extra,
    }


def hn_ok() -> dict[int, dict[str, Any]]:
    freelance, hiring = HN_THREADS["freelance"], HN_THREADS["hiring"]
    comments = [
        hn_comment(
            4001, freelance,
            "SEEKING FREELANCER | Nimbletide Example | GenAI engineer for an LLM evaluation "
            "pipeline | Remote, Europe",
        ),
        hn_comment(4002, freelance, "SEEKING WORK | Remote | Python, LLM evaluation"),
        {"id": 4003, "parent": freelance, "deleted": True, "type": "comment", "time": 1},
        hn_comment(4004, freelance, "SEEKING FREELANCER | Deadwood Example | AI engineer",
                   dead=True),
        hn_comment(4005, 4001, "SEEKING FREELANCER | Replying Example | AI engineer"),
        hn_comment(4006, freelance,
                   "SEEKING FREELANCER | Example Guard | Security AI engineer | Remote"),
        hn_comment(4007, freelance, "SEEKING FREELANCER | Longform Example | AI engineer | Remote"),
        hn_comment(5001, hiring, "Orbitwell Example | AI Engineer | Paris or Remote | Full-time"),
        hn_comment(5002, hiring, "Pinecrest Example | Senior GenAI Engineer | London | Contract"),
    ]  # fmt: skip
    comments[0]["text"] += "<p>TJM 600 € for three months.</p>"
    comments[6]["text"] += "<p>" + "x" * LONG + "</p>"
    items: dict[int, dict[str, Any]] = {c["id"]: c for c in comments}
    for name, story in HN_THREADS.items():
        items[story] = {
            "by": "whoishiring",
            "id": story,
            "kids": [c["id"] for c in comments if c.get("parent") == story],
            "time": int(_when(2).timestamp()),
            "title": HN_TITLES[name],
            "type": "story",
        }
    items[HN_THREADS["freelance"]]["kids"].append(
        4005
    )  # a reply listed among kids, ignored
    return items


def hn_many(count: int, freelance_only: bool = False) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for name, story in HN_THREADS.items():
        kids = []
        for n in range(count):
            item_id = story * 100_000 + n
            prefix = "SEEKING FREELANCER | " if name == "freelance" else ""
            items[item_id] = hn_comment(
                item_id,
                story,
                f"{prefix}Example Company {n} | AI Engineer {n} | Remote",
            )
            kids.append(item_id)
        items[story] = {
            "by": "whoishiring",
            "id": story,
            "kids": kids,
            "time": int(_when(2).timestamp()),
            "title": HN_TITLES[name],
            "type": "story",
        }
    return items


def hn_empty() -> dict[int, dict[str, Any]]:
    return {
        story: {"by": "whoishiring", "id": story, "kids": [], "title": HN_TITLES[name],
                "type": "story", "time": int(_when(2).timestamp())}
        for name, story in HN_THREADS.items()
    }  # fmt: skip


# --- the server ------------------------------------------------------------------------------------

COUNTS = {"one": 1, "many": 500, "huge": 5_000}


def remoteok(variant: str) -> Any:
    if variant in ("ok", "429-once"):
        return remoteok_ok()
    if variant == "empty":
        return [{"legal": LEGAL}]
    if variant == "shape":
        return {"legal": LEGAL}
    return remoteok_many(COUNTS[variant])


def weworkremotely(variant: str) -> str:
    if variant in ("ok", "429-once"):
        return wwr_ok()
    if variant == "empty":
        return wwr_feed([])
    if variant == "shape":
        return "<html><body>Not a feed</body></html>"
    return wwr_many(COUNTS[variant])


def hackernews(variant: str, path: str) -> tuple[int, Any]:
    if variant in ("ok", "429-once"):
        items = hn_ok()
    elif variant == "empty":
        items = hn_empty()
    elif variant == "shape":
        items = {}
    else:
        items = hn_many(COUNTS[variant])
    if path == "user/whoishiring.json":
        if variant == "shape":
            return 200, {"id": "whoishiring"}
        return 200, {"id": "whoishiring", "submitted": list(HN_THREADS.values())}
    if path.startswith("item/") and path.endswith(".json"):
        try:
            item_id = int(path[len("item/") : -len(".json")])
        except ValueError:
            return 404, None
        return 200, items.get(item_id)  # null for an unknown item, as the API answers
    return 404, None


VARIANTS = frozenset({"ok", "empty", "one", "many", "huge", "429-once", "shape"})
REDIRECTS = frozenset({"redirect", "redirect-off", "redirect-loop"})
STATUSES = {"404": 404, "403": 403, "500": 500, "429": 429}


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def count(self, path: str) -> int:
        with self.lock:
            self.counts[path] = self.counts.get(path, 0) + 1
            return self.counts[path]


def make_handler(state: State, slow_s: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def _send(
            self,
            status: int,
            body: bytes,
            kind: str,
            headers: dict[str, str] | None = None,
        ):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def do_POST(self) -> None:
            if self.path == "/_reset":
                with state.lock:
                    state.counts.clear()
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._json(200, {"ok": True})
                return
            if path == "/_stats":
                with state.lock:
                    self._json(200, dict(state.counts))
                return
            seen = state.count(path)
            variant, _, rest = path.lstrip("/").partition("/")
            source, _, own = rest.partition("/")
            if variant in REDIRECTS:
                target = f"/ok/{rest}"
                if variant == "redirect-off":
                    port = self.server.server_address[1]
                    target = f"http://localhost:{port}/ok/{rest}"
                elif variant == "redirect-loop":
                    target = path
                self._send(302, b"", "text/plain", {"Location": target})
                return
            if variant in STATUSES:
                headers = {"Retry-After": "1"} if variant == "429" else None
                self._send(STATUSES[variant], b"{}", "application/json", headers)
                return
            if variant == "timeout":
                time.sleep(slow_s)
                variant = "ok"
            if variant == "429-once" and seen == 1:
                self._send(429, b"{}", "application/json", {"Retry-After": "1"})
                return
            if variant == "malformed":
                if source == "weworkremotely":
                    self._send(
                        200, b"<rss><channel><item><title>", "application/rss+xml"
                    )
                else:
                    self._send(200, b"{not json", "application/json")
                return
            if variant not in VARIANTS:
                self._json(404, {"error": "unknown variant"})
                return
            if source == "remoteok" and own == "api":
                self._json(200, remoteok(variant))
            elif source == "weworkremotely" and own == "remote-jobs.rss":
                body = weworkremotely(variant).encode()
                self._send(200, body, "application/rss+xml; charset=utf-8")
            elif source == "hackernews" and own.startswith("v0/"):
                status, value = hackernews(variant, own[len("v0/") :])
                self._json(status, value)
            else:
                self._json(404, {"error": "not found"})

    return Handler


class FeedsStub:
    """The server on 127.0.0.1 (or `host`), in a daemon thread: `with FeedsStub() as stub:`."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        slow_s: float = SLOW_S,
        env: dict[str, str] | None = None,
    ) -> None:
        if (os.environ if env is None else env).get("APP_ENV") == "production":
            raise SystemExit(
                "the fake feeds server refuses to start when APP_ENV is production"
            )
        self.state = State()
        self.server = ThreadingHTTPServer(
            (host, port), make_handler(self.state, slow_s)
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def base(self, variant: str = "ok") -> str:
        """FEEDS_STUB_BASE_URL for one variant."""
        return f"{self.url}/{variant}"

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="fake feeds server for the O7 tests")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--slow", type=float, default=30.0)
    args = parser.parse_args()
    server = FeedsStub(args.host, args.port, args.slow)
    print(f"fake feeds on {server.url}", flush=True)
    server.server.serve_forever()


if __name__ == "__main__":
    main()
