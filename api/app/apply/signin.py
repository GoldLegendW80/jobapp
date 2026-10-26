"""Application sign in recovery. Credentials belong entirely to the person's browser."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import full_auto, states
from app.apply.ats import base
from app.apply.models import Application, ApplicationEvent, State
from app.browser.client import BrowserError
from app.browser.handoff import Outcome, request_signin
from app.browser.leases import StoreUnavailableError

Request = Callable[..., Awaitable[Outcome]]


def walled(shot: dict[str, Any], adapter: base.Adapter | None = None) -> bool:
    """The page read shows a sign in wall (`base.Adapter.pending_of`): a credential box, by
    browserd's read of the page or by its label. The adapter of the form's address, when there
    is one, reads it with its own steps (Greenhouse's e-mail code is no sign in)."""
    return bool(shot.get("ok")) and (adapter or base.Adapter()).pending_of(shot) == "signin"


class Tools:
    """Re-read a login wall after a handoff, at the current wizard page, for every adapter.

    The same wrapper covers an adapter discovered from the page and the form agent's reads.
    An unsuccessful handoff leaves the wall visible to the caller's normal pending-step rule.
    Three repeated uncompleted walls stop the run rather than starting an unlimited wait.
    After such a handoff every snapshot reads the page again: the wall read then is kept only
    while the page shows it or cannot be read (the lease gone after a Cancel, or waiting for the
    person), so a sign in done since, through the form agent's own tool too, hands the page back.
    """

    def __init__(
        self,
        tools: Any,
        url: str,
        *,
        request: Request | None = None,
        adapter: base.Adapter | None = None,
    ) -> None:
        self._tools, self.host, self.request = tools, urlsplit(url).hostname or "", request
        self.adapter = adapter
        self.outcome: Outcome | None = None
        self.attempts = 0
        self.blocked: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)

    async def recover_signin(self) -> str:
        """A known site's form navigation met a redirect outside the automated host list (A15
        wizards). Hands off only on the restored original host: browserd keeps its allow list,
        the person follows the single sign on in their own session and code reopens the exact
        job afterwards. '' once signed in, else `signin` (the caller's pending step)."""
        shot = await self._tools.snapshot()
        if not shot.get("ok") or urlsplit(str(shot.get("url") or "")).hostname != self.host:
            return "signin"
        if self.attempts >= 3:
            return "signin"
        self.attempts += 1
        if self.request is None and not hasattr(self._tools, "ask_signin"):
            self.outcome = Outcome("nobody_watching")
        else:
            self.outcome = await (self.request or request_signin)(
                self._tools, self.host, "This application needs you to sign in."
            )
        self.blocked = None
        return "" if self.outcome.code == "signed_in" else "signin"

    async def snapshot(self) -> dict[str, Any]:
        if self.blocked is not None:
            return await self._again(self.blocked)
        shot = await self._tools.snapshot()
        while (
            walled(shot, self.adapter)
            and (self.outcome is None or self.outcome.code == "signed_in")
            and self.attempts < 3
        ):
            self.attempts += 1
            ask = self.request or request_signin
            if self.request is None and not hasattr(self._tools, "ask_signin"):
                self.outcome = Outcome("nobody_watching")
            else:
                self.outcome = await ask(
                    self._tools, self.host, "This application needs you to sign in."
                )
            if self.outcome.code != "signed_in":
                self.blocked = shot
                break
            shot = await self._tools.snapshot()
        return shot

    async def _again(self, wall: dict[str, Any]) -> dict[str, Any]:
        try:
            shot = await self._tools.snapshot()
        except (BrowserError, StoreUnavailableError):
            return wall
        if not shot.get("ok"):
            return wall
        if walled(shot, self.adapter):
            self.blocked = shot
            return shot
        self.blocked = self.outcome = None
        return shot

    async def screenshot(self, **kwargs: Any) -> dict[str, Any]:
        try:
            shot: dict[str, Any] = await self._tools.screenshot(**kwargs)
        except (BrowserError, StoreUnavailableError):
            if self.blocked is None:
                raise
            # A Cancel or the deadline released the lease: no picture, and the item still waits.
            return {"ok": False, "error": "ended"}
        return shot


async def waiting(leases: Any, user: str, lease_id: str) -> bool:
    if not hasattr(leases, "current"):
        return False
    try:
        view = await leases.current(user)
    except (BrowserError, StoreUnavailableError):
        # A control-plane outage cannot be proof that the person's handoff ended.
        return True
    lease = view.lease
    return (
        lease is not None
        and lease.lease_id == lease_id
        and lease.state == "waiting_person"
        and getattr(lease, "wait_reason", "signin") == "signin"
    )


def address(db: Session, user_id: uuid.UUID, ref: uuid.UUID) -> str | None:
    application = db.execute(
        sa.select(Application).where(Application.id == ref, Application.user_id == user_id)
    ).scalar_one_or_none()
    if (
        application is None
        or application.state is not State.needs_person
        or application.needs != "signin"
    ):
        return None
    return application.form_url


def signed_in(db: Session, user_id: uuid.UUID, ref: uuid.UUID, now: datetime) -> bool:
    application = db.execute(
        sa.select(Application)
        .where(Application.id == ref, Application.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        application is None
        or application.state is not State.needs_person
        or application.needs != "signin"
    ):
        return False
    full_auto.hand_over(db, application, "signin", now)
    states.move(application, State.queued, now)
    application.needs = None
    application.error_code = None
    db.add(ApplicationEvent(application_id=ref, user_id=user_id, at=now, kind="signed_in", data={}))
    return True


def register() -> None:
    from app.browser.later_handoff import register as register_later
    from app.queue import apply_fill_job_id

    register_later(
        "application", address, signed_in, job_name="apply_fill", job_id=apply_fill_job_id
    )
