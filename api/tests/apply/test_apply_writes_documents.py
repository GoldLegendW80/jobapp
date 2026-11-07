"""Apply on a posting without documents writes them, then sends (owner, product audit chapter 2,
answer 5 of 2026-11-05), on a real Postgres with A5's fill on the modelled Ashby form and A6's
fake submit form: the click asks the CV and the letter as a Like does (`likes.on_apply`), the
application waits for them, then the gate sends it with the click as the approval; without the
documents consent nothing is written; a writer that fails ends it with a plain code and nothing
sent; with the review box ticked it waits for the person's Approve once filled. The writers are
played by storing the versions they would make: no model, no browser, no company.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import likes, run
from app.apply.caps_models import ApplySettings
from app.apply.models import Mode, State
from app.documents import generate, store
from app.documents.models import (
    Author,
    DocumentPanel,
    FitResult,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Origin,
    PanelStatus,
)
from tests.apply import a1_support
from tests.apply.conftest import AT, Person
from tests.apply.test_738_one_click import HOUR, answered, authorisations, job
from tests.apply.test_a5_run import application, events
from tests.apply.test_a6_gate import _password, stub
from tests.apply.test_a12_likes import api_app, filled, form, generations, posting
from tests.documents.conftest import load
from tests.identity.api_helpers import Api

__all__ = ["api_app", "stub"]


def write(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, kind: Kind) -> None:
    """What the writer of `kind` leaves once done: a rendered one page version, and its
    generation done. Commits."""
    document = store.get_or_create(db, user_id, posting_id, kind, at=AT)
    content = load(f"written/{kind.value}_typical.json")
    store.save_draft(db, user_id, document.id, content, document.draft_revision, Author.user, at=AT)
    made, _ = store.snapshot(db, user_id, document.id, Origin.edited, at=AT)
    store.attach_pdf(
        db,
        user_id,
        document.id,
        made.number,
        a1_support.PDF,
        fit=FitResult.one_page,
        fit_step=0,
        pages=1,
    )
    open_one = store.open_generation(db, user_id, document.id)
    if open_one is not None:
        open_one.status, open_one.wait_reason = GenerationStatus.done, None
        open_one.started_at = open_one.finished_at = AT + timedelta(minutes=2)
    db.commit()


def fail(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, kind: Kind) -> None:
    """The writer of `kind` failed (the provider down): its generation ends without a version."""
    document = store.get_or_create(db, user_id, posting_id, kind, at=AT)
    generation = store.open_generation(db, user_id, document.id)
    if generation is None:
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            None,
            {},
            1000,
            at=AT,
        )
    generation.status, generation.wait_reason = GenerationStatus.failed, None
    generation.finished_at = AT + timedelta(minutes=3)
    db.commit()


def apply_click(db: Session, person: Person, posting_id: uuid.UUID, mode: Mode = Mode.direct):
    try:
        return likes.on_apply(db, person.user_id, posting_id, mode, False, now=AT)
    except Exception:
        db.rollback()
        raise


def agent_runs(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.scalar(sa.text("SELECT count(*) FROM agent_runs WHERE user_id = :u"), {"u": user_id})
    )


def test_the_apply_button_asks_the_missing_documents_and_waits_for_the_consent(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, api_app: FastAPI
) -> None:
    _password(factory, person_a)
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        started = api.post(
            f"/api/postings/{job_id}/applications", token=token, json={"mode": "apply"}
        )
        manual = posting(db, person_a.user_id, form(stub), kinds=())
        myself = api.post(
            f"/api/postings/{manual}/applications", token=token, json={"mode": "manual"}
        )
    assert (started.status_code, started.json()["mode"]) == (201, "direct")
    app_id = uuid.UUID(started.json()["id"])
    [(kind, generation)] = generations(db, job_id)
    assert (kind, generation.trigger) == (Kind.cv, GenerationTrigger.click)
    assert generation.estimate[generate.LETTER_FOLLOWS] is True
    # No documents consent yet: the writing waits for it, exactly as "Write now" and a Like do.
    assert (generation.status, generation.wait_reason) == (
        GenerationStatus.waiting,
        GenerationWait.consent,
    )
    assert agent_runs(db, person_a.user_id) == 0  # nothing written, nothing spent
    asked = [e.data for e in events(db, app_id) if e.kind == "documents_asked"]
    assert asked == [{"kinds": ["cv", "letter"]}]
    assert run.prepare(db, app_id, AT) == "waiting_documents"
    db.commit()
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.queued, "documents:consent")
    # Apply myself asks nothing: the person fills the form himself.
    assert myself.status_code == 201
    assert generations(db, manual) == []


def test_written_then_sent_with_the_click_as_the_approval(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id).application.id
    assert run.prepare(db, app_id, AT) == "waiting_documents"
    db.commit()
    write(db, person_a.user_id, job_id, Kind.cv)
    # The CV is there, its letter not yet begun: the application keeps waiting for it.
    assert run.prepare(db, app_id, AT + timedelta(minutes=3)) == "waiting_documents"
    db.commit()
    assert application(db, app_id).needs == "documents:writing"
    write(db, person_a.user_id, job_id, Kind.letter)

    assert filled(factory, app_id, stub) == "ready"
    answered(db, app_id)
    assert set(application(db, app_id).document_versions) == {"cv", "letter"}
    written, sent = job(factory, stub, app_id, AT + HOUR)
    assert (written, sent.gate_clicks) == ("approved", [])
    [authorisation] = authorisations(db, app_id)
    assert authorisation.kind.value == "apply_click"
    written, sent = job(factory, stub, app_id, AT + HOUR)
    assert written == "submitted"
    assert [click["what"] for click in sent.gate_clicks] == ["submit"]


def test_a_cv_writer_that_fails_ends_it_with_nothing_sent(
    db: Session, person_a: Person, stub: str
) -> None:
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id).application.id
    fail(db, person_a.user_id, job_id, Kind.cv)
    assert run.prepare(db, app_id, AT + HOUR) == "blocked"
    db.commit()
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, run.NOT_WRITTEN)
    assert found.document_versions in (None, {})
    assert authorisations(db, app_id) == []


def test_a_letter_writer_that_fails_sends_nothing_either(
    db: Session, person_a: Person, stub: str
) -> None:
    job_id = posting(db, person_a.user_id, form(stub), kinds=(Kind.cv,))
    started = apply_click(db, person_a, job_id)
    assert [e.data for e in events(db, started.application.id) if e.kind == "documents_asked"] == [
        {"kinds": ["letter"]}
    ]
    fail(db, person_a.user_id, job_id, Kind.letter)
    assert run.prepare(db, started.application.id, AT + HOUR) == "blocked"
    db.commit()
    found = application(db, started.application.id)
    assert (found.state, found.error_code) == (State.blocked, run.NOT_WRITTEN)


def test_a_letter_that_never_begins_after_its_cv_is_not_waited_for_good(
    db: Session, person_a: Person, stub: str
) -> None:
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id).application.id
    write(db, person_a.user_id, job_id, Kind.cv)  # its run ended at AT + 2 minutes
    late = AT + timedelta(minutes=2) + run.LETTER_START_GRACE
    assert run.prepare(db, app_id, late - timedelta(seconds=1)) == "waiting_documents"
    db.commit()
    assert run.prepare(db, app_id, late) == "blocked"
    db.commit()
    assert application(db, app_id).error_code == run.NOT_WRITTEN


def _cv_panel(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    *,
    started: bool,
    opened: datetime | None = None,
) -> uuid.UUID:
    """The hiring panel the CV's write opened (documents D29) at AT + 2 minutes: running, or
    waiting its turn behind another panel of the person (`started_at` None); `opened` moves its
    start. Commits."""
    opened = opened or AT + timedelta(minutes=2)
    cv = next(d for d in store.posting_documents(db, user_id, posting_id) if d.kind == Kind.cv)
    made = DocumentPanel(
        id=uuid.uuid4(),
        user_id=user_id,
        document_id=cv.id,
        kind="cv",
        status=PanelStatus.running.value,
        reviewed_version=1,
        created_at=opened,
        started_at=opened if started else None,
    )
    db.add(made)
    db.commit()
    return made.id


def _end_panel(db: Session, panel_id: uuid.UUID, at: datetime) -> None:
    db.execute(
        sa.update(DocumentPanel)
        .where(DocumentPanel.id == panel_id)
        .values(status=PanelStatus.done.value, finished_at=at)
    )
    db.commit()


def test_a_bulk_apply_waits_for_panels_run_one_after_the_other_and_none_is_blocked(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """Three Apply clicks at once: the three CVs are written, then their hiring panels run one
    after the other (one per person at a time), 20 minutes each, and each letter follows its
    CV's panel. No application ends `documents_not_written` while its panel waits its turn,
    the letter's grace counts from its panel's end, and each one fills once its letter is
    there."""
    titles = ("Applied AI Engineer", "ML Engineer", "Data Engineer")
    jobs = [posting(db, person_a.user_id, form(stub), title=t, kinds=()) for t in titles]
    apps = [apply_click(db, person_a, job_id).application.id for job_id in jobs]
    for job_id in jobs:
        write(db, person_a.user_id, job_id, Kind.cv)  # its run ended at AT + 2 minutes
    panels = [
        _cv_panel(db, person_a.user_id, job_id, started=number == 0)
        for number, job_id in enumerate(jobs)
    ]
    ends = [AT + timedelta(minutes=2 + 20 * (number + 1)) for number in range(3)]

    def waiting(at: datetime, among: range) -> None:
        for number in among:
            assert run.prepare(db, apps[number], at) == "waiting_documents", (number, at)
            db.commit()
            assert application(db, apps[number]).state is State.queued

    # Past the CV's grace, the three wait: one panel runs, two wait their turn.
    waiting(AT + timedelta(minutes=2) + run.LETTER_START_GRACE + timedelta(minutes=1), range(3))
    for number in range(3):
        _end_panel(db, panels[number], ends[number])
        if number + 1 < 3:  # the next panel takes the slot
            db.execute(
                sa.update(DocumentPanel)
                .where(DocumentPanel.id == panels[number + 1])
                .values(started_at=ends[number])
            )
            db.commit()
        # The letter has not begun yet: within its grace from the panel's end, it waits.
        waiting(ends[number] + run.LETTER_START_GRACE - timedelta(seconds=1), range(number, 3))
        write(db, person_a.user_id, jobs[number], Kind.letter)
        assert filled(factory, apps[number], stub) == "ready"
        assert set(application(db, apps[number]).document_versions) == {"cv", "letter"}
    for app_id in apps:
        assert application(db, app_id).error_code != run.NOT_WRITTEN


def test_a_letter_that_never_begins_after_its_cvs_panel_is_not_waited_for_good(
    db: Session, person_a: Person, stub: str
) -> None:
    """The bound: the panel ended (or the tick closed it), and no letter began within the grace
    from that end."""
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id).application.id
    write(db, person_a.user_id, job_id, Kind.cv)
    panel_id = _cv_panel(db, person_a.user_id, job_id, started=True)
    ended = AT + timedelta(minutes=40)
    _end_panel(db, panel_id, ended)
    assert run.prepare(db, app_id, ended + run.LETTER_START_GRACE - timedelta(seconds=1)) == (
        "waiting_documents"
    )
    db.commit()
    assert run.prepare(db, app_id, ended + run.LETTER_START_GRACE) == "blocked"
    db.commit()
    assert application(db, app_id).error_code == run.NOT_WRITTEN


def test_documents_already_there_ask_nothing_and_a_like_keeps_its_code(
    db: Session, person_a: Person, stub: str
) -> None:
    job_id = posting(db, person_a.user_id, form(stub))
    app_id = apply_click(db, person_a, job_id).application.id
    assert generations(db, job_id) == []
    assert "documents_asked" not in [e.kind for e in events(db, app_id)]
    # A Like whose documents never come keeps `no_documents` (likes.on_like is unchanged).
    liked_job = posting(db, person_a.user_id, form(stub), title="ML Engineer", kinds=())
    liked = likes.on_like(db, person_a.user_id, liked_job, now=AT)
    fail(db, person_a.user_id, liked_job, Kind.cv)
    assert run.prepare(db, liked.application.id, AT + HOUR) == "blocked"
    db.commit()
    assert application(db, liked.application.id).error_code == "no_documents"


def test_review_first_goes_to_review_once_written_never_straight_to_submit(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    db.add(ApplySettings(user_id=person_a.user_id, updated_at=AT - HOUR, review_before_send=True))
    db.commit()
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id, Mode.review).application.id
    write(db, person_a.user_id, job_id, Kind.cv)
    write(db, person_a.user_id, job_id, Kind.letter)
    assert filled(factory, app_id, stub) == "ready"
    _, sent = job(factory, stub, app_id, AT + HOUR)
    assert sent.gate_clicks == []
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.ready, Mode.review)
    assert authorisations(db, app_id) == []


def test_a_panel_opened_before_the_apply_click_and_still_running_is_waited_for(
    db: Session, person_a: Person, stub: str
) -> None:
    """A Write click on the CV opened its panel before the Apply click: the letter still follows
    its end, so the wait holds past the CV's grace, then counts from the panel's end."""
    job_id = posting(db, person_a.user_id, form(stub), kinds=())
    app_id = apply_click(db, person_a, job_id).application.id
    write(db, person_a.user_id, job_id, Kind.cv)
    panel_id = _cv_panel(db, person_a.user_id, job_id, started=True, opened=AT - timedelta(hours=1))
    late = AT + timedelta(minutes=2) + run.LETTER_START_GRACE + timedelta(hours=1)
    assert run.prepare(db, app_id, late) == "waiting_documents"
    db.commit()
    ended = late + timedelta(minutes=5)
    _end_panel(db, panel_id, ended)
    assert run.prepare(db, app_id, ended + run.LETTER_START_GRACE - timedelta(seconds=1)) == (
        "waiting_documents"
    )
    db.commit()
    assert run.prepare(db, app_id, ended + run.LETTER_START_GRACE) == "blocked"
    db.commit()
    assert application(db, app_id).error_code == run.NOT_WRITTEN
