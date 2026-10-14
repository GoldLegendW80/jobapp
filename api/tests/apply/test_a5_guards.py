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
# The owner's rules: an answer never invents a claim; legal and consent questions, pay, work
# authorization, criminal and employment history, identity and credentials stay with the person.
# Two lines of defence: a question the resolver does not know is asked only when its label reads
# as an open question or a prompt (`not_open` otherwise), and the vocabularies refuse a topic in
# any wording. `None`: the question goes to the agent.

YN = ("Yes", "No")
ON = ("Oui", "Non")
THREE = ("First", "Second", "Third")
HUGE = "Why Example Robotics? " + "Tell us more about the robots you would build. " * 400

# The reviewer's leaks of round 2 (PR #312): each asked and its "None" kept before. The code is
# the vocabulary's, so dropping a vocabulary turns its rows red even where `not_open` would also
# refuse the label.
LEAKS = [
    # pay
    ("Desired comp", "pay"),
    ("Comp expectations", "pay"),
    ("Total comp", "pay"),
    ("Desired total comp", "pay"),
    ("TC expectations", "pay"),
    ("Your expected TC?", "pay"),
    ("How much do you expect to make?", "pay"),
    ("Budget expectations", "pay"),
    ("Gross annual income expected", "pay"),
    ("Annual income", "pay"),
    ("Hourly", "pay"),
    ("Current base", "pay"),
    ("Revenu annuel", "pay"),
    ("Gehaltsvorstellung", "pay"),
    ("Pretensión salarial", "pay"),
    # work authorization and immigration
    ("Work permit", "authorization"),
    ("Work permit status", "authorization"),
    ("Right to work", "authorization"),
    ("Right to work in the UK", "authorization"),
    ("Green card", "authorization"),
    ("Green card holder?", "authorization"),
    ("Residency status", "authorization"),
    ("Permanent resident?", "authorization"),
    ("Visa", "authorization"),
    ("Visa type", "authorization"),
    ("Sponsor required", "authorization"),
    ("Eligibility to work in Canada", "authorization"),
    ("Titre de séjour", "authorization"),
    ("Permis de travail", "authorization"),
    ("Arbeitserlaubnis", "authorization"),
    # criminal and employment history
    ("Pending charges", "history"),
    ("Arrests", "history"),
    ("Misdemeanors", "history"),
    ("DBS check", "history"),
    ("Fired from a job?", "history"),
]
# The same topics worded as open questions: the shape lets them through, so the vocabulary is
# the only guard (defence in depth).
OPEN_LEAKS = [
    ("What are your comp expectations?", "pay"),
    ("What is your annual income?", "pay"),
    ("What is your daily rate?", "pay"),
    ("Wie viel möchten Sie verdienen?", "pay"),
    ("Welche Gehaltsvorstellung haben Sie?", "pay"),
    ("¿Cuál es su pretensión salarial?", "pay"),
    ("¿Cuál es su sueldo deseado?", "pay"),
    ("Quel est votre revenu annuel ?", "pay"),
    ("What is your work permit status?", "authorization"),
    ("Describe your right to work in the UK", "authorization"),
    ("Which visa do you hold?", "authorization"),
    ("What is your residency status?", "authorization"),
    ("What is your nationality?", "authorization"),
    ("What is your H-1B status?", "authorization"),
    ("How did you get your green card?", "authorization"),
    ("What is your immigration status?", "authorization"),
    ("Explain your OPT or CPT situation", "authorization"),
    ("Quel est votre titre de séjour ?", "authorization"),
    ("Quelle est votre autorisation de travail ?", "authorization"),
    ("Welche Arbeitserlaubnis haben Sie?", "authorization"),
    ("Wie ist Ihr Aufenthaltsstatus?", "authorization"),
    ("¿Cuál es su permiso de trabajo?", "authorization"),
    ("Tell us about any pending charges", "history"),
    ("Why were you fired from your last job?", "history"),
    ("Explain any arrests or convictions", "history"),
    ("Describe any disciplinary action taken against you", "history"),
    ("Why were you dismissed?", "history"),
    ("What offences have you committed?", "history"),
    ("Quelles condamnations figurent à votre casier ?", "history"),
    # the author's own probe of the same families
    ("¿Cuánto quieres ganar?", "pay"),
    ("What are your financial expectations?", "pay"),
    ("What is your right-to-work status?", "authorization"),
    ("Welche Arbeitsgenehmigung haben Sie?", "authorization"),
    ("Welche Staatsangehörigkeit haben Sie?", "authorization"),
    ("Which work pass do you hold?", "authorization"),
    ("What is your EAD status?", "authorization"),
    ("¿Cuál es su nacionalidad?", "authorization"),
    ("¿Cuál es su ciudadanía?", "authorization"),
    ("¿Cuál es su situación de patrocinio?", "authorization"),
    ("Quel est votre besoin de parrainage ?", "authorization"),
    ("Welche Vorstrafen haben Sie?", "history"),
    ("¿Cuáles son sus antecedentes penales?", "history"),
]


