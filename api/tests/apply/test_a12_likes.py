"""Apply A12 on a real Postgres at head: the Like (`likes.on_like`, the contract with the swipe deck
of #74), the documents it asks, its refusals, and the review queue's routes. Persons A and B hold
postings at the same invented company ("Example Robotics"); every account is
`e2e-*@example.org`. No test sleeps; the race on the person's lock is played with two sessions in
a fixed order under a short lock timeout.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps, gate, likes, queue_router, run, service
from app.apply.caps_models import ApplySettings, LikeMode
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.documents import freeze as documents
from app.documents import generate, store
from app.documents.models import (
    Author,
    DocumentGeneration,
    FitResult,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Origin,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.main import create_app
from app.profile import service as profile_service
from tests.apply import a1_support
from tests.apply.a5_support import FakeAgent, run_ashby
from tests.apply.a6_support import FakeQueue, submit_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import WHY, fill, services
from tests.apply.test_a6_gate import _password, run_submit, submit_services
from tests.conftest import make_settings
from tests.documents.conftest import load
from tests.documents.test_tick import World, posting_cv
from tests.identity.api_helpers import Api
from tests.profile.conftest import full

SECRET_KEY = "e2e-only-not-a-secret-" + "0" * 24
OWNER_RULE = "No cybersecurity roles: cyber, security, pentest, threat (example rule)"


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


def form(stub: str) -> str:
    return f"{stub}/ashby/example-robotics/{uuid.uuid4()}/application"


def posting(
    db: Session,
    user_id: uuid.UUID,
    url: str | None,
    *,
    title: str = "Applied AI Engineer",
    company: str = "Example Robotics",
    kinds: tuple[Kind, ...] = (Kind.cv, Kind.letter),
    status: str = "found",
) -> uuid.UUID:
    """A posting in Paris with a rendered one page version of each of `kinds`. Commits."""
    found = db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url, status, location, posting_text) "
            "VALUES (:u, :s, :c, :t, :url, :st, 'Paris, France', :x) RETURNING id"
        ),
        {
            "u": user_id,
            "s": f"a12-{uuid.uuid4().hex[:12]}",
            "c": company,
            "t": title,
            "url": url,
            "st": status,
            "x": "Example Robotics builds warehouse robots. You will build agents.",
        },
    ).scalar_one()
    for kind in kinds:
        content = load(f"written/{kind.value}_typical.json")
        document = store.get_or_create(db, user_id, found, kind, at=AT)
        store.save_draft(
            db, user_id, document.id, content, document.draft_revision, Author.user, at=AT
        )
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
    db.commit()
    return found


def like(db: Session, person: Person, posting_id: uuid.UUID, **kw: Any) -> likes.Liked:
    try:
        return likes.on_like(db, person.user_id, posting_id, now=AT, **kw)
    except Exception:
        db.rollback()
        raise


def refused(db: Session, person: Person, posting_id: uuid.UUID) -> service.RefusedError:
    with pytest.raises(service.RefusedError) as caught:
        like(db, person, posting_id)
    return caught.value


def generations(db: Session, posting_id: uuid.UUID) -> list[tuple[Kind, DocumentGeneration]]:
    db.expire_all()
    rows = db.execute(
        sa.text(
            "SELECT d.kind, g.id FROM document_generations g JOIN documents d ON d.id = "
            "g.document_id WHERE d.posting_id = :p ORDER BY g.created_at, g.id"
        ),
        {"p": posting_id},
    ).all()
    return [(Kind(kind), db.get(DocumentGeneration, gid)) for kind, gid in rows]  # type: ignore[misc]


def applications(db: Session, user_id: uuid.UUID) -> list[Application]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(Application)
            .where(Application.user_id == user_id)
            .order_by(Application.created_at)
        ).scalars()
    )


def never_apply(db: Session, user_id: uuid.UUID, *rules: str) -> None:
    profile = profile_service.get_profile(db, user_id, AT)
    search = {**full("search"), "never_apply": list(rules)}
    profile_service.save_section(db, user_id, "search", search, profile.revision, AT)
    db.commit()


# --- the Like ------------------------------------------------------------------------------------


def test_a_like_is_the_application_created_at_once(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub))
    liked = like(db, person_a, job)
    found = applications(db, person_a.user_id)
    assert [(a.id, a.state, a.mode, a.dry_run) for a in found] == [
        (liked.application.id, State.queued, Mode.review, False)
    ]
    assert liked.documents == ()  # both are there: nothing asked
    assert generations(db, job) == []
    kinds = [e.kind for e in service.events(db, person_a.user_id, liked.application.id)]
    assert kinds == ["created", "liked"]
    assert liked.application.id in run.due(db)


def test_a_like_without_documents_asks_the_cv_and_its_letter(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=())
    liked = like(db, person_a, job)
    assert liked.documents == (Kind.cv, Kind.letter)
    [(kind, generation)] = generations(db, job)
    assert (kind, generation.trigger) == (Kind.cv, GenerationTrigger.click)
    assert generation.estimate[generate.LETTER_FOLLOWS] is True
    # No consent yet: the click waits for it, as "Write now" does, and so does the application.
    assert (generation.status, generation.wait_reason) == (
        GenerationStatus.waiting,
        GenerationWait.consent,
    )
    assert documents.current(db, person_a.user_id, job).cv.coming == "consent"
    assert run.prepare(db, liked.application.id, AT) == "waiting_documents"
    db.commit()
    [found] = applications(db, person_a.user_id)
    assert (found.state, found.needs) == (State.queued, "documents:consent")


def test_documents_that_never_come_end_the_application_blocked(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=())
    liked = like(db, person_a, job)
    [(_, generation)] = generations(db, job)
    generation.status, generation.wait_reason = GenerationStatus.failed, None
    db.commit()
    assert run.prepare(db, liked.application.id, AT) == "blocked"
    db.commit()
    [found] = applications(db, person_a.user_id)
    assert (found.state, found.error_code) == (State.blocked, "no_documents")


def test_a_cv_without_its_letter_asks_the_letter_alone(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=(Kind.cv,))
    assert like(db, person_a, job).documents == (Kind.letter,)
    [(kind, generation)] = generations(db, job)
    assert (kind, generation.trigger) == (Kind.letter, GenerationTrigger.click)
    assert generate.LETTER_FOLLOWS not in generation.estimate


def _auto(db: Session, user_id: uuid.UUID, job: uuid.UUID, **kw: Any) -> DocumentGeneration:
    document = store.get_or_create(db, user_id, job, kw.pop("kind", Kind.cv), at=AT)
    made = generate.create(
        db,
        document,
        GenerationMode.posting,
        kw.pop("trigger", GenerationTrigger.auto),
        None,
        {},
        1000,
        at=AT,
        **kw,
    )
    db.commit()
    return made


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (GenerationStatus.waiting, GenerationWait.daily_ceiling),
        (GenerationStatus.waiting, GenerationWait.consent),
        (GenerationStatus.waiting, GenerationWait.credential),
        (GenerationStatus.waiting, GenerationWait.base_cv),
        (GenerationStatus.queued, None),
    ],
)
def test_an_automatic_write_not_started_gives_way(
    db: Session,
    person_a: Person,
    stub: str,
    status: GenerationStatus,
    reason: GenerationWait | None,
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=())
    held = _auto(db, person_a.user_id, job, status=status, wait_reason=reason)
    assert like(db, person_a, job).documents == (Kind.cv, Kind.letter)
    found = generations(db, job)
    # No consent yet: the click waits for it, never for the daily ceiling.
    assert {(g.id == held.id, g.trigger, g.status, g.wait_reason) for _, g in found} == {
        (True, GenerationTrigger.auto, GenerationStatus.cancelled, None),
        (False, GenerationTrigger.click, GenerationStatus.waiting, GenerationWait.consent),
    }


@pytest.mark.parametrize(
    ("trigger", "status", "reason"),
    [
        (GenerationTrigger.click, GenerationStatus.waiting, GenerationWait.consent),
        (GenerationTrigger.click, GenerationStatus.queued, None),
        (GenerationTrigger.chat, GenerationStatus.queued, None),
        (GenerationTrigger.auto, GenerationStatus.running, None),
    ],
)
def test_a_write_on_its_way_is_not_asked_again(
    db: Session,
    person_a: Person,
    stub: str,
    trigger: GenerationTrigger,
    status: GenerationStatus,
    reason: GenerationWait | None,
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=())
    made = _auto(db, person_a.user_id, job, trigger=trigger, status=status, wait_reason=reason)
    liked = like(db, person_a, job)
    assert liked.documents == ()
    [(_, found)] = generations(db, job)
    assert (found.id, found.status) == (made.id, status)
    assert applications(db, person_a.user_id)[0].id == liked.application.id


def test_a_write_finishing_between_the_read_and_the_lock_is_not_asked_again(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = posting(db, person_a.user_id, form(stub), kinds=())
    running = _auto(db, person_a.user_id, job, status=GenerationStatus.running)
    real = likes.store.lock_posting

    def settled_meanwhile(*args: Any, **kw: Any) -> Any:
        with factory() as other:
            other.execute(
                sa.text("UPDATE document_generations SET status = 'done' WHERE id = :g"),
                {"g": running.id},
            )
            other.execute(
                sa.text("UPDATE documents SET render_state = 'queued' WHERE id = :d"),
                {"d": running.document_id},
            )
            other.commit()
        return real(*args, **kw)

    monkeypatch.setattr(likes.store, "lock_posting", settled_meanwhile)
    liked = like(db, person_a, job)
    found = generations(db, job)
    assert [g.trigger for _, g in found if g.trigger is GenerationTrigger.click] == []
    assert liked.documents == ()


@pytest.mark.parametrize("before", ["lock_posting", "lock_document"])
@pytest.mark.parametrize(("there", "running_kind"), [((), Kind.cv), ((Kind.cv,), Kind.letter)])
def test_a_write_settling_before_any_lock_of_the_like_is_not_asked_again(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    there: tuple[Kind, ...],
    running_kind: Kind,
) -> None:
    """The family of the probe above: the CV and its letter, or the letter alone, settling just
    before the Like's posting lock or its first document lock."""
    job = posting(db, person_a.user_id, form(stub), kinds=there)
    running = _auto(db, person_a.user_id, job, kind=running_kind, status=GenerationStatus.running)
    real = getattr(likes.store, before)
    calls: list[object] = []

    def settled_meanwhile(*args: Any, **kw: Any) -> Any:
        if not calls:
            with factory() as other:
                other.execute(
                    sa.text("UPDATE document_generations SET status = 'done' WHERE id = :g"),
                    {"g": running.id},
                )
                other.execute(
                    sa.text("UPDATE documents SET render_state = 'queued' WHERE id = :d"),
                    {"d": running.document_id},
                )
                other.commit()
        calls.append(args)
        return real(*args, **kw)

    monkeypatch.setattr(likes.store, before, settled_meanwhile)
    liked = like(db, person_a, job)
    assert calls
    assert [g for _, g in generations(db, job) if g.trigger is GenerationTrigger.click] == []
    assert liked.documents == ()


