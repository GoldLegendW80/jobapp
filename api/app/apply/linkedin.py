"""LinkedIn's checkpoint persistence and company-site lease boundary."""

import asyncio
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import caps, full_auto, guardrails, states
from app.apply.ats.linkedin import LinkedIn
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.browser import address, linkedin_guard
from app.jobs.models import Job


def pause(db: Session, user_id: Any, now: datetime) -> None:
    linkedin_guard.record_checkpoint(db, user_id, now, "easy_apply")
    held = db.scalars(
        sa.select(Application)
        .where(
            Application.user_id == user_id,
            Application.mode == Mode.full_auto,
            Application.state == State.held,
        )
        .with_for_update()
    ).all()
    for application in held:
        if caps.host_of(application.ats, application.form_url) == "linkedin":
            states.move(application, State.ready, now)
            full_auto.hand_over(db, application, "linkedin_paused", now)
            application.error_code = "linkedin_paused"


class Watched:
    def __init__(self, services: Any, plan: Any, tools: Any) -> None:
        self.services, self.plan, self._tools = services, plan, tools
        self.paused = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)

    async def snapshot(self) -> dict[str, Any]:
        shot = await self._tools.snapshot()
        if (
            not self.paused
            and shot.get("ok")
            and LinkedIn().pending_step(str(shot.get("snapshot") or "")) == "linkedin_paused"
        ):
            self.paused = True

            def record() -> None:
                with self.services.sessions.begin() as db:
                    pause(db, self.plan.user_id, self.services.clock())

            # Commit the seven-day pause and move held work to review before returning content.
            await asyncio.to_thread(record)
        return shot


def watched(services: Any, plan: Any, tools: Any) -> Any:
    return (
        Watched(services, plan, tools)
        if LinkedIn(services.stub_base).form_url(plan.form_url)
        else tools
    )


def follow_company(
    db: Session, application: Application, posting: Job, url: str, now: datetime
) -> str:
    """Queue a new fill with its own restricted lease. The caller releases LinkedIn's lease.

    No host is appended to the current lease, and prepare rechecks all posting, document and
    site guards before another lease can start. The discovered address never enters an event.
    """
    key = guardrails.canonical_key(guardrails.unstub(url, caps._setting("apply_stub_base_url")))
    duplicate = (
        guardrails.duplicates(db, application.user_id, posting, key, now, exclude=application.id)
        if key
        else None
    )
    if (
        address.check_address(url) is None
        or caps.host_of("other", url) == "linkedin"
        or key is None
        or duplicate
    ):
        states.move(application, State.needs_person, now)
        application.needs = duplicate.code if duplicate else "company_site_changed"
        full_auto.hand_over(db, application, application.needs, now)
        return State.needs_person.value
    application.form_url, application.canonical_key = url, key
    application.ats = guardrails.ats_of(
        guardrails.unstub(url, caps._setting("apply_stub_base_url"))
    )
    application.answers_hash = None
    application.needs = None
    states.move(application, State.queued, now)
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=application.user_id,
            at=now,
            kind="company_site",
            data={},
        )
    )
    return State.queued.value
