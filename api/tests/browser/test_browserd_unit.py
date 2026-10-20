"""browserd's logic without a browser (task B2): the lease's clocks on a fake clock, one lease at
a time, the control API's secret, the egress proxy's verdicts, the memory guard and the profile
preparation. browserd lives in browser/browserd and imports nothing of the api; its pieces that
need Chromium are test_browser_container.py.
"""

import asyncio
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import app, chromium, prep, proxy  # noqa: E402
from browserd.config import Config, lease_max_table  # noqa: E402
from browserd.leases import LeaseEnded, LeaseError, Manager, Request  # noqa: E402

PROFILE = "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeBrowser:
    def __init__(self) -> None:
        self.stopped = self.killed = False

    async def stop(self) -> None:
        self.stopped = True

    async def kill(self) -> None:
        self.killed = True


def request(purpose: str = "apply_fill", uid: int = 20001, user: str = "a") -> Request:
    return Request.parse({"user_id": user, "profile_id": PROFILE, "uid": uid, "purpose": purpose})


def manager(clock: Clock, **config: Any) -> tuple[Manager, list[FakeBrowser]]:
    started: list[FakeBrowser] = []

    async def start(_: Request, __: str) -> FakeBrowser:
        started.append(FakeBrowser())
        return started[-1]

    return Manager(Config(secret=b"s", **config), start, clock), started


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_one_lease_at_a_time_and_409_names_the_purpose_only() -> None:
    clock = Clock()
    mgr, _ = manager(clock)

    async def go() -> None:
        lease, token = await mgr.acquire(request("sweep"))
        with pytest.raises(LeaseError) as refused:
            await mgr.acquire(request("takeover", uid=20002, user="b"))
        assert (refused.value.status, refused.value.code) == (409, "busy")
        assert refused.value.extra == {"holder_purpose": "sweep"}
        assert lease.lease_id not in json.dumps(refused.value.extra)
        assert token not in json.dumps(lease.public(clock()))

    run(go())


def test_apply_lease_ends_at_its_active_maximum_and_a_session_lasts_1800() -> None:
    clock = Clock()
    mgr, started = manager(clock, idle_s=10_000)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("apply_fill"))
        assert lease.max_s == 900
        clock.now += 899
        assert await mgr.tick() is None
        clock.now += 1
        assert await mgr.tick() == "lease_max"
        assert mgr.current() is None
        assert started[0].stopped
        assert mgr.last_end == {"lease_id": lease.lease_id, "reason": "lease_max"}
        session, _ = await mgr.acquire(request("takeover"))
        assert session.max_s == 1800

    run(go())


def test_waiting_person_suspends_the_maximum_and_the_idle_timer() -> None:
    """10 minutes of work, 25 of waiting for the person, 4 more of work on a 15 minute apply
    lease: still active. The wait has its own deadline."""
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("apply_fill"))
        for _ in range(10):
            clock.now += 60
            lease.touch(clock())
        lease.wait_person(clock(), "signin", "example.org", 1800)
        clock.now += 25 * 60
        assert await mgr.tick() is None
        assert lease.state == "waiting_person"
        lease.resume(clock())
        for _ in range(4):
            clock.now += 60
            lease.touch(clock())
        assert await mgr.tick() is None
        assert lease.active_s(clock()) == 14 * 60
        clock.now += 60
        assert await mgr.tick() == "lease_max"

    run(go())


def test_the_wait_ends_at_its_own_deadline() -> None:
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("apply_fill"))
        lease.wait_person(clock(), "signin", "example.org", 1800)
        clock.now += 1799
        assert await mgr.tick() is None
        clock.now += 1
        assert await mgr.tick() == "person_timeout"

    run(go())


def test_idle_timeout_and_a_tool_call_counts_as_input() -> None:
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)

    async def go() -> None:
        lease, token = await mgr.acquire(request("sweep"))
        clock.now += 299

        async def work() -> str:
            return "done"

        assert await mgr.call(lease.lease_id, token, work) == "done"
        clock.now += 299
        assert await mgr.tick() is None
        clock.now += 1
        assert await mgr.tick() == "idle"

    run(go())


def test_lease_max_reached_during_a_tool_call_ends_the_call() -> None:
    clock = Clock()
    mgr, started = manager(clock, idle_s=10_000)

    async def go() -> None:
        lease, token = await mgr.acquire(request("apply_submit"))
        gate = asyncio.Event()

        async def slow() -> str:
            await gate.wait()
            return "never"

        call = asyncio.ensure_future(mgr.call(lease.lease_id, token, slow))
        await asyncio.sleep(0)
        clock.now += 900
        assert await mgr.tick() == "lease_max"
        with pytest.raises(LeaseEnded) as ended:
            await call
        assert ended.value.reason == "lease_max"
        assert started[0].stopped

    run(go())


def test_a_crash_kills_and_the_token_is_checked() -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        lease, token = await mgr.acquire(request())
        with pytest.raises(LeaseError) as wrong:
            mgr.check_token(lease.lease_id, token + "x")
        assert wrong.value.status == 403
        with pytest.raises(LeaseError) as other:
            mgr.check_token("00000000-0000-0000-0000-000000000000", token)
        assert other.value.status == 404
        assert await mgr.release(lease.lease_id, "browser_crashed")
        assert started[0].killed
        assert not started[0].stopped
        assert not await mgr.release(lease.lease_id, "released")

    run(go())


def test_a_failed_start_answers_503_and_frees_the_slot() -> None:
    async def start(_: Request, __: str) -> FakeBrowser:
        raise RuntimeError("Chromium gave no CDP endpoint")

    mgr = Manager(Config(secret=b"s"), start, Clock())

    async def go() -> None:
        with pytest.raises(LeaseError) as failed:
            await mgr.acquire(request())
        assert (failed.value.status, failed.value.code) == (503, "browser_failed")
        assert mgr.holder_purpose() is None

    run(go())


