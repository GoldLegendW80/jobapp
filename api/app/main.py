"""FastAPI application factory. Run with: uvicorn app.main:create_app --factory"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import health
from app.agents import chat_router, proposals_router
from app.agents import router as agents_router
from app.agents import runs_router as agent_runs_router
from app.agents.crypto import Keyring
from app.agents.live import RedisLiveStore
from app.agents.ratelimit import RateLimiter
from app.agents.verify import Verifier
from app.body_limit import LimitRequestBodyMiddleware, SessionGate
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import deps as identity_deps
from app.identity import service as identity_service
from app.identity import throttle as identity_throttle
from app.identity.admin_router import router as admin_router
from app.identity.router import router as auth_router
from app.ingestion.routers import no_store as ingestion_no_store
from app.ingestion.routers import private as ingestion_private
from app.ingestion.routers import routers as ingestion_routers
from app.jobs.router import router as jobs_router
from app.jobs.sweep_router import router as sweeps_router
from app.logging_config import configure_logging
from app.profile.router import router as profile_router
from app.queue import Queue

logger = logging.getLogger("app")

# Path segments that carry a secret (invite and reset tokens), masked in the request log.
_SECRET_SEGMENT = re.compile(r"(/(?:invites?|reset)/)[^/]+")


def log_path(request: Request) -> str:
    """The path as logged: the route template when one matched (no parameter values), else the
    raw path with any link token masked."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    return _SECRET_SEGMENT.sub(r"\1[redacted]", request.url.path)


def validation_detail(exc: RequestValidationError) -> str:
    """One readable sentence for the first problem, with no echo of the submitted values."""
    errors = exc.errors()
    if not errors:  # pragma: no cover, pydantic always reports at least one error
        return "Invalid request."
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = str(first.get("msg", "invalid value"))
    return f"{where}: {message}" if where else f"Invalid request body: {message}"


def credentials_keyring(settings: Settings) -> Keyring | None:
    """The keys of the model credentials, from CREDENTIALS_KEY (and _PREVIOUS), or None."""
    if settings.credentials_key is None:
        return None
    previous = settings.credentials_key_previous
    return Keyring.from_text(
        settings.credentials_key.get_secret_value(),
        previous.get_secret_value() if previous is not None else None,
    )


def create_app(
    settings: Settings | None = None, clock: Callable[[], datetime] | None = None
) -> FastAPI:
    """Build the app. Raises ConfigError at once when the environment is incomplete.

    `clock` returns the current time (timezone-aware); tests pass a frozen one.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    sessions = build_sessionmaker(engine)
    checker = health.HealthChecker(sessions, settings.health_timeout_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Again at startup: routes added after create_app are covered too.
        identity_deps.assert_default_deny(app)
        logger.info(
            "api starting", extra={"env": settings.app_env, "database": settings.safe_database_url}
        )
        yield
        await app.state.queue.close()
        await checker.close()
        engine.dispose()
        logger.info("api stopped")

    app = FastAPI(
        title="jobapp api",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
        # Default deny: every route, including any added later, needs a session unless it is
        # in identity.deps.PUBLIC_ROUTES.
        dependencies=[Depends(identity_deps.default_deny)],
    )
    app.state.settings = settings
    app.state.clock = clock or identity_service.utcnow
    # Purges old sign in attempts at most once an hour in this process (identity.throttle).
    app.state.login_purger = identity_throttle.AttemptPurger()
    app.state.engine = engine
    app.state.sessionmaker = sessions
    app.state.health = checker
    # The worker's queue (jobs J5); the Redis pool opens on the first enqueue.
    app.state.queue = Queue(settings.redis_url.get_secret_value())
    # The answer an agent is writing, read from Redis by the run stream (app/agents/live.py).
    app.state.live_store = (
        RedisLiveStore(app.state.queue.pool) if settings.agents_live_text else None
    )
    # Model credentials (agents A1): the encryption keys (None without CREDENTIALS_KEY, then the
    # credential routes answer 503), the real check and the per-user limits.
    app.state.credentials_keyring = credentials_keyring(settings)
    if app.state.credentials_keyring is None:
        logger.warning("CREDENTIALS_KEY is not set: saving model credentials is disabled")
    app.state.model_verifier = Verifier(base_url=settings.model_api_base_url)
    app.state.credential_saves = RateLimiter(agents_router.SAVE_LIMIT, agents_router.WINDOW_S)
    app.state.credential_checks = RateLimiter(agents_router.VERIFY_LIMIT, agents_router.WINDOW_S)

    # Middleware added last runs first: request_log, then CSRF, then the cookie writer, then the
    # body limits closest to the router (app.body_limit, issue #100). Added first so that it is
    # the innermost: it stands around the read of the body that the router does before any
    # dependency runs, its refusals still pass out through request_log, and the cookie writer
    # outside it still clears a stale cookie on its 401. No session, no body: on every route
    # that is not public a request without a valid session is answered before its body is read.
    app.add_middleware(
        LimitRequestBodyMiddleware,
        gate=SessionGate(
            needs_session=lambda method, path: not identity_deps.is_public_request(method, path),
            has_session=identity_deps.scope_has_session,
            detail=identity_deps.UNAUTHORIZED,
        ),
    )
    app.middleware("http")(identity_deps.session_cookie_middleware)
    app.middleware("http")(identity_deps.csrf_middleware)
    # Around the two above, so the 401 and the 403 of an ingestion route are never cached.
    app.middleware("http")(ingestion_no_store)

    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    # The visitor's address: uvicorn --proxy-headers takes it from the single
                    # X-Forwarded-For that caddy sets (Funnel's, on the tunnel listener).
                    "client": request.client.host if request.client else None,
                    "method": request.method,
                    "path": log_path(request),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path})
        # Built outside every middleware: the no-store of the ingestion routes is set here.
        headers = {"Cache-Control": "no-store"} if ingestion_private(request.url.path) else None
        return JSONResponse(
            status_code=500, content={"detail": "internal server error"}, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the submitted input, a password included: never.
        return JSONResponse(status_code=422, content={"detail": validation_detail(exc)})

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(profile_router)
    # Before the jobs router: /api/jobs/sweeps must not be read as /api/jobs/{job_id}.
    app.include_router(sweeps_router)
    app.include_router(jobs_router)
    app.include_router(agents_router.router)
    app.include_router(agent_runs_router.router)
    app.include_router(proposals_router.router)
    app.include_router(chat_router.router)
    for ingestion_router in ingestion_routers:
        app.include_router(ingestion_router)
    identity_deps.assert_default_deny(app)
    return app