def test_the_like_reads_the_documents_under_their_locks(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While the Like reads what the posting has, no other transaction can lock its CV or its
    letter: no run settles or starts on them between the read and the ask."""
    job = posting(db, person_a.user_id, form(stub), kinds=(Kind.cv,))
    _auto(db, person_a.user_id, job, kind=Kind.letter, status=GenerationStatus.running)
    ids = [d.id for d in store.posting_documents(db, person_a.user_id, job)]
    db.commit()
    real = likes.documents.current
    refused: list[str] = []

    def read_while_others_try(*args: Any, **kw: Any) -> Any:
        for document_id in ids if not refused else ():
            with factory() as other:
                try:
                    other.execute(
                        sa.text("SELECT id FROM documents WHERE id = :d FOR UPDATE NOWAIT"),
                        {"d": document_id},
                    )
                except sa.exc.OperationalError as error:
                    refused.append(type(error.orig).__name__)
                other.rollback()
        return real(*args, **kw)

    monkeypatch.setattr(likes.documents, "current", read_while_others_try)
    assert like(db, person_a, job).documents == ()
    assert refused == ["LockNotAvailable", "LockNotAvailable"]


def test_a_like_before_the_consent_is_not_held_by_the_ceiling_after_it(
    factory: sessionmaker[Session], migrated_url: str, stub: str
) -> None:
    """The Like takes over the automatic write waiting for the consent: once the consent comes,
    the tick starts the Like's click though the daily ceiling holds the automatic writes."""
    who = World(factory, migrated_url, "e2e-apply-ceiling@example.org", consent=False)
    who.search(daily_ceiling=1)
    first, liked = who.posting(95), who.posting(90)
    who.set_job(liked, url=form(stub))
    who.tick()
    assert {(g["trigger"], g["wait_reason"]) for g in posting_cv(who.generations())} == {
        (GenerationTrigger.auto, GenerationWait.consent)
    }
    with factory() as db:
        assert likes.on_like(db, who.user, liked, now=AT).documents == (Kind.cv, Kind.letter)
    who.consent()
    who.tick()
    assert {
        (g["trigger"], g["status"], g["wait_reason"]) for g in posting_cv(who.generations(liked))
    } == {
        (GenerationTrigger.auto, GenerationStatus.cancelled, None),
        (GenerationTrigger.click, GenerationStatus.running, None),
    }
    with factory() as db:
        assert documents.current(db, who.user, liked).cv.coming == documents.WRITING
    assert [g["status"] for g in posting_cv(who.generations(first))] == [GenerationStatus.running]


@pytest.mark.parametrize(
    "title",
    [
        "Cybersecurity Engineer",
        "Cyber Security Analyst",
        "CYBERSECURITY engineer",
        "\u0421ybersecurity Engineer",  # a Cyrillic capital Es, drawn like a Latin C
        "Cyber­security Engineer",  # a soft hyphen
        "Senior Security Engineer",
        "Threat Intelligence Lead",
    ],
)
def test_a_like_on_a_never_apply_role_is_refused_at_once(
    db: Session, person_a: Person, stub: str, title: str
) -> None:
    never_apply(db, person_a.user_id, OWNER_RULE)
    job = posting(db, person_a.user_id, form(stub), title=title, kinds=())
    error = refused(db, person_a, job)
    assert error.refusal.code == "never_apply_role"
    assert likes.sentence("never_apply_role").startswith("Your rules exclude this role")
    assert applications(db, person_a.user_id) == []
    assert generations(db, job) == []  # nothing asked, nothing spent


def test_unreadable_rules_refuse_the_like(db: Session, person_a: Person, stub: str) -> None:
    db.execute(
        sa.text(
            "UPDATE profiles SET sections = sections || "
            "jsonb_build_object('search', CAST(:s AS jsonb)) WHERE user_id = :u"
        ),
        {"s": '{"never_apply": 7}', "u": person_a.user_id},
    )
    db.commit()
    job = posting(db, person_a.user_id, form(stub))
    assert refused(db, person_a, job).refusal.code == "rules_unreadable"


@pytest.mark.parametrize(
    ("company", "title"),
    [
        ("Example Robotics", "Applied AI Engineer"),
        ("Example Robotics GmbH", "Applied AI Engineer"),
        ("example robotics", "Applied AI Engineer (m/w/d)"),
        ("Example Robotics", "Applied  AI  Engineer"),
        ("Example Robotics SAS", "Applied AI Engineer (H/F)"),
    ],
)
def test_likes_on_two_postings_of_the_same_role(
    db: Session, person_a: Person, stub: str, company: str, title: str
) -> None:
    first = like(db, person_a, posting(db, person_a.user_id, form(stub)))
    second = posting(db, person_a.user_id, form(stub), company=company, title=title)
    error = refused(db, person_a, second)
    assert (error.refusal.code, error.refusal.application_id) == (
        "same_role_live",
        first.application.id,
    )
    assert likes.sentence("same_role_live") == likes.SENTENCES["same_role_live"]
    assert len(applications(db, person_a.user_id)) == 1


@pytest.mark.parametrize(
    ("company", "title"),
    [
        ("Example Robotics", "Applied AI Engineer II"),
        ("Example Robotics", "Applied AI Researcher"),
        ("Example Robotic", "Applied AI Engineer"),
        ("Sample Robotics", "Applied AI Engineer"),
    ],
)
def test_another_role_is_not_a_duplicate(
    db: Session, person_a: Person, stub: str, company: str, title: str
) -> None:
    like(db, person_a, posting(db, person_a.user_id, form(stub)))
    like(db, person_a, posting(db, person_a.user_id, form(stub), company=company, title=title))
    assert len(applications(db, person_a.user_id)) == 2


def test_the_same_role_is_free_again_once_the_first_ended(
    db: Session, person_a: Person, stub: str
) -> None:
    first = like(db, person_a, posting(db, person_a.user_id, form(stub)))
    gate.cancel(db, person_a.user_id, first.application.id, AT, reject=False)
    db.commit()
    like(db, person_a, posting(db, person_a.user_id, form(stub)))
    assert sorted(a.state for a in applications(db, person_a.user_id)) == [
        State.cancelled,
        State.queued,
    ]


def test_a_clicked_role_keeps_a2s_refusal(db: Session, person_a: Person, stub: str) -> None:
    first = like(db, person_a, posting(db, person_a.user_id, form(stub)))
    found = db.get(Application, first.application.id)
    found.state, found.submitted_at = State.submitted, AT  # type: ignore[union-attr]
    db.commit()
    assert (
        refused(db, person_a, posting(db, person_a.user_id, form(stub))).refusal.code == "same_role"
    )


def test_the_same_posting_liked_twice_is_one_application(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub))
    first = like(db, person_a, job)
    error = refused(db, person_a, job)
    assert (error.refusal.code, error.refusal.application_id) == (
        "in_progress",
        first.application.id,
    )


