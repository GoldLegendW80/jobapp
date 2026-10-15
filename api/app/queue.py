"""The Redis queue between the api and the worker: arq (decision in tasks/jobs/todo.md, J5).

Queues are named lists in Redis, each consumed by its own arq Worker inside the `worker`
container (app/worker.py): SWEEPS (jobs J5), AGENTS (agents A2, the user's agent runs) and
INGESTION (ingestion G2b, the reading of uploaded documents, one at a time), DOCUMENTS
(documents D6, the renders of CVs and letters, one at a time, and the module's cron jobs) and
APPLY (apply A5, the fill of one application form at a time, and its tick). A
later lane for automatic agent runs (mail triage, scheduled assessments) gets a queue name of its
own here, consumed after the interactive one, so a batch never delays a run someone is watching.

Messages are JSON, never pickle (arq's default): a job is a function name and string arguments,
so nothing read from Redis can execute code. Job ids make enqueueing idempotent: arq refuses a
second job with the id of one still queued, running or kept as a result, so a sweep id or a
scheduled (user, slot) pair is enqueued at most once.

The api holds one lazily made connection pool (`Queue`); when Redis is down, `enqueue` raises
QueueUnavailableError after at most CONNECT_TIMEOUT_S instead of retrying for seconds.
"""

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

SWEEPS = "jobapp:sweeps"
AGENTS = "jobapp:agents"
INGESTION = "jobapp:ingestion"
DOCUMENTS = "jobapp:documents"
APPLY = "jobapp:apply"
CONNECT_TIMEOUT_S = 2


class QueueUnavailableError(Exception):
    """Redis could not be reached or refused the job."""


def serialize(job: dict[str, Any]) -> bytes:
    return json.dumps(job, separators=(",", ":"), default=str).encode()


def deserialize(raw: bytes) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(raw)
    return data


def redis_settings(url: str) -> RedisSettings:
    settings = RedisSettings.from_dsn(url)
    settings.conn_timeout = CONNECT_TIMEOUT_S
    settings.conn_retries = 0
    return settings


def sweep_job_id(sweep_id: object) -> str:
    return f"sweep:{sweep_id}"


def schedule_job_id(user_id: object, slot: str) -> str:
    return f"schedule:{user_id}:{slot}"


def agent_run_job_id(run_id: object) -> str:
    return f"agent-run:{run_id}"


def document_job_id(document_id: object, attempts: int, pending_since: datetime | None) -> str:
    """One id per try of one upload: the same document, tried again or uploaded again, is
    another job; the same try asked for twice (by the upload and by the reaper) is one."""
    since = int(pending_since.timestamp() * 1_000_000) if pending_since is not None else 0
    return f"document:{document_id}:{attempts}:{since}"


def render_job_id(document_id: object, revision: int) -> str:
    """One render per (document, draft revision): asking again while it waits is the same job."""
    return f"render:{document_id}:{revision}"


def apply_fill_job_id(application_id: object) -> str:
    """One fill per application at a time: the tick asking again while it waits or runs is the
    same job (its result is not kept, so the next tick may queue it again once it ended)."""
    return f"apply-fill:{application_id}"


def apply_submit_job_id(application_id: object) -> str:
    """One submit lease per application at a time (apply A6): the approve route and the tick
    asking for the same `approved` application make one job."""
    return f"apply-submit:{application_id}"


class Queue:
    """The api's handle on the queues: one pool, made on first use."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def pool(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(
                    redis_settings(self.url),
                    job_serializer=serialize,
                    job_deserializer=deserialize,
                    default_queue_name=SWEEPS,
                )
            return self._pool

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        """Queue `function(*args)` on `queue` under `job_id`. Returns False when a job with that
        id already exists. Raises QueueUnavailableError when Redis cannot take it."""
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S * 2):
                pool = await self.pool()
                job = await pool.enqueue_job(function, *args, _job_id=job_id, _queue_name=queue)
        except (TimeoutError, OSError, ConnectionError) as exc:
            await self.reset()
            raise QueueUnavailableError(str(exc)) from None
        except Exception as exc:  # redis-py's own errors (ConnectionError, ResponseError, ...)
            if type(exc).__module__.startswith("redis"):
                await self.reset()
                raise QueueUnavailableError(type(exc).__name__) from None
            raise
        return job is not None

    async def reset(self) -> None:
        async with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            # Closing a broken pool: nothing more to do when that fails too.
            with contextlib.suppress(Exception):
                await pool.aclose()

    async def close(self) -> None:
        await self.reset()
