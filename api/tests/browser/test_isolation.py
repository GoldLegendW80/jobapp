"""Browser B11: privacy and isolation over the whole module, with two people, A and B, on a real
Postgres at head, a real Redis and browserd's own lease, live and hand off logic behind an httpx
transport (b3_support.FakeBrowserd).

- The table ROUTES is every route the module mounts (a route added without a row here fails the
  first test), and no path of the module holds an id: the user always comes from the session.
- Signed out, every route answers 401 and nothing of A's.
- B, signed in, tries every route and every tool on A's lease, look session, hand off, ticket,
  later sign in, LinkedIn switch and profile while A holds them: 403 or 404 on each (or B's own
  empty state on a read), A's lease, profile and settings untouched, and none of A's
  identifiers (user id, lease id, lease token, ticket, session cookie) in any of B's answers.
- The canary: after a full path of A (a sign in at a typed address, a look session with its
  ticket and grant, an agent's lease that waits for a sign in, I'm signed in, the errors of
  each route), no cookie, ticket, lease token, typed address or page text is in the logs, in
  Redis, in any table, or in any answer but the one that hands it out.
- A deleted person's directory goes, and only theirs.

What runs in containers is proved beside this file: two uids that cannot read each other's
directory (test_browser_container.py, test_two_profiles_b_cannot_read_a), Caddy's masked log
and the live view through Caddy (web/e2e/browser-b11.spec.ts).
"""

import asyncio
import inspect
import logging
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.browser import later_handoff, linkedin_guard, removals
from app.browser.client import BrowserError
from app.browser.handoff import request_signin
from app.browser.leases import DbProfiles, Leases
from app.browser.tools import LeaseTools, gate_click
from app.main import create_app
from tests.browser.b3_support import IDLE_S, SESSION_MAX_S, Clock, FakeBrowserd, flush, redis_url
from tests.browser.test_b3_api import SECRET_KEY, USER_A, USER_B, no_removals, row, sign_in
from tests.browser.test_b4_live_api import HOST, ORIGIN, auth
from tests.browser.test_b7_api import Volume, queued, volume
from tests.browser.test_b12a_api import HOST as SITE_HOST
from tests.browser.test_b12a_api import Chromium, Site, watch, with_pages
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url", "no_removals", "redis_url", "volume"]

VIEWER = str(uuid.uuid4())
MODULE = "isolation_test"  # a later sign in module of this test only
REF = str(uuid.uuid4())  # A's item in it

# Every route of the module: the body a caller sends to each.
ROUTES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("GET", "/api/browser"): None,
    ("POST", "/api/browser/sessions"): {"purpose": "look"},
    ("DELETE", "/api/browser/sessions/current"): None,
    ("POST", "/api/browser/live/ticket"): {"viewer": VIEWER},
    ("GET", "/api/browser/live/auth"): None,
    ("POST", "/api/browser/takeover"): {"viewer": VIEWER},
    ("POST", "/api/browser/giveback"): {"viewer": VIEWER},
    ("DELETE", "/api/browser/profile"): None,
    ("GET", "/api/browser/linkedin"): None,
    ("PUT", "/api/browser/linkedin"): {"enabled": False},
    ("POST", "/api/browser/handoff/done"): None,
    ("POST", "/api/browser/handoff/cancel"): None,
    ("POST", "/api/browser/handoff/later"): {"module": MODULE, "ref": REF},
    ("POST", "/api/browser/handoff/later/done"): {"module": MODULE, "ref": REF},
    ("POST", "/api/browser/handoff/later/cancel"): {"module": MODULE, "ref": REF},
}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def browserd(clock: Clock) -> FakeBrowserd:
    return FakeBrowserd(clock, "e2e-browserd-" + uuid.uuid4().hex)


@pytest.fixture
def app(migrated_url: str, redis_url: str, browserd: FakeBrowserd, clock: Clock) -> FastAPI:
    flush(redis_url)
    made = create_app(
        make_settings(
            migrated_url,
            secret_key=SECRET_KEY,
            redis_url=redis_url,
            browser_control_secret=browserd.secret,
        )
    )
    made.state.browser_leases = Leases(
        made.state.queue.pool,
        browserd.client(),
        DbProfiles(made.state.sessionmaker),
        idle_s=IDLE_S,
        session_max_s=SESSION_MAX_S,
        clock=clock,
    )
    return made


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