def test_reject_then_like_again_is_a_new_application(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub))
    first = like(db, person_a, job)
    found = db.get(Application, first.application.id)
    found.state = State.ready  # type: ignore[union-attr]
    db.commit()
    gate.cancel(db, person_a.user_id, first.application.id, AT, reject=True)
    db.commit()
    second = like(db, person_a, job)
    assert second.application.id != first.application.id
    assert sorted(a.state for a in applications(db, person_a.user_id)) == [
        State.cancelled,
        State.queued,
    ]


def test_a_refusal_rolls_the_documents_ask_back(db: Session, person_a: Person) -> None:
    job = posting(db, person_a.user_id, None, kinds=())
    assert refused(db, person_a, job).refusal.code == "no_address"
    assert generations(db, job) == []
    assert applications(db, person_a.user_id) == []


@pytest.mark.parametrize("status", ["applied", "closed"])
def test_a_posting_already_applied_or_closed_is_refused(
    db: Session, person_a: Person, stub: str, status: str
) -> None:
    job = posting(db, person_a.user_id, form(stub), status=status, kinds=())
    code = refused(db, person_a, job).refusal.code
    assert code == ("already_applied" if status == "applied" else "posting_closed")
    assert generations(db, job) == []


def test_a_stored_full_auto_still_makes_a_review_item(
    db: Session, person_a: Person, stub: str
) -> None:
    db.add(
        ApplySettings(user_id=person_a.user_id, mode_after_like=LikeMode.full_auto, updated_at=AT)
    )
    db.commit()
    assert caps.settings_of(db, person_a.user_id).mode_after_like is LikeMode.full_auto
    liked = like(db, person_a, posting(db, person_a.user_id, form(stub)))
    assert liked.application.mode is Mode.review
    [event] = [
        e for e in service.events(db, person_a.user_id, liked.application.id) if e.kind == "liked"
    ]
    assert event.data == {"mode_after_like": "full_auto", "documents": []}


