"""browserd's side of browser B7 without a container: a person's own session (no automation,
no debugging port, the person's input from start to end, every tool call refused), the end of a
lease (busy until Chromium closed and the profile was measured, the size in `last_end`), the
removal of a profile's directory, and prep's measure and trim.

No test sleeps or reads the wall clock: the lease clock is injected, waits are on events, and
the session's own timers are set to zero here. Every id and secret is made up.
"""

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import app, chromium, live, prep, signin  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import LeaseError, Manager, Request  # noqa: E402

PROFILE_A = str(uuid.uuid4())
PROFILE_B = str(uuid.uuid4())
VIEWER = str(uuid.uuid4())


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SlowBrowser:
    """A Chromium whose stop waits until the test lets it close."""

    def __init__(self) -> None:
        self.closing = asyncio.Event()
        self.closed = asyncio.Event()
        self.stopped = self.killed = False

    async def stop(self) -> None:
        self.closing.set()
        await self.closed.wait()
        self.stopped = True

    async def kill(self) -> None:
        self.killed = True


def request(purpose: str, profile: str = PROFILE_A, uid: int = 20001, **extra: Any) -> Request:
    return Request.parse(
        {"user_id": "a", "profile_id": profile, "uid": uid, "purpose": purpose, **extra}
    )


def manager(measure: Any = None) -> tuple[Manager, list[Any]]:
    browsers: list[Any] = []

    async def start(_: Request, __: str) -> Any:
        browsers.append(SlowBrowser())
        browsers[-1].closed.set()
        return browsers[-1]

    return Manager(Config(secret=b"s"), start, Clock(), measure=measure), browsers


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- a person's own session ----------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ["takeover", "signin"])
@pytest.mark.parametrize("asked", [True, False, None])
def test_a_persons_session_never_runs_with_automation(purpose: str, asked: Any) -> None:
    extra = {} if asked is None else {"automation": asked}
    assert request(purpose, **extra).automation is False


@pytest.mark.parametrize("purpose", ["apply_fill", "apply_submit", "full_auto", "sweep"])
def test_an_agents_lease_keeps_its_automation(purpose: str) -> None:
    assert request(purpose).automation is True


def test_the_address_is_checked_and_only_for_a_persons_session() -> None:
    assert request("signin", address="https://example.org/login?next=/a").address == (
        "https://example.org/login?next=/a"
    )
    for bad in ("--remote-debugging-port=9222", "-a", "javascript:alert(1)", "file:///etc/x", 7):
        with pytest.raises(LeaseError) as refused:
            request("signin", address=bad)
        assert (refused.value.status, refused.value.code) == (400, "bad_address")
    with pytest.raises(LeaseError):
        request("apply_fill", address="https://example.org")
    assert request("signin").address is None


def test_a_session_without_cdp_has_no_debugging_switch_and_the_address_comes_last() -> None:
    req = request("signin", address="https://example.org/login?next=/a")
    argv = chromium.switches(req, "/leases/x", None, req.address or "about:blank")
    assert not any("remote-debugging" in a for a in argv)
    assert argv[:2] == ["chromium", "--"]  # Debian's wrapper passes the rest verbatim
    assert argv[-2:] == ["--", "https://example.org/login?next=/a"]
    assert argv.count("--") == 2
    with_cdp = chromium.switches(request("apply_fill"), "/leases/x", 9333)
    assert "--remote-debugging-port=9333" in with_cdp
    assert with_cdp[-2:] == ["--", "about:blank"]


def test_a_tool_call_on_a_persons_session_is_refused_409() -> None:
    mgr, _ = manager()
    svc = app.Service(mgr, b"s")

    async def go() -> None:
        lease, token = await mgr.acquire(request("signin"))
        for route in app.ROUTES:
            match = route.pattern.fullmatch(f"/leases/{lease.lease_id}/tools/browser_snapshot")
            if match and route.method == "POST":
                req = app.HttpRequest("POST", "", {"x-lease-token": token}, b"{}")
                with pytest.raises(LeaseError) as refused:
                    await route.handler(svc, req, match)
                assert (refused.value.status, refused.value.code) == (409, "person_session")
            put = route.pattern.fullmatch(f"/leases/{lease.lease_id}/files/cv.pdf")
            if put and route.method == "PUT":
                req = app.HttpRequest("PUT", "", {"x-lease-token": token}, b"%PDF-1.4 x")
                with pytest.raises(LeaseError) as refused:
                    await route.handler(svc, req, put)
                assert refused.value.code == "person_session"

        async def work() -> str:
            return "ran"

        with pytest.raises(LeaseError) as refused:
            await mgr.call(lease.lease_id, token, work)
        assert refused.value.code == "person_session"
        # The token is checked first: a wrong one learns nothing of the lease.
        with pytest.raises(LeaseError) as wrong:
            await mgr.call(lease.lease_id, "wrong", work)
        assert wrong.value.status == 403

    run(go())


