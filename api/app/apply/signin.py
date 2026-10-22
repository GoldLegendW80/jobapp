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


class Tools:
    """Re-read a login wall after a handoff, at the current wizard page, for every adapter.

    The same wrapper covers an adapter discovered from the page and the form agent's reads.
    An unsuccessful handoff leaves the wall visible to the caller's normal pending-step rule.
    Three repeated uncompleted walls stop the run rather than starting an unlimited wait.
    """

    def __init__(
        self,
        tools: Any,
        url: str,
        *,
        request: Request | None = None,
        pending: Callable[[str], str] | None = None,
    ) -> None:
        self._tools, self.host, self.request = tools, urlsplit(url).hostname or "", request
        self.pending = pending or base.Adapter().pending_step
        self.outcome: Outcome | None = None
        self.keep_lease = False
        self.attempts = 0
        self.blocked: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)

    async def recover_signin(self) -> str:
        """Known-site form navigation met a redirect outside the automated host list.

        Only hand off on the restored original host. Browserd keeps the existing allow list;
        the person follows SSO in their human session and code reopens the exact job afterwards.
        """
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
        self.keep_lease = self.outcome.code == "turn_ended"
        self.blocked = None
        return "" if self.outcome.code == "signed_in" else "signin"

    async def snapshot(self) -> dict[str, Any]:
        if self.blocked is not None:
            return self.blocked
        shot = await self._tools.snapshot()
        while (
            shot.get("ok")
            and self.pending(str(shot.get("snapshot") or "")) == "signin"
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
            self.keep_lease = self.outcome.code == "turn_ended"
            if self.outcome.code != "signed_in":
                self.blocked = shot
                break
            shot = await self._tools.snapshot()
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
    return lease is not None and lease.lease_id == lease_id and lease.state == "waiting_person"


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
