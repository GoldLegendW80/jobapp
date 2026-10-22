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
from collections.abc import Iterator
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
    assert not any("AutomationControlled" in a for a in argv)  # no infobar for the person
    with_cdp = chromium.switches(request("apply_fill"), "/leases/x", 9333)
    assert "--remote-debugging-port=9333" in with_cdp
    assert "--disable-blink-features=AutomationControlled" in with_cdp
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

    async def kill_group() -> None:
        seen.append("kill")
        proc.done.set()

    monkeypatch.setattr(browser, "_finish", finish)
    monkeypatch.setattr(browser, "_kill_group", kill_group)
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
    assert seen == [(20001, ("quit", "4242")), "kill", "finish"]


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


class ExitedProc:
    def __init__(self, code: int) -> None:
        self.pid, self.returncode = 4242, code

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.parametrize("kind", [signin.PersonChromium, chromium.Chromium])
@pytest.mark.parametrize(
    ("code", "reason"),
    [(0, "browser_closed"), (-9, "browser_crashed"), (-11, "browser_crashed"),
     (1, "browser_crashed"), (139, "browser_crashed")],
)  # fmt: skip
def test_the_exit_of_chromium_ends_the_lease_closed_or_crashed(
    kind: Any, code: int, reason: str
) -> None:
    """Status 0 is Chromium quitting by itself (its window closed): not a crash."""
    ended: list[str] = []
    browser = kind(request("signin"), str(uuid.uuid4()), None, Config())
    browser.proc = ExitedProc(code)

    async def on_crash() -> None:
        ended.append("browser_crashed")

    async def on_end(why: str) -> None:
        ended.append(why)

    browser.on_crash, browser.on_end = on_crash, on_end
    run(browser._watch_exit())
    assert ended == [reason]
    browser.stopping = True
    run(browser._watch_exit())
    assert ended == [reason]  # an end asked for is neither


# --- an end whose helper fails, and the reap before every start -----------------------------------


