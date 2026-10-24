"""The submit lease (SPEC-apply, "The flow of one application", steps 4 and 5): the job
`apply_submit(application_id)` of the `jobapp:apply` queue, after the gate's decision moved the
application to `approved`. No model is called anywhere here.

`submit` goes in this order; each step that stops ends the job before the click:

1. Under the gate's locks (the posting's row, the person's apply lock, the application's row):
   still `approved`; the gate's checks that can change after its decision pass again (`recheck`:
   the approval not past its `APPLY_APPROVAL_TTL_S`, else back to `ready` with
   `authorisation_expired`; never apply; duplicates, this application excluded, so a posting
   applied or closed since, or the same role or form clicked since on another posting, ends
   `blocked` or `posting_closed` as in `decide`; the caps); and the hash of the stored fields is
   still the one the gate approved (else back to `ready`, `stale`). The pace of the form's host
   (`caps.next_slot`): a host not free yet keeps the application `approved`, waiting `host_pace`
   with the time it goes, and the tick asks again. The PDFs of the versions the approval covered
   (never newer ones).
2. A lease of purpose `apply_submit` (the only one that carries the gate's capability, browser
   B5). Behind another person's lease it stays `approved`; the tick asks again.
3. The form opened, read (`adapter.discover`, anchors and steps for the person first), and refilled
   from the approved values only, in page order, through `adapter.fill`. Then the read back: the
   fields on the page are exactly the approved ones (no field added or gone, the same unsupported
   widgets), each shows its approved value (a field left shows nothing), no alert. A fill that is
   not `filled` (`unverified` included: a yes or no group without a pressed state, a typeahead
   that keeps its text) is a difference. Any difference is `form_changed`: back to `ready`, the
   approval spent; a captcha or a sign in wall is `needs_person`; a closed posting
   `posting_closed`.
4. The "before" screenshot again, the dwell (`DWELL_S`, the paced wait before a submit that the
   fake ATS's invisible check asks for), a fresh read checked the same way, whose submit ref is
   the one clicked.
5. Under the gate's locks again, just before the click: still the decision step 1 read (the
   same spent authorisation, answers hash and `dry_run`, else `superseded`: no click, and the
   next job refills with the values approved since; a stop of this lease is `superseded` too).
   Then the kill switches (`APPLY_SUBMIT_ENABLED`, the administrator's pause read from Redis just
   before): a switch off is `cancelled` with its code. Then `recheck` again, as in step 1: the
   person's apply lock makes two approved postings of one role go one at a time, and the second
   sees the first `submitting` or `submitted`. A rehearsal (`dry_run`) ends here as `rehearsed`,
   so it reports these refusals too. Then the host's slot, under its lock (`caps.reserve`:
   another person's click since step 1 keeps it `approved` and waiting, as in step 1). Otherwise
   `submitting` is committed with its `click` event, then the one click: `gate_click` with the
   lease's capability on the adapter's submit element.
6. The page is read every `POLL_S` until `CONFIRM_WAIT_S` (60 s, by the injected clock, and at
   most `MAX_POLLS` reads): the adapter's confirmation is `submitted`, the form's errors are
   `refused_by_site`, nothing is `submit_unknown`. Never a second click.
7. `record`, in one transaction: for `submitted` and `submit_unknown` every sent version frozen
   (`documents.freeze(..., FreezeReason.apply, at)`, D14's contract, before `set_status`);
   for `submitted` the posting to `applied` through `set_status` (`via: "apply"`, `applied_on`
   filled) unless it is already applied or later, in a savepoint: if `set_status` fails, the
   confirmed submission is still `submitted`, with the code `status_not_set` and an event, and
   the posting keeps its status; the "after" screenshot; the event. If that transaction fails
   (the freeze rolls back with it), a second one records `submit_unknown` with `record_failed`,
   its freeze included.

Full auto (A13a, `full_auto.py`): the job of a `ready` Full auto item (`decision`, which the
worker runs on the agents queue) runs the check by model of the agent's free text first when it
needs one (A13c, `model_check.before_decision`), then its decision (`full_auto.decide`), and
ends; a `held` item is asked only once its hold ended (`due`), and goes the same way as an
`approved` one, with Full auto's lines in `recheck`. Any stop before the click sends it to the
review queue (`mode` `review`), a kill switch included, never to the bin.

A Review first approval that only the pace per host holds back stays valid past
`APPLY_APPROVAL_TTL_S`, at most 7 days after it was given (`full_auto.expired`, planner default
of 2026-10-19), and every other check runs again before the click.

Once `submitting` is committed nothing moves the application to a state that frees its posting:
a crash, a cancelled job or a failed record ends `submit_unknown` (shielded from the
cancellation), and `reset_interrupted`, run when the queue's worker starts, moves a `submitting`
left by a killed worker to `submit_unknown` too. A crash before it sends an `approved`
application back to `ready` with `interrupted`: nothing was clicked.

Nothing of an answer, a typed value, a screenshot, the token or the capability goes into a log,
an event or the job's result: ids, codes and counts.

Declared open points (each fails closed, toward not clicking):
- a multi-select approved with more than one value never reads back as the same (`_shown` has
  no single text to compare), so such a form ends `form_changed` and is never submitted by the
  app; the person applies by hand;
- `reset_interrupted` assumes one apply worker: it turns every `submitting` into
  `submit_unknown`, including one a second live worker would still be clicking.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps, fallback, full_auto, gate, guardrails, model_check, run, shots, states
from app.apply.answers import Answer
from app.apply.ats import adapter_for, generic
from app.apply.ats import base as ats_base
from app.apply.ats.linkedin import LinkedIn
from app.apply.models import Application, Authorisation, Mode, ShotKind, State
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.documents import freeze as documents
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

PURPOSE = "apply_submit"
DWELL_S = 4.0
POLL_S = 2.0
CONFIRM_WAIT_S = 60
MAX_POLLS = 40
# Tool codes that say the lease or the page is gone, kept as the stop's code; any other
# difference on the refill is `form_changed`.
LEASE_GONE = frozenset({"ended", "host_not_allowed", "unreadable"})

Click = Callable[[Any, str, str, str], Awaitable[dict[str, Any]]]


@dataclass
class Services:
    sessions: sessionmaker[Session]
    clock: Callable[[], datetime]
    leases: run.LeaseStore
    tools_for: Callable[[str, str, str], run.RunTools]  # user id, lease id, token
    click: Click  # browser B5's `tools.gate_click(tools, ref, capability, element)`
    paused: Callable[[], Awaitable[bool]]  # the administrator's pause (admin.paused)
    enabled: bool  # APPLY_SUBMIT_ENABLED
    stub_base: str | None = None
    check: model_check.Checker | None = None  # the check by model (A13c)


def from_fill(base: run.Services, settings: Any, redis: Any) -> Services:
    """The worker's submit services: the fill's (sessions, clock, leases, tools), browser B5's
    `gate_click`, the administrator's pause on this worker's Redis and the setting."""
    from app.apply import admin

    async def paused() -> bool:
        return await admin.paused(redis)

    return Services(
        sessions=base.sessions,
        clock=base.clock,
        leases=base.leases,
        tools_for=base.tools_for,
        click=gate._click,
        paused=paused,
        enabled=settings.apply_submit_enabled,
        stub_base=base.stub_base,
        check=base.check,
    )


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    kind: str
    value: Any
    source: str
    required: bool


@dataclass
class Plan:
    """What step 1 read under the gate's locks. `authorisation_id`, `answers_hash` and `dry_run`
    bind the click to that decision (`_bound`)."""

    user_id: uuid.UUID
    application_id: uuid.UUID
    form_url: str
    dry_run: bool
    rows: list[Row]
    authorisation_id: uuid.UUID | None
    answers_hash: bytes = field(repr=False)
    pdfs: dict[str, bytes] = field(repr=False, default_factory=dict)
    authorisation_deadline: datetime | None = None


@dataclass
class Stop:
    """How a submit lease ended before the click."""

    state: State
    code: str
    needs: str | None = None


@dataclass
class Read:
    """The form as read back, ready for the click."""

    adapter: ats_base.Adapter
    submit_ref: str
    shot: bytes | None = field(default=None, repr=False)


# --- the database steps ------------------------------------------------------------------------


def _stop(
    db: Session,
    application: Application,
    stop: Stop,
    now: datetime,
    *,
    click_refused: bool = False,
) -> None:
    states.move(application, stop.state, now, click_refused=click_refused)
    application.error_code = stop.code
    if stop.state is State.needs_person and stop.code != "linkedin_paused":
        application.error_code = None
    application.needs = stop.needs
    gate._event(db, application, "submit_stopped", now, state=stop.state.value, code=stop.code)
    if stop.state in (State.ready, State.needs_person):
        full_auto.hand_over(db, application, stop.code, now)


def _locked(db: Session, application_id: uuid.UUID) -> tuple[Application, Job] | None:
    """The application and its posting under the gate's locks, in its order (posting, person,
    application), so the checks again before the click see what `decide` would see."""
    user_id = db.execute(
        sa.select(Application.user_id).where(Application.id == application_id)
    ).scalar_one_or_none()
    if user_id is None:
        return None
    try:
        return gate.lock(db, user_id, application_id)
    except gate.NotFoundError:
        return None


def _spent(db: Session, application: Application) -> Authorisation | None:
    """The authorisation the gate spent on this approval: the latest one used."""
    return db.execute(
        sa.select(Authorisation)
        .where(
            Authorisation.application_id == application.id,
            Authorisation.user_id == application.user_id,
            Authorisation.used_at.is_not(None),
        )
        .order_by(Authorisation.used_at.desc(), Authorisation.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def recheck(db: Session, application: Application, posting: Job, now: datetime) -> Stop | None:
    """The gate's checks that can change after its decision, again (the caller holds the gate's
    locks): the approval still valid (`full_auto.expired`, else `ready`,
    `authorisation_expired`), never apply, LinkedIn's guard on a form on LinkedIn (a pause that
    started since), duplicates (this application excluded), the caps, the current document
    versions in both modes, and in Full auto `full_auto.recheck`. A refusal ends as `decide`'s
    does, `blocked` with its code
    (`posting_closed` for a closed posting), except that a code `decide` keeps at `ready` goes
    back to `ready`: an approved application never waits on it."""
    spent = _spent(db, application)
    if spent is None or full_auto.expired(application, spent, now):
        return Stop(State.ready, "authorisation_expired")
    return _checks(db, application, posting, now)


def _checks(db: Session, application: Application, posting: Job, now: datetime) -> Stop | None:
    """Mutable non-authority guards, also checked after the admitted authority was consumed."""
    code = guardrails.never_apply(
        posting,
        gate._rules(db, application.user_id),
        swept=guardrails.swept_never_apply(db, application.user_id, posting.id),
        mode=full_auto.blocking_mode(application.mode),
    ) or caps.linkedin_refusal(db, application, now)
    if code is None:
        duplicate = guardrails.duplicates(
            db, application.user_id, posting, application.canonical_key, now, exclude=application.id
        )
        code = duplicate.code if duplicate is not None else caps.check(db, application, now)
    if code is None:
        code = gate.documents_code(db, application)
    if code is None and application.mode is Mode.full_auto:
        code = full_auto.recheck(db, application, posting, now)
    if code is None:
        return None
    if code in gate.KEEP or (
        application.mode is Mode.full_auto
        and code in ("linkedin_off", "linkedin_paused", "linkedin_daily")
    ):
        return Stop(State.ready, code)
    return Stop(State.posting_closed if code == "posting_closed" else State.blocked, code)


def prepare(db: Session, application_id: uuid.UUID, now: datetime) -> Plan | str:
    """Step 1: the plan, or the code of why there is none (the caller commits either way)."""
    found = _locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state not in states.DECIDED:
        return "not_approved"
    if application.state is State.held:
        until = full_auto.hold_until(db, application)
        if until is None or now < until:
            return "holding"
    if refused := recheck(db, application, posting, now):
        _stop(db, application, refused, now)
        return refused.code
    spent = _spent(db, application)
    rows = gate.rows_of(db, application)
    if application.answers_hash is None or gate.answers_hash(
        rows, application.document_versions
    ) != bytes(application.answers_hash):
        _stop(db, application, Stop(State.ready, "stale"), now)
        return "stale"
    if not application.dry_run and (until := caps.next_slot(db, application, now)) is not None:
        run._wait(db, application, "host_pace", now, until=until.isoformat())
        return "paced"
    pdfs: dict[str, bytes] = {}
    for kind, name in (("cv", run.CV_NAME), ("letter", run.LETTER_NAME)):
        record = (application.document_versions or {}).get(kind)
        if not isinstance(record, dict):
            continue
        data = documents.pdf(db, application.user_id, uuid.UUID(str(record.get("version_id"))))
        if data is None:
            _stop(db, application, Stop(State.ready, "no_documents"), now)
            return "no_documents"
        pdfs[name] = data
    return Plan(
        user_id=application.user_id,
        application_id=application.id,
        form_url=application.form_url,
        dry_run=application.dry_run,
        rows=[Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required) for r in rows],
        authorisation_id=spent.id if spent is not None else None,
        answers_hash=bytes(application.answers_hash),
        pdfs=pdfs,
    )


def _bound(db: Session, plan: Plan) -> tuple[Application, Job] | None:
    """The application under the gate's locks while it is still the decision `plan` was read
    from: `approved` or `held` with the same spent authorisation, the same answers hash and the
    same `dry_run`. None otherwise (a new approval since, an answer changed, a rehearsal released):
    the lease that holds this plan refilled the form with values nobody approved since."""
    found = _locked(db, plan.application_id)
    if found is None or found[0].state not in states.DECIDED:
        return None
    application = found[0]
    spent = _spent(db, application)
    if (
        spent is None
        or plan.authorisation_id is None
        or spent.id != plan.authorisation_id
        or application.answers_hash is None
        or bytes(application.answers_hash) != plan.answers_hash
        or application.dry_run != plan.dry_run
    ):
        return None
    return found


def stopped(db: Session, plan: Plan, stop: Stop, now: datetime) -> str:
    found = _bound(db, plan)
    if found is None:
        return "superseded"
    _stop(db, found[0], stop, now)
    return stop.state.value


def wait(db: Session, application_id: uuid.UUID, needs: str, now: datetime, **data: Any) -> None:
    found = run._locked(db, application_id)
    if found is not None and found[0].state in states.DECIDED:
        run._wait(db, found[0], needs, now, **data)


def arm(
    db: Session,
    plan: Plan,
    now: datetime,
    switches: gate.Switches,
    read: Read,
    *,
    clock: Callable[[], datetime] | None = None,
    switches_for: Callable[[], gate.Switches] | None = None,
) -> str:
    """Step 5, before the click, under the gate's locks: still the decision `plan` was read from
    (`_bound`, else `superseded`: nothing written, no click, the next job refills with what was
    approved), the screenshot, the kill switches, the gate's checks again (`recheck`), then
    `rehearsed` or `submitting` (committed by the caller before the click). Returns what was
    written."""
    found = _bound(db, plan)
    if found is None:
        return "superseded"
    application, posting = found
    now = clock() if clock else now
    if read.shot is not None:
        shot = shots.store(db, application, ShotKind.before, read.shot, now)
        gate._event(db, application, "screenshot", now, kind="before", id=str(shot.id))
    else:
        gate._event(db, application, "screenshot_failed", now)
    switches = switches_for() if switches_for else switches
    now = clock() if clock else now
    if (code := gate.switch_code(switches)) and application.mode is Mode.full_auto:
        _stop(db, application, Stop(State.ready, code), now)
        return State.ready.value
    if code:
        states.move(application, State.cancelled, now)
        application.error_code = code
        gate._event(db, application, "kill_switch", now, code=code)
        return "cancelled"
    if refused := recheck(db, application, posting, now):
        _stop(db, application, refused, now)
        return refused.state.value
    if application.dry_run:
        states.move(application, State.rehearsed, now)
        gate._event(db, application, "rehearsed", now)
        return "rehearsed"
    until = caps.reserve(db, application, now, clock=clock)
    switches = switches_for() if switches_for else switches
    now = clock() if clock else now
    if code := gate.switch_code(switches):
        target = State.ready if application.mode is Mode.full_auto else State.cancelled
        _stop(db, application, Stop(target, code), now)
        return target.value
    if refused := recheck(db, application, posting, now):
        _stop(db, application, refused, now)
        return refused.state.value
    if until is not None:
        run._wait(db, application, "host_pace", now, until=until.isoformat())
        return "paced"
    spent = _spent(db, application)
    if spent is None:
        _stop(db, application, Stop(State.ready, "authorisation_expired"), now)
        return State.ready.value
    plan.authorisation_deadline = full_auto.deadline(application, spent)
    states.move(application, State.submitting, now)
    application.needs = None
    gate._event(db, application, "click", now, attempt=application.attempt)
    return "submitting"


def record(
    db: Session,
    application_id: uuid.UUID,
    outcome: State,
    now: datetime,
    *,
    shot: bytes | None = None,
    code: str | None = None,
    errors: int = 0,
) -> str:
    """Step 7: the outcome of the click, in one transaction (the caller commits)."""
    found = run._locked(db, application_id)
    if found is None:
        return "gone"
    application, posting = found
    if application.state is not State.submitting:
        gate._event(db, application, "outcome_superseded", now, state=application.state.value)
        return "superseded"
    frozen = 0
    if outcome in states.CLICKED:
        frozen = gate.freeze_sent(db, application, now)
    if outcome is State.submitted and JobStatus(posting.status) not in guardrails.APPLIED_STATUSES:
        try:
            with db.begin_nested():
                jobs_service.set_status(
                    db,
                    posting,
                    JobStatus.applied,
                    now,
                    via="apply",
                    extra={"application_id": str(application.id)},
                )
        except Exception as exc:  # the site confirmed it: never lose that for a posting status
            logger.error("apply.status_not_set", extra={"error": type(exc).__name__})
            code = "status_not_set"
            gate._event(db, application, "status_not_set", now, error=type(exc).__name__)
    states.move(application, outcome, now)
    application.error_code = code
    if shot is not None:
        stored = shots.store(db, application, ShotKind.after, shot, now)
        gate._event(db, application, "screenshot", now, kind="after", id=str(stored.id))
    gate._event(
        db, application, outcome.value, now, code=code, frozen=frozen, errors=errors or None
    )
    return outcome.value


def interrupted(db: Session, application_id: uuid.UUID, now: datetime, code: str) -> str:
    """A crash or a cancellation: `submit_unknown` after `submitting` (the click may have gone
    through), `ready` before it (nothing was clicked)."""
    found = run._locked(db, application_id)
    if found is None:
        return "gone"
    application = found[0]
    if application.state is State.submitting:
        return record(db, application_id, State.submit_unknown, now, code=code)
    if application.state in states.DECIDED:
        _stop(db, application, Stop(State.ready, code), now)
        return "ready"
    return "kept"


def reset_interrupted(db: Session, now: datetime) -> int:
    """At the worker's start: every `submitting` left by a killed worker is `submit_unknown`,
    with its versions frozen. `approved` ones stay: the tick asks their submit again."""
    ids = list(
        db.execute(sa.select(Application.id).where(Application.state == State.submitting)).scalars()
    )
    for application_id in ids:
        interrupted(db, application_id, now, "interrupted")
    return len(ids)


def due(db: Session, now: datetime | None = None) -> list[uuid.UUID]:
    """The applications whose submit job the tick asks for, oldest first: `approved` ones, `held`
    ones whose hold ended at `now` (the database's clock when None), and `ready` Full auto ones,
    whose job runs the decision."""
    return list(
        db.execute(
            sa.select(Application.id)
            .where(
                sa.or_(
                    Application.state == State.approved,
                    Application.id.in_(full_auto.held_over(now)),
                    sa.and_(Application.state == State.ready, Application.mode == Mode.full_auto),
                )
            )
            .order_by(Application.updated_at, Application.id)
            .limit(200)
        ).scalars()
    )


def undecided_of(db: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Those of `ids` that are `ready` Full auto items: the tick asks their decision alone."""
    if not ids:
        return set()
    return set(
        db.execute(
            sa.select(Application.id).where(
                Application.id.in_(ids),
                Application.state == State.ready,
                Application.mode == Mode.full_auto,
            )
        ).scalars()
    )


# --- the browser steps -------------------------------------------------------------------------


def _answer(row: Row) -> Answer:
    """The approved value as the adapter fills it: the option the page showed for a choice."""
    value = row.value
    source = f"approved.{row.source}"
    if row.kind == "file":
        return Answer(str(value), f"documents.{row.source}")
    if row.kind == "checkbox":
        return Answer(value is True, source)
    if row.kind == "yes_no":
        text = ("Yes" if value else "No") if isinstance(value, bool) else str(value)
        return Answer(text, source, option=text)
    if isinstance(value, list):
        return Answer(tuple(str(v) for v in value), source)
    if row.kind in ("select", "combobox"):
        return Answer(str(value), source, option=str(value))
    return Answer(str(value), source)


def _shown(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if len(value) == 1 else None
    return str(value)


def _same(one: str, two: str) -> bool:
    return " ".join(ats_base.fold(one).split()) == " ".join(ats_base.fold(two).split())


def reads_back(item: ats_base.Field, row: Row) -> bool:
    """The page shows the approved value of `row` in `item` (a field left shows nothing)."""
    if row.value is None:
        return not item.value and not item.typed and not item.checked
    if item.kind == "checkbox":
        return bool(item.checked) is (row.value is True)
    if item.kind == "yes_no":
        wanted = _answer(row).option or ""
        return item.checked is not None and item.value == wanted
    wanted_text = _shown(row.value)
    return wanted_text is not None and _same(item.value, wanted_text)


def difference(found: ats_base.Discovery, rows: list[Row]) -> str:
    """'' when the form read is the approved one, else what differs (a code, no value)."""
    approved = {r.key: r for r in rows if not r.key.startswith("skipped:")}
    if {f.key for f in found.fields} != set(approved):
        return "fields_differ"
    skipped_now = sorted(ats_base.fold(s.label) for s in found.skipped)
    skipped_then = sorted(ats_base.fold(r.label) for r in rows if r.key.startswith("skipped:"))
    if skipped_now != skipped_then:
        return "widgets_differ"
    if found.alerts:
        return "alert_shown"
    if len(found.submit_refs) != 1:
        return "submit_missing"
    if any(not reads_back(f, approved[f.key]) for f in found.fields):
        return "value_differs"
    return ""


def _stop_of(code: str, step: str = "") -> Stop:
    if code == "linkedin_paused" or (code == "needs_person" and step == "linkedin_paused"):
        return Stop(State.needs_person, "linkedin_paused", "linkedin_paused")
    if code == "needs_person":
        return Stop(State.needs_person, "needs_person", step or "needs_person")
    if code in (*ats_base.PENDING_STEPS, run.failures.PAYMENT, "profile_incomplete"):
        return Stop(State.needs_person, "needs_person", code)
    if code == "posting_closed":
        return Stop(State.posting_closed, code)
    if code in LEASE_GONE:
        return Stop(State.ready, code)
    return Stop(State.ready, "form_changed")


async def _open(services: Services, plan: Plan, tools: run.RunTools) -> ats_base.Adapter | Stop:
    has_strategy = any(row.key == fallback.KEY for row in plan.rows)
    address = fallback.canonical_url(plan.form_url) if has_strategy else plan.form_url
    if address is None:
        return Stop(State.ready, "form_changed")
    adapter = adapter_for(address, stub_base=services.stub_base)
    if has_strategy:
        # A company page's framed adapter is recovered from the code-owned vendor, never
        # chosen by a model during replay. Framed bindings confer no Full auto exception.
        if adapter is None:
            candidates = [
                fallback.binding(plan.rows, plan.form_url, vendor, allow_frame=True)
                for vendor in fallback.KNOWN
            ]
            framed = next((value for value in candidates if value and "frame" in value), None)
            if framed:
                from app.apply.ats import ADAPTERS

                adapter = next(
                    (
                        kind(services.stub_base, framed=True)
                        for kind in ADAPTERS
                        if kind.name == framed["vendor"]
                    ),
                    None,
                )
        bound = fallback.binding(
            plan.rows, plan.form_url, adapter.name if adapter else "", allow_frame=True
        )
        if bound and adapter and "frame" in bound:
            adapter.framed = True
        if bound is None or fallback.target(adapter, plan.form_url, bound["target"]) is None:
            return Stop(State.ready, "form_changed")
        if ("frame" in bound) != bool(adapter and adapter.framed):
            return Stop(State.ready, "form_changed")
        prepared = fallback.Generic(services.stub_base, known=adapter, frame=bound.get("frame"))
        code = await prepared.visit(tools, bound["target"])
        if code:
            return _stop_of(code)
        shot = await tools.snapshot()
        if fallback.canonical_url(str(shot.get("url") or "")) != fallback.canonical_url(
            bound["target"]
        ):
            return Stop(State.ready, "form_changed")
        return prepared
    if adapter is None:
        opened = await tools.navigate(plan.form_url)
        if not opened.get("ok"):
            return Stop(State.ready, str(opened.get("error") or "failed"))
        shot = await tools.snapshot()
        adapter = adapter_for(
            plan.form_url, str(shot.get("snapshot") or ""), stub_base=services.stub_base
        )
        return adapter if adapter is not None else Stop(State.ready, "form_changed")
    code = await adapter.open(tools, plan.form_url)
    return _stop_of(code) if code else adapter


async def refill(
    adapter: ats_base.Adapter, tools: run.RunTools, rows: list[Row]
) -> ats_base.Discovery | Stop:
    """Step 3: the approved values typed again, in page order, then the form read back."""
    approved = {r.key: r for r in rows if not r.key.startswith("skipped:")}
    found, code = await adapter.discover(tools)
    if found is None or code:
        return _stop_of(code, found.pending if found is not None else "")
    done: set[str] = set()
    for _ in range(2):
        pending = [f for f in found.fields if f.key not in done]
        if isinstance(adapter, generic.Deterministic):
            pending.sort(key=lambda field: field.kind != "file")
        for item in pending:
            done.add(item.key)
            row = approved.get(item.key)
            if row is None:
                return Stop(State.ready, "form_changed")  # a question nobody approved
            if row.value is None:
                continue  # left: the read back checks it shows nothing
            if (
                row.kind == "checkbox"
                and row.value is not True
                and not (isinstance(adapter, LinkedIn) and adapter.optional_follow(item))
            ):
                # A box approved unticked (the person's No): the adapter never ticks it, so
                # there is nothing to type; the read back checks the page shows it unticked.
                continue
            filled = await adapter.fill(tools, item, _answer(row))
            if filled.status != "filled":
                return _stop_of(filled.code if filled.status == "failed" else "")
        found, code = await adapter.discover(tools, frozenset(done))
        if found is None or code:
            return _stop_of(code, found.pending if found is not None else "")
        if all(f.key in done for f in found.fields):
            break
    return found


async def _read_back(
    adapter: ats_base.Adapter, tools: run.RunTools, rows: list[Row]
) -> ats_base.Discovery | Stop:
    found, code = await adapter.discover(tools, frozenset(r.key for r in rows))
    if found is None or code:
        return _stop_of(code, found.pending if found is not None else "")
    if problem := difference(found, rows):
        logger.info("apply.read_back_differs", extra={"code": problem})
        return Stop(State.ready, "form_changed")
    return found


async def ready_to_click(services: Services, plan: Plan, tools: run.RunTools) -> Read | Stop:
    """Steps 3 and 4."""
    for name, data in plan.pdfs.items():
        put = await tools.put_file(name, data)
        if not put.get("ok"):
            return Stop(State.ready, str(put.get("error") or "put_file_failed"))
    opened = await _open(services, plan, tools)
    if isinstance(opened, Stop):
        return opened
    # The generic strategy (A8): its approved designation, and each earlier step refilled, read
    # back and left by its recorded Next, with no model.
    adapter = generic.configured(opened, plan.rows)
    stages = generic.stages(adapter, plan.rows) if adapter is not None else []
    if adapter is None or not stages:
        return Stop(State.ready, "form_changed")
    for rows in stages[:-1]:
        step = await _refilled(adapter, tools, rows)
        if isinstance(step, Stop):
            return step
        code = await generic.advance(adapter, tools, step)  # type: ignore[arg-type]
        if code:
            return _stop_of(code)
    rows = stages[-1]
    checked = await _refilled(adapter, tools, rows)
    if isinstance(checked, Stop):
        return checked
    shot = await shots.take(tools)
    lit = await generic.highlighted(tools, checked.submit_refs[0], shot)
    await tools.wait_for(time=DWELL_S)
    final = await _read_back(adapter, tools, rows)  # fresh refs for the click
    if isinstance(final, Stop):
        return final
    if code := await generic.recheck(adapter, tools, final):
        logger.info("apply.designation_refused", extra={"code": code})
        return Stop(State.ready, "form_changed")
    return Read(adapter, final.submit_refs[0], lit or shot)


async def _refilled(
    adapter: ats_base.Adapter, tools: run.RunTools, rows: list[Row]
) -> ats_base.Discovery | Stop:
    refilled = await refill(adapter, tools, rows)
    if isinstance(refilled, Stop):
        return refilled
    return await _read_back(adapter, tools, rows)


async def outcome(
    services: Services, adapter: ats_base.Adapter, tools: run.RunTools, clicked: datetime
) -> tuple[State, int]:
    """Step 6: `submitted`, `refused_by_site` (with the count of errors) or `submit_unknown`."""
    deadline = clicked + timedelta(seconds=CONFIRM_WAIT_S)
    for _ in range(MAX_POLLS):
        shot = await tools.snapshot()
        if shot.get("ok"):
            snapshot = str(shot.get("snapshot") or "")
            if adapter.confirmation(ats_base.page_text(snapshot), str(shot.get("url") or "")):
                return State.submitted, 0
            if errors := adapter.errors(snapshot):
                return State.refused_by_site, len(errors)
        if services.clock() >= deadline:
            break
        await tools.wait_for(time=POLL_S)
    return State.submit_unknown, 0


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    return run._in_db(sessions, step)


async def _record_click(
    services: Services, application_id: uuid.UUID, result: State, **rest: Any
) -> str:
    """`record`, and if its transaction fails, `submit_unknown` with `record_failed`."""
    try:
        return await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: record(db, application_id, result, services.clock(), **rest),
        )
    except Exception as exc:
        logger.error("apply.record_failed", extra={"error": type(exc).__name__})
        return await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: record(
                db, application_id, State.submit_unknown, services.clock(), code="record_failed"
            ),
        )


