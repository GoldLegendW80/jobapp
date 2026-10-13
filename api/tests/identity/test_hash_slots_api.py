"""The bound on password hashes at once, through the API (issue #108): a flood of sign ins over a
real socket, the answer when no slot is free, and the requests that must never ask for one."""

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.body_limit import PUBLIC_MAX_BYTES
from app.identity import security, service
from app.identity.hash_slots import HashBusyError, HashSlots
from app.identity.models import InviteKind, LoginAttempt, Role
from app.identity.router import LOGIN_FAILED, SERVER_BUSY
from tests.conftest import FAST_HASHER
from tests.identity.api_helpers import (
    CSRF,
    PASSWORD,
    Api,
    Clock,
    issue_link,
    make_user,
    session_cookie,
)
from tests.identity.test_body_limit_api import Server

WRONG = "not the password at all"
NEW = "a brand new passphrase 7"
LOGIN = "/api/auth/login"
BUSY = {"detail": SERVER_BUSY}


class CountingHasher:
    """The test hasher, slowed down, counting the computations running at once from inside."""

    def __init__(self, seconds: float = 0.0) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self.inside = 0
        self.peak = 0
        self.calls = 0

    @contextmanager
    def _counted(self) -> Iterator[None]:
        with self._lock:
            self.inside += 1
            self.calls += 1
            self.peak = max(self.peak, self.inside)
        try:
            time.sleep(self.seconds)
            yield
        finally:
            with self._lock:
                self.inside -= 1

    def hash(self, password: str | bytes) -> str:
        with self._counted():
            return FAST_HASHER.hash(password)

    def verify(self, password_hash: str, password: str | bytes) -> bool:
        with self._counted():
            return FAST_HASHER.verify(password_hash, password)

    def check_needs_rehash(self, password_hash: str) -> bool:
        return FAST_HASHER.check_needs_rehash(password_hash)


def use_hasher(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.0) -> CountingHasher:
    hasher = CountingHasher(seconds)
    monkeypatch.setattr(security, "_hasher", hasher)
    security._dummy_hash.cache_clear()
    return hasher


def use_slots(monkeypatch: pytest.MonkeyPatch, *args: Any) -> HashSlots:
    slots = HashSlots(*args)
    monkeypatch.setattr(security, "password_slots", slots)
    return slots


@contextmanager
def occupied(slots: HashSlots) -> Iterator[None]:
    """Every slot held by someone else for the block."""
    release = threading.Event()

    def hold() -> None:
        with slots.hold():
            release.wait(timeout=30)

    threads = [threading.Thread(target=hold) for _ in range(slots.slots)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    while slots.held < slots.slots:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    try:
        yield
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)


