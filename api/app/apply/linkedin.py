"""LinkedIn's checkpoint persistence and company-site lease boundary."""

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from publicsuffixlist import PublicSuffixList
from sqlalchemy.orm import Session

from app.apply import caps, full_auto, guardrails, states
from app.apply.ats.linkedin import LinkedIn
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.browser import address, linkedin_guard
from app.jobs.models import Job
from app.mail import domains, hosts

# The reason a Full auto item followed off LinkedIn becomes a review item.
NOT_OWN_COMPANY = "company_site_unverified"

# The ICANN section of the bundled public suffix list (`domains` reads both sections). A host whose
# registrable domain differs between the two sits under a private suffix (`gitlab.io`,
# `azurewebsites.net`, `workers.dev`): anyone may take a name there, so it is nobody's own site.
_ICANN = PublicSuffixList(only_icann=True)


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


def own_company(company: str, url: str) -> bool:
    """Whether `url`, read on a LinkedIn posting, is the posting's own company's form: the ATS
    organisation of a known ATS form (`caps.company_names`), or the registrable domain of a
    company's own site (the check Full auto e-mail makes with `email.own_company`), has the key
    of the posting's company (`caps.company_key`: case, accents, spacing and trailing legal forms
    do not count). Only the whole name matches, never a leading part of it ("Example" is not
    "Example Robotics"). A shared host (under a private suffix of the public suffix list, such
    as `github.io` or `azurewebsites.net`), a job board, a host without a registrable domain,
    an empty company or an organisation the ATS address does not name are not: fail closed,
    the person reviews it."""
    real = guardrails.unstub(url, caps._setting("apply_stub_base_url"))
    posting = caps.company_names(company or "", "other", "")
    if not posting.keys:
        return False
    ats = guardrails.ats_of(real)
    if ats in ("ashby", "greenhouse", "lever"):
        org = caps.company_names("", ats, real)
        return bool(posting.keys & org.keys)
    parts = urlsplit(real)
    host = parts.hostname
    domain = domains.registrable_domain(host)
    if (
        ats != "other"
        or parts.scheme not in ("http", "https")
        or domain is None
        or _ICANN.privatesuffix(domains.normalise(host) or "") != domain
        or not hosts.is_company_own(host)
    ):
        return False
    own = caps.company_names(domain.split(".", 1)[0], "other", "")
    return bool(posting.keys & own.keys)


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
    if application.mode is Mode.full_auto and not own_company(posting.company, url):
        # Full auto continues only on the posting's own company's form; any other site is
        # followed in Review first, so the person approves what is sent.
        full_auto.hand_over(db, application, NOT_OWN_COMPANY, now)
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
