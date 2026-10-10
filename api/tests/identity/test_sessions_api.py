"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me and the session cookie,
against a real Postgres."""

import unicodedata
from datetime import timedelta

import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import Settings
from app.identity import security, service
from app.identity.deps import COOKIE_NAME, UNAUTHORIZED
from app.identity.models import Role, User
from app.identity.models import Session as UserSession
from app.identity.router import LOGIN_FAILED
from app.main import create_app
from tests.identity.api_helpers import PASSWORD, Api, Clock, make_user, session_cookie

EMAIL = "harald@example.org"
THIRTY_DAYS = 30 * 24 * 3600


def sessions_of(factory: sessionmaker[Session], email: str = EMAIL) -> list[UserSession]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(UserSession)
                .join(User, User.id == UserSession.user_id)
                .where(User.email == email)
                .order_by(UserSession.created_at)
            )
        )


def user_row(factory: sessionmaker[Session], email: str = EMAIL) -> User:
    with factory() as db:
        return db.scalars(sa.select(User).where(User.email == email)).one()


# --- login -------------------------------------------------------------------------------------


def test_login_ok_sets_cookie_and_returns_the_user(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    response = api.login(EMAIL, headers={"User-Agent": "pytest-agent"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "email", "name", "role", "created_at", "last_login_at"}
    assert body["email"] == EMAIL
    assert body["role"] == "admin"
    assert body["name"] == "Harald"
    assert "password" not in response.text
    assert response.headers["cache-control"] == "no-store"

    morsel = session_cookie(response)
    assert morsel is not None
    assert security.is_well_formed_token(morsel.value)
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert int(morsel["max-age"]) == THIRTY_DAYS
    assert not morsel["secure"]  # plain http, development

    [row] = sessions_of(factory)
    assert row.token_hash == security.hash_token(morsel.value)  # only the hash is stored
    assert row.expires_at == clock.now + timedelta(days=30)
    assert row.last_seen_at == clock.now
    assert row.user_agent == "pytest-agent"
    assert row.ip is None  # "testclient" is not an address
    assert user_row(factory).last_login_at == clock.now


@pytest.mark.parametrize(
    "case", ["wrong_password", "unknown_email", "disabled", "invalid_email", "sql_like"]
)
def test_login_failures_are_identical(api: Api, factory: sessionmaker[Session], case: str) -> None:
    make_user(factory)
    make_user(factory, email="off@example.org", active=False)
    email, password = {
        "wrong_password": (EMAIL, "not the password at all"),
        "unknown_email": ("nobody@example.org", PASSWORD),
        "disabled": ("off@example.org", PASSWORD),  # right password, disabled account
        "invalid_email": ("not an email", PASSWORD),
        "sql_like": ("' OR 1=1; --@example.org", "' OR '1'='1"),
    }[case]
    response = api.login(email, password)
    assert response.status_code == 401
    assert response.json() == {"detail": LOGIN_FAILED}
    assert session_cookie(response) is None
    assert sessions_of(factory) == []
    assert sessions_of(factory, "off@example.org") == []
    assert user_row(factory).last_login_at is None


@pytest.mark.parametrize(
    "email", ["HARALD@EXAMPLE.ORG", "  Harald@Example.org\n", "harald@example.ORG"]
)
def test_login_email_is_case_insensitive_and_trimmed(
    api: Api, factory: sessionmaker[Session], email: str
) -> None:
    make_user(factory)
    assert api.login(email).status_code == 200


def test_login_unicode_password_any_normal_form(api: Api, factory: sessionmaker[Session]) -> None:
    password = "Grüße aus Bordeaux 🔑 ÉTÉ"
    make_user(factory, password=password)
    assert api.login(EMAIL, unicodedata.normalize("NFC", password)).status_code == 200
    assert api.login(EMAIL, unicodedata.normalize("NFD", password)).status_code == 200
    assert api.login(EMAIL, password.lower()).status_code == 401


@pytest.mark.parametrize(
    ("payload", "where"),
    [
        ({}, "email"),
        ({"email": EMAIL}, "password"),
        ({"password": "secret value 123"}, "email"),
        ({"email": "", "password": "secret value 123"}, "email"),
        ({"email": EMAIL, "password": ""}, "password"),
        ({"email": None, "password": "secret value 123"}, "email"),
        ({"email": 42, "password": "secret value 123"}, "email"),
        ({"email": EMAIL, "password": "secret value 123", "remember": True}, "remember"),
        ({"email": "x" * 10_000, "password": "secret value 123"}, "email"),
        ({"email": EMAIL, "password": "p" * 1025}, "password"),
    ],
)
def test_login_invalid_bodies_are_422_without_echo(
    api: Api, payload: dict[str, object], where: str
) -> None:
    response = api.post("/api/auth/login", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith(where)
    assert "secret value 123" not in response.text
    for dash in (" - ", "—", "–"):
        assert dash not in detail


@pytest.mark.parametrize(
    "content",
    [b"", b"not json", b"[]", b'"string"', b"{", b'{"email": "\\ud800"}'],
)
def test_login_malformed_json_is_422(api: Api, content: bytes) -> None:
    response = api.post(
        "/api/auth/login", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("a" * 300 + "@example.org", PASSWORD),  # long but under the schema limit
        (EMAIL, "p" * 1024),  # longest accepted input, longer than any valid password
        ("nul\x00@example.org", PASSWORD),  # Postgres text cannot hold NUL
        (EMAIL, "pass\x00word with nul"),
        ("émoji🙂@exämple.org", "🙂" * 50),
    ],
)
def test_login_odd_values_are_401_never_500(
    api: Api, factory: sessionmaker[Session], email: str, password: str
) -> None:
    make_user(factory)
    response = api.login(email, password)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": LOGIN_FAILED}


@pytest.mark.parametrize(
    "body",
    [
        b'{"email": "harald@example.org", "password": "\\ud800 lone surrogate pass"}',
        b'{"email": "\\ud800@example.org", "password": "correct horse battery 42"}',
    ],
)
def test_login_lone_surrogates_are_422(
    api: Api, factory: sessionmaker[Session], body: bytes
) -> None:
    # Valid JSON escapes that are not valid unicode text: refused by the schema, never a 500.
    make_user(factory)
    response = api.post(
        "/api/auth/login", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert "lone surrogate" not in response.text


def test_login_rehashes_an_outdated_hash(api: Api, factory: sessionmaker[Session]) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    assert security.needs_rehash(weak)
    make_user(factory, password_hash=weak)
    assert api.login(EMAIL).status_code == 200
    stored = user_row(factory).password_hash
    assert stored != weak
    assert not security.needs_rehash(stored)
    assert security.verify_password(stored, PASSWORD)
    # A failed attempt never rewrites the hash.
    assert api.login(EMAIL, "wrong password here").status_code == 401
    assert user_row(factory).password_hash == stored


def test_login_again_from_the_same_browser_replaces_its_session(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    first = api.login_token(EMAIL)
    second_response = api.login(EMAIL, token=first)
    second = session_cookie(second_response)
    assert second is not None
    assert second.value != first
    assert [row.token_hash for row in sessions_of(factory)] == [security.hash_token(second.value)]
    assert api.me(first).status_code == 401
    assert api.me(second.value).status_code == 200


# --- cookie flags ------------------------------------------------------------------------------


def test_cookie_is_secure_over_https(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    https = {"X-Forwarded-Proto": "https"}
    response = api.login(EMAIL, headers=https)
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert api.me(morsel.value, headers=https).status_code == 200
    cleared = session_cookie(api.post("/api/auth/logout", token=morsel.value, headers=https))
    assert cleared is not None
    assert cleared["secure"] is True
    assert cleared.value == ""


def test_cookie_is_secure_in_production_even_over_http(
    migrated_url: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    settings = Settings(
        database_url=migrated_url,  # type: ignore[arg-type]
        app_env="production",
        secret_key="s" * 40,  # type: ignore[arg-type]
        credentials_key="A" * 43,  # type: ignore[arg-type]
    )
    app = create_app(settings, clock=clock)
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:
        response = Api(client).login(EMAIL)
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel["secure"] is True


def test_logout_clears_the_cookie_with_the_same_flags(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    response = api.post("/api/auth/logout", token=token)
    assert response.status_code == 204
    assert response.content == b""
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel.value == ""
    assert int(morsel["max-age"]) == 0
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"


# --- me ----------------------------------------------------------------------------------------


def test_me_with_a_valid_session(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory, role=Role.member, name="Élodie")
    token = api.login_token(EMAIL)
    response = api.me(token)
    assert response.status_code == 200
    assert response.json()["name"] == "Élodie"
    assert response.json()["role"] == "member"
    assert session_cookie(response) is None  # no refresh within the day


def test_me_without_cookie(api: Api) -> None:
    response = api.me(None)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    assert session_cookie(response) is None


@pytest.mark.parametrize(
    "token",
    [
        "abc",
        "",
        "x" * 4000,
        security.new_token(),  # well formed, unknown
        security.new_token()[:-1] + "!",
        "a" * 43,
        "%00" * 15,
        '"quoted value"',
    ],
    # Fixed ids: random tokens in the id would differ between pytest-xdist workers.
    ids=["short", "empty", "4000-chars", "unknown", "bad-char", "43-same", "nul", "quoted"],
)
def test_me_with_a_bad_or_malformed_cookie(
    api: Api, factory: sessionmaker[Session], token: str
) -> None:
    make_user(factory)
    live = api.login_token(EMAIL)
    response = api.me(token)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    if token:
        cleared = session_cookie(response)
        assert cleared is not None
        assert cleared.value == ""
    assert api.me(live).status_code == 200  # nothing else was touched


def test_me_with_an_expired_session_deletes_it(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    clock.advance(days=30)  # expires_at == now: no longer valid
    response = api.me(token)
    assert response.status_code == 401
    assert session_cookie(response).value == ""  # type: ignore[union-attr]
    assert sessions_of(factory) == []


def test_me_just_before_expiry_still_works(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    clock.advance(days=30, seconds=-1)
    assert api.me(token).status_code == 200


def test_session_of_a_disabled_user_is_refused_and_deleted(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    first = api.login_token(EMAIL)
    second = api.login_token(EMAIL)
    with factory() as db, db.begin():
        db.execute(sa.update(User).values(is_active=False))
    assert api.me(first).status_code == 401
    assert sessions_of(factory) == []  # every session of that user ended
    assert api.me(second).status_code == 401
    with factory() as db, db.begin():
        db.execute(sa.update(User).values(is_active=True))
    assert api.me(second).status_code == 401  # re-enabling does not revive them


def test_session_survives_a_password_hash_change_only_by_design(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # Ending sessions on a password change is the job of the password and reset endpoints
    # (I6 and accept); a direct hash update does not touch sessions.
    make_user(factory)
    token = api.login_token(EMAIL)
    with factory() as db, db.begin():
        db.execute(sa.update(User).values(password_hash=security.hash_password("another pass 99")))
    assert api.me(token).status_code == 200


def test_deleted_user_session_is_gone(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    with factory() as db, db.begin():
        db.execute(sa.delete(User))
    assert api.me(token).status_code == 401


# --- logout ------------------------------------------------------------------------------------


def test_logout_ends_only_this_session(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    laptop = api.login_token(EMAIL)
    phone = api.login_token(EMAIL)
    assert len(sessions_of(factory)) == 2
    assert api.post("/api/auth/logout", token=laptop).status_code == 204
    assert api.me(laptop).status_code == 401
    assert api.me(phone).status_code == 200
    [left] = sessions_of(factory)
    assert left.token_hash == security.hash_token(phone)


@pytest.mark.parametrize(
    "token", [None, "garbage", security.new_token()], ids=["none", "garbage", "unknown"]
)
def test_logout_without_a_session_is_idempotent(api: Api, token: str | None) -> None:
    for _ in range(2):
        response = api.post("/api/auth/logout", token=token)
        assert response.status_code == 204
        assert session_cookie(response).value == ""  # type: ignore[union-attr]


def test_logout_twice_with_the_same_cookie(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    assert api.post("/api/auth/logout", token=token).status_code == 204
    assert api.post("/api/auth/logout", token=token).status_code == 204
    assert sessions_of(factory) == []


def test_logout_of_another_users_session_is_not_possible_without_its_token(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    make_user(factory, email="friend@example.org", role=Role.member)
    mine = api.login_token(EMAIL)
    theirs = api.login_token("friend@example.org")
    assert api.post("/api/auth/logout", token=mine).status_code == 204
    assert api.me(theirs).status_code == 200


# --- sliding refresh ---------------------------------------------------------------------------


def test_sliding_refresh_at_most_once_a_day(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    start = clock.now
    token = api.login_token(EMAIL)

    clock.advance(hours=23, minutes=59)
    response = api.me(token)
    assert response.status_code == 200
    assert session_cookie(response) is None
    [row] = sessions_of(factory)
    assert row.last_seen_at == start
    assert row.expires_at == start + timedelta(days=30)

    clock.advance(minutes=1)  # exactly one day after the last refresh
    response = api.me(token)
    assert response.status_code == 200
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel.value == token  # same session, cookie lifetime renewed
    assert int(morsel["max-age"]) == THIRTY_DAYS
    assert morsel["httponly"] is True
    [row] = sessions_of(factory)
    assert row.last_seen_at == clock.now
    assert row.expires_at == clock.now + timedelta(days=30)

    clock.advance(hours=12)
    assert session_cookie(api.me(token)) is None
    [row] = sessions_of(factory)
    assert row.last_seen_at == start + timedelta(days=1)


def test_sliding_refresh_keeps_an_active_session_beyond_thirty_days(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    for _ in range(4):
        clock.advance(days=20)
        assert api.me(token).status_code == 200
    # 80 days after sign in, still valid; 30 idle days later it is gone.
    clock.advance(days=30)
    assert api.me(token).status_code == 401


def test_refresh_also_happens_on_routes_that_return_a_response(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    # The cookie is written by middleware, so it reaches the browser whatever the handler
    # returns (logout returns a bare Response; health a JSONResponse).
    make_user(factory)
    token = api.login_token(EMAIL)
    clock.advance(days=2)
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(UserSession)) == 1
    response = api.post("/api/auth/logout", token=token)
    assert response.status_code == 204
    assert session_cookie(response).value == ""  # type: ignore[union-attr]


def test_service_resolve_session_rejects_malformed_tokens(db: Session) -> None:
    assert service.resolve_session(db, "") is None
    assert service.resolve_session(db, "short") is None
    assert service.delete_session_by_token(db, "short") is False


def test_cookie_name_and_lifetime_constants() -> None:
    assert COOKIE_NAME == "jobapp_session"
    assert timedelta(days=30) == service.SESSION_TTL
    assert timedelta(days=1) == service.SESSION_REFRESH_AFTER
