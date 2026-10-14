"""browserd's logic without a browser (task B2): the lease's clocks on a fake clock, one lease at
a time, the control API's secret, the egress proxy's verdicts, the memory guard and the profile
preparation. browserd lives in browser/browserd and imports nothing of the api; its pieces that
need Chromium are test_browser_container.py.
"""

import asyncio
import json
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


def test_the_router_list_has_the_placeholders_of_b4_and_b5() -> None:
    text = (ROOT / "browser/browserd/app.py").read_text(encoding="utf-8")
    assert "    # *live.ROUTES,  # B4" in text
    assert "    # *tools.ROUTES,  # B5" in text
    paths = {(r.method, r.pattern.pattern) for r in app.ROUTES}
    assert ("POST", "/leases") in paths
    assert ("GET", "/healthz") in paths


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
        assert await egress.target("pages", 8000) == "172.20.0.4"
        assert await proxy.Proxy(frozenset(), resolver).target("pages", 8000) is None

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
    assert prefs["bookmark_bar"] == {"x": 1}
    assert prefs["download"]["default_directory"] == f"{lease}/downloads"
    assert (profile.stat().st_mode & 0o777) == 0o700


def test_switches_keep_the_sandbox_the_proxy_and_the_real_user_agent() -> None:
    switches = chromium.switches(request(), "/leases/x", 9333)
    assert "--no-sandbox" not in switches
    assert not any("headless" in s for s in switches)
    assert not any(s.startswith("--user-agent") for s in switches)
    assert "--proxy-server=http://127.0.0.1:3128" in switches
    assert "--proxy-bypass-list=<-loopback>" in switches
    assert f"--user-data-dir=/profiles/{PROFILE}/data" in switches
    assert "--remote-debugging-address=127.0.0.1" in switches
