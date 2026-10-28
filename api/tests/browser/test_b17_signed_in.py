"""B17: a job website the person signed in to is read signed in, in their own profile only.

browserd's rules (the signed in scope, the request rule, the sign in address, the cookie store
check), the api's Done and site list over browserd's real routes, isolation between two people,
and the interactive source's signed in scope and expired login.
"""

import asyncio
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from browserd import prep, signin  # noqa: E402
from browserd.tools import discovery  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.browser import site_logins, sources  # noqa: E402
from app.browser.site_router import NOT_SIGNED_IN  # noqa: E402
from tests.browser import b3_support  # noqa: E402
from tests.browser import test_b7_api as b7  # noqa: E402
from tests.browser import test_selected_interactive as selected_module  # noqa: E402
from tests.browser.test_b9_site import ScriptedFinder  # noqa: E402
from tests.browser.test_b13_runtime import BASE, InteractiveTools  # noqa: E402
from tests.identity.api_helpers import Api  # noqa: E402

# The fixtures of the B7 api tests: browserd's real routes over a fake Chromium.
api, app, browserd, clock, measured, volume = (
    b7.api,
    b7.app,
    b7.browserd,
    b7.clock,
    b7.measured,
    b7.volume,
)
engine, factory, migrated_url, no_removals = b7.engine, b7.factory, b7.migrated_url, b7.no_removals
user_a, user_b, redis_url = b7.user_a, b7.user_b, b3_support.redis_url
Volume, row, start = b7.Volume, b7.row, b7.start

SITE = "https://members.example.test/jobs/search"
SCOPE = {
    "start": "https://jobs.example.test/search",
    "titles": ["robot"],
    "places": [],
    "filters": {},
}

# --- browserd: the signed in scope and its request rule --------------------------------------


def test_scope_parse_takes_signed_in_as_a_strict_boolean() -> None:
    assert discovery.Scope.parse(SCOPE).signed_in is False
    assert discovery.Scope.parse({**SCOPE, "signed_in": True}).signed_in is True
    for bad in (1, "true", None, [True]):
        assert discovery.Scope.parse({**SCOPE, "signed_in": bad}) is None
    assert discovery.Scope.parse({**SCOPE, "cookies": True}) is None


SIGNED = discovery.Scope.parse({**SCOPE, "signed_in": True})


@pytest.mark.parametrize(
    ("url", "method", "document", "allowed"),
    [
        ("https://jobs.example.test/search?q=robot", "GET", True, True),
        ("https://jobs.example.test/jobs/42", "GET", True, True),
        ("https://jobs.example.test/jobs/42", "HEAD", True, True),
        ("https://jobs.example.test/search?q=robot", "POST", True, False),
        ("https://jobs.example.test/search?q=other", "GET", True, False),
        ("https://jobs.example.test/login?next=/search", "GET", True, False),
        ("https://jobs.example.test/jobs/42/apply", "GET", True, False),
        ("https://elsewhere.example.test/search?q=robot", "GET", True, False),
        ("https://cdn.example.net/app.js", "GET", False, True),
        ("https://jobs.example.test/api/me", "GET", False, True),
        ("https://jobs.example.test/api/account", "GET", False, True),
        ("https://jobs.example.test/api/search", "POST", False, False),
        ("https://jobs.example.test/api/track", "PUT", False, False),
        ("https://jobs.example.test/api/x", "DELETE", False, False),
        ("https://jobs.example.test/api/x", "PATCH", False, False),
        ("https://jobs.example.test/jobs/42/easyapply", "GET", False, False),
        ("https://api.example.test/jobs/42/candidater", "GET", False, False),
        ("https://api.example.test/v1/postuler", "GET", False, False),
        ("https://api.example.test/v1/submit", "GET", False, False),
        ("https://api.example.test/v1/delete", "GET", False, False),
        ("https://u:p@cdn.example.net/app.js", "GET", False, False),
        ("javascript:alert(1)", "GET", False, False),
        ("https://cdn.example.net/" + "a" * 9000, "GET", False, False),
    ],
)
def test_signed_request_lets_only_reads_leave(url, method, document, allowed) -> None:
    assert discovery.signed_request(url, method, document, SIGNED) is allowed


