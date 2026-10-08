"""FastAPI application factory. Run with: uvicorn app.main:create_app --factory"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import health
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker
from app.logging_config import configure_logging

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Raises ConfigError at once when the environment is incomplete."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    sessions = build_sessionmaker(engine)
    checker = health.HealthChecker(sessions, settings.health_timeout_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "api starting", extra={"env": settings.app_env, "database": settings.safe_database_url}
        )
        yield
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
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessions
    app.state.health = checker

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
                    # X-Forwarded-For that caddy sets (Cf-Connecting-Ip through the tunnel).
                    "client": request.client.host if request.client else None,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    app.include_router(health.router)
    return app
