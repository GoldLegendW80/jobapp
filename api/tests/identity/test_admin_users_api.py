"""/api/admin/users against a real Postgres: who may call it, the list (sorting, sessions,
pagination), and PATCH: disabling ends sessions at once, roles, the last active admin rule,
idempotence, bad input, and the rule holding under concurrent requests."""

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.identity import admin_router, service
from app.identity.deps import COOKIE_NAME, CSRF_REFUSED, UNAUTHORIZED
from app.identity.models import Role, User
from app.identity.models import Session as UserSession
from tests.identity.api_helpers import CSRF, Api, Clock, make_user

ADMIN = "harald@example.org"
OTHER_ADMIN = "olga@example.org"
MEMBER = "member@example.org"
USERS = "/api/admin/users"
USER_KEYS = {
    "id",
    "email",
    "name",
    "role",
    "is_active",
    "created_at",
    "last_login_at",
    "sessions",
}


def user_id(factory: sessionmaker[Session], email: str) -> UUID:
    with factory() as db:
        found = db.scalar(sa.select(User.id).where(User.email == email))
        assert found is not None
        return found


def load(factory: sessionmaker[Session], email: str) -> User:
    with factory() as db:
        user = db.scalars(sa.select(User).where(User.email == email)).one()
        db.expunge(user)
        return user


def session_rows(factory: sessionmaker[Session], email: str) -> int:
    with factory() as db:
        return int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(UserSession)
                .join(User, User.id == UserSession.user_id)
                .where(User.email == email)
            )
            or 0
        )