@pytest.mark.parametrize(
    "change",
    [
        {"uid": 0},
        {"uid": 19999},
        {"uid": "20001"},
        {"profile_id": "../a"},
        {"purpose": "evaluate"},
        {"user_id": "a/b"},
        {"languages": ["fr-FR; q=1"]},
        {"timezone": "../../etc/passwd"},
    ],
)
def test_a_bad_lease_request_is_refused(change: dict) -> None:
    body = {"user_id": "a", "profile_id": PROFILE, "uid": 20001, "purpose": "sweep", **change}
    with pytest.raises(LeaseError) as refused:
        Request.parse(body)
    assert refused.value.status == 400


def test_lease_max_table_overrides() -> None:
    assert lease_max_table("apply_fill=1200, sweep=600")["apply_fill"] == 1200
    with pytest.raises(ValueError, match="purpose=seconds"):
        lease_max_table("evaluate=10")


@pytest.mark.parametrize(
    ("secret", "header", "ok"),
    [
        (b"s3cret", "Bearer s3cret", True),
        (b"s3cret", "bearer s3cret", True),
        (b"s3cret", "Bearer s3cre", False),
        (b"s3cret", "", False),
        (b"s3cret", "Basic s3cret", False),
        (b"", "Bearer ", False),
    ],
)
def test_the_control_secret(secret: bytes, header: str, ok: bool) -> None:
    assert app.authorized(secret, {"authorization": header} if header else {}) is ok


def test_the_router_list_has_the_routes_of_b4_and_b5() -> None:
    text = (ROOT / "browser/browserd/app.py").read_text(encoding="utf-8")
    assert "    *live.ROUTES,  # B4" in text
    paths = {(r.method, r.pattern.pattern) for r in app.ROUTES}
    assert ("POST", "/leases") in paths
    assert ("GET", "/healthz") in paths
    assert ("POST", r"/leases/(?P<lease_id>[0-9a-f-]{36})/live/input") in paths
    methods = {
        r.method
        for r in app.ROUTES
        if "/tools/" in r.pattern.pattern or "/files/" in r.pattern.pattern
    }
    assert methods == {"POST", "PUT"}  # B5: browserd/tools/routes.py


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("10.0.0.1", False),
        ("172.18.0.3", False),
        ("192.168.1.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.100.100.100", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),  # noqa: S104 - an address the proxy refuses, not a bind
        ("::1", False),
        ("fd7a:115c:a1e0::1", False),
        ("fe80::1", False),
        ("::ffff:10.0.0.1", False),
        ("224.0.0.1", False),
        # IPv6 carrying an IPv4 (review of PR #267): mapped addresses are checked as their
        # IPv4; IPv4 compatible, NAT64, 6to4 and Teredo are refused whatever they carry.
        ("::ffff:127.0.0.1", False),
        ("::ffff:169.254.169.254", False),
        ("::ffff:93.184.215.14", True),
        ("::127.0.0.1", False),
        ("::a9fe:a9fe", False),
        ("::93.184.215.14", False),
        ("64:ff9b::7f00:1", False),
        ("64:ff9b::a9fe:a9fe", False),
        ("64:ff9b::a00:1", False),
        ("64:ff9b::5db8:d70e", False),
        ("64:ff9b:1::a00:1", False),
        ("64:ff9b:1:ffff::5db8:d70e", False),
        ("2002:7f00:1::1", False),
        ("2002:a9fe:a9fe::1", False),
        ("2002:5db8:d70e::1", False),
        ("2001:0:7f00:1::1", False),
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),
        ("2001:db8::1", False),  # documentation, not Teredo: refused as not global
        ("2001:4860:4860::8888", True),  # a public address next to Teredo's prefix
        # Issue #281: the rule names what it accepts (2000::/3 for IPv6, outside listed blocks
        # for IPv4), whatever the Python version's is_global says.
        ("::ffff:0:7f00:1", False),  # IPv4 translated (SIIT), loopback inside
        ("::ffff:0:a9fe:a9fe", False),
        ("::ffff:0:5db8:d70e", False),  # translated, public inside: refused all the same
        ("::ffff:100.64.0.1", False),
        ("::ffff:224.0.0.251", False),
        ("::", False),
        ("100::1", False),  # discard only
        ("4000::1", False),  # outside 2000::/3
        ("3fff::1", False),  # documentation
        ("2001:1::1", False),  # IETF protocol assignments, global for Python 3.13 only
        ("2001:3::1", False),
        ("2001:20::1", False),
        ("fc00::1", False),
        ("ff02::1", False),
        ("ff0e::1", False),
        ("fe80::1%eth0", False),
        ("2606:4700::1111%eth0", False),  # a scope id: never a public address
        ("::1%lo", False),
        ("0.1.2.3", False),
        ("192.0.0.8", False),
        ("192.0.0.9", False),
        ("192.0.0.170", False),
        ("192.88.99.1", False),
        ("198.18.0.1", False),
        ("198.19.255.255", False),
        ("198.51.100.1", False),
        ("203.0.113.1", False),
        ("239.255.255.250", False),
        ("240.0.0.1", False),
        ("255.255.255.255", False),
        ("100.127.255.255", False),
        ("172.31.255.255", False),
        ("", False),
        ("garbage", False),
        ("1.2.3", False),
        ("127.1", False),
        ("2130706433", False),
        ("0177.0.0.1", False),
        ("93.184.215.14 ", False),
        ("1.1.1.1", True),
        ("11.0.0.1", True),
        ("100.63.255.255", True),
        ("100.128.0.1", True),
        ("172.15.255.255", True),
        ("172.32.0.1", True),
        ("192.0.1.1", True),
        ("198.20.0.1", True),
        ("223.255.255.254", True),
        ("2a00:1450:4007:80e::200e", True),
        ("2001:200::1", True),  # next to 2001::/23
        ("2c0f:f248::1", True),
    ],
)
def test_the_proxy_refuses_every_address_that_is_not_public(address: str, allowed: bool) -> None:
    assert proxy.address_allowed(address) is allowed