@pytest.fixture
def user_a(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_A)


@pytest.fixture
def user_b(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_B)


@pytest.fixture
def owned(monkeypatch: pytest.MonkeyPatch, user_a: tuple[uuid.UUID, str]) -> list[str]:
    """A later sign in module whose one item is A's: its address for A, nothing for anyone
    else, as a module answers for its own rows. The list holds what `signed_in` was asked."""
    asked: list[str] = []

    def address(_db: Session, user_id: uuid.UUID, ref: uuid.UUID) -> str | None:
        return f"https://{SITE_HOST}/login" if (user_id, str(ref)) == (user_a[0], REF) else None

    def signed_in(_db: Session, user_id: uuid.UUID, ref: uuid.UUID, _at: Any) -> bool:
        asked.append(str(user_id))
        return False

    handler = later_handoff.Handler(address, signed_in, "e2e_isolation_job", str)
    monkeypatch.setitem(later_handoff.HANDLERS, MODULE, handler)
    return asked


def send(api: Api, method: str, path: str, token: str | None, **kw: Any) -> httpx.Response:
    body = ROUTES[(method, path)]
    ticket = kw.pop("ticket", "x" * 43)
    if path == "/api/browser/live/auth":
        headers = {"Host": HOST, "Origin": ORIGIN, "X-Forwarded-Method": "GET"}
        headers["X-Forwarded-Uri"] = f"/vnc/websockify?ticket={ticket}"
        return api.get(path, token=token, csrf=False, headers=headers)
    return api.request(method, path, token=token, json=body, **kw)


def seen(answer: httpx.Response) -> str:
    """The whole answer as text: its status line, every header and the body."""
    head = "\n".join(f"{k}: {v}" for k, v in answer.headers.items())
    return f"{answer.status_code}\n{head}\n{answer.text}"


def none_of(texts: list[str], secrets_: dict[str, str]) -> None:
    for text in texts:
        for name, value in secrets_.items():
            assert value not in text, f"{name} leaked: {text[:300]}"


async def redis_text(app: FastAPI) -> str:
    """Every key and value of the app's Redis, as text."""
    r = await app.state.queue.pool()
    out = []
    async for key in r.scan_iter():
        kind = (await r.type(key)).decode()
        if kind == "string":
            value: Any = await r.get(key)
        elif kind == "zset":
            value = await r.zrange(key, 0, -1, withscores=True)
        elif kind == "list":
            value = await r.lrange(key, 0, -1)
        elif kind == "hash":
            value = await r.hgetall(key)
        elif kind == "set":
            value = await r.smembers(key)
        else:
            value = kind
        out.append(f"{key!r}={value!r}")
    return "\n".join(out)


