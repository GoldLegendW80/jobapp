"""The guards around every route: CSRF header, default deny with its public allowlist, the
route table check, admin-only routes, log hygiene and login timing."""

import inspect
import json
import logging
import re
import statistics
import time

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.orm import Session, sessionmaker
from starlette.routing import Mount, Route

from app.identity import deps, router
from app.identity.deps import (
    CSRF_REFUSED,
    PUBLIC_ROUTES,
    UNAUTHORIZED,
    current_user,
    default_deny,
    default_deny_problems,
    optional_user,
    require_admin,
)
from app.identity.models import InviteKind, Role, User
from app.main import create_app
from tests.conftest import make_settings
from tests.identity.api_helpers import PASSWORD, Api, Clock, issue_link, make_user, session_cookie

EMAIL = "harald@example.org"


# --- CSRF --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "XMLHttpRequest", "JOBAPP", "jobapp ", "other"])
@pytest.mark.parametrize(
    "path", ["/api/auth/login", "/api/auth/logout", "/api/auth/invites/x/accept", "/api/nope"]
)
def test_post_without_the_csrf_header_is_403(api: Api, path: str, value: str | None) -> None:
    headers = {} if value is None else {"X-Requested-With": value}
    response = api.post(path, csrf=False, headers=headers, json={"email": EMAIL, "password": "x"})
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_need_the_header_too(api: Api, method: str) -> None:
    assert api.request(method, "/api/auth/me", csrf=False).status_code == 403
    assert api.request(method, "/api/auth/me").status_code == 405


def test_csrf_check_runs_before_authentication_and_login(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    token = api.login_token(EMAIL)
    refused = api.post("/api/auth/logout", token=token, csrf=False)
    assert refused.status_code == 403
    assert api.me(token).status_code == 200  # the refused logout did nothing
    blocked = api.login(EMAIL, csrf=False)
    assert blocked.status_code == 403
    assert session_cookie(blocked) is None


def test_post_with_the_header_goes_through(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory)
    assert api.login(EMAIL).status_code == 200


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_need_no_header(api: Api, method: str) -> None:
    response = api.request(method, "/api/auth/me", csrf=False)
    assert response.status_code in (401, 405)
    assert response.status_code != 403
    assert api.get("/api/health", csrf=False).status_code in (200, 503)


def test_csrf_only_guards_the_api(api: Api) -> None:
    for path in ("/", "/apix", "/docs"):
        assert api.post(path, csrf=False).status_code != 403


# --- default deny ------------------------------------------------------------------------------


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "0" * 43, path)


def test_every_non_public_route_is_401_without_a_session(app: FastAPI, api: Api) -> None:
    """Walk the route table as served and call every non-public API route with no session and
    a dummy body: each must answer 401. Fails when a new route forgets the session check."""
    checked = []
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        for method in sorted(context.methods or ()):
            if (method, context.path) in PUBLIC_ROUTES:
                continue
            response = api.request(method, _fill(context.path), json={"dummy": True})
            assert response.status_code == 401, (method, context.path, response.text)
            assert response.json() == {"detail": UNAUTHORIZED}
            checked.append((method, context.path))
    assert ("GET", "/api/auth/me") in checked


def test_public_routes_answer_without_a_session(api: Api) -> None:
    assert api.get("/api/health").status_code in (200, 503)
    assert api.login("nobody@example.org").status_code == 401  # the login answer, not the guard
    assert api.login("nobody@example.org").json()["detail"] != UNAUTHORIZED
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get(f"/api/auth/invites/{'0' * 43}").status_code == 404
    accepted = api.post(f"/api/auth/invites/{'0' * 43}/accept", json={"password": "x" * 12})
    assert accepted.status_code == 404


def test_routes_added_later_are_denied_too(app: FastAPI, api: Api) -> None:
    extra = APIRouter(prefix="/api/later")

    @extra.get("/items")
    def items() -> list[int]:
        return [1]

    @extra.post("/items")
    def create() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(extra)

    @app.get("/api/direct")
    def direct() -> int:
        return 1

    nested = APIRouter()
    nested.include_router(extra, prefix="/v2")
    app.include_router(nested)

    assert deps.default_deny_problems(app.routes) == []
    for method, path in [
        ("GET", "/api/later/items"),
        ("POST", "/api/later/items"),
        ("GET", "/api/direct"),
        ("GET", "/v2/api/later/items"),
    ]:
        assert api.request(method, path).status_code == 401, path