def test_the_proxy_checks_every_resolved_address_and_its_ports() -> None:
    answers = {
        "public.example": ["93.184.215.14"],
        "mixed.example": ["93.184.215.14", "10.0.0.5"],
        "pages": ["172.20.0.4"],
    }

    async def resolver(host: str, port: int) -> list[str]:
        if host not in answers:
            raise OSError("no such host")
        return answers[host]

    egress = proxy.Proxy(frozenset({"pages"}), resolver)

    async def go() -> None:
        assert await egress.target("public.example", 443) == "93.184.215.14"
        assert await egress.target("public.example", 22) is None
        assert await egress.target("mixed.example", 443) is None
        assert await egress.target("missing.example", 443) is None
        # Issue #281: the allowed test name passes the port set too.
        assert await egress.target("pages", 8080) == "172.20.0.4"
        assert await egress.target("pages", 8000) is None
        assert await egress.target("pages", 22) is None
        assert await egress.target("pages", 5349) is None
        assert await proxy.Proxy(frozenset(), resolver).target("pages", 8080) is None

    run(go())


class FakePage:
    def __init__(self, pages: list) -> None:
        self.pages, self.closed = pages, False

    async def close(self) -> None:
        self.closed = True
        self.pages.remove(self)


class FakeContext:
    def __init__(self, count: int) -> None:
        self.pages: list[FakePage] = []
        for _ in range(count):
            self.pages.append(FakePage(self.pages))

    async def new_page(self) -> FakePage:
        self.pages.append(FakePage(self.pages))
        return self.pages[-1]


class FakeBrowserContexts:
    def __init__(self, count: int) -> None:
        self.contexts = [FakeContext(count)]


def test_the_memory_guard_closes_the_newest_tab_past_the_share() -> None:
    events: list[dict] = []
    limit = 1024 << 20

    async def go() -> None:
        browser = FakeBrowserContexts(2)
        newest = browser.contexts[0].pages[-1]
        below = await chromium.guard_memory(
            browser, 90, lambda **e: events.append(e), lambda: (int(limit * 0.89), limit)
        )
        assert below is False
        assert len(browser.contexts[0].pages) == 2
        past = await chromium.guard_memory(
            browser, 90, lambda **e: events.append(e), lambda: (int(limit * 0.91), limit)
        )
        assert past is True
        assert newest.closed
        assert len(browser.contexts[0].pages) == 1
        assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 931}]
        alone = FakeBrowserContexts(1)
        await chromium.guard_memory(alone, 90, lambda **e: None, lambda: (limit, limit))
        assert len(alone.contexts[0].pages) == 1  # a blank tab replaces the only one

    run(go())


# memory_stat/v2-docker-desktop.txt: the browser container's memory.stat recorded on Docker
# Desktop (kernel 7.0.12-linuxkit, arm64) with a lease running and a file of the profiles volume
# freshly written, at memory.current 1024716800 of memory.max 1073741824. v1-layout.txt: a
# cgroup v1 memory.stat laid out as the kernel's cgroup v1 memory documentation lists it (no v1
# host here), its local inactive_file unlike its total_ one so the key read shows.
MEMORY_STAT = Path(__file__).parent / "memory_stat"
V2_STAT = (MEMORY_STAT / "v2-docker-desktop.txt").read_text(encoding="utf-8")
V1_STAT = (MEMORY_STAT / "v1-layout.txt").read_text(encoding="utf-8")
V2_CURRENT, V2_INACTIVE, LIMIT = 1024716800, 718323712, 1073741824
V1_USAGE, V1_TOTAL_INACTIVE = 1059061760, 756023296


def without(stat: str, key: str) -> str:
    return "".join(line for line in stat.splitlines(True) if line.split(" ")[0] != key)


def cgroup(root: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return str(root)


def v2(stat: str | None = V2_STAT, current: str = f"{V2_CURRENT}\n", top: str = f"{LIMIT}\n"):
    files = {"memory.current": current, "memory.max": top}
    return files if stat is None else {**files, "memory.stat": stat}


def v1(stat: str | None = V1_STAT, usage: str = f"{V1_USAGE}\n"):
    files = {"memory/memory.usage_in_bytes": usage, "memory/memory.limit_in_bytes": f"{LIMIT}\n"}
    return files if stat is None else {**files, "memory/memory.stat": stat}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (v2(), (V2_CURRENT - V2_INACTIVE, LIMIT)),
        (v1(), (V1_USAGE - V1_TOTAL_INACTIVE, LIMIT)),
        # Without the inactive file figure, the usage whole: the guard trips no later than before.
        (v2(stat=None), (V2_CURRENT, LIMIT)),
        (v2(stat=""), (V2_CURRENT, LIMIT)),
        (v2(stat=without(V2_STAT, "inactive_file")), (V2_CURRENT, LIMIT)),
        (
            v2(stat=V2_STAT.replace(f"inactive_file {V2_INACTIVE}", "inactive_file x")),
            (V2_CURRENT, LIMIT),
        ),
        (
            v2(stat=V2_STAT.replace(f"inactive_file {V2_INACTIVE}", "inactive_file -5")),
            (V2_CURRENT, LIMIT),
        ),
        (v2(stat="inactive_file_x 5\nactive_file 9\n"), (V2_CURRENT, LIMIT)),
        (v1(stat=None), (V1_USAGE, LIMIT)),
        # v1's local inactive_file is not docker stats' figure: total_inactive_file only.
        (v1(stat=without(V1_STAT, "total_inactive_file")), (V1_USAGE, LIMIT)),
        # A cache figure above the usage (two files read a moment apart): zero, never below.
        (v2(current="600000000\n"), (0, LIMIT)),
        (v2(current=f"{V2_INACTIVE}\n"), (0, LIMIT)),
        (v2(current=f"{V2_INACTIVE + 1}\n"), (1, LIMIT)),
        (v2(top="max\n"), None),
        (v2(current="\n"), None),
        (v2(current="12x\n"), None),
        ({"memory.current": f"{V2_CURRENT}\n"}, None),
        ({}, None),
    ],
)
def test_memory_is_the_working_set_of_docker_stats(
    tmp_path: Path, files: dict[str, str], expected: tuple[int, int] | None
) -> None:
    assert chromium.memory(cgroup(tmp_path, files)) == expected