async def _after_arm(
    services: Services,
    plan: Plan,
    read: Read,
    tools: run.RunTools,
    capability: str,
) -> str:
    """The click and its outcome; the application is `submitting` (committed)."""
    application_id = plan.application_id
    loop = asyncio.get_running_loop()

    def before_click(db: Session) -> str | datetime:
        found = _locked(db, application_id)
        if found is None or found[0].state is not State.submitting:
            return "superseded"
        application, posting = found
        spent = _spent(db, application)
        try:
            paused = asyncio.run_coroutine_threadsafe(services.paused(), loop).result()
        except Exception:
            paused = True
        now = services.clock()
        code = gate.switch_code(gate.Switches(enabled=services.enabled, paused=paused))
        if code is None and (
            spent is None
            or spent.id != plan.authorisation_id
            or spent.kind is not full_auto.gate_kind(application.mode)
            or plan.authorisation_deadline is None
            or now >= plan.authorisation_deadline
        ):
            code = "authorisation_expired"
        if code is None and (
            bytes(application.answers_hash or b"") != plan.answers_hash
            or application.dry_run != plan.dry_run
            or gate.current_hash(db, application) != plan.answers_hash
        ):
            code = "stale"
        if code is None and (refused := _checks(db, application, posting, now)):
            code = refused.code
        if code:
            _stop(db, application, Stop(State.ready, code), now, click_refused=True)
            return State.ready.value
        return plan.authorisation_deadline

    admitted = await asyncio.to_thread(_in_db, services.sessions, before_click)
    if isinstance(admitted, str):
        return admitted
    # The commit above can itself wait. No database work separates this live pause read,
    # fresh clock and external click; the deadline belongs to the exact admitted authority.
    try:
        paused = await services.paused()
    except Exception:
        paused = True
    clicked = services.clock()
    code = gate.switch_code(gate.Switches(enabled=services.enabled, paused=paused))
    if code is None and clicked >= admitted:
        code = "authorisation_expired"
    if code:

        def refuse(db: Session) -> str:
            found = _locked(db, application_id)
            if found is None or found[0].state is not State.submitting:
                return "superseded"
            _stop(db, found[0], Stop(State.ready, code), clicked, click_refused=True)
            return State.ready.value

        return await asyncio.to_thread(_in_db, services.sessions, refuse)
    code: str | None = None
    after: bytes | None = None
    try:
        result = await services.click(tools, read.submit_ref, capability, "Submit")
        if not result.get("ok") and result.get("error") in ("person_input", "person_used"):
            # browserd refused before its external act. The person may have edited the page,
            # so return to a fresh approval, with no sent-version freeze or uncertain outcome.
            def taken(db: Session) -> str:
                found = run._locked(db, application_id)
                if found is None or found[0].state is not State.submitting:
                    return "superseded"
                _stop(
                    db,
                    found[0],
                    Stop(State.ready, "form_changed"),
                    services.clock(),
                    click_refused=True,
                )
                return State.ready.value

            return await asyncio.to_thread(_in_db, services.sessions, taken)
        if not result.get("ok"):
            code = f"click_{result.get('error') or 'failed'}"
        state, errors = await outcome(services, read.adapter, tools, clicked)
        after = await shots.take(tools)
    except BrowserError as exc:  # the lease ended after the click: nobody knows
        state, errors, code = State.submit_unknown, 0, exc.code
    if state is not State.submit_unknown:
        code = None
    return await _record_click(
        services, application_id, state, shot=after, code=code, errors=errors
    )


