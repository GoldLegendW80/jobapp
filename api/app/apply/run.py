"""The apply run (SPEC-apply, "The flow of one application", steps 2 and 3): the job
`apply_fill(application_id)` of the `jobapp:apply` queue fills one application's form and stops
before any decision. One job at a time (the queue's concurrency is 1, the browser is one).

`fill` goes in this order, and each step that refuses ends the job before anything later is spent:

1. Under the posting's row lock, then the application's: the application is still `queued` and
   not Apply myself (A9's); never apply is read again (`guardrails.never_apply`): a match ends it
   `blocked` before the lease (test_a_never_apply_posting_is_refused_before_the_lease). A form
   on LinkedIn passes LinkedIn's guard first (`caps.linkedin_refusal`, browser B10): the switch
   off, the sentence not accepted or a checkpoint's pause ends it `blocked` with `linkedin_off`
   or `linkedin_paused`, before the lease too.
2. The documents through `documents.current` (D14): no CV and none coming is `blocked` with
   `no_documents`; a CV or a letter still being written keeps the application `queued` with
   `needs` = `documents:<reason>`, and the tick asks again. An approved version whose render is
   queued or running is waited for the same way (`documents:rendering`), and the older rendered
   one is not filled in its place (test_which_version_the_run_takes). A wait on a render ends
   with the render: its PDF stored, or the render failed (back to idle with its code), after
   which `documents.current` answers as for any document (the latest rendered version, else
   none); a render still queued or running RENDER_WAIT_S after the wait began ends the
   application `failed` with `documents_rendering`, before any lease. Nothing is refused on
   content: the versions sent, their fit and their corrections are stored on the application
   (`document_versions`) for the run report.
3. The lease (purpose `apply_fill`, the adapter's hosts and the form's host only). Behind another
   person's lease the application stays `queued` with `needs` = `browser_queue` and an event with
   its place (written when the place changes); the tick asks again within the ticket's 60 s. A
   lease of this person and purpose left by a dead job (no token to drive it) is released, and
   the next tick starts a fresh one.
4. `queued` to `filling` (the attempt counted), the PDFs placed on the lease, the form opened, the
   adapter's fill from the resolver (A3, A4), then the answer agent for what the resolver left
   and `agent.eligible` allows (`agent.judge` keeps what is sourced and passes the fact check),
   its accepted values typed by the adapter. Then the full page "before" screenshot (an "error"
   one when the fill stopped).
5. Under the locks again: the fields with their sources (`application_answers`), the screenshot,
   the cost and the duration, and the state: `ready`; `needs_person` with what is needed (a
   captcha, a sign in or an e-mail code seen at any point of the fill, the agent's fields
   included, a required field left, `form_changed`, `no_adapter`); `posting_closed`; or `failed`
   with the tool's code, or `queued` again for a retry (A10a, `failures.py`). The posting moves
   to `staged` through `set_status` (`via: "apply"`) only from `found`, `shortlisted` or
   `cv_ready`, so a status the person set during the fill (skipped, closed, applied by a mail)
   is never moved back (test_a_status_the_person_set_during_the_fill_is_kept). An application
   the person cancelled during the fill keeps its state, and nothing of the fill is stored over
   it.

The lease is released whatever happens, and no application stays `filling`: a fill cancelled (a
stop, the job's timeout) or whose step 5 raised ends `failed` with `interrupted`, or is queued for
its retry (`interrupt`, shielded from the cancellation), and the queue's start does the same with
those a killed worker left (`reset_interrupted`). A Full auto fill holds a `full_auto` lease, which
never waits for a person (browser B12a); in Review first a step on the page (a captcha, an e-mail
code) keeps the lease while the run waits for the person (`_wait_for_person`, A10a). A dry run
ends `ready` like any other: SPEC-apply has every rehearsal go through the whole gate, and the
gate (A6) is the one that stops it as `rehearsed` before the click; this run never moves an
application past `ready`, so never to `submitting`.

Tool calls go through `Services.before_tool` when set: browser B4's `Live.agent_turn`, awaited
before each call so the person's input in the live view comes first. B4 is not merged yet, so
the worker sets none; B4's merge sets it in `services_of` (one line).

Nothing of an answer, a typed value, a screenshot or a token goes into a log, an event or the
job's result: events and logs carry ids, codes and counts.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Protocol
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import (
    agent,
    caps,
    failures,
    fallback,
    full_auto,
    guardrails,
    model_check,
    shots,
    signin,
    states,
)
from app.apply import form_agent as forms
from app.apply.answers import Answer, Context, Documents, Left, Posting
from app.apply.ats import adapter_for, generic
from app.apply.ats import base as ats_base
from app.apply.bank import AnswerBank, question_key
from app.apply.models import (
    AnswerBankEntry,
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Mode,
    ShotKind,
    State,
)
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.documents import freeze as documents
from app.documents.models import DocumentVersion
from app.jobs import service as jobs_service
from app.jobs import sweep
from app.jobs.models import Job, JobStatus
from app.profile import service as profile_service

logger = logging.getLogger(__name__)

PURPOSE = "apply_fill"
FULL_AUTO_PURPOSE = "full_auto"
CV_NAME, LETTER_NAME = "cv.pdf", "letter.pdf"
# Extra marks for browserd's submit detector, beside its own (B5): the generic submit controls.
SUBMIT_SELECTORS = ('button[type="submit"]', 'input[type="submit"]')
# The statuses a fill moves to `staged`: earlier in the pipeline only, never back.
STAGE_FROM = frozenset({JobStatus.found, JobStatus.shortlisted, JobStatus.cv_ready})
FILLED = frozenset({"filled", "unverified"})
INTERRUPTED = "interrupted"
DUE_LIMIT = 200
# A step the person takes on the page: what the page shows wins over its HTTP status.
_ON_PAGE = frozenset({*ats_base.PENDING_STEPS, failures.PAYMENT, "linkedin_paused"})


class RunTools(ats_base.Tools, Protocol):
    async def put_file(self, name: str, data: bytes) -> dict[str, Any]: ...
    async def screenshot(self, *, full_page: bool = False, kind: str = "png") -> dict[str, Any]: ...


class LeaseStore(Protocol):
    async def acquire(
        self,
        user_id: str,
        purpose: str,
        automation: bool,
        *,
        hosts: Sequence[str] = (),
        submit_selectors: Sequence[str] = (),
    ) -> Any: ...

    async def release(
        self, user_id: str, lease_id: str | None = None, reason: str = "released"
    ) -> bool: ...


@dataclass
class Services:
    sessions: sessionmaker[Session]
    clock: Callable[[], datetime]
    leases: LeaseStore
    tools_for: Callable[[str, str, str], RunTools]  # user id, lease id, token
    answer_agent: agent.AnswerAgent | None = None
    stub_base: str | None = None
    before_tool: Callable[[], Awaitable[None]] | None = None  # B4's Live.agent_turn
    form_agent: forms.FormAgent | None = None  # the generic strategy's (A8)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    retry_delays_s: tuple[int, ...] | None = None  # None: APPLY_RETRY_DELAYS_S
    needs_person_wait_s: int | None = None  # None: APPLY_NEEDS_PERSON_WAIT_S
    check: model_check.Checker | None = None  # the check by model before a Full auto decision

    def delays(self) -> tuple[int, ...]:
        return self.retry_delays_s if self.retry_delays_s is not None else failures.retry_delays()


@dataclass
class Plan:
    """What step 1 read, as plain data (no ORM object crosses into the browser steps)."""

    user_id: uuid.UUID
    application_id: uuid.UUID
    form_url: str
    context: Context
    sections: dict[str, Any]
    posting_title: str
    posting_company: str
    posting_text: str
    letter_text: str
    pdfs: dict[str, bytes] = field(repr=False, default_factory=dict)
    mode: Mode = Mode.review
    person_answers: dict[str, generic.PersonAnswer] = field(default_factory=dict, repr=False)
    person_shapes: dict[str, str] = field(default_factory=dict, repr=False)
    person_target: str = field(default="", repr=False)
    fallback_used: bool = False
    form_deadline: float | None = None


@dataclass
class Result:
    state: State
    needs: str | None = None
    error: str | None = None
    report: ats_base.Report = field(default_factory=ats_base.Report)
    shot: bytes | None = field(default=None, repr=False)
    cost: dict[str, Any] = field(default_factory=dict)
    company_url: str | None = None
    strategy: dict[str, Any] | None = None
    review_reason: str | None = None


class _Watched:
    """The lease's tools with the HTTP status of the last page opened kept (a 404 or 5xx page,
    which an adapter reads only as a page without its form)."""

    def __init__(self, tools: RunTools) -> None:
        self._tools = tools
        self.status: int | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)

    async def navigate(self, url: str) -> dict[str, Any]:
        answer = await self._tools.navigate(url)
        status = answer.get("status") if isinstance(answer, dict) else None
        self.status = status if isinstance(status, int) else None
        return answer


class _Turned:
    """The lease's tools with the live view's turn awaited before each call (B4)."""

    def __init__(self, tools: RunTools, before: Callable[[], Awaitable[None]]) -> None:
        self._tools, self._before = tools, before

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._tools, name)
        if not callable(found):
            return found

        async def call(*args: Any, **kwargs: Any) -> Any:
            await self._before()
            return await found(*args, **kwargs)

        return call


# --- the database steps (run in a thread) ------------------------------------------------------


def _event(db: Session, application: Application, kind: str, at: datetime, /, **data: Any) -> None:
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=application.user_id,
            at=at,
            kind=kind,
            data=data,
        )
    )


def _locked(db: Session, application_id: uuid.UUID) -> tuple[Application, Job] | None:
    """The application and its posting, the posting's row locked first (the order of the gate
    and of `service.start`), then the application's."""
    head = db.execute(
        sa.select(Application.user_id, Application.posting_id).where(
            Application.id == application_id
        )
    ).one_or_none()
    if head is None:
        return None
    posting = jobs_service.lock_job(db, head.user_id, head.posting_id)
    application = db.execute(
        sa.select(Application)
        .where(Application.id == application_id, Application.user_id == head.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if posting is None or application is None:
        return None
    return application, posting


def _rules(db: Session, user_id: uuid.UUID) -> list[sweep.NeverApplyRule] | None:
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        return None
    return sweep.never_apply_rules(search) if search is not None else []


def _version_record(version: documents.SentVersion) -> dict[str, Any]:
    return {
        "version_id": str(version.version_id),
        "document_id": str(version.document_id),
        "number": version.number,
        "approved": version.approved,
        "fit": version.fit.value if version.fit is not None else None,
        "fit_step": version.fit_step,
        "pages": version.pages,
        "corrections": [dict(c) for c in version.corrections],
    }


def _letter_text(db: Session, user_id: uuid.UUID, version_id: uuid.UUID) -> str:
    content = db.execute(
        sa.select(DocumentVersion.content).where(
            DocumentVersion.id == version_id, DocumentVersion.user_id == user_id
        )
    ).scalar_one_or_none()
    paragraphs = (content or {}).get("paragraphs") or []
    return "\n\n".join(str(p) for p in paragraphs if isinstance(p, str) and p.strip())


def _applied_before(db: Session, user_id: uuid.UUID, company: str, now: datetime) -> bool:
    """Confirmed submissions at this company, across roles and without another person's history.
    A rehearsal or an uncertain click cannot establish that the person applied."""
    key = guardrails.company_key(company)
    if not key:
        return False
    companies = db.scalars(
        sa.select(Job.company)
        .join(Application, Application.posting_id == Job.id)
        .where(
            Application.user_id == user_id,
            Job.user_id == user_id,
            Application.state == State.submitted,
            Application.dry_run.is_(False),
            Application.submitted_at <= now,
        )
        .distinct()
    )
    return any(guardrails.company_key(prior) == key for prior in companies)


def _bank(db: Session, user_id: uuid.UUID) -> AnswerBank:
    rows = db.execute(
        sa.select(AnswerBankEntry.question_key, AnswerBankEntry.answer).where(
            AnswerBankEntry.user_id == user_id
        )
    ).all()
    return AnswerBank(user_id, {key: value for key, value in rows if isinstance(value, str)})


# Over twice render_job.JOB_TIMEOUT_S: a render still queued or running by then is lost.
RENDER_WAIT_S = 3600
RENDERING = f"documents:{documents.RENDERING}"
RENDERING_CODE = "documents_rendering"


def _render_wait_began(db: Session, application: Application, kind: str, now: datetime) -> datetime:
    """When the application began to wait on the render of its `kind` document: the time of
    that wait's `waiting` event, else `now`. Only a new fill attempt gets a new bound; polling,
    a changed `needs` or releasing a dead browser lease does not restart the same wait."""
    wanted = {"needs": RENDERING, "document": kind, "attempt": application.attempt}
    last = db.execute(
        sa.select(ApplicationEvent.at, ApplicationEvent.data)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == "waiting",
            ApplicationEvent.data["needs"].astext == RENDERING,
            ApplicationEvent.data["document"].astext == kind,
        )
        .order_by(ApplicationEvent.id.desc())
        .limit(1)
    ).first()
    # Legacy waits have no attempt marker: retain their original deadline conservatively.
    if last is not None and last.data.get("attempt", application.attempt) == application.attempt:
        return last.at
    _event(db, application, "waiting", now, **wanted)
    # `_wait` reads the last event next, and the sessions do not autoflush.
    db.flush()
    return now


def _wait(db: Session, application: Application, needs: str, now: datetime, **data: Any) -> None:
    """Stays `queued`; the event only when what it waits for changed."""
    if application.needs != needs or data:
        last = db.execute(
            sa.select(ApplicationEvent.data)
            .where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.user_id == application.user_id,
                ApplicationEvent.kind == "waiting",
            )
            .order_by(ApplicationEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        wanted = {"needs": needs, **data}
        if last != wanted:
            _event(db, application, "waiting", now, **wanted)
    application.needs = needs
    application.updated_at = now


def _end(db: Session, application: Application, to: State, code: str, now: datetime) -> None:
    states.move(application, to, now)
    application.error_code = code
    _event(db, application, "refused" if to is State.blocked else "ended", now, code=code)


def prepare(
    db: Session, application_id: uuid.UUID, now: datetime, *, manual: bool = False
) -> Plan | str:
    """Step 1 and 2: the plan of the fill, or the code of why there is none (the caller
    commits either way). `manual`: the plan of an Apply myself session (A9, apply/manual.py),
    which never apply only warns about (at the start) and which opens without a CV."""
    found = _locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state is not State.queued:
        return "not_queued"
    if (application.mode is Mode.manual) is not manual:
        return "manual" if not manual else "not_manual"
    user_id = application.user_id
    code = None
    if not manual:
        code = guardrails.never_apply(
            posting,
            _rules(db, user_id),
            swept=guardrails.swept_never_apply(db, user_id, posting.id),
            mode=full_auto.blocking_mode(application.mode),
        )
    if code is None:
        code = caps.linkedin_refusal(db, application, now)
    if code is not None:
        _end(db, application, State.blocked, code, now)
        return "blocked"

    known_adapter = adapter_for(
        application.form_url, stub_base=caps._setting("apply_stub_base_url")
    )
    if application.mode is Mode.full_auto and getattr(known_adapter, "review_only", False):
        full_auto.hand_over(db, application, "unsupported_source", now)

    current = documents.current(db, user_id, posting.id)
    if current.cv.version is None and current.cv.coming is None and not manual:
        _end(db, application, State.blocked, "no_documents", now)
        return "blocked"
    for kind, slot in (("cv", current.cv), ("letter", current.letter)):
        if slot.version is None and slot.coming is not None:
            needs = f"documents:{slot.coming}"
        elif slot.awaits_render:
            needs = RENDERING
        else:
            continue
        if needs == RENDERING:
            began = _render_wait_began(db, application, kind, now)
            if (now - began).total_seconds() >= RENDER_WAIT_S:
                _end(db, application, State.failed, RENDERING_CODE, now)
                return "failed"
            # The clock helper owns this wait's single event. A second event here would
            # move its deadline when another waiting reason briefly took `needs`.
            application.needs = needs
            application.updated_at = now
        else:
            _wait(db, application, needs, now, document=kind)
        return "waiting_documents"
    versions: dict[str, Any] = {}
    pdfs: dict[str, bytes] = {}
    letter_text = ""
    for kind, slot, name in (("cv", current.cv, CV_NAME), ("letter", current.letter, LETTER_NAME)):
        if slot.version is None:
            continue
        data = documents.pdf(db, user_id, slot.version.version_id)
        if data is None:
            continue
        versions[kind] = _version_record(slot.version)
        pdfs[name] = data
        if kind == "letter":
            letter_text = _letter_text(db, user_id, slot.version.version_id)
    if CV_NAME not in pdfs and not manual:
        _end(db, application, State.blocked, "no_documents", now)
        return "blocked"
    application.document_versions = versions
    application.updated_at = now

    profile = profile_service.get_profile(db, user_id, now)
    sections = {k: v for k, v in profile_service.sections_view(profile).items() if v}
    context = Context(
        user_id,
        sections,
        Posting.at(posting.location, posting.title),
        Documents(
            cv_path=CV_NAME,
            letter_path=LETTER_NAME if LETTER_NAME in pdfs else None,
            letter_text=letter_text or None,
        ),
        _bank(db, user_id),
        applied_before=_applied_before(db, user_id, posting.company, now),
    )
    person_rows = list(
        db.scalars(
            sa.select(ApplicationAnswer).where(
                ApplicationAnswer.application_id == application.id,
                ApplicationAnswer.user_id == user_id,
            )
        )
    )
    return Plan(
        user_id=user_id,
        application_id=application.id,
        form_url=application.form_url,
        context=context,
        sections=sections,
        posting_title=posting.title,
        posting_company=posting.company,
        posting_text=posting.posting_text or "",
        letter_text=letter_text,
        pdfs=pdfs,
        mode=application.mode,
        person_answers={
            r.field_key: generic.PersonAnswer(r.label, r.kind, tuple(r.options or ()), r.value)
            for r in person_rows
            if r.source is AnswerSource.person
        },
        person_shapes={
            r.field_key.removeprefix("designated:shape:"): r.value
            for r in person_rows
            if r.field_key.startswith("designated:shape:")
        },
        person_target=next(
            (r.value for r in person_rows if r.field_key == "designated:target"), ""
        ),
    )


def begin(db: Session, application_id: uuid.UUID, now: datetime, purpose: str = PURPOSE) -> bool:
    """`queued` to `filling` once the lease is held; False when the application moved since."""
    found = _locked(db, application_id)
    if found is None or found[0].state is not State.queued:
        return False
    application = found[0]
    states.move(application, State.filling, now)
    application.attempt += 1
    application.needs = None
    application.error_code = None
    _event(db, application, "lease", now, purpose=purpose, attempt=application.attempt)
    return True


def still_needs(db: Session, application_id: uuid.UUID, reason: str) -> bool:
    """The application still waits in Review first for the step `reason`: the person has not
    answered, approved, rejected or cancelled it in the app meanwhile."""
    application = db.execute(
        sa.select(Application).where(Application.id == application_id)
    ).scalar_one_or_none()
    return (
        application is not None
        and application.state is State.needs_person
        and application.needs == reason
        and application.mode is Mode.review
    )


def resume(db: Session, application_id: uuid.UUID, reason: str, now: datetime) -> bool:
    """`needs_person` to `filling` once the step `reason` is gone from the page (the person took
    it in the live view); the attempt is the same. False when the application moved since."""
    found = _locked(db, application_id)
    if found is None:
        return False
    application = found[0]
    if application.state is not State.needs_person or application.needs != reason:
        return False
    states.move(application, State.filling, now)
    application.needs = None
    _event(db, application, "person_done", now, needs=reason)
    return True


def note(db: Session, application_id: uuid.UUID, kind: str, now: datetime, **data: Any) -> None:
    found = _locked(db, application_id)
    if found is not None:
        _event(db, found[0], kind, now, **data)


def _source(answer: Answer | Left, status: str) -> AnswerSource:
    if isinstance(answer, Left) or status not in FILLED:
        return AnswerSource.left
    head = answer.source.split(".", 1)[0]
    return {
        "identity": AnswerSource.identity,
        "documents": AnswerSource.document,
        "bank": AnswerSource.bank,
        "person": AnswerSource.person,
        "agent": AnswerSource.agent,
    }.get(head, AnswerSource.answers)  # answers.*, terms.*: the person's profile and rules


def _value(answer: Answer | Left, status: str) -> Any:
    if isinstance(answer, Left) or status not in FILLED:
        return None
    if answer.option is not None:
        return answer.option  # what the page shows, and what the gate (A6) refills and reads back
    value = answer.value
    return list(value) if isinstance(value, tuple) else value


def _answer_rows(
    application: Application, report: ats_base.Report, strategy: dict[str, Any] | None = None
) -> list[ApplicationAnswer]:
    rows: list[ApplicationAnswer] = []
    seen: set[str] = set()
    for outcome in report.outcomes:
        item = outcome.field
        if item.key in seen or item.key.startswith("designated:"):
            continue
        seen.add(item.key)
        rows.append(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=item.key,
                label=item.label,
                kind=item.kind,
                options=list(item.options) or None,
                value=_value(outcome.answer, outcome.filled.status),
                source=_source(outcome.answer, outcome.filled.status),
                document_letter=(
                    item.kind in {"text", "textarea"}
                    and isinstance(outcome.answer, Answer)
                    and outcome.answer.source == "documents.letter_text"
                ),
                left_reason=outcome.filled.code if outcome.filled.status == "left" else None,
                required=item.required,
                approved=False,
            )
        )
    for i, skipped in enumerate(report.skipped):
        key = f"skipped:{i}:{ats_base.fold(skipped.label)}"[:500]
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=key,
                label=skipped.label,
                kind=skipped.role,
                value=None,
                source=AnswerSource.left,
                required=False,
                approved=False,
            )
        )
    if isinstance(report, forms.FormReport):
        marks = []
        if report.designated:
            marks.append(
                (
                    generic.SUBMIT_KEY,
                    "Submit button, clicked after your approval",
                    report.designated,
                )
            )
        elif (
            isinstance(report, generic.DeterministicReport)
            and report.needs_person == "required_left"
        ):
            marks.append(("designated:incomplete", "More application steps remain", "true"))
        for i, name in enumerate(report.steps, start=1):
            marks.append((f"{generic.STEP_KEY}{i}", f"Step {i}: the button to the next step", name))
        for step, shape in getattr(report, "shapes", {}).items():
            marks.append(
                (f"designated:shape:{step}", f"Step {int(step) + 1}: field structure", shape)
            )
        if getattr(report, "target", ""):
            marks.append(("designated:target", "Application link from the job page", report.target))
        rows += [
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=key,
                label=label,
                kind="designated",
                value=name,
                source=AnswerSource.identity
                if isinstance(report, generic.DeterministicReport)
                else AnswerSource.agent,
                required=False,
                approved=False,
            )
            for key, label, name in marks
        ]
    if strategy is not None:
        rows.append(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=fallback.KEY,
                label="Application form",
                kind="designated",
                value=strategy,
                source=AnswerSource.identity,
                required=False,
                approved=False,
            )
        )
    return rows


