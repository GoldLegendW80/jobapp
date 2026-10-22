"""Run the Gmail boundary without database, OAuth credentials, or model credentials."""

import asyncio
from pathlib import Path

import redis.asyncio as redis
import uvicorn

from app.config import Settings
from app.gmailmcp.server import Service, build_app


async def main() -> None:
    settings = Settings(database_url="postgresql+psycopg://unused@invalid/unused")
    password_file = settings.gmail_mcp_redis_password_file
    if not password_file:
        raise RuntimeError("gmailmcp Redis password file is required")
    password = (await asyncio.to_thread(Path(password_file).read_text)).strip()
    store = redis.Redis(host="redis", username="gmailmcp", password=password)
    app = build_app(Service(settings, store))
    try:
        await uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",  # noqa: S104, internal Compose service only
                port=9400,
                access_log=False,
                log_level="warning",
            )
        ).serve()
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