async def _drive(
    services: Services,
    plan: Plan,
    tools: run.RunTools,
    capability: str | None,
    armed: list[bool],
) -> str:
    read = await ready_to_click(services, plan, tools)
    if isinstance(read, Stop):
        return await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: stopped(db, plan, read, services.clock()),
        )
    if capability is None and not plan.dry_run:
        stop = Stop(State.ready, "no_capability")
        return await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: stopped(db, plan, stop, services.clock()),
        )
    switches = gate.Switches(enabled=services.enabled, paused=await services.paused())
    loop = asyncio.get_running_loop()

    def fresh_switches() -> gate.Switches:
        paused = asyncio.run_coroutine_threadsafe(services.paused(), loop).result()
        return gate.Switches(enabled=services.enabled, paused=paused)

    written = await asyncio.to_thread(
        _in_db,
        services.sessions,
        lambda db: arm(
            db,
            plan,
            services.clock(),
            switches,
            read,
            clock=services.clock,
            switches_for=fresh_switches,
        ),
    )
    if written != "submitting" or capability is None:
        return written
    armed.append(True)
    return await _after_arm(services, plan, read, tools, capability)


async def _decide(services: Services, application_id: uuid.UUID) -> str:
    if services.check is not None:
        waiting = await model_check.before_decision(
            services.check, services.sessions, services.clock, application_id
        )
        if waiting is not None:
            return waiting
    switches = gate.Switches(enabled=services.enabled, paused=await services.paused())
    try:
        return await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: full_auto.decide(db, application_id, services.clock(), switches),
        )
    except jobs_service.JobBusyError:
        return "busy"