def test_every_viewer_of_a_persons_session_holds_the_input_and_take_over_is_refused() -> None:
    mgr, _ = manager()
    view = live.Live(mgr, b"s")

    async def go() -> None:
        lease, _ = await mgr.acquire(request("takeover"))
        assert view.person(lease.lease_id)
        assert not view.person(str(uuid.uuid4()))
        with pytest.raises(LeaseError) as refused:
            view.take(lease.lease_id, VIEWER)
        assert (refused.value.status, refused.value.code) == (409, "person_session")
        svc = app.Service(mgr, b"s")
        svc.live = view  # type: ignore[attr-defined]
        match = re.fullmatch(r"/leases/(?P<lease_id>[0-9a-f-]{36})/live/input", "")
        path = f"/leases/{lease.lease_id}/live/input"
        for route in live.ROUTES:
            match = route.pattern.fullmatch(path)
            assert match is not None
            for body in (b'{"viewer": null}', f'{{"viewer": "{VIEWER}"}}'.encode()):
                with pytest.raises(LeaseError) as answer:
                    await route.handler(svc, app.HttpRequest("POST", path, {}, body), match)
                assert answer.value.code == "person_session"
        await mgr.release(lease.lease_id, "released")
        assert not view.person(lease.lease_id)
        agent, _ = await mgr.acquire(request("apply_fill"))
        assert not view.person(agent.lease_id)
        view.take(agent.lease_id, VIEWER)
        assert view.holder(agent.lease_id) == VIEWER

    run(go())


# --- the end of a lease --------------------------------------------------------------------------


def test_the_end_is_busy_until_chromium_closed_and_the_profile_was_measured() -> None:
    measured: list[str] = []

    async def measure(req: Request) -> int:
        measured.append(req.profile_id)
        return 4242

    browsers: list[SlowBrowser] = []

    async def start(_: Request, __: str) -> SlowBrowser:
        browsers.append(SlowBrowser())
        return browsers[-1]

    mgr = Manager(Config(secret=b"s"), start, Clock(), measure=measure)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("signin"))
        ending = asyncio.ensure_future(mgr.release(lease.lease_id, "released"))
        await browsers[0].closing.wait()
        assert mgr.current() is None
        assert mgr.uses(PROFILE_A)
        with pytest.raises(LeaseError) as busy:
            await mgr.acquire(request("apply_fill", PROFILE_B, 20002))
        assert (busy.value.code, busy.value.extra) == ("busy", {"holder_purpose": "signin"})
        assert "size_bytes" not in (mgr.last_end or {})
        browsers[0].closed.set()
        assert await ending is True
        end = {"lease_id": lease.lease_id, "reason": "released", "size_bytes": 4242}
        assert mgr.last_end == end
        assert measured == [PROFILE_A]
        assert not mgr.uses(PROFILE_A)
        await mgr.acquire(request("apply_fill", PROFILE_B, 20002))

    run(go())


def test_a_shutdown_is_not_measured_and_a_failed_measure_still_ends() -> None:
    calls: list[str] = []

    async def failing(_: Request) -> int:
        calls.append("measure")
        raise OSError("no")

    mgr, _ = manager(failing)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("signin"))
        assert await mgr.release(None, "shutdown")
        assert calls == []
        assert mgr.last_end == {"lease_id": lease.lease_id, "reason": "shutdown"}
        lease, _ = await mgr.acquire(request("signin"))
        assert await mgr.release(lease.lease_id, "idle")
        assert calls == ["measure"]
        assert mgr.last_end == {"lease_id": lease.lease_id, "reason": "idle"}
        assert mgr.ending is None

    run(go())


def test_the_delete_route_answers_the_end_with_the_size() -> None:
    async def measure(_: Request) -> int:
        return 77

    mgr, _ = manager(measure)
    svc = app.Service(mgr, b"s")

    async def go() -> None:
        lease, _ = await mgr.acquire(request("signin"))
        match = re.fullmatch(r"/leases/(?P<lease_id>[0-9a-f-]{36})", f"/leases/{lease.lease_id}")
        assert match is not None
        status, body = await app.delete_lease(svc, app.HttpRequest("DELETE", "", {}, b""), match)
        assert (status, body) == (200, {"lease_id": lease.lease_id, "reason": "released",
                                        "size_bytes": 77})  # fmt: skip
        with pytest.raises(LeaseError):
            await app.delete_lease(svc, app.HttpRequest("DELETE", "", {}, b""), match)

    run(go())


# --- the session without CDP's own lifecycle ------------------------------------------------------


