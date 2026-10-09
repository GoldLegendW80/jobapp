"""POST /api/auth/password and PATCH /api/auth/me (I6) against a real Postgres: the password
change ends the other sessions and keeps this one, every refusal changes nothing, unicode
normal forms, CSRF and anonymous calls, a session disabled or a hash replaced while the request
runs, two sessions changing the password at once, and renaming yourself."""

import threading
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.identity import router, security, service
from app.identity.deps import COOKIE_NAME, CSRF_REFUSED, UNAUTHORIZED
from app.identity.models import Role, User
from app.identity.models import Session as UserSession
from tests.identity.api_helpers import (
    CSRF,
    PASSWORD,
    Api,
    Clock,
    issue_link,
    make_user,
    session_cookie,
)

EMAIL = "harald@example.org"
NEW = "a brand new passphrase 7"
PASSWORD_URL = "/api/auth/password"
ME = "/api/auth/me"
WRONG = {"detail": "current_password: The current password is incorrect."}


def change(api: Api, token: str | None, current: str = PASSWORD, new: str = NEW, **kwargs: object):  # type: ignore[no-untyped-def]
    return api.post(
        PASSWORD_URL,
        token=token,
        json={"current_password": current, "new_password": new},
        **kwargs,  # type: ignore[arg-type]
    )


def rename(api: Api, token: str | None, name: object, **kwargs: object):  # type: ignore[no-untyped-def]
    return api.request("PATCH", ME, token=token, json={"name": name}, **kwargs)  # type: ignore[arg-type]


def user_row(factory: sessionmaker[Session], email: str = EMAIL) -> User:
    with factory() as db:
        return db.scalars(sa.select(User).where(User.email == email)).one()


def session_hashes(factory: sessionmaker[Session], email: str = EMAIL) -> set[bytes]:
    with factory() as db:
        return set(
            db.scalars(
                sa.select(UserSession.token_hash)
                .join(User, User.id == UserSession.user_id)
                .where(User.email == email)
            )
        )


def snapshot(factory: sessionmaker[Session], email: str = EMAIL) -> tuple[object, ...]:
    user = user_row(factory, email)
    return (
        user.password_hash,
        user.name,
        user.updated_at,
        user.is_active,
        user.role,
        user.last_login_at,
        frozenset(session_hashes(factory, email)),
    )


@pytest.fixture
def signed_in(api: Api, factory: sessionmaker[Session]) -> tuple[str, str]:
    """Harald signed in on two browsers: (this one, the other one)."""
    make_user(factory)
    return api.login_token(EMAIL), api.login_token(EMAIL)


# --- password change: success ------------------------------------------------------------------


