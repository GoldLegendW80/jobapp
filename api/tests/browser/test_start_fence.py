"""Owned startup generations, with event barriers and fake browser boundaries only."""

import asyncio
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.config import Config
from browserd.leases import LeaseError, Manager, Request


def request(user: str = "a") -> Request:
    return Request.parse(
        {
            "user_id": user,
            "profile_id": "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11",
            "uid": 20001,
            "purpose": "apply_fill" if user == "a" else "signin",
        }
    )


class Browser:
    def __init__(self, fail: bool = False) -> None:
        self.closing = asyncio.Event()
        self.finish = asyncio.Event()
        self.stopped = False
        self.fail = fail

    async def stop(self) -> None:
        self.closing.set()
        await self.finish.wait()
        if self.fail:
            raise OSError("fake close failed")
        self.stopped = True

    async def kill(self) -> None:
        await self.stop()


class Factory:
    def __init__(self, pause: int = 0, fail_close: bool = False) -> None:
        self.pause, self.fail_close = pause, fail_close
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.allow = asyncio.Event()
        self.returned = asyncio.Event()
        self.hold_close = False
        self.observe: Callable[[], None] | None = None
        self.browsers: list[Browser] = []
        self.ids: list[str] = []

    async def __call__(self, _: Request, lease_id: str) -> Browser:
        self.ids.append(lease_id)
        if len(self.ids) - 1 == self.pause:
            self.entered.set()
            try:
                await self.allow.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.allow.wait()  # A factory can suppress cancellation and return late.
        browser = Browser(self.fail_close)
        if not self.hold_close or len(self.ids) - 1 != self.pause:
            browser.finish.set()
        self.browsers.append(browser)
        self.returned.set()
        if self.observe is not None:
            asyncio.get_running_loop().call_soon(self.observe)
        return browser


async def busy(manager: Manager) -> None:
    with pytest.raises(LeaseError) as refused:
        await manager.acquire(request("b"))
    assert (refused.value.status, refused.value.code) == (409, "busy")
    assert refused.value.extra == {"holder_purpose": "apply_fill"}


@pytest.mark.parametrize("fail_close", [False, True])
def test_cancelled_initial_caller_cannot_install_a_late_browser(fail_close: bool) -> None:
    async def go() -> None:
        factory = Factory(fail_close=fail_close)
        factory.hold_close = True
        manager = Manager(Config(secret=b"s"), factory, lambda: 1000.0)
        installed: list[Any] = []
        factory.observe = lambda: installed.append(manager.current())
        caller = asyncio.create_task(manager.acquire(request()))
        await factory.entered.wait()
        caller.cancel()
        await factory.cancelled.wait()
        await busy(manager)
        factory.allow.set()
        outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        assert manager.current() is None
        await factory.returned.wait()
        await factory.browsers[0].closing.wait()
        await busy(manager)
        factory.browsers[0].finish.set()
        record = manager._records[factory.ids[0]]
        if record.task is not None:
            await asyncio.gather(record.task, return_exceptions=True)
        assert installed == [None]
        assert record.revoked
        assert record.bound
        assert record.task is None
        if fail_close:
            assert record.browser is factory.browsers[0]
            assert record.cleanup_failed
            await busy(manager)
            assert len(factory.ids) == 1
        else:
            assert factory.browsers[0].stopped
            assert record.browser is None
            other, token = await manager.acquire(request("b"))
            assert not await manager.release(factory.ids[0], "released")
            assert manager.check_token(other.lease_id, token) is other
            assert not factory.browsers[1].stopped
            assert await manager.release(other.lease_id, "released")

    asyncio.run(go())


def test_cancellation_between_installation_and_delivery_closes_the_owned_browser() -> None:
    async def go() -> None:
        browser = Browser()
        browser.finish.set()

        async def start(_: Request, __: str) -> Browser:
            asyncio.get_running_loop().call_soon(caller.cancel)
            return browser

        manager = Manager(Config(secret=b"s"), start)
        caller = asyncio.create_task(manager.acquire(request()))
        outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        records = list(manager._records.values())
        if records[0].task is not None:
            await asyncio.gather(records[0].task, return_exceptions=True)
        assert manager.current() is None
        assert browser.stopped
        assert records[0].task is None
        assert records[0].browser is None

    asyncio.run(go())


def test_release_revokes_an_initial_start_without_changing_its_false_result() -> None:
    async def go() -> None:
        factory = Factory()
        manager = Manager(Config(secret=b"s"), factory)
        caller = asyncio.create_task(manager.acquire(request()))
        await factory.entered.wait()
        assert not await manager.release(factory.ids[0], "released")
        factory.allow.set()
        outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        assert manager.current() is None
        assert factory.browsers[0].stopped
        assert manager.last_end is None

    asyncio.run(go())


@pytest.mark.parametrize("shielded", [False, True])
def test_cancelled_swap_generation_cannot_install_after_revocation(shielded: bool) -> None:
    async def go() -> None:
        factory = Factory(pause=1)
        manager = Manager(Config(secret=b"s"), factory)
        lease, token = await manager.acquire(request())
        person = replace(lease.request, automation=False)

        async def swapping() -> bool:
            work = manager.swap(lease, person)
            return await asyncio.shield(work) if shielded else await work

        caller = asyncio.create_task(swapping())
        await factory.entered.wait()
        caller.cancel()
        if shielded:
            with pytest.raises(asyncio.CancelledError):
                await caller
            entered = asyncio.Event()

            async def release() -> bool:
                entered.set()
                return await manager.release(lease.lease_id, "released")

            ending = asyncio.create_task(release())
            await entered.wait()
            await busy(manager)
        else:
            await factory.cancelled.wait()
        factory.allow.set()
        outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        record = manager._records.get(lease.lease_id) if hasattr(manager, "_records") else None
        if record is not None and record.task is not None:
            await asyncio.gather(record.task, return_exceptions=True)
        if shielded:
            assert await ending
        assert manager.current() is None
        assert all(browser.stopped for browser in factory.browsers)
        with pytest.raises(LeaseError) as refused:
            manager.check_token(lease.lease_id, token)
        assert refused.value.status == 404

    asyncio.run(go())


def test_ordinary_failed_close_retains_ownership_and_refuses_other_person() -> None:
    async def go() -> None:
        factory = Factory(pause=-1, fail_close=True)
        manager = Manager(Config(secret=b"s"), factory)
        lease, _ = await manager.acquire(request())
        assert await manager.release(lease.lease_id, "released")
        assert manager.last_end == {"lease_id": lease.lease_id, "reason": "released"}
        assert (manager.current(), manager.ending) == (None, None)
        await busy(manager)
        assert len(factory.ids) == 1
        assert not factory.browsers[0].stopped
        assert manager.uses(lease.request.profile_id)

    asyncio.run(go())


def test_records_evict_only_terminal_history_and_drop_consumed_references() -> None:
    async def go() -> None:
        factory = Factory(pause=-1)
        manager = Manager(Config(secret=b"s"), factory)
        for _ in range(33):
            lease, _ = await manager.acquire(request())
            assert await manager.release(lease.lease_id, "released")
        records: dict[str, Any] = getattr(manager, "_records", {})
        assert len(records) == 32
        assert factory.ids[0] not in records
        assert list(records) == factory.ids[1:]
        assert all(r.task is None and r.browser is None and r.revoked for r in records.values())
        assert all(r.request == request() and r.bound for r in records.values())
        # Exercise the defensive full-live bound without creating concurrent real browsers.
        for record in records.values():
            record.revoked = False
        await busy(manager)
        assert len(factory.ids) == 33
        assert len(records) == 32

    asyncio.run(go())
