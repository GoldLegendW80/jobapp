"""The Redis queue between the api and the worker: arq (decision in tasks/jobs/todo.md, J5).

Queues are named lists in Redis, each consumed by its own arq Worker inside the `worker`
container (app/worker.py). Today: SWEEPS. The agents module adds AGENTS the same way: a queue
name here, a QueueSpec in the worker, its job functions; nothing else changes.

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
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

SWEEPS = "jobapp:sweeps"
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
