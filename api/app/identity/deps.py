"""Who is calling: the session cookie, the auth dependencies and the default-deny guard.

Every later module takes its user from `Depends(current_user)` (or `require_admin`) and filters
its rows by `user.id`. It never has to remember anything else: `default_deny` is installed on
the whole app (`FastAPI(dependencies=...)` in app.main), so a route under /api answers 401
without a valid session unless its method and path are in PUBLIC_ROUTES, and
`assert_default_deny` refuses to build or start an app where a route escapes that guard.

Session resolution (database lookup, lazy deletion of expired or disabled sessions, sliding
refresh) runs once per request on the threadpool and is cached on `request.state`. Cookie writes
are not done here: handlers and dependencies record what the cookie should become in
`request.state`, and `session_cookie_middleware` writes it on whatever response goes out, the
only place that knows the flags. Nothing here logs a token or a cookie value.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.dependencies.models import Dependant
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.concurrency import run_in_threadpool
from starlette.routing import BaseRoute, Mount

from app.identity import service
from app.identity.models import Role, User

COOKIE_NAME = "jobapp_session"
COOKIE_MAX_AGE = int(service.SESSION_TTL.total_seconds())
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "jobapp"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The only routes that answer without a session, by method and path template. Adding one is a
# security decision: say why next to it.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),  # container and smoke health checks
        ("POST", "/api/auth/login"),  # how a session starts
        # Idempotent: it only ends the session the cookie names, and answers 204 without one,
        # so a browser whose session already expired can still clear its cookie.
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/invites/{token}"),  # the invite and reset pages check their link
        ("POST", "/api/auth/invites/{token}/accept"),  # and use it: the token is the credential
    }
)

UNAUTHORIZED = "Sign in to continue."
CSRF_REFUSED = "Missing or invalid X-Requested-With header."

# request.state keys
_AUTH = "identity_auth"
_COOKIE = "identity_cookie"
_CLEAR = ""  # request.state value meaning: expire the cookie


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def clock(request: Request) -> datetime:
    """Now, from the app's clock (tests freeze it through app.state.clock)."""
    now: datetime = request.app.state.clock()
    return now


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# --- cookie ------------------------------------------------------------------------------------


def issue_cookie(request: Request, token: str) -> None:
    """Send this session token as the cookie on the response of this request."""
    setattr(request.state, _COOKIE, token)


def clear_cookie(request: Request) -> None:
    """Expire the cookie on the response of this request."""
    setattr(request.state, _COOKIE, _CLEAR)


def cookie_is_secure(request: Request) -> bool:
    """Secure when the visitor's connection is https (the scheme uvicorn --proxy-headers takes
    from X-Forwarded-Proto: https through the tunnel), and always in production."""
    return request.url.scheme == "https" or request.app.state.settings.app_env == "production"


def write_cookie(request: Request, response: Response) -> None:
    pending = getattr(request.state, _COOKIE, None)
    if pending is None:
        return
    secure = cookie_is_secure(request)
    if pending == _CLEAR:
        response.delete_cookie(COOKIE_NAME, path="/", secure=secure, httponly=True, samesite="lax")
    else:
        response.set_cookie(
            COOKIE_NAME,
            pending,
            max_age=COOKIE_MAX_AGE,
            path="/",
            secure=secure,
            httponly=True,
            samesite="lax",
        )
    response.headers["Cache-Control"] = "no-store"


async def session_cookie_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    write_cookie(request, response)
    return response


