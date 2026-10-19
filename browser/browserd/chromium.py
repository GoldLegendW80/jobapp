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
import secrets
import signal
import socket
import stat
import sys
import uuid
from typing import Any

from browserd import config as c
from browserd.leases import LeaseError, Request

log = logging.getLogger("browserd")

# At a lease end a profile over this size loses its caches (SPEC-browser: "over 1 GB").
TRIM_BYTES = 1 << 30
MEASURE_S = 120  # the walk and the trim of a large profile, as its uid


def switches(
    request: Request, lease_dir: str, port: int | None, address: str = "about:blank"
) -> list[str]:
    """SPEC-browser's switches. Never --no-sandbox, never --headless. The user agent is
    Chromium's own (B1: "X11; Linux x86_64" with the client hint architecture "arm"): a
    fingerprint that lies is never set. WebGL stays off under Xvfb (no SwiftShader): the
    owner's question in B2's pull request. `port` None: no debugging port at all (a person's
    own session, browserd/signin.py). The address comes last, after `--`, so Chromium never
    reads it as a switch; the first `--` stops the option loop of Debian's wrapper script
    (/usr/bin/chromium), which would otherwise drop the second one."""
    debugging = (
        []
        if port is None
        else ["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"]
    )
    return [
        "chromium",
        "--",
        f"--user-data-dir={c.PROFILES}/{request.profile_id}/data",
        *debugging,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1440,900",
        "--window-position=0,0",
        "--start-maximized",
        f"--lang={request.languages[0]}",
        "--password-store=basic",
        # WebRtcHideLocalIpsWithMdns: no mDNS responder (UDP 224.0.0.251:5353, review of PR
        # #267); with no UDP outside the proxy there is no local candidate for it to hide.
        # One --disable-features carries every feature: a repeated switch may replace it.
        "--disable-features=Translate,WebRtcHideLocalIpsWithMdns",
        # No traffic of Chromium's own (component updates, GCM, variations): what leaves is
        # what a page asks for.
        "--disable-background-networking",
        "--disable-component-update",
        # Every request through browserd's egress proxy (browserd/proxy.py), loopback included,
        # and no UDP outside it (WebRTC), so a page reaches public addresses and nothing else.
        f"--proxy-server=http://127.0.0.1:{c.PROXY_PORT}",
        "--proxy-bypass-list=<-loopback>",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        # Chromium resolves no name itself (the proxy resolves every one), so a page cannot send
        # a DNS query out of the proxy, a WebRTC STUN server's name for instance.
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        "--disable-quic",
        "--",
        address,
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


async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
    return (await uid_output(uid, *argv, timeout=timeout))[0]


async def uid_output(uid: int, *argv: str, timeout: float = 15) -> tuple[int, str]:  # noqa: ASYNC109
    """Runs argv under that uid; its exit code and what it printed (at most a few lines)."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        user=uid,
        group=uid,
        extra_groups=[],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/opt/browserd"},
        cwd="/",
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode:
        log.warning("%s as uid %d: %s", argv[0], uid, err.decode(errors="replace")[-300:])
    return proc.returncode or 0, out.decode(errors="replace")[-200:]


async def measure_profile(request: Request) -> int | None:
    """At a lease end, once its Chromium is gone: the profile trimmed of its caches when it is
    over TRIM_BYTES, then its size in bytes (browserd.prep, as the profile's uid)."""
    code, out = await uid_output(
        request.uid, sys.executable, "-m", "browserd.prep", "measure",
        f"{c.PROFILES}/{request.profile_id}", str(TRIM_BYTES), timeout=MEASURE_S,
    )  # fmt: skip
    return int(out.strip()) if code == 0 and out.strip().isdigit() else None


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


def make_uid_records(root: str = c.UIDS) -> None:
    """At start, before any lease: the record directory, browserd's own (root, 0700). It lives on
    the profiles volume, which every profile uid may write (1733), so bind_uid checks it again."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(root, 0o700)


def bind_uid(uid: int, profile_id: str, root: str = c.UIDS) -> None:
    """One uid, one profile, for ever (review of PR #267): the first lease of a uid records its
    profile, and a later lease of that uid for another profile is refused 409 `uid_in_use`, so a
    new profile can never be created under a uid that another profile holds. B3 allocates the
    uids from a sequence that is never reused; this is browserd's own check of it. The record is
    written whole then linked into place, so a crash leaves no half written record. When the
    directory is not browserd's own (a profile uid made it first, or it is a link) every lease
    is refused 503 `uid_records_unsafe`: the check never runs on a record another uid wrote."""
    try:
        info = os.lstat(root)
    except OSError:
        raise LeaseError(503, "uid_records_unsafe") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise LeaseError(503, "uid_records_unsafe")
    record = f"{root}/{uid}"
    draft = f"{root}/.{uid}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with open(os.open(draft, flags, 0o600), "w") as handle:
        handle.write(profile_id)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(draft, record)
    except FileExistsError:
        try:
            with open(os.open(record, os.O_RDONLY | os.O_NOFOLLOW)) as handle:
                held = handle.read()
        except OSError:
            raise LeaseError(503, "uid_records_unsafe") from None
        if held != profile_id:
            raise LeaseError(409, "uid_in_use") from None
    else:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(draft)


# The record of a uid that owns two profile directories: never a profile id (those are uuids), so
# bind_uid refuses every lease of that uid.
AMBIGUOUS = "ambiguous"


def seed_uid_records(profiles: str = c.PROFILES, root: str = c.UIDS, lstat=os.lstat) -> int:
    """At start, after make_uid_records and before the first lease (review of PR #294, issue
    #230): a profile directory made before the records existed binds its owner uid, so a new
    profile can never be created under that uid. Only a real directory named as a profile id
    and owned by a profile uid counts; a uid owning two such directories is ambiguous and gets
    the record AMBIGUOUS, which names no profile, so every lease of that uid is refused 409
    (logged). A uid whose record already names another profile keeps it, and nothing is seeded
    when the record directory is not browserd's own (every lease is refused 503 then).
    Returns how many records were written."""
    owners: dict[int, list[str]] = {}
    for name in os.listdir(profiles):
        try:
            if str(uuid.UUID(name)) != name:
                continue
            info = lstat(f"{profiles}/{name}")
        except (ValueError, OSError):
            continue
        if stat.S_ISDIR(info.st_mode) and c.UID_MIN <= info.st_uid <= c.UID_MAX:
            owners.setdefault(info.st_uid, []).append(name)
    seeded = 0
    for uid, names in sorted(owners.items()):
        profile = names[0]
        if len(names) > 1:
            log.warning("uid %d owns %d profile directories: leases refused", uid, len(names))
            profile = AMBIGUOUS
        known = os.path.lexists(f"{root}/{uid}")
        try:
            bind_uid(uid, profile, root)  # an existing record that agrees is left as it is
        except LeaseError as exc:  # another profile's record, or records that are not safe
            log.warning("uid %d: not seeded (%s)", uid, exc.code)
            continue
        seeded += not known
    return seeded


async def reap_strays() -> int:
    """At start, and after a lease: any process left under a profile uid is killed (a Chromium
    that outlived a browserd restart). Returns how many were found."""
    strays = profile_uids()
    for uid, pids in strays.items():
        await as_uid(uid, sys.executable, "-m", "browserd.prep", "kill", *map(str, pids))
    return sum(len(p) for p in strays.values())


async def start_display() -> asyncio.subprocess.Process:
    # X11's own socket directory, inside the container's private /tmp.
    os.makedirs("/tmp/.X11-unix", exist_ok=True)  # noqa: S108
    os.chmod("/tmp/.X11-unix", 0o1777)  # noqa: S103, S108
    proc = await asyncio.create_subprocess_exec(
        "Xvfb", c.DISPLAY, "-screen", "0", c.SCREEN, "-nolisten", "tcp", "-ac",
        user=c.DISPLAY_UID, group=c.DISPLAY_UID, extra_groups=[],
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, cwd="/",
    )  # fmt: skip
    socket_path = f"/tmp/.X11-unix/X{c.DISPLAY[1:]}"  # noqa: S108
    for _ in range(100):
        if os.path.exists(socket_path) or proc.returncode is not None:  # noqa: ASYNC240
            break
        await asyncio.sleep(0.05)
    if not os.path.exists(socket_path):  # noqa: ASYNC240 - one stat at start
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
            while b"webSocketDebuggerUrl" not in data:  # the server may keep the connection
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

    def __init__(self, request: Request, lease_id: str, playwright: Any, cfg: c.Config) -> None:
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
        self.on_crash: Any = None  # set by the service: ends the lease `browser_crashed`
        self.on_end: Any = None  # set by the service: ends the lease with the reason given
        self.on_event: Any = None
        self._tasks: list[asyncio.Task[Any]] = []

    @property
    def lease_dir(self) -> str:
        return f"{c.LEASES}/{self.lease_id}"

    async def prepare(self) -> None:
        request = self.request
        profile = f"{c.PROFILES}/{request.profile_id}"
        with contextlib.suppress(FileNotFoundError):
            owner = os.stat(profile).st_uid
            if owner != request.uid:
                raise LeaseError(409, "profile_uid_mismatch")
        bind_uid(request.uid, request.profile_id)
        languages = ",".join(request.languages)
        if await as_uid(request.uid, sys.executable, "-m", "browserd.prep", "prepare", profile,
                        self.lease_dir, languages):  # fmt: skip
            raise RuntimeError("profile preparation failed")

    async def spawn(
        self, port: int | None, address: str = "about:blank"
    ) -> asyncio.subprocess.Process:
        request = self.request
        self.proc = await asyncio.create_subprocess_exec(
            *switches(request, self.lease_dir, port, address),
            user=request.uid, group=request.uid, extra_groups=[], start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            # cwd "/": the chdir happens before the uid change, as root without DAC_OVERRIDE.
            stderr=asyncio.subprocess.DEVNULL, env=child_env(request), cwd="/",
        )  # fmt: skip
        return self.proc

    async def start(self) -> "Chromium":
        await self.prepare()
        self.port = free_port()
        await self.spawn(self.port)
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
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
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
            self._tasks.append(asyncio.ensure_future(page.close()))

    async def _watch_exit(self) -> None:
        if self.proc is None:
            return
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
                await asyncio.wait_for(self.cdp.send("Browser.getVersion"), c.CDP_ANSWER_S)
            except Exception:
                if not self.stopping and self.on_crash:
                    log.warning("lease %s: no CDP answer in %d s", self.lease_id, c.CDP_ANSWER_S)
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


CGROUP = "/sys/fs/cgroup"
# Under CGROUP, v2 then v1: usage, limit, memory.stat and its key of the inactive file cache.
CGROUP_FILES = (
    ("memory.current", "memory.max", "memory.stat", "inactive_file"),
    (
        "memory/memory.usage_in_bytes",
        "memory/memory.limit_in_bytes",
        "memory/memory.stat",
        "total_inactive_file",
    ),
)


def working_set(usage: int, stat: str, key: str) -> int:
    """`usage` less the inactive file cache (`key` in the text of memory.stat), never below zero:
    docker stats' figure. Without the key, `usage` whole, the page cache counted."""
    for line in stat.splitlines():
        name, _, value = line.partition(" ")
        if name == key and value.strip().isdecimal():
            return max(0, usage - int(value))
    return usage


def memory(root: str = CGROUP) -> tuple[int, int] | None:
    """(working set, limit) of the container's cgroup, v2 else v1, or None without a limit. The
    inactive page cache is left out: the kernel reclaims it before it kills a process, and on a
    Mac it held about 770 MiB of the browser's 1024m at rest."""
    for usage, limit, stats, key in CGROUP_FILES:
        try:
            with open(f"{root}/{usage}") as cur, open(f"{root}/{limit}") as top:
                current, ceiling = cur.read().strip(), top.read().strip()
        except OSError:
            continue
        if not (current.isdecimal() and ceiling.isdecimal()):
            return None
        try:
            with open(f"{root}/{stats}") as figures:
                listed = figures.read()
        except OSError:
            listed = ""
        return working_set(int(current), listed, key), int(ceiling)
    return None


def past_guard(figures: tuple[int, int], pct: int) -> bool:
    """The container's (working set, limit) of `memory()` is at or past `pct` % of its limit."""
    return figures[0] * 100 >= figures[1] * pct


async def guard_memory(browser: Any, pct: int, event: Any, reader: Any = memory) -> bool:
    """Closes the newest tab when the container is past `pct` % of its limit (a blank tab is
    opened first when it was the only one). True when a tab was closed."""
    figures = reader()
    if figures is None or not past_guard(figures, pct):
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