def test_the_guard_leaves_the_page_cache_and_closes_on_allocation(tmp_path: Path) -> None:
    """The recorded container held 95% of its limit, two thirds of it inactive page cache: the
    guard reads 28% and closes nothing. The same usage held by allocated memory closes a tab."""
    assert V2_CURRENT * 100 >= LIMIT * 95  # what the guard read before: past 90%
    cache = cgroup(tmp_path / "cache", v2())
    allocated = cgroup(
        tmp_path / "allocated", v2(stat=without(V2_STAT, "inactive_file") + "inactive_file 0\n")
    )
    events: list[dict] = []

    async def go() -> None:
        browser = FakeBrowserContexts(2)
        record = lambda **e: events.append(e)  # noqa: E731
        assert not await chromium.guard_memory(browser, 90, record, lambda: chromium.memory(cache))
        assert len(browser.contexts[0].pages) == 2
        assert await chromium.guard_memory(browser, 90, record, lambda: chromium.memory(allocated))
        assert len(browser.contexts[0].pages) == 1

    run(go())
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": V2_CURRENT >> 20}]


def test_prepare_clears_stale_locks_and_writes_the_languages(tmp_path: Path) -> None:
    """Items 5 and 7: a lock of another host name removed; intl.accept_languages written (the
    switch --lang is ignored on Linux); a person's own preferences kept."""
    profile, lease = tmp_path / "p", tmp_path / "l"
    (profile / "data/Default").mkdir(parents=True)
    (profile / "data/SingletonLock").symlink_to("otherhost-4242")
    (profile / "data/SingletonCookie").write_text("1")
    (profile / "data/Default/Preferences").write_text(json.dumps({"bookmark_bar": {"x": 1}}))
    prep.prepare(str(profile), str(lease), "de-DE,de,en")
    assert not (profile / "data/SingletonLock").is_symlink()
    assert not (profile / "data/SingletonCookie").exists()
    prefs = json.loads((profile / "data/Default/Preferences").read_text())
    assert prefs["intl"]["accept_languages"] == "de-DE,de,en"
    assert prefs["session"]["restore_on_startup"] == 1
    assert prefs["credentials_enable_service"] is False
    assert prefs["profile"]["password_manager_enabled"] is False
    assert prefs["bookmark_bar"] == {"x": 1}
    assert prefs["download"]["default_directory"] == f"{lease}/downloads"
    assert prefs["webrtc"] == {"ip_handling_policy": "disable_non_proxied_udp"}
    assert (profile.stat().st_mode & 0o777) == 0o700


def test_switches_keep_the_sandbox_the_proxy_and_the_real_user_agent() -> None:
    switches = chromium.switches(request(), "/leases/x", 9333)
    assert "--no-sandbox" not in switches
    assert not any("headless" in s for s in switches)
    assert not any(s.startswith("--user-agent") for s in switches)
    assert "--proxy-server=http://127.0.0.1:3128" in switches
    assert "--proxy-bypass-list=<-loopback>" in switches
    assert "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1" in switches
    assert "--disable-quic" in switches
    assert f"--user-data-dir=/profiles/{PROFILE}/data" in switches
    assert "--remote-debugging-address=127.0.0.1" in switches


def test_the_image_installs_the_managed_webrtc_policy() -> None:
    """Review of PR #267, blocking 1: Debian's Chromium ignores the WebRTC switch, so the
    image ships the policy, which no profile state can undo (the container test reads it back
    from chrome://policy)."""
    policy = json.loads((ROOT / "browser/policies/browserd.json").read_text())
    assert policy == {
        "WebRtcIPHandling": "disable_non_proxied_udp",
        "QuicAllowed": False,
        "URLBlocklist": ["chrome://*"],
        "URLAllowlist": [
            "chrome://policy",
            "chrome://sandbox",
            "chrome://newtab",
            "chrome://new-tab-page",
            "chrome://new-tab-page-third-party",
        ],
        "ExtensionInstallBlocklist": ["*"],
        "PasswordManagerEnabled": False,
    }
    dockerfile = (ROOT / "browser/Dockerfile").read_text()
    assert "COPY policies/browserd.json /etc/chromium/policies/managed/browserd.json" in dockerfile


ANSWERS = {"upstream": ["127.0.0.1"], "other.example": ["93.184.215.14"]}


class Upstream:
    """A hostile plain http server: it answers `Connection: keep-alive` and keeps reading."""

    def __init__(self) -> None:
        self.received = b""
        self.open = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.open += 1
        try:
            answered = False
            while data := await reader.read(65536):
                self.received += data
                if not answered and b"\r\n\r\n" in self.received:
                    answered = True
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n"
                        b"Keep-Alive: timeout=60\r\n\r\nok"
                    )
                    await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()
            self.open -= 1