class Helpers:
    """chromium's helpers as the uid, recorded; `failing` names the commands that raise."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *failing: str) -> None:
        self.seen: list[str] = []
        self.failing = set(failing)
        self.strays: list[dict[int, list[int]]] = []
        monkeypatch.setattr(chromium, "as_uid", self.as_uid)
        monkeypatch.setattr(chromium, "profile_uids", self.profile_uids)
        monkeypatch.setattr(chromium, "REAP_TRIES", 0)

    async def as_uid(self, uid: int, *argv: str, timeout: float = 15) -> int:  # noqa: ASYNC109
        command = argv[3]
        self.seen.append(command)
        if command in self.failing:
            raise OSError(f"{command} could not start")
        return 0

    def profile_uids(self) -> dict[int, list[int]]:
        return self.strays.pop(0) if self.strays else {}


class StuckProc:
    """A Chromium that exits only once its group is killed."""

    def __init__(self) -> None:
        self.pid, self.returncode = 4242, None

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return -9


def made(kind: Any, proc: Any) -> Any:
    browser = kind(request("signin"), str(uuid.uuid4()), None, Config())
    browser.proc = proc
    return browser


@pytest.mark.parametrize(
    ("kind", "failing", "end", "expected"),
    [
        (signin.PersonChromium, ("quit",), "stop", ["quit", "killuid", "rmtree"]),
        (signin.PersonChromium, ("killpg",), "stop", ["quit", "killpg", "killuid", "rmtree"]),
        (signin.PersonChromium, ("killpg",), "kill", ["killpg", "killuid", "rmtree"]),
        (chromium.Chromium, ("killpg",), "stop", ["killpg", "killuid", "rmtree"]),
        (chromium.Chromium, ("killpg",), "kill", ["killpg", "killuid", "rmtree"]),
    ],
)
def test_an_end_whose_helper_raises_still_reaps_every_process_of_the_uid(
    monkeypatch: pytest.MonkeyPatch, kind: Any, failing: tuple[str, ...], end: str,
    expected: list[str],
) -> None:  # fmt: skip
    """The reap ("killuid" of every process left under a profile uid) runs after a helper failed,
    so that Chromium is not on the display in the next lease."""
    monkeypatch.setattr(signin, "QUIT_S", 0)
    monkeypatch.setattr(chromium.c, "CLOSE_S", 0)
    helpers = Helpers(monkeypatch, *failing)
    helpers.strays = [{20001: [4242, 4243]}]
    browser = made(kind, StuckProc())
    with pytest.raises(OSError, match="could not start"):
        run(getattr(browser, end)())
    assert helpers.seen == expected
    assert browser.stopping


def test_the_end_runs_every_step_when_the_reap_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    helpers = Helpers(monkeypatch, "killuid")
    helpers.strays = [{20001: [4242]}]
    browser = made(signin.PersonChromium, ExitedProc(0))
    run(browser.stop())  # the end itself does not raise
    assert helpers.seen == ["killuid", "rmtree"]


def test_the_manager_ends_a_lease_whose_browser_failed_to_stop() -> None:
    class Failing:
        async def stop(self) -> None:
            raise OSError("quit could not start")

        async def kill(self) -> None:
            raise OSError("killpg could not start")

    async def start(_: Request, __: str) -> Any:
        return Failing()

    async def measure(_: Request) -> int:
        return 12

    mgr = Manager(Config(secret=b"s"), start, Clock(), measure=measure)

    async def go() -> None:
        for reason in ("released", "browser_crashed"):
            lease, _ = await mgr.acquire(request("signin"))
            assert await mgr.release(lease.lease_id, reason)
            assert mgr.last_end == {"lease_id": lease.lease_id, "reason": reason, "size_bytes": 12}
            assert (mgr.current(), mgr.ending) == (None, None)

    run(go())


def test_every_start_reaps_what_an_earlier_lease_left(monkeypatch: pytest.MonkeyPatch) -> None:
    helpers = Helpers(monkeypatch)
    monkeypatch.setattr(chromium, "bind_uid", lambda *_: None)
    monkeypatch.setattr(chromium.c, "PROFILES", "/nonexistent-424")
    helpers.strays = [{20002: [77, 78]}, {}]
    browser = made(chromium.Chromium, None)
    run(browser.prepare())
    assert helpers.seen == ["killuid", "prepare"]


def test_a_start_is_refused_while_a_process_of_a_profile_uid_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helpers = Helpers(monkeypatch)
    helpers.strays = [{20002: [77]}, {20002: [77]}, {20002: [77]}]
    browser = made(signin.PersonChromium, None)
    with pytest.raises(RuntimeError, match="survived the reap"):
        run(browser.start())
    assert helpers.seen == ["killuid"]  # nothing prepared, nothing started


def test_a_session_that_exits_at_its_start_is_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    helpers = Helpers(monkeypatch)
    browser = made(signin.PersonChromium, None)

    async def prepare() -> None:
        return None

    async def spawn(*_: Any) -> Any:
        browser.proc = ExitedProc(1)
        return browser.proc

    monkeypatch.setattr(browser, "prepare", prepare)
    monkeypatch.setattr(browser, "spawn", spawn)
    with pytest.raises(RuntimeError, match="exited at its start"):
        run(browser.start())
    assert helpers.seen == ["rmtree"]
    assert browser.stopping


def test_an_agents_start_that_fails_after_the_spawn_kills_and_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chromium.c, "CLOSE_S", 0)
    helpers = Helpers(monkeypatch)
    browser = made(chromium.Chromium, None)

    async def prepare() -> None:
        return None

    async def spawn(*_: Any) -> Any:
        browser.proc = StuckProc()
        return browser.proc

    async def no_cdp(*_: Any) -> bool:
        return False

    monkeypatch.setattr(browser, "prepare", prepare)
    monkeypatch.setattr(browser, "spawn", spawn)
    monkeypatch.setattr(chromium, "cdp_ready", no_cdp)
    with pytest.raises(RuntimeError, match="no CDP endpoint"):
        run(browser.start())
    assert helpers.seen == ["killpg", "rmtree"]


# --- no port in a person's session ----------------------------------------------------------------


def test_a_port_opened_in_a_persons_session_ends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signin, "PORTS_EVERY_S", 0)
    browser = made(signin.PersonChromium, None)
    ended: list[str] = []
    events: list[dict] = []

    async def on_end(reason: str) -> None:
        ended.append(reason)

    browser.on_end, browser.on_event = on_end, events.append
    seen = iter([[], [], [9222]])
    asked: list[int] = []

    def reader(uid: int) -> list[int]:
        asked.append(uid)
        return next(seen)

    run(browser._watch_ports(reader))
    assert ended == ["debugging_port"]
    assert asked == [20001, 20001, 20001]
    assert events == [{"event": "session_ended", "reason": "listening_port", "ports": [9222]}]


TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:2406 00000000:0000 0A 00000000:00000000 00:00000000 00000000 20001        0 1 1
   1: 0100007F:2407 00000000:0000 0A 00000000:00000000 00:00000000 00000000 20002        0 2 1
   2: 0100007F:2408 0100007F:9C40 01 00000000:00000000 00:00000000 00000000 20001        0 3 1
"""  # noqa: E501
TCP6 = """  sl  local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout
   0: 00000000000000000000000001000000:24D4 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 20001        0 4 1
"""  # noqa: E501