def test_change_ends_other_sessions_and_keeps_this_one(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    this, other = signed_in
    make_user(factory, email="friend@example.org", role=Role.member, name="Friend")
    friend = api.login_token("friend@example.org")
    before = user_row(factory)

    response = change(api, this)
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert session_cookie(response) is None  # same session, same cookie

    assert api.me(this).status_code == 200
    gone = api.me(other)
    assert gone.status_code == 401
    assert gone.json() == {"detail": UNAUTHORIZED}
    assert session_hashes(factory) == {security.hash_token(this)}
    assert api.me(friend).status_code == 200  # someone else's sessions are untouched

    after = user_row(factory)
    assert after.password_hash != before.password_hash
    assert after.updated_at > before.updated_at
    assert after.name == before.name
    assert after.role is before.role
    assert after.last_login_at == before.last_login_at

    assert api.login(EMAIL, PASSWORD).status_code == 401
    assert api.login(EMAIL, NEW).status_code == 200


def test_change_with_a_single_session_ends_nothing(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    assert change(api, token).status_code == 204
    assert api.me(token).status_code == 200
    assert session_hashes(factory) == {security.hash_token(token)}


def test_change_twice_in_a_row(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    assert change(api, token).status_code == 204
    assert change(api, token, current=PASSWORD).json() == WRONG  # the old one is gone
    assert change(api, token, current=NEW, new="third passphrase here").status_code == 204
    assert api.login(EMAIL, "third passphrase here").status_code == 200


def test_change_the_new_hash_is_argon2id_of_the_nfkc_form(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    new = "Ｆｕｌｌ width ＰＡＳＳ ２０２６"  # NFKC turns full-width into ASCII
    assert change(api, signed_in[0], new=new).status_code == 204
    stored = user_row(factory).password_hash
    assert stored.startswith("$argon2id$")
    assert security.verify_password(stored, unicodedata.normalize("NFKC", new))
    assert api.login(EMAIL, "Full width PASS 2026").status_code == 200


# --- password change: refusals change nothing --------------------------------------------------


def test_wrong_current_is_400_changes_nothing_and_keeps_every_session(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    this, other = signed_in
    before = snapshot(factory)
    for _ in range(3):  # repeated mistakes never sign anyone out
        response = change(api, this, current="not my password at all")
        assert response.status_code == 400
        assert response.json() == WRONG
        assert session_cookie(response) is None
    assert snapshot(factory) == before
    assert api.me(this).status_code == 200
    assert api.me(other).status_code == 200
    assert api.login(EMAIL, PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("short pw", "Use at least 10 characters for the password."),
        ("ab" * 64 + "c", "Use at most 128 characters for the password."),
        ("password123", "This password is too common. Choose one that is harder to guess."),
        ("PASSWORD123", "This password is too common. Choose one that is harder to guess."),
        (EMAIL, "The password cannot be your email address."),
        ("HARALD@Example.ORG", "The password cannot be your email address."),
        ("1234567890", "This password is too common. Choose one that is harder to guess."),
    ],
)
def test_weak_new_password_is_422_with_the_rule_message(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], new: str, message: str
) -> None:
    this, other = signed_in
    before = snapshot(factory)
    response = change(api, this, new=new)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": f"new_password: {message}"}
    assert new not in response.text
    assert snapshot(factory) == before
    assert api.me(other).status_code == 200


def test_the_rule_messages_are_the_ones_of_the_invite_page(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], clock: Clock
) -> None:
    link = issue_link(factory, "new@example.org", now=clock.now)
    for weak in ("short pw", "password123", "x" * 129):
        accepted = api.post(
            f"/api/auth/invites/{link}/accept", json={"name": "N", "password": weak}
        ).json()["detail"]
        changed = change(api, signed_in[0], new=weak).json()["detail"]
        assert changed == f"new_password: {accepted}"


@pytest.mark.parametrize(
    "new",
    [
        PASSWORD,
        unicodedata.normalize("NFD", "Grüße aus Bordeaux ÉTÉ"),
        unicodedata.normalize("NFC", "Grüße aus Bordeaux ÉTÉ"),
    ],
)
def test_new_equal_to_current_is_refused_in_any_normal_form(
    api: Api, factory: sessionmaker[Session], new: str
) -> None:
    current = PASSWORD if new == PASSWORD else "Grüße aus Bordeaux ÉTÉ"
    make_user(factory, password=current)
    token = api.login_token(EMAIL, current)
    before = snapshot(factory)
    response = change(api, token, current=current, new=new)
    assert response.status_code == 422
    assert response.json() == {
        "detail": "new_password: The new password must be different from the current one."
    }
    assert snapshot(factory) == before


def test_same_password_is_checked_after_the_current_one(
    api: Api, signed_in: tuple[str, str]
) -> None:
    # Wrong current and new == current typed: the wrong current wins, nothing is confirmed.
    response = change(api, signed_in[0], current="wrong guess 12345", new="wrong guess 12345")
    assert response.json() == WRONG


def test_a_case_change_is_a_different_password(api: Api, signed_in: tuple[str, str]) -> None:
    assert change(api, signed_in[0], new=PASSWORD.upper()).status_code == 204
    assert api.login(EMAIL, PASSWORD.upper()).status_code == 200


def test_unicode_current_password_verifies_in_any_normal_form(
    api: Api, factory: sessionmaker[Session]
) -> None:
    current = "Grüße aus Bordeaux 🔑 ÉTÉ"
    make_user(factory, password=unicodedata.normalize("NFC", current))
    token = api.login_token(EMAIL, current)
    typed = unicodedata.normalize("NFD", current)
    assert typed != unicodedata.normalize("NFC", current)
    new = unicodedata.normalize("NFD", "Ça déménage à Noël 2026")
    assert change(api, token, current=typed, new=new).status_code == 204
    assert api.login(EMAIL, unicodedata.normalize("NFC", new)).status_code == 200


@pytest.mark.parametrize(
    ("payload", "where"),
    [
        ({}, "current_password"),
        ({"current_password": PASSWORD}, "new_password"),
        ({"current_password": "", "new_password": NEW}, "current_password"),
        ({"current_password": PASSWORD, "new_password": ""}, "new_password"),
        ({"current_password": None, "new_password": NEW}, "current_password"),
        ({"current_password": PASSWORD, "new_password": 1234567890}, "new_password"),
        ({"current_password": PASSWORD, "new_password": "n" * 1025}, "new_password"),
        ({"current_password": PASSWORD, "new_password": NEW, "confirm": NEW}, "confirm"),
        ({"password": PASSWORD, "new_password": NEW}, "current_password"),
    ],
)
def test_malformed_bodies_are_422_without_echo(
    api: Api,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    payload: dict[str, object],
    where: str,
) -> None:
    before = snapshot(factory)
    response = api.post(PASSWORD_URL, token=signed_in[0], json=payload)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail.startswith(where)
    assert PASSWORD not in response.text
    assert NEW not in response.text
    assert snapshot(factory) == before


def test_lone_surrogate_and_nul_never_500(api: Api, signed_in: tuple[str, str]) -> None:
    body = b'{"current_password": "correct horse battery 42", "new_password": "\\ud800 lone pass"}'
    response = api.post(
        PASSWORD_URL,
        token=signed_in[0],
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert change(api, signed_in[0], current="nul\x00inside password").json() == WRONG
    # NUL is a character like any other in a password (it is hashed, never stored as text).
    assert change(api, signed_in[0], new="new pass\x00with nul").status_code == 204


def test_longest_accepted_input_is_a_clean_400(api: Api, signed_in: tuple[str, str]) -> None:
    assert change(api, signed_in[0], current="c" * 1024).json() == WRONG


# --- who may call ------------------------------------------------------------------------------


def test_anonymous_is_401(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    for token in (None, "x" * 43, "not a token"):
        response = change(api, token)
        assert response.status_code == 401
        assert response.json() == {"detail": UNAUTHORIZED}
        assert rename(api, token, "Someone").status_code == 401
    assert api.login(EMAIL, PASSWORD).status_code == 200


@pytest.mark.parametrize("value", [None, "", "XMLHttpRequest", "JobApp"])
def test_without_the_csrf_header_is_403(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], value: str | None
) -> None:
    before = snapshot(factory)
    headers = {} if value is None else {"X-Requested-With": value}
    response = change(api, signed_in[0], csrf=False, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    renamed = rename(api, signed_in[0], "Hacker", csrf=False, headers=headers)
    assert renamed.status_code == 403
    assert snapshot(factory) == before


def test_expired_session_is_401(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], clock: Clock
) -> None:
    clock.advance(days=31)
    assert change(api, signed_in[0]).status_code == 401
    assert api.login(EMAIL, PASSWORD).status_code == 200


def test_a_member_may_change_their_own_password(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory, email="friend@example.org", role=Role.member, name="Friend")
    token = api.login_token("friend@example.org")
    assert change(api, token).status_code == 204
    assert api.login("friend@example.org", NEW).status_code == 200


# --- races -------------------------------------------------------------------------------------


def _patch_hash(monkeypatch: pytest.MonkeyPatch, before: Callable[[], None]) -> None:
    """Run `before` inside the request, after the slow hash and before the account lock."""
    real = security.hash_password

    def slow_hash(password: str) -> str:
        result = real(password)
        before()
        return result

    monkeypatch.setattr(security, "hash_password", slow_hash)


def test_disabled_while_the_request_runs_is_401_and_nothing_changes(
    api: Api,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    this, _ = signed_in
    before = user_row(factory).password_hash

    def disable() -> None:
        with factory() as db, db.begin():
            db.execute(sa.update(User).where(User.email == EMAIL).values(is_active=False))

    _patch_hash(monkeypatch, disable)
    response = change(api, this)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel.value == ""  # the cookie is dropped
    assert user_row(factory).password_hash == before


def test_session_ended_while_the_request_runs_is_401(
    api: Api,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    this, other = signed_in
    before = user_row(factory).password_hash

    def sign_out_here() -> None:
        with factory() as db, db.begin():
            service.delete_session_by_token(db, this)

    _patch_hash(monkeypatch, sign_out_here)
    assert change(api, this).status_code == 401
    assert user_row(factory).password_hash == before
    assert api.me(other).status_code == 200  # nothing else ended


def test_password_reset_while_the_request_runs_is_400(
    api: Api,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    this, other = signed_in
    replaced = security.hash_password("set by a reset link 99")

    def reset() -> None:
        with factory() as db, db.begin():
            db.execute(sa.update(User).where(User.email == EMAIL).values(password_hash=replaced))

    _patch_hash(monkeypatch, reset)
    response = change(api, this)
    assert response.status_code == 400
    assert response.json() == WRONG
    assert user_row(factory).password_hash == replaced
    assert api.me(other).status_code == 200


@pytest.mark.parametrize("round_", range(4))
def test_two_sessions_changing_at_once_one_wins_cleanly(
    app: FastAPI,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    round_: int,
) -> None:
    del round_
    tokens = list(signed_in)
    news = ["first new passphrase 1", "second new passphrase 2"]
    barrier = threading.Barrier(2)
    real = security.hash_password

    def hash_then_meet(password: str) -> str:
        result = real(password)
        barrier.wait(timeout=10)  # both hashed and verified: now race for the lock
        return result

    monkeypatch.setattr(security, "hash_password", hash_then_meet)

    # One client for both threads: one lifespan, so the engine is not disposed under the other
    # request (a client per thread left a connection to be garbage collected, a ResourceWarning).
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:

        def attempt(i: int) -> int:
            return client.post(
                PASSWORD_URL,
                json={"current_password": PASSWORD, "new_password": news[i]},
                headers={**CSRF, "Cookie": f"{COOKIE_NAME}={tokens[i]}"},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(attempt, range(2)))
    assert sorted(codes) == [204, 401], codes
    winner = codes.index(204)
    # Only the winner's browser stays, and only the winner's password works.
    assert session_hashes(factory) == {security.hash_token(tokens[winner])}
    stored = user_row(factory).password_hash
    assert security.verify_password(stored, news[winner])
    assert not security.verify_password(stored, news[1 - winner])
    assert not security.verify_password(stored, PASSWORD)


# --- rename ------------------------------------------------------------------------------------


def test_rename_returns_the_user_and_shows_in_me(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    this, other = signed_in
    before = user_row(factory)
    response = rename(api, this, "  Harald   Thorvald\tHautin \n")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"id", "email", "name", "role", "created_at", "last_login_at"}
    assert body["name"] == "Harald Thorvald Hautin"
    assert api.me(other).json()["name"] == "Harald Thorvald Hautin"  # every browser sees it
    after = user_row(factory)
    assert after.updated_at > before.updated_at
    assert after.password_hash == before.password_hash
    assert session_hashes(factory) == {security.hash_token(this), security.hash_token(other)}


def test_rename_to_the_same_name_writes_nothing(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    before = user_row(factory).updated_at
    assert rename(api, signed_in[0], " Harald ").json()["name"] == "Harald"
    assert user_row(factory).updated_at == before


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("Zoë Ångström", "Zoë Ångström"),
        (unicodedata.normalize("NFD", "Zoë Ångström"), "Zoë Ångström"),  # stored NFC
        ("李小龍", "李小龍"),
        ("محمد علي", "محمد علي"),
        ("Ola 🙂 Nordmann", "Ola 🙂 Nordmann"),
        ("x" * 100, "x" * 100),
        ("'; drop table users; --", "'; drop table users; --"),
        ("<script>alert(1)</script>", "<script>alert(1)</script>"),
    ],
)
def test_rename_accepts_unicode_and_odd_but_valid_names(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], name: str, stored: str
) -> None:
    response = rename(api, signed_in[0], name)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == stored
    assert user_row(factory).name == stored
    assert unicodedata.is_normalized("NFC", user_row(factory).name)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "Enter your name."),
        ("   ", "Enter your name."),
        ("\t\n  ", "Enter your name."),  # unicode whitespace only
        ("x" * 101, "Use at most 100 characters for the name."),
        ("x" * 200, "Use at most 100 characters for the name."),
        ("é" * 101, "Use at most 100 characters for the name."),
        ("bad\x00name", "The name contains characters that are not valid."),
        ("bell\x07name", "The name contains characters that are not valid."),
        ("esc\x1b[31mred", "The name contains characters that are not valid."),
        ("del\x7fname", "The name contains characters that are not valid."),
    ],
)
def test_rename_refusals_are_422_and_change_nothing(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], name: str, message: str
) -> None:
    before = snapshot(factory)
    response = rename(api, signed_in[0], name)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": f"name: {message}"}
    assert snapshot(factory) == before


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": None}, {"name": 42}, {"name": "n" * 1025}, {"name": "A", "role": "admin"}],
)
def test_rename_malformed_bodies_are_422(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str], payload: object
) -> None:
    before = snapshot(factory)
    response = api.request("PATCH", ME, token=signed_in[0], json=payload)
    assert response.status_code == 422
    assert snapshot(factory) == before
    assert user_row(factory).role is Role.admin