def tables_text(factory: sessionmaker[Session]) -> str:
    """Every row of every table of the database, as text."""
    with factory() as db:
        names = db.execute(
            sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars()
        return "\n".join(
            str(db.execute(sa.text(f'SELECT t::text FROM "{name}" t')).scalars().all())
            for name in list(names)
        )


def agents_lease(api: Api, app: FastAPI, browserd: FakeBrowserd, user_id: uuid.UUID) -> Any:
    """An agent's lease of the person, as the worker starts it; its id and token."""
    store: Leases = app.state.browser_leases

    async def go() -> Any:
        got = await store.acquire(str(user_id), "apply_fill", True, hosts=[SITE_HOST])
        assert got.lease is not None
        assert got.token is not None
        return got

    return api.client.portal.call(go)  # type: ignore[union-attr]


def waits_for_signin(api: Api, app: FastAPI, browserd: FakeBrowserd, got: Any, user: str) -> None:
    """The agent of that lease asks the person to sign in; its own call ends at once and the
    lease waits for the person (B12a)."""
    store: Leases = app.state.browser_leases
    watch(browserd, got.lease.lease_id)
    tools = LeaseTools(store, store.client, user, got.lease.lease_id, got.token)

    async def go() -> None:
        outcome = await request_signin(tools, "Example Portal", "x", budget_s=0)
        assert outcome.code == "turn_ended"

    api.client.portal.call(go)  # type: ignore[union-attr]


# --- the table ---------------------------------------------------------------------------------


def test_the_table_is_every_route_of_the_module_and_no_path_names_an_id(app: FastAPI) -> None:
    mounted = {
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and (context.path or "").startswith("/api/browser")
        for method in (context.methods or set()) - {"HEAD"}
    }
    assert mounted == set(ROUTES)
    assert not [path for _, path in mounted if "{" in path]
    # A websocket or a mount of the module is no APIRoute: none may slip past the table.
    others = [
        context.path
        for context in iter_route_contexts(app.routes)
        if not isinstance(context.original_route, APIRoute)
        and (context.path or "").startswith("/api/browser")
    ]
    assert others == []


# --- signed out --------------------------------------------------------------------------------


def test_every_route_signed_out_or_with_a_made_up_cookie_is_401_and_shows_nothing(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str], owned: list[str]
) -> None:
    a_id, a = user_a
    with_pages(browserd, Site())
    lease = api.post("/api/browser/sessions", token=a, json={"purpose": "look"}).json()["lease"]
    for method, path in ROUTES:
        for token in (None, secrets.token_urlsafe(32)):
            answer = send(api, method, path, token)
            assert answer.status_code == 401, (method, path, answer.text)
            none_of(
                [seen(answer)],
                {"lease id": lease["id"], "token": browserd.tokens[0], "A": str(a_id)},
            )
    assert browserd.manager.current().lease_id == lease["id"]
    assert owned == []


# --- B against A's own session -----------------------------------------------------------------