def test_listening_ports_are_the_uids_own_listening_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = {"/proc/net/tcp": TCP, "/proc/net/tcp6": TCP6}

    def fake_open(name: str, *_: Any, **__: Any) -> Any:
        import io

        if name not in tables:
            raise FileNotFoundError(name)
        return io.StringIO(tables[name])

    monkeypatch.setattr(chromium, "open", fake_open, raising=False)
    assert chromium.listening_ports(20001) == [9222, 9428]  # the connected socket is not one
    assert chromium.listening_ports(20002) == [9223]
    assert chromium.listening_ports(20003) == []
    del tables["/proc/net/tcp6"]
    assert chromium.listening_ports(20001) == [9222]


@pytest.mark.parametrize(
    ("stat", "is_zombie"),
    [(b"77 (chromium) S 1 77", False), (b"77 (chromium) Z 1 77", True),
     (b"77 (a) Z (b) R 1 77", False), (b"77 (a) R (b) Z 1 77", True)],
)  # fmt: skip
def test_a_zombie_is_read_after_the_last_parenthesis(
    monkeypatch: pytest.MonkeyPatch, stat: bytes, is_zombie: bool
) -> None:
    import io

    monkeypatch.setattr(chromium, "open", lambda *_, **__: io.BytesIO(stat), raising=False)
    assert chromium.zombie("77") is is_zombie


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


@pytest.fixture
def umask() -> Iterator[None]:
    """prep.prepare sets the process's umask, as the uid's own child does: put back after."""
    before = os.umask(0o022)
    os.umask(before)
    yield
    os.umask(before)


def test_prepare_turns_off_the_remote_debugging_switch_kept_in_local_state(
    tmp_path: Path, umask: None
) -> None:
    """chrome://inspect's switch, once on, opens a DevTools port at every later start (measured
    for B7): prep turns it off before every start and keeps the rest of Local State."""
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    (profile / "data").mkdir(parents=True)
    local = profile / "data" / "Local State"
    local.write_text(json.dumps({
        "devtools": {"remote_debugging": {"user-enabled": True}, "other": 1},
        "browser": {"last_redirect_origin": "", "window_placement": {"top": 0}},
    }))  # fmt: skip
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert json.loads(local.read_text()) == {
        "devtools": {"remote_debugging": {"user-enabled": False}, "other": 1},
        "browser": {"last_redirect_origin": "", "window_placement": {"top": 0}},
    }


@pytest.mark.parametrize(
    "browser",
    [
        {"enabled_labs_experiments": ["enable-experimental-web-platform-features"]},
        {"enabled_labs_experiments": ["ozone-platform-hint@2", "smooth-scrolling@2"]},
        {"enabled_labs_experiments": []},
        {"enabled_labs_experiments_origin_lists": {
            "unsafely-treat-insecure-origin-as-secure": "http://site.example"}},
        {"enabled_labs_experiments": ["unsafely-treat-insecure-origin-as-secure"],
         "enabled_labs_experiments_origin_lists": {
             "unsafely-treat-insecure-origin-as-secure": "http://site.example"}},
        {"enabled_labs_experiments": "not a list"},
    ],
)  # fmt: skip
def test_prepare_drops_the_saved_chrome_flags(tmp_path: Path, umask: None, browser: dict) -> None:
    """chrome://flags is blocked (owner, 2026-10-20), but entries saved before keep applying at
    every start: prep drops them and keeps the rest of Local State."""
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    (profile / "data").mkdir(parents=True)
    local = profile / "data" / "Local State"
    local.write_text(json.dumps({
        "browser": {**browser, "window_placement": {"top": 0}},
        "enabled_labs_experiments": ["kept, not where Chromium reads it"],
    }))  # fmt: skip
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert json.loads(local.read_text()) == {
        "browser": {"window_placement": {"top": 0}},
        "enabled_labs_experiments": ["kept, not where Chromium reads it"],
        "devtools": {"remote_debugging": {"user-enabled": False}},
    }


TEST_POLICIES = '[{"name":"URLBlocklist","value":[],"level":1,"scope":1,"source":0}]'


@pytest.mark.parametrize(
    "saved",
    [TEST_POLICIES, "[]", "", "not json", [{"name": "URLAllowlist", "value": ["chrome://*"]}],
     None, {"nested": TEST_POLICIES}],
)  # fmt: skip
def test_prepare_drops_the_saved_test_policies(tmp_path: Path, umask: None, saved: object) -> None:
    """chrome://policy/test's "Restart and apply" saves a policy set in Local State, which
    replaced the image's managed policy at the next start: prep drops it, whatever its shape,
    and keeps the rest of Local State."""
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    (profile / "data").mkdir(parents=True)
    local = profile / "data" / "Local State"
    local.write_text(json.dumps({
        "local_test_policies_for_next_startup": saved,
        "browser": {"local_test_policies_for_next_startup": "kept, not where Chromium reads it"},
        "policy": {"last_statistics_update": "1"},
    }))  # fmt: skip
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert json.loads(local.read_text()) == {
        "browser": {"local_test_policies_for_next_startup": "kept, not where Chromium reads it"},
        "policy": {"last_statistics_update": "1"},
        "devtools": {"remote_debugging": {"user-enabled": False}},
    }