@pytest.mark.parametrize(("label", "code"), LEAKS + OPEN_LEAKS)
@pytest.mark.parametrize(("kind", "options"), [("text", ()), ("select", THREE), ("select", YN)])
def test_pay_authorization_and_history_stay_the_persons_in_any_widget(
    label: str, code: str, kind: str, options: tuple[str, ...]
) -> None:
    assert agent.refusal(label, kind, options) == code
    item = ats_base.Field(key="k", label=label, kind=kind, options=options)  # type: ignore[arg-type]
    for reason in agent.AGENT_REASONS:
        outcome = ats_base.Outcome(item, run.Left(reason, options), ats_base.Filled("left", reason))
        assert agent.eligible([outcome]) == []
        asked = agent.Question("k", label, kind, options, reason=reason)
        value = options[0] if options else "None"
        reply = agent.Reply((agent.Proposal("k", value, "answers"),))
        (verdict,) = agent.judge([asked], reply, agent.sources_of({}, "Engineer", "Example"))
        assert (verdict.answer, verdict.code) == (None, code)


@pytest.mark.parametrize(
    ("label", "kind", "options", "code"),
    [
        # the reviewer's probe of round 1, every one asked and accepted before
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
        ("Passport number", "text", (), "authorization"),
        ("Are you a US citizen?", "text", (), "authorization"),
        ("Do you now or in the future require sponsorship?", "text", (), "authorization"),
        ("Have you ever been convicted of a felony?", "text", (), "history"),
        ("I acknowledge the privacy notice", "text", (), "legal"),
        ("Do you consent to us sharing your data with partners?", "text", (), "legal"),
        ("Do you enjoy puzzles?", "yes_no", (), "yes_or_no"),
        ("Kubernetes in production", "select", YN, "yes_or_no"),
        ("Kubernetes in production", "select", ("Yes, daily", "Not yet"), "yes_or_no"),
        (
            "Which describes you best?",
            "select",
            ("Citizen", "Permanent resident", "Other"),
            "option_topic",
        ),
        (
            "Which applies to you?",
            "select",
            ("Need a visa", "Hold a work permit", "Other"),
            "option_topic",
        ),
        (
            "Which range fits you?",
            "select",
            ("Salary under 50k", "Salary over 50k"),
            "option_topic",
        ),
        ("Which applies to you?", "select", ("Clean criminal record", "Other"), "option_topic"),
        ("Have you used Kubernetes in production?", "text", (), "yes_or_no"),
        ("Please confirm whether you can travel", "text", (), "yes_or_no"),
        ("Est-ce que vous parlez allemand ?", "text", (), "yes_or_no"),
        ("Haben Sie einen Führerschein?", "text", (), "yes_or_no"),
        ("Haben Sie Erfahrung mit Kubernetes?", "text", (), "yes_or_no"),
        ("¿Tiene experiencia con Kubernetes?", "text", (), "yes_or_no"),
        ("Is there anything else you would like us to know?", "text", (), "yes_or_no"),
        ("AWS certification", "text", (), "unproven"),
        ("Team preference", "combobox", (), "no_options"),
        ("Portfolio", "file", (), "kind"),
        ("", "text", (), "no_label"),
        ("   ", "text", (), "no_label"),
        # not an open question nor a prompt: a bare noun phrase, a statement, a question whose
        # opener is not on the list. Declared over-refusals: "Team preference" with its options,
        # "Anything else you'd like to share?".
        ("Team preference", "combobox", ("Ops", "Research"), "not_open"),
        ("Team preference", "select", THREE, "not_open"),
        ("Favourite colour", "text", (), "not_open"),
        ("Availability", "text", (), "not_open"),
        ("Comments", "text", (), "not_open"),
        ("Anything else you'd like to share?", "text", (), "not_open"),
        ("Where are you based?", "text", (), "not_open"),
        ("Who referred you?", "text", (), "not_open"),
        ("Kubernetes experience", "select", THREE, "not_open"),
        ("Experience with Kubernetes: why?", "text", (), "not_open"),
        ("Somewhat related: what do you like?", "text", (), "not_open"),
        ("Was motiviert Sie?", "text", (), "yes_or_no"),  # English "was" wins: over-refusal
        ("Motivation for the role", "text", (), "not_open"),
        ("Cover letter text", "text", (), "not_open"),
        ("Whatever you like", "text", (), "not_open"),
        ("Howdy", "text", (), "not_open"),
        ("Commentaires", "text", (), "not_open"),
        # true free text, still asked
        ("Why do you want to work here?", "text", (), None),
        ("Why Example Robotics?", "text", (), None),
        ("WHY EXAMPLE ROBOTICS", "text", (), None),
        ("  * 1. Why Example Robotics?", "text", (), None),
        ("What is your favourite robot?", "text", (), None),
        ("What's your favourite robot?", "text", (), None),
        ("What would you build in your first 90 days?", "text", (), None),
        ("Which team interests you?", "select", ("Ops", "Research"), None),
        ("Which team interests you?", "select", THREE, None),
        ("How do you approach LLM evaluation?", "text", (), None),
        ("Tell us about a project", "text", (), None),
        ("Tell us about a project you are proud of", "text", (), None),
        ("Describe a project you are proud of", "text", (), None),
        ("Describe a project you were in charge of", "text", (), None),
        ("Describe your experience with LLM evaluation", "text", (), None),
        ("Please describe your experience with LLM evaluation", "text", (), None),
        ("Briefly explain how you test agents", "text", (), None),
        ("In a few sentences, tell us why this role", "text", (), None),
        ("Share an example of a hard bug you fixed", "text", (), None),
        ("Walk us through a system you designed", "text", (), None),
        ("Give an example of a trade-off you made", "text", (), None),
        ("What excites you about this role?", "text", (), None),
        ("What makes you a great fit for this role?", "text", (), None),
        ("Motivation", "text", (), None),
        ("Motivation:", "text", (), None),
        ("Additional information", "text", (), None),
        ("Cover letter", "text", (), None),
        ("Pourquoi nous rejoindre ?", "text", (), None),
        ("Pourquoi souhaitez-vous rejoindre Example Robotics ?", "text", (), None),
        ("Décrivez un projet dont vous êtes fier", "text", (), None),
        ("Qu'est-ce qui vous motive dans ce poste ?", "text", (), None),
        ("Parlez-nous d'un projet", "text", (), None),
        ("Quelles technologies préférez-vous ?", "text", (), None),
        ("Comment évaluez-vous un agent ?", "text", (), None),
        ("Expliquez votre approche des tests", "text", (), None),
        ("Partagez un exemple de projet", "text", (), None),
        ("Lettre de motivation", "text", (), None),
        ("Warum möchten Sie bei uns arbeiten?", "text", (), None),
        ("Beschreiben Sie ein Projekt", "text", (), None),
        ("¿Por qué quieres trabajar aquí?", "text", (), None),
        ("Describa un proyecto", "text", (), None),
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
        # A3 wrote a free text question for this company: its label needs no opener.
        expected = None if code == "not_open" and reason == "free_text_for_the_agent" else code
        assert bool(agent.eligible([outcome])) is (expected is None)


def test_a_question_of_unknown_origin_must_read_as_open() -> None:
    """Fail closed: a `Question` built without its reason is held to the opener rule."""
    assert agent.Question("k", "Favourite colour", "text").reason == "not_a_known_question"
    assert agent.refusal("Favourite colour", "text") == "not_open"
    assert agent.refusal("Favourite colour", "text", (), "free_text_for_the_agent") is None
    sources = agent.sources_of({}, "Engineer", "Example Robotics")
    reply = agent.Reply((agent.Proposal("k", "Green, I like building agents.", "facts"),))
    unknown = agent.Question("k", "Favourite colour", "text")
    (verdict,) = agent.judge([unknown], reply, sources)
    assert (verdict.answer, verdict.code) == (None, "not_open")
    written = agent.Question("k", "Favourite colour", "text", reason="free_text_for_the_agent")
    (verdict,) = agent.judge([written], reply, sources)
    assert verdict.code == "accepted"


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
        ("We may share your data with third parties", True),
        ("Why Example Robotics?", False),
        ("Tell us about a project", False),
        # a number or a "share" is a clause in a box, not a legal topic in a question
        ("What would you build in your first 90 days?", False),
        ("Share an example of a hard bug you fixed", False),
        ("Partagez un exemple de projet", False),
        ("", False),
    ],
)
def test_the_legal_reading_of_terms(label: str, legal: bool) -> None:
    assert terms.legal_reading(label) is legal


@pytest.mark.parametrize(
    "label",
    ["I have read the privacy notice of 2026", "I have read the privacy notice (v2)"],
)
def test_a_box_with_a_number_stays_the_persons(label: str) -> None:
    assert terms.classify(label).kind is terms.BoxKind.person


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
