"""Xvfb, and Chromium for a lease: started on the person's profile under the profile's uid,
watched (process exit, CDP answers, memory, tabs), closed cleanly at the lease end.

browserd itself is root with SETUID and SETGID only. Every child that touches a profile or a
Chromium process runs under the profile's uid (`user=`, `group=`, no supplementary group): the
capabilities are dropped in the child by the uid change. Xvfb runs as the image's display user.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import secrets
import signal
import socket
import stat
import sys
import time
import uuid
from typing import Any

from browserd import config as c
from browserd.leases import LeaseError, Request

log = logging.getLogger("browserd")

# At a lease end a profile over this size loses its caches (SPEC-browser: "over 1 GB").
TRIM_BYTES = 1 << 30
MEASURE_S = 120  # the walk and the trim of a large profile, as its uid
REAP_TRIES = 100  # 50 ms apart: what a reap killed is gone in 5 s, or the next start is refused


# Software WebGL "when necessary" (owner, 2026-10-14, issue #264): off by default, as it costs
# the GPU process's memory; on for a sweep lease of a website whose bot check fails without it.
# 2026-10-30: Indeed, signed in, showed only Cloudflare's check and "Navigateur non pris en
# charge" (browser not supported), and WebGL is one thing that browser lacked. The lease's
# hosts tell the website (api finder.lease_hosts: the platform's domain, then its wildcard or
# its `www.` host), so a sweep whose first host is one of WEBGL_DOMAINS (or its `.test` stand
# in) starts Chromium with SwiftShader through ANGLE. Declared open points: its memory was not
# measured on the Jetson (the 90 % guard still closes a tab past it), nor whether Debian's
# arm64 Chromium ships SwiftShader (without it WebGL stays off, as before).
WEBGL_DOMAINS = frozenset({"indeed.com", "indeed.test"})
WEBGL_SWITCHES = ("--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader")


def webgl(request: Request) -> bool:
    """Whether this lease's Chromium starts with software WebGL (WEBGL_DOMAINS)."""
    return request.purpose == "sweep" and bool(request.hosts) and request.hosts[0] in WEBGL_DOMAINS


