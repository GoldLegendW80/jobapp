"""/api/admin/invites against a real Postgres: who may call it, creating invite and reset links,
listing them with their status, revoking them, and the token appearing once only."""

import inspect
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.identity import admin_router, security
from app.identity.deps import COOKIE_NAME, CSRF_REFUSED, UNAUTHORIZED, _calls, require_admin
from app.identity.models import Invite, InviteKind, Role, User
from app.identity.router import LINK_INVALID
from tests.identity.api_helpers import CSRF, Api, Clock, issue_link, make_user

ADMIN = "harald@example.org"
MEMBER = "member@example.org"
FRIEND = "friend@example.org"
INVITES = "/api/admin/invites"
LINK = re.compile(r"^/(invite|reset)/([A-Za-z0-9_-]{43})$")
LIST_KEYS = {
    "id",
    "email",
    "kind",
    "role",
    "status",
    "created_by_name",
    "created_at",
    "expires_at",
    "used_at",
    "revoked_at",
}


@pytest.fixture
def admin(api: Api, factory: sessionmaker[Session]) -> str:
    """The session token of a signed in admin named Harald."""
    make_user(factory, email=ADMIN, role=Role.admin, name="Harald")
    return api.login_token(ADMIN)


@pytest.fixture
def member(api: Api, factory: sessionmaker[Session]) -> str:
    make_user(factory, email=MEMBER, role=Role.member, name="Mia")
    return api.login_token(MEMBER)


def create(api: Api, token: str | None, **body: object) -> object:
    return api.post(INVITES, token=token, json=body)


def created_token(response: object) -> str:
    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    match = LINK.match(response.json()["path"])  # type: ignore[attr-defined]
    assert match, response.json()  # type: ignore[attr-defined]
    return match.group(2)


def listing(api: Api, token: str, **params: object) -> dict:  # type: ignore[type-arg]
    response = api.get(INVITES, token=token, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def invites(factory: sessionmaker[Session], email: str = FRIEND) -> list[Invite]:
    with factory() as db:
        return list(
            db.scalars(sa.select(Invite).where(Invite.email == email).order_by(Invite.created_at))
        )


def check(api: Api, token: str) -> int:
    return api.get(f"/api/auth/invites/{token}").status_code


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


# --- who may call ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", INVITES),
        ("POST", INVITES),
        ("DELETE", f"{INVITES}/{'0' * 8}-0000-0000-0000-{'0' * 12}"),
    ],
)
def test_anonymous_is_401(api: Api, method: str, path: str) -> None:
    response = api.request(method, path, json={"email": FRIEND})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", INVITES), ("POST", INVITES), ("DELETE", f"{INVITES}/not-even-a-uuid")],
)
def test_member_gets_the_same_404_as_an_unknown_route(
    api: Api, member: str, method: str, path: str
) -> None:
    response = api.request(method, path, token=member, json={"email": FRIEND})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert response.json() == api.get("/api/admin/nothing-here", token=member).json()


def test_member_cannot_create_even_with_a_valid_body(
    api: Api, member: str, factory: sessionmaker[Session]
) -> None:
    assert create(api, member, email=FRIEND, role="admin").status_code == 404  # type: ignore[attr-defined]
    assert invites(factory) == []