class FakeProc:
    def __init__(self, exits: bool) -> None:
        self.pid, self.returncode = 4242, None
        self.exits = exits
        self.done = asyncio.Event()

    async def wait(self) -> int:
        if self.exits:
            self.returncode = 0
            return 0
        await self.done.wait()
        return -9


def person_chromium(monkeypatch: pytest.MonkeyPatch, proc: FakeProc) -> tuple[Any, list[Any]]:
    seen: list[Any] = []

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        seen.append((uid, argv[-2:]))
        return 0

    monkeypatch.setattr(chromium, "as_uid", as_uid)
    browser = signin.PersonChromium(request("signin"), str(uuid.uuid4()), None, Config())
    browser.proc = proc  # type: ignore[assignment]

    async def finish() -> None:
        seen.append("finish")

    async def kill() -> None:
        seen.append("kill")
        proc.done.set()

    monkeypatch.setattr(browser, "_finish", finish)
    monkeypatch.setattr(browser, "kill", kill)
    return browser, seen


def test_the_end_asks_chromium_to_quit_with_sigint(monkeypatch: pytest.MonkeyPatch) -> None:
    browser, seen = person_chromium(monkeypatch, FakeProc(exits=True))
    run(browser.stop())
    assert seen == [(20001, ("quit", "4242")), "finish"]
    assert browser.stopping


def test_a_chromium_that_does_not_quit_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signin, "QUIT_S", 0)
    browser, seen = person_chromium(monkeypatch, FakeProc(exits=False))
    run(browser.stop())
    assert seen == [(20001, ("quit", "4242")), "kill"]


def test_the_memory_guard_ends_the_whole_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signin, "MEMORY_EVERY_S", 0)
    browser, _ = person_chromium(monkeypatch, FakeProc(exits=False))
    ended: list[str] = []
    events: list[dict] = []

    async def on_end(reason: str) -> None:
        ended.append(reason)

    browser.on_end, browser.on_event = on_end, events.append
    figures = iter([(100, 1000), None, (899, 1000), (900, 1000)])
    run(browser._watch_memory(lambda: next(figures)))
    assert ended == ["memory_guard"]
    assert events == [{"event": "session_ended", "reason": "memory", "used_mib": 0}]


def test_the_exit_of_chromium_ends_the_lease_browser_crashed() -> None:
    crashed: list[str] = []
    browser = signin.PersonChromium(request("signin"), str(uuid.uuid4()), None, Config())
    browser.proc = FakeProc(exits=True)  # type: ignore[assignment]

    async def on_crash() -> None:
        crashed.append("browser_crashed")

    browser.on_crash = on_crash
    run(browser._watch_exit())
    assert crashed == ["browser_crashed"]
    browser.stopping = True
    run(browser._watch_exit())
    assert crashed == ["browser_crashed"]  # a stop asked for is not a crash


# --- DELETE /profiles/{id} ------------------------------------------------------------------------


def removal(svc: Any, profile: str, uid: Any) -> Any:
    path = f"/profiles/{profile}"
    for route in app.ROUTES:
        match = route.pattern.fullmatch(path)
        if match and route.method == "DELETE":
            body = json.dumps({} if uid is None else {"uid": uid}).encode()
            return route.handler(svc, app.HttpRequest("DELETE", path, {}, body), match)
    raise AssertionError("no route")


def test_a_removal_is_refused_while_a_lease_of_that_profile_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signin.c, "PROFILES", str(tmp_path))
    (tmp_path / PROFILE_A).mkdir()
    mgr, _ = manager()
    svc = app.Service(mgr, b"s")

    async def go() -> None:
        lease, _ = await mgr.acquire(request("signin"))
        with pytest.raises(LeaseError) as refused:
            await removal(svc, PROFILE_A, 20001)
        assert (refused.value.status, refused.value.code) == (409, "profile_in_use")
        await mgr.release(lease.lease_id, "released")

    run(go())
    assert (tmp_path / PROFILE_A).is_dir()


def test_a_removal_of_another_uids_directory_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B's removal names B's profile and uid: A's directory (another owner) is never touched."""
    monkeypatch.setattr(signin.c, "PROFILES", str(tmp_path))
    removed: list[str] = []

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        removed.append(argv[-1])
        return 0

    monkeypatch.setattr(chromium, "as_uid", as_uid)
    (tmp_path / PROFILE_A).mkdir()
    (tmp_path / PROFILE_A / "Cookies").write_text("a")
    svc = app.Service(manager()[0], b"s")
    mine = os.getuid()
    other = 20002 if mine != 20002 else 20003

    async def go() -> None:
        with pytest.raises(LeaseError) as refused:
            await removal(svc, PROFILE_A, other)
        assert refused.value.code == "profile_uid_mismatch"
        # B's own profile has no directory: done, and A's is still there.
        assert await removal(svc, PROFILE_B, other) == (204, None)
        for bad in (None, "20001", 19999, 60000, True):
            with pytest.raises(LeaseError) as wrong:
                await removal(svc, PROFILE_A, bad)
            assert (wrong.value.status, wrong.value.code) == (400, "bad_uid")

    run(go())
    assert (tmp_path / PROFILE_A / "Cookies").read_text() == "a"
    assert removed == []


