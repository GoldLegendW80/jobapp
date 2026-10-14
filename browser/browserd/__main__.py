"""`python -m browserd`: the service under tini. `python -m browserd --check`: the healthcheck.

At start: every process left under a profile uid is killed (a Chromium that outlived browserd),
the lease state is empty (no lease survives a restart of browserd or of the container), Xvfb is
started, then the egress proxy and the control API. Every second: the lease's clocks (maximum,
idle, the person's wait), Xvfb alive, the heartbeat the healthcheck reads. SIGTERM: the lease
is released, Chromium closed cleanly (Browser.close, profile flushed), the process exits 0,
within the 15 s compose gives it.
"""

import asyncio
import contextlib
import logging
import os
import sys
import time

from browserd import chromium, proxy
from browserd import config as c
from browserd.app import Service, handler_for
from browserd.leases import Manager, Request

HEARTBEAT = f"{c.STATE_DIR}/heartbeat"
log = logging.getLogger("browserd")


def check() -> int:
    """Healthy while the loop wrote its heartbeat within 15 s (it writes it only while Xvfb
    lives). Reads no secret: the stack is healthy before the api holds one."""
    try:
        return 0 if time.time() - os.stat(HEARTBEAT).st_mtime < 15 else 1
    except OSError:
        return 1


def beat() -> None:
    os.close(os.open(HEARTBEAT, os.O_WRONLY | os.O_CREAT, 0o600))
    os.utime(HEARTBEAT)


async def main() -> None:
    cfg = c.Config.from_env()
    if not cfg.secret:
        log.warning(
            "BROWSER_CONTROL_SECRET is not set: every control request is refused"
        )
    os.makedirs(c.STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(
        c.PROFILES, 0o1733
    )  # anyone creates an entry, nobody lists, owners remove theirs
    os.chmod(c.LEASES, 0o1733)
    killed = await chromium.reap_strays()
    if killed:
        log.warning("killed %d process(es) left under a profile uid", killed)
    display = await chromium.start_display()

    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    manager: Manager

    async def start(request: Request, lease_id: str) -> chromium.Chromium:
        browser = chromium.Chromium(request, lease_id, playwright, cfg)

        async def crashed() -> None:
            await manager.release(lease_id, "browser_crashed")

        def event(item: dict) -> None:
            lease = manager.current()
            if lease and lease.lease_id == lease_id:
                lease.events.append(item)
            log.info("lease event %s", item)

        browser.on_crash, browser.on_event = crashed, event
        return await browser.start()

    manager = Manager(cfg, start)
    service = Service(manager, cfg.secret)
    service.display_ok = lambda: display.returncode is None
    egress = await proxy.serve(proxy.Proxy(cfg.proxy_allow), c.PROXY_PORT)
    server = await asyncio.start_server(handler_for(service), "0.0.0.0", c.CONTROL_PORT)
    stop = asyncio.Event()
    chromium.stop_signals(asyncio.get_running_loop(), stop)
    log.info("browserd ready on :%d", c.CONTROL_PORT)
    while not stop.is_set():
        reason = await manager.tick()
        if reason:
            log.info("lease ended: %s", reason)
        if display.returncode is None:
            beat()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 1)
    log.info("SIGTERM: closing")
    server.close()
    egress.close()
    if await manager.release(None, "shutdown"):
        log.info("chromium closed")
    with contextlib.suppress(Exception):
        await playwright.stop()
    # Xvfb is the display user's: browserd has no CAP_KILL, so a child of that uid stops it.
    await chromium.as_uid(c.DISPLAY_UID, sys.executable, "-m", "browserd.prep", "kill",
                          str(display.pid))  # fmt: skip


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    asyncio.run(main())
