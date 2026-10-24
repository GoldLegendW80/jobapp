"""Issue #356 (from the review of PR #346): while the approved version of a CV or a letter is
still rendering, the apply run waits for it instead of filling the older rendered one, and that
wait has a bound (`run.RENDER_WAIT_S`).

On a real Postgres with person A's posting (tests/apply/conftest.py): its CV and letter have a
rendered version 1. A test writes version 2, approves it before its PDF, and moves the document's
render state by hand the way the render job does: `queued`, `rendering`, then `idle` with the PDF
stored, or `idle` with an error code. Each ask is `run.prepare` in its own transaction at a time
the test gives, as the tick's `apply_fill` runs it. No renderer, no browser, no model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import gate, run, service
from app.apply.models import Application, Mode, State
from app.documents import freeze as documents
from app.documents import render_job, store
from app.documents.models import Author, Document, FitResult, Kind, Origin, RenderState
from tests.apply import a1_support
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events, fill, services, started, why_answer
from tests.documents.conftest import load

PDF_2 = b"%PDF-1.7\n" + bytes(reversed(range(256))) + b"\n%%EOF\n"
NAMES = {Kind.cv: run.CV_NAME, Kind.letter: run.LETTER_NAME}
RENDERING = {"needs": "documents:rendering", "attempt": 0}
SECOND = timedelta(seconds=1)


def ask(
    factory: sessionmaker[Session],
    application_id: uuid.UUID,
    now: datetime,
    *,
    manual: bool = False,
) -> object:
    """One ask of the tick: steps 1 and 2 of the fill in their own transaction."""
    with factory() as session:
        out = run.prepare(session, application_id, now, manual=manual)
        session.commit()
    return out


def document_of(db: Session, person: Person, kind: Kind) -> Document:
    (found,) = [
        d for d in store.posting_documents(db, person.user_id, person.posting_id) if d.kind is kind
    ]
    return found


def set_render(
    db: Session, document_id: uuid.UUID, state: RenderState, error: str | None = None
) -> None:
    db.execute(
        sa.update(Document)
        .where(Document.id == document_id)
        .values(render_state=state, render_error=error)
    )
    db.commit()
    db.expire_all()


def second_version(
    db: Session,
    person: Person,
    kind: Kind,
    state: RenderState = RenderState.rendering,
    *,
    approve: bool = True,
) -> uuid.UUID:
    """Version 2 of the posting's `kind`, without its PDF, approved when `approve` (the person
    approved it before the render landed), the document's render `state`. Its document's id."""
    document = document_of(db, person, kind)
    draft = {**document.draft, "headline": "Second headline"}
    store.save_draft(
        db, person.user_id, document.id, draft, document.draft_revision, Author.user, at=AT
    )
    made, _ = store.snapshot(db, person.user_id, document.id, Origin.edited, at=AT)
    assert made.number == 2
    if approve:
        store.approve(db, person.user_id, document.id, made.number, AT)
    db.commit()
    set_render(db, document.id, state)
    return document.id


def land(db: Session, person: Person, document_id: uuid.UUID) -> None:
    """The render of version 2 stores its PDF and the document goes back to idle."""
    store.attach_pdf(
        db, person.user_id, document_id, 2, PDF_2, fit=FitResult.one_page, fit_step=0, pages=1
    )
    db.commit()
    set_render(db, document_id, RenderState.idle)


def bare_cv_application(
    db: Session, person: Person, url: str, state: RenderState
) -> tuple[uuid.UUID, uuid.UUID]:
    """Another posting of the person whose CV has one version, approved before its first PDF,
    and no letter. The application started on it and the CV's document id."""
    posting = db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url, location, posting_text) "
            "VALUES (:u, :s, 'Example Logistics', 'Robotics Software Engineer', :url, "
            "'Paris, France', 'Example Logistics moves parcels with robots.') RETURNING id"
        ),
        {"u": person.user_id, "s": f"example-logistics-{uuid.uuid4().hex[:8]}", "url": url},
    ).scalar_one()
    document = store.get_or_create(db, person.user_id, posting, Kind.cv, at=AT)
    store.save_draft(
        db,
        person.user_id,
        document.id,
        load("written/cv_typical.json"),
        document.draft_revision,
        Author.user,
        at=AT,
    )
    made, _ = store.snapshot(db, person.user_id, document.id, Origin.edited, at=AT)
    store.approve(db, person.user_id, document.id, made.number, AT)
    db.commit()
    set_render(db, document.id, state)
    started_on = service.start(db, person.user_id, posting, Mode.review, False, now=AT)
    return started_on.application.id, document.id


