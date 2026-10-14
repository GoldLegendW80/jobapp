"""Xvfb, and Chromium for a lease: started on the person's profile under the profile's uid,
watched (process exit, CDP answers, memory, tabs), closed cleanly at the lease end.

browserd itself is root with SETUID and SETGID only. Every child that touches a profile or a
Chromium process runs under the profile's uid (`user=`, `group=`, no supplementary group): the
capabilities are dropped in the child by the uid change. Xvfb runs as the image's display user.
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import sys
from typing import Any

from browserd import config as c
from browserd.leases import LeaseError, Request

log = logging.getLogger("browserd")


def switches(request: Request, lease_dir: str, port: int) -> list[str]:
    """SPEC-browser's switches. Never --no-sandbox, never --headless. The user agent is
    Chromium's own (B1: "X11; Linux x86_64" with the client hint architecture "arm"): a
    fingerprint that lies is never set. WebGL stays off under Xvfb (no SwiftShader): the
    owner's question in B2's pull request."""
    return [
        "chromium",
        f"--user-data-dir={c.PROFILES}/{request.profile_id}/data",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1440,900",
        "--window-position=0,0",
        "--start-maximized",
        f"--lang={request.languages[0]}",
        "--password-store=basic",
        "--disable-features=Translate",
        # No traffic of Chromium's own (component updates, GCM, variations): what leaves is
        # what a page asks for.
        "--disable-background-networking",
        "--disable-component-update",
        # Every request through browserd's egress proxy (browserd/proxy.py), loopback included,
        # and no UDP outside it (WebRTC), so a page reaches public addresses and nothing else.
        f"--proxy-server=http://127.0.0.1:{c.PROXY_PORT}",
        "--proxy-bypass-list=<-loopback>",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "about:blank",
    ]


def child_env(request: Request) -> dict[str, str]:
    home = f"{c.PROFILES}/{request.profile_id}"
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "DISPLAY": c.DISPLAY,
        "TZ": request.timezone,
        "LANG": "C.UTF-8",
        "LANGUAGE": ":".join(lang.replace("-", "_") for lang in request.languages),
        "XDG_CONFIG_HOME": f"{home}/config",
        "XDG_CACHE_HOME": f"{home}/cache",
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        user=uid,
        group=uid,
        extra_groups=[],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/opt/browserd"},
        cwd="/",
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode:
        log.warning(
            "%s as uid %d: %s", argv[0], uid, err.decode(errors="replace")[-300:]
        )
    return proc.returncode or 0


async def kill_group(uid: int, pgid: int) -> None:
    """SIGKILL to a process group of a profile's uid, sent by a child under that uid: browserd
    has no CAP_KILL."""
    await as_uid(uid, sys.executable, "-m", "browserd.prep", "killpg", str(pgid))


def profile_uids() -> dict[int, list[int]]:
    """Processes running under a profile uid, by uid, read from /proc."""
    found: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            with contextlib.suppress(OSError):
                uid = os.stat(f"/proc/{entry}").st_uid
                if c.UID_MIN <= uid <= c.UID_MAX:
                    found.setdefault(uid, []).append(int(entry))
    return found


async def reap_strays() -> int:
    """At start, and after a lease: any process left under a profile uid is killed (a Chromium
    that outlived a browserd restart). Returns how many were found."""
    strays = profile_uids()
    for uid, pids in strays.items():
        await as_uid(
            uid, sys.executable, "-m", "browserd.prep", "kill", *map(str, pids)
        )
    return sum(len(p) for p in strays.values())


async def start_display() -> asyncio.subprocess.Process:
    os.makedirs("/tmp/.X11-unix", exist_ok=True)
    os.chmod("/tmp/.X11-unix", 0o1777)
    proc = await asyncio.create_subprocess_exec(
        "Xvfb", c.DISPLAY, "-screen", "0", c.SCREEN, "-nolisten", "tcp", "-ac",
        user=c.DISPLAY_UID, group=c.DISPLAY_UID, extra_groups=[],
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, cwd="/",
    )  # fmt: skip
    socket_path = f"/tmp/.X11-unix/X{c.DISPLAY[1:]}"
    for _ in range(100):
        if os.path.exists(socket_path) or proc.returncode is not None:
            break
        await asyncio.sleep(0.05)
    if not os.path.exists(socket_path):
        raise RuntimeError("Xvfb did not start")
    return proc


async def cdp_ready(port: int, deadline_s: float) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        with contextlib.suppress(OSError):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(  # HTTP/1.1: DevTools refuses HTTP/1.0
                b"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            )
            data = b""
            while (
                b"webSocketDebuggerUrl" not in data
            ):  # the server may keep the connection
                chunk = await asyncio.wait_for(reader.read(4096), 2)
                if not chunk:
                    break
                data += chunk
            writer.close()
            if b"webSocketDebuggerUrl" in data:
                return True
        await asyncio.sleep(0.1)
    return False


class Chromium:
    """One Chromium process for one lease, with its Playwright connection."""

    def __init__(
        self, request: Request, lease_id: str, playwright: Any, cfg: c.Config
    ) -> None:
        self.request, self.lease_id, self.playwright, self.cfg = (
            request,
            lease_id,
            playwright,
            cfg,
        )
        self.proc: asyncio.subprocess.Process | None = None
        self.port = 0
        self.browser: Any = None
        self.cdp: Any = None
        self.stopping = False
        self.on_crash: Any = (
            None  # set by the service: ends the lease `browser_crashed`
        )
        self.on_event: Any = None
        self._tasks: list[asyncio.Task[Any]] = []

    @property
    def lease_dir(self) -> str:
        return f"{c.LEASES}/{self.lease_id}"

    async def start(self) -> "Chromium":
        request = self.request
        profile = f"{c.PROFILES}/{request.profile_id}"
        with contextlib.suppress(FileNotFoundError):
            owner = os.stat(profile).st_uid
            if owner != request.uid:
                raise LeaseError(409, "profile_uid_mismatch")
        languages = ",".join(request.languages)
        if await as_uid(request.uid, sys.executable, "-m", "browserd.prep", "prepare", profile,
                        self.lease_dir, languages):  # fmt: skip
            raise RuntimeError("profile preparation failed")
        self.port = free_port()
        self.proc = await asyncio.create_subprocess_exec(
            *switches(request, self.lease_dir, self.port),
            user=request.uid, group=request.uid, extra_groups=[], start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            # cwd "/": the chdir happens before the uid change, as root without DAC_OVERRIDE.
            stderr=asyncio.subprocess.DEVNULL, env=child_env(request), cwd="/",
        )  # fmt: skip
        if not await cdp_ready(self.port, c.START_S):
            await self.kill()
            raise RuntimeError("Chromium gave no CDP endpoint")
        self.browser = await self.playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self.port}", timeout=c.START_S * 1000
        )
        self.cdp = await self.browser.new_browser_cdp_session()
        context = self.browser.contexts[0]
        context.on("page", self._on_page)
        self._write_state()
        self._tasks = [
            asyncio.create_task(self._watch_exit()),
            asyncio.create_task(self._watch()),
        ]
        return self

    def _write_state(self) -> None:
        """The CDP port of the current lease for browserd.probe, the development and test path
        that runs inside the container (`docker compose exec`); root only, never served."""
        os.makedirs(c.STATE_DIR, mode=0o700, exist_ok=True)
        path = f"{c.STATE_DIR}/current.json"
        with open(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as handle:
            json.dump({"port": self.port, "lease_id": self.lease_id}, handle)

    def _clear_state(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"{c.STATE_DIR}/current.json")

    def _event(self, **event: Any) -> None:
        if self.on_event:
            self.on_event(event)

    def _on_page(self, page: Any) -> None:
        # At most TABS_MAX tabs per session: a further one opened by a page is closed.
        if len(page.context.pages) > c.TABS_MAX:
            self._event(event="tab_closed", reason="tabs_max")
            asyncio.ensure_future(page.close())

    async def _watch_exit(self) -> None:
        assert self.proc is not None
        await self.proc.wait()
        if not self.stopping and self.on_crash:
            await self.on_crash()

    async def _watch(self) -> None:
        """Every 2 s: a CDP answer within CDP_ANSWER_S, else the lease ends `browser_crashed`;
        the container's memory against BROWSER_MEM_GUARD_PCT of its limit, past it the newest
        tab is closed (the session goes on)."""
        while not self.stopping:
            await asyncio.sleep(2)
            try:
                await asyncio.wait_for(
                    self.cdp.send("Browser.getVersion"), c.CDP_ANSWER_S
                )
            except Exception:  # noqa: BLE001 - no answer, a closed pipe: the same verdict
                if not self.stopping and self.on_crash:
                    log.warning(
                        "lease %s: no CDP answer in %d s", self.lease_id, c.CDP_ANSWER_S
                    )
                    await self.on_crash()
                return
            with contextlib.suppress(Exception):
                await guard_memory(self.browser, self.cfg.mem_guard_pct, self._event)

    async def stop(self) -> None:
        """Browser.close over CDP (profile flushed, B1: 0.32 s), the process group killed if it
        has not exited within CLOSE_S, the lease's directory removed."""
        self.stopping = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.cdp.send("Browser.close"), c.CLOSE_S)
        if self.proc and self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), c.CLOSE_S)
            except TimeoutError:
                await self.kill()
        await self._finish()

    async def kill(self) -> None:
        self.stopping = True
        if self.proc and self.proc.returncode is None:
            await kill_group(self.request.uid, self.proc.pid)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.proc.wait(), c.CLOSE_S)
        await self._finish()

    async def _finish(self) -> None:
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.browser.close(), 2)
        self._clear_state()
        await reap_strays()
        await as_uid(
            self.request.uid,
            sys.executable,
            "-m",
            "browserd.prep",
            "rmtree",
            self.lease_dir,
        )


def memory() -> tuple[int, int] | None:
    """(memory.current, memory.max) of the container's cgroup (v2), or None without a limit."""
    try:
        with (
            open("/sys/fs/cgroup/memory.current") as cur,
            open("/sys/fs/cgroup/memory.max") as top,
        ):
            current, limit = cur.read().strip(), top.read().strip()
    except OSError:
        return None
    return None if limit == "max" else (int(current), int(limit))


async def guard_memory(
    browser: Any, pct: int, event: Any, reader: Any = memory
) -> bool:
    """Closes the newest tab when the container is past `pct` % of its limit (a blank tab is
    opened first when it was the only one). True when a tab was closed."""
    figures = reader()
    if figures is None or figures[0] * 100 < figures[1] * pct:
        return False
    pages = browser.contexts[0].pages if browser.contexts else []
    if not pages:
        return False
    victim = pages[-1]
    if len(pages) == 1:
        await browser.contexts[0].new_page()
    await victim.close()
    event(event="tab_closed", reason="memory", used_mib=figures[0] >> 20)
    return True


def stop_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