def test_a_disabled_admin_is_signed_out(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(User).where(User.email == ADMIN).values(is_active=False))
    assert api.get(INVITES, token=admin).status_code == 401
    assert create(api, admin, email=FRIEND).status_code == 401  # type: ignore[attr-defined]


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_csrf_header_is_required_even_for_an_admin(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock, method: str
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    invite_id = invites(factory)[0].id
    path = INVITES if method == "POST" else f"{INVITES}/{invite_id}"
    response = api.request(method, path, token=admin, csrf=False, json={"email": "x@example.org"})
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert invites(factory, "x@example.org") == []
    assert check(api, token) == 200  # not revoked


def test_admin_routes_are_served_and_all_run_the_admin_check(app: FastAPI) -> None:
    served = set()
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        path = context.path or ""
        if not path.startswith("/api/admin"):
            continue
        assert require_admin in set(_calls(context.dependant)), path
        served.update((method, path) for method in context.methods or ())
    assert served == {
        ("GET", INVITES),
        ("POST", INVITES),
        ("DELETE", f"{INVITES}/{{invite_id}}"),
    }
    for handler in (
        admin_router.list_invites,
        admin_router.create_invite,
        admin_router.revoke_invite,
    ):
        # Plain def: the threadpool, never the event loop.
        assert not inspect.iscoroutinefunction(handler)


# --- create ------------------------------------------------------------------------------------


def test_create_an_invite_returns_the_link_once(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    response = create(api, admin, email=FRIEND)
    token = created_token(response)
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"invite", "path"}
    assert body["path"] == f"/invite/{token}"
    invite = body["invite"]
    assert set(invite) == LIST_KEYS
    assert invite["email"] == FRIEND
    assert invite["kind"] == "invite"
    assert invite["role"] == "member"
    assert invite["status"] == "active"
    assert invite["created_by_name"] == "Harald"
    assert parse(invite["created_at"]) == clock.now
    assert parse(invite["expires_at"]) == clock.now + timedelta(days=7)
    assert invite["used_at"] is None
    assert invite["revoked_at"] is None
    assert response.headers["cache-control"] == "no-store"  # type: ignore[attr-defined]

    # Stored as a hash only, made by this admin.
    (row,) = invites(factory)
    assert row.token_hash == security.hash_token(token)
    with factory() as db:
        assert row.created_by == db.scalar(sa.select(User.id).where(User.email == ADMIN))

    # The link works, and the friend becomes a member.
    assert api.get(f"/api/auth/invites/{token}").json()["email"] == FRIEND
    accepted = api.post(
        f"/api/auth/invites/{token}/accept", json={"name": "Friend", "password": "friendly pass 77"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "member"


def test_create_an_admin_invite(api: Api, admin: str) -> None:
    response = create(api, admin, email=FRIEND, kind="invite", role="admin")
    token = created_token(response)
    assert response.json()["invite"]["role"] == "admin"  # type: ignore[attr-defined]
    accepted = api.post(
        f"/api/auth/invites/{token}/accept",
        json={"name": "Co admin", "password": "co admin pass 9"},
    )
    assert accepted.json()["role"] == "admin"


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("  Friend@Example.ORG ", "friend@example.org"),
        ("zoë@exämple.org", "zoë@exämple.org"),
    ],
)
def test_create_normalises_the_email(api: Api, admin: str, raw: str, stored: str) -> None:
    response = create(api, admin, email=raw)
    assert response.status_code == 201  # type: ignore[attr-defined]
    assert response.json()["invite"]["email"] == stored  # type: ignore[attr-defined]


@pytest.mark.parametrize("email", [ADMIN, ADMIN.upper(), f"  {ADMIN}  "])
def test_invite_for_an_existing_account_is_409(
    api: Api, admin: str, factory: sessionmaker[Session], email: str
) -> None:
    response = create(api, admin, email=email)
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json() == {"detail": admin_router.ACCOUNT_EXISTS}  # type: ignore[attr-defined]
    assert invites(factory, ADMIN) == []


def test_invite_for_a_disabled_account_is_409_too(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    make_user(factory, email=FRIEND, role=Role.member, active=False)
    assert create(api, admin, email=FRIEND).status_code == 409  # type: ignore[attr-defined]


def test_invite_after_the_friend_accepted_is_409(api: Api, admin: str) -> None:
    token = created_token(create(api, admin, email=FRIEND))
    api.post(
        f"/api/auth/invites/{token}/accept", json={"name": "F", "password": "friendly pass 77"}
    )
    assert create(api, admin, email=FRIEND).status_code == 409  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "email",
    [
        "",
        " ",
        "nope",
        "a@",
        "@example.org",
        "a b@example.org",
        "a@@example.org",
        "friend@example",
        "' OR 1=1 --@example.org",
        "x" * 300 + "@example.org",
        "x" * 513,
        "friend@example.org\x00",
    ],
)
def test_invalid_email_is_422(
    api: Api, admin: str, factory: sessionmaker[Session], email: str
) -> None:
    response = create(api, admin, email=email)
    assert response.status_code == 422  # type: ignore[attr-defined]
    detail = response.json()["detail"]  # type: ignore[attr-defined]
    assert isinstance(detail, str)
    assert email.strip() == "" or email not in detail  # never echoed
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": None},
        {"email": 42},
        {"email": FRIEND, "kind": "signup"},
        {"email": FRIEND, "role": "owner"},
        {"email": FRIEND, "role": "ADMIN"},
        {"email": FRIEND, "expires_at": "2099-01-01T00:00:00Z"},
        {"email": FRIEND, "token": "x" * 43},
    ],
)
def test_bad_bodies_are_422(api: Api, admin: str, body: dict) -> None:  # type: ignore[type-arg]
    assert api.post(INVITES, token=admin, json=body).status_code == 422