def waits(db: Session, application_id: uuid.UUID) -> list[tuple[dict[str, object], object]]:
    return [(e.data, e.at) for e in events(db, application_id) if e.kind == "waiting"]


def ended(db: Session, application_id: uuid.UUID) -> Application:
    found = application(db, application_id)
    assert found.state is State.failed
    assert found.error_code == "documents_rendering"
    assert found.document_versions == {}
    (last,) = [e for e in events(db, application_id) if e.kind == "ended"]
    assert last.data == {"code": "documents_rendering"}
    return found


@pytest.fixture
def bound() -> timedelta:
    return timedelta(seconds=run.RENDER_WAIT_S)


@pytest.fixture
def other_url(forms_base_url: str) -> str:
    return forms_base_url + "/ashby/example-logistics/robotics-software-engineer/application"


# --- the documents' side: `Slot.awaits_render` ---------------------------------------------------


def test_current_says_when_the_approved_version_is_still_rendering(
    db: Session, person_a: Person
) -> None:
    document_id = second_version(db, person_a, Kind.cv, RenderState.queued)
    now = documents.current(db, person_a.user_id, person_a.posting_id)
    assert now.cv.version is not None
    assert (now.cv.version.number, now.cv.coming, now.cv.awaits_render) == (
        1,
        documents.RENDERING,
        True,
    )
    assert now.letter.awaits_render is False
    set_render(db, document_id, RenderState.idle, "render_failed")
    after = documents.current(db, person_a.user_id, person_a.posting_id)
    assert after.cv.awaits_render is False
    assert after.cv.version is not None
    assert after.cv.version.number == 1


# --- the older version is never sent while the approved one renders ------------------------------


@pytest.mark.parametrize("kind", [Kind.cv, Kind.letter])
@pytest.mark.parametrize("state", [RenderState.queued, RenderState.rendering])
def test_the_run_waits_while_the_approved_version_renders_then_fills_it(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    kind: Kind,
    state: RenderState,
) -> None:
    app_id = started(db, person_a)
    document_id = second_version(db, person_a, kind, state)

    for later in (0, 30):  # the tick asks twice: one event, not two
        assert ask(factory, app_id, AT + timedelta(seconds=later)) == "waiting_documents"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.queued, "documents:rendering")
    assert found.document_versions == {}
    assert waits(db, app_id) == [({**RENDERING, "document": kind.value}, AT)]

    land(db, person_a, document_id)
    plan = ask(factory, app_id, AT + timedelta(seconds=60))
    assert isinstance(plan, run.Plan)
    assert plan.pdfs[NAMES[kind]] == PDF_2
    sent = application(db, app_id).document_versions[kind.value]
    assert (sent["number"], sent["approved"]) == (2, True)


@pytest.mark.parametrize("state", list(RenderState))
@pytest.mark.parametrize("approved", [None, 1, 2])
def test_which_version_the_run_takes(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    state: RenderState,
    approved: int | None,
) -> None:
    """The rule as a table: version 1 rendered, version 2 without its PDF, the CV's render state
    and which version is approved. Only an approved version 2 with a render queued or running
    makes the run wait; every other row sends version 1, the one rendered."""
    app_id = started(db, person_a)
    document_id = second_version(db, person_a, Kind.cv, state, approve=approved == 2)
    if approved == 1:
        store.approve(db, person_a.user_id, document_id, 1, AT)
        db.commit()
    out = ask(factory, app_id, AT)
    if approved == 2 and state is not RenderState.idle:
        assert out == "waiting_documents"
        return
    assert isinstance(out, run.Plan)
    assert out.pdfs[run.CV_NAME] == a1_support.PDF
    sent = application(db, app_id).document_versions["cv"]
    assert (sent["number"], sent["approved"]) == (1, approved == 1)