async def csrf_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Refuse any state-changing /api request without `X-Requested-With: jobapp`. A cross-site
    form cannot set a custom header, and a cross-site fetch that sets one needs a CORS
    preflight, which this API never grants."""
    if (
        request.method not in SAFE_METHODS
        and is_api_path(request.url.path)
        and request.headers.get(CSRF_HEADER) != CSRF_VALUE
    ):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": CSRF_REFUSED})
    return await call_next(request)


# --- session resolution ------------------------------------------------------------------------


def _resolve(request: Request) -> service.ActiveSession | None:
    """Look the cookie up once per request (blocking: call through the threadpool)."""
    if hasattr(request.state, _AUTH):
        cached: service.ActiveSession | None = getattr(request.state, _AUTH)
        return cached
    token = request.cookies.get(COOKIE_NAME)
    active = None
    if token:
        factory = request.app.state.sessionmaker
        with factory() as db, db.begin():
            active = service.resolve_session(db, token, clock(request))
        if active is None:
            clear_cookie(request)  # stale, expired, revoked or malformed: drop it
        elif active.refreshed:
            issue_cookie(request, token)
    setattr(request.state, _AUTH, active)
    return active


async def resolve(request: Request) -> service.ActiveSession | None:
    return await run_in_threadpool(_resolve, request)


def unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHORIZED)


async def default_deny(request: Request) -> None:
    """Installed on the whole app: no session, no access, except on PUBLIC_ROUTES.

    The route in the scope is the APIRoute as declared; `assert_default_deny` guarantees its
    declared path is also the path it is served at for every public entry.
    """
    route = request.scope.get("route")
    if (request.method, getattr(route, "path", None)) in PUBLIC_ROUTES:
        return
    if await resolve(request) is None:
        raise unauthorized()


async def optional_user(request: Request) -> User | None:
    active = await resolve(request)
    return active.user if active else None


async def current_user(request: Request) -> User:
    active = await resolve(request)
    if active is None:
        raise unauthorized()
    return active.user


async def current_session(request: Request) -> service.ActiveSession:
    """The session this request runs under (its row and user), for routes that act on it."""
    active = await resolve(request)
    if active is None:
        raise unauthorized()
    return active


async def require_admin(user: User = Depends(current_user)) -> User:  # noqa: B008
    # 404, not 403: a member learns nothing about which admin routes exist.
    if user.role is not Role.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return user


# --- the guard on the route table --------------------------------------------------------------


def _calls(dependant: Dependant | None) -> Iterable[Any]:
    if dependant is None:
        return
    for sub in dependant.dependencies:
        yield sub.call
        yield from _calls(sub)


def default_deny_problems(routes: Sequence[BaseRoute]) -> list[str]:
    """Why this route table could serve /api without the session check (empty when it cannot).

    Walks every route as served (included routers flattened, prefixes applied):
    - every API route must run `default_deny`;
    - nothing else (plain Starlette route, websocket, mount) may serve a path under /api, where
      the check would not run, and no mount may sit at the root;
    - a route declared at a public path must be served at that same path (a router included
      under a prefix would otherwise carry its public status to the prefixed copy);
    - every PUBLIC_ROUTES entry must name a served route, so a renamed endpoint never leaves a
      stale entry behind.
    """
    problems = []
    served: set[tuple[str, str]] = set()
    for context in iter_route_contexts(routes):
        original = context.original_route
        path = context.path or ""
        if isinstance(original, APIRoute):
            methods = context.methods or set()
            served.update((method, path) for method in methods)
            if default_deny not in set(_calls(context.dependant)):
                problems.append(f"{sorted(methods)} {path} does not run default_deny")
            for method in methods:
                if (method, original.path) in PUBLIC_ROUTES and original.path != path:
                    problems.append(f"public route {original.path} is also served at {path}")
        elif is_api_path(path) or (isinstance(original, Mount) and path in ("", "/")):
            problems.append(f"{path or '/'} is served outside the session check")
    problems.extend(
        f"PUBLIC_ROUTES names {method} {path}, which no route serves"
        for method, path in sorted(PUBLIC_ROUTES - served)
    )
    return problems


def assert_default_deny(app: FastAPI) -> None:
    problems = default_deny_problems(app.routes)
    if problems:
        raise RuntimeError("Default deny is broken: " + "; ".join(problems))