@pytest.mark.parametrize(
    ("url", "signin"),
    [
        ("https://jobs.example.test/login", True),
        ("https://jobs.example.test/Login?next=/search", True),
        ("https://jobs.example.test/sign-in", True),
        ("https://jobs.example.test/fr/connexion", True),
        ("https://jobs.example.test/auth/callback", True),
        ("https://jobs.example.test/sso/start", True),
        ("https://jobs.example.test/jobs/42", False),
        ("https://jobs.example.test/search?q=login", False),
        ("https://jobs.example.test/", False),
        ("not a url", False),
    ],
)
def test_signin_url_reads_the_path_only(url, signin) -> None:
    assert discovery.signin_url(url) is signin


def get_form(native: bool) -> dict[str, Any]:
    return {
        "tag": "input",
        "type": "search",
        "names": ["search"],
        "credential": False,
        "discovery": {
            "native": native,
            "method": "get",
            "action": "https://jobs.example.test/search",
            "target": "",
            "name": "q",
            "href": "",
            "fields": [{"tag": "input", "type": "search", "name": "q", "value": ""}],
        },
    }


def test_a_page_with_scripts_is_refused_unless_signed_in() -> None:
    anonymous = discovery.Scope.parse(SCOPE)
    args = {"text": "robot"}
    current = SCOPE["start"]
    assert discovery.verdict(get_form(False), "browser_type", current, scope=anonymous, args=args)
    assert (
        discovery.verdict(get_form(True), "browser_type", current, scope=anonymous, args=args)
        is None
    )
    assert (
        discovery.verdict(get_form(False), "browser_type", current, scope=SIGNED, args=args) is None
    )
    # The query is still the frozen one, signed in or not.
    other = {"text": "anything"}
    assert discovery.verdict(get_form(False), "browser_type", current, scope=SIGNED, args=other)


@pytest.mark.parametrize("label", ["Postuler", "Candidater", "Easy Apply", "Apply now", "Contact"])
def test_apply_and_contact_buttons_stay_refused_signed_in(label: str) -> None:
    facts = {
        "tag": "button",
        "type": "submit",
        "names": [label],
        "discovery": {**get_form(False)["discovery"], "name": ""},
    }
    assert discovery.verdict(facts, "browser_click", SCOPE["start"], scope=SIGNED) is not None


# --- browserd: the cookie store check ---------------------------------------------------------

NOW = 1_800_000_000.0


def chrome_time(seconds: float) -> int:
    return int((seconds + prep.CHROME_EPOCH_S) * 1_000_000)


def store(profile: Path, cookies: list[tuple[str, int, float]], where: str) -> None:
    path = profile / where
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB,"
        " has_expires INTEGER, expires_utc INTEGER)"
    )
    for host, has_expires, expires in cookies:
        db.execute(
            "INSERT INTO cookies VALUES (?, 'e2e-name', 'e2e-value-3c1f', x'00', ?, ?)",
            (host, has_expires, chrome_time(expires)),
        )
    db.commit()
    db.close()


@pytest.mark.parametrize(
    ("cookies", "host", "present"),
    [
        ([("members.example.test", 1, NOW + 60)], "members.example.test", True),
        ([(".example.test", 1, NOW + 60)], "members.example.test", True),
        ([(".members.example.test", 0, 0)], "members.example.test", True),  # a session cookie
        ([("members.example.test", 1, NOW - 60)], "members.example.test", False),  # expired
        ([("other.example.test", 1, NOW + 60)], "members.example.test", False),
        ([(".test", 1, NOW + 60)], "members.example.test", False),  # a top level domain
        ([("www.members.example.test", 1, NOW + 60)], "members.example.test", False),
        ([], "members.example.test", False),
    ],
)
def test_site_cookies(tmp_path: Path, cookies, host, present) -> None:
    store(tmp_path, cookies, prep.COOKIE_STORES[0])
    assert prep.site_cookies(str(tmp_path), host, NOW) is present


def test_site_cookies_reads_the_older_store_and_no_store(tmp_path: Path) -> None:
    assert prep.site_cookies(str(tmp_path), "members.example.test", NOW) is False
    store(tmp_path, [("members.example.test", 1, NOW + 60)], prep.COOKIE_STORES[1])
    assert prep.site_cookies(str(tmp_path), "members.example.test", NOW) is True


# --- the api: Done, the list, isolation -------------------------------------------------------


@pytest.fixture
def cookie_reader(monkeypatch: pytest.MonkeyPatch, volume: Volume) -> list[tuple[int, str]]:
    """browserd's prep run in process: the uid it runs as, the host asked."""
    asked: list[tuple[int, str]] = []

    async def uid_output(uid: int, *argv: str, timeout: float = 15) -> tuple[int, str]:  # noqa: ASYNC109
        assert argv[-3] == "sitecookies"
        asked.append((uid, argv[-1]))
        return 0, "1" if prep.site_cookies(argv[-2], argv[-1], NOW) else "0"

    monkeypatch.setattr(signin.chromium, "uid_output", uid_output)
    return asked


