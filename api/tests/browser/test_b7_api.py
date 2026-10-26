"""/api/browser for browser B7 on a real Postgres at head, a real Redis and browserd's own
lease logic and profile route behind an httpx transport (b3_support.FakeBrowserd): a person's
own sessions start with no automation and a checked address, "Forget all logins", the drain of
`browser_profile_removals`, and the profile's size written at every lease end.

Two people, A and B: B's removal never touches A's directory, and B can forget nothing of A's.
No test sleeps; the clock is injected. Every account is `e2e-*@example.org`.
"""

import asyncio
import logging
import os
import secrets
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.browser import profile_router, profiles, removals
from app.browser.address import ADDRESS_MAX
from app.browser.client import BrowserdClient, BrowserError, ended_of
from app.browser.leases import DbProfiles, Leases, StoreUnavailableError
from app.browser.live_router import OWN_SESSION
from app.browser.profile_router import FORGET_LIMIT, FORGETS_PER_DAY, forget_limit
from app.browser.router import BAD_ADDRESS
from app.main import create_app
from tests.browser.b3_support import IDLE_S, SESSION_MAX_S, Clock, FakeBrowserd, flush, redis_url
from tests.browser.test_b3_api import SECRET_KEY, USER_A, USER_B, no_removals, row, sign_in
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import signin  # noqa: E402
from browserd.leases import Request  # noqa: E402

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url", "no_removals", "redis_url"]

SIZES = [1234, 5678, 91011, 121314]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def measured() -> list[str]:
    return []


@pytest.fixture
def browserd(clock: Clock, measured: list[str]) -> FakeBrowserd:
    async def measure(request: Request) -> int:
        measured.append(request.profile_id)
        return SIZES[(len(measured) - 1) % len(SIZES)]

    return FakeBrowserd(clock, "e2e-browserd-" + uuid.uuid4().hex, measure)


@pytest.fixture
def app(migrated_url: str, redis_url: str, browserd: FakeBrowserd, clock: Clock) -> FastAPI:
    flush(redis_url)
    made = create_app(make_settings(migrated_url, secret_key=SECRET_KEY, redis_url=redis_url))
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


