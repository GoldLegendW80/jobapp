"""Fakes of the job website search's tests: browserd's tools, the lease store, a sweep's plan
and stats as the website Runner reads them, and a scripted finder in place of the model.
Invented sites only."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.browser import finder
from app.browser.client import BrowserError
from app.browser.finder import FinderSession, Ran
from app.browser.leases import Acquired, Lease, StoreUnavailableError, Waiting
from app.jobs import slug as slugs


@dataclass
class FakeTools:
    """URL to snapshot; a click shows `after_click`; `end_after` ends the lease after N calls;
    `store_gone_after` makes the snapshot after N calls find the lease store gone."""

    pages: dict[str, str]
    after_click: list[str] = field(default_factory=list)
    end_after: int | None = None
    calls: list[str] = field(default_factory=list)
    current: str = ""
    store_gone_after: int | None = None

    def _count(self) -> None:
        if self.end_after is not None and len(self.calls) >= self.end_after:
            raise BrowserError("ended")

    async def navigate(self, url: str) -> dict[str, Any]:
        self._count()
        self.calls.append(url)
        if url not in self.pages:
            return {"ok": False, "error": "navigation_failed"}
        self.current = url
        return {"ok": True}

    async def click(self, ref: str, element: str = "") -> dict[str, Any]:
        self._count()
        self.calls.append(f"click {ref}")
        self.pages[self.current] = self.after_click.pop(0)
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        if self.store_gone_after is not None and len(self.calls) > self.store_gone_after:
            raise StoreUnavailableError  # LeaseTools._held without Redis
        return {"ok": True, "url": self.current, "snapshot": self.pages[self.current]}


class RedirectTools(FakeTools):
    """FakeTools whose pages end on another address: `moved` maps a requested URL to it."""

    def __init__(self, pages_: dict[str, str], moved: dict[str, str]) -> None:
        super().__init__(pages_)
        self.moved = moved

    async def snapshot(self) -> dict[str, Any]:
        done = await super().snapshot()
        return done | {"url": self.moved.get(self.current, self.current)}


class FakeLeases:
    def __init__(self, busy: bool = False, max_s: int = 900) -> None:
        self.busy, self.max_s = busy, max_s
        self.acquired: list[tuple[str, str, list[str]]] = []
        self.released: list[str] = []
        self.cancelled: list[str] = []

    async def acquire(self, user: str, purpose: str, automation: bool, *, hosts: Any) -> Acquired:
        self.acquired.append((user, purpose, list(hosts)))
        if self.busy:
            return Acquired(waiting=Waiting(purpose, 1, 60))
        lease = Lease(
            lease_id=f"lease-{len(self.acquired)}", user_id=user, purpose=purpose,
            automation=True, profile_id="p", max_s=self.max_s, idle_s=300, started_at=0.0,
        )  # fmt: skip
        return Acquired(lease=lease, token="invented-token")

    async def release(self, user: str, lease_id: str, reason: str = "") -> bool:
        self.released.append(lease_id)
        return True

    async def cancel(self, user: str, purposes: Any = None) -> int:
        self.cancelled.append(user)
        return 1


class Known:
    """The sweep's Known by URL only, normalised by the same rule (slug.py)."""

    def __init__(self, urls: set[str] | None = None) -> None:
        self.urls = {slugs.PostingKey.of("", "", "", url).url for url in urls or set()}

    def has(self, key: Any) -> bool:
        return key.url in self.urls


@dataclass
class Plan:
    sources: list[Any] = field(default_factory=list)
    known: Known = field(default_factory=Known)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Stats:
    sources: list[dict[str, Any]] = field(default_factory=list)
    current_board: str | None = None
    new: int = 0


class Tools(FakeTools):
    async def wait_for(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(f"wait {sorted(kw)}")
        return {"ok": True}


Script = Callable[[FinderSession], Awaitable[Ran]]


@dataclass
class ScriptedFinder(finder.FinderAgent):
    script: Script
    has_key: bool = True
    ran: int = 0

    async def available(self, user_id: str) -> bool:
        return self.has_key

    async def __call__(self, session: FinderSession) -> Ran:
        self.ran += 1
        return await self.script(session)