def test_another_persons_posting_is_not_found(
    db: Session, person_a: Person, person_b: Person
) -> None:
    with pytest.raises(service.PostingNotFoundError):
        like(db, person_a, person_b.posting_id)
    assert applications(db, person_a.user_id) == applications(db, person_b.user_id) == []


def test_a_like_waits_for_the_persons_apply_lock(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 50)
    first, second = (
        posting(db, person_a.user_id, form(stub)),
        posting(db, person_a.user_id, form(stub)),
    )
    with factory() as holder:
        assert jobs_service.lock_job(holder, person_a.user_id, first) is not None
        service._lock_person(holder, person_a.user_id)  # another Like of the person, not committed
        with pytest.raises(jobs_service.JobBusyError):
            like(db, person_a, second)
        holder.rollback()
    assert applications(db, person_a.user_id) == []
    like(db, person_a, second)
    assert len(applications(db, person_a.user_id)) == 1


# --- the queue's routes --------------------------------------------------------------------------


@pytest.fixture
def api_app(migrated_url: str, stub: str) -> FastAPI:
    settings = make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub)
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def filled(factory: sessionmaker[Session], application_id: uuid.UUID, stub: str) -> str:
    agent = FakeAgent(
        lambda q: ("I build agents.", "motivation") if q.label == WHY else ("leave", "")
    )
    return fill(services(factory, run_ashby(), None, agent, stub_base=stub), application_id)