def test_create_a_reset_link_for_an_active_user(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory, email=FRIEND, role=Role.member, name="Friend")
    response = create(api, admin, email=FRIEND.upper(), kind="reset")
    token = created_token(response)
    body = response.json()  # type: ignore[attr-defined]
    assert body["path"] == f"/reset/{token}"
    assert body["invite"]["kind"] == "reset"
    assert body["invite"]["role"] is None
    assert parse(body["invite"]["expires_at"]) == clock.now + timedelta(hours=1)
    assert api.get(f"/api/auth/invites/{token}").json()["kind"] == "reset"
    reset = api.post(f"/api/auth/invites/{token}/accept", json={"password": "a new passphrase 5"})
    assert reset.status_code == 200
    assert reset.json()["email"] == FRIEND


@pytest.mark.parametrize("state", ["unknown", "disabled"])
def test_reset_for_an_unknown_or_disabled_account_is_404(
    api: Api, admin: str, factory: sessionmaker[Session], state: str
) -> None:
    if state == "disabled":
        make_user(factory, email=FRIEND, role=Role.member, active=False)
    response = create(api, admin, email=FRIEND, kind="reset")
    assert response.status_code == 404  # type: ignore[attr-defined]
    assert response.json() == {"detail": admin_router.NO_ACTIVE_ACCOUNT}  # type: ignore[attr-defined]
    assert invites(factory) == []


def test_reset_with_a_role_is_422(api: Api, admin: str, factory: sessionmaker[Session]) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    for role in ("member", "admin"):
        response = create(api, admin, email=FRIEND, kind="reset", role=role)
        assert response.status_code == 422  # type: ignore[attr-defined]
        assert response.json() == {"detail": admin_router.ROLE_ON_RESET}  # type: ignore[attr-defined]


def test_admin_can_make_a_reset_link_for_themselves(api: Api, admin: str) -> None:
    created_token(create(api, admin, email=ADMIN, kind="reset"))