async def started(egress: proxy.Proxy) -> tuple[asyncio.Server, int, Upstream, asyncio.Server]:
    upstream = Upstream()
    up_server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
    egress.ports = proxy.PORTS | {up_server.sockets[0].getsockname()[1]}
    server = await proxy.serve(egress, 0)
    return server, server.sockets[0].getsockname()[1], upstream, up_server


def recording_resolver(calls: list[str]):
    async def resolver(host: str, port: int) -> list[str]:
        calls.append(host)
        host.encode("idna")  # what getaddrinfo does first: a label over 63 characters raises
        if host not in ANSWERS:
            raise OSError("no such host")
        return ANSWERS[host]

    return resolver


async def read_response(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    length = int(
        next(
            line.split(b":")[1]
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length")
        )
    )
    return head, await reader.readexactly(length)


@pytest.mark.parametrize("second", ["other.example", "10.0.0.1"])
def test_a_kept_alive_connection_carries_one_request_only(second: str) -> None:
    """Review of PR #267, non-blocking 1: Chromium is told `Connection: close`, whatever the
    upstream says, and a second request on the same proxy connection (to another host or to a
    private address) is never read, never resolved and never forwarded."""
    calls: list[str] = []
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver(calls))
    egress.idle_s = 0.2  # the hostile upstream never closes: the idle timeout ends it

    async def go() -> None:
        server, port, upstream, up_server = await started(egress)
        up_port = up_server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"GET http://upstream:%d/first HTTP/1.1\r\nHost: upstream\r\n"
            b"Proxy-Connection: keep-alive\r\n\r\n" % up_port
        )
        head, body = await read_response(reader)
        assert body == b"ok"
        assert b"Connection: close" in head
        assert b"keep-alive" not in head.lower()
        writer.write(
            b"GET http://%s/second HTTP/1.1\r\nHost: %s\r\nCookie: c=1\r\n\r\n"
            % (second.encode(), second.encode())
        )
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        await writer.wait_closed()
        assert upstream.received.count(b"GET ") == 1
        assert b"/first" in upstream.received
        assert b"Connection: close" in upstream.received
        assert b"second" not in upstream.received
        assert b"Cookie" not in upstream.received
        assert calls == ["upstream"]
        server.close()
        up_server.close()

    run(go())


def test_pipelined_requests_forward_the_first_and_its_body_only() -> None:
    calls: list[str] = []
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver(calls))
    egress.idle_s = 0.2

    async def go() -> None:
        server, port, upstream, up_server = await started(egress)
        up_port = up_server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST http://upstream:%d/a HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"
            b"GET http://10.0.0.1/b HTTP/1.1\r\n\r\n" % up_port
        )
        assert (await read_response(reader))[1] == b"ok"
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        assert upstream.received.endswith(b"\r\n\r\nhello")
        assert b"10.0.0.1" not in upstream.received
        assert calls == ["upstream"]
        server.close()
        up_server.close()

    run(go())


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def test_malformed_requests_leak_no_socket() -> None:
    """Review of PR #267, non-blocking 3: inputs that raised in the handler (an IDNA label over
    63 characters, a port `²`, a huge port, a bad body) answer or close, and after 100 of them
    the process holds exactly the descriptors it held before."""
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver([]))

    async def go() -> None:
        server, port, upstream, up_server = await started(egress)
        up_port = b"%d" % up_server.sockets[0].getsockname()[1]
        inputs = [
            b"GARBAGE\r\n\r\n",
            b"GET http://upstream:\xb2/ HTTP/1.1\r\n\r\n",
            b"CONNECT upstream:99999999999999999999 HTTP/1.1\r\n\r\n",
            b"CONNECT upstream:0 HTTP/1.1\r\n\r\n",
            b"CONNECT " + b"a" * 70 + b".example:443 HTTP/1.1\r\n\r\n",
            b"GET http://" + b"b" * 64 + b".example/ HTTP/1.1\r\n\r\n",
            b"GET http://upstream:" + up_port + b"/ HTTP/1.1\r\nContent-Length: zz\r\n\r\n",
            b"POST http://upstream:" + up_port + b"/ HTTP/1.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\nzz\r\n",
            b"\x00\x01 \x02 \x03\r\n\r\n",
            b"CONNECT [::1 HTTP/1.1\r\n\r\n",
            b"GET http://upstream/",  # closed before the end of the head
        ]
        before = open_descriptors()
        for n in range(100):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(inputs[n % len(inputs)])
            if n % len(inputs) == len(inputs) - 1:
                writer.write_eof()
            answer = await asyncio.wait_for(reader.read(), 10)
            assert not answer.startswith(b"HTTP/1.1 200"), (n, answer)
            writer.close()
            await writer.wait_closed()
        for _ in range(1000):  # yields to the loop until the upstream's side has closed too
            if upstream.open == 0 and egress.open == 0 and open_descriptors() == before:
                break
            await asyncio.sleep(0)
        assert (egress.open, upstream.open) == (0, 0)
        assert open_descriptors() == before
        # And the proxy still answers.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n")
        assert (await reader.read()).startswith(b"HTTP/1.1 403")
        writer.close()
        server.close()
        up_server.close()

    run(go())