# What holds the logins and the site data, never removed; Preferences last (prep rewrites it).
KEPT = ("Cookies", "Local Storage/leveldb/000003.log", "Session Storage/000003.log",
        "IndexedDB/x/data", "Preferences")  # fmt: skip


@pytest.mark.parametrize(
    "saved",
    [("Sessions/Session_13400000000000000", "Sessions/Tabs_13400000000000000"),
     ("Current Session", "Current Tabs", "Last Session", "Last Tabs"),
     ("Sessions/Session_1", "Last Tabs"), ()],
)  # fmt: skip
def test_prepare_drops_the_tabs_of_the_last_session(
    tmp_path: Path, umask: None, saved: tuple[str, ...]
) -> None:
    """Issue #488: with "continue where you left off" Chromium reopened the last session's tabs
    beside the address of the new one, a tab more at every lease (18 after 16 leases of one
    profile), so a page's second tab went past TABS_MAX and was closed. prep removes the saved
    tabs before every start, and nothing that holds a login."""
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    default = profile / "data" / "Default"
    for name in (*saved, *KEPT):
        (default / name).parent.mkdir(parents=True, exist_ok=True)
        (default / name).write_text("x")
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    for name in ("Sessions", "Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        assert not (default / name).exists()
    for name in KEPT[:-1]:
        assert (default / name).read_text() == "x", name
    assert json.loads((default / "Preferences").read_text())["session"] == {
        "restore_on_startup": 1
    }  # the setting that keeps session cookies stays


def test_prepare_never_follows_a_link_in_place_of_the_saved_tabs(
    tmp_path: Path, umask: None
) -> None:
    """A Sessions entry that is a link (to a directory or a file) is unlinked, never followed:
    what it pointed at stays."""
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    default = profile / "data" / "Default"
    default.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("x")
    (default / "Sessions").symlink_to(elsewhere, target_is_directory=True)
    (default / "Last Tabs").symlink_to(elsewhere / "keep")
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert not os.path.lexists(default / "Sessions")
    assert not os.path.lexists(default / "Last Tabs")
    assert (elsewhere / "keep").read_text() == "x"


@pytest.mark.parametrize("browser", [None, "a string", ["enabled_labs_experiments"]])
def test_a_browser_entry_that_is_not_an_object_is_left_as_it_is(
    tmp_path: Path, umask: None, browser: object
) -> None:
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    (profile / "data").mkdir(parents=True)
    local = profile / "data" / "Local State"
    local.write_text(json.dumps({"browser": browser}))
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert json.loads(local.read_text())["browser"] == browser


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]"])
def test_a_local_state_without_the_switch_is_left_as_it_is(
    tmp_path: Path, umask: None, content: str | None
) -> None:
    profile, lease = tmp_path / "profile", tmp_path / "lease"
    (profile / "data").mkdir(parents=True)
    local = profile / "data" / "Local State"
    if content is not None:
        local.write_text(content)
    prep.prepare(str(profile), str(lease), "fr-FR,fr")
    assert (local.read_text() if local.exists() else None) == content


def test_the_managed_policy_blocks_every_chrome_page_and_keeps_cdp() -> None:
    """Every chrome:// page is blocked (owner, 2026-10-20) but the two browserd.probe reads, the
    new tab page and the browser's own UI (*.top-chrome); chrome://policy's test page is off,
    its "Apply policies" replaced the image's policy; no extension may be installed. The
    policies that would also block CDP (measured for B7: RemoteDebuggingAllowed false ignores
    --remote-debugging-port, DeveloperToolsAvailability 2 breaks Playwright's pages) are never
    set: the agents' leases need CDP."""
    policy = json.loads((ROOT / "browser/policies/browserd.json").read_text())
    assert policy["URLBlocklist"] == ["chrome://*"]
    assert sorted(policy["URLAllowlist"]) == [
        "chrome://new-tab-page", "chrome://new-tab-page-third-party", "chrome://newtab",
        "chrome://policy", "chrome://sandbox", "chrome://top-chrome",
    ]  # fmt: skip
    assert policy["PolicyTestPageEnabled"] is False
    assert policy["ExtensionInstallBlocklist"] == ["*"]
    assert "RemoteDebuggingAllowed" not in policy
    assert "DeveloperToolsAvailability" not in policy