def test_a_removal_runs_as_the_uid_and_holds_off_a_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not 20000 <= os.getuid() <= 59999:
        monkeypatch.setattr(signin.c, "UID_MIN", 0)
    monkeypatch.setattr(signin.c, "PROFILES", str(tmp_path))
    mgr, _ = manager()
    svc = app.Service(mgr, b"s")
    (tmp_path / PROFILE_A / "data").mkdir(parents=True)
    calls: list[Any] = []

    async def as_uid(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        calls.append((uid, argv[-2:]))
        with pytest.raises(LeaseError) as held:
            await mgr.acquire(request("signin"))
        assert held.value.code == "profile_removing"
        import shutil

        shutil.rmtree(tmp_path / PROFILE_A)
        return 0

    monkeypatch.setattr(chromium, "as_uid", as_uid)
    assert run(removal(svc, PROFILE_A, os.getuid())) == (204, None)
    assert calls == [(os.getuid(), ("rmprofile", f"{tmp_path}/{PROFILE_A}"))]
    assert not mgr.removing
    # A removal that left the directory answers 503: the api keeps its queue row.
    (tmp_path / PROFILE_A).mkdir()

    async def keeps(uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        return 1

    monkeypatch.setattr(chromium, "as_uid", keeps)
    with pytest.raises(LeaseError) as kept:
        run(removal(svc, PROFILE_A, os.getuid()))
    assert (kept.value.status, kept.value.code) == (503, "not_removed")


# --- prep: measure and trim ----------------------------------------------------------------------


def make_profile(root: Path, cache_bytes: int) -> Path:
    profile = root / PROFILE_A
    (profile / "data/Default/Local Storage").mkdir(parents=True)
    (profile / "data/Default/Cookies").write_bytes(b"c" * 100)
    (profile / "data/Default/Local Storage/leveldb").write_bytes(b"l" * 50)
    (profile / "data/Default/Preferences").write_text("{}")
    cache = profile / "data/Default/Cache/Cache_Data"
    cache.mkdir(parents=True)
    with open(cache / "data_1", "wb") as handle:  # sparse: the size without the disk
        handle.truncate(cache_bytes)
    (profile / "cache").mkdir()
    (profile / "cache/x").write_bytes(b"x" * 10)
    return profile


def test_a_profile_over_the_trim_size_loses_its_caches_never_its_cookies(tmp_path: Path) -> None:
    profile = make_profile(tmp_path, 1_200 * 1024 * 1024)
    size = prep.measure(str(profile), chromium.TRIM_BYTES)
    assert size == 100 + 50 + 2
    assert (profile / "data/Default/Cookies").read_bytes() == b"c" * 100
    assert (profile / "data/Default/Local Storage/leveldb").exists()
    assert not (profile / "data/Default/Cache").exists()
    assert not (profile / "cache").exists()


def test_a_profile_under_the_trim_size_is_measured_and_kept(tmp_path: Path) -> None:
    profile = make_profile(tmp_path, 1000)
    assert prep.measure(str(profile), chromium.TRIM_BYTES) == 100 + 50 + 2 + 1000 + 10
    assert (profile / "data/Default/Cache/Cache_Data/data_1").exists()


def test_the_measure_follows_no_link(tmp_path: Path) -> None:
    profile = make_profile(tmp_path, 0)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"o" * 5000)
    (profile / "data/link").symlink_to(outside)
    (profile / "data/filelink").symlink_to(outside / "big")
    (profile / "data/Default/Cache").rename(profile / "data/Default/Cache.real")
    (profile / "data/Default/Cache").symlink_to(outside)
    assert prep.size_of(str(profile)) == 100 + 50 + 2 + 10
    prep.measure(str(profile), 0)  # trimming never follows a link out of the profile
    assert (outside / "big").exists()


def test_prep_refuses_a_path_that_is_not_a_profile() -> None:
    assert prep.PROFILE.fullmatch(f"/profiles/{PROFILE_A}")
    for path in ("/profiles/.uids", f"/profiles/{PROFILE_A}/..", "/", "/profiles", "/leases/x",
                 f"/profiles/{PROFILE_A}/data", f"/var/{PROFILE_A}"):  # fmt: skip
        assert not prep.PROFILE.fullmatch(path)