def test_signed_in_requests_reach_the_route(
    app: FastAPI, api: Api, factory: sessionmaker[Session]
) -> None:
    @app.get("/api/probe/whoami")
    def whoami(user: User = Depends(current_user)) -> dict[str, str]:  # noqa: B008
        return {"email": user.email}

    make_user(factory)
    token = api.login_token(EMAIL)
    assert api.get("/api/probe/whoami", token=token).json() == {"email": EMAIL}


def test_require_admin(app: FastAPI, api: Api, factory: sessionmaker[Session]) -> None:
    @app.get("/api/probe/admin-only")
    def admin_only(user: User = Depends(require_admin)) -> dict[str, str]:  # noqa: B008
        return {"email": user.email}

    make_user(factory)
    make_user(factory, email="member@example.org", role=Role.member)
    admin = api.login_token(EMAIL)
    member = api.login_token("member@example.org")
    assert api.get("/api/probe/admin-only", token=admin).status_code == 200
    as_member = api.get("/api/probe/admin-only", token=member)
    assert as_member.status_code == 404
    assert as_member.json() == {"detail": "Not Found"}  # same as a route that does not exist
    assert api.get("/api/probe/admin-only").status_code == 401


def test_optional_user(app: FastAPI, api: Api, factory: sessionmaker[Session]) -> None:
    @app.get("/api/probe/maybe")
    def maybe(user: User | None = Depends(optional_user)) -> dict[str, str | None]:  # noqa: B008
        return {"email": user.email if user else None}

    # optional_user only matters behind a public route; lift the guard to exercise it alone.
    app.dependency_overrides[default_deny] = lambda: None
    make_user(factory)
    token = api.login_token(EMAIL)
    assert api.get("/api/probe/maybe").json() == {"email": None}
    assert api.get("/api/probe/maybe", token="garbage").json() == {"email": None}
    assert api.get("/api/probe/maybe", token=token).json() == {"email": EMAIL}