def test_rename_cannot_touch_anyone_else(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    make_user(factory, email="friend@example.org", role=Role.member, name="Friend")
    friend = api.login_token("friend@example.org")
    assert rename(api, friend, "Renamed").json()["email"] == "friend@example.org"
    assert user_row(factory).name == "Harald"
    assert user_row(factory, "friend@example.org").name == "Renamed"


def test_rename_by_a_disabled_account_is_401(
    api: Api, factory: sessionmaker[Session], signed_in: tuple[str, str]
) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(User).where(User.email == EMAIL).values(is_active=False))
    assert rename(api, signed_in[0], "Ghost").status_code == 401
    assert user_row(factory).name == "Harald"


def test_rename_disabled_while_the_request_runs_is_401(
    api: Api,
    factory: sessionmaker[Session],
    signed_in: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = service.clean_name

    def clean_then_disable(raw: str | None) -> str:
        with factory() as db, db.begin():
            db.execute(sa.update(User).where(User.email == EMAIL).values(is_active=False))
        return real(raw)

    monkeypatch.setattr(service, "clean_name", clean_then_disable)
    assert rename(api, signed_in[0], "Ghost").status_code == 401
    assert user_row(factory).name == "Harald"


# --- service level -----------------------------------------------------------------------------


def test_service_change_returns_how_many_sessions_ended(
    factory: sessionmaker[Session], clock: Clock
) -> None:
    user = make_user(factory)
    with factory() as db, db.begin():
        mine = service.create_session(db, user, now=clock.now).row.id
        for _ in range(3):
            service.create_session(db, user, now=clock.now)
    with factory() as db, db.begin():
        ended = service.change_password(db, user.id, mine, PASSWORD, NEW, now=clock.now)
    assert ended == 3


def test_service_change_refuses_an_expired_own_session(
    factory: sessionmaker[Session], clock: Clock
) -> None:
    user = make_user(factory)
    with factory() as db, db.begin():
        mine = service.create_session(db, user, now=clock.now - timedelta(days=40)).row.id
    with factory() as db, pytest.raises(service.SessionEndedError):
        service.change_password(db, user.id, mine, PASSWORD, NEW, now=clock.now)


def test_router_docs_name_the_status_codes() -> None:
    assert router.CURRENT_PASSWORD_WRONG.startswith("current_password: ")
    for dash in (" - ", "—", "–"):
        assert dash not in router.CURRENT_PASSWORD_WRONG
        assert dash not in service.SAME_PASSWORD