class Volume:
    """browserd's /profiles in a folder of the test: a directory is owned by the uid `own`
    gives it (the test runs as one user), and a removal runs `rmtree` as that uid would."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.owners: dict[str, int] = {}
        self.path = os.path

    def own(self, profile_id: Any, uid: int, cookie: str) -> Path:
        folder = self.root / str(profile_id)
        folder.mkdir()
        (folder / "Cookies").write_text(cookie)
        self.owners[str(profile_id)] = uid
        return folder

    def lstat(self, path: str) -> os.stat_result:
        real = os.lstat(path)
        fields = list(real)
        fields[4] = self.owners.get(Path(path).name, real.st_uid)
        return os.stat_result(fields)


@pytest.fixture
def volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Volume:
    found = Volume(tmp_path)
    monkeypatch.setattr(signin.c, "PROFILES", str(tmp_path))
    monkeypatch.setattr(signin, "os", found)

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        import shutil

        assert argv[-2] == "rmprofile"
        shutil.rmtree(argv[-1])
        return 0

    monkeypatch.setattr(signin.chromium, "as_uid", as_uid)
    return found


def start(api: Api, token: str, purpose: str = "look", **body: Any) -> Any:
    return api.post("/api/browser/sessions", token=token, json={"purpose": purpose, **body})


def queued(factory: sessionmaker[Session]) -> list[tuple[str, int]]:
    with factory() as db:
        rows = db.execute(sa.text("SELECT profile_id, uid FROM browser_profile_removals")).all()
    return [(str(r.profile_id), r.uid) for r in rows]


def call(api: Api, fn: Any) -> Any:
    return api.client.portal.call(fn)  # type: ignore[union-attr]


# --- a person's own sessions ---------------------------------------------------------------------


def test_both_sessions_start_without_automation_and_signin_opens_the_address(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    looked = start(api, a)
    assert looked.status_code == 201
    assert looked.json()["lease"]["automation"] is False
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    signed = start(api, a, "signin", address=" https://example.org/login?next=/a ")
    assert signed.status_code == 201
    assert signed.json()["lease"]["purpose"] == "signin"
    assert api.get("/api/browser", token=a).json()["lease"]["automation"] is False
    assert [(s.purpose, s.automation, s.address) for s in browserd.starts] == [
        ("takeover", False, None),
        ("signin", False, "https://example.org/login?next=/a"),
    ]


@pytest.mark.parametrize(
    ("purpose", "address"),
    [
        ("signin", "--remote-debugging-port=9222"),
        ("signin", "-a"),
        ("signin", "javascript:alert(1)"),
        ("signin", "file:///etc/passwd"),
        ("signin", "chrome://settings"),
        ("signin", "data:text/html,x"),
        ("signin", "https://"),
        ("signin", "https://" + "a" * 5000 + ".org"),
        ("signin", "https://example.org/" + "a" * (ADDRESS_MAX - 19)),
        ("signin", ""),
        ("look", "https://example.org"),
        ("signin", 123),
        ("signin", 1.5),
        ("signin", True),
        ("signin", ["https://example.org"]),
        ("signin", {"href": "https://example.org"}),
        ("look", 123),
    ],
)
def test_a_bad_address_is_refused_and_nothing_starts(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str], purpose: str,
    address: Any,
) -> None:  # fmt: skip
    _, a = user_a
    answer = start(api, a, purpose, address=address)
    assert answer.status_code == 422
    assert answer.json() == {"detail": BAD_ADDRESS, "code": "bad_address"}
    assert answer.headers["cache-control"] == "no-store"
    assert browserd.starts == []
    assert browserd.calls == []
    shown = api.get("/api/browser", token=a).json()
    assert (shown["lease"], shown["queue"]) == (None, [])


def test_an_address_of_the_cap_is_opened_and_the_schema_says_the_cap(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    address = "https://example.org/" + "a" * (ADDRESS_MAX - 20)
    assert len(address) == ADDRESS_MAX
    assert start(api, a, "signin", address=address).status_code == 201
    assert [s.address for s in browserd.starts] == [address]
    shown = app.openapi()["components"]["schemas"]["SessionIn"]["properties"]["address"]
    assert {"type": "string", "maxLength": ADDRESS_MAX} in shown["anyOf"]


def test_the_typed_address_goes_to_browserd_only(
    app: FastAPI,
    api: Api,
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The canary: the address the person typed is in no log, no Redis key and no answer."""
    _, a = user_a
    caplog.set_level(logging.DEBUG)
    canary = secrets.token_hex(12)
    started = start(api, a, "signin", address=f"https://example.org/{canary}?k={canary}")
    assert started.status_code == 201
    shown = api.get("/api/browser", token=a)
    assert canary not in started.text + shown.text
    assert canary not in "\n".join(r.getMessage() for r in caplog.records)
    assert canary in str(browserd.starts[-1].address)

    async def dump() -> str:
        r = await app.state.queue.pool()
        out = []
        async for key in r.scan_iter():
            kind = (await r.type(key)).decode()
            out.append(f"{key!r}={(await r.get(key)) if kind == 'string' else kind!r}")
        return "\n".join(out)

    assert canary not in call(api, dump)