def switches(
    request: Request, lease_dir: str, port: int | None, address: str = "about:blank"
) -> list[str]:
    """SPEC-browser's switches. Never --no-sandbox, never --headless. The user agent is
    Chromium's own (B1: "X11; Linux x86_64" with the client hint architecture "arm"): a
    fingerprint that lies is never set. WebGL stays off under Xvfb (no SwiftShader): the
    owner's question in B2's pull request; on only for a sweep of a website that needs it
    (WEBGL_DOMAINS). `port` None: no debugging port at all (a person's
    own session, browserd/signin.py), and no --disable-blink-features=AutomationControlled
    either: nothing automates that session. The address comes last, after `--`, so Chromium
    never reads it as a switch; the first `--` stops the option loop of Debian's wrapper script
    (/usr/bin/chromium), which would otherwise drop the second one. A sweep lease, and no other,
    also disables IsolateSandboxedIframes (B17): a search, signed in or not, runs only in a sweep
    lease, and its purpose is known at launch while a signed in scope arrives later, so every
    sweep lease carries it and every other lease keeps Chromium's default."""
    debugging = (
        []
        if port is None
        else [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    sandboxed = ",IsolateSandboxedIframes" if request.purpose == "sweep" else ""
    return [
        "chromium",
        "--",
        f"--user-data-dir={c.PROFILES}/{request.profile_id}/data",
        *debugging,
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,900",
        "--window-position=0,0",
        "--start-maximized",
        f"--lang={request.languages[0]}",
        "--password-store=basic",
        # WebRtcHideLocalIpsWithMdns: no mDNS responder (UDP 224.0.0.251:5353, review of PR
        # #267); with no UDP outside the proxy there is no local candidate for it to hide.
        # IsolateSandboxedIframes (sweep leases only, B17): an inline sandboxed frame
        # (`<iframe sandbox srcdoc>`) runs in its embedder's process again, so the signed in
        # search's init script and route reach it; isolated, its script ran before them and its
        # POST and sockets left (measured in Chromium 153).
        # One --disable-features carries every feature: a repeated switch may replace it.
        f"--disable-features=Translate,WebRtcHideLocalIpsWithMdns{sandboxed}",
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
        *(WEBGL_SWITCHES if webgl(request) else ()),
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
    """Live processes running under a profile uid, by uid, read from /proc. A zombie (exited, not
    yet waited for by its parent) is left out: it holds no window and cannot be killed."""
    found: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            with contextlib.suppress(OSError, ValueError):
                uid = real_uid(entry)
                if c.UID_MIN <= uid <= c.UID_MAX and not zombie(entry):
                    found.setdefault(uid, []).append(int(entry))
    return found


def real_uid(pid: str) -> int:
    """The process's real uid, from its status: the uid a kill(-1) sent as that uid reaches. The
    owner of /proc/<pid> is the effective uid, the same here (no setuid program runs)."""
    with open(f"/proc/{pid}/status", "rb") as handle:
        for line in handle:
            if line.startswith(b"Uid:"):
                return int(line.split()[1])
    raise ValueError(pid)


def zombie(pid: str) -> bool:
    with open(f"/proc/{pid}/stat", "rb") as handle:
        line = handle.read()
    # The state follows the command name, which is in parentheses and may hold any byte.
    return line[line.rindex(b")") + 2 :][:1] == b"Z"


def listening_ports(uid: int) -> list[int]:
    """The TCP ports a process of that uid listens on, read from the container's /proc/net/tcp
    and tcp6 (a socket's uid is its owner's); a table that cannot be read counts nothing."""
    ports: list[int] = []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name) as handle:
                rows = handle.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) > 7 and fields[3] == "0A" and fields[7] == str(uid):
                ports.append(int(fields[1].rsplit(":", 1)[1], 16))
    return ports


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
    """At start, before every lease and after it: every process left under a profile uid is killed
    (a Chromium that outlived a browserd restart or an end that failed), then waited for, at most
    REAP_TRIES times. Each uid's processes go in one kill(-1) sent as that uid, which no fork can
    outrun; a list of pids would miss the children forked after it was read. Returns how many
    were found."""
    strays = profile_uids()
    for uid in strays:
        await as_uid(uid, sys.executable, "-m", "browserd.prep", "killuid")
    for _ in range(REAP_TRIES if strays else 0):
        if not profile_uids():
            break
        await asyncio.sleep(0.05)
    return sum(len(p) for p in strays.values())


async def clear_display() -> None:
    """Before every start: no process of a profile uid is left, so nothing of an earlier lease,
    perhaps another person's, is on the display the live view shows. A start is refused while
    one survives the reap."""
    killed = await reap_strays()
    if killed:
        log.warning("killed %d process(es) left under a profile uid before a start", killed)
    if profile_uids():
        raise RuntimeError("a process of a profile uid survived the reap")


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
        self._first_navigation = False

    @property
    def lease_dir(self) -> str:
        return f"{c.LEASES}/{self.lease_id}"

    async def prepare(self) -> None:
        await clear_display()
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
        self._memory_boundary("startup")
        await self.prepare()
        self.port = free_port()
        await self.spawn(self.port)
        try:
            if not await cdp_ready(self.port, c.START_S):
                raise RuntimeError("Chromium gave no CDP endpoint")
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.port}", timeout=c.START_S * 1000
            )
            self.cdp = await self.browser.new_browser_cdp_session()
            self._memory_boundary("cdp_ready")
            context = self.browser.contexts[0]
            context.on("page", self._on_page)
            with contextlib.suppress(Exception):
                for page in context.pages:
                    self._memory_page(page)
            self._write_state()
        except BaseException:
            await self.kill()
            raise
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
        self._memory_page(page)

    def _memory_page(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            if log.isEnabledFor(logging.DEBUG):
                page.on("framenavigated", self._on_navigation)

    def _on_navigation(self, frame: Any) -> None:
        with contextlib.suppress(Exception):
            if not self._first_navigation and frame.parent_frame is None:
                self._first_navigation = True
                self._memory_boundary("first_navigation")

    def _memory_boundary(self, phase: str) -> None:
        # Optional lifecycle reads are distinct from the watcher's deciding sample.
        with contextlib.suppress(Exception):
            if log.isEnabledFor(logging.DEBUG):
                _observed_memory(memory, phase)

    async def _watch_exit(self) -> None:
        """An exit nobody asked for ends the lease: `browser_closed` when Chromium quit by itself
        (status 0: its window closed, by the person or by a page), `browser_crashed` otherwise."""
        if self.proc is None:
            return
        code = await self.proc.wait()
        if self.stopping:
            return
        if code == 0 and self.on_end:
            await self.on_end("browser_closed")
        elif code != 0 and self.on_crash:
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
        has not exited within CLOSE_S, then the end finished whatever failed before it."""
        self.stopping = True
        try:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.cdp.send("Browser.close"), c.CLOSE_S)
            if self.proc and self.proc.returncode is None:
                try:
                    await asyncio.wait_for(self.proc.wait(), c.CLOSE_S)
                except TimeoutError:
                    await self._kill_group()
        finally:
            await self._finish()

    async def kill(self) -> None:
        self.stopping = True
        try:
            await self._kill_group()
        finally:
            await self._finish()

    async def _kill_group(self) -> None:
        if self.proc and self.proc.returncode is None:
            await kill_group(self.request.uid, self.proc.pid)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.proc.wait(), c.CLOSE_S)

    async def _finish(self) -> None:
        """Every end runs it once, whatever failed before: the watchers stopped, Playwright let
        go, every process of a profile uid killed (nothing of this lease stays on the display),
        the lease's directory removed. A step that fails is logged and the next one still runs;
        a process that survives is reaped again before the next start (clear_display)."""
        self._memory_boundary("end")
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.browser.close(), 2)
        with contextlib.suppress(OSError):
            self._clear_state()
        try:
            await reap_strays()
        except Exception as exc:
            log.warning("lease %s: processes not reaped: %s", self.lease_id, type(exc).__name__)
        try:
            await as_uid(self.request.uid, sys.executable, "-m", "browserd.prep", "rmtree",
                         self.lease_dir)  # fmt: skip
        except Exception as exc:
            log.warning("lease %s: directory not removed: %s", self.lease_id, type(exc).__name__)


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
_MEMORY_OBSERVATION: contextvars.ContextVar[tuple[str, dict] | None] = contextvars.ContextVar(
    "memory_observation", default=None
)
# Kernel memory.stat names only. v1's total_* counters describe the hierarchical charge.
_MEMORY_COUNTERS = frozenset(
    (
        "inactive_file",
        "active_file",
        "anon",
        "file",
        "shmem",
        "kernel",
        "slab",
        "file_mapped",
        "rss",
        "cache",
        "mapped_file",
        "total_inactive_file",
        "total_active_file",
        "total_rss",
        "total_cache",
        "total_shmem",
        "total_mapped_file",
    )
)


def _memory_counters(listed: str) -> dict[str, int]:
    counters: dict[str, int] = {}
    for line in listed[:16384].splitlines():
        name, _, value = line.partition(" ")
        value = value.strip()
        if (
            name in _MEMORY_COUNTERS
            and name not in counters
            and value.isdecimal()
            and len(value) <= 20
        ):
            counters[name] = int(value)
    return counters


def _memory_log(sample: dict, level: int = logging.DEBUG) -> bool:
    try:
        if log.isEnabledFor(level):
            log.log(
                level,
                "%s",
                json.dumps({"event": "browser_memory", **sample}, separators=(",", ":")),
            )
        return True
    except Exception:
        return False


def _observe_memory(sample: dict, current: str, ceiling: str, listed: str, key: str) -> None:
    observation = _MEMORY_OBSERVATION.get()
    sample.update(event="browser_memory", phase=observation[0] if observation else "sample")
    sample["observer_failed"] = 0
    try:
        # Only bounded numbers leave this function, never raw read bodies or exception text.
        sample["current_bytes"] = (
            int(current) if current.isdecimal() and len(current) <= 20 else None
        )
        sample["limit_bytes"] = int(ceiling) if ceiling.isdecimal() and len(ceiling) <= 20 else None
        sample["counters"] = _memory_counters(listed)
        sample["stat_truncated"] = int(len(listed) > 16384)
        if sample["stat_status"] == "read":
            present = any(line.partition(" ")[0] == key for line in listed[:16384].splitlines())
            sample["stat_status"] = (
                "ok" if key in sample["counters"] else "invalid" if present else "missing"
            )
    except Exception:
        sample["observer_failed"] = 1
    if not _memory_log(sample):
        sample["observer_failed"] = 1
    if observation is not None:
        observation[1].update(sample)


def _observed_memory(reader: Any, phase: str) -> tuple[Any, dict]:
    sample: dict = {}
    token = _MEMORY_OBSERVATION.set((phase, sample))
    try:
        return reader(), sample
    finally:
        _MEMORY_OBSERVATION.reset(token)


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
    started = time.monotonic_ns()
    sample = {
        "sample_id": started,
        "read_started_ns": started,
        "cgroup_version": 0,
        "accounting_status": "unavailable",
        "stat_status": "not_read",
        "read_failures": 0,
        "working_set_bytes": None,
    }
    current = ceiling = listed = key = ""
    try:
        for index, (usage, limit, stats, key) in enumerate(CGROUP_FILES):
            try:
                with open(f"{root}/{usage}") as cur, open(f"{root}/{limit}") as top:
                    current, ceiling = cur.read().strip(), top.read().strip()
            except OSError:
                sample["read_failures"] += 1
                continue
            sample["cgroup_version"] = 2 - index
            if not (current.isdecimal() and ceiling.isdecimal()):
                sample["accounting_status"] = "unlimited" if ceiling == "max" else "invalid"
                return None
            try:
                with open(f"{root}/{stats}") as figures:
                    listed = figures.read()
                sample["stat_status"] = "read"
            except OSError:
                listed = ""
                sample["stat_status"] = "unreadable"
            result = working_set(int(current), listed, key), int(ceiling)
            sample["accounting_status"] = "ok"
            sample["working_set_bytes"] = result[0] if result[0] < 10**20 else None
            return result
        return None
    finally:
        with contextlib.suppress(Exception):
            sample["read_finished_ns"] = time.monotonic_ns()
            _observe_memory(sample, current, ceiling, listed, key)


def past_guard(figures: tuple[int, int], pct: int) -> bool:
    """The container's (working set, limit) of `memory()` is at or past `pct` % of its limit."""
    return figures[0] * 100 >= figures[1] * pct


async def guard_memory(browser: Any, pct: int, event: Any, reader: Any = memory) -> bool:
    """Closes the newest tab when the container is past `pct` % of its limit (a blank tab is
    opened first when it was the only one). True when a tab was closed."""
    figures, sample = _observed_memory(reader, "guard")
    if figures is None or not past_guard(figures, pct):
        return False
    pages = [(context, page) for context in browser.contexts for page in context.pages]
    if not pages:
        return False
    context, victim = pages[-1]
    if len(pages) == 1:
        await context.new_page()
    await victim.close()
    event(event="tab_closed", reason="memory", used_mib=figures[0] >> 20)
    if sample:
        _memory_log({**sample, "phase": "guard_close", "pct": pct}, logging.WARNING)
    return True


def stop_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