def _counts(report: ats_base.Report) -> dict[str, int]:
    counts = {"filled": 0, "unverified": 0, "failed": 0, "left": 0, "agent": 0}
    for outcome in report.outcomes:
        counts[outcome.filled.status] += 1
        if (
            isinstance(outcome.answer, Answer)
            and outcome.answer.source.startswith("agent.")
            and outcome.filled.status in FILLED
        ):
            counts["agent"] += 1
    counts["skipped"] = len(report.skipped)
    return counts


def _record_bank_use(
    db: Session,
    application: Application,
    rows: list[ApplicationAnswer],
    report: ats_base.Report,
    now: datetime,
) -> None:
    """Count a verified bank fill once per application field, including across refills."""
    verified = {o.field.key for o in report.outcomes if o.filled.status == "filled"}
    eligible = [
        row for row in rows if row.source is AnswerSource.bank and row.field_key in verified
    ]
    if not eligible:
        return
    counted = {
        data.get("field_key")
        for data in db.scalars(
            sa.select(ApplicationEvent.data).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.user_id == application.user_id,
                ApplicationEvent.kind == "saved_answer_used",
            )
        )
    }
    for row in eligible:
        if row.field_key in counted:
            continue
        key = db.execute(
            sa.update(AnswerBankEntry)
            .where(
                AnswerBankEntry.user_id == application.user_id,
                AnswerBankEntry.question_key == question_key(row.label),
            )
            .values(used_count=AnswerBankEntry.used_count + 1)
            .returning(AnswerBankEntry.question_key)
        ).scalar_one_or_none()
        if key is not None:
            _event(db, application, "saved_answer_used", now, field_key=row.field_key)
            counted.add(row.field_key)


