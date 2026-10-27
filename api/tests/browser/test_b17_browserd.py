"""B17 in browserd: the signed in discovery context of a website the person signed in to.

Only the Playwright and CDP boundary is fake (no Chromium here; the journey
web/e2e/browser-b17.spec.ts runs the real one). Before the first navigation the tab bypasses
every service worker, asks Chromium to block WebSockets and gets the script that removes workers and
`navigator.serviceWorker`; any step that
fails ends the discovery with nothing read. The route lets a request leave only by
`discovery.signed_request`, and a tab sent to a sign in page is told `signin_required`.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import chromium  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import Request  # noqa: E402
from browserd.tools import actions, discovery, pacing  # noqa: E402

START = "https://jobs.example.test/search"
SCOPE = discovery.Scope.parse(
    {"start": START, "titles": ["robot"], "places": [], "filters": {}, "signed_in": True}
)


class Cdp:
    def __init__(self, fail: str | None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.fail = fail

    async def send(self, method: str, params: Any = None) -> dict[str, Any]:
        if method == self.fail:
            raise RuntimeError("Protocol error")
        self.sent.append((method, params))
        return {}


class Page:
    def __init__(self, url: str = actions.ABOUT_BLANK) -> None:
        self.url, self.closed = url, False

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self, pages: list[Page], fail: str | None = None) -> None:
        self.pages = pages
        self.order: list[str] = []
        self.routes: list[Any] = []
        self.scripts: list[str] = []
        self.listeners: dict[str, list[Any]] = {}
        self.cdp = Cdp(fail)
        self.closed = False

    async def add_init_script(self, script: str) -> None:
        self.order.append("script")
        self.scripts.append(script)

    async def route(self, pattern: str, handler: Any) -> None:
        self.order.append("route")
        self.routes.append((pattern, handler))

    def on(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def new_cdp_session(self, page: Page) -> Cdp:
        self.order.append("cdp")
        return self.cdp

    async def close(self) -> None:  # the person's own context is never closed by a search
        self.closed = True


def tab_on(context: Context) -> actions.Tab:
    request = Request.parse(
        {
            "user_id": "e2e-b17",
            "profile_id": "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b17",
            "uid": 20017,
            "purpose": "sweep",
            "hosts": ["jobs.example.test"],
        }
    )
    browser = chromium.Chromium(request, "b17-test", None, Config(secret=b"s"))
    browser.browser = SimpleNamespace(contexts=[context])

    async def no_sleep(_: float) -> None:
        raise AssertionError("no pacing here")

    return actions.Tab(
        request=request,
        state=actions.State(),
        browser=browser,
        pacer=pacing.Pacer(no_sleep),
        lease_tag="b17-test",
    )


def begin(context: Context) -> actions.Tab:
    tab = tab_on(context)
    asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    return tab


def test_the_persons_context_bypasses_service_workers_and_removes_sockets_first() -> None:
    first, other = Page(), Page()
    context = Context([first, other])
    tab = begin(context)
    assert tab.state.signed_in
    assert tab.state.discovery
    assert tab.state.native_context is context
    assert tab.state.page is first
    assert other.closed
    assert not first.closed
    assert not context.closed
    assert context.scripts == [actions.SIGNED_IN_JS]
    assert context.order == ["script", "route", "cdp"]  # all before the first navigation
    assert context.cdp.sent == [
        ("Network.enable", None),
        ("Network.setBypassServiceWorker", {"bypass": True}),
        ("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]}),
    ]
    # Network.setBlockedURLs stops no WebSocket in Chromium 153 (measured: the handshake reached
    # the site with it in place), so the init script is what refuses a socket: every constructor
    # of a worker or a socket, WebSocketStream included (native in 153), is replaced.
    for name in ("Worker", "SharedWorker", "WebSocket", "WebSocketStream", "WebTransport"):
        assert f'"{name}"' in actions.SIGNED_IN_JS
    assert '"register"' in actions.SIGNED_IN_JS
    # A service worker the profile already holds is out of the page's reach: with no
    # navigator.serviceWorker, getRegistration().active.postMessage cannot wake it, and its own
    # fetch (which Playwright's route never sees) never starts. Measured in Chromium 153.
    assert "delete Navigator.prototype.serviceWorker;" in actions.SIGNED_IN_JS


@pytest.mark.parametrize(
    "fail",
    ["Network.enable", "Network.setBypassServiceWorker", "Network.setBlockedURLs"],
)
def test_a_hardening_step_that_fails_ends_the_discovery(fail: str) -> None:
    page = Page()
    context = Context([page], fail=fail)
    tab = tab_on(context)
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    assert tab.state.native_failed
    assert page.closed  # the tab only, never the person's context
    assert not context.closed


def test_a_page_already_open_is_never_upgraded() -> None:
    context = Context([Page("https://jobs.example.test/me")])
    tab = tab_on(context)
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(SCOPE, "browser_navigate", {"url": START}))
    assert context.routes == []
    assert context.cdp.sent == []


class Route:
    def __init__(self) -> None:
        self.outcome: str | None = None

    async def fallback(self) -> None:
        self.outcome = "sent"

    async def abort(self, reason: str) -> None:
        self.outcome = reason


def request(url: str, method: str = "GET", frame: str | None = None) -> Any:
    """A request: `frame` "main" or "sub" for a document, None for anything else."""
    parent = None if frame == "main" else SimpleNamespace()
    return SimpleNamespace(
        url=url,
        method=method,
        is_navigation_request=lambda: frame is not None,
        frame=SimpleNamespace(parent_frame=parent),
    )


@pytest.mark.parametrize(
    ("url", "method", "frame", "sent"),
    [
        (f"{START}?q=robot", "GET", "main", True),
        ("https://jobs.example.test/jobs/42", "GET", "main", True),
        ("https://jobs.example.test/jobs/42", "HEAD", "main", True),
        (f"{START}?q=robot", "POST", "main", False),
        ("https://jobs.example.test/jobs/42/apply", "GET", "main", False),
        ("https://jobs.example.test/login?next=/search", "GET", "main", False),
        ("https://elsewhere.example.test/search?q=robot", "GET", "main", False),
        ("https://jobs.example.test/embed/other", "GET", "sub", True),
        ("https://ads.example.net/frame", "GET", "sub", False),
        ("https://cdn.example.net/app.js", "GET", None, True),
        ("https://jobs.example.test/api/jobs?q=robot", "GET", None, True),
        ("https://jobs.example.test/api/jobs/42/save", "POST", None, False),
        ("https://jobs.example.test/api/jobs/42/easy-apply", "GET", None, False),
        ("https://jobs.example.test/logout", "GET", None, False),
        ("https://jobs.example.test/api/session?action=signout", "GET", None, False),
        ("https://jobs.example.test/api/messages", "PUT", None, False),
        ("https://jobs.example.test/api/beacon", "POST", None, False),
    ],
)
def test_the_route_lets_only_reads_leave(url: str, method: str, frame: str | None, sent: bool):
    tab = begin(Context([Page()]))
    route = Route()
    asyncio.run(tab._signed_route(route, request(url, method, frame)))
    assert route.outcome == ("sent" if sent else "blockedbyclient")


def test_a_sign_in_page_is_told_and_nothing_leaves_after_the_end() -> None:
    tab = begin(Context([Page()]))
    route = Route()
    asyncio.run(tab._signed_route(route, request("https://jobs.example.test/login", "GET", "main")))
    assert route.outcome == "blockedbyclient"
    assert tab.state.signin_seen
    assert tab.state.blocked
    asyncio.run(tab.close_discovery())
    for url in (f"{START}?q=robot", "https://cdn.example.net/app.js"):
        route = Route()
        asyncio.run(tab._signed_route(route, request(url)))
        assert route.outcome == "blockedbyclient"


def test_an_anonymous_scope_never_touches_the_persons_context() -> None:
    page = Page()
    context = Context([page])
    tab = tab_on(context)
    anonymous = discovery.Scope.parse(
        {"start": START, "titles": ["robot"], "places": [], "filters": {}}
    )
    made: list[dict[str, Any]] = []

    async def new_context(**options: Any) -> Any:
        made.append(options)
        raise RuntimeError("no Chromium here")

    tab.browser.browser.new_context = new_context
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(anonymous, "browser_navigate", {"url": START}))
    assert made == [
        {"java_script_enabled": False, "service_workers": "block", "accept_downloads": False}
    ]
    assert context.routes == []
    assert context.scripts == []
    assert not tab.state.signed_in


# What a signed in page may ask without the person: never to sign them out (path or query, any
# spelling), while the reads of a search, their account data and words that only contain the
# letters stay allowed.
@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("https://jobs.example.test/logout", True),
        ("https://jobs.example.test/LogOut", True),
        ("https://jobs.example.test/log-out", True),
        ("https://jobs.example.test/log_out?next=/", True),
        ("https://jobs.example.test/api/v2/sign-out", True),
        ("https://jobs.example.test/users/sign_out", True),
        ("https://jobs.example.test/SignOut.aspx", True),
        ("https://jobs.example.test/logoff", True),
        ("https://jobs.example.test/fr/deconnexion", True),
        ("https://jobs.example.test/fr/se-deconnecter", True),
        ("https://jobs.example.test/de/abmelden", True),
        ("https://jobs.example.test/account/disconnect", True),
        ("https://jobs.example.test/index.php?action=logout", True),
        ("https://jobs.example.test/a?do=sign-out", True),
        ("https://jobs.example.test/a?logout", True),
        ("https://jobs.example.test/a?next=%2Flogout", True),
        ("https://jobs.example.test/jobs/42/apply", True),
        ("https://jobs.example.test/api/jobs/42/save", True),
        ("https://jobs.example.test/api/alerts/7/delete", True),
        ("https://jobs.example.test/checkout", True),
        ("https://jobs.example.test/jobs/42/candidater", True),
        ("https://jobs.example.test/search?q=robot", False),
        ("https://jobs.example.test/api/me", False),
        ("https://jobs.example.test/api/account", False),
        ("https://jobs.example.test/jobs/logistics-engineer", False),
        ("https://jobs.example.test/jobs/log-analyst", False),
        ("https://jobs.example.test/jobs/sign-language-teacher", False),
        ("https://jobs.example.test/blog/out-of-office", False),
        ("https://jobs.example.test/connexions-reseau", False),
        ("https://jobs.example.test/jobs?q=signal+outage", False),
        ("https://jobs.example.test/jobs?location=Logrono", False),
        ("https://cdn.example.net/static/app.4f2a.js", False),
        ("https://jobs.example.test/jobs/supply-chain-lead", False),
        ("https://jobs.example.test/jobs/applied-scientist", False),
    ],
)
def test_a_signed_in_request_never_signs_the_person_out(url: str, refused: bool) -> None:
    assert discovery.destructive(url) is refused


# B17 round 1 (blocker 3): the apply and submit words are read in the query's keys and values
# too, so a GET that applies through its query never leaves a signed in page. The table is made
# from the rule: every spelling of an apply or submit word, in every place of a query, refused;
# the reads of a search and the person's own search words, allowed.
OWN = discovery.Scope.parse(
    {
        "start": START,
        "titles": ["apply engineer"],
        "places": ["Sendai"],
        "filters": {"category": "Sender"},
        "signed_in": True,
    }
)
ACTION_SPELLINGS = (
    "apply",
    "Apply",
    "APPLY",
    "easyApply",
    "easy_apply",
    "easy-apply",
    "EasyApply",
    "EASY_APPLY",
    "easy apply",
    "easyapply",
    "quickApply",
    "oneClickApply",
    "1-click-apply",
    "applyNow",
    "apply_now",
    "ａｐｐｌｙ",  # full width
    "ap​ply",  # a zero width space inside
    "postuler",
    "Postulez",
    "postulér",
    "candidater",
    "candidatez",
    "candidature",
    "candidature-spontanee",
    "bewerben",
    "jetzt-bewerben",
    "Bewerbung",
    "solliciteren",
    "submit",
    "submitApplication",
    "submit_application",
    "Submit Application",
    "Soumettre",
    "soumission",
    "envoyer",
    "send",
    "validerCandidature",
    "confirm_application",
    "finalizeApplication",
    "complete-application",
    "confirmapplication",  # glued: the noun read inside the word
    "finishapplication",
    "completeapplication",
)
ACTION_PLACES = (
    "action={}&id=42",
    "do={}&offre=42",
    "op={}&job=42",
    "id=42&cmd={}",
    "{}=true&id=42",
    "{}=1",
    "{}",
    "a=1;action={}",
    "operationName={}",
    "&".join(f"f{n}=x" for n in range(70)) + "&op={}",  # past detect's 60 names
    "note=" + "x" * 600 + " {}",  # past detect's 500 characters
)


@pytest.mark.parametrize("word", ACTION_SPELLINGS)
def test_an_apply_or_submit_word_anywhere_in_the_query_never_leaves(word: str) -> None:
    for place in ACTION_PLACES:
        for host in ("https://jobs.example.test/index.php", "https://api.example.net/v1/x"):
            url = f"{host}?{place.format(word)}"
            assert discovery.destructive(url, OWN), url
            assert not discovery.signed_request(url, "GET", False, OWN), url


@pytest.mark.parametrize(
    "query",
    [
        "q=applied+scientist",
        "q=supply+chain",
        "category=payments",
        "category=achats",
        "event=page_view",
        "en=view_item_list&tid=G-EXAMPLE",
        "q=robot&location=Paris&page=2&sort=date",
        "keywords=robot&radius=25&remote=true&contract=CDI",
        "sort=updated_at",
        "orderBy=updatedAt&updatedSince=2026-10-01",
        "sort=-updated",
        "saved=true",
        "savedSearchId=7",
        "hasApplied=false",
        "applied=true",
        "status=applied",
        "applicants=10",
        "appliedFilters=remote",
        "include=applications",
        "q=appliance+technician",
        "q=Postal+worker",
        "q=post+office",
        "ns=postings&lang=fr",
        "type=candidat",
        "q=candidate+experience",
        "q=solicitor",
        "q=supplier+quality",
        "filter=happy",
        "ref=applist",
        "fields=category,location,contract",
        "v=4f2a",
        "display=swap",
        "dl=https%3A%2F%2Fjobs.example.test%2Fjobs%2Fapplied-scientist",
        "operationName=JobSearch&variables=%7B%22q%22%3A%22robot%22%7D",
        "",
        # The person's own search words, in any case or spelling, whatever the key.
        "q=apply+engineer",
        "keywords=Apply%20Engineer&page=2",
        "q=apply-engineer",
        "text=APPLY_ENGINEER",
        "category=Sender",
        "city=sendai",
    ],
)
def test_the_reads_of_a_search_and_the_persons_own_words_still_leave(query: str) -> None:
    url = f"https://jobs.example.test/api/jobs?{query}"
    assert not discovery.query_applies(query, OWN), query
    assert not discovery.destructive(url, OWN), url
    assert discovery.signed_request(url, "GET", False, OWN), url


@pytest.mark.parametrize(
    ("query", "refused"),
    [
        ("q=apply", True),  # not the person's words: those are "apply engineer"
        ("q=apply+engineer+now", True),
        ("q=applyengineer", True),
        ("q=apply+engineer&action=apply", True),
        ("apply+engineer=1", True),  # a key is always read
        ("city=Sender+Freiburg", True),
        ("category=Sendai", False),  # a filter's value under another key is still the person's
        ("q=Apply+Engineer", False),
    ],
)
def test_only_a_value_that_is_exactly_the_persons_words_is_skipped(query, refused) -> None:
    assert discovery.query_applies(query, OWN) is refused
    assert discovery.query_applies(query) is True  # no scope: no words of the person's


def test_a_query_that_cannot_be_split_is_refused() -> None:
    query = "&".join(f"f{n}=x" for n in range(101))
    assert discovery.query_applies(query, OWN)
    assert discovery.destructive(f"https://jobs.example.test/api?{query}", OWN)


# The reviewer's inputs of round 1, never deleted: (url, document, may leave).
ROUND1 = [
    ("https://jobs.example.test/index.php?action=apply&id=42", False, False),
    ("https://jobs.example.test/ajax?do=postuler&offre=42", False, False),
    ("https://jobs.example.test/api/jobs/42?easyApply=true", False, False),
    ("https://jobs.example.test/api?op=submitApplication&job=42", False, False),
    ("https://jobs.example.test/search?q=applied+scientist", False, True),
    ("https://jobs.example.test/search?q=supply+chain", False, True),
    ("https://jobs.example.test/search?category=payments", False, True),
    ("https://jobs.example.test/search?event=page_view", False, True),
    # A frame's navigation is held to the same address rule: it never signs the person out.
    ("https://jobs.example.test/logoff", True, False),
    ("https://jobs.example.test/fr/deconnexion", True, False),
    ("https://jobs.example.test/fr/se-deconnecter", True, False),
    ("https://jobs.example.test/de/abmelden", True, False),
    ("https://jobs.example.test/jobs/42", True, True),
    (
        "https://jobs.example.test/search?q=apply+engineer&category=Sender&location=Sendai",
        True,
        True,
    ),
]


@pytest.mark.parametrize(("url", "document", "leaves"), ROUND1)
def test_round1_regressions(url: str, document: bool, leaves: bool) -> None:
    assert discovery.signed_request(url, "GET", document, OWN) is leaves


# B17 round 2 (follow up 1): a server reads `/ap%70ly` as `/apply`, so the path of a request is
# percent decoded once before its words are read, and a `%` still in it after that decode is
# refused (fails closed). A document with any `%` in its path was already refused (read_url).
@pytest.mark.parametrize(
    ("path", "refused"),
    [
        ("/jobs/42/ap%70ly", True),  # the reviewer's two rows of round 2, never deleted
        ("/%61pply", True),
        ("/jobs/42/AP%50LY", True),
        ("/jobs/42/ap%70ly/", True),
        ("/jobs/42/%61%70%70%6c%79", True),
        ("/jobs/42/%61%70%70%6C%79", True),
        ("/jobs%2F42%2Fapply", True),
        ("/jobs/42/apply%3Fsource%3Dlist", True),
        ("/jobs/42/easy%5Fapply", True),
        ("/jobs/42/easy%20apply", True),
        ("/jobs/42/postu%6Cer", True),
        ("/jobs/42/candi%64ater", True),
        ("/offres/42/candidat%75re", True),
        ("/stellen/42/bewer%62en", True),
        ("/vacatures/42/sollicit%65ren", True),
        ("/form/sub%6Dit", True),
        ("/form/env%6Fyer", True),
        ("/api/jobs/42/sa%76e", True),
        ("/api/alerts/7/de%6Cete", True),
        ("/check%6Fut", True),
        ("/log%6Fut", True),
        ("/users/sign%5Fout", True),
        ("/fr/d%C3%A9connexion", True),
        ("/de/ab%6Delden", True),
        ("/jobs/42/ap%E2%80%8Bply", True),  # a zero width space, dropped as the detector does
        ("/jobs/42/ap%C2%ADply", True),  # a soft hyphen
        ("/jobs/42/%EF%BD%81pply", True),  # a full width a
        ("/jobs/42/ap%2570ly", True),  # encoded twice: a `%` is left
        ("/jobs/42/ap%ply", True),  # not an escape: a `%` is left
        ("/static/100%25.png", True),  # declared: an encoded percent sign is refused too
        ("/search/data%20engineer", False),
        ("/search/robot%2Bengineer", False),
        ("/jobs/ing%C3%A9nieur-donn%C3%A9es", False),
        ("/jobs/m%C3%BCnchen", False),
        ("/jobs/%E6%97%A5%E6%9C%AC", False),
        ("/jobs/%D0%B8%D0%BD%D0%B6%D0%B5%D0%BD%D0%B5%D1%80", False),
        ("/jobs/applied%2Dscientist", False),
        ("/jobs/supply%20chain%20lead", False),
        ("/jobs/log%20analyst", False),
        ("/jobs/sign%20language%20teacher", False),
        ("/blog/out%20of%20office", False),
        ("/img/logo%402x.png", False),
        ("/fonts/Inter%20Var.woff2", False),
        ("/jobs/42", False),
    ],
)
def test_a_percent_encoded_path_is_read_as_the_server_reads_it(path: str, refused: bool) -> None:
    for host in ("https://jobs.example.test", "https://cdn.example.net"):
        url = host + path
        assert discovery.destructive(url, OWN) is refused, url
        assert discovery.signed_request(url, "GET", False, OWN) is not refused, url
    if "%" in path:
        assert not discovery.signed_request(f"https://jobs.example.test{path}", "GET", True, OWN)


@pytest.mark.parametrize(
    ("url", "signin"),
    [
        ("https://jobs.example.test/login", True),
        ("https://jobs.example.test/log-in", True),
        ("https://jobs.example.test/users/sign_in", True),
        ("https://jobs.example.test/SignIn?ReturnUrl=%2Fjobs", True),
        ("https://jobs.example.test/logon.aspx", True),
        ("https://jobs.example.test/fr/connexion", True),
        ("https://jobs.example.test/fr/se-connecter", True),
        ("https://sso.example.test/sso/start", True),
        ("https://auth.example.test/auth/realms/x", True),
        ("https://jobs.example.test/search?q=login", False),
        ("https://jobs.example.test/jobs/42", False),
        ("https://jobs.example.test/jobs/logistics", False),
        ("https://jobs.example.test/signals", False),
        ("https://jobs.example.test/", False),
        ("", False),
    ],
)
def test_the_sign_in_address_is_read_from_the_path(url: str, signin: bool) -> None:
    assert discovery.signin_url(url) is signin