def test_the_connection_cap_and_the_idle_timeout() -> None:
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver([]))
    egress.connections_max, egress.idle_s = 2, 0.2

    async def go() -> None:
        server, port, _, up_server = await started(egress)
        up_port = up_server.sockets[0].getsockname()[1]
        held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
        for _ in range(1000):
            if egress.open == 2:
                break
            await asyncio.sleep(0)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert (await reader.read()).startswith(b"HTTP/1.1 503")
        writer.close()
        for _, w in held:
            w.close()
        for _ in range(1000):
            if egress.open == 0:
                break
            await asyncio.sleep(0)
        # A tunnel with no byte either way is closed by the proxy.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT upstream:%d HTTP/1.1\r\n\r\n" % up_port)
        assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection established\r\n\r\n"
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        server.close()
        up_server.close()

    run(go())


MAPPED = ipaddress.IPv6Address("::ffff:93.184.215.14").compressed


# Issue #281: the request target's authority. The grammar names what it accepts (a DNS name in
# ASCII, an IPv6 literal, an ASCII port 1 to 65535); every neighbour below is refused before
# any resolution. Names that resolve to a refused address (localhost, 0x7f.1) pass the grammar
# and are refused by the address rule.
@pytest.mark.parametrize(
    ("authority", "connect", "expected"),
    [
        ("example.com", False, ("example.com", 80, "example.com")),
        ("Example.COM", False, ("example.com", 80, "example.com")),
        ("example.com:8080", False, ("example.com", 8080, "example.com:8080")),
        ("example.com:443", True, ("example.com", 443, "example.com:443")),
        ("example.com:00443", True, ("example.com", 443, "example.com:443")),
        ("example.com.", False, ("example.com.", 80, "example.com.")),
        ("xn--bcher-kva.example", False, ("xn--bcher-kva.example", 80, "xn--bcher-kva.example")),
        ("a_b.example:80", False, ("a_b.example", 80, "a_b.example:80")),
        ("93.184.215.14:443", True, ("93.184.215.14", 443, "93.184.215.14:443")),
        ("a" * 63 + ".example", False, ("a" * 63 + ".example", 80, "a" * 63 + ".example")),
        ("localhost", False, ("localhost", 80, "localhost")),
        ("0x7f.1:80", False, ("0x7f.1", 80, "0x7f.1:80")),
        ("pages:8080", False, ("pages", 8080, "pages:8080")),
        ("[2606:4700::1111]", False, ("2606:4700::1111", 80, "[2606:4700::1111]")),
        ("[2606:4700:0:0::1111]:443", True, ("2606:4700::1111", 443, "[2606:4700::1111]:443")),
        ("[2606:4700::ABCD]:443", True, ("2606:4700::abcd", 443, "[2606:4700::abcd]:443")),
        # Python's own compressed form of a mapped address (it changed in 3.13).
        ("[::ffff:93.184.215.14]:80", True, (MAPPED, 80, f"[{MAPPED}]:80")),
        ("", False, None),
        (":80", False, None),
        ("example.com:", False, None),
        ("example.com", True, None),  # CONNECT names its port
        ("example.com:0", True, None),
        ("example.com:65536", True, None),
        ("example.com:99999999999999999999", True, None),
        ("example.com:000443", True, None),
        ("example.com:²", True, None),
        ("example.com:٤٤٣", True, None),  # Arabic-Indic digits
        ("example.com:+80", True, None),
        ("example.com:-1", True, None),
        ("example.com: 80", True, None),
        ("example.com:80:80", True, None),
        ("example.com:443\t", True, None),
        ("user@example.com", False, None),
        ("user:pass@example.com:80", False, None),
        ("example.com@10.0.0.1", False, None),
        ("exa mple.com", False, None),
        ("exämple.com", False, None),
        ("exampleK.com", False, None),  # the Kelvin sign lower cases to an ASCII k
        ("１２７.0.0.1", False, None),  # full width digits
        ("example..com", False, None),
        (".example.com", False, None),
        ("example.com..", False, None),
        ("a" * 64 + ".example", False, None),
        (("a" * 62 + ".") * 4 + "com", False, None),  # over 253 characters
        ("example.com%2f", False, None),
        ("example.com\x00", False, None),
        ("example.com/:443", True, None),
        ("[::1", False, None),
        ("[::1]x", False, None),
        ("[::1]:", False, None),
        ("[::1]]:80", True, None),
        ("[]", False, None),
        ("[fe80::1%25eth0]:80", True, None),
        ("[v1.x]:80", True, None),
        ("[127.0.0.1]:80", True, None),
        ("[::1:80", True, None),
    ],
)
def test_the_proxy_reads_only_a_plain_authority(
    authority: str, connect: bool, expected: tuple | None
) -> None:
    assert proxy.parse_authority(authority, port_required=connect) == expected


GET = b"GET http://example.com/ HTTP/1.1\r\n"
POST = b"POST http://example.com/ HTTP/1.1\r\n"