def finish(
    db: Session,
    application_id: uuid.UUID,
    result: Result,
    now: datetime,
    started: datetime,
    delays: Sequence[int] = (),
) -> str:
    """Step 5. Returns the state written, or `superseded` when the application left `filling`
    during the fill (the person cancelled it). A failure with retries left is `queued` again
    (`failures.after_failure`, `delays` the waits before each retry)."""
    found = _locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state is not State.filling:
        _event(db, application, "fill_superseded", now, state=application.state.value)
        return "superseded"
    if result.company_url:
        from app.apply.linkedin import follow_company

        return follow_company(db, application, posting, result.company_url, now)
    # The person's own answers (A6, `gate.set_answer`) outlive a refill: the agent never
    # replaces them, for a field the form still has.
    mine = (
        db.execute(
            sa.select(ApplicationAnswer).where(
                ApplicationAnswer.application_id == application.id,
                ApplicationAnswer.user_id == application.user_id,
                ApplicationAnswer.source == AnswerSource.person,
            )
        )
        .scalars()
        .all()
    )
    theirs = {row.field_key: row for row in mine}
    db.execute(
        sa.delete(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
        )
    )
    rows = _answer_rows(application, result.report, result.strategy)
    for row in rows:
        if row.field_key in theirs and not row.field_key.startswith(("skipped:", "designated:")):
            old = theirs[row.field_key]
            # A reused key on a changed form must not transfer the old field's allowance.
            same_shape = (row.label, row.kind, row.options, row.required) == (
                old.label,
                old.kind,
                old.options,
                old.required,
            )
            if same_shape:
                row.value, row.source = old.value, AnswerSource.person
                row.document_letter = bool(row.document_letter or old.document_letter)
                row.left_reason = None
    _record_bank_use(db, application, rows, result.report, now)
    db.add_all(rows)
    counts = _counts(result.report)
    _event(db, application, "filled", now, **counts)
    if result.shot is not None:
        kind = (
            ShotKind.before if result.state in (State.ready, State.needs_person) else ShotKind.error
        )
        shot = shots.store(db, application, kind, result.shot, now)
        _event(
            db,
            application,
            "screenshot",
            now,
            kind=kind.value,
            id=str(shot.id),
            bytes=len(result.shot),
        )
    else:
        _event(db, application, "screenshot_failed", now)
    application.cost = {**result.cost, "duration_s": round((now - started).total_seconds(), 3)}
    to, needs, retry = result.state, result.needs, {}
    if to is State.failed:
        to, retry = failures.after_failure(application, result.error, now, delays)
        needs = failures.RETRY if to is State.queued else None
    application.needs = needs
    application.error_code = result.error
    states.move(application, to, now)
    _event(
        db,
        application,
        "retry" if to is State.queued else to.value,
        now,
        needs=needs,
        code=result.error,
        dry_run=application.dry_run,
        **retry,
    )
    if result.state is State.needs_person or (result.state is State.ready and result.review_reason):
        # Full auto (A13a): what needs the person, a sign in wall included, is a review item.
        full_auto.hand_over(db, application, result.review_reason or result.needs, now)
    filled_any = counts["filled"] + counts["unverified"] > 0
    if result.state in (State.ready, State.needs_person) and filled_any:
        if posting.status in STAGE_FROM:
            jobs_service.set_status(
                db,
                posting,
                JobStatus.staged,
                now,
                via="apply",
                extra={"application_id": str(application.id)},
            )
        else:
            _event(db, application, "posting_status_kept", now, status=posting.status.value)
    return to.value


