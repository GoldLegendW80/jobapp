"""Fakes for the apply run of A5: the lease store (browser B3) and the lease's tools (B5) over A4's
modelled form, with the two calls an adapter does not make (`put_file`, `screenshot`), and an
answer agent that records what it was asked. Every call is recorded; nothing here reaches a
browser, a model or a company."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.apply import agent
from app.browser.client import BrowserError
from tests.apply.a4_support import FakeForm, ashby_form, greenhouse_form

JPEG = b"\xff\xd8\xff\xe0" + os.urandom(64) + b"\xff\xd9"


class RunForm(FakeForm):
    """A4's FakeForm, given no file until the run places one, and able to take a screenshot."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("files", ())
        super().__init__(*args, **kwargs)
        self.placed: dict[str, bytes] = {}
        self.shot: bytes | None = JPEG
        self.crash_at: int | None = None  # the call that finds the lease gone

    def _log(self, name: str, **args: Any) -> None:
        super()._log(name, **args)
        if self.crash_at is not None and len(self.calls) >= self.crash_at:
            raise BrowserError("ended")

    async def put_file(self, name: str, data: bytes) -> dict[str, Any]:
        self._log("put_file", file=name, size=len(data))
        self.placed[name] = data
        self.files = (*self.files, name)
        return {"ok": True}

    async def screenshot(self, *, full_page: bool = False, kind: str = "png") -> dict[str, Any]:
        self._log("screenshot", full_page=full_page, kind=kind)
        if self.shot is None:
            return {"ok": False, "error": "too_large"}
        return {"ok": True, "type": kind, "data": base64.b64encode(self.shot).decode()}


def run_ashby(**rest: Any) -> RunForm:
    """A4's Ashby form with "Current location" optional: the fixture person's city is not among
    the stub's three options, so it is left (A4 tests the typeahead itself)."""
    form = ashby_form(**rest)
    for spec in form.fields:
        if spec.label == "Current location":
            spec.required = False
    return RunForm(form.fields, submit=form.submit, title=form.title)


def run_greenhouse(**rest: Any) -> RunForm:
    form = greenhouse_form(**rest)
    return RunForm(form.fields, submit=form.submit, title=form.title)


@dataclass
class Lease:
    lease_id: str


@dataclass
class Waiting:
    purpose: str
    position: int
    estimated_wait_s: int = 60


@dataclass
class Acquired:
    lease: Lease | None = None
    waiting: Waiting | None = None
    token: str | None = None


@dataclass
class FakeLeases:
    """`waiting` puts the caller at that place; `token=None` answers a lease already held."""

    waiting: int | None = None
    token: str | None = "lease-token"
    error: Exception | None = None
    acquired: list[dict[str, Any]] = field(default_factory=list)
    released: list[tuple[str, str | None, str]] = field(default_factory=list)

    async def acquire(
        self,
        user_id: str,
        purpose: str,
        automation: bool,
        *,
        hosts: Any = (),
        submit_selectors: Any = (),
        application_id: str | None = None,
    ) -> Acquired:
        self.acquired.append(
            {
                "user_id": user_id,
                "purpose": purpose,
                "hosts": list(hosts),
                "automation": automation,
                "application_id": application_id,
            }
        )
        if self.error is not None:
            raise self.error
        if self.waiting is not None:
            return Acquired(waiting=Waiting(purpose, self.waiting))
        return Acquired(lease=Lease(f"lease-{len(self.acquired)}"), token=self.token)

    async def release(
        self, user_id: str, lease_id: str | None = None, reason: str = "released"
    ) -> bool:
        self.released.append((user_id, lease_id, reason))
        return True


@dataclass
class FakeAgent:
    """Answers every question it is asked with `answer(question)`: a (value, source) pair."""

    answer: Callable[[agent.Question], tuple[str | None, str]] = lambda q: ("leave", "")
    stopped: str = ""
    limit: int | None = None  # answers only the first `limit` questions
    requests: list[agent.Request] = field(default_factory=list)

    async def __call__(self, request: agent.Request) -> agent.Reply:
        self.requests.append(request)
        questions = request.questions[: self.limit] if self.limit is not None else request.questions
        proposals = []
        for question in questions:
            value, source = self.answer(question)
            proposals.append(
                agent.Proposal(question.key, None if value == "leave" else value, source)
            )
        return agent.Reply(tuple(proposals), 12_000, 800, self.stopped)