async def decision(services: Services, application_id: uuid.UUID) -> str:
    """The job of a `ready` Full auto item's decision alone, its check by model included, never a
    lease: the worker runs it on the agents queue, so the call never holds the browser's queue."""
    undecided = await asyncio.to_thread(
        _in_db, services.sessions, lambda db: full_auto.undecided(db, application_id)
    )
    if not undecided:
        return "not_undecided"
    return await _decide(services, application_id)


async def submit(services: Services, application_id: uuid.UUID) -> str:
    """The job: one approved or held application to its outcome. A `ready` Full auto one is left
    to `decision`, which the tick queues: a submit job queued for it before that job existed makes
    no second model call. Returns a code for the log (no value of the person's)."""
    undecided = await asyncio.to_thread(
        _in_db, services.sessions, lambda db: full_auto.undecided(db, application_id)
    )
    if undecided:
        return "undecided"
    plan = await asyncio.to_thread(
        _in_db, services.sessions, lambda db: prepare(db, application_id, services.clock())
    )
    if isinstance(plan, str):
        return plan
    user = str(plan.user_id)

    async def waiting(needs: str, **data: Any) -> None:
        await asyncio.to_thread(
            _in_db,
            services.sessions,
            lambda db: wait(db, application_id, needs, services.clock(), **data),
        )

    adapter = adapter_for(plan.form_url, stub_base=services.stub_base)
    try:
        acquired = await services.leases.acquire(
            user,
            PURPOSE,
            True,
            hosts=run._hosts(adapter, plan.form_url),
            submit_selectors=run.SUBMIT_SELECTORS,
            application_id=str(application_id),
        )
    except (BrowserError, StoreUnavailableError) as exc:
        await waiting(
            "browser_unavailable",
            code=exc.code if isinstance(exc, BrowserError) else "store_unavailable",
        )
        return "lease_unavailable"
    if acquired.lease is None:
        place = acquired.waiting
        await waiting("browser_queue", position=place.position if place else None)
        return "waiting_lease"
    lease_id = acquired.lease.lease_id
    if acquired.token is None:
        await services.leases.release(user, lease_id, "apply_restart")
        return "lease_restarted"
    capability = getattr(acquired, "capability", None)
    armed: list[bool] = []
    written = "interrupted"
    try:
        tools = services.tools_for(user, lease_id, acquired.token)
        from app.apply.linkedin import watched

        tools = watched(services, plan, tools)
        try:
            written = await _drive(services, plan, tools, capability, armed)
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.to_thread(
                    _in_db,
                    services.sessions,
                    lambda db: interrupted(db, application_id, services.clock(), "interrupted"),
                )
            )
            raise
        except Exception as exc:  # never leave an application `approved` or `submitting`
            code = exc.code if isinstance(exc, BrowserError) else "internal_error"
            if not isinstance(exc, BrowserError):
                logger.error("apply.submit_crashed", extra={"error": type(exc).__name__})
            written = await asyncio.to_thread(
                _in_db,
                services.sessions,
                lambda db: interrupted(db, application_id, services.clock(), code),
            )
    finally:
        try:
            await services.leases.release(user, lease_id, "apply_submit_done")
        except (BrowserError, StoreUnavailableError):
            logger.warning("apply.release_failed", extra={"application_id": str(application_id)})
    logger.info(
        "apply.submit_done",
        extra={"application_id": str(application_id), "state": written, "clicked": bool(armed)},
    )
    return written