def sign_in_on(api: Api, factory: sessionmaker[Session], volume: Volume, user, cookie_host):
    user_id, token = user
    assert start(api, token, "signin", address=SITE).status_code == 201
    made = row(factory, user_id)
    folder = volume.own(made.profile_id, made.uid, "")
    if cookie_host:
        store(folder, [(cookie_host, 1, NOW + 3600)], prep.COOKIE_STORES[0])
    return made


def done(api: Api, token: str, address: str = SITE) -> Any:
    return api.post("/api/browser/sites/done", token=token, json={"address": address})


def test_done_records_the_login_for_that_person_only(
    api: Api, factory, volume: Volume, cookie_reader, user_a, user_b
) -> None:
    a_profile = sign_in_on(api, factory, volume, user_a, "members.example.test")
    answer = done(api, user_a[1])
    assert answer.status_code == 200
    assert answer.json() == {"host": "members.example.test", "state": "signed_in", "detail": None}
    assert answer.headers["cache-control"] == "no-store"
    # The sign in session ended first, then the person's own profile was read as their uid.
    assert api.get("/api/browser", token=user_a[1]).json()["lease"] is None
    assert cookie_reader == [(a_profile.uid, "members.example.test")]
    [site] = api.get("/api/browser/sites", token=user_a[1]).json()["sites"]
    assert (site["host"], site["state"]) == ("members.example.test", "signed_in")
    # B has nothing, and B's Done reads B's own profile, which holds no login of the site.
    assert api.get("/api/browser/sites", token=user_b[1]).json() == {"sites": []}
    b_profile = sign_in_on(api, factory, volume, user_b, None)
    refused = done(api, user_b[1])
    assert refused.json() == {
        "host": "members.example.test",
        "state": "not_signed_in",
        "detail": NOT_SIGNED_IN,
    }
    assert cookie_reader[-1] == (b_profile.uid, "members.example.test")
    assert api.get("/api/browser/sites", token=user_b[1]).json() == {"sites": []}
    assert site_logins.signed_in(factory, user_a[0], "members.example.test")
    assert not site_logins.signed_in(factory, user_b[0], "members.example.test")


def test_done_refuses_a_bad_address_and_needs_a_session(api: Api, user_a) -> None:
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "members.example.test", ""):
        answer = done(api, user_a[1], bad)
        assert answer.status_code == 422
    # The body is closed, like every other request schema: an unknown field is refused.
    extra = {"address": SITE, "profile": "x" * 64}
    assert api.post("/api/browser/sites/done", token=user_a[1], json=extra).status_code == 422
    assert api.post("/api/browser/sites/done", json={"address": SITE}).status_code in (401, 403)


def test_expired_then_forget_all_logins_removes_every_site(
    api: Api, factory, volume: Volume, cookie_reader, user_a
) -> None:
    sign_in_on(api, factory, volume, user_a, ".example.test")
    assert done(api, user_a[1]).json()["state"] == "signed_in"
    logins = site_logins.SiteLogins(factory)
    asyncio.run(logins.expire(str(user_a[0]), "members.example.test"))
    [site] = api.get("/api/browser/sites", token=user_a[1]).json()["sites"]
    assert site["state"] == "expired"
    assert not asyncio.run(logins.signed_in(str(user_a[0]), "members.example.test"))
    assert api.request("DELETE", "/api/browser/profile", token=user_a[1]).status_code == 204
    assert api.get("/api/browser/sites", token=user_a[1]).json() == {"sites": []}
    with factory() as db:
        assert db.execute(sa.text("SELECT count(*) FROM browser_site_logins")).scalar() == 0


def test_a_login_is_recorded_only_on_the_profile_whose_cookies_were_read(
    api: Api, factory, volume: Volume, cookie_reader, user_a, user_b
) -> None:
    """A Forget and a new profile between browserd's check and the write: the new, empty
    profile never gets the login, and nobody's checked profile records for another person."""
    read = sign_in_on(api, factory, volume, user_a, "members.example.test")
    assert api.request("DELETE", "/api/browser/profile", token=user_a[1]).status_code == 204
    from app.browser import profiles

    fresh = profiles.ensure(factory, user_a[0])
    assert fresh.profile_id != str(read.profile_id)
    at = site_logins.datetime.now(site_logins.UTC)
    host = "members.example.test"
    assert not site_logins.record(factory, user_a[0], host, at, str(read.profile_id))
    assert not site_logins.record(factory, user_b[0], host, at, fresh.profile_id)
    assert api.get("/api/browser/sites", token=user_a[1]).json() == {"sites": []}
    assert site_logins.record(factory, user_a[0], host, at, fresh.profile_id)
    assert site_logins.signed_in(factory, user_a[0], host)
    assert not site_logins.signed_in(factory, user_b[0], host)