@pytest.mark.parametrize(
    ("head", "length"),
    [
        (GET + b"Host: example.com\r\n\r\n", 0),
        (GET + b"\r\n", 0),
        (b"GET http://example.com HTTP/1.1\r\n\r\n", 0),
        (b"GET http://example.com/?q=a%20b HTTP/1.0\r\n\r\n", 0),
        (b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 5\r\n\r\n", 5),
        (b"POST http://example.com/ HTTP/1.1\r\ncontent-length:5\r\n\r\n", 5),
        (b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 5\r\n\r\n", 5),
        (b"POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: Chunked\r\n\r\n", None),
        (b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n", 0),
        (GET + b"X-A: a\tb\r\nCookie: c=1; d=2\r\n\r\n", 0),
    ],
)
def test_the_proxy_accepts_plain_requests(head: bytes, length: int | None) -> None:
    parsed = proxy.parse_head(head)
    assert parsed is not None
    assert parsed.length == length


@pytest.mark.parametrize(
    "head",
    [
        b"GARBAGE\r\n\r\n",
        b"\r\n\r\n",
        b"GET /relative HTTP/1.1\r\n\r\n",
        b"GET https://example.com/ HTTP/1.1\r\n\r\n",
        b"GET HTTP://example.com/ HTTP/1.1\r\n\r\n",
        b"GET http://example.com/ HTTP/2.0\r\n\r\n",
        b"GET http://example.com/ http/1.1\r\n\r\n",
        b"GET http://example.com/ HTTP/1.1 extra\r\n\r\n",
        b"GET  http://example.com/ HTTP/1.1\r\n\r\n",
        b"G(T http://example.com/ HTTP/1.1\r\n\r\n",
        b"CONNECT example.com HTTP/1.1\r\n\r\n",
        b"GET http://ex\xc3\xa4mple.com/ HTTP/1.1\r\n\r\n",
        b"GET http://a@10.0.0.1/ HTTP/1.1\r\n\r\n",
        b"GET http://example.com:22x/ HTTP/1.1\r\n\r\n",
        # One body, one framing, so the proxy and the upstream agree on its end.
        POST + b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n",
        POST + b"Transfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: xchunked\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked, identity\r\n\r\n",
        POST + b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: identity\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 6\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: +5\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: -1\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 5,5\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: \r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 0x10\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 1e3\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: \xef\xbc\x95\r\n\r\n",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 1234567890123456\r\n\r\n",
        # Header lines: a token name right before the colon, no folding, no bare CR or LF, no NUL.
        GET + b"Host : evil.example\r\n\r\n",
        GET + b"Transfer-Encoding : chunked\r\n\r\n",
        GET + b"X-A: a\r\n b\r\n\r\n",
        GET + b"X-A: a\r\n\tb\r\n\r\n",
        GET + b"no colon here\r\n\r\n",
        GET + b": empty name\r\n\r\n",
        GET + b"X-A: a\nGET http://10.0.0.1/ HTTP/1.1\r\n\r\n",
        GET + b"X-A: a\rb\r\n\r\n",
        GET + b"X-A: a\x00b\r\n\r\n",
    ],
)
def test_the_proxy_refuses_what_is_not_plain_http(head: bytes) -> None:
    assert proxy.parse_head(head) is None


def test_the_upstream_gets_the_checked_host_and_one_framing() -> None:
    """Issue #281 item 6: the Host the upstream reads is the authority the proxy checked, never
    the client's (one given, two given, or none). A refused head answers 400 and opens no
    upstream connection."""
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver([]))
    egress.idle_s = 0.2

    async def go() -> None:
        server, port, upstream, up_server = await started(egress)
        up_port = up_server.sockets[0].getsockname()[1]
        target = b"GET http://upstream:%d/h HTTP/1.1\r\n" % up_port
        for extra in (b"Host: evil.example\r\n", b"Host: a\r\nHost: b\r\n", b""):
            upstream.received = b""
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(target + extra + b"X-Kept: 1\r\n\r\n")
            assert (await read_response(reader))[1] == b"ok"
            writer.close()
            for _ in range(1000):
                if upstream.open == 0:
                    break
                await asyncio.sleep(0)
            lines = upstream.received.split(b"\r\n")
            assert lines[0] == b"GET /h HTTP/1.1"
            assert [h for h in lines if h.lower().startswith(b"host:")] == [
                b"Host: upstream:%d" % up_port
            ]
            assert b"X-Kept: 1" in lines
            assert b"evil" not in upstream.received
        upstream.received = b""
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST http://upstream:%d/ HTTP/1.1\r\nContent-Length: 5\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\nGET /smuggled HTTP/1.1\r\n\r\n" % up_port
        )
        assert (await reader.read()).startswith(b"HTTP/1.1 400")
        writer.close()
        assert upstream.received == b""
        server.close()
        up_server.close()

    run(go())


def test_a_chunked_body_is_written_again_and_ends_at_its_last_chunk() -> None:
    """Chunk sizes are forwarded from their value (an extension is dropped), trailers are not
    forwarded, nothing after the last chunk is, and a chunk that does not end with CRLF closes
    the connection."""
    egress = proxy.Proxy(frozenset({"upstream"}), recording_resolver([]))
    egress.idle_s = 0.2

    async def go() -> None:
        server, port, upstream, up_server = await started(egress)
        up_port = up_server.sockets[0].getsockname()[1]
        head = b"POST http://upstream:%d/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n" % up_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            head + b"5;ext=GET /x\r\nhello\r\n0\r\nX-Trailer: t\r\n\r\n"
            b"GET http://10.0.0.1/ HTTP/1.1\r\n\r\n"
        )
        assert (await read_response(reader))[1] == b"ok"
        assert await asyncio.wait_for(reader.read(), 10) == b""
        writer.close()
        for _ in range(1000):
            if upstream.open == 0:
                break
            await asyncio.sleep(0)
        assert upstream.received.endswith(b"\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
        assert b"ext" not in upstream.received
        assert b"X-Trailer" not in upstream.received
        assert b"10.0.0.1" not in upstream.received
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(head + b"5\r\nhelloXX0\r\n\r\n")
        assert b"200 OK" not in await asyncio.wait_for(reader.read(), 10)
        writer.close()
        server.close()
        up_server.close()

    run(go())


def test_one_uid_one_profile_for_ever(tmp_path: Path) -> None:
    """Issue #281 item 8: browserd's own record. The first lease of a uid binds its profile; the
    same uid for another profile is refused 409, again after any number of tries; another uid
    is free; a record directory that is not browserd's own refuses every lease 503."""
    root = tmp_path / "uids"
    chromium.make_uid_records(str(root))
    chromium.make_uid_records(str(root))  # a second start keeps what is there
    assert oct(root.stat().st_mode & 0o777) == "0o700"
    other = "0b7e1c1e-9d43-4c55-8c1a-6f0f3c7f1a00"
    chromium.bind_uid(20001, PROFILE, str(root))
    chromium.bind_uid(20001, PROFILE, str(root))
    for _ in range(3):
        with pytest.raises(LeaseError) as refused:
            chromium.bind_uid(20001, other, str(root))
        assert (refused.value.status, refused.value.code) == (409, "uid_in_use")
    chromium.bind_uid(20002, other, str(root))
    assert (root / "20001").read_text() == PROFILE
    assert sorted(p.name for p in root.iterdir()) == ["20001", "20002"]  # no draft left

    (root / "20003").symlink_to(root / "20001")  # a planted link is never read through
    with pytest.raises(LeaseError) as unsafe:
        chromium.bind_uid(20003, PROFILE, str(root))
    assert unsafe.value.code == "uid_records_unsafe"

    for bad in ("open", "link", "missing"):
        path = tmp_path / bad
        if bad == "open":
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        elif bad == "link":
            path.symlink_to(root)
        with pytest.raises(LeaseError) as unsafe:
            chromium.bind_uid(20009, PROFILE, str(path))
        assert (unsafe.value.status, unsafe.value.code) == (503, "uid_records_unsafe")


@pytest.mark.parametrize(
    ("app_env", "allow", "refused"),
    [
        ("production", "pages", True),
        ("Production", "pages", True),
        (" production ", "pages,atsforms", True),
        ("production", " , ", False),  # nothing named: nothing to refuse
        ("production", "", False),
        ("test", "pages", False),
        ("development", "pages", False),
        ("", "pages", False),
        ("prod", "pages", False),  # the api's own convention: only the word production
    ],
)
def test_proxy_allow_private_refused_in_production(
    monkeypatch: pytest.MonkeyPatch, app_env: str, allow: str, refused: bool
) -> None:
    """Issue #230 (review of PR #294): the test stacks' private names never reach a production
    browser. browserd refuses to start, so the container is never healthy and the deploy stops."""
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("BROWSER_PROXY_ALLOW_PRIVATE", allow)
    if refused:
        with pytest.raises(ValueError, match="APP_ENV=production"):
            Config.from_env()
    else:
        assert Config.from_env().proxy_allow == frozenset(
            h.strip() for h in allow.split(",") if h.strip()
        )


def test_uid_records_seeded_from_existing_profiles(tmp_path: Path) -> None:
    """Issue #230 (follow up 1 of PR #294's review): directories made before the records existed
    bind their owner uid at start, so a new profile under that uid is refused 409. A uid with
    two directories gets a record that refuses every lease of it (PR #307 review, follow up 7);
    a link, a file, a name that is not a profile id and a uid outside the range are left alone;
    a second start writes nothing more."""
    profiles, root = tmp_path / "profiles", tmp_path / "profiles" / ".uids"
    profiles.mkdir()
    chromium.make_uid_records(str(root))
    a, b = PROFILE, "0b7e1c1e-9d43-4c55-8c1a-6f0f3c7f1a00"
    twin1, twin2 = "1d1e2f30-0000-4000-8000-000000000001", "1d1e2f30-0000-4000-8000-000000000002"
    owner = {a: 20001, b: 20002, twin1: 20003, twin2: 20003, "not-a-profile": 20004}
    for name in owner:
        (profiles / name).mkdir()
    low = "1d1e2f30-0000-4000-8000-000000000005"
    (profiles / low).mkdir()
    owner[low] = 1000  # the display's uid, not a profile's
    upper = "ABCDEF01-0000-4000-8000-000000000006"  # uuid accepts it, browserd never writes it
    (profiles / upper).mkdir()
    owner[upper] = 20006
    filed = "1d1e2f30-0000-4000-8000-000000000007"
    (profiles / filed).write_text("x")
    owner[filed] = 20007
    linked = "1d1e2f30-0000-4000-8000-000000000008"
    (profiles / linked).symlink_to(profiles / a)
    owner[linked] = 20008

    def lstat(path: str) -> os.stat_result:
        real = os.lstat(path)
        fields = list(real)
        fields[4] = owner.get(Path(path).name, real.st_uid)  # st_uid
        return os.stat_result(fields)

    assert chromium.seed_uid_records(str(profiles), str(root), lstat) == 3
    assert sorted(p.name for p in root.iterdir()) == ["20001", "20002", "20003"]
    assert (root / "20001").read_text() == a
    assert (root / "20003").read_text() == chromium.AMBIGUOUS
    with pytest.raises(LeaseError) as refused:
        chromium.bind_uid(20001, "1d1e2f30-0000-4000-8000-000000000009", str(root))
    assert (refused.value.status, refused.value.code) == (409, "uid_in_use")
    # The uid of two directories: neither of them, nor a new profile, may be leased under it.
    for profile in (twin1, twin2, "1d1e2f30-0000-4000-8000-00000000000a"):
        with pytest.raises(LeaseError) as refused:
            chromium.bind_uid(20003, profile, str(root))
        assert (refused.value.status, refused.value.code) == (409, "uid_in_use")
    assert chromium.seed_uid_records(str(profiles), str(root), lstat) == 0  # idempotent

    # A record that names another profile is kept, never rewritten from the directories.
    owner[a] = 20002
    owner[b] = 20009
    assert chromium.seed_uid_records(str(profiles), str(root), lstat) == 1  # 20009 only
    assert (root / "20002").read_text() == b

    # Records that are not browserd's own: nothing seeded, start goes on, leases refuse 503.
    root.chmod(0o755)
    assert chromium.seed_uid_records(str(profiles), str(root), lstat) == 0