def test_session_is_resolved_once_per_request(
    app: FastAPI, api: Api, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    @app.get("/api/probe/twice")
    def twice(
        a: User = Depends(current_user),  # noqa: B008
        b: User | None = Depends(optional_user),  # noqa: B008
    ) -> bool:
        return a is b

    make_user(factory)
    token = api.login_token(EMAIL)
    calls = []
    real = deps.service.resolve_session

    def counting(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(deps.service, "resolve_session", counting)
    assert api.get("/api/probe/twice", token=token).json() is True
    assert len(calls) == 1


# --- the route table check ---------------------------------------------------------------------


def test_the_real_app_passes_the_check(app: FastAPI) -> None:
    assert default_deny_problems(app.routes) == []


def test_check_flags_an_app_without_the_global_dependency() -> None:
    bare = FastAPI()
    bare.include_router(router.router)
    problems = default_deny_problems(bare.routes)
    assert any("/api/auth/me does not run default_deny" in p for p in problems)
    assert any("/api/health, which no route serves" in p for p in problems)


def test_check_flags_mounts_and_plain_routes_under_api() -> None:
    from starlette.responses import PlainTextResponse

    app = create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/d"))
    app.router.routes.append(Route("/api/raw", lambda _: PlainTextResponse("raw")))
    app.router.routes.append(Mount("/api/files", app=lambda *_: None))
    app.router.routes.append(Mount("", app=lambda *_: None))
    problems = default_deny_problems(app.routes)
    assert any("/api/raw is served outside" in p for p in problems)
    assert any("/api/files" in p and "outside" in p for p in problems)
    assert any(p.startswith("/ is served outside") for p in problems)
    with pytest.raises(RuntimeError, match="Default deny is broken"):
        deps.assert_default_deny(app)


def test_check_flags_a_public_route_served_under_another_prefix() -> None:
    app = create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/d"))
    app.include_router(router.router, prefix="/v2")
    problems = default_deny_problems(app.routes)
    assert any(
        "public route /api/auth/login is also served at /v2/api/auth/login" in p for p in problems
    )


def test_create_app_refuses_a_stale_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = PUBLIC_ROUTES | {("GET", "/api/auth/renamed")}
    monkeypatch.setattr(deps, "PUBLIC_ROUTES", stale)
    with pytest.raises(RuntimeError, match="GET /api/auth/renamed, which no route serves"):
        create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/d"))


def test_startup_rechecks_routes_added_after_create_app() -> None:
    from fastapi.testclient import TestClient
    from starlette.responses import PlainTextResponse

    app = create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/d"))
    app.router.routes.append(Route("/api/sneaky", lambda _: PlainTextResponse("x")))
    with pytest.raises(RuntimeError, match="/api/sneaky"), TestClient(app):
        pass


def test_handlers_doing_argon2_run_on_the_threadpool() -> None:
    # Plain def handlers: FastAPI calls them through run_in_threadpool, never on the loop.
    for handler in (router.login, router.accept_invite, router.logout, router.check_invite):
        assert not inspect.iscoroutinefunction(handler), handler.__name__


# --- logs --------------------------------------------------------------------------------------


def test_logs_never_hold_passwords_tokens_or_cookies(
    api: Api,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    make_user(factory)
    invite = issue_link(factory, "friend@example.org", now=clock.now)
    reset = issue_link(factory, EMAIL, kind=InviteKind.reset, now=clock.now)
    friend_password = "friend secret passphrase 1"
    reset_password = "reset secret passphrase 2"
    wrong_password = "wrong secret passphrase 3"

    secrets = [PASSWORD, friend_password, reset_password, wrong_password, invite, reset]
    assert api.login(EMAIL, wrong_password).status_code == 401
    assert api.login("nobody@example.org", wrong_password).status_code == 401
    assert api.post("/api/auth/login", json={"email": EMAIL}).status_code == 422
    assert api.post("/api/auth/login", json={"password": wrong_password}).status_code == 422
    first = api.login_token(EMAIL)
    secrets.append(first)
    assert api.get(f"/api/auth/invites/{invite}").status_code == 200
    accepted = api.post(
        f"/api/auth/invites/{invite}/accept", json={"name": "Ann", "password": friend_password}
    )
    secrets.append(session_cookie(accepted).value)  # type: ignore[union-attr]
    assert api.get(f"/api/auth/invites/{invite}/nope").status_code == 404
    assert (
        api.post(f"/api/auth/invites/{invite}/accept", json={"password": "short"}).status_code
        == 404
    )
    reset_response = api.post(
        f"/api/auth/invites/{reset}/accept", json={"password": reset_password}
    )
    second = session_cookie(reset_response).value  # type: ignore[union-attr]
    secrets.append(second)
    clock.advance(days=2)  # sliding refresh reissues the cookie
    assert api.me(second).status_code == 200
    assert api.me(first).status_code == 401
    assert api.post("/api/auth/logout", token=second).status_code == 204

    app_records = [r for r in caplog.records if not r.name.startswith("httpx")]
    assert any(r.getMessage() == "request" for r in app_records)
    text = "\n".join(r.getMessage() + " " + json.dumps(vars(r), default=str) for r in app_records)
    stdout = "\n".join(
        line for line in capsys.readouterr().out.splitlines() if '"logger": "httpx"' not in line
    )
    for secret in secrets:
        assert secret not in text
        assert secret not in stdout
    # Invite paths are logged by template; an unmatched one is masked.
    paths = {r.__dict__.get("path") for r in app_records if r.getMessage() == "request"}
    assert "/api/auth/invites/{token}" in paths
    assert "/api/auth/invites/[redacted]/nope" in paths


# --- timing ------------------------------------------------------------------------------------


def test_unknown_email_and_wrong_password_take_comparable_time(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory)
    api.login("warmup@example.org", "warm up password")  # builds the dummy hash once
    api.login(EMAIL, "warm up password")
    unknown: list[float] = []
    wrong: list[float] = []
    for i in range(12):
        for email, bucket in (("nobody@example.org", unknown), (EMAIL, wrong)):
            start = time.perf_counter()
            response = api.login(email, f"not the password {i}")
            bucket.append(time.perf_counter() - start)
            assert response.status_code == 401
    ratio = statistics.median(unknown) / statistics.median(wrong)
    assert 0.5 <= ratio <= 2, (ratio, unknown, wrong)
