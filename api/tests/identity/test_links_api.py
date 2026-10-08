"""GET /api/auth/invites/{token} and POST /api/auth/invites/{token}/accept, against a real
Postgres: invite and reset links, one use, concurrent accepts."""

import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.identity import security, service
from app.identity.models import Invite, InviteKind, Role, User
from app.identity.models import Session as UserSession
from app.identity.router import LINK_INVALID
from tests.identity.api_helpers import (
    CSRF,
    PASSWORD,
    Api,
    Clock,
    issue_link,
    make_user,
    session_cookie,
)

FRIEND = "friend@example.org"
NEW_PASSWORD = "a brand new passphrase 7"


def accept(api: Api, token: str, **body: object) -> object:
    return api.post(f"/api/auth/invites/{token}/accept", json=body)


def invite_row(factory: sessionmaker[Session], email: str = FRIEND) -> Invite:
    with factory() as db:
        return db.scalars(
            sa.select(Invite).where(Invite.email == email).order_by(Invite.created_at.desc())
        ).first()  # type: ignore[return-value]


def users(factory: sessionmaker[Session]) -> list[User]:
    with factory() as db:
        return list(db.scalars(sa.select(User).order_by(User.created_at)))


def session_count(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(db.scalar(sa.select(sa.func.count()).select_from(UserSession)) or 0)


# --- check -------------------------------------------------------------------------------------


def test_check_a_valid_invite(api: Api, factory: sessionmaker[Session], clock: Clock) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    response = api.get(f"/api/auth/invites/{token}")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == FRIEND
    assert body["kind"] == "invite"
    assert set(body) == {"email", "kind", "expires_at"}
    # Checking does not use the link.
    assert api.get(f"/api/auth/invites/{token}").status_code == 200
    assert invite_row(factory).used_at is None


def test_check_a_reset_link(api: Api, factory: sessionmaker[Session], clock: Clock) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)
    response = api.get(f"/api/auth/invites/{token}")
    assert response.status_code == 200
    assert response.json()["kind"] == "reset"


