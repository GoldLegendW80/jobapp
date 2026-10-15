"""Sign in throttling (I7) against a real Postgres: 5 failures per email and 20 per client
address in a sliding 15 minute window give 429 with Retry-After, before any argon2 work; a
success resets the email counter only; unknown, disabled and malformed addresses count; the
window slides to the second; parallel guesses never overshoot; old rows are purged; the
password change shares the counters; and nothing secret reaches the logs."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.identity import security, service, throttle
from app.identity.models import LoginAttempt, Role
from app.identity.router import LOGIN_FAILED
from tests.identity.api_helpers import CSRF, PASSWORD, Api, Clock, make_user, session_cookie

EMAIL = "harald@example.org"
WRONG = "not the password at all"
IP = "198.51.100.7"
OTHER_IP = "203.0.113.50"
PASSWORD_URL = "/api/auth/password"
NEW = "a brand new passphrase 7"


def throttled(minutes: int) -> dict[str, str]:
    unit = "minute" if minutes == 1 else "minutes"
    return {"detail": f"Too many sign in attempts. Try again in {minutes} {unit}."}


def from_ip(ip: str) -> dict[str, str]:
    # Caddy sends exactly one X-Forwarded-For; uvicorn --proxy-headers (ProxyHeadersMiddleware
    # in the api fixture) turns it into request.client.host.
    return {"X-Forwarded-For": ip}


def login(
    api: Api, email: str = EMAIL, password: str = WRONG, ip: str | None = IP
) -> httpx.Response:
    return api.login(email, password, headers=from_ip(ip) if ip else None)


def attempts(factory: sessionmaker[Session]) -> list[LoginAttempt]:
    with factory() as db:
        return list(db.scalars(sa.select(LoginAttempt).order_by(LoginAttempt.id)))


def fail_times(api: Api, n: int, email: str = EMAIL, ip: str | None = IP) -> None:
    for _ in range(n):
        response = login(api, email, ip=ip)
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": LOGIN_FAILED}


@pytest.fixture
def argon2_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every argon2 verification the api runs (real or dummy), recorded thread safely."""
    calls: list[str] = []
    lock = threading.Lock()
    real_verify, real_dummy = security.verify_password, security.hash_dummy

    def verify(password_hash: str, password: str) -> bool:
        with lock:
            calls.append("verify")
        return real_verify(password_hash, password)

    def dummy(password: str = "") -> None:
        with lock:
            calls.append("dummy")
        real_dummy(password)

    monkeypatch.setattr(security, "verify_password", verify)
    monkeypatch.setattr(security, "hash_dummy", dummy)
    return calls


# --- per email ---------------------------------------------------------------------------------


def test_sixth_attempt_is_429_even_with_the_right_password(
    api: Api, factory: sessionmaker[Session], argon2_calls: list[str]
) -> None:
    make_user(factory)
    fail_times(api, 5)
    assert len(argon2_calls) == 5
    response = login(api, password=PASSWORD)
    assert response.status_code == 429
    assert response.json() == throttled(15)
    assert response.headers["retry-after"] == "900"  # the oldest failure is 15 minutes away
    assert session_cookie(response) is None
    assert len(argon2_calls) == 5  # refused before any argon2 work
    assert len(attempts(factory)) == 5  # a refusal is not recorded