def test_the_fill_never_places_the_older_version_while_the_approved_one_renders(
    db: Session, factory: sessionmaker[Session], person_a: Person, forms_base_url: str
) -> None:
    app_id = started(db, person_a)
    document_id = second_version(db, person_a, Kind.cv)
    leases, form = FakeLeases(), run_ashby()
    svc = services(factory, form, leases, FakeAgent(why_answer), stub_base=forms_base_url)
    assert fill(svc, app_id) == "waiting_documents"
    assert (leases.acquired, form.placed) == ([], {})

    land(db, person_a, document_id)
    assert fill(svc, app_id) == "ready"
    assert form.placed[run.CV_NAME] == PDF_2
    assert application(db, app_id).document_versions["cv"]["number"] == 2


def test_the_gate_refuses_a_fill_older_than_an_approved_version_still_rendering(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    """The person approves a new version after the fill and approves the application while it
    renders: the gate must not let version 1 go. Once that render failed, version 1 is the
    documents' choice again and the gate lets it go."""
    app_id = started(db, person_a)
    assert isinstance(ask(factory, app_id, AT), run.Plan)
    document_id = second_version(db, person_a, Kind.cv)
    assert gate.documents_code(db, application(db, app_id)) == "documents_changed"
    set_render(db, document_id, RenderState.idle, "render_failed")
    assert gate.documents_code(db, application(db, app_id)) is None


# --- a render that fails ends the wait ----------------------------------------------------------


@pytest.mark.parametrize("error", render_job.ERRORS)
def test_a_render_that_fails_lets_the_run_send_the_latest_rendered_version(
    db: Session, factory: sessionmaker[Session], person_a: Person, error: str
) -> None:
    """The render job puts a failed render back to `idle` with its code: the approved version
    has no PDF and none is coming, so `documents.current` answers the latest rendered one."""
    app_id = started(db, person_a)
    document_id = second_version(db, person_a, Kind.cv)
    assert ask(factory, app_id, AT) == "waiting_documents"
    set_render(db, document_id, RenderState.idle, error)
    plan = ask(factory, app_id, AT + timedelta(seconds=30))
    assert isinstance(plan, run.Plan)
    assert plan.pdfs[run.CV_NAME] == a1_support.PDF
    sent = application(db, app_id).document_versions["cv"]
    assert (sent["number"], sent["approved"]) == (1, False)


def test_a_failed_first_render_of_the_only_cv_is_no_documents(
    db: Session, factory: sessionmaker[Session], person_a: Person, other_url: str
) -> None:
    app_id, document_id = bare_cv_application(db, person_a, other_url, RenderState.rendering)
    assert ask(factory, app_id, AT) == "waiting_documents"
    set_render(db, document_id, RenderState.idle, "render_failed")
    assert ask(factory, app_id, AT + timedelta(seconds=30)) == "blocked"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, "no_documents")


# --- a render that never ends: the bound ---------------------------------------------------------


def test_the_bound_outlasts_two_render_jobs_at_their_ceiling() -> None:
    assert run.RENDER_WAIT_S >= 2 * render_job.JOB_TIMEOUT_S