def test_take_over_and_give_back_on_a_persons_session_answer_409(
    api: Api, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    assert start(api, a).status_code == 201
    viewer = str(uuid.uuid4())
    for path in ("/api/browser/takeover", "/api/browser/giveback"):
        answer = api.post(path, token=a, json={"viewer": viewer})
        assert (answer.status_code, answer.json()) == (409, {"detail": OWN_SESSION})


def test_a_tool_call_on_a_persons_session_is_refused(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, _ = user_a
    store: Leases = app.state.browser_leases

    async def go() -> None:
        got = await store.acquire(str(a_id), "signin", automation=False)
        assert got.lease is not None
        assert got.token
        with pytest.raises(BrowserError) as refused:
            await store.client.tool(got.lease.lease_id, got.token, "browser_snapshot")
        assert refused.value.code == "busy"  # browserd's 409 person_session

    call(api, go)
    assert browserd.calls[-1].endswith("/tools/browser_snapshot")


def test_a_signin_runs_before_an_apply_lease_of_the_same_person(
    app: FastAPI, api: Api, user_a: tuple[uuid.UUID, str], user_b: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    _, b = user_b
    store: Leases = app.state.browser_leases
    assert start(api, b).status_code == 201

    async def apply() -> Any:
        return await store.acquire(str(a_id), "apply_fill", True)

    assert call(api, apply).waiting.position == 1
    waiting = start(api, a, "signin")
    assert waiting.status_code == 202
    assert waiting.json()["queue"]["position"] == 1  # ahead of the apply lease
    assert api.request("DELETE", "/api/browser/sessions/current", token=b).status_code == 204
    assert call(api, apply).waiting is not None  # the sign in comes first
    turn = start(api, a, "signin")
    assert turn.status_code == 201
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    assert call(api, apply).lease.purpose == "apply_fill"


# --- "Forget all logins" -------------------------------------------------------------------------


def test_forget_with_nothing_to_forget_is_204_twice(
    api: Api, factory: sessionmaker[Session], user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    for _ in range(2):
        answer = api.request("DELETE", "/api/browser/profile", token=a)
        assert answer.status_code == 204
        assert answer.headers["cache-control"] == "no-store"
    assert queued(factory) == []


def test_forget_is_capped_per_person_and_utc_day(
    app: FastAPI,
    api: Api,
    clock: Clock,
    factory: sessionmaker[Session],
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    """Each Forget then a lease spends a uid for good: FORGETS_PER_DAY a day per person, the
    next one 429 with nothing ended nor deleted, another person's count apart, a new UTC day a
    new count."""
    a_id, a = user_a
    _, b = user_b
    clock.now = datetime(2026, 10, 19, 23, 0, tzinfo=UTC).timestamp()
    for _ in range(FORGETS_PER_DAY):
        assert start(api, a).status_code == 201
        assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert start(api, a).status_code == 201
    kept = row(factory, a_id)
    refused = api.request("DELETE", "/api/browser/profile", token=a)
    assert refused.status_code == 429
    assert refused.json() == {
        "detail": f"{FORGET_LIMIT} Try again in 60 minutes.",
        "code": "forget_limit",
    }
    assert refused.headers["cache-control"] == "no-store"
    assert api.get("/api/browser", token=a).json()["lease"] is not None
    assert row(factory, a_id).profile_id == kept.profile_id
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    assert start(api, b).status_code == 201
    assert api.request("DELETE", "/api/browser/profile", token=b).status_code == 204
    clock.now = datetime(2026, 10, 20, 0, 0, tzinfo=UTC).timestamp()
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert row(factory, a_id) is None


def test_a_forget_with_nothing_to_forget_spends_none(
    api: Api, factory: sessionmaker[Session], user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    for _ in range(FORGETS_PER_DAY + 2):
        assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert start(api, a).status_code == 201
    assert row(factory, a_id) is not None
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert row(factory, a_id) is None


@pytest.mark.parametrize(
    ("at", "wait"),
    [
        ((0, 0, 0), "Try again in 24 hours."),
        ((0, 0, 1), "Try again in 24 hours."),
        ((12, 30, 0), "Try again in 12 hours."),
        ((22, 0, 0), "Try again in 2 hours."),
        ((22, 0, 1), "Try again in 2 hours."),
        ((22, 59, 59), "Try again in 2 hours."),
        ((23, 0, 0), "Try again in 60 minutes."),
        ((23, 0, 1), "Try again in 60 minutes."),
        ((23, 58, 30), "Try again in 2 minutes."),
        ((23, 59, 0), "Try again in 1 minute."),
        ((23, 59, 59), "Try again in 1 minute."),
    ],
)
def test_the_limit_says_how_long_until_the_next_utc_day(
    at: tuple[int, int, int], wait: str
) -> None:
    now = datetime(2026, 10, 19, *at, tzinfo=UTC).timestamp()
    assert forget_limit(now) == f"{FORGET_LIMIT} {wait}"


def test_the_stated_wait_is_never_shorter_than_the_utc_day_left() -> None:
    """The figure is rounded up, so the person never tries again before the count is new."""
    start = datetime(2026, 10, 19, tzinfo=UTC).timestamp()
    for second in range(0, 86400, 7):
        words = forget_limit(start + second).split()
        figure, unit = int(words[-2]), words[-1]
        stated = figure * (3600 if unit.startswith("hour") else 60)
        left = 86400 - second
        assert left <= stated < left + (3600 if unit.startswith("hour") else 60), second


@pytest.mark.parametrize("failing", ["release", "remove"])
def test_a_forget_that_fails_before_the_row_goes_spends_none(
    app: FastAPI,
    api: Api,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    user_a: tuple[uuid.UUID, str],
    failing: str,
) -> None:
    """Redis lost after the count was taken (503), or Postgres failing on the delete: the
    profile stays and so do the day's FORGETS_PER_DAY Forgets."""
    a_id, a = user_a
    assert start(api, a).status_code == 201
    kept = row(factory, a_id)
    store = app.state.browser_leases

    async def release(*_: Any, **__: Any) -> bool:
        raise StoreUnavailableError

    def remove(*_: Any) -> None:
        raise RuntimeError("e2e-452 the database went away")

    with monkeypatch.context() as patch:
        if failing == "release":
            patch.setattr(store, "release", release)
            assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 503
        else:
            patch.setattr(profile_router.profiles, "remove", remove)
            # The fixed 500 of app.main, answered by its outermost middleware (issue #670).
            assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 500
    assert row(factory, a_id).profile_id == kept.profile_id
    for _ in range(FORGETS_PER_DAY):
        assert start(api, a).status_code in (200, 201)
        assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
        assert row(factory, a_id) is None


def test_two_forgets_at_once_never_both_spend_the_last_one(app: FastAPI, api: Api) -> None:
    store = app.state.browser_leases

    async def at_once() -> list[str | None]:
        return list(await asyncio.gather(*(store.take_forget("e2e-424", 1) for _ in range(8))))

    spent = call(api, at_once)
    assert sum(key is not None for key in spent) == 1
    assert call(api, at_once) == [None] * 8


def test_forget_needs_a_session_and_csrf(api: Api, user_a: tuple[uuid.UUID, str]) -> None:
    assert api.request("DELETE", "/api/browser/profile").status_code == 401
    assert (
        api.request("DELETE", "/api/browser/profile", token=user_a[1], csrf=False).status_code
        == 403
    )


def test_forget_during_a_session_ends_it_and_the_next_lease_starts_empty(
    api: Api,
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    assert start(api, b).status_code == 201
    assert api.request("DELETE", "/api/browser/sessions/current", token=b).status_code == 204
    b_row = row(factory, b_id)
    b_dir = volume.own(b_row.profile_id, b_row.uid, "b")
    assert start(api, a, "signin", address="https://example.org").status_code == 201
    old = row(factory, a_id)
    a_dir = volume.own(old.profile_id, old.uid, "a")
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert browserd.chromiums[-1].stopped
    assert browserd.manager.last_end["reason"] == "released"
    shown = api.get("/api/browser", token=a).json()
    assert shown["lease"] is None
    assert shown["profile"] == {"exists": False, "size_bytes": 0, "last_used_at": None}
    assert row(factory, a_id) is None
    assert not a_dir.exists()  # removed at once, browserd answered
    assert queued(factory) == []
    assert (b_dir / "Cookies").read_text() == "b"
    assert row(factory, b_id) is not None
    # The next lease: a new profile, a new uid, a new empty directory.
    assert start(api, a).status_code == 201
    new = row(factory, a_id)
    assert (new.profile_id, new.uid) != (old.profile_id, old.uid)
    assert browserd.starts[-1].profile_id == str(new.profile_id)
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204


def test_forget_ends_an_agents_lease_too(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    store: Leases = app.state.browser_leases

    async def take() -> None:
        assert (await store.acquire(str(a_id), "apply_fill", True)).lease is not None

    call(api, take)
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert browserd.manager.current() is None
    assert api.get("/api/browser", token=a).json()["lease"] is None


def test_forget_with_browserd_down_queues_the_removal_for_the_cron(
    api: Api,
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    user_a: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    assert start(api, a).status_code == 201
    old = row(factory, a_id)
    a_dir = volume.own(old.profile_id, old.uid, "a")
    browserd.down = True
    assert api.request("DELETE", "/api/browser/profile", token=a).status_code == 204
    assert row(factory, a_id) is None
    assert queued(factory) == [(str(old.profile_id), old.uid)]
    client = browserd.client()

    def drain() -> int:
        return asyncio.run(removals.drain({"sessions": factory, "browserd_client": client}))

    assert drain() == 0  # browserd still down: the row stays
    assert queued(factory) == [(str(old.profile_id), old.uid)]
    browserd.down = False
    # The api's end reaches browserd at the next call: the lease ends there, then the removal.
    assert drain() == 0  # profile_in_use: browserd still holds that lease
    api.get("/api/browser", token=a)
    assert browserd.manager.current() is None
    assert drain() == 1
    assert queued(factory) == []
    assert not a_dir.exists()


def test_redis_down_on_forget_is_503_and_nothing_is_deleted(
    app: FastAPI, api: Api, factory: sessionmaker[Session], user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    assert start(api, a).status_code == 201

    async def down() -> Any:
        raise OSError("redis is down")

    app.state.browser_leases._pool = down
    answer = api.request("DELETE", "/api/browser/profile", token=a)
    assert answer.status_code == 503
    assert row(factory, a_id) is not None
    assert queued(factory) == []


# --- the drain -----------------------------------------------------------------------------------


def test_the_drain_waits_for_the_lease_and_never_touches_another_directory(
    app: FastAPI,
    api: Api,
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    assert start(api, a).status_code == 201
    a_row = row(factory, a_id)
    a_dir = volume.own(a_row.profile_id, a_row.uid, "a")
    # A's row deleted by another path (the account's cascade) while A's lease runs.
    gone = profiles.remove(factory, a_id)
    assert gone is not None
    client = browserd.client()
    assert asyncio.run(removals.drain_with(factory, client)) == 0
    assert queued(factory) == [(gone.profile_id, gone.uid)]
    assert (a_dir / "Cookies").exists()
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    # B's removal, and a row naming A's directory with B's uid: A's directory is not B's.
    assert start(api, b).status_code == 201
    assert api.request("DELETE", "/api/browser/sessions/current", token=b).status_code == 204
    b_row = row(factory, b_id)
    profiles.remove(factory, b_id)
    with factory() as db:
        db.execute(sa.text("DELETE FROM browser_profile_removals WHERE profile_id = :p"),
                   {"p": a_row.profile_id})  # fmt: skip
        db.execute(
            sa.text("INSERT INTO browser_profile_removals (profile_id, uid) VALUES (:p, :u)"),
            {"p": a_row.profile_id, "u": b_row.uid},
        )
        db.commit()
    assert asyncio.run(removals.drain_with(factory, client)) == 1  # B's own, no directory
    assert queued(factory) == [(str(a_row.profile_id), b_row.uid)]  # kept: not B's directory
    assert (a_dir / "Cookies").read_text() == "a"


def queue_rows(factory: sessionmaker[Session], *rows: tuple[str, int]) -> None:
    """Queued a second apart, oldest first, as the trigger would have queued them."""
    with factory() as db:
        for at, (profile, uid) in enumerate(rows):
            db.execute(
                sa.text("INSERT INTO browser_profile_removals (profile_id, uid, queued_at)"
                        " VALUES (:p, :u, :t)"),
                {"p": profile, "u": uid, "t": datetime(2026, 10, 19, 8, 0, at, tzinfo=UTC)},
            )  # fmt: skip
        db.commit()


def test_a_row_browserd_keeps_is_skipped_counted_and_never_holds_back_a_newer_one(
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Oldest first: a directory that is another uid's (kept for good), one browserd cannot
    remove (503 not_removed), then one that goes. With one removal asked per run, the third goes
    on the third run; the two kept rows stay queued and are counted in the log."""
    mismatch, stuck, fine = (str(uuid.uuid4()) for _ in range(3))
    volume.own(mismatch, 20999, "m")
    volume.own(stuck, 20102, "s")
    fine_dir = volume.own(fine, 20103, "f")

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        import shutil

        if not argv[-1].endswith(stuck):
            shutil.rmtree(argv[-1])
        return 0

    monkeypatch.setattr(signin.chromium, "as_uid", as_uid)
    queue_rows(factory, (mismatch, 20101), (stuck, 20102), (fine, 20103))
    client = browserd.client()
    with caplog.at_level(logging.WARNING, logger="app.browser.removals"):
        assert [asyncio.run(removals.drain_with(factory, client, limit=1)) for _ in range(3)] == [
            0, 0, 1
        ]  # fmt: skip
    assert not fine_dir.exists()
    assert sorted(queued(factory)) == sorted([(mismatch, 20101), (stuck, 20102)])
    kept = [r.getMessage() for r in caplog.records if "kept" in r.getMessage()]
    assert kept == ["browser profile removals kept: profile_uid_mismatch 1",
                    "browser profile removals kept: not_removed 1"]  # fmt: skip
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.browser.removals"):
        assert asyncio.run(removals.drain_with(factory, client)) == 0
    assert "not_removed 1, profile_uid_mismatch 1" in caplog.text


def test_a_failing_row_at_the_head_does_not_stop_the_pass(
    browserd: FakeBrowserd,
    factory: sessionmaker[Session],
    volume: Volume,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck, fine = str(uuid.uuid4()), str(uuid.uuid4())
    volume.own(stuck, 20111, "s")
    fine_dir = volume.own(fine, 20112, "f")

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        import shutil

        if not argv[-1].endswith(stuck):
            shutil.rmtree(argv[-1])
        return 1

    monkeypatch.setattr(signin.chromium, "as_uid", as_uid)
    queue_rows(factory, (stuck, 20111), (fine, 20112))
    assert asyncio.run(removals.drain_with(factory, browserd.client())) == 1
    assert queued(factory) == [(stuck, 20111)]
    assert not fine_dir.exists()


def test_the_pass_stops_only_when_browserd_does_not_answer(
    browserd: FakeBrowserd, factory: sessionmaker[Session]
) -> None:
    queue_rows(factory, (str(uuid.uuid4()), 20121), (str(uuid.uuid4()), 20122))
    browserd.down = True
    assert asyncio.run(removals.drain_with(factory, browserd.client())) == 0
    assert len(browserd.calls) == 1  # one call, then the pass waits
    assert len(queued(factory)) == 2


@pytest.mark.parametrize(
    ("status", "payload", "outcome"),
    [(204, None, "removed"), (409, {"error": "profile_in_use"}, "profile_in_use"),
     (409, {"error": "profile_uid_mismatch"}, "profile_uid_mismatch"),
     (503, {"error": "not_removed"}, "not_removed"), (409, {"error": "busy"}, "failed"),
     (400, {"error": "bad_uid"}, "failed"), (500, {"error": "internal"}, "failed"),
     (503, {"error": "other"}, "failed"), (404, None, "failed"), (500, None, "failed")],
)  # fmt: skip
def test_every_answer_about_one_directory_is_an_outcome(
    status: int, payload: Any, outcome: str
) -> None:
    import httpx

    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload) if payload else httpx.Response(status)

    client = BrowserdClient("http://browser:9300", "s", httpx.MockTransport(answer))
    assert asyncio.run(client.remove_profile(str(uuid.uuid4()), 20131)) == outcome


def test_a_refused_secret_stops_the_pass_like_a_browserd_down() -> None:
    import httpx

    client = BrowserdClient(
        "http://browser:9300", "s", httpx.MockTransport(lambda _: httpx.Response(401))
    )
    with pytest.raises(BrowserError):
        asyncio.run(client.remove_profile(str(uuid.uuid4()), 20131))


def test_the_drain_without_a_secret_asks_nothing(factory: sessionmaker[Session]) -> None:
    unconfigured = BrowserdClient("http://browser:9300", None)
    assert asyncio.run(removals.drain_with(factory, unconfigured)) == 0


def test_the_worker_runs_the_drain_every_minute() -> None:
    from app.worker import sweeps_queue

    [found] = [c for c in sweeps_queue().cron_jobs if c.name == "cron:browser_removals"]
    assert found.coroutine is removals.drain


# --- the size at every lease end -----------------------------------------------------------------


def test_the_size_is_written_at_every_end(
    api: Api,
    browserd: FakeBrowserd,
    clock: Clock,
    factory: sessionmaker[Session],
    measured: list[str],
    user_a: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    assert start(api, a).status_code == 201
    clock.advance(60)
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    first = row(factory, a_id)
    assert first.size_bytes == SIZES[0]
    assert first.last_used_at == datetime.fromtimestamp(clock(), UTC)
    assert measured == [str(first.profile_id)]
    # An end browserd decides (its idle timer): the api reads the size in browserd's last_end.
    assert start(api, a).status_code == 201
    clock.advance(IDLE_S + 1)
    asyncio.run(browserd.manager.tick())
    shown = api.get("/api/browser", token=a).json()
    assert shown["lease"] is None
    assert shown["profile"]["size_bytes"] == SIZES[1]
    assert row(factory, a_id).size_bytes == SIZES[1]


def test_a_late_size_never_lands_on_the_profile_made_after_forget(
    app: FastAPI, api: Api, factory: sessionmaker[Session], user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    assert start(api, a).status_code == 201
    old = row(factory, a_id)
    profiles.remove(factory, a_id)
    profiles.ensure(factory, a_id)
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    new = row(factory, a_id)
    assert new.profile_id != old.profile_id
    assert new.size_bytes == 0


def test_the_end_of_browserd_is_read_strictly() -> None:
    assert ended_of({"reason": "memory_guard", "size_bytes": 12}) == ended_of(
        {"reason": "memory_guard", "size_bytes": 12, "x": 1}
    )
    for odd in (None, [], {"reason": "Bad Reason", "size_bytes": -1}, {"size_bytes": True},
                {"reason": "a" * 40, "size_bytes": "12"}):  # fmt: skip
        got = ended_of(odd)
        assert got.reason == "ended"
        assert got.size_bytes is None