def test_another_email_is_not_affected(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    make_user(factory, email="ann@example.org", name="Ann", role=Role.member)
    fail_times(api, 5)
    assert login(api).status_code == 429
    assert login(api, "ann@example.org", WRONG).status_code == 401
    assert login(api, "ann@example.org", PASSWORD).status_code == 200


def test_email_variants_share_one_counter(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    for variant in ("HARALD@EXAMPLE.ORG", " Harald@Example.org", "harald@example.ORG\n"):
        assert login(api, variant).status_code == 401
    fail_times(api, 2)
    assert login(api, "Harald@EXAMPLE.org", PASSWORD).status_code == 429
    assert {row.email for row in attempts(factory)} == {EMAIL}


def test_unknown_email_attempts_are_counted(api: Api, factory: sessionmaker[Session]) -> None:
    fail_times(api, 5, email="Nobody@Example.org")
    assert login(api, "nobody@example.org").json() == throttled(15)
    rows = attempts(factory)
    assert [(r.email, r.success) for r in rows] == [("nobody@example.org", False)] * 5


def test_disabled_account_attempts_are_counted_with_the_same_401(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory, active=False)
    for _ in range(5):
        response = login(api, password=PASSWORD)  # right password, disabled account
        assert response.status_code == 401
        assert response.json() == {"detail": LOGIN_FAILED}
    assert login(api, password=PASSWORD).status_code == 429
    assert [row.success for row in attempts(factory)] == [False] * 5


def test_malformed_addresses_are_counted_under_their_text(
    api: Api, factory: sessionmaker[Session]
) -> None:
    fail_times(api, 5, email="  Not An\x00 Email ")
    assert login(api, "not an email").status_code == 429
    assert {row.email for row in attempts(factory)} == {"not an email"}


def test_success_resets_the_email_counter(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    fail_times(api, 4)
    assert login(api, password=PASSWORD).status_code == 200
    fail_times(api, 5)  # four before the success no longer count
    assert login(api, password=PASSWORD).status_code == 429
    assert [row.success for row in attempts(factory)] == [False] * 4 + [True] + [False] * 5


# --- per client address ------------------------------------------------------------------------


def test_twenty_failures_from_one_address_block_a_new_email(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    for i in range(20):
        assert login(api, f"user{i}@example.org").status_code == 401
    response = login(api, "fresh@example.org")
    assert response.status_code == 429
    assert response.json() == throttled(15)
    assert response.headers["retry-after"] == "900"
    # The same account from another address is fine, and the address is the forwarded one.
    assert login(api, password=PASSWORD, ip=OTHER_IP).status_code == 200
    assert {str(row.ip) for row in attempts(factory)} == {IP, OTHER_IP}


def test_a_success_does_not_reset_the_address_counter(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    for i in range(19):
        assert login(api, f"user{i}@example.org").status_code == 401
    assert login(api, password=PASSWORD).status_code == 200
    assert login(api, "user19@example.org").status_code == 401  # the 20th failure
    assert login(api, password=PASSWORD).status_code == 429


# An IPv6 subscriber is given a whole /64: the limit counts the /64, not each address in it.
SUBSCRIBER = "2001:db8:1:2"
NEIGHBOUR = "2001:db8:1:3"


def in_64(prefix: str, host: int) -> str:
    """One of the addresses of a /64, a different one for each `host`."""
    return f"{prefix}:{host + 1:x}:aaaa:0:{host + 1:x}"


def test_addresses_of_one_ipv6_64_share_a_count_and_another_64_does_not(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    for i in range(20):
        assert login(api, f"user{i}@example.org", ip=in_64(SUBSCRIBER, i)).status_code == 401
    # A fresh address of the same /64 is stopped, the right password included.
    response = login(api, "fresh@example.org", ip=in_64(SUBSCRIBER, 500))
    assert response.status_code == 429
    assert response.json() == throttled(15)
    assert login(api, password=PASSWORD, ip=in_64(SUBSCRIBER, 501)).status_code == 429
    # The /64 next to it has its own count: a failure is a 401 and the right password signs in.
    assert login(api, "fresh@example.org", ip=in_64(NEIGHBOUR, 0)).status_code == 401
    assert login(api, password=PASSWORD, ip=in_64(NEIGHBOUR, 1)).status_code == 200
    # The rows keep the whole address: 20 different ones from the first /64.
    stored = [str(row.ip) for row in attempts(factory)]
    assert len({ip for ip in stored if ip.startswith(SUBSCRIBER + ":")}) == 20
    with factory() as db:
        assert throttle.failures_in_window(db, clock(), ip=in_64(SUBSCRIBER, 999)) == 20
        assert throttle.failures_in_window(db, clock(), ip=in_64(NEIGHBOUR, 999)) == 1


def test_two_ipv4_addresses_next_to_each_other_do_not_share_a_count(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # IPv4 stays as it was: the whole address, no grouping by network.
    for i in range(20):
        assert login(api, f"user{i}@example.org", ip="198.51.100.7").status_code == 401
    assert login(api, "fresh@example.org", ip="198.51.100.7").status_code == 429
    assert login(api, "fresh@example.org", ip="198.51.100.8").status_code == 401


def test_an_ipv4_mapped_address_counts_with_its_ipv4_form(
    api: Api, factory: sessionmaker[Session]
) -> None:
    for i in range(10):
        assert login(api, f"user{i}@example.org", ip=IP).status_code == 401
        assert login(api, f"mapped{i}@example.org", ip=f"::ffff:{IP}").status_code == 401
    assert login(api, "fresh@example.org", ip=f"::ffff:{IP}").status_code == 429
    assert login(api, "fresh@example.org", ip=IP).status_code == 429
    assert {str(row.ip) for row in attempts(factory)} == {IP}
    # Another mapped address is another IPv4 address, not a neighbour in some /64.
    assert login(api, "fresh@example.org", ip=f"::ffff:{OTHER_IP}").status_code == 401


def test_a_malformed_address_is_recorded_as_null_and_not_limited_per_address(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # As before the /64: what is not an address has no per address count, only the email one.
    for i in range(25):
        assert login(api, f"user{i}@example.org", ip="2001:db8:1:2::zz").status_code == 401
    assert all(row.ip is None for row in attempts(factory))


@pytest.mark.parametrize(
    ("raw", "stored", "key"),
    [
        ("198.51.100.7", "198.51.100.7", "198.51.100.7"),
        (
            "2001:db8:1:2:aaaa:bbbb:cccc:dddd",
            "2001:db8:1:2:aaaa:bbbb:cccc:dddd",
            "2001:db8:1:2::/64",
        ),
        ("2001:DB8:1:2::1", "2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::1", "::1", "::/64"),
        ("fe80::1%eth0", "fe80::1", "fe80::/64"),  # the zone is dropped, inet refuses one
        ("::ffff:198.51.100.7", "198.51.100.7", "198.51.100.7"),
        ("::ffff:c633:6407", "198.51.100.7", "198.51.100.7"),
    ],
)
def test_client_address(raw: str, stored: str, key: str) -> None:
    assert throttle.client_address(raw) == throttle.ClientAddress(stored, key)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "testclient",
        "not an address",
        "198.51.100.7:1234",
        "[2001:db8::1]",
        "2001:db8::/64",
    ],
)
def test_client_address_of_what_is_not_an_address(raw: str | None) -> None:
    assert throttle.client_address(raw) is None
    assert service.clean_ip(raw) is None  # the same fallback as the sessions table


def test_without_an_address_only_the_email_limit_applies(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # The test client's "testclient" host is not an address: recorded as NULL, never grouped.
    for i in range(25):
        assert login(api, f"user{i}@example.org", ip=None).status_code == 401
    assert all(row.ip is None for row in attempts(factory))


def test_retry_after_is_the_longer_wait_when_both_limits_hit(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    for i in range(16):
        assert login(api, f"user{i}@example.org").status_code == 401
    clock.advance(minutes=5)
    fail_times(api, 4)  # 20 for the address, the oldest 15 minutes ahead at the start
    response = login(api)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"  # address: 15 minutes after the first
    fail_times(api, 1, ip=OTHER_IP)  # the fifth for the email, from elsewhere
    response = login(api)
    assert response.headers["retry-after"] == "900"  # email: its oldest failure is the newest


# --- the window --------------------------------------------------------------------------------


def test_the_window_slides_to_the_second(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    for _ in range(5):
        fail_times(api, 1)
        clock.advance(minutes=1)
    # Failures at t, t+1, t+2, t+3, t+4 minutes; now t+5.
    response = login(api, password=PASSWORD)
    assert response.headers["retry-after"] == "600"
    assert response.json() == throttled(10)
    clock.advance(minutes=10, seconds=-1)  # t+15m-1s: the first still counts
    response = login(api, password=PASSWORD)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json() == throttled(1)
    clock.advance(seconds=1)  # exactly t+15m: the first falls out
    fail_times(api, 1)  # four left, plus this one
    response = login(api, password=PASSWORD)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"  # the failure at t+1 goes at t+16
    clock.advance(minutes=1)
    assert login(api, password=PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("seconds_ago", "retry_after", "minutes"),
    [(0, 900, 15), (1, 899, 15), (59.5, 841, 15), (840, 60, 1), (841, 59, 1), (899.2, 1, 1)],
)
def test_retry_after_is_a_whole_number_of_seconds_at_least_one(
    api: Api, clock: Clock, seconds_ago: float, retry_after: int, minutes: int
) -> None:
    start = clock.now
    fail_times(api, 5)
    clock.now = start + timedelta(seconds=seconds_ago)
    response = login(api)
    assert response.status_code == 429
    value = response.headers["retry-after"]
    assert value.isdigit()
    assert int(value) == retry_after >= 1
    assert response.json() == throttled(minutes)


def test_after_the_window_the_right_password_works(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    fail_times(api, 5)
    assert login(api, password=PASSWORD).status_code == 429
    clock.advance(minutes=15)
    assert login(api, password=PASSWORD).status_code == 200


# --- concurrency -------------------------------------------------------------------------------


def _parallel_logins(app: FastAPI, bodies: list[tuple[str, str]], ip: str | list[str]) -> list[int]:
    # One client, so one lifespan and one engine: its portal takes calls from any thread, and
    # each request runs its handler on the app's threadpool, all at once after the barrier.
    # The engine holds a connection for every guess (issue #293): the production pool (5 plus
    # 5, a 2 s wait) is not what these tests are about, and on a busy CPU the guesses queued on
    # the advisory lock outlasted that wait (QueuePool limit reached). Each guess still waits
    # for the one lock per email and per address, which is what keeps the count exact.
    pooled = sa.create_engine(
        app.state.engine.url, pool_size=len(bodies), max_overflow=0, hide_parameters=True
    )
    app.state.sessionmaker = sessionmaker(bind=pooled, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(len(bodies))
    ips = [ip] * len(bodies) if isinstance(ip, str) else ip
    try:
        with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:

            def attempt(body: tuple[str, str], from_address: str) -> int:
                barrier.wait(timeout=30)
                return client.post(
                    "/api/auth/login",
                    json={"email": body[0], "password": body[1]},
                    headers={**CSRF, **from_ip(from_address)},
                ).status_code

            with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
                return list(pool.map(attempt, bodies, ips))
    finally:
        pooled.dispose()


def test_parallel_guesses_on_one_email_never_overshoot(
    app: FastAPI, factory: sessionmaker[Session], argon2_calls: list[str]
) -> None:
    # The count and the insert happen under one advisory lock per email: exactly 5 reach argon2.
    make_user(factory)
    codes = _parallel_logins(app, [(EMAIL, f"wrong guess {i}") for i in range(20)], IP)
    assert sorted(codes) == [401] * 5 + [429] * 15, codes
    assert len(argon2_calls) == 5
    assert len(attempts(factory)) == 5


def test_parallel_guesses_from_one_address_never_overshoot(
    app: FastAPI, factory: sessionmaker[Session], argon2_calls: list[str]
) -> None:
    codes = _parallel_logins(app, [(f"user{i}@example.org", WRONG) for i in range(30)], IP)
    assert sorted(codes) == [401] * 20 + [429] * 10, codes
    assert len(argon2_calls) == 20


def test_parallel_guesses_from_one_ipv6_64_never_overshoot(
    app: FastAPI, factory: sessionmaker[Session], argon2_calls: list[str]
) -> None:
    # Thirty addresses of one /64 at once: one lock and one count for the /64, exactly 20 pass.
    bodies = [(f"user{i}@example.org", WRONG) for i in range(30)]
    codes = _parallel_logins(app, bodies, [in_64(SUBSCRIBER, i) for i in range(30)])
    assert sorted(codes) == [401] * 20 + [429] * 10, codes
    assert len(argon2_calls) == 20


# --- retention ---------------------------------------------------------------------------------


def _insert(factory: sessionmaker[Session], *ats: datetime) -> None:
    with factory() as db, db.begin():
        db.add_all(LoginAttempt(email="old@example.org", ip=IP, at=at, success=False) for at in ats)


def _old_rows(factory: sessionmaker[Session]) -> list[datetime]:
    return sorted(r.at for r in attempts(factory) if r.email == "old@example.org")


def test_old_attempts_are_purged_at_most_once_an_hour(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    now = clock.now
    month = timedelta(days=30)
    kept = [now - month, now - month + timedelta(seconds=1), now - timedelta(days=29)]
    _insert(factory, now - timedelta(days=365), now - month - timedelta(seconds=1), *kept)
    login(api)  # the first write of this process purges
    assert _old_rows(factory) == kept  # older than 30 days only; exactly 30 days stays

    _insert(factory, now - timedelta(days=40))
    clock.advance(minutes=59, seconds=59)
    login(api, "other@example.org")
    assert len(_old_rows(factory)) == 4  # not again within the hour

    clock.advance(seconds=1)
    login(api, "third@example.org")
    # An hour later the cutoff moved by an hour too: only the 29 day row is still young enough.
    assert _old_rows(factory) == kept[2:]


def test_purger_is_due_again_when_the_clock_goes_backwards(
    factory: sessionmaker[Session], clock: Clock
) -> None:
    purger = throttle.AttemptPurger()
    with factory() as db:
        assert purger.maybe_purge(db, clock.now) == 0
        assert purger.maybe_purge(db, clock.now + timedelta(minutes=30)) is None
        assert purger.maybe_purge(db, clock.now - timedelta(minutes=1)) == 0
        db.rollback()


def test_a_refused_attempt_purges_nothing_and_records_nothing(
    app: FastAPI, api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    fail_times(api, 5)
    _insert(factory, clock.now - timedelta(days=60))
    app.state.login_purger = throttle.AttemptPurger()  # a purge is due again
    assert login(api).status_code == 429
    assert len(_old_rows(factory)) == 1
    assert len(attempts(factory)) == 6
    clock.advance(minutes=15)
    assert login(api).status_code == 401  # recorded, and the due purge runs with it
    assert _old_rows(factory) == []


# --- the password change -----------------------------------------------------------------------


def change(api: Api, token: str, current: str, ip: str = IP) -> httpx.Response:
    return api.post(
        PASSWORD_URL,
        token=token,
        json={"current_password": current, "new_password": NEW},
        headers=from_ip(ip),
    )


def test_password_change_is_throttled_after_five_wrong_current_passwords(
    api: Api, factory: sessionmaker[Session], clock: Clock, argon2_calls: list[str]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    for i in range(5):
        assert change(api, token, f"wrong current {i}").status_code == 400
    calls = len(argon2_calls)
    response = change(api, token, PASSWORD)  # even the right one
    assert response.status_code == 429
    assert response.json() == {
        "detail": "Too many attempts with a wrong current password. Try again in 15 minutes."
    }
    assert response.headers["retry-after"] == "900"
    assert len(argon2_calls) == calls  # nothing verified
    assert api.me(token).status_code == 200  # still signed in
    # Both are guesses of the same password: the sign in of that email is throttled too.
    assert login(api, password=PASSWORD).status_code == 429
    clock.advance(minutes=15)
    assert change(api, token, PASSWORD).status_code == 204


def test_a_verified_current_password_resets_the_count(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    for i in range(4):
        assert change(api, token, f"wrong current {i}").status_code == 400
    # Right current password, refused new one: the current one was still verified.
    refused = api.post(
        PASSWORD_URL,
        token=token,
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=from_ip(IP),
    )
    assert refused.status_code == 422
    for i in range(4):
        assert change(api, token, f"wrong again {i}").status_code == 400
    assert change(api, token, PASSWORD).status_code == 204


def test_password_change_counts_toward_the_address_limit(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    for i in range(19):
        assert login(api, f"user{i}@example.org").status_code == 401
    assert change(api, token, "wrong current").status_code == 400  # the 20th failure
    assert change(api, token, PASSWORD).status_code == 429


# --- logs --------------------------------------------------------------------------------------


def test_throttling_logs_no_password_cookie_token_or_email(
    api: Api,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    make_user(factory)
    token = api.login_token(EMAIL)
    wrongs = [f"secret wrong passphrase {i}" for i in range(6)]
    for wrong in wrongs[:5]:
        assert change(api, token, wrong).status_code == 400
    assert change(api, token, wrongs[5]).status_code == 429
    assert login(api, "nobody@example.org", wrongs[0]).status_code == 401
    assert login(api, password=PASSWORD).status_code == 429

    app_records = [r for r in caplog.records if not r.name.startswith("httpx")]
    assert any(r.__dict__.get("status") == 429 for r in app_records)
    text = "\n".join(r.getMessage() + " " + json.dumps(vars(r), default=str) for r in app_records)
    stdout = "\n".join(
        line for line in capsys.readouterr().out.splitlines() if '"logger": "httpx"' not in line
    )
    # Emails were never logged before I7 and still are not.
    for secret in [PASSWORD, token, *wrongs, EMAIL, "nobody@example.org"]:
        assert secret not in text
        assert secret not in stdout


# --- units -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "phrase"),
    [
        (1, "Try again in 1 minute."),
        (60, "Try again in 1 minute."),
        (61, "Try again in 2 minutes."),
        (900, "Try again in 15 minutes."),
        (0, "Try again in 1 minute."),
    ],
)
def test_wait_phrase(seconds: int, phrase: str) -> None:
    assert throttle.wait_phrase(seconds) == phrase


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("  Harald@Example.ORG ", "harald@example.org"),
        ("not an email", "not an email"),
        ("NUL\x00@x", "nul@x"),
        ("x" * 400, "x" * 320),
        ("   ", ""),
    ],
)
def test_attempt_email_key(raw: str, key: str) -> None:
    assert throttle.attempt_email(raw) == key
