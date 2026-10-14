"""The apply run (SPEC-apply, "The flow of one application", steps 2 and 3): the job
`apply_fill(application_id)` of the `jobapp:apply` queue fills one application's form and stops
before any decision. One job at a time (the queue's concurrency is 1, the browser is one).

`fill` goes in this order, and each step that refuses ends the job before anything later is spent:

1. Under the posting's row lock, then the application's: the application is still `queued` and
   not Apply myself (A9's); never apply is read again (`guardrails.never_apply`): a match ends it
   `blocked` before the lease (test_a_never_apply_posting_is_refused_before_the_lease).
2. The documents through `documents.current` (D14): no CV and none coming is `blocked` with
   `no_documents`; a CV or a letter still being written keeps the application `queued` with
   `needs` = `documents:<reason>`, and the tick asks again. Nothing is refused on content: the
   versions sent, their fit and their corrections are stored on the application
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
   with the tool's code (A10 adds the retries). The posting moves to `staged` through
   `set_status` (`via: "apply"`) only from `found`, `shortlisted` or `cv_ready`, so a status the
   person set during the fill (skipped, closed, applied by a mail) is never moved back
   (test_a_status_the_person_set_during_the_fill_is_kept). An application the person cancelled
   during the fill keeps its state, and nothing of the fill is stored over it.

The lease is released whatever happens, and no application stays `filling`: a fill cancelled (a
stop, the job's timeout) or whose step 5 raised ends `failed` with `interrupted` (`interrupt`,
shielded from the cancellation), and the queue's start ends those a killed worker left
(`reset_interrupted`). A dry run ends `ready` like any other: SPEC-apply has every
rehearsal go through the whole gate, and the gate (A6) is the one that stops it as `rehearsed`
before the click; this run never moves an application past `ready`, so never to `submitting`.

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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import agent, guardrails, shots, states
from app.apply.answers import Answer, Context, Documents, Left, Posting
from app.apply.ats import adapter_for
from app.apply.ats import base as ats_base
from app.apply.bank import AnswerBank
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
CV_NAME, LETTER_NAME = "cv.pdf", "letter.pdf"
# Extra marks for browserd's submit detector, beside its own (B5): the generic submit controls.
SUBMIT_SELECTORS = ('button[type="submit"]', 'input[type="submit"]')
# The statuses a fill moves to `staged`: earlier in the pipeline only, never back.
STAGE_FROM = frozenset({JobStatus.found, JobStatus.shortlisted, JobStatus.cv_ready})
FILLED = frozenset({"filled", "unverified"})
INTERRUPTED = "interrupted"
DUE_LIMIT = 200


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


@dataclass
class Result:
    state: State
    needs: str | None = None
    error: str | None = None
    report: ats_base.Report = field(default_factory=ats_base.Report)
    shot: bytes | None = field(default=None, repr=False)
    cost: dict[str, Any] = field(default_factory=dict)


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


def _bank(db: Session, user_id: uuid.UUID) -> AnswerBank:
    rows = db.execute(
        sa.select(AnswerBankEntry.question_key, AnswerBankEntry.answer).where(
            AnswerBankEntry.user_id == user_id
        )
    ).all()
    return AnswerBank(user_id, {key: value for key, value in rows if isinstance(value, str)})


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


def prepare(db: Session, application_id: uuid.UUID, now: datetime) -> Plan | str:
    """Step 1 and 2: the plan of the fill, or the code of why there is none (the caller
    commits either way)."""
    found = _locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state is not State.queued:
        return "not_queued"
    if application.mode is Mode.manual:
        return "manual"
    user_id = application.user_id
    code = guardrails.never_apply(
        posting,
        _rules(db, user_id),
        swept=guardrails.swept_never_apply(db, user_id, posting.id),
        mode=application.mode,
    )
    if code is not None:
        _end(db, application, State.blocked, code, now)
        return "blocked"

    current = documents.current(db, user_id, posting.id)
    if current.cv.version is None and current.cv.coming is None:
        _end(db, application, State.blocked, "no_documents", now)
        return "blocked"
    for kind, slot in (("cv", current.cv), ("letter", current.letter)):
        if slot.version is None and slot.coming is not None:
            _wait(db, application, f"documents:{slot.coming}", now, document=kind)
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
    if CV_NAME not in pdfs:
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
    )


def begin(db: Session, application_id: uuid.UUID, now: datetime) -> bool:
    """`queued` to `filling` once the lease is held; False when the application moved since."""
    found = _locked(db, application_id)
    if found is None or found[0].state is not State.queued:
        return False
    application = found[0]
    states.move(application, State.filling, now)
    application.attempt += 1
    application.needs = None
    application.error_code = None
    _event(db, application, "lease", now, purpose=PURPOSE, attempt=application.attempt)
    return True


def _source(answer: Answer | Left, status: str) -> AnswerSource:
    if isinstance(answer, Left) or status not in FILLED:
        return AnswerSource.left
    head = answer.source.split(".", 1)[0]
    return {
        "identity": AnswerSource.identity,
        "documents": AnswerSource.document,
        "bank": AnswerSource.bank,
        "agent": AnswerSource.agent,
    }.get(head, AnswerSource.answers)  # answers.*, terms.*: the person's profile and rules


def _value(answer: Answer | Left, status: str) -> Any:
    if isinstance(answer, Left) or status not in FILLED:
        return None
    if answer.option is not None:
        return answer.option  # what the page shows, and what the gate (A6) refills and reads back
    value = answer.value
    return list(value) if isinstance(value, tuple) else value


def _answer_rows(application: Application, report: ats_base.Report) -> list[ApplicationAnswer]:
    rows: list[ApplicationAnswer] = []
    seen: set[str] = set()
    for outcome in report.outcomes:
        item = outcome.field
        if item.key in seen:
            continue
        seen.add(item.key)
        rows.append(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=item.key,
                label=item.label,
                kind=item.kind,
                value=_value(outcome.answer, outcome.filled.status),
                source=_source(outcome.answer, outcome.filled.status),
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


def finish(
    db: Session, application_id: uuid.UUID, result: Result, now: datetime, started: datetime
) -> str:
    """Step 5. Returns the state written, or `superseded` when the application left `filling`
    during the fill (the person cancelled it)."""
    found = _locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state is not State.filling:
        _event(db, application, "fill_superseded", now, state=application.state.value)
        return "superseded"
    # The person's own answers (A6, `gate.set_answer`) outlive a refill: the agent never
    # replaces them, for a field the form still has.
    mine = db.execute(
        sa.select(ApplicationAnswer.field_key, ApplicationAnswer.value).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
            ApplicationAnswer.source == AnswerSource.person,
        )
    ).all()
    theirs = {key: value for key, value in mine}
    db.execute(
        sa.delete(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
        )
    )
    rows = _answer_rows(application, result.report)
    for row in rows:
        if row.field_key in theirs and not row.field_key.startswith("skipped:"):
            row.value, row.source = theirs[row.field_key], AnswerSource.person
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
    application.needs = result.needs
    application.error_code = result.error
    states.move(application, result.state, now)
    _event(
        db,
        application,
        result.state.value,
        now,
        needs=result.needs,
        code=result.error,
        dry_run=application.dry_run,
    )
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
    return result.state.value


def interrupt(db: Session, application_id: uuid.UUID, now: datetime) -> str:
    """A fill that could not end (cancelled by a stop or its timeout, or `finish` raised):
    `failed` with `interrupted` when the application is still `filling`, so it never stays
    there (A10's retries start from `failed`). Locks the application's row alone: the posting's
    may be the one held."""
    application = db.execute(
        sa.select(Application)
        .where(Application.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if application is None or application.state is not State.filling:
        return "superseded"
    _end(db, application, State.failed, INTERRUPTED, now)
    return State.failed.value


def reset_interrupted(db: Session, now: datetime) -> int:
    """At the worker's start no fill runs: every application left `filling` by a dead process
    ends `failed` with `interrupted`. Answers a count."""
    ids = (
        db.execute(sa.select(Application.id).where(Application.state == State.filling))
        .scalars()
        .all()
    )
    return sum(interrupt(db, application_id, now) == State.failed.value for application_id in ids)


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
        return State.needs_person, step or code, None
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
    """The answer agent for what is left; accepted values typed in place. Returns its cost."""
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
    verdicts = agent.judge(
        questions, reply, agent.sources_of(plan.sections, plan.posting_title, plan.posting_company)
    )
    cost = {
        "agent": "stopped" if reply.stopped else "ran",
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
        item = report.outcomes[index].field
        filled = await adapter.fill(tools, item, verdict.answer)
        report.outcomes[index] = ats_base.Outcome(item, verdict.answer, filled)
        if filled.status == "failed" and filled.code in ats_base.PENDING_STEPS:
            report.stopped, report.needs_person = "needs_person", filled.code
            break
        if filled.status == "failed" and filled.code in ats_base.STOPPING:
            report.stopped = filled.code
            break
    return cost


async def _drive(services: Services, plan: Plan, tools: RunTools) -> Result:
    for name, data in plan.pdfs.items():
        put = await tools.put_file(name, data)
        if not put.get("ok"):
            return Result(State.failed, error=str(put.get("error") or "put_file_failed"))
    adapter = adapter_for(plan.form_url, stub_base=services.stub_base)
    if adapter is None:
        opened = await tools.navigate(plan.form_url)
        if not opened.get("ok"):
            return Result(State.failed, error=str(opened.get("error") or "failed"))
        shot = await tools.snapshot()
        adapter = adapter_for(
            plan.form_url, str(shot.get("snapshot") or ""), stub_base=services.stub_base
        )
        if adapter is None:
            return Result(State.needs_person, needs="no_adapter", shot=await shots.take(tools))
    else:
        code = await adapter.open(tools, plan.form_url)
        if code:
            state, needs, error = _stopped(code)
            return Result(state, needs=needs, error=error, shot=await shots.take(tools))
    report = await adapter.fill_form(tools, plan.context)
    cost: dict[str, Any] = {"agent": "not_run"}
    if not report.stopped:
        cost = await _answer_left(services, plan, adapter, tools, report)
    if report.stopped:
        state, needs, error = _stopped(report.stopped, report.needs_person)
    else:
        needs = _needs(report)
        state, error = (State.needs_person if needs else State.ready), None
    return Result(
        state, needs=needs, error=error, report=report, shot=await shots.take(tools), cost=cost
    )


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

    async def wait(needs: str, **data: Any) -> None:
        def step(db: Session) -> None:
            found = _locked(db, application_id)
            if found is not None and found[0].state is State.queued:
                _wait(db, found[0], needs, services.clock(), **data)

        await asyncio.to_thread(_in_db, services.sessions, step)

    try:
        acquired = await services.leases.acquire(
            user,
            PURPOSE,
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
        # Ours, from a job that died: nothing can drive it. Let the next tick start a fresh one.
        await services.leases.release(user, lease_id, "apply_restart")
        return "lease_restarted"

    def interrupted(db: Session) -> str:
        return interrupt(db, application_id, services.clock())

    try:
        began = await asyncio.to_thread(
            _in_db, services.sessions, lambda db: begin(db, application_id, services.clock())
        )
        if not began:
            return "not_queued"
        tools: RunTools = services.tools_for(user, lease_id, acquired.token)
        if services.before_tool is not None:
            tools = _Turned(tools, services.before_tool)  # type: ignore[assignment]
        try:
            result = await _drive(services, plan, tools)
        except BrowserError as exc:  # the lease ended under the fill, browserd went away
            result = Result(State.failed, error=exc.code)
        except Exception as exc:  # never leave an application `filling`
            logger.error("apply.fill_crashed", extra={"error": type(exc).__name__})
            result = Result(State.failed, error="internal_error")
        try:
            written = await asyncio.to_thread(
                _in_db,
                services.sessions,
                lambda db: finish(db, application_id, result, services.clock(), started),
            )
        except Exception as exc:  # the posting's row held past the lock timeout, the store gone
            logger.error("apply.finish_failed", extra={"error": type(exc).__name__})
            written = await asyncio.to_thread(_in_db, services.sessions, interrupted)
    except asyncio.CancelledError:
        # docker stop, or the job's timeout: the application never stays `filling`.
        await asyncio.shield(asyncio.to_thread(_in_db, services.sessions, interrupted))
        raise
    finally:
        try:
            await services.leases.release(user, lease_id, "apply_fill_done")
        except (BrowserError, StoreUnavailableError):
            logger.warning("apply.release_failed", extra={"application_id": str(application_id)})
    logger.info("apply.filled", extra={"application_id": str(application_id), "state": written})
    return written


# --- the tick ----------------------------------------------------------------------------------


def due(db: Session) -> list[uuid.UUID]:
    """The applications a fill should be asked for: `queued`, not Apply myself, at most one per
    person, and none for a person whose fill runs (`filling`), so one person's backlog never
    keeps another's out of the one-at-a-time queue: each tick queues the next of every person
    in turn. A person's next is their oldest application ready to fill; those waiting for their
    documents come after, the one asked longest ago first (each ask touches `updated_at`), so a
    document finished for a later one is not stuck behind an earlier one still being written.
    Oldest person's turn first, the limit after the ranking."""
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
