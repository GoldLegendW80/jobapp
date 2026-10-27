"""/api/browser and the profile rows (browser B3) on a real Postgres at head, a real Redis and
browserd's own lease logic behind an httpx transport (b3_support.FakeBrowserd).

Two people, A and B: B never sees A's lease, its id or its token, and cannot end it.
"""

import logging
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.browser import profiles
from app.browser.leases import DbProfiles, Leases
from app.browser.models import UID_MIN
from app.browser.router import NO_SESSION, NOT_SET_UP, UNAVAILABLE
from app.main import create_app
from tests.browser.b3_support import (
    IDLE_S,
    SESSION_MAX_S,
    Clock,
    FakeBrowserd,
    flush,
    redis_url,
)
from tests.conftest import free_port, make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user
from tests.test_migrations import HEAD, alembic_config, current_revision

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url", "redis_url"]

USER_A = "e2e-browser-a@example.org"
USER_B = "e2e-browser-b@example.org"
# An invented job website: a Sign in of Find jobs opens its address (B17).
SITE = "https://jobs.example.org/"
SECRET_KEY = "e2e-only-not-a-secret-" + "0" * 24


@pytest.fixture(autouse=True)
def no_removals(migrated_url: str) -> Iterator[None]:
    """The queue of removals outlives the users' TRUNCATE (a trigger does not run on it)."""
    yield
    with psycopg.connect(migrated_url.replace("+psycopg", ""), autocommit=True) as conn:
        conn.execute("TRUNCATE browser_profile_removals")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def browserd(clock: Clock) -> FakeBrowserd:
    return FakeBrowserd(clock, "e2e-browserd-" + uuid.uuid4().hex)


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


def sign_in(factory: sessionmaker[Session], api: Api, email: str) -> tuple[uuid.UUID, str]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    return user.id, api.login_token(email)


@pytest.fixture
def user_a(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_A)


@pytest.fixture
def user_b(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_B)


def row(factory: sessionmaker[Session], user_id: uuid.UUID) -> Any:
    with factory() as db:
        return db.execute(
            sa.text("SELECT * FROM browser_profiles WHERE user_id = :u"), {"u": user_id}
        ).one_or_none()


def test_nothing_yet(api: Api, user_a: tuple[uuid.UUID, str]) -> None:
    answer = api.get("/api/browser", token=user_a[1])
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "available": True,
        "profile": {"exists": False, "size_bytes": 0, "last_used_at": None},
        "lease": None,
        "holder": None,
        "queue": [],
    }