@pytest.mark.parametrize("state", ["used", "revoked", "expired", "unknown", "superseded"])
def test_check_dead_links_all_answer_the_same_404(
    api: Api, factory: sessionmaker[Session], clock: Clock, state: str
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    if state == "used":
        with factory() as db, db.begin():
            db.execute(sa.update(Invite).values(used_at=clock.now))
    elif state == "revoked":
        with factory() as db, db.begin():
            db.execute(sa.update(Invite).values(revoked_at=clock.now))
    elif state == "expired":
        clock.advance(days=7)  # valid strictly before expires_at
    elif state == "unknown":
        token = security.new_token()
    elif state == "superseded":
        issue_link(factory, FRIEND, now=clock.now)  # a new link revokes the first
    response = api.get(f"/api/auth/invites/{token}")
    assert response.status_code == 404
    assert response.json() == {"detail": LINK_INVALID}


@pytest.mark.parametrize(
    "token",
    [
        "abc",
        "x" * 5000,
        "%E2%9C%93" * 15,
        "' OR 1=1 --",
        "a" * 42,
        "a" * 44,
        "%00" * 43,
        "..%5C..%5Cetc",
    ],
)
def test_check_malformed_tokens_answer_the_same_404(api: Api, token: str) -> None:
    response = api.get(f"/api/auth/invites/{token}")
    assert response.status_code == 404
    assert response.json() == {"detail": LINK_INVALID}


def test_accept_with_a_malformed_token_is_404(api: Api) -> None:
    for token in ("abc", "x" * 5000, security.new_token()):
        response = accept(api, token, name="Ann", password=NEW_PASSWORD)
        assert response.status_code == 404  # type: ignore[attr-defined]
        assert response.json() == {"detail": LINK_INVALID}  # type: ignore[attr-defined]


# --- accept an invite --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.admin, Role.member])
def test_accept_creates_the_user_with_the_links_role_and_signs_in(
    api: Api, factory: sessionmaker[Session], clock: Clock, role: Role
) -> None:
    token = issue_link(factory, FRIEND, role=role, now=clock.now)
    response = accept(api, token, name="  Ann   Smith ", password=NEW_PASSWORD)
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["email"] == FRIEND
    assert body["name"] == "Ann Smith"
    assert body["role"] == role.value

    [user] = users(factory)
    assert user.role is role
    assert user.is_active
    assert user.last_login_at == clock.now
    assert security.verify_password(user.password_hash, NEW_PASSWORD)
    assert invite_row(factory).used_at == clock.now

    morsel = session_cookie(response)  # type: ignore[arg-type]
    assert morsel is not None
    assert api.me(morsel.value).json()["email"] == FRIEND
    assert api.login(FRIEND, NEW_PASSWORD).status_code == 200


def test_the_cli_setup_link_makes_an_admin(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    # What `python -m app.identity.cli create-admin` issues: kind invite, role admin.
    with factory() as db, db.begin():
        issued = service.create_invite(db, "Harald@Example.org", InviteKind.invite, role=Role.admin)
    assert issued.path.startswith("/invite/")
    response = accept(api, issued.token, name="Harald", password=NEW_PASSWORD)
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert response.json()["role"] == "admin"  # type: ignore[attr-defined]
    assert response.json()["email"] == "harald@example.org"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("Élodie Dupré", "Élodie Dupré"),
        (unicodedata.normalize("NFD", "Élodie"), "Élodie"),  # stored composed
        ("山田 太郎", "山田 太郎"),
        ("  Ann\t\nLee  ", "Ann Lee"),
        ("Zoë 👋", "Zoë 👋"),
        ("Robert'); DROP TABLE users; --", "Robert'); DROP TABLE users; --"),
        ("n" * 100, "n" * 100),
    ],
)
def test_accept_names(
    api: Api, factory: sessionmaker[Session], clock: Clock, raw: str, stored: str
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    response = accept(api, token, name=raw, password=NEW_PASSWORD)
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert users(factory)[0].name == stored


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"password": NEW_PASSWORD}, "Enter your name."),
        ({"name": None, "password": NEW_PASSWORD}, "Enter your name."),
        ({"name": "", "password": NEW_PASSWORD}, "Enter your name."),
        ({"name": "   \t ", "password": NEW_PASSWORD}, "Enter your name."),
        ({"name": "n" * 101, "password": NEW_PASSWORD}, "Use at most 100 characters for the name."),
        (
            {"name": "Ann\x00Lee", "password": NEW_PASSWORD},
            "The name contains characters that are not valid.",
        ),
        (
            {"name": "Ann\x07", "password": NEW_PASSWORD},
            "The name contains characters that are not valid.",
        ),
        ({"name": "Ann", "password": "short"}, "Use at least 10 characters for the password."),
        ({"name": "Ann", "password": "x" * 129}, "Use at most 128 characters for the password."),
        (
            {"name": "Ann", "password": "password123"},
            "This password is too common. Choose one that is harder to guess.",
        ),
        (
            {"name": "Ann", "password": "Friend@Example.org"},
            "The password cannot be your email address.",
        ),
    ],
)
def test_accept_rule_errors_are_422_and_leave_the_link_usable(
    api: Api, factory: sessionmaker[Session], clock: Clock, body: dict[str, object], detail: str
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    response = api.post(f"/api/auth/invites/{token}/accept", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert session_cookie(response) is None
    assert users(factory) == []
    assert invite_row(factory).used_at is None
    retry = accept(api, token, name="Ann", password=NEW_PASSWORD)
    assert retry.status_code == 200  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "Ann"},
        {"name": "Ann", "password": ""},
        {"name": "Ann", "password": "p" * 1025},
        {"name": "n" * 1025, "password": NEW_PASSWORD},
        {"name": 5, "password": NEW_PASSWORD},
        {"name": "Ann", "password": NEW_PASSWORD, "role": "admin"},
        {"name": "Ann", "password": NEW_PASSWORD, "email": "other@example.org"},
    ],
)
def test_accept_schema_errors_are_422(
    api: Api, factory: sessionmaker[Session], clock: Clock, body: dict[str, object]
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    response = api.post(f"/api/auth/invites/{token}/accept", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert NEW_PASSWORD not in response.text
    assert users(factory) == []


def test_dead_link_wins_over_a_bad_body(api: Api) -> None:
    # An invalid link is a 404 whatever the form says: the form is never validated first.
    response = accept(api, security.new_token(), name="", password="short")
    assert response.status_code == 404  # type: ignore[attr-defined]


def test_accept_twice_second_is_404(api: Api, factory: sessionmaker[Session], clock: Clock) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    assert accept(api, token, name="Ann", password=NEW_PASSWORD).status_code == 200  # type: ignore[attr-defined]
    second = accept(api, token, name="Eve", password="another passphrase 8")
    assert second.status_code == 404  # type: ignore[attr-defined]
    assert second.json() == {"detail": LINK_INVALID}  # type: ignore[attr-defined]
    [user] = users(factory)
    assert user.name == "Ann"
    assert api.get(f"/api/auth/invites/{token}").status_code == 404


def test_accept_an_expired_or_revoked_link(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    clock.advance(days=7)
    assert accept(api, token, name="Ann", password=NEW_PASSWORD).status_code == 404  # type: ignore[attr-defined]
    clock.advance(days=-7, seconds=-1)
    with factory() as db, db.begin():
        db.execute(sa.update(Invite).values(revoked_at=clock.now))
    assert accept(api, token, name="Ann", password=NEW_PASSWORD).status_code == 404  # type: ignore[attr-defined]
    assert users(factory) == []


def test_accept_an_invite_for_an_existing_account_is_409(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    token = issue_link(factory, FRIEND.upper(), now=clock.now)
    response = accept(api, token, name="Ann", password=NEW_PASSWORD)
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "detail": "An account with this email already exists. Sign in instead."
    }
    assert invite_row(factory).used_at is None
    assert security.verify_password(users(factory)[0].password_hash, PASSWORD)


def test_ten_concurrent_accepts_exactly_one_wins(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    client: TestClient = api.client
    barrier = threading.Barrier(10)

    def attempt(index: int) -> int:
        barrier.wait()
        response = client.post(
            f"/api/auth/invites/{token}/accept",
            json={"name": f"Racer {index}", "password": f"racing passphrase {index:02d}"},
            headers=CSRF,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(attempt, range(10)))
    assert sorted(codes) == [200] + [404] * 9, codes
    assert len(users(factory)) == 1
    assert session_count(factory) == 1
    assert invite_row(factory).used_at is not None


def test_concurrent_accepts_in_the_service_serialise_on_the_row_lock(
    factory: sessionmaker[Session], clock: Clock
) -> None:
    # Without the HTTP layer: every worker has passed the unlocked lookup before any of them
    # takes the lock, so only SELECT ... FOR UPDATE with the recheck stops the others.
    token = issue_link(factory, FRIEND, now=clock.now)
    barrier = threading.Barrier(6)
    outcomes: list[str] = []
    lock = threading.Lock()

    original_hash = security.hash_password

    def slow_hash(password: str) -> str:
        result = original_hash(password)
        barrier.wait()  # all six have looked the link up and hashed before anyone locks
        return result

    def worker(index: int) -> None:
        with factory() as db:
            try:
                service.accept_invite(
                    db, token, f"parallel passphrase {index}", name=f"W{index}", now=clock.now
                )
                db.commit()
                outcome = "won"
            except service.InvalidLinkError:
                db.rollback()
                outcome = "invalid"
        with lock:
            outcomes.append(outcome)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(security, "hash_password", slow_hash)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert sorted(outcomes) == ["invalid"] * 5 + ["won"]
    assert len(users(factory)) == 1


# --- accept a reset link -----------------------------------------------------------------------


def test_reset_sets_the_password_ends_every_old_session_and_signs_in(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory, email=FRIEND, role=Role.member, name="Ann")
    laptop = api.login_token(FRIEND)
    phone = api.login_token(FRIEND)
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)

    # A name sent with a reset is ignored: the account keeps its own.
    response = accept(api, token, name="Mallory", password=NEW_PASSWORD)
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json()["name"] == "Ann"  # type: ignore[attr-defined]
    fresh = session_cookie(response)  # type: ignore[arg-type]
    assert fresh is not None

    assert api.me(laptop).status_code == 401
    assert api.me(phone).status_code == 401
    assert api.me(fresh.value).status_code == 200
    assert session_count(factory) == 1
    assert api.login(FRIEND, PASSWORD).status_code == 401
    assert api.login(FRIEND, NEW_PASSWORD).status_code == 200
    assert invite_row(factory).used_at == clock.now


def test_reset_does_not_touch_other_users(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    make_user(factory, email=FRIEND, role=Role.member)
    admin = api.login_token("harald@example.org")
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)
    assert accept(api, token, password=NEW_PASSWORD).status_code == 200  # type: ignore[attr-defined]
    assert api.me(admin).status_code == 200


def test_reset_password_rules_apply(api: Api, factory: sessionmaker[Session], clock: Clock) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)
    response = accept(api, token, password=FRIEND)
    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json() == {"detail": "The password cannot be your email address."}  # type: ignore[attr-defined]


@pytest.mark.parametrize("state", ["disabled", "deleted"])
def test_reset_for_a_disabled_or_missing_account_is_404(
    api: Api, factory: sessionmaker[Session], clock: Clock, state: str
) -> None:
    make_user(factory, email=FRIEND, role=Role.member, active=state != "disabled")
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)
    if state == "deleted":
        with factory() as db, db.begin():
            db.execute(sa.delete(User))
    response = accept(api, token, password=NEW_PASSWORD)
    assert response.status_code == 404  # type: ignore[attr-defined]
    assert response.json() == {"detail": LINK_INVALID}  # type: ignore[attr-defined]
    assert invite_row(factory).used_at is None


def test_reset_link_expires_after_one_hour(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    token = issue_link(factory, FRIEND, kind=InviteKind.reset, now=clock.now)
    clock.advance(hours=1)
    assert accept(api, token, password=NEW_PASSWORD).status_code == 404  # type: ignore[attr-defined]
    clock.advance(seconds=-1)
    assert accept(api, token, password=NEW_PASSWORD).status_code == 200  # type: ignore[attr-defined]


def test_accept_while_signed_in_replaces_that_browsers_session(
    api: Api, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory)
    admin = api.login_token("harald@example.org")
    token = issue_link(factory, FRIEND, now=clock.now)
    response = api.post(
        f"/api/auth/invites/{token}/accept",
        json={"name": "Ann", "password": NEW_PASSWORD},
        token=admin,
    )
    assert response.status_code == 200
    assert api.me(admin).status_code == 401  # this browser now belongs to the new account
    assert session_count(factory) == 1


def test_service_clean_name() -> None:
    assert service.clean_name(" a  b ") == "a b"
    with pytest.raises(service.AcceptError) as info:
        service.clean_name(None)
    assert info.value.code == "name_required"
    assert timedelta(hours=1) == service.RESET_TTL