def interrupt(
    db: Session, application_id: uuid.UUID, now: datetime, delays: Sequence[int] | None = None
) -> str:
    """A fill that could not end (cancelled by a stop or its timeout, or `finish` raised):
    `failed` with `interrupted` when the application is still `filling`, so it never stays
    there, or `queued` for its retry while one is left (A10a). Locks the application's row
    alone: the posting's may be the one held."""
    application = db.execute(
        sa.select(Application)
        .where(Application.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if application is None or application.state is not State.filling:
        return "superseded"
    waits = failures.retry_delays() if delays is None else delays
    to, retry = failures.after_failure(application, INTERRUPTED, now, waits)
    if to is State.failed:
        _end(db, application, State.failed, INTERRUPTED, now)
        return to.value
    states.move(application, to, now)
    application.needs, application.error_code = failures.RETRY, INTERRUPTED
    _event(db, application, "retry", now, needs=failures.RETRY, code=INTERRUPTED, **retry)
    return to.value


def reset_interrupted(db: Session, now: datetime, delays: Sequence[int] | None = None) -> int:
    """At the worker's start no fill runs: every application left `filling` by a dead process
    ends `failed` with `interrupted`, or is queued for its retry. Apply myself's sessions use
    `manual.reset_interrupted`. Answers a count."""
    ids = (
        db.execute(
            sa.select(Application.id).where(
                Application.state == State.filling, Application.mode != Mode.manual
            )
        )
        .scalars()
        .all()
    )
    return sum(interrupt(db, application_id, now, delays) != "superseded" for application_id in ids)


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


# --- the browser steps --------------------------------------------------------------------------


def _hosts(adapter: ats_base.Adapter | None, form_url: str) -> list[str]:
    hosts = list(adapter.hosts) if adapter is not None else []
    target = adapter.form_url(form_url) if adapter is not None else None
    host = urlsplit(target or form_url).hostname
    if host:
        hosts.append(host.lower())
    return list(dict.fromkeys(hosts))


def _needs(report: ats_base.Report) -> str | None:
    if report.needs_person:
        return report.needs_person
    for outcome in report.outcomes:
        if outcome.field.required and outcome.filled.status not in FILLED:
            return "required_left"
    return None


def _stopped(code: str, step: str = "") -> tuple[State, str | None, str | None]:
    """The state a stopped fill ends in: (state, needs, error). A step for the person (A4's
    `needs_person`, the step in `Report.needs_person`: captcha, signin, email_code) and a page
    the adapter cannot read as discovered need the person; a click the tool layer refused
    (`submit`, `danger`) and a tool's failure end `failed`."""
    if code == "posting_closed":
        return State.posting_closed, None, code
    if code == "needs_person":
        return (
            State.needs_person,
            step or code,
            "linkedin_paused" if step == "linkedin_paused" else None,
        )
    if code == "linkedin_paused":
        return State.needs_person, code, code
    if code in ("form_changed", "no_adapter", "snapshot_cut", *ats_base.PENDING_STEPS):
        return State.needs_person, code, None
    return State.failed, None, code or "failed"


async def _answer_left(
    services: Services,
    plan: Plan,
    adapter: ats_base.Adapter,
    tools: RunTools,
    report: ats_base.Report,
) -> dict[str, Any]:
    """The answer agent for what is left; accepted values typed in place. Returns its cost. A
    cover letter box the resolver left gets the letter's own text first, by code."""
    for index, answer in agent.letter_boxes(report.outcomes, plan.letter_text):
        if await _type(adapter, tools, report, index, answer):
            return {"agent": "not_run"}
    questions = agent.eligible(report.outcomes)
    if not questions:
        return {"agent": "none_needed"}
    if services.answer_agent is None:
        return {"agent": "skipped", "questions": len(questions)}
    request = agent.Request(
        user_id=str(plan.user_id),
        questions=tuple(questions),
        profile=agent.profile_for(plan.sections),
        posting_title=plan.posting_title,
        posting_company=plan.posting_company,
        posting_text=plan.posting_text,
        letter_text=plan.letter_text,
    )
    try:
        reply = await services.answer_agent(request)
    except Exception as exc:  # the model failing leaves its questions
        logger.warning("apply.agent_failed", extra={"error": type(exc).__name__})
        return {"agent": "failed", "questions": len(questions)}
    if reply.stopped in agent.NOT_RUN and not reply.proposals:
        # No credential, or the person's runs are all busy: as a run without the agent.
        return {"agent": "skipped", "questions": len(questions), "reason": reply.stopped}
    verdicts = agent.judge(
        questions,
        reply,
        agent.sources_of(plan.sections, plan.posting_title, plan.posting_company, plan.letter_text),
    )
    cost = {
        "agent": "stopped" if reply.stopped else "ran",
        **({"stopped": reply.stopped} if reply.stopped else {}),
        "questions": len(questions),
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "verdicts": {},
    }
    for verdict in verdicts:
        cost["verdicts"][verdict.code] = cost["verdicts"].get(verdict.code, 0) + 1
        if verdict.answer is None:
            continue
        index = next(i for i, o in enumerate(report.outcomes) if o.field.key == verdict.key)
        if await _type(adapter, tools, report, index, verdict.answer):
            break
    return cost


async def _type(
    adapter: ats_base.Adapter,
    tools: RunTools,
    report: ats_base.Report,
    index: int,
    answer: Answer,
) -> bool:
    """One value typed in place of a left field; True when the fill must stop there."""
    item = report.outcomes[index].field
    filled = await adapter.fill(tools, item, answer)
    report.outcomes[index] = ats_base.Outcome(item, answer, filled)
    if filled.status == "failed" and filled.code in ats_base.PENDING_STEPS:
        report.stopped, report.needs_person = "needs_person", filled.code
        return True
    if filled.status == "failed" and filled.code in ats_base.STOPPING:
        report.stopped = filled.code
        return True
    return False


def _fallback_progress(session: "_FallbackSession") -> forms.FormReport:
    """The last established progress, without another read or a submit designation.

    A cancelled read cannot erase the earlier report. A completed preparation read replaces
    this page's evidence, retaining answers only for fields with the same key and structure.
    Closed wizard pages remain their original record.
    """
    outcomes = {outcome.field.key: outcome for outcome in session.closed}
    outcomes.update(
        (key, outcome)
        for key, outcome in session.outcomes.items()
        if generic._step_of(outcome.field) == session.adapter.step
    )
    outcomes.update(
        (item.key, session._outcome(item))
        for item in session.visible.values()
        if generic._step_of(item) == session.adapter.step
    )
    report = forms.FormReport(
        outcomes=list(outcomes.values()),
        skipped=list(dict.fromkeys(session.skipped)),
        steps=list(session.steps),
        stopped="form_changed",
        refused=dict(session.refused),
        addressed=session.addressed,
    )
    report.shapes = dict(getattr(session.adapter, "shapes", {}))
    return report


@dataclass
class _FallbackSession(forms.FormSession):
    """Retain current field evidence while the original attempt allowance is live."""

    visible: dict[str, ats_base.Field] = field(default_factory=dict)

    def remember(self, found: ats_base.Discovery) -> None:
        known, self.outcomes = self.outcomes, {}
        self.visible = {item.key: item for item in found.fields}
        for item in found.fields:
            outcome = known.get(item.key)
            old = outcome.field if outcome is not None else None
            if old is not None and (item.label, item.kind, item.options, item.required) == (
                old.label,
                old.kind,
                old.options,
                old.required,
            ):
                self.outcomes[item.key] = ats_base.Outcome(item, outcome.answer, outcome.filled)
        self.skipped += found.skipped

    def expired(self) -> bool:
        return self.deadline is not None and self.clock() >= self.deadline

    async def _read(self):
        if self.expired():
            self.stopped = "form_changed"
            return None, "The fill allowance has ended. Stop now."
        shapes = dict(getattr(self.adapter, "shapes", {}))
        found, problem = await super()._read()
        if found is not None:
            self.remember(found)
        else:
            self.adapter.shapes = shapes
        if self.expired():
            self.stopped = "form_changed"
            return None, "The fill allowance has ended. Stop now."
        return found, problem

    async def finish(self):
        if self.expired():
            return _fallback_progress(self)
        report = await super().finish()
        if self.expired():
            report.stopped = "form_changed"
            report.designated = report.designated_ref = ""
        return report


async def _drive_generic(
    services: Services,
    plan: Plan,
    adapter: generic.Generic,
    tools: RunTools,
    *,
    strategy: dict[str, Any] | None = None,
    previous_cost: dict[str, Any] | None = None,
    previous_report: ats_base.Report | None = None,
) -> Result:
    """Unknown forms require Review first. Known fallback keeps its remaining allowance,
    original lease and gate checks; an unverified frame is handed to Review."""
    if strategy is None and (plan.mode is not Mode.review or services.form_agent is None):
        return Result(State.needs_person, needs="no_adapter", shot=await shots.take(tools))
    adapter.person_answers = plan.person_answers
    factory = _FallbackSession if strategy is not None else forms.FormSession
    session = factory(adapter, tools, plan.context, str(plan.user_id))
    session.clock = lambda: services.clock().timestamp()
    session.deadline = plan.form_deadline
    review_reason = "frame_origin_unverified" if strategy and "frame" in strategy else None
    if strategy is not None:
        before = previous_cost or {}
        used = int(before.get("input_tokens", 0)) + int(before.get("output_tokens", 0))
        session.token_budget = max(0, agent.TOKEN_BUDGET - used)
        if before.get("stopped") == "token_budget":
            session.token_budget = 0
        adapter.person_shapes = plan.person_shapes
        if previous_report is not None:
            adapter.shapes = {
                **getattr(previous_report, "shapes", {}),
                **getattr(adapter, "shapes", {}),
            }
            session.steps = list(getattr(previous_report, "steps", ()))
            session.closed = [
                outcome
                for outcome in previous_report.outcomes
                if generic._step_of(outcome.field) < adapter.step
            ]
            session.outcomes = {
                outcome.field.key: outcome
                for outcome in previous_report.outcomes
                if generic._step_of(outcome.field) == adapter.step
            }
            session.skipped = list(previous_report.skipped)

        def stopped(state=State.needs_person, needs="form_changed", error=None):
            return Result(
                state,
                needs=needs,
                error=error,
                report=_fallback_progress(session),
                cost=before,
                strategy=strategy,
                review_reason=review_reason,
            )

        if services.form_agent is None:
            return stopped(needs="no_adapter")
        remaining = max(0.0, (session.deadline or session.clock()) - session.clock())
        if not session.token_budget or not remaining:
            return stopped()
        try:
            async with asyncio.timeout(remaining):
                if previous_report is not None:
                    # Keep known progress only where the current page has the exact same field.
                    # Refill it by code, so a model may skip an already filled field safely.
                    shapes = dict(getattr(adapter, "shapes", {}))
                    found, code = await adapter.read(tools)
                    if found is None or code or found.pending:
                        adapter.shapes = shapes
                        state, needs, error = _stopped(
                            code or "needs_person", found.pending if found else ""
                        )
                        return stopped(state, needs, error)
                    current = {item.key: item for item in found.fields}
                    session.remember(found)
                    for outcome in previous_report.outcomes:
                        if session.clock() >= session.deadline:
                            return stopped()
                        item = current.get(outcome.field.key)
                        old = outcome.field
                        if (
                            item is not None
                            and isinstance(outcome.answer, Answer)
                            and outcome.filled.status in FILLED
                            and (item.label, item.kind, item.options, item.required)
                            == (old.label, old.kind, old.options, old.required)
                        ):
                            filled = await adapter.fill(tools, item, outcome.answer)
                            if filled.status not in FILLED:
                                state, needs, error = _stopped(filled.code)
                                session.outcomes[item.key] = ats_base.Outcome(
                                    item, outcome.answer, filled
                                )
                                return stopped(state, needs, error)
                            session.outcomes[item.key] = ats_base.Outcome(
                                item, outcome.answer, filled
                            )
                if session.clock() >= session.deadline:
                    return stopped()
                report, cost = await forms.fill(services.form_agent, session)
        except TimeoutError:
            return stopped()
        cost["input_tokens"] = int(before.get("input_tokens", 0)) + cost.get("input_tokens", 0)
        cost["output_tokens"] = int(before.get("output_tokens", 0)) + cost.get("output_tokens", 0)
        cost["fallback"] = strategy["vendor"]
        report.shapes = adapter.shapes
        if session.expired():
            return Result(
                State.needs_person,
                needs="form_changed",
                report=report,
                cost=cost,
                strategy=strategy,
                review_reason=review_reason,
            )
    else:
        report, cost = await forms.fill(services.form_agent, session)
    shot = await shots.take(tools)
    if report.stopped:
        state, needs, error = _stopped(report.stopped, report.needs_person)
        return Result(
            state,
            needs=needs,
            error=error,
            report=report,
            shot=shot,
            cost=cost,
            strategy=strategy,
            review_reason=review_reason,
        )
    needs = _needs(report)
    if not report.designated:
        needs = "submit_not_designated"
    elif (lit := await generic.highlighted(tools, report.designated_ref, shot)) is None:
        needs = "submit_not_highlighted"
    else:
        shot = lit
    state = State.needs_person if needs else State.ready
    return Result(
        state,
        needs=needs,
        report=report,
        shot=shot,
        cost=cost,
        strategy=strategy,
        review_reason=review_reason,
    )


async def _known_fallback(
    services: Services,
    plan: Plan,
    adapter: ats_base.Adapter,
    tools: RunTools,
    report: ats_base.Report | None = None,
    cost: dict[str, Any] | None = None,
) -> Result:
    """One attempt only, on the already open page. A changed submit readback never calls this."""
    stopped = Result(
        State.needs_person,
        needs="form_changed",
        report=report or ats_base.Report(),
        cost=cost or {},
    )
    if plan.fallback_used:
        return stopped
    plan.fallback_used = True
    shot = await tools.snapshot()
    if _card_asked(shot):
        return await _payment_asked(tools)
    if not shot.get("ok"):
        return stopped
    if pending := adapter.pending_step(str(shot.get("snapshot") or "")):
        return Result(State.needs_person, needs=pending, shot=await shots.take(tools))
    target = fallback.target(adapter, plan.form_url, str(shot.get("url") or ""))
    if target is None:
        return stopped
    frame = None
    root = ats_base.parse_snapshot(str(shot.get("snapshot") or ""))
    scope = ats_base.form_frame(root, generic.SUBMIT_NAMES | generic.NEXT_NAMES)
    frames = [node for node in root.walk() if node.role == "iframe"]
    if adapter.framed and scope is None:
        candidates = [
            node
            for node in frames
            if ats_base.discover_fields(node, generic.SUBMIT_NAMES | generic.NEXT_NAMES).fields
        ]
        scope = candidates[0] if len(candidates) == 1 else None
    if adapter.framed or scope is not None:
        if scope is None or scope not in frames or frames.index(scope) >= 100:
            return stopped
        frame = frames.index(scope)
    prepared = fallback.Generic(services.stub_base, known=adapter, frame=frame)
    prepared.step = len(getattr(report, "steps", ()))
    if prepared.step:
        # The replay starts at the original form and follows the recorded earlier Nexts.
        target = adapter.form_url(fallback.canonical_url(plan.form_url) or "") or target
        prepared.shapes = {
            key: value
            for key, value in getattr(adapter, "seen_shapes", {}).items()
            if int(key) < prepared.step
        }
    strategy = {"version": 1, "vendor": adapter.name, "form_url": plan.form_url, "target": target}
    if frame is not None:
        strategy["frame"] = frame
    return await _drive_generic(
        services,
        plan,
        prepared,
        tools,
        strategy=strategy,
        previous_cost=cost,
        previous_report=report,
    )


async def _drive(
    services: Services, plan: Plan, tools: RunTools, adapter: ats_base.Adapter | None = None
) -> Result:
    if plan.form_deadline is None:
        plan.form_deadline = services.clock().timestamp() + forms.APPLY_FORM.wall_time_s
    for name, data in plan.pdfs.items():
        put = await tools.put_file(name, data)
        if not put.get("ok"):
            return Result(State.failed, error=str(put.get("error") or "put_file_failed"))
    adapter = adapter or adapter_for(plan.form_url, stub_base=services.stub_base)
    if adapter is None:
        opened = await tools.navigate(plan.form_url)
        if not opened.get("ok"):
            return Result(State.failed, error=str(opened.get("error") or "failed"))
        shot = await tools.snapshot()
        if _card_asked(shot):
            return await _payment_asked(tools)
        adapter = adapter_for(
            plan.form_url, str(shot.get("snapshot") or ""), stub_base=services.stub_base
        )
        if adapter is None:
            return Result(State.needs_person, needs="no_adapter", shot=await shots.take(tools))
        if isinstance(adapter, generic.Generic):
            return await _drive_generic(services, plan, adapter, tools)
    else:
        code = await adapter.open(tools, plan.form_url)
        if code == "company_site" and isinstance(adapter, generic.Deterministic):
            return Result(State.queued, company_url=adapter.company_url)
        if code == "form_changed":
            return await _known_fallback(services, plan, adapter, tools)
        if code:
            state, needs, error = _stopped(code)
            return Result(state, needs=needs, error=error, shot=await shots.take(tools))
    return await _fill_on_page(services, plan, adapter, tools)


async def _fill_on_page(
    services: Services, plan: Plan, adapter: ats_base.Adapter, tools: RunTools
) -> Result:
    """The adapter's fill of the page shown, then the answer agent for what is left. A page
    that asks for a card ends here, before anything is typed."""
    if _card_asked(await tools.snapshot()):
        return await _payment_asked(tools)
    adapter.person_answers = plan.person_answers
    if isinstance(adapter, generic.Deterministic):
        if plan.person_target and getattr(adapter, "target", "") != plan.person_target:
            return Result(State.needs_person, needs="form_changed", shot=await shots.take(tools))
        adapter.person_shapes = plan.person_shapes
        if getattr(adapter, "_waiting_report", None) is None:
            adapter.seen_shapes = {}
    report = await adapter.fill_form(tools, plan.context)
    if isinstance(adapter, generic.Deterministic):
        report.shapes = adapter.seen_shapes
    cost: dict[str, Any] = {"agent": "not_run"}
    if not report.stopped and not isinstance(adapter, generic.Deterministic):
        cost = await _answer_left(services, plan, adapter, tools, report)
    if report.stopped == "form_changed":
        return await _known_fallback(services, plan, adapter, tools, report, cost)
    if report.stopped:
        state, needs, error = _stopped(report.stopped, report.needs_person)
    else:
        needs = _needs(report)
        state, error = (State.needs_person if needs else State.ready), None
    shot = await shots.take(tools)
    if isinstance(adapter, generic.Deterministic) and getattr(report, "designated_ref", ""):
        lit = await generic.highlighted(tools, report.designated_ref, shot)
        if lit is None:
            state, needs = State.needs_person, "submit_not_highlighted"
        else:
            shot = lit
    return Result(state, needs=needs, error=error, report=report, shot=shot, cost=cost)


def _card_asked(shot: dict[str, Any]) -> bool:
    return bool(shot.get("ok")) and failures.payment_page(str(shot.get("snapshot") or ""))


async def _payment_asked(tools: RunTools) -> Result:
    return Result(State.needs_person, needs=failures.PAYMENT, shot=await shots.take(tools))


async def _checked(result: Result, tools: _Watched) -> Result:
    """What the page says beside the adapter's reading (A10a). A step the person takes on the
    page (captcha, sign in, e-mail code, a card asked) is kept whatever the page's status; else
    a 404 page is a closed posting and a 5xx page a passing fault (retried). A card field or
    frame on the page after the fill asks for payment, whatever state the fill reached."""
    if result.needs in _ON_PAGE:
        return result
    if tools.status == 410 or (result.state is not State.ready and tools.status == 404):
        return replace(result, state=State.posting_closed, needs=None, error="posting_closed")
    if result.state is not State.ready and tools.status is not None and tools.status >= 500:
        return replace(result, state=State.failed, needs=None, error=failures.HTTP_5XX)
    if tools.status == 429:
        return replace(result, state=State.failed, needs=None, error=failures.HTTP_429)
    if tools.status == 403:
        return replace(result, state=State.needs_person, needs="forbidden", error=None)
    if result.state is State.posting_closed or result.error in failures.RETRYABLE:
        return result
    try:
        shot = await tools.snapshot()
    except BrowserError:
        return result
    if _card_asked(shot):
        return replace(result, state=State.needs_person, needs=failures.PAYMENT, error=None)
    return result


async def _guarded(work: Awaitable[Result], tools: _Watched) -> Result:
    """The fill then `_checked`; nothing either raises escapes, so nothing leaves it `filling`."""
    try:
        return await _checked(await work, tools)
    except BrowserError as exc:  # the lease ended under the fill, browserd went away
        return Result(State.failed, error=exc.code)
    except Exception as exc:  # never leave an application `filling`
        logger.error("apply.fill_crashed", extra={"error": type(exc).__name__})
        return Result(State.failed, error="internal_error")


async def _wait_for_person(
    services: Services,
    plan: Plan,
    adapter: ats_base.Adapter,
    tools: _Watched,
    started: datetime,
    reason: str,
    report: ats_base.Report | None = None,
) -> str:
    """Review first, a step on the page (`failures.WAIT_ON_PAGE`): the lease is kept while the
    person takes the step in the live view, at most `needs_person_wait_s` by the clock. Once the
    step is gone from the page the fill goes on at the same page; when the person acted in the
    app (answered, rejected, cancelled) or the time is up, the run ends and the application keeps
    its state."""
    application_id = plan.application_id
    if isinstance(adapter, generic.Deterministic) and isinstance(
        report, generic.DeterministicReport
    ):
        adapter._waiting_report = report
    wait_s = services.needs_person_wait_s or failures.needs_person_wait_s()
    try:
        waiting = await tools._apply_wait("begin", reason)
    except BrowserError:
        return State.needs_person.value
    deadline = services.clock() + timedelta(seconds=min(wait_s, waiting["wait_left_s"]))
    written = State.needs_person.value

    def db(step: Callable[[Session], Any]) -> Awaitable[Any]:
        return asyncio.to_thread(_in_db, services.sessions, step)

    await db(lambda s: note(s, application_id, "waiting_person", services.clock(), needs=reason))
    while True:
        await services.sleep(failures.WAIT_POLL_S)
        if services.clock() >= deadline:
            await db(lambda s: note(s, application_id, "person_not_back", services.clock()))
            return written
        if not await db(partial(still_needs, application_id=application_id, reason=reason)):
            return "person_acted"
        try:
            waiting = await tools._apply_wait("read")
            if not waiting.get("wait_left_s"):
                return written
            shot = await tools._person_snapshot()
        except BrowserError:
            return written  # the lease ended (its maximum, browserd gone): the item waits
        page = str(shot.get("snapshot") or "")
        if not shot.get("ok") or adapter.pending_step(page) == reason:
            continue
        if reason == "forbidden":

            class Passive:
                async def snapshot(self, current=shot):
                    return current

            # Discovery opens empty comboboxes. Inspect only this passive snapshot until
            # the browser wait and the database transition have both resumed the run.
            found, code = await adapter.read(Passive())
            if found is None or code or found.pending or found.cut or adapter.anchor_problem(found):
                continue
        try:
            await tools._apply_wait("resume")
        except BrowserError:
            return written
        went_on = partial(resume, application_id=application_id, reason=reason)
        if not await db(partial(went_on, now=services.clock())):
            return "person_acted"
        # The person reached this page in the live view, not by `navigate`: its status is unknown.
        tools.status = None
        result = await _guarded(_fill_on_page(services, plan, adapter, tools), tools)
        try:
            written = await db(
                partial(
                    finish,
                    application_id=application_id,
                    result=result,
                    now=services.clock(),
                    started=started,
                    delays=services.delays(),
                )
            )
        except Exception as exc:  # as in `fill`: the posting's row held, the store gone
            logger.error("apply.finish_failed", extra={"error": type(exc).__name__})
            now, delays = services.clock(), services.delays()
            return str(
                await db(partial(interrupt, application_id=application_id, now=now, delays=delays))
            )
        if written != State.needs_person.value or result.needs not in failures.WAIT_ON_PAGE:
            return written
        reason = result.needs
        if isinstance(adapter, generic.Deterministic) and isinstance(
            result.report, generic.DeterministicReport
        ):
            adapter._waiting_report = result.report
        try:
            await tools._apply_wait("begin", reason)
        except BrowserError:
            return written


async def fill(services: Services, application_id: uuid.UUID) -> str:
    """The job: one application's fill, from `queued` to `ready`, `needs_person` or an end.
    Returns a code for the log (no value of the person's)."""
    started = services.clock()
    plan = await asyncio.to_thread(
        _in_db, services.sessions, lambda db: prepare(db, application_id, started)
    )
    if isinstance(plan, str):
        return plan
    user = str(plan.user_id)
    adapter = adapter_for(plan.form_url, stub_base=services.stub_base)
    # Full auto never waits for a person (browser B12a's rule for a `full_auto` lease).
    purpose = FULL_AUTO_PURPOSE if plan.mode is Mode.full_auto else PURPOSE

    async def wait(needs: str, **data: Any) -> None:
        def step(db: Session) -> None:
            found = _locked(db, application_id)
            if found is not None and found[0].state is State.queued:
                _wait(db, found[0], needs, services.clock(), **data)

        await asyncio.to_thread(_in_db, services.sessions, step)

    try:
        acquired = await services.leases.acquire(
            user,
            purpose,
            True,
            hosts=_hosts(adapter, plan.form_url),
            submit_selectors=SUBMIT_SELECTORS,
        )
    except (BrowserError, StoreUnavailableError) as exc:
        code = exc.code if isinstance(exc, BrowserError) else "store_unavailable"
        await wait("browser_unavailable", code=code)
        return "lease_unavailable"
    if acquired.lease is None:
        place = acquired.waiting
        await wait("browser_queue", position=place.position if place else None)
        return "waiting_lease"
    lease_id = acquired.lease.lease_id
    if acquired.token is None:
        if getattr(acquired.lease, "state", None) == "waiting_person":
            await wait("browser_signin")
            return "waiting_person"
        # Ours, from a job that died: nothing can drive it. Let the next tick start a fresh one.
        await services.leases.release(user, lease_id, "apply_restart")
        return "lease_restarted"

    def interrupted(db: Session) -> str:
        return interrupt(db, application_id, services.clock(), services.delays())

    try:
        began = await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: begin(db, application_id, services.clock(), purpose),
        )
        if not began:
            return "not_queued"
        lease_tools: RunTools = services.tools_for(user, lease_id, acquired.token)
        if services.before_tool is not None:
            lease_tools = _Turned(lease_tools, services.before_tool)  # type: ignore[assignment]
        from app.apply.linkedin import watched

        tools = _Watched(
            watched(
                services,
                plan,
                signin.Tools(
                    lease_tools, plan.form_url, pending=adapter.pending_step if adapter else None
                ),
            )
        )
        result = await _guarded(_drive(services, plan, tools, adapter), tools)
        if result.company_url:
            await services.leases.release(user, lease_id, "company_site")
        try:
            written = await asyncio.to_thread(
                _in_db,
                services.sessions,
                lambda db: finish(
                    db, application_id, result, services.clock(), started, services.delays()
                ),
            )
        except Exception as exc:  # the posting's row held past the lock timeout, the store gone
            logger.error("apply.finish_failed", extra={"error": type(exc).__name__})
            written = await asyncio.to_thread(_in_db, services.sessions, interrupted)
        if (
            written == State.needs_person.value
            and plan.mode is Mode.review
            and result.needs in failures.WAIT_ON_PAGE
            and adapter is not None
            and (
                not isinstance(adapter, generic.Generic)
                or isinstance(adapter, generic.Deterministic)
            )
        ):
            written = await _wait_for_person(
                services, plan, adapter, tools, started, str(result.needs), result.report
            )
    except asyncio.CancelledError:
        # docker stop, or the job's timeout: the application never stays `filling`.
        if await asyncio.shield(signin.waiting(services.leases, user, lease_id)):
            await asyncio.shield(
                asyncio.to_thread(
                    _in_db,
                    services.sessions,
                    lambda db: finish(
                        db,
                        application_id,
                        Result(State.needs_person, needs="signin"),
                        services.clock(),
                        started,
                        services.delays(),
                    ),
                )
            )
        else:
            await asyncio.shield(asyncio.to_thread(_in_db, services.sessions, interrupted))
        raise
    finally:
        try:
            if await signin.waiting(services.leases, user, lease_id):
                from app.browser.later_handoff import bind_wait

                await bind_wait(services.leases, user, "application", application_id, lease_id)
            else:
                await services.leases.release(user, lease_id, "apply_fill_done")
        except (BrowserError, StoreUnavailableError):
            logger.warning("apply.release_failed", extra={"application_id": str(application_id)})
    logger.info("apply.filled", extra={"application_id": str(application_id), "state": written})
    return written


# --- the tick ----------------------------------------------------------------------------------


def due(
    db: Session, now: datetime | None = None, delays: Sequence[int] | None = None
) -> list[uuid.UUID]:
    """The applications a fill should be asked for: `queued`, not Apply myself, at most one per
    person, and none for a person whose fill runs (`filling`), so one person's backlog never
    keeps another's out of the one-at-a-time queue: each tick queues the next of every person
    in turn. A person's next is their oldest application ready to fill; those waiting for their
    documents come after, the one asked longest ago first (each ask touches `updated_at`), so a
    document finished for a later one is not stuck behind an earlier one still being written.
    Oldest person's turn first, the limit after the ranking. A retry (A10a) is asked for once its
    time came by `now` (None: the database's clock), and meanwhile the person's next goes."""
    waits = sa.case((Application.needs.like("documents:%"), 1), else_=0)
    rank = sa.func.row_number().over(
        partition_by=Application.user_id,
        order_by=(
            waits,
            sa.case((waits == 1, Application.updated_at), else_=Application.created_at),
            Application.id,
        ),
    )
    filling = sa.select(Application.user_id).where(Application.state == State.filling)
    ranked = (
        sa.select(Application.id, Application.created_at, rank.label("rank"))
        .where(
            Application.state == State.queued,
            Application.mode != Mode.manual,
            Application.user_id.not_in(filling),
            ~failures.retry_waiting(now, failures.retry_delays() if delays is None else delays),
        )
        .subquery()
    )
    return list(
        db.execute(
            sa.select(ranked.c.id)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.created_at, ranked.c.id)
            .limit(DUE_LIMIT)
        ).scalars()
    )
