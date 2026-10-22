"""Run the Gmail boundary without database, OAuth credentials, or model credentials."""

import asyncio
from pathlib import Path

import redis.asyncio as redis
import uvicorn

from app.gmailmcp.server import Service, build_app
from app.gmailmcp.settings import ProviderSettings


async def main() -> None:
    settings = ProviderSettings()
    password_file = settings.gmail_mcp_redis_password_file
    if settings.mail_send_key and not password_file:
        raise RuntimeError("gmailmcp Redis password file is required for sending")
    password = (
        (await asyncio.to_thread(Path(password_file).read_text)).strip() if password_file else None
    )
    store = redis.Redis(host="redis", username="gmailmcp", password=password) if password else None
    app = build_app(Service(settings, store))
    try:
        await uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",  # noqa: S104, internal Compose service only
                port=9400,
                access_log=False,
                log_level="warning",
                limit_concurrency=16,
                timeout_keep_alive=5,
            )
        ).serve()
    finally:
        if store is not None:
            await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
