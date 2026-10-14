"""The guards of A5's run (review of PR #312): what the answer agent is never asked, an
application that never stays `filling`, a step for the person seen at any point of the fill, and
people taking turns on the one-at-a-time queue. Real Postgres, the fakes of a5_support."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.apply import agent, run, terms
from app.apply.ats import base as ats_base
from app.apply.models import Application, Mode, State
from app.jobs import service as jobs_service
from app.jobs.models import JobStatus
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import (
    WHY,
    Clock,
    application,
    events,
    fill,
    services,
    started,
    status,
    why_answer,
)


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


# --- what the agent is never asked -----------------------------------------------------------
# The owner's rules: an answer never invents a claim; legal and consent questions, pay, identity
# and credentials stay with the person. `None`: the question goes to the agent.

YN = ("Yes", "No")
ON = ("Oui", "Non")
HUGE = "Why Example Robotics? " + "Tell us more about the robots you would build. " * 400


@pytest.mark.parametrize(
    ("label", "kind", "options", "code"),
    [
        # the reviewer's probe, every one asked and accepted before
        ("Do you authorize us to run a background check?", "text", (), "legal"),
        ("Do you agree to the arbitration agreement?", "select", YN, "legal"),
        ("Do you agree to binding arbitration?", "text", (), "legal"),
        ("I would like to receive marketing emails", "select", YN, "legal"),
        ("Would you like to join our talent community?", "select", YN, "legal"),
        ("Can we keep your data for future opportunities?", "text", (), "legal"),
        ("Do you agree to our terms and conditions?", "text", (), "legal"),
        ("I certify that the information provided is true", "text", (), "legal"),
        ("Are you subject to a non-compete agreement?", "text", (), "legal"),
        ("What's your expected OTE?", "text", (), "pay"),
        ("Expected annual package", "text", (), "pay"),
        ("Password", "text", (), "identity"),
        ("Social security number", "text", (), "identity"),
        ("What is your home address?", "text", (), "identity"),
        ("Please type your full legal name as an electronic signature", "text", (), "legal"),
        ("Do you have a PhD?", "text", (), "unproven"),
        ("Do you hold a security clearance?", "select", YN, "unproven"),
        ("Do you have experience fine-tuning LLMs?", "text", (), "unproven"),
        ("Are you a US person under export control regulations?", "text", (), "unproven"),
        # French forms
        ("Acceptez-vous l'arbitrage en cas de litige ?", "text", (), "legal"),
        ("J'accepte les conditions générales", "text", (), "legal"),
        ("Autorisez-vous une vérification de vos antécédents ?", "text", (), "legal"),
        ("Souhaitez-vous rejoindre notre vivier de talents ?", "select", ON, "legal"),
        ("Pouvons-nous conserver votre CV ?", "text", (), "legal"),
        ("Rémunération annuelle brute souhaitée", "text", (), "pay"),
        ("Prétentions salariales", "text", (), "pay"),
        ("Mot de passe", "text", (), "identity"),
        ("Numéro de sécurité sociale", "text", (), "identity"),
        ("Adresse postale", "text", (), "identity"),
        ("Nom et prénom", "text", (), "identity"),
        ("Êtes-vous titulaire d'un doctorat ?", "text", (), "unproven"),
        ("Disposez-vous d'une habilitation secret défense ?", "text", (), "unproven"),
        ("Signature électronique", "text", (), "legal"),
        # neighbours: case, punctuation, spacing, other wordings
        ("PASSWORD:", "text", (), "identity"),
        ("Confirm  pass word", "text", (), "identity"),
        ("Hourly rate (USD)", "text", (), "pay"),
        ("Bonus expectations", "text", (), "pay"),
        ("Equity expectations?", "text", (), "pay"),
        ("On-target earnings", "text", (), "pay"),
        ("Phone number", "text", (), "identity"),
        ("E-mail address", "text", (), "identity"),
        ("Date of birth", "text", (), "eeo"),
        ("National Insurance number", "text", (), "identity"),
        ("Tax ID", "text", (), "identity"),
        ("I acknowledge the privacy notice", "text", (), "legal"),
        ("Do you consent to us sharing your data with partners?", "text", (), "legal"),
        ("Do you enjoy puzzles?", "yes_no", (), "yes_or_no"),
        ("Kubernetes in production", "select", YN, "yes_or_no"),
        ("Kubernetes in production", "select", ("Yes, daily", "Not yet"), "yes_or_no"),
        ("Have you used Kubernetes in production?", "text", (), "yes_or_no"),
        ("Please confirm whether you can travel", "text", (), "yes_or_no"),
        ("Est-ce que vous parlez allemand ?", "text", (), "yes_or_no"),
        ("Haben Sie einen Führerschein?", "text", (), "yes_or_no"),
        ("Haben Sie Erfahrung mit Kubernetes?", "text", (), "yes_or_no"),
        ("¿Tiene experiencia con Kubernetes?", "text", (), "yes_or_no"),
        ("AWS certification", "text", (), "unproven"),
        ("Team preference", "combobox", (), "no_options"),
        ("Portfolio", "file", (), "kind"),
        ("", "text", (), "no_label"),
        ("   ", "text", (), "no_label"),
        # true free text, still asked
        ("Why do you want to work here?", "text", (), None),
        ("Why Example Robotics?", "text", (), None),
        ("WHY EXAMPLE ROBOTICS", "text", (), None),
        ("What is your favourite robot?", "text", (), None),
        ("Which team interests you?", "select", ("Ops", "Research"), None),
        ("Team preference", "combobox", ("Ops", "Research"), None),
        ("Tell us about a project", "text", (), None),
        ("Tell us about a project you are proud of", "text", (), None),
        ("What excites you about this role?", "text", (), None),
        ("Pourquoi souhaitez-vous rejoindre Example Robotics ?", "text", (), None),
        ("Décrivez un projet dont vous êtes fier", "text", (), None),
        ("Qu'est-ce qui vous motive dans ce poste ?", "text", (), None),
        (HUGE, "text", (), None),
    ],
)
def test_what_the_agent_is_never_asked(
    label: str, kind: str, options: tuple[str, ...], code: str | None
) -> None:
    assert agent.refusal(label, kind, options) == code
    item = ats_base.Field(key="k", label=label, kind=kind, options=options)  # type: ignore[arg-type]
    for reason in agent.AGENT_REASONS:
        outcome = ats_base.Outcome(item, run.Left(reason, options), ats_base.Filled("left", reason))
        assert bool(agent.eligible([outcome])) is (code is None)


def test_the_judge_refuses_a_question_that_reaches_it_by_another_path() -> None:
    asked = agent.Question("p", "Password", "text")
    reply = agent.Reply((agent.Proposal("p", "hunter2", "facts"),))
    (verdict,) = agent.judge([asked], reply, agent.sources_of({}, "Engineer", "Example Robotics"))
    assert (verdict.answer, verdict.code) == (None, "identity")


@pytest.mark.parametrize(
    ("label", "legal"),
    [
        ("I agree to arbitration", True),
        ("I have read the privacy notice", True),
        ("I confirm the information is accurate", True),
        ("Join our talent community", True),
        ("Why Example Robotics?", False),
        ("Tell us about a project", False),
        ("", False),
    ],
)
def test_the_legal_reading_of_terms(label: str, legal: bool) -> None:
    assert terms.legal_reading(label) is legal


def test_a_field_reported_twice_is_asked_once() -> None:
    item = ats_base.Field(key="why", label=WHY, kind="text")
    left = ats_base.Outcome(
        item, run.Left("free_text_for_the_agent", ()), ats_base.Filled("left", "x")
    )
    assert [q.key for q in agent.eligible([left, left])] == ["why"]


# --- never left `filling` ----------------------------------------------------------------------


def test_a_fill_cancelled_mid_fill_ends_interrupted_and_releases_the_lease_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form, leases = run_ashby(), FakeLeases()

    def stop(_: Any, n: int) -> None:
        if n == 6:
            raise asyncio.CancelledError  # docker stop, or the job's timeout

    form.on_call = stop
    with pytest.raises(asyncio.CancelledError):
        fill(services(factory, form, leases, stub_base=stub), app_id)
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "interrupted")
    assert [e.data for e in events(db, app_id) if e.kind == "ended"] == [{"code": "interrupted"}]
    assert len(leases.released) == 1
    assert status(db, person_a) is JobStatus.found


def test_a_fill_whose_end_cannot_be_written_ends_failed(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = started(db, person_a)
    leases = FakeLeases()

    def busy(*_: Any) -> str:
        raise jobs_service.JobBusyError  # the person holds the posting's row

    monkeypatch.setattr(run, "finish", busy)
    assert fill(services(factory, run_ashby(), leases, stub_base=stub), app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "interrupted")
    assert len(leases.released) == 1


def test_an_application_no_longer_filling_is_not_interrupted(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a)
    with factory() as other:
        assert run.interrupt(other, app_id, AT) == "superseded"
        other.commit()
    assert application(db, app_id).state is State.queued


def backlog(
    db: Session, person: Person, n: int, first: timedelta, state: State = State.queued
) -> list[uuid.UUID]:
    """`n` applications of `person` on postings of their own, created a second apart from
    `AT + first`, oldest first."""
    tag = uuid.uuid4().hex[:8]
    ids = db.execute(
        sa.text(
            "WITH j AS (INSERT INTO jobs (user_id, slug, company, title, url) "
            "SELECT :u, 'backlog-' || :tag || '-' || g, 'Example Robotics', 'Engineer', :url || g "
            "FROM generate_series(1, :n) g RETURNING id, slug) "
            "INSERT INTO applications (user_id, posting_id, mode, state, ats, form_url, "
            "canonical_key, created_at, updated_at) "
            "SELECT :u, j.id, 'review', CAST(:state AS application_state), 'ashby', :url, j.slug, "
            ":at + CAST(split_part(j.slug, '-', 3) AS int) * interval '1 second', :at "
            "FROM j RETURNING id, created_at"
        ),
        {
            "u": person.user_id,
            "tag": tag,
            "url": "https://jobs.ashbyhq.com/example/" + tag,
            "n": n,
            "state": state.value,
            "at": AT + first,
        },
    ).all()
    db.commit()
    return [row.id for row in sorted(ids, key=lambda row: row.created_at)]


def test_the_queue_start_ends_fills_left_by_a_dead_worker_and_nothing_else(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    left = started(db, person_a)
    with factory() as other:
        assert run.begin(other, left, AT)
        other.commit()
    (ready,) = backlog(db, person_a, 1, timedelta(0), State.ready)
    b_id = started(db, person_b)
    ctx = {"sessions": factory, "clock": Clock()}
    assert asyncio.run(worker.reset_fills_at_start(ctx)) == 1
    found = application(db, left)
    assert (found.state, found.error_code) == (State.failed, "interrupted")
    assert application(db, ready).state is State.ready
    assert application(db, b_id).state is State.queued
    assert asyncio.run(worker.reset_fills_at_start(ctx)) == 0


def test_the_fill_timeout_is_the_leases_ceiling_and_the_queue_resets_at_start() -> None:
    default = worker.apply_queue()
    longer = worker.apply_queue(SimpleNamespace(browser_lease_max_s="apply_fill=1200"))  # type: ignore[arg-type]
    for spec, lease_s in ((default, 900), (longer, 1200)):
        (filling,) = [f for f in spec.functions if f.name == "apply_fill"]
        assert filling.timeout_s == spec.job_timeout_s == lease_s + worker.APPLY_JOB_MARGIN_S
        assert spec.on_start is worker.reset_fills_at_start


# --- a step for the person, anywhere --------------------------------------------------------------


def test_a_captcha_at_discovery_needs_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    form.captcha = "instead"
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (State.needs_person, "captcha", None)
    assert form.submit_clicks == 0


def test_a_captcha_on_the_agents_field_needs_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()

    def robot(question: agent.Question) -> tuple[str | None, str]:
        form.captcha = "beside"  # shown while the agent's value is typed
        return why_answer(question)

    assert fill(services(factory, form, answer_agent=FakeAgent(robot), stub_base=stub), app_id) == (
        "needs_person"
    )
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, "captcha")
    assert form.submit_clicks == 0


# --- people take turns -------------------------------------------------------------------------


def _set(factory: sessionmaker[Session], application_id: uuid.UUID, **values: Any) -> None:
    with factory() as other:
        other.execute(sa.update(Application).where(Application.id == application_id).values(values))
        other.commit()


def test_one_persons_backlog_never_keeps_another_out_and_people_alternate(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    a_ids = backlog(db, person_a, 250, timedelta(seconds=-1000))
    b_ids = backlog(db, person_b, 2, timedelta(seconds=10))  # after every one of A's
    with factory() as other:
        assert run.due(other) == [a_ids[0], b_ids[0]]
    _set(factory, a_ids[0], state=State.filling)  # A's fill runs: nothing more of A's
    with factory() as other:
        assert run.due(other) == [b_ids[0]]
    _set(factory, a_ids[0], state=State.ready)
    _set(factory, b_ids[0], state=State.filling)
    with factory() as other:
        assert run.due(other) == [a_ids[1]]
    _set(factory, b_ids[0], state=State.ready)
    with factory() as other:
        assert run.due(other) == [a_ids[1], b_ids[1]]
    # Apply myself is never asked for
    _set(factory, b_ids[1], mode=Mode.manual)
    with factory() as other:
        assert run.due(other) == [a_ids[1]]


def test_a_persons_application_waiting_for_documents_lets_their_next_one_go(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    first, second, third = backlog(db, person_a, 3, timedelta(0))
    _set(factory, first, needs="documents:writing", updated_at=AT + timedelta(seconds=50))
    with factory() as other:
        assert run.due(other) == [second]
    _set(factory, second, state=State.ready)
    _set(factory, third, needs="documents:writing", updated_at=AT + timedelta(seconds=40))
    with factory() as other:  # both wait: the one asked longest ago
        assert run.due(other) == [third]