def active_admins(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return service.count_active_admins(db)


def patch(api: Api, token: str | None, target: UUID | str, **body: object) -> httpx.Response:
    return api.request("PATCH", f"{USERS}/{target}", token=token, json=body)


def listing(api: Api, token: str, **params: object) -> dict:  # type: ignore[type-arg]
    response = api.get(USERS, token=token, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.fixture
def admin(api: Api, factory: sessionmaker[Session]) -> str:
    """The session token of a signed in admin named Harald."""
    make_user(factory, email=ADMIN, role=Role.admin, name="Harald")
    return api.login_token(ADMIN)


@pytest.fixture
def member(api: Api, factory: sessionmaker[Session]) -> str:
    make_user(factory, email=MEMBER, role=Role.member, name="Mia")
    return api.login_token(MEMBER)


@pytest.fixture
def other_admin(api: Api, factory: sessionmaker[Session]) -> str:
    make_user(factory, email=OTHER_ADMIN, role=Role.admin, name="Olga")
    return api.login_token(OTHER_ADMIN)


# --- who may call ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("GET", USERS), ("PATCH", f"{USERS}/{uuid4()}"), ("PATCH", f"{USERS}/x")]
)
def test_anonymous_is_401(api: Api, method: str, path: str) -> None:
    response = api.request(method, path, json={"is_active": False})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


def test_member_gets_the_same_404_as_an_unknown_route(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    unknown = api.get("/api/admin/nothing-here", token=member).json()
    harald = user_id(factory, ADMIN)
    mia = user_id(factory, MEMBER)
    for response in (
        api.get(USERS, token=member),
        patch(api, member, harald, is_active=False),
        patch(api, member, mia, role="admin"),  # promoting yourself
        patch(api, member, "not-a-uuid", role="admin"),
        patch(api, member, uuid4(), role="admin"),
    ):
        assert response.status_code == 404
        assert response.json() == unknown == {"detail": "Not Found"}
    assert load(factory, ADMIN).is_active
    assert load(factory, MEMBER).role is Role.member


def test_csrf_header_is_required_even_for_an_admin(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    mia = user_id(factory, MEMBER)
    response = api.request(
        "PATCH", f"{USERS}/{mia}", token=admin, csrf=False, json={"is_active": False}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert load(factory, MEMBER).is_active
    assert api.me(member).status_code == 200


def test_handlers_run_on_the_threadpool() -> None:
    for handler in (admin_router.list_users, admin_router.update_user):
        assert not inspect.iscoroutinefunction(handler)


# --- list --------------------------------------------------------------------------------------


def test_list_shows_the_admin_alone_at_first(api: Api, admin: str, clock: Clock) -> None:
    page = listing(api, admin)
    assert (page["total"], page["limit"], page["offset"], page["active_admins"]) == (1, 50, 0, 1)
    (only,) = page["items"]
    assert set(only) == USER_KEYS  # never a password hash
    assert only["email"] == ADMIN
    assert only["role"] == "admin"
    assert only["is_active"] is True
    assert only["sessions"] == 1
    assert datetime.fromisoformat(only["last_login_at"]) == clock.now


def test_list_is_sorted_by_name_ignoring_case_with_unicode_names(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    for email, name in [
        ("zoe@example.org", "Zoë Ångström"),
        ("bob@example.org", "bob"),
        ("alice@example.org", "Alice"),
        ("emile@example.org", "Émile 李"),
        ("nobody@example.org", "Ana"),
    ]:
        make_user(factory, email=email, name=name, role=Role.member, password_hash="x")
    names = [u["name"] for u in listing(api, admin)["items"]]
    assert names[:3] == ["Alice", "Ana", "bob"]
    assert set(names) == {"Alice", "Ana", "bob", "Harald", "Zoë Ångström", "Émile 李"}
    assert names.index("Harald") < names.index("Zoë Ångström")


def test_list_counts_only_live_sessions_and_active_admins(
    api: Api, admin: str, member: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    api.login_token(MEMBER)  # a second browser
    with factory() as db, db.begin():
        mia = db.scalars(sa.select(User).where(User.email == MEMBER)).one()
        expired = service.create_session(db, mia, now=clock.now - timedelta(days=31))
        assert expired.row.expires_at < clock.now
    make_user(factory, email="old@example.org", name="Old admin", active=False)
    page = listing(api, admin)
    by_email = {u["email"]: u for u in page["items"]}
    assert by_email[MEMBER]["sessions"] == 2
    assert by_email["old@example.org"]["sessions"] == 0
    assert by_email["old@example.org"]["is_active"] is False
    assert by_email["old@example.org"]["last_login_at"] is None
    assert page["active_admins"] == 1  # the disabled admin does not count


def test_list_pages(api: Api, admin: str, factory: sessionmaker[Session]) -> None:
    # Equal names: the email breaks the tie, so pages never overlap.
    for index in range(6):
        make_user(factory, email=f"u{index}@example.org", name="Same", password_hash="x")
    seen = []
    for offset in range(0, 8, 3):
        page = listing(api, admin, limit=3, offset=offset)
        assert page["total"] == 7
        assert (page["limit"], page["offset"]) == (3, offset)
        seen += [u["email"] for u in page["items"]]
    assert len(seen) == len(set(seen)) == 7
    assert listing(api, admin, offset=99)["items"] == []
    assert listing(api, admin, offset=99)["total"] == 7


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "ten"}, {"offset": 10**7}],
)
def test_list_refuses_bad_paging(api: Api, admin: str, params: dict) -> None:  # type: ignore[type-arg]
    assert api.get(USERS, token=admin, params=params).status_code == 422


# --- disable and enable ------------------------------------------------------------------------


def test_disabling_ends_the_sessions_at_once(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    second = api.login_token(MEMBER)
    assert session_rows(factory, MEMBER) == 2
    response = patch(api, admin, user_id(factory, MEMBER), is_active=False)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == USER_KEYS
    assert (body["is_active"], body["role"], body["sessions"]) == (False, "member", 0)
    # Deleted by the PATCH itself, not lazily on their next request.
    assert session_rows(factory, MEMBER) == 0
    for token in (member, second):
        response = api.me(token)
        assert response.status_code == 401
        assert response.json() == {"detail": UNAUTHORIZED}
    assert api.login(MEMBER).status_code == 401
    assert session_rows(factory, MEMBER) == 0


def test_re_enabling_does_not_bring_old_sessions_back(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    mia = user_id(factory, MEMBER)
    assert patch(api, admin, mia, is_active=False).status_code == 200
    response = patch(api, admin, mia, is_active=True)
    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert api.me(member).status_code == 401
    fresh = api.login_token(MEMBER)
    assert api.me(fresh).status_code == 200


def test_enabling_clears_sessions_left_by_a_disable_outside_the_api(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(User).where(User.email == MEMBER).values(is_active=False))
    assert session_rows(factory, MEMBER) == 1  # nobody used it since
    assert patch(api, admin, user_id(factory, MEMBER), is_active=True).status_code == 200
    assert session_rows(factory, MEMBER) == 0
    assert api.me(member).status_code == 401


def test_patch_is_idempotent_and_writes_nothing_when_nothing_changes(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    mia = user_id(factory, MEMBER)
    assert patch(api, admin, mia, is_active=False).status_code == 200
    before = load(factory, MEMBER)
    for body in ({"is_active": False}, {"role": "member"}, {"is_active": False, "role": "member"}):
        response = patch(api, admin, mia, **body)
        assert response.status_code == 200
        assert (response.json()["is_active"], response.json()["role"]) == (False, "member")
    after = load(factory, MEMBER)
    assert after.updated_at == before.updated_at
    # An admin already admin, an active account enabled: 200, unchanged.
    harald = user_id(factory, ADMIN)
    before = load(factory, ADMIN)
    response = patch(api, admin, harald, role="admin", is_active=True)
    assert response.status_code == 200
    assert response.json()["sessions"] == 1
    assert load(factory, ADMIN).updated_at == before.updated_at
    assert api.me(admin).status_code == 200


def test_an_admin_can_disable_another_admin(
    api: Api, admin: str, other_admin: str, factory: sessionmaker[Session]
) -> None:
    response = patch(api, admin, user_id(factory, OTHER_ADMIN), is_active=False)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert api.get(USERS, token=other_admin).status_code == 401
    assert active_admins(factory) == 1


def test_an_admin_may_disable_themselves_while_another_admin_remains(
    api: Api, admin: str, other_admin: str, factory: sessionmaker[Session]
) -> None:
    response = patch(api, admin, user_id(factory, ADMIN), is_active=False)
    assert response.status_code == 200
    assert api.me(admin).status_code == 401
    assert api.me(other_admin).status_code == 200


# --- roles -------------------------------------------------------------------------------------


def test_promote_then_demote_a_member(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    mia = user_id(factory, MEMBER)
    response = patch(api, admin, mia, role="admin")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["sessions"] == 1  # a role change keeps the sessions
    # The same session is an admin on its very next request.
    assert api.get(USERS, token=member).status_code == 200
    assert api.me(member).json()["role"] == "admin"
    assert listing(api, admin)["active_admins"] == 2

    assert patch(api, admin, mia, role="member").status_code == 200
    assert api.get(USERS, token=member).status_code == 404
    assert api.me(member).status_code == 200


def test_an_admin_may_demote_themselves_when_another_admin_exists(
    api: Api, admin: str, other_admin: str, factory: sessionmaker[Session]
) -> None:
    response = patch(api, admin, user_id(factory, ADMIN), role="member")
    assert response.status_code == 200
    assert response.json()["role"] == "member"
    assert api.me(admin).json()["role"] == "member"  # still signed in
    assert api.get(USERS, token=admin).status_code == 404
    # And now Olga is the last admin.
    response = patch(api, other_admin, user_id(factory, OTHER_ADMIN), role="member")
    assert response.status_code == 409


def test_a_demoted_admin_cannot_act_with_the_session_they_had(
    api: Api, admin: str, other_admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    assert patch(api, other_admin, user_id(factory, ADMIN), role="member").status_code == 200
    response = patch(api, admin, user_id(factory, MEMBER), is_active=False)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert load(factory, MEMBER).is_active


def test_the_service_refuses_an_actor_who_is_no_longer_an_admin(
    admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    # The race the router cannot see: the session said admin, the row no longer does.
    with factory() as db, pytest.raises(service.NotAnAdminError):
        service.update_user(db, user_id(factory, MEMBER), user_id(factory, ADMIN), is_active=False)
    with factory() as db, pytest.raises(service.NotAnAdminError):
        service.update_user(db, uuid4(), user_id(factory, MEMBER), is_active=False)


# --- the last active admin ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"is_active": False},
        {"role": "member"},
        {"is_active": False, "role": "member"},
        {"is_active": True, "role": "member"},
    ],
)
def test_the_last_active_admin_can_be_neither_disabled_nor_demoted(
    api: Api,
    admin: str,
    member: str,
    factory: sessionmaker[Session],
    body: dict,  # type: ignore[type-arg]
) -> None:
    before = load(factory, ADMIN)
    response = patch(api, admin, user_id(factory, ADMIN), **body)
    assert response.status_code == 409
    assert response.json() == {"detail": admin_router.LAST_ADMIN}
    assert " - " not in admin_router.LAST_ADMIN
    assert "\u2014" not in admin_router.LAST_ADMIN
    after = load(factory, ADMIN)
    assert (after.is_active, after.role, after.updated_at) == (True, Role.admin, before.updated_at)
    assert api.me(admin).status_code == 200  # the session survives the refusal
    assert session_rows(factory, ADMIN) == 1


def test_a_disabled_admin_does_not_count_until_enabled(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    make_user(factory, email=OTHER_ADMIN, name="Olga", role=Role.admin, active=False)
    harald, olga = user_id(factory, ADMIN), user_id(factory, OTHER_ADMIN)
    assert patch(api, admin, harald, role="member").status_code == 409
    assert patch(api, admin, olga, is_active=True).status_code == 200
    assert patch(api, admin, harald, role="member").status_code == 200
    assert active_admins(factory) == 1


def test_a_disabled_admin_may_be_demoted(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    make_user(factory, email=OTHER_ADMIN, name="Olga", role=Role.admin, active=False)
    response = patch(api, admin, user_id(factory, OTHER_ADMIN), role="member")
    assert response.status_code == 200
    assert (response.json()["role"], response.json()["is_active"]) == ("member", False)


# --- bad input ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["not-a-uuid", "1", "' or 1=1 --", "x" * 500, "00000000-0000-0000-0000-00000000000g"],
)
def test_a_malformed_id_is_404(api: Api, admin: str, target: str) -> None:
    response = patch(api, admin, target, is_active=False)
    assert response.status_code == 404
    assert response.json() == {"detail": admin_router.USER_NOT_FOUND}


def test_an_unknown_id_is_404(api: Api, admin: str) -> None:
    response = patch(api, admin, uuid4(), is_active=False)
    assert response.status_code == 404
    assert response.json() == {"detail": admin_router.USER_NOT_FOUND}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"is_active": None},
        {"role": None},
        {"is_active": False, "role": None},
        {"role": "owner"},
        {"role": "ADMIN"},
        {"is_active": "yes"},
        {"is_active": "false"},
        {"is_active": 0},
        {"email": "x@example.org"},
        {"is_active": False, "password_hash": "x"},
        [],
        "disable",
    ],
)
def test_bad_bodies_are_422_and_change_nothing(
    api: Api, admin: str, member: str, factory: sessionmaker[Session], body: object
) -> None:
    response = api.request("PATCH", f"{USERS}/{user_id(factory, MEMBER)}", token=admin, json=body)
    assert response.status_code == 422, response.text
    assert load(factory, MEMBER).is_active
    assert api.me(member).status_code == 200


def test_a_body_that_is_not_json_is_422(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    response = api.request(
        "PATCH",
        f"{USERS}/{user_id(factory, MEMBER)}",
        token=admin,
        content=b"is_active=false",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 422


def test_unicode_names_come_back_intact(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    make_user(factory, email="zoe@example.org", name="Zoë Ångström 🌍", role=Role.member)
    response = patch(api, admin, user_id(factory, "zoe@example.org"), role="admin")
    assert response.status_code == 200
    assert response.json()["name"] == "Zoë Ångström 🌍"


# --- concurrency -------------------------------------------------------------------------------


def _concurrent_patches(
    api: Api,
    calls: list[tuple[str, UUID, dict]],  # type: ignore[type-arg]
) -> list[int]:
    client: TestClient = api.client
    barrier = threading.Barrier(len(calls))

    def attempt(call: tuple[str, UUID, dict]) -> int:  # type: ignore[type-arg]
        token, target, body = call
        barrier.wait()
        return client.patch(
            f"{USERS}/{target}",
            json=body,
            headers={**CSRF, "Cookie": f"{COOKIE_NAME}={token}"},
        ).status_code

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(attempt, calls))


@pytest.mark.parametrize("round_", range(5))
def test_two_admins_disabling_each_other_leave_exactly_one(
    api: Api, admin: str, other_admin: str, factory: sessionmaker[Session], round_: int
) -> None:
    harald, olga = user_id(factory, ADMIN), user_id(factory, OTHER_ADMIN)
    codes = _concurrent_patches(
        api, [(admin, olga, {"is_active": False}), (other_admin, harald, {"is_active": False})]
    )
    # The loser was disabled first: refused under the lock (404), or before it, at sign in (401).
    assert sorted(codes) in ([200, 401], [200, 404], [200, 409]), codes
    assert active_admins(factory) == 1
    survivor = ADMIN if load(factory, ADMIN).is_active else OTHER_ADMIN
    loser = OTHER_ADMIN if survivor == ADMIN else ADMIN
    assert session_rows(factory, loser) == 0
    assert session_rows(factory, survivor) == 1


@pytest.mark.parametrize("round_", range(3))
def test_two_admins_demoting_themselves_at_once_leave_one(
    api: Api, admin: str, other_admin: str, factory: sessionmaker[Session], round_: int
) -> None:
    harald, olga = user_id(factory, ADMIN), user_id(factory, OTHER_ADMIN)
    codes = _concurrent_patches(
        api, [(admin, harald, {"role": "member"}), (other_admin, olga, {"role": "member"})]
    )
    assert sorted(codes) == [200, 409], codes
    assert active_admins(factory) == 1


def test_a_ring_of_admins_disabling_each_other_keeps_one(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    emails = [ADMIN] + [f"admin{i}@example.org" for i in range(1, 6)]
    for email in emails[1:]:
        make_user(factory, email=email, name=email, role=Role.admin)
    tokens = [admin] + [api.login_token(email) for email in emails[1:]]
    ids = [user_id(factory, email) for email in emails]
    calls = [(tokens[i], ids[(i + 1) % len(ids)], {"is_active": False}) for i in range(len(ids))]
    codes = _concurrent_patches(api, calls)
    # 401: the caller was disabled (sessions gone) before their own request was let in.
    assert set(codes) <= {200, 401, 404, 409}, codes
    assert active_admins(factory) >= 1
    assert codes.count(200) == len(ids) - active_admins(factory)
