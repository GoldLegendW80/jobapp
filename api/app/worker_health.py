"""The worker container's healthcheck, `python -m app.worker_health`: exit 0 when every queue's
worker recorded its health in Redis within arq's interval, 1 otherwise.

Apart from app.worker on purpose: importing the worker loads every job and agent module, and the
image keeps no bytecode, so on the Jetson that import alone outgrew the check's 8 s and a healthy
worker was rolled back (deploy of 2026-11-08). This module imports the queue names and Redis only.
"""

import asyncio
import sys
from collections.abc import Sequence

from arq.connections import create_pool

from app import queue
from app.config import get_settings

# Every queue app.worker.queues() serves; tests/jobs/test_worker.py keeps the two equal.
NAMES = (
    queue.SWEEPS,
    queue.AGENTS,
    queue.INGESTION,
    queue.DOCUMENTS,
    queue.APPLY,
    queue.APPLY_HOLD,
)


async def check(redis_url: str, names: Sequence[str] = NAMES) -> int:
    """0 when every queue's worker recorded its health recently, 1 otherwise."""
    try:
        async with asyncio.timeout(5):
            pool = await create_pool(queue.redis_settings(redis_url))
            try:
                for name in names:
                    if not await pool.exists(name + ":health-check"):
                        print(f"{name}: no recent health record", file=sys.stderr)
                        return 1
            finally:
                await pool.aclose()
    except Exception as exc:
        print(f"redis unreachable: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return asyncio.run(check(get_settings().redis_url.get_secret_value()))


if __name__ == "__main__":
    sys.exit(main())