# --- the interactive source: signed in scope, isolation, expired login --------------------------


class Logins:
    def __init__(self, signed: dict[str, set[str]]) -> None:
        self.signed, self.expired = signed, []

    async def signed_in(self, user_id: str, host: str) -> bool:
        return host in self.signed.get(user_id, set())

    async def expire(self, user_id: str, host: str) -> None:
        self.expired.append((user_id, host))


class ScopeTools(InteractiveTools):
    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.scopes: list[dict[str, Any]] = []
        self.answer = answer

    async def call(self, name, args):
        scope = args["_discovery_scope"]
        self.scopes.append(scope)
        if self.answer is not None:
            self.actions.append(name)
            return self.answer
        plain = {k: v for k, v in scope.items() if k != "signed_in"}
        return await super().call(
            name, {**args, "_discovery_scope": {**plain, "filters": {"remote": "1"}}}
        )


def run_for(user: str, logins: Logins, tools: ScopeTools, monkeypatch) -> list[dict[str, Any]]:
    class Runner(RUNNER):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **{**kwargs, "site_logins": logins})

    monkeypatch.setattr(selected_module.sources, "Runner", Runner)

    async def nothing(_session):
        return finder_ran()

    entries, *_ = asyncio.run(
        selected_module.execute(
            selected_module.selected(), user, ScriptedFinder(nothing), tools=tools
        )
    )
    return entries


def finder_ran() -> Any:
    from app.browser.finder import Ran

    return Ran(input_tokens=0, output_tokens=0)


RUNNER = sources.Runner
HOST = BASE.split("/")[2].split(":")[0]


def test_only_the_person_who_signed_in_reads_the_site_signed_in(monkeypatch) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    logins = Logins({alice: {HOST}})
    tools = ScopeTools()
    run_for(alice, logins, tools, monkeypatch)
    assert tools.scopes
    assert all(s.get("signed_in") is True for s in tools.scopes)
    tools = ScopeTools()
    run_for(bob, logins, tools, monkeypatch)
    assert tools.scopes
    assert all("signed_in" not in s for s in tools.scopes)
    assert logins.expired == []


def test_a_sign_in_page_while_signed_in_ends_platform_signin_and_expires(monkeypatch) -> None:
    alice = str(uuid.uuid4())
    logins = Logins({alice: {HOST}})
    tools = ScopeTools({"ok": False, "error": "signin_required", "message": "x"})
    [entry] = run_for(alice, logins, tools, monkeypatch)
    assert entry["error"] == "platform_signin"
    assert logins.expired == [(alice, HOST)]
    assert tools.actions == ["browser_navigate"]


def test_signin_required_while_anonymous_is_not_an_expiry(monkeypatch) -> None:
    bob = str(uuid.uuid4())
    logins = Logins({})
    tools = ScopeTools({"ok": False, "error": "signin_required", "message": "x"})
    [entry] = run_for(bob, logins, tools, monkeypatch)
    assert entry["error"] != "platform_signin"
    assert logins.expired == []


class SignInPage(ScopeTools):
    """The website answers its sign in page at the search's own address: a heading, no redirect."""

    def __init__(self) -> None:
        super().__init__()
        self.pages[BASE] = '- heading "Sign in"\n- textbox "Email" [ref=e1]'


def test_a_sign_in_heading_while_signed_in_ends_platform_signin_and_expires(monkeypatch) -> None:
    alice = str(uuid.uuid4())
    logins = Logins({alice: {HOST}})
    [entry] = run_for(alice, logins, SignInPage(), monkeypatch)
    assert entry["error"] == "platform_signin"
    assert logins.expired == [(alice, HOST)]


def test_a_sign_in_heading_while_anonymous_is_not_an_expiry(monkeypatch) -> None:
    bob = str(uuid.uuid4())
    logins = Logins({})
    [entry] = run_for(bob, logins, SignInPage(), monkeypatch)
    assert entry["error"] != "platform_signin"
    assert logins.expired == []


def test_the_migration_is_in_step(factory) -> None:
    with factory() as db:
        assert db.execute(sa.text("SELECT count(*) FROM browser_site_logins")).scalar() == 0
