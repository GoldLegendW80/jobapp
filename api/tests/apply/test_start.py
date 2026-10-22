"""A2 on a real Postgres at head: `service.start` and its refusals, the routes, the tables'
constraints and cascades. Two people, A and B, hold postings at the same invented company
("Example Robotics"); every account is `e2e-*@example.org`. No test sleeps: the race of two starts
is played with two sessions in a fixed order under a short lock timeout.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.apply import service
from app.apply.models import Application, Mode, State
from app.config import Settings
from app.jobs import service as jobs_service
from app.main import create_app
from app.profile import service as profile_service
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user
from tests.profile.conftest import full

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url"]

AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
USER_A = "e2e-apply-a2-a@example.org"
USER_B = "e2e-apply-b2-b@example.org"
SECRET_KEY = "e2e-only-not-a-secret-" + "0" * 24
GH = "https://boards.greenhouse.io/examplerobotics/jobs/{}"
OWNER_RULE = "No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"
SECURITY_RULE = "No security roles: Security Engineer"
JPEG = b"\xff\xd8\xff" + bytes(range(64))


@pytest.fixture
def db(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
        session.rollback()


def user(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    return make_user(factory, email=email, name=email.split("@")[0]).id


@pytest.fixture
def a(factory: sessionmaker[Session]) -> uuid.UUID:
    return user(factory, USER_A)


@pytest.fixture
def b(factory: sessionmaker[Session]) -> uuid.UUID:
    return user(factory, USER_B)


def rules(db: Session, user_id: uuid.UUID, *never_apply: str) -> None:
    profile = profile_service.get_profile(db, user_id, AT)
    search = {**full("search"), "never_apply": list(never_apply)}
    profile_service.save_section(db, user_id, "search", search, profile.revision, AT)
    db.commit()


def posting(
    db: Session,
    user_id: uuid.UUID,
    title: str = "Applied AI Engineer",
    url: str | None = None,
    *,
    company: str = "Example Robotics",
    status: str = "found",
    applied_on: date | None = None,
    text: str = "Example Robotics builds warehouse robots.",
) -> uuid.UUID:
    found = db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url, status, applied_on, "
            "posting_text) VALUES (:u, :s, :c, :t, :url, :st, :ap, :x) RETURNING id"
        ),
        {
            "u": user_id,
            "s": f"example-{uuid.uuid4().hex[:12]}",
            "c": company,
            "t": title,
            "url": url if url is not None else GH.format(uuid.uuid4().int % 10**9),
            "st": status,
            "ap": applied_on,
            "x": text,
        },
    ).scalar_one()
    db.commit()
    return found


def start(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, mode: Mode = Mode.review, **kw: Any
) -> service.Started:
    try:
        return service.start(db, user_id, posting_id, mode, kw.pop("dry_run", False), now=AT, **kw)
    except Exception:
        db.rollback()
        raise


def refused(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, mode: Mode = Mode.review
) -> str:
    with pytest.raises(service.RefusedError) as caught:
        start(db, user_id, posting_id, mode)
    return caught.value.refusal.code


def set_state(db: Session, application_id: uuid.UUID, state: State, at: datetime | None) -> None:
    db.execute(
        sa.update(Application)
        .where(Application.id == application_id)
        .values(state=state, submitted_at=at)
    )
    db.commit()


def count(db: Session, table: str, user_id: uuid.UUID) -> int:
    return db.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": user_id}
    ).scalar_one()


# --- never apply ---------------------------------------------------------------------------------


@pytest.mark.parametrize("rule", [SECURITY_RULE, OWNER_RULE])
def test_a_never_apply_title_in_each_mode(db: Session, a: uuid.UUID, rule: str) -> None:
    rules(db, a, rule)
    job = posting(db, a, "Security Engineer")
    assert refused(db, a, job, Mode.review) == "never_apply_role"
    assert refused(db, a, job, Mode.full_auto) == "never_apply_role"
    assert count(db, "applications", a) == 0
    started = start(db, a, job, Mode.manual)
    assert started.warnings == ("never_apply_role",)
    assert started.application.state is State.queued


def test_the_owners_rule_refuses_a_glued_title(db: Session, a: uuid.UUID) -> None:
    rules(db, a, OWNER_RULE)
    assert refused(db, a, posting(db, a, "Cybersecurity Engineer (H/F)")) == "never_apply_role"
    assert start(db, a, posting(db, a, "Applied AI Engineer")).warnings == ()


def test_the_sweeps_never_apply_event(db: Session, a: uuid.UUID) -> None:
    rules(db, a)  # the rule was removed since the sweep
    job = posting(db, a, "Applied AI Engineer", status="skipped")
    db.execute(
        sa.text(
            "INSERT INTO job_events (job_id, user_id, kind, data) VALUES (:j, :u, 'status', "
            """'{"from": "found", "to": "skipped", "via": "never_apply"}')"""
        ),
        {"j": job, "u": a},
    )
    db.commit()
    assert refused(db, a, job, Mode.review) == "never_apply_role"
    assert refused(db, a, job, Mode.full_auto) == "never_apply_role"
    assert start(db, a, job, Mode.manual).warnings == ("never_apply_role", "passed")


def test_rules_that_cannot_be_read_refuse_like_a_match(db: Session, a: uuid.UUID) -> None:
    rules(db, a, OWNER_RULE)
    db.execute(
        sa.text(
            "UPDATE profiles SET sections = jsonb_set(sections, '{search,days}', '\"x\"') "
            "WHERE user_id = :u"
        ),
        {"u": a},
    )
    db.commit()
    job = posting(db, a)
    assert refused(db, a, job, Mode.review) == "rules_unreadable"
    assert start(db, a, job, Mode.manual).warnings == ("rules_unreadable",)


def test_full_auto_reads_the_posting_text(db: Session, a: uuid.UUID) -> None:
    rules(db, a, OWNER_RULE)
    job = posting(db, a, text="You will build threat detection agents.")
    assert refused(db, a, job, Mode.full_auto) == "never_apply_role"
    assert start(db, a, job, Mode.review).warnings == ()


# --- duplicates ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "refuses"), [(179, True), (180, True), (180.00002, False), (181, False)]
)
def test_the_same_role_on_two_postings(
    db: Session, a: uuid.UUID, days: float, refuses: bool
) -> None:
    first = posting(db, a, "Applied AI Engineer")
    done = start(db, a, first).application.id
    set_state(db, done, State.submitted, AT - timedelta(days=days))
    second = posting(db, a, "Applied AI Engineer (H/F)", company="Example Robotics SAS")
    if refuses:
        assert refused(db, a, second, Mode.review) == "same_role"
        assert refused(db, a, second, Mode.full_auto) == "same_role"
        assert start(db, a, second, Mode.manual).warnings == ("same_role",)
    else:
        assert start(db, a, second).warnings == ()


@pytest.mark.parametrize(("days", "refuses"), [(179, True), (180, True), (181, False)])
def test_the_same_role_applied_outside_the_app(
    db: Session, a: uuid.UUID, days: int, refuses: bool
) -> None:
    posting(
        db, a, "Applied AI Engineer", status="applied", applied_on=(AT - timedelta(days)).date()
    )
    second = posting(db, a, "applied ai engineer")
    if refuses:
        assert refused(db, a, second) == "same_role"
    else:
        assert start(db, a, second).warnings == ()


def test_another_role_or_company_is_not_the_same_role(db: Session, a: uuid.UUID) -> None:
    done = start(db, a, posting(db, a, "Applied AI Engineer")).application.id
    set_state(db, done, State.submitted, AT - timedelta(days=10))
    assert start(db, a, posting(db, a, "Senior Applied AI Engineer")).warnings == ()
    assert (
        start(db, a, posting(db, a, "Applied AI Engineer", company="Northwind Labs")).warnings == ()
    )


def test_a_cancelled_application_is_not_the_same_role(db: Session, a: uuid.UUID) -> None:
    done = start(db, a, posting(db, a)).application.id
    set_state(db, done, State.cancelled, None)
    assert start(db, a, posting(db, a)).warnings == ()


def test_the_same_greenhouse_job_under_two_addresses(db: Session, a: uuid.UUID) -> None:
    first = posting(db, a, "Applied AI Engineer", GH.format(4012345))
    second = posting(
        db, a, "AI Engineer, Agents", "https://example.org/careers/ai?gh_jid=4012345&utm_source=x"
    )
    live = start(db, a, first).application
    assert live.canonical_key == "greenhouse:4012345"
    for mode in Mode:
        with pytest.raises(service.RefusedError) as caught:
            start(db, a, second, mode)
        assert caught.value.refusal.code == "same_form"
        assert caught.value.refusal.application_id == live.id
    set_state(db, live.id, State.cancelled, None)
    assert start(db, a, second).application.canonical_key == "greenhouse:4012345"


def test_the_same_form_applied_outside_the_app(db: Session, a: uuid.UUID) -> None:
    posting(db, a, "Applied AI Engineer", GH.format(777), status="interview")
    job = posting(db, a, "Applied AI Engineer II", "https://job-boards.eu.greenhouse.io/x/jobs/777")
    assert refused(db, a, job, Mode.manual) == "same_form"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("applied", "already_applied"),
        ("interview", "already_applied"),
        ("rejected", "already_applied"),
        ("closed", "posting_closed"),
    ],
)
def test_a_posting_already_applied(db: Session, a: uuid.UUID, status: str, code: str) -> None:
    job = posting(db, a, status=status)
    for mode in Mode:
        assert refused(db, a, job, mode) == code


@pytest.mark.parametrize("status", ["found", "shortlisted", "cv_ready", "staged", "skipped"])
def test_a_posting_not_applied_yet(db: Session, a: uuid.UUID, status: str) -> None:
    if status == "skipped":
        assert refused(db, a, posting(db, a, status=status)) == "passed"
        return
    assert start(db, a, posting(db, a, status=status)).application.state is State.queued


def test_the_live_application_of_the_posting(db: Session, a: uuid.UUID) -> None:
    job = posting(db, a)
    first = start(db, a, job).application
    for mode in Mode:
        with pytest.raises(service.RefusedError) as caught:
            start(db, a, job, mode)
        assert caught.value.refusal.code == "in_progress"
        assert caught.value.refusal.application_id == first.id
    for state, code in ((State.submit_unknown, "submit_unknown"), (State.submitted, "submitted")):
        set_state(db, first.id, state, AT)
        assert refused(db, a, job) == code
    set_state(db, first.id, State.failed, None)
    assert start(db, a, job).application.id != first.id


@pytest.mark.parametrize("url", ["", "javascript:void(0)", "/careers/1"])
def test_no_form_address(db: Session, a: uuid.UUID, url: str) -> None:
    job = posting(db, a)
    db.execute(
        sa.text("UPDATE jobs SET url = NULLIF(:url, '') WHERE id = :j"), {"url": url, "j": job}
    )
    db.commit()
    for mode in Mode:
        assert refused(db, a, job, mode) == "no_address"


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
def test_mailto_posting_starts_an_email_application(db: Session, a: uuid.UUID, mode: Mode) -> None:
    job = posting(db, a)
    db.execute(
        sa.text("UPDATE jobs SET url = :url WHERE id = :j"),
        {"url": "mailto:e2e-jobs@example.org", "j": job},
    )
    db.commit()
    application = start(db, a, job, mode).application
    assert application.ats == "email"
    assert application.form_url == "mailto:e2e-jobs@example.org"


def test_a_rehearsal_is_never_manual(db: Session, a: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="never a rehearsal"):
        start(db, a, posting(db, a), Mode.manual, dry_run=True)
    assert start(db, a, posting(db, a), Mode.review, dry_run=True).application.dry_run


def test_the_created_event_holds_ids_and_codes_only(db: Session, a: uuid.UUID) -> None:
    rules(db, a, SECURITY_RULE)
    marker = "canary-" + uuid.uuid4().hex
    job = posting(db, a, f"Security Engineer {marker}", text=marker)
    application = start(db, a, job, Mode.manual).application
    events = service.events(db, a, application.id)
    assert [(e.kind, e.data) for e in events] == [
        ("created", {"mode": "manual", "dry_run": False, "warnings": ["never_apply_role"]})
    ]
    row = db.execute(
        sa.text("SELECT row_to_json(a)::text FROM applications a WHERE id = :i"),
        {"i": application.id},
    ).scalar_one()
    assert marker not in row


# --- two at once ---------------------------------------------------------------------------------


@pytest.fixture
def short_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 50)


def test_two_starts_at_once_make_one_row(
    factory: sessionmaker[Session], a: uuid.UUID, short_locks: None
) -> None:
    with factory() as setup:
        job = posting(setup, a)
        other = posting(setup, a, "AI Engineer, Agents", GH.format(4012999))
        twin = posting(setup, a, "AI Engineer, Tools", f"https://example.org/?gh_jid={4012999}")
    first, second = factory(), factory()
    try:
        first.commit = lambda: None  # type: ignore[method-assign]  # keeps the locks
        service.start(first, a, job, Mode.review, False, now=AT)
        # The same posting: its row lock. Another posting: the person's apply lock.
        for target in (job, other):
            with pytest.raises(jobs_service.JobBusyError):
                service.start(second, a, target, Mode.review, False, now=AT)
            second.rollback()
        Session.commit(first)
        with pytest.raises(service.RefusedError) as caught:
            service.start(second, a, job, Mode.review, False, now=AT)
        assert caught.value.refusal.code == "in_progress"
        second.rollback()
        service.start(second, a, other, Mode.review, False, now=AT)
        with pytest.raises(service.RefusedError) as caught:
            service.start(second, a, twin, Mode.review, False, now=AT)
        assert caught.value.refusal.code == "same_form"
        second.rollback()
        assert count(second, "applications", a) == 2
    finally:
        first.close()
        second.close()


def test_the_unique_indexes_hold_without_the_locks(db: Session, a: uuid.UUID) -> None:
    job = posting(db, a)
    insert = sa.text(
        "INSERT INTO applications (user_id, posting_id, mode, state, ats, form_url, canonical_key) "
        "VALUES (:u, :p, 'review', :s, 'greenhouse', 'https://x', :k)"
    )
    db.execute(insert, {"u": a, "p": job, "s": "cancelled", "k": "k1"})
    db.execute(insert, {"u": a, "p": job, "s": "queued", "k": "k1"})
    db.commit()
    with pytest.raises(IntegrityError, match="uq_applications_live_posting"):
        db.execute(insert, {"u": a, "p": job, "s": "ready", "k": "k2"})
    db.rollback()
    with pytest.raises(IntegrityError, match="uq_applications_live_form"):
        db.execute(insert, {"u": a, "p": posting(db, a), "s": "queued", "k": "k1"})
    db.rollback()


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ("'manual', true, 'queued', NULL", "ck_applications_manual_is_never_dry"),
        ("'review', false, 'submitted', NULL", "ck_applications_submitted_at_when_clicked"),
        ("'review', false, 'submit_unknown', NULL", "ck_applications_submitted_at_when_clicked"),
    ],
)
def test_application_constraints(db: Session, a: uuid.UUID, values: str, constraint: str) -> None:
    job = posting(db, a)
    with pytest.raises(IntegrityError, match=constraint):
        db.execute(
            sa.text(
                "INSERT INTO applications (user_id, posting_id, mode, dry_run, state, "
                "submitted_at, ats, form_url, canonical_key) "
                f"VALUES (:u, :p, {values}, 'x', 'https://x', 'k')"
            ),
            {"u": a, "p": job},
        )


# --- isolation and cascades ----------------------------------------------------------------------


def test_b_cannot_start_on_as_posting(db: Session, a: uuid.UUID, b: uuid.UUID) -> None:
    job = posting(db, a)
    with pytest.raises(service.PostingNotFoundError):
        start(db, b, job)
    assert start(db, b, posting(db, b)).warnings == ()  # B's own, same company and title
    mine = start(db, a, job).application
    assert service.get_application(db, b, mine.id) is None
    assert service.list_applications(db, b, None, 50)[0].user_id == b


def test_a_child_cannot_hang_under_another_persons_application(
    db: Session, a: uuid.UUID, b: uuid.UUID
) -> None:
    mine = start(db, a, posting(db, a)).application
    with pytest.raises(IntegrityError, match="fk_application_events_application_id_applications"):
        db.execute(
            sa.text(
                "INSERT INTO application_events (application_id, user_id, kind) "
                "VALUES (:i, :u, 'x')"
            ),
            {"i": mine.id, "u": b},
        )
    db.rollback()


def _fill_every_table(db: Session, user_id: uuid.UUID, job: uuid.UUID) -> uuid.UUID:
    application = start(db, user_id, job).application.id
    digest = hashlib.sha256(JPEG).digest()
    params = {"a": application, "u": user_id, "j": JPEG, "h": digest}
    for statement in (
        "INSERT INTO application_answers (application_id, user_id, field_key, label, kind, value, "
        "source, required) VALUES (:a, :u, 'email', 'Email', 'email', '\"x\"', 'identity', true)",
        "INSERT INTO application_events (application_id, user_id, kind) VALUES (:a, :u, 'lease')",
        "INSERT INTO application_screenshots (application_id, user_id, kind, jpeg, sha256) "
        "VALUES (:a, :u, 'before', :j, :h)",
        "INSERT INTO apply_answer_bank (user_id, question_key, question, answer) "
        "VALUES (:u, 'notice period', 'Notice period?', '\"1 month\"')",
        "INSERT INTO apply_authorisations (user_id, application_id, kind, answers_hash, "
        "expires_at) VALUES (:u, :a, 'person_approval', :h, now() + interval '1 day')",
    ):
        db.execute(sa.text(statement), params)
    run = db.execute(
        sa.text(
            "INSERT INTO site_profile_runs (user_id, host, site_kind) "
            "VALUES (:u, 'example.myworkdayjobs.com', 'workday') RETURNING id"
        ),
        params,
    ).scalar_one()
    params["r"] = run
    for statement in (
        "INSERT INTO site_profile_fields (run_id, user_id, section, field_key, label, kind, "
        "source) VALUES (:r, :u, 'contact', 'phone', 'Phone', 'text', 'identity')",
        "INSERT INTO site_profile_shots (run_id, user_id, section, kind, jpeg, sha256) "
        "VALUES (:r, :u, 'contact', 'before', :j, :h)",
        "INSERT INTO apply_authorisations (user_id, site_run_id, kind, answers_hash, expires_at) "
        "VALUES (:u, :r, 'profile_section', :h, now() + interval '1 day')",
    ):
        db.execute(sa.text(statement), params)
    db.commit()
    return application


APPLICATION_TABLES = (
    "applications",
    "application_answers",
    "application_events",
    "application_screenshots",
)
PERSON_TABLES = (
    *APPLICATION_TABLES,
    "apply_answer_bank",
    "apply_authorisations",
    "site_profile_runs",
    "site_profile_fields",
    "site_profile_shots",
)


def test_deleting_the_posting_cascades(db: Session, a: uuid.UUID) -> None:
    job = posting(db, a)
    _fill_every_table(db, a, job)
    db.execute(sa.text("DELETE FROM jobs WHERE id = :j"), {"j": job})
    db.commit()
    for table in APPLICATION_TABLES:
        assert count(db, table, a) == 0, table
    # Only the profile run's authorisation is left: it is not the posting's.
    assert count(db, "apply_authorisations", a) == 1
    assert count(db, "site_profile_runs", a) == 1


def test_deleting_the_person_cascades(db: Session, a: uuid.UUID, b: uuid.UUID) -> None:
    _fill_every_table(db, a, posting(db, a))
    _fill_every_table(db, b, posting(db, b))
    db.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": a})
    db.commit()
    for table in PERSON_TABLES:
        assert count(db, table, a) == 0, table
        # B's own: two events (created, lease) and two authorisations (application, run).
        expected = 2 if table in ("application_events", "apply_authorisations") else 1
        assert count(db, table, b) == expected, table


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO apply_authorisations (user_id, application_id, site_run_id, kind, "
            "answers_hash, expires_at) VALUES (:u, NULL, NULL, 'person_approval', :h, now() + "
            "interval '1 day')",
            "ck_apply_authorisations_one_subject",
        ),
        (
            "INSERT INTO apply_authorisations (user_id, application_id, kind, answers_hash, "
            "expires_at) VALUES (:u, :a, 'profile_section', :h, now() + interval '1 day')",
            "ck_apply_authorisations_kind_matches_subject",
        ),
        (
            "INSERT INTO apply_authorisations (user_id, application_id, kind, answers_hash, "
            "expires_at) VALUES (:u, :a, 'person_approval', :h, now() - interval '1 second')",
            "ck_apply_authorisations_expires_after_creation",
        ),
        (
            "INSERT INTO apply_authorisations (user_id, application_id, kind, answers_hash, "
            "expires_at) VALUES (:u, :a, 'person_approval', 'short', now() + interval '1 day')",
            "ck_apply_authorisations_hash_length",
        ),
        (
            "INSERT INTO application_screenshots (application_id, user_id, kind, jpeg, sha256) "
            "VALUES (:a, :u, 'before', :big, :h)",
            "ck_application_screenshots_jpeg_size",
        ),
        (
            "INSERT INTO apply_answer_bank (user_id, question_key, question, answer) "
            "VALUES (:u, '', 'q', '1')",
            "ck_apply_answer_bank_question_key_length",
        ),
    ],
)
def test_child_constraints(db: Session, a: uuid.UUID, statement: str, constraint: str) -> None:
    application = start(db, a, posting(db, a)).application.id
    params = {
        "u": a,
        "a": application,
        "h": hashlib.sha256(b"x").digest(),
        "big": b"\xff" * (2 * 1024 * 1024 + 1),
    }
    with pytest.raises(IntegrityError, match=constraint):
        db.execute(sa.text(statement), params)
    db.rollback()


# --- the routes ----------------------------------------------------------------------------------

STUB = "http://127.0.0.1:8123"


@pytest.fixture
def app(migrated_url: str) -> FastAPI:
    settings = make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=STUB)
    return create_app(settings, clock=lambda: AT)


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


def test_the_routes(api: Api, factory: sessionmaker[Session], a: uuid.UUID, b: uuid.UUID) -> None:
    token_a, token_b = api.login_token(USER_A), api.login_token(USER_B)
    with factory() as db:
        rules(db, a, SECURITY_RULE)
        job = posting(
            db,
            a,
            url=f"{STUB}/ashby/example-robotics/3f1c2a9e-5b7d-4c11-9a2e-7d0b6c5e4f31/application",
        )
        excluded = posting(db, a, "Security Engineer")
    path = f"/api/postings/{job}/applications"

    answer = api.post(path, token=token_b, json={})
    assert answer.status_code == 404
    assert answer.json() == {"detail": "No such posting."}
    assert api.post(f"/api/postings/{job}/applications", json={}).status_code == 401
    assert api.post(path, token=token_a, json={}, csrf=False).status_code == 403
    for body in ({"mode": "full_auto"}, {"mode": "manual", "dry_run": True}, {"other": 1}):
        assert api.post(path, token=token_a, json=body).status_code == 422, body

    answer = api.post(path, token=token_a, json={"mode": "review"})
    assert answer.status_code == 201, answer.text
    assert answer.headers["cache-control"] == "no-store"
    made = answer.json()
    assert made["state"] == "queued"
    assert made["ats"] == "ashby"
    assert made["warnings"] == []
    assert "canonical_key" not in made

    answer = api.post(path, token=token_a, json={"mode": "manual"})
    assert answer.status_code == 409
    assert answer.json() == {
        "detail": "An application to this posting is already under way.",
        "code": "in_progress",
        "application_id": made["id"],
    }

    answer = api.post(f"/api/postings/{excluded}/applications", token=token_a, json={})
    assert answer.status_code == 409
    assert answer.json()["code"] == "never_apply_role"
    answer = api.post(
        f"/api/postings/{excluded}/applications", token=token_a, json={"mode": "manual"}
    )
    assert answer.status_code == 201
    assert answer.json()["warnings"] == [
        {"code": "never_apply_role", "sentence": "Your rules exclude this role."}
    ]

    listed = api.get("/api/applications", token=token_a).json()["items"]
    assert {item["posting_id"] for item in listed} == {str(job), str(excluded)}
    only = api.get(f"/api/applications?posting_id={job}", token=token_a).json()["items"]
    assert [item["id"] for item in only] == [made["id"]]
    assert api.get("/api/applications", token=token_b).json() == {"items": []}

    detail = api.get(f"/api/applications/{made['id']}", token=token_a)
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert [e["kind"] for e in detail.json()["events"]] == ["created"]
    for token, target in ((token_b, made["id"]), (token_a, "nope"), (token_a, str(uuid.uuid4()))):
        answer = api.get(f"/api/applications/{target}", token=token)
        assert answer.status_code == 404
        assert answer.json() == {"detail": "No such application."}


# --- settings ------------------------------------------------------------------------------------

DEFAULTS = {
    "APPLY_SUBMIT_ENABLED": "true",
    "APPLY_APPROVAL_TTL_S": "86400",
    "APPLY_HOLD_S": "900",
    "APPLY_FULL_AUTO_SAFETY_CEILING": "30",
    "APPLY_FULL_AUTO_MIN_FIT_DEFAULT": "80",
    "APPLY_FULL_AUTO_COMPANY_DAYS": "30",
    "APPLY_REHEARSALS": "3",
    "APPLY_HOST_MIN_INTERVAL_S": "180",
    "APPLY_HOST_DAILY_MAX": "20",
    "APPLY_LINKEDIN_MIN_INTERVAL_S": "600",
    "APPLY_LINKEDIN_DAILY_MAX": "15",
    "APPLY_RETRY_DELAYS_S": "600,3600",
    "APPLY_NEEDS_PERSON_WAIT_S": "1800",
    "APPLY_STEP_SHOTS_KEEP_DAYS": "30",
    "APPLY_STUB_BASE_URL": "",
}


def test_env_example_names_every_setting_with_its_default() -> None:
    template = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in template.splitlines()
        if "=" in line and not line.startswith("#")
    )
    for name, value in DEFAULTS.items():
        assert lines.get(name) == value, name
    names = {name.upper() for name in Settings.model_fields if name.startswith("apply_")}
    assert names == set(DEFAULTS)
    settings = Settings(database_url="postgresql+psycopg://x:y@db/z")
    for name, value in DEFAULTS.items():
        got = getattr(settings, name.lower())
        assert str(got).lower() == value or (got is None and value == ""), name


def test_the_stub_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="APPLY_STUB_BASE_URL"):
        Settings(
            database_url="postgresql+psycopg://x:y@db/z",
            app_env="production",
            secret_key="s" * 40,
            apply_stub_base_url=STUB,
        )


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("600,3600", "600,3600"),
        (" 600 , 3600 ", "600,3600"),
        ("60", "60"),
        ("", None),
        ("59", None),
        ("600,abc", None),
        ("600,-1", None),
        ("1,2,3,4,5,6", None),
        ("600,86401", None),
        ("６００", None),
    ],
)
def test_retry_delays(value: str, ok: str | None) -> None:
    base = {"database_url": "postgresql+psycopg://x:y@db/z", "apply_retry_delays_s": value}
    if ok is None:
        with pytest.raises(ValidationError, match="APPLY_RETRY_DELAYS_S"):
            Settings(**base)  # type: ignore[arg-type]
    else:
        assert Settings(**base).apply_retry_delays_s == ok  # type: ignore[arg-type]


# --- issue #296 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["applied", "interview", "rejected"])
def test_an_applied_posting_without_a_date_is_the_same_role(
    db: Session, a: uuid.UUID, status: str
) -> None:
    posting(db, a, "Applied AI Engineer", status=status, applied_on=None)
    second = posting(db, a, "Applied AI Engineer (H/F)")
    assert refused(db, a, second, Mode.review) == "same_role"
    assert refused(db, a, second, Mode.full_auto) == "same_role"
    assert start(db, a, second, Mode.manual).warnings == ("same_role",)


def test_another_role_applied_without_a_date_is_not_the_same_role(
    db: Session, a: uuid.UUID
) -> None:
    posting(db, a, "Applied AI Engineer", status="applied", applied_on=None)
    assert start(db, a, posting(db, a, "Agent Engineer")).warnings == ()


def test_one_careers_page_applied_warns_in_apply_myself(db: Session, a: uuid.UUID) -> None:
    posting(db, a, "Agent Engineer", "https://example.org/careers", status="applied")
    job = posting(db, a, "Applied AI Engineer", "https://www.example.org/careers/?utm_source=x")
    assert refused(db, a, job, Mode.review) == "same_address"
    assert refused(db, a, job, Mode.full_auto) == "same_address"
    started = start(db, a, job, Mode.manual)
    assert started.warnings == ("same_address",)
    assert started.application.canonical_key == "url:http://example.org/careers"


def test_one_careers_page_with_a_live_application_is_still_the_same_form(
    db: Session, a: uuid.UUID
) -> None:
    # The second unique index holds one live application per form address, ordinary or not.
    first = posting(db, a, "Agent Engineer", "https://example.org/careers")
    live = start(db, a, first).application
    job = posting(db, a, "Applied AI Engineer", "https://www.example.org/careers/")
    for mode in Mode:
        with pytest.raises(service.RefusedError) as caught:
            start(db, a, job, mode)
        assert caught.value.refusal.code == "same_form"
        assert caught.value.refusal.application_id == live.id


def _hold_person_lock(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(f"apply:{user_id}", 0)))
    )


def _hold_live_insert(session: Session, user_id: uuid.UUID, job: uuid.UUID, key: str) -> None:
    """A writer that skipped the locks: an uncommitted live application on `key`."""
    session.execute(
        sa.text(
            "INSERT INTO applications (user_id, posting_id, mode, state, ats, form_url, "
            "canonical_key) VALUES (:u, :p, 'review', 'queued', 'greenhouse', 'https://x', :k)"
        ),
        {"u": user_id, "p": job, "k": key},
    )


@pytest.mark.parametrize("held", ["the person's lock", "another writer's insert"])
def test_a_start_past_the_lock_timeout_is_busy_with_a_code(
    api: Api, factory: sessionmaker[Session], a: uuid.UUID, short_locks: None, held: str
) -> None:
    token = api.login_token(USER_A)
    with factory() as setup:
        job = posting(setup, a, url=GH.format(4012777))
        other = posting(setup, a, "Agent Engineer")
    holder = factory()
    try:
        if held == "the person's lock":
            _hold_person_lock(holder, a)
        else:
            # the insert of the start waits on the form's unique index: a raw lock timeout
            _hold_live_insert(holder, a, other, "greenhouse:4012777")
        answer = api.post(f"/api/postings/{job}/applications", token=token, json={})
    finally:
        holder.rollback()
        holder.close()
    assert answer.status_code == 409, answer.text
    assert answer.json()["code"] == "busy"
    assert (
        answer.json()["detail"]
        == "This posting is busy with another change. Try again in a moment."
    )
    assert answer.headers["cache-control"] == "no-store"
    with factory() as check:
        assert count(check, "applications", a) == 0