def attempts(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(db.scalar(sa.select(sa.func.count()).select_from(LoginAttempt)) or 0)


# --- a flood over a real socket ----------------------------------------------------------------


@pytest.fixture
def proxied(app: FastAPI) -> Iterator[Server]:
    # As in production: uvicorn behind Caddy, the client address from X-Forwarded-For.
    made = Server(ProxyHeadersMiddleware(app, trusted_hosts="*"))  # type: ignore[arg-type]
    yield made
    made.close()


def sign_ins_at_once(server: Server, bodies: list[tuple[str, str]]) -> list[httpx.Response]:
    """One request per body, each from its own address, all sent after one barrier."""
    barrier = threading.Barrier(len(bodies))
    url = f"http://127.0.0.1:{server.port}{LOGIN}"

    def send(numbered: tuple[int, tuple[str, str]]) -> httpx.Response:
        i, (email, password) = numbered
        headers = {**CSRF, "X-Forwarded-For": f"198.51.{100 + i // 250}.{1 + i % 250}"}
        barrier.wait(timeout=30)
        return httpx.post(
            url, json={"email": email, "password": password}, headers=headers, timeout=30
        )

    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        return list(pool.map(send, enumerate(bodies)))


def test_a_flood_of_sign_ins_never_passes_the_slots_and_the_excess_gets_503(
    proxied: Server, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The production bounds (2 slots, 16 waiting, 3 s), a hash of 0.2 s, 60 sign ins at once:
    # 10 with the right password, 25 with a wrong one, 25 for emails with no account.
    hasher = use_hasher(monkeypatch, seconds=0.2)
    slots = use_slots(monkeypatch, 2, 16, 3.0, 2)
    assert (slots.slots, slots.max_waiting, slots.max_wait_s) == (
        security.PASSWORD_HASH_SLOTS,
        security.PASSWORD_HASH_MAX_WAITING,
        security.PASSWORD_HASH_MAX_WAIT_S,
    )
    users = [f"e2e-flood-{i}@example.org" for i in range(10)]
    for email in users:
        make_user(factory, email=email, role=Role.member)
    security.hash_dummy("made once, before the count")
    hasher.calls = hasher.peak = 0
    right = [(email, PASSWORD) for email in users]
    wrong = [(users[i % 10], f"{WRONG} {i}") for i in range(25)]
    unknown = [(f"e2e-nobody-{i}@example.org", PASSWORD) for i in range(25)]
    bodies = right + wrong + unknown

    started = time.monotonic()
    responses = sign_ins_at_once(proxied, bodies)
    elapsed = time.monotonic() - started

    codes = [response.status_code for response in responses]
    busy = [response for response in responses if response.status_code == 503]
    assert set(codes) == {200, 401, 503}, codes
    # The hashes never ran more than 2 at once, measured inside the hash itself.
    assert hasher.peak == 2
    assert slots.peak == 2
    # The excess is refused, cheaply and with a time to come back; the rest is served.
    assert len(busy) == slots.refused
    assert 1 <= len(busy) <= 60 - 18  # at least the 2 holders and the 16 waiters were served
    for response in busy:
        assert response.headers["Retry-After"] == "2"
        assert response.json() == BUSY
        assert "set-cookie" not in response.headers
    # One verification per request that got a slot, none for a refused one.
    assert hasher.calls == 60 - len(busy)
    # A refused attempt tested no password: it is not recorded, so it counts for no lockout.
    assert attempts(factory) == 60 - len(busy)
    # Right passwords sign in or are told to come back, never 401; the others never sign in.
    assert {r.status_code for r in responses[:10]} <= {200, 503}
    assert {r.status_code for r in responses[10:]} <= {401, 503}
    assert codes.count(200) + codes.count(401) >= 18
    assert elapsed < 10

    # Told to come back, the right ones then sign in.
    for (email, password), response in zip(right, responses[:10], strict=True):
        if response.status_code == 503:
            again = sign_ins_at_once(proxied, [(email, password)])[0]
            assert again.status_code == 200, again.text
    assert (slots.held, slots.waiting) == (0, 0)


def test_a_burst_of_ten_real_sign_ins_all_succeed(
    proxied: Server, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ten people within the same instant, a hash of 0.25 s (five times the Mac's figure): five
    # rounds of two, nobody refused, everybody in within the 3 s wait bound.
    hasher = use_hasher(monkeypatch, seconds=0.25)
    slots = use_slots(monkeypatch, 2, 16, 3.0, 2)
    users = [f"e2e-burst-{i}@example.org" for i in range(10)]
    for email in users:
        make_user(factory, email=email, role=Role.member)

    started = time.monotonic()
    responses = sign_ins_at_once(proxied, [(email, PASSWORD) for email in users])
    elapsed = time.monotonic() - started

    assert [response.status_code for response in responses] == [200] * 10
    assert all(session_cookie(response) is not None for response in responses)
    assert hasher.peak == 2
    assert slots.refused == 0
    assert 5 * 0.25 <= elapsed < 3.0 + 1.0


# --- no slot free: the answer, and no oracle ---------------------------------------------------


def comparable(response: httpx.Response) -> tuple[int, Any, list[str], str | None]:
    names = sorted(name for name in response.headers if name.lower() != "x-request-id")
    return response.status_code, response.json(), names, response.headers.get("Retry-After")


def test_no_slot_answers_503_the_same_for_any_account(
    api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    hasher = use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 0, 0.0, 2)
    user = make_user(factory, email="e2e-known@example.org")
    make_user(factory, email="e2e-disabled@example.org", active=False)
    hasher.calls = 0

    with occupied(slots):
        answers = [
            api.login(user.email),  # right password
            api.login(user.email, WRONG),
            api.login("e2e-disabled@example.org"),
            api.login("e2e-nobody@example.org"),
            api.login("not an email address"),
        ]
    first = comparable(answers[0])
    assert first[0] == 503
    assert first[1] == BUSY
    assert first[3] == "2"
    assert [comparable(answer) for answer in answers] == [first] * 5
    assert all(session_cookie(answer) is None for answer in answers)
    assert hasher.calls == 0
    assert attempts(factory) == 0  # nothing recorded: five busy moments, no step to a lockout

    # With a slot free again: one verification each, and the same 401 for every failure.
    failures = [
        api.login(user.email, WRONG),
        api.login("e2e-disabled@example.org"),
        api.login("e2e-nobody@example.org"),
        api.login("not an email address"),
    ]
    assert [comparable(answer)[:2] for answer in failures] == [(401, {"detail": LOGIN_FAILED})] * 4
    assert len({tuple(comparable(answer)[2]) for answer in failures}) == 1
    assert hasher.calls == 4 + 1  # the dummy hash itself is made once
    assert api.login(user.email).status_code == 200


def test_an_unknown_email_waits_for_a_slot_as_long_as_a_wrong_password(
    api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The slot is asked for before the account is looked up, so the wait cannot depend on it.
    use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 4, 0.3, 2)
    user = make_user(factory, email="e2e-known@example.org")
    waits = []
    with occupied(slots):
        for email, password in [(user.email, WRONG), ("e2e-nobody@example.org", WRONG)]:
            started = time.monotonic()
            assert api.login(email, password).status_code == 503
            waits.append(time.monotonic() - started)
    assert all(0.29 <= wait < 2.0 for wait in waits), waits
    assert slots.refused == 2


def test_requests_refused_cheaply_never_ask_for_a_slot(
    api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    hasher = use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 0, 0.0, 2)
    user = make_user(factory, email="e2e-known@example.org")
    for _ in range(5):
        assert api.login(user.email, WRONG).status_code == 401
    calls_before = hasher.calls

    # Any request that asked for a slot now would answer 503.
    with occupied(slots):
        throttled = api.login(user.email)
        malformed = [
            api.post(LOGIN, json={"email": user.email}),
            api.post(LOGIN, json={"email": user.email, "password": "x" * 2000}),
            api.post(LOGIN, content=b"{not json", headers={"Content-Type": "application/json"}),
            api.post(LOGIN, json=["email", "password"]),
        ]
        oversized = api.post(
            LOGIN, json={"email": user.email, "password": "x" * (PUBLIC_MAX_BYTES + 1)}
        )
        no_header = api.post(LOGIN, json={"email": user.email, "password": PASSWORD}, csrf=False)
        made_up = api.post(
            f"/api/auth/invites/{security.new_token()}/accept",
            json={"password": NEW, "name": "Nobody"},
        )
        bad_shape = api.post(
            "/api/auth/invites/short/accept", json={"password": NEW, "name": "Nobody"}
        )
        no_session = api.post(
            "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
        )

    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) > 2
    assert [response.status_code for response in malformed] == [422] * 4
    assert oversized.status_code == 413
    assert no_header.status_code == 403
    assert (made_up.status_code, bad_shape.status_code) == (404, 404)
    assert no_session.status_code == 401
    assert slots.refused == 0
    assert hasher.calls == calls_before


# --- the other routes that hash ----------------------------------------------------------------


def test_change_password_with_no_slot_is_503_and_changes_nothing(
    api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    hasher = use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 0, 0.0, 2)
    user = make_user(factory, email="e2e-known@example.org")
    token = api.login_token(user.email)
    recorded, calls_before = attempts(factory), hasher.calls
    body = {"current_password": PASSWORD, "new_password": NEW}

    with occupied(slots):
        busy = api.post("/api/auth/password", token=token, json=body)
        wrong = api.post("/api/auth/password", token=token, json={**body, "current_password": NEW})

    assert comparable(busy) == comparable(wrong)  # no word on whether the password was right
    assert (busy.status_code, busy.json(), busy.headers["Retry-After"]) == (503, BUSY, "2")
    assert hasher.calls == calls_before
    assert attempts(factory) == recorded  # the two reserved attempts are forgotten
    assert api.me(token).status_code == 200  # still signed in

    # One slot serves the verification and the new hash together.
    slots.reset_counters()
    assert api.post("/api/auth/password", token=token, json=body).status_code == 204
    assert hasher.calls == calls_before + 2
    assert (slots.peak, slots.refused) == (1, 0)
    assert api.login(user.email, NEW).status_code == 200


def test_accepting_a_link_with_no_slot_is_503_and_the_link_stays_usable(
    api: Api, factory: sessionmaker[Session], clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    hasher = use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 0, 0.0, 2)
    with factory() as db, db.begin():
        issued = service.create_invite(
            db, "e2e-invited@example.org", InviteKind.invite, now=clock()
        )
        path = f"/api/auth/invites/{issued.token}/accept"
    body = {"password": NEW, "name": "Invited"}

    with occupied(slots):
        busy = api.post(path, json=body)
    assert (busy.status_code, busy.json(), busy.headers["Retry-After"]) == (503, BUSY, "2")
    assert session_cookie(busy) is None
    assert hasher.calls == 0

    accepted = api.post(path, json=body)
    assert accepted.status_code == 200, accepted.text
    assert hasher.calls == 1
    assert api.post(path, json=body).status_code == 404  # used once


# --- waiting for a slot holds no database connection -------------------------------------------


def test_callers_waiting_for_a_slot_hold_no_database_connection(
    app: FastAPI,
    api: Api,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pool has 10 connections and 16 callers may wait 3 s each: a waiter that kept its
    # connection would let a flood of sign ins stall every other request of the api. So the four
    # kinds of waiters are parked on the slot together and the pool is read while they wait.
    # Each one reaches the slot by a different road: the committed attempt of a sign in (known
    # and unknown email), the session lookup plus the committed attempt of a password change,
    # and the rollback after the link lookup of an acceptance.
    use_hasher(monkeypatch)
    slots = use_slots(monkeypatch, 1, 8, 20.0, 2)
    changing = make_user(factory, email="e2e-changing@example.org")
    guessed = make_user(factory, email="e2e-guessed@example.org")  # its own email lock
    token = api.login_token(changing.email)
    link = issue_link(factory, "e2e-invited@example.org", now=clock())
    pool = app.state.engine.pool
    assert pool.checkedout() == 0
    waiters = {
        "sign in, wrong password": lambda: api.login(guessed.email, WRONG),
        "sign in, unknown account": lambda: api.login("e2e-nobody@example.org"),
        "password change": lambda: api.post(
            "/api/auth/password",
            token=token,
            json={"current_password": PASSWORD, "new_password": NEW},
        ),
        "link acceptance": lambda: api.post(
            f"/api/auth/invites/{link}/accept", json={"password": NEW, "name": "Invited"}
        ),
    }

    checked_out: list[int] = []
    with ThreadPoolExecutor(max_workers=len(waiters)) as workers:
        with occupied(slots):
            futures = {name: workers.submit(call) for name, call in waiters.items()}
            deadline = time.monotonic() + 10
            while slots.waiting < len(waiters) and time.monotonic() < deadline:
                time.sleep(0.005)
            waiting_before = slots.waiting
            for _ in range(20):
                checked_out.append(pool.checkedout())
                time.sleep(0.02)
            waiting_after = slots.waiting
        # The slot is free again: the four are served one after the other.
        answers = {name: future.result(timeout=30) for name, future in futures.items()}

    assert (waiting_before, waiting_after) == (4, 4)  # all four waited through every sample
    assert checked_out == [0] * 20, checked_out
    assert {name: answer.status_code for name, answer in answers.items()} == {
        "sign in, wrong password": 401,
        "sign in, unknown account": 401,
        "password change": 204,
        "link acceptance": 200,
    }
    assert (slots.held, slots.waiting, slots.refused) == (0, 0, 0)
    assert pool.checkedout() == 0


def test_a_stray_busy_error_is_a_503_never_a_500(
    api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The net in main.py, under any caller of a hash that does not answer for itself.
    user = make_user(factory, email="e2e-known@example.org")

    def busy(*_: object, **__: object) -> None:
        raise HashBusyError(7)

    monkeypatch.setattr(service, "create_session", busy)
    response = api.login(user.email)
    assert (response.status_code, response.json()) == (503, BUSY)
    assert response.headers["Retry-After"] == "7"
