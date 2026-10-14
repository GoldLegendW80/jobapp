"""The administrator's pause of every submission (SPEC-apply, "Guardrails", 1): a Redis flag that
`./deploy.sh apply pause|resume|status` sets, clears and reads through this module's command line
in the api container. The gate reads it in `decide` (the approve route) and again just before the
click (`submit.py`), beside the setting `APPLY_SUBMIT_ENABLED`.

Reading fails closed: a Redis that cannot be read counts as paused, so nothing is clicked while
the flag is unknown (test_a_redis_that_cannot_be_read_counts_as_paused).

    python -m app.apply.admin pause|resume|status
"""

import argparse
import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

PAUSE_KEY = "jobapp:apply:paused"


class Flags(Protocol):
    async def get(self, name: str) -> Any: ...
    async def set(self, name: str, value: str) -> Any: ...
    async def delete(self, *names: str) -> Any: ...


async def paused(redis: Flags | None) -> bool:
    """True when the administrator paused submissions, or when the flag cannot be read."""
    if redis is None:
        return True
    try:
        return bool(await redis.get(PAUSE_KEY))
    except Exception as exc:  # any failure to read: paused, never "go"
        logger.warning("apply.pause_unreadable", extra={"error": type(exc).__name__})
        return True


async def pause(redis: Flags) -> None:
    await redis.set(PAUSE_KEY, "1")


async def resume(redis: Flags) -> None:
    await redis.delete(PAUSE_KEY)


async def _command(action: str, url: str) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(url)
    try:
        if action == "pause":
            await pause(redis)
        elif action == "resume":
            await resume(redis)
        return "paused" if await paused(redis) else "running"
    finally:
        await redis.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.apply.admin")
    parser.add_argument("action", choices=("pause", "resume", "status"))
    args = parser.parse_args(argv)
    from app.config import get_settings

    url = get_settings().redis_url.get_secret_value()
    print(f"apply submissions: {asyncio.run(_command(args.action, url))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