@pytest.mark.parametrize("state", [RenderState.queued, RenderState.rendering])
@pytest.mark.parametrize("mode", [Mode.review, Mode.manual])
def test_an_approved_render_that_never_ends_fails_the_application_at_the_bound(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    state: RenderState,
    mode: Mode,
    bound: timedelta,
) -> None:
    app_id = started(db, person_a)
    db.execute(sa.update(Application).where(Application.id == app_id).values(mode=mode))
    second_version(db, person_a, Kind.cv, state)
    is_manual = mode is Mode.manual
    assert ask(factory, app_id, AT, manual=is_manual) == "waiting_documents"
    assert ask(factory, app_id, AT + bound - SECOND, manual=is_manual) == "waiting_documents"
    assert application(db, app_id).state is State.queued
    assert waits(db, app_id) == [({**RENDERING, "document": "cv"}, AT)]

    assert ask(factory, app_id, AT + bound, manual=is_manual) == "failed"
    ended(db, app_id)
    # Ended: the tick asks no more, nothing was chosen, nothing was placed.
    assert ask(factory, app_id, AT + bound + SECOND, manual=is_manual) == "not_queued"
    assert app_id not in run.due(db)


def test_needs_changes_do_not_refresh_the_same_render_wait(
    db: Session, factory: sessionmaker[Session], person_a: Person, bound: timedelta
) -> None:
    app_id = started(db, person_a)
    second_version(db, person_a, Kind.cv)
    assert ask(factory, app_id, AT) == "waiting_documents"
    db.execute(sa.update(Application).where(Application.id == app_id).values(needs="retry"))
    db.commit()
    assert ask(factory, app_id, AT + SECOND) == "waiting_documents"
    assert ask(factory, app_id, AT + bound) == "failed"
    assert waits(db, app_id) == [({**RENDERING, "document": "cv"}, AT)]
    ended(db, app_id)
    # Ended: the tick asks no more, nothing was chosen, nothing was placed.
    assert ask(factory, app_id, AT + bound + SECOND) == "not_queued"
    assert app_id not in run.due(db)


def test_a_first_render_that_never_ends_fails_the_application_at_the_bound(
    db: Session, factory: sessionmaker[Session], person_a: Person, other_url: str, bound: timedelta
) -> None:
    app_id, _ = bare_cv_application(db, person_a, other_url, RenderState.queued)
    assert ask(factory, app_id, AT) == "waiting_documents"
    assert ask(factory, app_id, AT + bound - SECOND) == "waiting_documents"
    assert ask(factory, app_id, AT + bound) == "failed"
    ended(db, app_id)


def test_each_render_waited_for_has_its_own_bound(
    db: Session, factory: sessionmaker[Session], person_a: Person, bound: timedelta
) -> None:
    """The CV's approved version renders for 50 minutes, then the letter's: the letter's wait
    counts from its own start, not from the CV's."""
    app_id = started(db, person_a)
    cv = second_version(db, person_a, Kind.cv)
    assert ask(factory, app_id, AT) == "waiting_documents"
    later = AT + timedelta(minutes=50)
    land(db, person_a, cv)
    second_version(db, person_a, Kind.letter)
    assert ask(factory, app_id, later) == "waiting_documents"
    assert ask(factory, app_id, later + bound - SECOND) == "waiting_documents"
    assert waits(db, app_id) == [
        ({**RENDERING, "document": "cv"}, AT),
        ({**RENDERING, "document": "letter"}, later),
    ]
    assert ask(factory, app_id, later + bound) == "failed"
    ended(db, app_id)


def test_a_genuinely_new_attempt_starts_a_new_bound(
    db: Session, factory: sessionmaker[Session], person_a: Person, bound: timedelta
) -> None:
    """Only an attempt actually begun, not a changed status or lease restart, gets a new hour."""
    app_id = started(db, person_a)
    second_version(db, person_a, Kind.cv)
    assert ask(factory, app_id, AT) == "waiting_documents"
    db.execute(
        sa.update(Application).where(Application.id == app_id).values(needs="retry", attempt=1)
    )
    db.commit()
    again = AT + 2 * bound
    assert ask(factory, app_id, again) == "waiting_documents"
    assert waits(db, app_id) == [
        ({**RENDERING, "document": "cv"}, AT),
        ({**RENDERING, "document": "cv", "attempt": 1}, again),
    ]
    assert ask(factory, app_id, again + bound - SECOND) == "waiting_documents"
    assert ask(factory, app_id, again + bound) == "failed"
    ended(db, app_id)