def test_a_holds_b_waits_and_never_sees_as_lease(
    api: Api,
    factory: sessionmaker[Session],
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    a_id, a = user_a
    b_id, b = user_b
    started = api.post("/api/browser/sessions", token=a, json={"purpose": "look"})
    assert started.status_code == 201, started.text
    assert started.headers["cache-control"] == "no-store"
    lease = started.json()["lease"]
    assert (lease["purpose"], lease["state"], lease["max_s"]) == ("look", "active", 1800)
    assert browserd.tokens[0] not in started.text
    assert browserd.starts[0].purpose == "takeover"
    assert browserd.starts[0].automation is False

    mine = api.get("/api/browser", token=a).json()
    assert mine["holder"] == "you"
    assert mine["lease"]["id"] == lease["id"]
    assert mine["profile"]["exists"]
    assert mine["profile"]["last_used_at"] is not None

    waiting = api.post("/api/browser/sessions", token=b, json={"purpose": "signin"})
    assert waiting.status_code == 202
    assert waiting.json() == {
        "lease": None,
        "queue": {"purpose": "signin", "position": 1, "estimated_wait_s": 300},
    }
    theirs = api.get("/api/browser", token=b)
    assert theirs.json()["holder"] == "other"
    assert theirs.json()["lease"] is None
    assert theirs.json()["queue"] == [waiting.json()["queue"]]
    for answer in (waiting, theirs):
        assert lease["id"] not in answer.text
        assert str(a_id) not in answer.text
        assert browserd.tokens[0] not in answer.text
    assert row(factory, b_id) is None  # a profile is made when the person's first lease starts

    # B's DELETE takes B out of the queue and leaves A's lease alone.
    assert api.request("DELETE", "/api/browser/sessions/current", token=b).status_code == 204
    gone = api.request("DELETE", "/api/browser/sessions/current", token=b)
    assert (gone.status_code, gone.json()) == (404, {"detail": NO_SESSION})
    assert api.get("/api/browser", token=a).json()["holder"] == "you"

    ended = api.request("DELETE", "/api/browser/sessions/current", token=a)
    assert ended.status_code == 204
    assert ended.headers["cache-control"] == "no-store"
    assert browserd.manager.current() is None
    assert browserd.chromiums[0].stopped
    assert api.get("/api/browser", token=b).json()["holder"] is None


def test_a_session_does_not_end_an_agents_lease(
    app: FastAPI, api: Api, user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    store: Leases = app.state.browser_leases

    async def take() -> None:
        assert (await store.acquire(str(a_id), "apply_fill", True)).lease is not None

    api.client.portal.call(take)  # type: ignore[union-attr]
    shown = api.get("/api/browser", token=a).json()
    assert (shown["lease"]["purpose"], shown["lease"]["application_id"]) == ("apply_fill", None)
    assert shown["holder"] == "you"
    answer = api.request("DELETE", "/api/browser/sessions/current", token=a)
    assert answer.status_code == 404
    assert api.get("/api/browser", token=a).json()["lease"]["purpose"] == "apply_fill"


def test_the_holder_reads_the_application_of_its_submit_lease_and_b_never_does(
    app: FastAPI, api: Api, user_a: tuple[uuid.UUID, str], user_b: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    store: Leases = app.state.browser_leases
    sent = str(uuid.uuid4())

    async def take() -> None:
        got = await store.acquire(str(a_id), "apply_submit", True, application_id=sent)
        assert got.lease is not None

    api.client.portal.call(take)  # type: ignore[union-attr]
    shown = api.get("/api/browser", token=a).json()
    assert (shown["holder"], shown["lease"]["purpose"]) == ("you", "apply_submit")
    assert shown["lease"]["application_id"] == sent
    theirs = api.get("/api/browser", token=user_b[1])
    assert (theirs.json()["holder"], theirs.json()["lease"]) == ("other", None)
    assert sent not in theirs.text


def test_one_profile_for_every_lease_of_a_person(
    api: Api, factory: sessionmaker[Session], browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    for purpose in ("look", "signin", "look"):
        assert (
            api.post("/api/browser/sessions", token=a, json={"purpose": purpose}).status_code == 201
        )
        assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    found = row(factory, a_id)
    assert found is not None
    assert found.uid >= UID_MIN
    assert found.size_bytes == 0
    assert {(s.profile_id, s.uid) for s in browserd.starts} == {(str(found.profile_id), found.uid)}
    assert [s.purpose for s in browserd.starts] == ["takeover", "signin", "takeover"]


def test_browserd_down_is_503_and_get_says_unavailable(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    browserd.down = True
    answer = api.post("/api/browser/sessions", token=user_a[1], json={"purpose": "look"})
    assert (answer.status_code, answer.json()) == (503, {"detail": UNAVAILABLE})
    shown = api.get("/api/browser", token=user_a[1])
    assert shown.status_code == 200
    assert shown.json()["available"] is False


@pytest.mark.parametrize(
    ("broken", "detail", "logged"),
    [
        # Production on 2026-10-27: Sign in answered 503 in a few milliseconds, browserd healthy,
        # and no log line said why. The page now says the browser is not set up, the log says
        # which setting is empty (never a value).
        ("no_secret", NOT_SET_UP, "browserd not configured: BROWSER_CONTROL_SECRET is empty"),
        ("no_redis", UNAVAILABLE, "browser lease store unavailable: "),
    ],
)
@pytest.mark.parametrize("body", [{"purpose": "look"}, {"purpose": "signin", "address": SITE}])
def test_the_settings_path_without_a_secret_or_redis(
    migrated_url: str,
    redis_url: str,
    factory: sessionmaker[Session],
    broken: str,
    detail: str,
    logged: str,
    body: dict[str, str],
) -> None:
    url = redis_url if broken == "no_secret" else f"redis://127.0.0.1:{free_port()}/0"
    made = create_app(make_settings(migrated_url, secret_key=SECRET_KEY, redis_url=url))
    # The app's logging replaces the root's handlers (pytest's too): read the module's records.
    lines = Lines()
    logging.getLogger("app.browser").addHandler(lines)
    try:
        with TestClient(made, follow_redirects=False) as client:
            api = Api(client)
            _, token = sign_in(factory, api, USER_A)
            answer = api.post("/api/browser/sessions", token=token, json=body)
            assert (answer.status_code, answer.json()) == (503, {"detail": detail})
            assert any(line.startswith(logged) for line in lines.lines), lines.lines
            assert not any(SECRET_KEY in line for line in lines.lines)
            shown = api.get("/api/browser", token=token)
            assert shown.status_code == 200
            assert shown.json()["available"] is False
    finally:
        logging.getLogger("app.browser").removeHandler(lines)


class Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_the_routes_need_a_session_csrf_and_a_known_purpose(
    api: Api, user_a: tuple[uuid.UUID, str]
) -> None:
    assert api.get("/api/browser").status_code == 401
    assert api.post("/api/browser/sessions", json={"purpose": "look"}).status_code == 401
    no_csrf = api.post(
        "/api/browser/sessions", token=user_a[1], csrf=False, json={"purpose": "look"}
    )
    assert no_csrf.status_code == 403
    for body in ({"purpose": "sweep"}, {"purpose": "takeover"}, {}, {"purpose": "look", "x": 1}):
        assert api.post("/api/browser/sessions", token=user_a[1], json=body).status_code == 422


def test_uids_are_unique_under_concurrency(factory: sessionmaker[Session]) -> None:
    people = [make_user(factory, email=f"e2e-browser-{n}@example.org").id for n in range(8)]
    found: list[profiles.ProfileRef] = []
    gate = threading.Barrier(16)

    def first_lease(user_id: uuid.UUID) -> None:
        gate.wait()
        found.append(profiles.ensure(factory, user_id))

    threads = [threading.Thread(target=first_lease, args=(p,)) for p in people + people]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(found) == 16
    assert len({ref.uid for ref in found}) == 8
    assert len({ref.profile_id for ref in found}) == 8
    with factory() as db:
        assert db.execute(sa.text("SELECT count(*) FROM browser_profiles")).scalar_one() == 8


def last_uid(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(
            db.execute(sa.text("SELECT last_value FROM browser_profile_uid_seq")).scalar_one()
        )


def test_later_leases_use_up_no_uid(factory: sessionmaker[Session]) -> None:
    first_person = make_user(factory, email="e2e-browser-1@example.org").id
    first = profiles.ensure(factory, first_person)
    for _ in range(6):  # every later lease of that person
        assert profiles.ensure(factory, first_person) == first
    assert last_uid(factory) == first.uid
    second = profiles.ensure(factory, make_user(factory, email="e2e-browser-2@example.org").id)
    assert second.uid == first.uid + 1


def test_racing_first_leases_of_one_person_make_one_row(factory: sessionmaker[Session]) -> None:
    """k first leases of one person at once: one row, and at most k numbers of the sequence (each
    call that passed the read before the winner committed draws one), never more; the calls that
    follow draw none."""
    person = make_user(factory, email="e2e-browser-1@example.org").id
    profiles.ensure(factory, make_user(factory, email="e2e-browser-2@example.org").id)
    before = last_uid(factory)
    k = 8
    found: list[profiles.ProfileRef] = []
    gate = threading.Barrier(k)

    def first_lease() -> None:
        gate.wait()
        found.append(profiles.ensure(factory, person))

    threads = [threading.Thread(target=first_lease) for _ in range(k)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(found) == k
    assert len(set(found)) == 1
    used = last_uid(factory) - before
    assert 1 <= used <= k
    with factory() as db:
        rows = db.execute(
            sa.text("SELECT count(*) FROM browser_profiles WHERE user_id = :u"), {"u": person}
        ).scalar_one()
    assert rows == 1
    for _ in range(3):
        profiles.ensure(factory, person)
    assert last_uid(factory) - before == used


def test_a_taken_uid_is_retried(factory: sessionmaker[Session]) -> None:
    first = profiles.ensure(factory, make_user(factory, email="e2e-browser-1@example.org").id)
    with factory() as db:
        # The sequence set back by hand: its next number is the one the first person has.
        db.execute(sa.text("SELECT setval('browser_profile_uid_seq', :v, false)"), {"v": first.uid})
        db.commit()
    second = profiles.ensure(factory, make_user(factory, email="e2e-browser-2@example.org").id)
    assert second.uid == first.uid + 1


def test_a_uid_is_never_given_twice(factory: sessionmaker[Session]) -> None:
    gone = make_user(factory, email="e2e-browser-1@example.org").id
    old = profiles.ensure(factory, gone)
    with factory() as db:
        db.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": gone})
        db.commit()
    new = profiles.ensure(factory, make_user(factory, email="e2e-browser-2@example.org").id)
    assert new.uid > old.uid


def test_deleting_the_account_cascades_and_queues_the_directory(
    factory: sessionmaker[Session],
) -> None:
    user_id = make_user(factory, email=USER_A).id
    made = profiles.ensure(factory, user_id)
    with factory() as db:
        db.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        db.commit()
        assert db.execute(sa.text("SELECT count(*) FROM browser_profiles")).scalar_one() == 0
        queued = db.execute(sa.text("SELECT profile_id, uid FROM browser_profile_removals")).all()
    assert [(str(q.profile_id), q.uid) for q in queued] == [(made.profile_id, made.uid)]


def test_0016_up_down_up(engine: sa.Engine) -> None:
    from alembic import command

    url = engine.url.render_as_string(hide_password=False)
    names = (
        "SELECT (SELECT count(*) FROM pg_class WHERE relname IN "
        "('browser_profiles', 'browser_profile_removals', 'browser_profile_uid_seq')) + "
        "(SELECT count(*) FROM pg_proc WHERE proname = 'browser_profiles_queue_removal')"
    )

    def count() -> int:
        with engine.connect() as conn:
            return int(conn.execute(sa.text(names)).scalar_one())

    assert HEAD >= "0016"
    assert count() == 4
    command.downgrade(alembic_config(), "0015")
    assert current_revision(url) == "0015"
    assert count() == 0
    command.upgrade(alembic_config(), "head")
    assert current_revision(url) == HEAD
    assert count() == 4


def test_redis_down_on_delete_is_503(app: FastAPI, api: Api, user_a: tuple[uuid.UUID, str]) -> None:
    async def broken() -> Any:
        raise ConnectionError("redis is down")

    app.state.browser_leases._pool = broken
    answer = api.request("DELETE", "/api/browser/sessions/current", token=user_a[1])
    assert (answer.status_code, answer.json()) == (503, {"detail": UNAVAILABLE})