def test_a_new_invite_revokes_the_previous_unused_one(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    first = created_token(create(api, admin, email=FRIEND))
    clock.advance(minutes=5)
    second = created_token(create(api, admin, email=FRIEND.upper(), role="admin"))
    assert check(api, first) == 404
    assert api.get(f"/api/auth/invites/{first}").json() == {"detail": LINK_INVALID}
    assert check(api, second) == 200
    rows = invites(factory)
    assert [r.revoked_at for r in rows] == [clock.now, None]
    statuses = [item["status"] for item in listing(api, admin)["items"]]
    assert statuses == ["active", "revoked"]


def test_a_new_link_only_revokes_the_same_email_and_kind(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    make_user(factory, email=MEMBER, role=Role.member)
    other_invite = created_token(create(api, admin, email="other@example.org"))
    reset = created_token(create(api, admin, email=MEMBER, kind="reset"))
    created_token(create(api, admin, email=FRIEND))
    created_token(create(api, admin, email=FRIEND))
    assert check(api, other_invite) == 200
    assert check(api, reset) == 200


def test_ten_parallel_creates_for_one_email_leave_exactly_one_active(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    client: TestClient = api.client
    barrier = threading.Barrier(10)

    def attempt(index: int) -> tuple[int, str]:
        barrier.wait()
        response = client.post(
            INVITES,
            json={"email": FRIEND if index % 2 else FRIEND.upper()},
            headers={**CSRF, "Cookie": f"{COOKIE_NAME}={admin}"},
        )
        return response.status_code, response.json().get("path", "")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))
    assert [code for code, _ in results] == [201] * 10, results
    rows = invites(factory)
    assert len(rows) == 10
    assert sum(1 for r in rows if r.revoked_at is None) == 1
    tokens = [LINK.match(path).group(2) for _, path in results]  # type: ignore[union-attr]
    assert sorted(check(api, token) for token in tokens) == [200] + [404] * 9
    assert [i["status"] for i in listing(api, admin)["items"]].count("active") == 1


# --- list --------------------------------------------------------------------------------------


def test_list_is_empty_at_first(api: Api, admin: str) -> None:
    assert listing(api, admin) == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_computes_every_status_newest_first(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    start = clock.now
    # From the command line: no creator.
    cli = issue_link(factory, "cli@example.org", role=Role.admin, now=clock.now)
    clock.advance(minutes=1)
    used = created_token(create(api, admin, email="used@example.org"))
    api.post(
        f"/api/auth/invites/{used}/accept", json={"name": "U", "password": "used passphrase 1"}
    )
    clock.advance(minutes=1)
    revoked = create(api, admin, email="revoked@example.org")
    revoked_id = revoked.json()["invite"]["id"]  # type: ignore[attr-defined]
    assert api.request("DELETE", f"{INVITES}/{revoked_id}", token=admin).status_code == 204
    clock.advance(minutes=1)
    created_token(create(api, admin, email="active@example.org"))
    make_user(factory, email=MEMBER, role=Role.member)
    clock.advance(minutes=1)
    created_token(create(api, admin, email=MEMBER, kind="reset"))

    items = listing(api, admin)["items"]
    assert [(i["email"], i["status"]) for i in items] == [
        (MEMBER, "active"),
        ("active@example.org", "active"),
        ("revoked@example.org", "revoked"),
        ("used@example.org", "used"),
        ("cli@example.org", "active"),
    ]
    assert all(set(i) == LIST_KEYS for i in items)
    assert items[-1]["created_by_name"] is None
    assert items[-1]["role"] == "admin"
    assert items[0]["role"] is None  # a reset
    assert items[3]["used_at"] is not None
    assert items[2]["revoked_at"] is not None
    assert check(api, cli) == 200

    # An hour later the reset link has expired, exactly at its expiry.
    clock.now = start + timedelta(minutes=4, hours=1)
    assert listing(api, admin)["items"][0]["status"] == "expired"
    clock.now -= timedelta(microseconds=1)
    assert listing(api, admin)["items"][0]["status"] == "active"
    # After 7 days every unused invite has expired; used and revoked keep their status.
    clock.now = start + timedelta(days=8)
    statuses = [i["status"] for i in listing(api, admin)["items"]]
    assert statuses == ["expired", "expired", "revoked", "used", "expired"]


def test_list_pages(api: Api, admin: str, clock: Clock) -> None:
    emails = [f"friend{n}@example.org" for n in range(5)]
    for email in emails:
        created_token(create(api, admin, email=email))
        clock.advance(seconds=1)
    newest_first = list(reversed(emails))
    first = listing(api, admin, limit=2)
    assert [i["email"] for i in first["items"]] == newest_first[:2]
    assert (first["total"], first["limit"], first["offset"]) == (5, 2, 0)
    second = listing(api, admin, limit=2, offset=2)
    assert [i["email"] for i in second["items"]] == newest_first[2:4]
    last = listing(api, admin, limit=2, offset=4)
    assert [i["email"] for i in last["items"]] == newest_first[4:]
    assert listing(api, admin, offset=99)["items"] == []
    assert listing(api, admin, offset=99)["total"] == 5


def test_list_pages_never_overlap_when_links_share_a_timestamp(api: Api, admin: str) -> None:
    for n in range(6):  # frozen clock: same created_at for all
        created_token(create(api, admin, email=f"same{n}@example.org"))
    seen = [
        i["id"]
        for offset in (0, 2, 4)
        for i in listing(api, admin, limit=2, offset=offset)["items"]
    ]
    assert len(set(seen)) == 6


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"offset": -1},
        {"limit": "ten"},
        {"offset": 10**7},
    ],
)
def test_list_refuses_bad_paging(api: Api, admin: str, params: dict) -> None:  # type: ignore[type-arg]
    assert api.get(INVITES, token=admin, params=params).status_code == 422