def test_b_gets_403_or_404_on_every_route_while_a_holds_a_session(
    api: Api,
    factory: sessionmaker[Session],
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
    owned: list[str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    started = with_pages(browserd, Site())
    on = api.request(
        "PUT",
        "/api/browser/linkedin",
        token=a,
        json={"enabled": True, "query": "Example robotics", "accept_version": 1},
    )
    assert on.status_code == 200, on.text
    assert linkedin_guard.TEXT_VERSION == 1
    later = api.post(
        "/api/browser/handoff/later", token=a, json=ROUTES[("POST", "/api/browser/handoff/later")]
    )
    assert later.status_code == 201, later.text
    lease = later.json()["lease"]
    tickets = [
        api.post("/api/browser/live/ticket", token=a, json={"viewer": VIEWER}).json()["ticket"]
        for _ in range(2)
    ]
    a_profile = row(factory, a_id)
    secret_values = {
        "A's user id": str(a_id),
        "A's lease id": lease["id"],
        "A's lease token": browserd.tokens[0],
        "A's cookie": a,
        "A's ticket": tickets[0],
        "A's other ticket": tickets[1],
        "A's profile": str(a_profile.profile_id),
        "A's search": "Example robotics",
    }

    answers: dict[tuple[str, str], httpx.Response] = {}
    for method, path in ROUTES:
        answers[(method, path)] = send(api, method, path, b, ticket=tickets[0])
    codes = {key: answer.status_code for key, answer in answers.items()}
    assert codes == {
        ("GET", "/api/browser"): 200,
        ("POST", "/api/browser/sessions"): 202,  # B queues behind A, never shares A's lease
        ("DELETE", "/api/browser/sessions/current"): 204,  # B's own place in the queue
        ("POST", "/api/browser/live/ticket"): 403,
        ("GET", "/api/browser/live/auth"): 403,
        ("POST", "/api/browser/takeover"): 403,
        ("POST", "/api/browser/giveback"): 403,
        ("DELETE", "/api/browser/profile"): 204,  # B's own profile: nothing to forget
        ("GET", "/api/browser/linkedin"): 200,
        ("PUT", "/api/browser/linkedin"): 200,  # B's own switch, off
        ("POST", "/api/browser/handoff/done"): 404,
        ("POST", "/api/browser/handoff/cancel"): 404,
        ("POST", "/api/browser/handoff/later"): 404,
        ("POST", "/api/browser/handoff/later/done"): 404,
        ("POST", "/api/browser/handoff/later/cancel"): 404,
    }
    # The reads are B's own: nothing of A's.
    mine = answers[("GET", "/api/browser")].json()
    assert (mine["lease"], mine["holder"], mine["profile"]["exists"]) == (None, "other", False)
    card = answers[("GET", "/api/browser/linkedin")].json()
    assert (card["enabled"], card["accepted"], card["query"]) == (False, False, None)
    # B's DELETE of the session went again: no place left, and still nothing of A's.
    gone = api.request("DELETE", "/api/browser/sessions/current", token=b)
    assert gone.status_code == 404
    none_of([seen(answer) for answer in [*answers.values(), gone]], secret_values)

    # A's session, profile, switch and later sign in are as they were.
    assert browserd.manager.current().lease_id == lease["id"]
    assert not started[0].stopped
    assert row(factory, a_id).profile_id == a_profile.profile_id
    assert row(factory, b_id) is None
    assert api.get("/api/browser/linkedin", token=a).json()["query"] == "Example robotics"
    assert api.get("/api/browser", token=a).json()["lease"]["id"] == lease["id"]
    assert owned == []
    # B's try burnt that ticket (one use, whoever shows it); A's other ticket still opens it.
    assert auth(api, a, tickets[0]).status_code == 403  # type: ignore[attr-defined]
    assert auth(api, a, tickets[1]).status_code == 204  # type: ignore[attr-defined]
    done = api.post(
        "/api/browser/handoff/later/done",
        token=a,
        json=ROUTES[("POST", "/api/browser/handoff/later/done")],
    )
    assert done.status_code == 204
    assert owned == [str(a_id)]


# --- B against an agent's lease of A -----------------------------------------------------------


def test_b_can_neither_end_as_hand_off_nor_drive_as_lease_with_any_tool(
    api: Api,
    app: FastAPI,
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    with_pages(browserd, Site())
    got = agents_lease(api, app, browserd, a_id)
    waits_for_signin(api, app, browserd, got, str(a_id))
    assert browserd.manager.current().state == "waiting_person"

    for path in ("/api/browser/handoff/done", "/api/browser/handoff/cancel"):
        assert api.post(path, token=b).status_code == 404
    for path in ("/api/browser/takeover", "/api/browser/giveback", "/api/browser/live/ticket"):
        assert api.post(path, token=b, json={"viewer": VIEWER}).status_code == 403

    # B's tools naming A's lease, with A's own token even: refused before browserd, every one.
    store: Leases = app.state.browser_leases
    tools = LeaseTools(store, store.client, str(b_id), got.lease.lease_id, got.token)
    every = [
        tools.snapshot(),
        tools.navigate(f"https://{SITE_HOST}/"),
        tools.click("e1"),
        tools.type("e1", "x"),
        tools.select_option("e1", ["x"]),
        tools.check("e1"),
        tools.upload("e1", ["cv.pdf"]),
        tools.press_key("Enter"),
        tools.wait_for(time=0),
        tools.screenshot(),
        tools.box("e1"),
        tools.tabs(),
        tools.call("browser_snapshot"),
        tools.put_file("cv.pdf", b"%PDF-1.4"),
        tools.ask_signin("Example Portal", "x"),
        gate_click(tools, "e1", "x" * 43),
    ]

    # A new public tool of LeaseTools must join the list above, as a new route joins the table.
    tool_names = {
        name
        for name, member in inspect.getmembers(LeaseTools, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    assert tool_names == {
        "snapshot", "navigate", "click", "type", "select_option", "check", "upload", "press_key",
        "wait_for", "screenshot", "box", "tabs", "call", "put_file", "ask_signin",
    }  # fmt: skip

    async def each() -> list[str]:
        refused = []
        for call in every:
            try:
                await call
            except BrowserError as exc:
                refused.append(exc.code)
        return refused

    calls = len(browserd.calls)
    refused = api.client.portal.call(each)  # type: ignore[union-attr]
    assert refused == ["ended"] * len(every)
    assert len(browserd.calls) == calls  # nothing reached browserd
    assert browserd.manager.current().state == "waiting_person"
    shown = api.get("/api/browser", token=b)
    assert shown.json()["lease"] is None
    none_of([seen(shown)], {"lease": got.lease.lease_id, "token": got.token, "A": str(a_id)})
    assert api.get("/api/browser", token=a).json()["lease"]["state"] == "waiting_person"


# --- the canary over a full path of A ----------------------------------------------------------


def test_no_cookie_ticket_token_typed_address_or_page_text_reaches_a_log_redis_a_table_or_an_answer(
    api: Api,
    app: FastAPI,
    factory: sessionmaker[Session],
    browserd: FakeBrowserd,
    clock: Clock,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    a_id, a = user_a
    _, b = user_b
    typed = "cnry" + secrets.token_hex(8)  # in the address A types
    page = "cnry" + secrets.token_hex(8)  # in the page A signs in to
    site = Site()
    started: list[Chromium] = with_pages(browserd, site)
    answers: list[httpx.Response] = []
    handed: list[str] = []
    caplog.set_level(logging.DEBUG)

    def keep(answer: httpx.Response) -> httpx.Response:
        answers.append(answer)
        return answer

    # A signs in to a site at the address A typed, then ends the session.
    address = f"https://{SITE_HOST}/login?next={typed}"
    signin = keep(
        api.post("/api/browser/sessions", token=a, json={"purpose": "signin", "address": address})
    )
    assert signin.status_code == 201, signin.text
    assert started[0].request.address == address  # it reached browserd, and nothing else
    assert keep(api.request("DELETE", "/api/browser/sessions/current", token=a)).status_code == 204

    # A looks at the browser through the live view: a ticket, Caddy's check, the grant.
    assert (
        keep(api.post("/api/browser/sessions", token=a, json={"purpose": "look"})).status_code
        == 201
    )
    issued = api.post("/api/browser/live/ticket", token=a, json={"viewer": VIEWER})
    ticket = issued.json()["ticket"]
    handed.append(ticket)
    granted = auth(api, a, ticket)
    assert granted.status_code == 204  # type: ignore[attr-defined]
    grant = granted.headers["x-live-grant"]  # type: ignore[attr-defined]
    # The errors of A's own routes, each with nothing in it.
    keep(api.post("/api/browser/takeover", token=a, json={"viewer": VIEWER}))  # 409 own session
    keep(api.post("/api/browser/handoff/done", token=a))  # 404
    keep(api.post("/api/browser/sessions", token=a, json={"purpose": "signin", "address": "x"}))
    keep(auth(api, a, ticket))  # type: ignore[arg-type]  # 403: used
    assert keep(api.request("DELETE", "/api/browser/sessions/current", token=a)).status_code == 204

    # An agent's lease of A waits for a sign in; A signs in to the page and says so.
    got = agents_lease(api, app, browserd, a_id)
    waits_for_signin(api, app, browserd, got, str(a_id))
    keep(api.get("/api/browser", token=a))
    site.signed_in = page
    assert keep(api.post("/api/browser/handoff/done", token=a)).status_code == 204
    assert started[-1].browser.contexts[0].pages[0].name == f"Signed in as {page}"
    keep(api.get("/api/browser", token=a))
    browserd.down = True
    keep(api.post("/api/browser/handoff/done", token=a))  # 503
    keep(api.post("/api/browser/sessions", token=a, json={"purpose": "look"}))  # 503
    browserd.down = False
    # B, meanwhile, on every route.
    for method, path in ROUTES:
        keep(send(api, method, path, b))
    clock.advance(SESSION_MAX_S + 1)
    keep(api.get("/api/browser", token=a))

    canaries = {
        "A's cookie": a,
        "B's cookie": b,
        "the ticket": ticket,
        "the grant": grant,
        "the typed address": typed,
        "the page text": page,
        **{f"lease token {n}": token for n, token in enumerate(browserd.tokens)},
    }
    # The sign in, the look and the agent's lease; I'm signed in restarts Chromium on that lease.
    assert len(browserd.tokens) == 3
    none_of([seen(answer) for answer in answers], canaries)
    # caplog.text holds the messages only; the JSON log also writes every extra field.
    none_of([caplog.text, *(repr(vars(record)) for record in caplog.records)], canaries)
    none_of([api.client.portal.call(redis_text, app)], canaries)  # type: ignore[union-attr]
    none_of([tables_text(factory)], canaries)
    assert ticket in issued.text  # the one answer that hands it out, to A
    assert handed == [ticket]


# --- a deleted person --------------------------------------------------------------------------


def test_a_deleted_persons_directory_goes_and_only_theirs(
    api: Api,
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    folders = {}
    for user_id, token, cookie in ((a_id, a, "a"), (b_id, b, "b")):
        assert (
            api.post("/api/browser/sessions", token=token, json={"purpose": "look"}).status_code
            == 201
        )
        assert (
            api.request("DELETE", "/api/browser/sessions/current", token=token).status_code == 204
        )
        found = row(factory, user_id)
        folders[user_id] = volume.own(found.profile_id, found.uid, cookie)
    a_profile = row(factory, a_id)

    with factory() as db:  # the account's deletion, as the administrator does it
        db.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": a_id})
        db.commit()
    assert queued(factory) == [(str(a_profile.profile_id), a_profile.uid)]
    assert asyncio.run(removals.drain_with(factory, browserd.client())) == 1
    assert not folders[a_id].exists()
    assert (folders[b_id] / "Cookies").read_text() == "b"
    assert queued(factory) == []
    assert row(factory, a_id) is None
    assert row(factory, b_id) is not None
    assert api.get("/api/browser", token=a).status_code == 401
    assert api.get("/api/browser", token=b).json()["profile"]["exists"] is True


# --- the profile volume: one service mounts it, no backup holds it -----------------------------
# The sentences of /privacy that say so are web/src/features/public/texts.ts (BROWSER_WHERE,
# BROWSER_DISK); public-pages.test.tsx asserts the page shows them.

VOLUME = "browserprofiles"
ROOT = Path(__file__).resolve().parents[3]
STACK_FILES = [
    ("compose.yml",),
    *(("compose.yml", path.name) for path in sorted(ROOT.glob("compose.*.yml"))),
    *(
        ("compose.yml", str(path.relative_to(ROOT)))
        for path in sorted(ROOT.glob("tests/*.compose.yml"))
    ),
]


def _mounts(service: dict[str, Any]) -> list[tuple[str, str]]:
    """(source, target) of every mount of a service, short or long syntax."""
    found = []
    for entry in service.get("volumes") or []:
        if isinstance(entry, str):
            source, _, rest = entry.partition(":")
            found.append((source, rest.split(":")[0] if rest else source))
        else:
            found.append((str(entry.get("source", "")), str(entry.get("target", ""))))
    return found


@pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="the repository is not mounted")
@pytest.mark.parametrize("files", STACK_FILES, ids=lambda f: "+".join(f))
def test_only_the_browser_mounts_the_profile_volume(files: tuple[str, ...]) -> None:
    services: dict[str, dict[str, Any]] = {}
    for name in files:
        loaded = yaml.safe_load((ROOT / name).read_text(encoding="utf-8")) or {}
        for key, service in (loaded.get("services") or {}).items():
            services.setdefault(key, {}).setdefault("volumes", []).extend(
                (service or {}).get("volumes") or []
            )
            if (service or {}).get("volumes_from"):
                services[key]["volumes_from"] = service["volumes_from"]
    for key, service in services.items():
        mounts = _mounts(service)
        if key == "browser":  # its one mount, whatever the overlay
            assert mounts == [(VOLUME, "/profiles")], (files, mounts)
            continue
        assert not [m for m in mounts if m[0] == VOLUME or m[1].startswith("/profiles")], (
            files,
            key,
        )
        assert not [v for v in service.get("volumes_from") or [] if "browser" in str(v)]


@pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="the repository is not mounted")
def test_the_backup_dumps_the_database_and_names_no_profile_volume() -> None:
    db_sh = (ROOT / "ops/db.sh").read_text(encoding="utf-8")
    dump = db_sh[db_sh.index("do_backup() {") :]
    dump = dump[: dump.index("\n}\n")]
    assert "pg_dump" in dump
    # The nightly line of the crontab runs ops/db.sh backup and nothing else.
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    cron = deploy[deploy.index("cron_line() {") :]
    cron = cron[: cron.index("\n}\n")]
    assert '/ops/db.sh" backup' in cron
    for path in [
        ROOT / "ops/db.sh",
        ROOT / "deploy.sh",
        *sorted((ROOT / ".github/workflows").glob("*.yml")),
    ]:
        text = path.read_text(encoding="utf-8")
        assert VOLUME not in text, path
        assert "/profiles" not in text, path