def test_the_routes(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    never_apply(db, person_a.user_id, OWNER_RULE)
    _password(factory, person_a, person_b)
    jobs = [
        posting(db, person_a.user_id, form(stub), title=t)
        for t in ("Robotics Engineer", "ML Engineer")
    ]
    cyber = posting(db, person_a.user_id, form(stub), title="Cybersecurity Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = queue = FakeQueue()
        api = Api(client)
        token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)

        created = [api.post(f"/api/postings/{job}/like", token=token_a) for job in jobs]
        assert [r.status_code for r in created] == [201, 201]
        assert created[0].headers["cache-control"] == "no-store"
        body = created[0].json()
        assert (body["application"]["state"], body["application"]["mode"], body["documents"]) == (
            "queued",
            "review",
            [],
        )
        first, second = (uuid.UUID(r.json()["application"]["id"]) for r in created)

        answer = api.post(f"/api/postings/{cyber}/like", token=token_a)
        assert answer.status_code == 409
        assert answer.json()["code"] == "never_apply_role"
        assert answer.json()["detail"].startswith("Your rules exclude this role")
        again = api.post(f"/api/postings/{jobs[0]}/like", token=token_a).json()
        assert (again["code"], again["application_id"]) == ("in_progress", str(first))
        assert api.post(f"/api/postings/{jobs[0]}/like", token=token_b).status_code == 404
        assert api.post("/api/postings/not-a-uuid/like", token=token_a).status_code == 404
        assert (
            api.post(f"/api/postings/{jobs[0]}/like", token=token_a, csrf=False).status_code == 403
        )
        assert api.post(f"/api/postings/{jobs[0]}/like").status_code == 401

        queued = api.get("/api/apply/queue", token=token_a).json()
        assert [i["state"] for i in queued["items"]] == ["queued", "queued"]
        assert queued["waiting_on_you"] == 0

        # the runs finish: the queue fills
        assert filled(factory, first, stub) == "ready"
        assert filled(factory, second, stub) == "ready"
        assert api.get("/api/apply/queue/count", token=token_a).json() == {"waiting_on_you": 2}
        assert api.get("/api/apply/queue/count", token=token_b).json() == {"waiting_on_you": 0}
        assert api.get("/api/apply/queue", token=token_b).json()["items"] == []
        items = {i["id"]: i for i in api.get("/api/apply/queue", token=token_a).json()["items"]}
        card = items[str(first)]
        assert (card["company"], card["title"], card["state"]) == (
            "Example Robotics",
            "Robotics Engineer",
            "ready",
        )
        assert card["shot_id"] is not None
        assert card["documents"]["cv"] == {"number": 1, "fit": "one_page", "corrections": 0}
        assert card["documents_review"] is False
        assert card["reason"] is None
        assert card["left"] >= card["required_left"] >= 0

        # approve from the queue while the posting page is open in another tab: one decision
        approval = api.get(f"/api/applications/{first}/approval", token=token_a).json()
        sent = {"answers_hash": approval["answers_hash"]}
        tabs = [
            api.post(f"/api/applications/{first}/approve", token=token_a, json=sent)
            for _ in range(2)
        ]
        assert [t.status_code for t in tabs] == [200, 409]
        assert tabs[1].json()["code"] == "not_ready"
        assert len(queue.enqueued) == 1
        assert run_submit(submit_services(factory, submit_ashby(), stub), first) == "submitted"

        # reject and skip the posting; B cannot reject A's item
        path = f"/api/apply/queue/{second}/reject"
        assert api.post(path, token=token_b, json={"skip_posting": True}).status_code == 404
        rejected = api.post(path, token=token_a, json={"skip_posting": True}).json()
        assert (rejected["application"]["state"], rejected["posting_status"]) == (
            "cancelled",
            "skipped",
        )
        assert api.post(path, token=token_a, json={}).json()["code"] == "not_cancellable"
        assert api.post(path, token=token_a, json={"skip": True}).status_code == 422

        db.expire_all()
        assert db.get(Job, jobs[0]).status is JobStatus.applied  # type: ignore[union-attr]
        assert db.get(Application, second).error_code == "rejected"  # type: ignore[union-attr]
        assert api.get("/api/apply/queue", token=token_a).json()["items"] == []


def test_the_queue_and_its_count_are_one_moment(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change committed between the items' read and the count's is in neither."""
    _password(factory, person_a)
    liked = like(db, person_a, posting(db, person_a.user_id, form(stub))).application.id
    real = queue_router._refused_decisions

    def committed_meanwhile(read: Session, *args: Any) -> Any:
        with factory() as other:
            other.execute(
                sa.text("UPDATE applications SET state = 'needs_person' WHERE id = :a"),
                {"a": liked},
            )
            other.commit()
        return real(read, *args)

    monkeypatch.setattr(queue_router, "_refused_decisions", committed_meanwhile)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        answer = api.get("/api/apply/queue", token=api.login_token(person_a.email)).json()
    assert ([i["state"] for i in answer["items"]], answer["waiting_on_you"]) == (["queued"], 0)


def test_reject_without_skip_keeps_the_posting_and_a_late_status_is_kept(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a)
    kept, late = (
        posting(db, person_a.user_id, form(stub), title="Robotics Engineer"),
        posting(db, person_a.user_id, form(stub), title="ML Engineer"),
    )
    ids = [like(db, person_a, job).application.id for job in (kept, late)]
    assert [filled(factory, i, stub) for i in ids] == ["ready", "ready"]
    with factory() as s:
        job = jobs_service.lock_job(s, person_a.user_id, late)
        jobs_service.set_status(s, job, JobStatus.interview, AT)  # type: ignore[arg-type]
        s.commit()
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        plain = api.post(f"/api/apply/queue/{ids[0]}/reject", token=token, json={}).json()
        late_one = api.post(
            f"/api/apply/queue/{ids[1]}/reject", token=token, json={"skip_posting": True}
        )
    assert plain["posting_status"] == "staged"
    assert late_one.json()["posting_status"] == "interview"


def test_a_ready_item_carries_why_it_came_back(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, api_app: FastAPI
) -> None:
    _password(factory, person_a)
    first = like(db, person_a, posting(db, person_a.user_id, form(stub))).application.id
    assert filled(factory, first, stub) == "ready"
    with factory() as s:
        caps.save_settings(s, person_a.user_id, AT, paused=True, min_fit=None)
        s.commit()
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = FakeQueue()
        api = Api(client)
        token = api.login_token(person_a.email)
        sent = api.get(f"/api/applications/{first}/approval", token=token).json()["answers_hash"]
        answer = api.post(
            f"/api/applications/{first}/approve", token=token, json={"answers_hash": sent}
        )
        assert answer.json()["code"] == "paused_by_you"
        [card] = api.get("/api/apply/queue", token=token).json()["items"]
    assert card["state"] == "ready"
    assert card["reason"] == {"code": "paused_by_you", "sentence": caps.SENTENCES["paused_by_you"]}
    events = db.execute(
        sa.select(ApplicationEvent.kind).where(ApplicationEvent.application_id == first)
    ).scalars()
    assert "decision" in list(events)


# --- a passed posting ----------------------------------------------------------------------------


def test_a_like_on_a_passed_posting_is_refused_at_once(
    db: Session, person_a: Person, stub: str
) -> None:
    job = posting(db, person_a.user_id, form(stub), status="skipped", kinds=())
    error = refused(db, person_a, job)
    assert error.refusal.code == "passed"
    assert likes.sentence("passed").startswith("You passed this posting")
    assert applications(db, person_a.user_id) == []
    assert generations(db, job) == []  # nothing asked, nothing spent


def test_a_like_on_a_passed_posting_is_refused_in_full_auto_too(
    db: Session, person_a: Person, stub: str
) -> None:
    db.add(
        ApplySettings(
            user_id=person_a.user_id,
            mode_after_like=LikeMode.full_auto,
            full_auto_since=AT,
            rehearsals_done=3,
            updated_at=AT,
        )
    )
    db.commit()
    job = posting(db, person_a.user_id, form(stub), status="skipped")
    assert refused(db, person_a, job).refusal.code == "passed"
    assert applications(db, person_a.user_id) == []


def test_a_pass_then_its_undo_then_a_like_and_a_concurrent_pass_then_like(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 50)
    _password(factory, person_a)
    first, second = (posting(db, person_a.user_id, form(stub), title=t) for t in ("A", "B"))
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)

        # a Pass whose answer was lost, then a Like: 409 `passed`, no application
        assert api.post(f"/api/postings/{first}/pass", token=token).status_code == 200
        answer = api.post(f"/api/postings/{first}/like", token=token)
        assert answer.status_code == 409
        assert answer.json()["code"] == "passed"
        assert answer.json()["detail"] == likes.sentence("passed")
        assert applications(db, person_a.user_id) == []

        # the undo puts it back first: the Like is accepted
        assert api.request("DELETE", f"/api/postings/{first}/pass", token=token).status_code == 200
        assert api.post(f"/api/postings/{first}/like", token=token).status_code == 201

        # a Pass in flight (row lock held, not committed) makes the Like busy; once it commits the
        # Like sees the posting skipped
        with factory() as passer:
            assert jobs_service.lock_job(passer, person_a.user_id, second) is not None
            busy = api.post(f"/api/postings/{second}/like", token=token)
            assert (busy.status_code, busy.json()["code"]) == (409, "busy")
            jobs_service.set_status(
                passer,
                jobs_service.lock_job(passer, person_a.user_id, second),
                JobStatus.skipped,
                AT,
            )
            passer.commit()
        late = api.post(f"/api/postings/{second}/like", token=token)
        assert (late.status_code, late.json()["code"]) == (409, "passed")
        assert len(applications(db, person_a.user_id)) == 1
