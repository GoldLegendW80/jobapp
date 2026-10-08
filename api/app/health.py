"""GET /api/health: the service is up and the database answers a round trip."""

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter()
logger = logging.getLogger(__name__)

HEALTH_STATEMENT_TIMEOUT_MS = 1000
NO_STORE = {"Cache-Control": "no-store"}


class HealthOk(BaseModel):
    status: Literal["ok"]


class HealthDown(BaseModel):
    status: Literal["error"]
    detail: str


def ping(factory: sessionmaker[Session]) -> None:
    """Run `select 1` under a short statement timeout local to its transaction."""
    with factory() as session, session.begin():
        session.execute(
            text("select set_config('statement_timeout', :ms, true)"),
            {"ms": str(HEALTH_STATEMENT_TIMEOUT_MS)},
        )
        session.execute(text("select 1")).scalar_one()


class HealthChecker:
    """Runs pings on a small dedicated thread pool under an overall deadline.

    A database that accepts the connection and then never answers (paused, network partition)
    cannot hang a request, and stuck pings cannot pile up beyond the pool size: later checks
    queue behind them and time out with 503.
    """

    def __init__(self, factory: sessionmaker[Session], timeout_s: float, workers: int = 2) -> None:
        self.factory = factory
        self.timeout_s = timeout_s
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="health")
        self.in_flight: set[Future[None]] = set()

    async def check(self) -> str | None:
        """None when the database answered, else the reason it did not."""
        future = self.executor.submit(ping, self.factory)
        self.in_flight.add(future)
        future.add_done_callback(self.in_flight.discard)
        try:
            await asyncio.wait_for(asyncio.wrap_future(future), self.timeout_s)
        except TimeoutError:
            return f"no answer within {self.timeout_s}s"
        except SQLAlchemyError as exc:
            return type(exc.orig or exc).__name__
        return None

    async def close(self, grace_s: float = 2.0) -> None:
        """Give running pings a short grace to return their connections, then let go."""
        self.executor.shutdown(wait=False, cancel_futures=True)
        deadline = asyncio.get_running_loop().time() + grace_s
        # Polling, not an Event: the futures complete on worker threads.
        while self.in_flight and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


@router.get(
    "/api/health",
    response_model=HealthOk,
    responses={503: {"model": HealthDown, "description": "The database did not answer"}},
)
async def health(request: Request) -> JSONResponse:
    """200 {"status": "ok"} after a database round trip, else 503 within the health timeout."""
    reason = await request.app.state.health.check()
    if reason is None:
        return JSONResponse({"status": "ok"}, headers=NO_STORE)
    logger.warning("health check failed", extra={"reason": reason})
    return JSONResponse(
        status_code=503,
        content={"status": "error", "detail": "database unavailable"},
        headers=NO_STORE,
    )
