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

from app.browser import profiles, removals
from app.browser.client import BrowserdClient, BrowserError, ended_of
from app.browser.leases import DbProfiles, Leases
from app.browser.live_router import OWN_SESSION
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
        ("signin", ""),
        ("look", "https://example.org"),
    ],
)
def test_a_bad_address_is_refused_and_nothing_starts(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str], purpose: str,
    address: str,
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