def test_list_shows_no_name_once_the_creator_is_gone(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    created_token(create(api, admin, email=FRIEND))
    make_user(factory, email="second@example.org", role=Role.admin, name="Second")
    second = api.login_token("second@example.org")
    with factory() as db, db.begin():
        db.execute(sa.delete(User).where(User.email == ADMIN))
    (item,) = listing(api, second)["items"]
    assert item["created_by_name"] is None
    assert item["status"] == "active"


def test_tokens_never_appear_in_list_answers(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    tokens = [created_token(create(api, admin, email=f"t{n}@example.org")) for n in range(3)]
    tokens.append(issue_link(factory, "cli@example.org", now=clock.now))
    text = api.get(INVITES, token=admin).text
    hashes = [security.hash_token(t) for t in tokens]
    for token, digest in zip(tokens, hashes, strict=True):
        assert token not in text
        assert digest.hex() not in text
    assert "token" not in text
    assert "/invite/" not in text
    assert "/reset/" not in text


# --- revoke ------------------------------------------------------------------------------------


def revoke(api: Api, token: str, invite_id: object) -> object:
    return api.request("DELETE", f"{INVITES}/{invite_id}", token=token)


def test_revoke_an_active_invite(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    response = create(api, admin, email=FRIEND)
    token = created_token(response)
    invite_id = response.json()["invite"]["id"]  # type: ignore[attr-defined]
    clock.advance(minutes=3)
    answer = revoke(api, admin, invite_id)
    assert answer.status_code == 204  # type: ignore[attr-defined]
    assert answer.content == b""  # type: ignore[attr-defined]
    assert check(api, token) == 404
    accept = api.post(
        f"/api/auth/invites/{token}/accept", json={"name": "F", "password": "friendly pass 77"}
    )
    assert accept.status_code == 404
    (item,) = listing(api, admin)["items"]
    assert item["status"] == "revoked"
    assert parse(item["revoked_at"]) == clock.now
    # Idempotent in effect, explicit in answer: the second revoke finds nothing active.
    again = revoke(api, admin, invite_id)
    assert again.status_code == 404  # type: ignore[attr-defined]
    assert again.json() == {"detail": admin_router.INVITE_NOT_REVOCABLE}  # type: ignore[attr-defined]
    assert invites(factory)[0].revoked_at == clock.now  # unchanged by the second call
    # A fresh invite for the same email is fine afterwards.
    created_token(create(api, admin, email=FRIEND))


def test_revoke_a_reset_link(api: Api, admin: str, factory: sessionmaker[Session]) -> None:
    make_user(factory, email=FRIEND, role=Role.member)
    response = create(api, admin, email=FRIEND, kind="reset")
    token = created_token(response)
    assert revoke(api, admin, response.json()["invite"]["id"]).status_code == 204  # type: ignore[attr-defined]
    assert check(api, token) == 404


def test_revoke_a_used_invite_is_404_and_changes_nothing(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    response = create(api, admin, email=FRIEND)
    token = created_token(response)
    api.post(
        f"/api/auth/invites/{token}/accept", json={"name": "F", "password": "friendly pass 77"}
    )
    answer = revoke(api, admin, response.json()["invite"]["id"])  # type: ignore[attr-defined]
    assert answer.status_code == 404  # type: ignore[attr-defined]
    (row,) = invites(factory)
    assert row.revoked_at is None
    assert listing(api, admin)["items"][0]["status"] == "used"


def test_revoke_an_expired_invite_is_404(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    response = create(api, admin, email=FRIEND)
    clock.advance(days=7)  # exactly at expiry: already expired
    assert revoke(api, admin, response.json()["invite"]["id"]).status_code == 404  # type: ignore[attr-defined]
    assert invites(factory)[0].revoked_at is None
    assert listing(api, admin)["items"][0]["status"] == "expired"


@pytest.mark.parametrize(
    "invite_id",
    [
        "00000000-0000-4000-8000-000000000000",
        "not-a-uuid",
        "123",
        "' OR '1'='1",
        "x" * 2000,
        "%00",
        "zoë",
    ],
)
def test_revoke_unknown_or_malformed_ids_is_the_same_404(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock, invite_id: str
) -> None:
    token = issue_link(factory, FRIEND, now=clock.now)
    answer = revoke(api, admin, invite_id)
    assert answer.status_code == 404  # type: ignore[attr-defined]
    assert answer.json() == {"detail": admin_router.INVITE_NOT_REVOCABLE}  # type: ignore[attr-defined]
    assert check(api, token) == 200


def test_revoke_accepts_the_uuid_in_another_spelling(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    issue_link(factory, FRIEND, now=clock.now)
    invite_id = invites(factory)[0].id
    assert revoke(api, admin, invite_id.hex.upper()).status_code == 204  # type: ignore[attr-defined]


def test_concurrent_revokes_one_wins(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    issue_link(factory, FRIEND, now=clock.now)
    invite_id = invites(factory)[0].id
    client: TestClient = api.client
    barrier = threading.Barrier(6)

    def attempt(_: int) -> int:
        barrier.wait()
        return client.delete(
            f"{INVITES}/{invite_id}", headers={**CSRF, "Cookie": f"{COOKIE_NAME}={admin}"}
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(attempt, range(6)))
    assert codes == [204] + [404] * 5


# --- logs --------------------------------------------------------------------------------------


def test_links_never_reach_the_logs(
    api: Api,
    admin: str,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    make_user(factory, email=MEMBER, role=Role.member)
    first = create(api, admin, email=FRIEND)
    tokens = [created_token(first)]
    tokens.append(created_token(create(api, admin, email=FRIEND)))  # revokes the first
    tokens.append(created_token(create(api, admin, email=MEMBER, kind="reset")))
    listing(api, admin)
    revoke(api, admin, first.json()["invite"]["id"])  # type: ignore[attr-defined]
    create(api, admin, email="not an email")

    records = [r for r in caplog.records if not r.name.startswith("httpx")]
    assert any(r.getMessage() == "request" and getattr(r, "path", None) == INVITES for r in records)
    text = "\n".join(r.getMessage() + " " + json.dumps(vars(r), default=str) for r in records)
    stdout = "\n".join(
        line for line in capsys.readouterr().out.splitlines() if '"logger": "httpx"' not in line
    )
    for token in tokens:
        assert token not in text
        assert token not in stdout
        assert security.hash_token(token).hex() not in text


def test_the_cli_still_reuses_the_same_revocation(
    api: Api, admin: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    # A link from the admin area and one from the CLI for the same email and kind: the newer
    # one wins, whichever made it.
    from_ui = created_token(create(api, admin, email=FRIEND))
    from_cli = issue_link(factory, FRIEND, kind=InviteKind.invite, now=clock.now)
    assert check(api, from_ui) == 404
    assert check(api, from_cli) == 200
