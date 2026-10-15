"""D14: sent versions are frozen. The status hook of the jobs service (a posting reaching
`applied` by the job page, a bulk change, the undo of a later status, a mail's status change),
what it freezes (the approved version, else the latest rendered one, per document, once), the
rollback with the status transaction, "Mark as sent", `current` and `freeze` for the apply
module (never refusing on content), a frozen version through the routes of D6, isolation between
two people, a lock held by a render, and the canary over the logs."""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.documents import freeze, render_job, store
from app.documents.models import (
    Author,
    BaseSource,
    DocumentGeneration,
    DocumentVersion,
    FitResult,
    FreezeReason,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Origin,
    RenderState,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from tests.documents.canary import find, marker
from tests.documents.conftest import written
from tests.documents.render_support import CV, FakeRenderer, context, give_profile
from tests.documents.store_support import PDF, add_job_file, add_posting, add_user, db
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "db", "engine", "factory", "migrated_url"]

ALEX = "e2e-freeze-alex@example.org"
BLAKE = "e2e-freeze-blake@example.org"
LETTER = written("letter_typical")


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> tuple[str, Any]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def _posting(factory: sessionmaker[Session], user: uuid.UUID, status: str = "found") -> uuid.UUID:
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text, status) VALUES "
                "(:u, :s, 'Lumen Café', 'Platform engineer', "
                "'Lumen is hiring a platform engineer.', CAST(:st AS job_status)) RETURNING id"
            ),
            {"u": user, "s": f"lumen-{uuid.uuid4().hex[:8]}", "st": status},
        ).scalar_one()
        db.commit()
    return found


def _write(
    factory: sessionmaker[Session],
    clock: Any,
    user: uuid.UUID,
    posting: uuid.UUID,
    kind: Kind,
    content: dict[str, Any] | None = None,
    renderer: FakeRenderer | None = None,
    render: bool = True,
) -> uuid.UUID:
    """Save a draft of the posting's CV or letter and render it: a new version when the
    content differs from the latest one. Returns the document."""
    with factory() as db:
        document = store.get_or_create(db, user, posting, kind, at=clock())
        content = content or (CV if kind is Kind.cv else LETTER)
        saved = store.save_draft(
            db, user, document.id, content, document.draft_revision, Author.user, at=clock()
        )
        revision, document_id = saved.draft_revision, document.id
        if render:
            render_job.mark_queued(db, user, document_id, bounded=False)
        db.commit()
    if render:
        ctx = context(factory, renderer or FakeRenderer(), clock())
        asyncio.run(render_job.render_document(ctx, str(document_id), revision))
    return document_id


def _marks(factory: sessionmaker[Session], document: uuid.UUID) -> dict[int, str | None]:
    """number -> the reason it is frozen with (None: not frozen)."""
    with factory() as db:
        rows = db.execute(
            sa.select(DocumentVersion.number, DocumentVersion.frozen_reason).where(
                DocumentVersion.document_id == document
            )
        ).all()
    return {n: reason.value if reason else None for n, reason in sorted(rows)}


def _status(api: Api, token: str, posting: uuid.UUID, status: str) -> Any:
    response = api.request("PATCH", f"/api/jobs/{posting}", token=token, json={"status": status})
    assert response.status_code == 200, response.text
    return response


def _approve(api: Api, token: str, document: uuid.UUID, number: int) -> None:
    response = api.post(f"/api/documents/{document}/versions/{number}/approve", token=token)
    assert response.status_code == 200, response.text


@pytest.fixture
def two(factory: sessionmaker[Session], api: Api, clock: Any) -> dict[str, Any]:
    alex, alex_id = _person(factory, api, ALEX, clock)
    blake, blake_id = _person(factory, api, BLAKE, clock)
    return {"alex": alex, "alex_id": alex_id, "blake": blake, "blake_id": blake_id}


@pytest.fixture
def both(two: dict[str, Any], factory: sessionmaker[Session], clock: Any) -> dict[str, Any]:
    """A posting of Alex with a CV of two rendered versions and a letter of one."""
    posting = _posting(factory, two["alex_id"])
    cv = _write(factory, clock, two["alex_id"], posting, Kind.cv)
    _write(factory, clock, two["alex_id"], posting, Kind.cv, {**CV, "headline": "Second"})
    letter = _write(factory, clock, two["alex_id"], posting, Kind.letter)
    return {**two, "posting": posting, "cv": cv, "letter": letter}


# --- the hook: a posting reaching applied --------------------------------------------------------


def test_applied_by_the_job_page_freezes_the_approved_version_else_the_latest(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session]
) -> None:
    _approve(api, both["alex"], both["cv"], 1)
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    _status(api, both["alex"], both["posting"], "applied")
    assert _marks(factory, both["cv"]) == {
        1: "applied",
        2: None,
    }  # the approved one, not the newest
    assert _marks(factory, both["letter"]) == {1: "applied"}  # no approval: the latest rendered


def test_the_two_documents_freeze_in_the_transaction_of_the_status_change(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session]
) -> None:
    _status(api, both["alex"], both["posting"], "applied")
    with factory() as db:
        job = db.get(Job, both["posting"])
        assert job is not None
        assert job.status is JobStatus.applied
        stamps = {
            v.frozen_at
            for v in db.execute(sa.select(DocumentVersion)).scalars()
            if v.frozen_at is not None
        }
        events = db.execute(
            sa.text(
                "SELECT count(*) FROM job_events WHERE job_id = :j AND data->>'to' = 'applied'"
            ),
            {"j": both["posting"]},
        ).scalar_one()
    assert len(stamps) == 1  # one clock reading for the status and both freezes
    assert events == 1
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    assert _marks(factory, both["letter"]) == {1: "applied"}


def test_a_bulk_change_freezes_every_posting_it_moves(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    other = _posting(factory, both["alex_id"])
    cv = _write(factory, clock, both["alex_id"], other, Kind.cv)
    response = api.post(
        "/api/jobs/bulk",
        token=both["alex"],
        json={"ids": [str(both["posting"]), str(other)], "set": {"status": "applied"}},
    )
    assert response.status_code == 200, response.text
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    assert _marks(factory, both["letter"]) == {1: "applied"}
    assert _marks(factory, cv) == {1: "applied"}


def test_the_undo_of_a_later_status_freezes_and_the_undo_of_applied_leaves_it(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session]
) -> None:
    # The posting was `applied` before its documents existed (no hook ran for them).
    with factory() as db:
        db.execute(
            sa.text("UPDATE jobs SET status = 'applied' WHERE id = :j"), {"j": both["posting"]}
        )
        db.commit()
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    moved = api.post(
        "/api/jobs/bulk",
        token=both["alex"],
        json={"ids": [str(both["posting"])], "set": {"status": "interview"}},
    )
    token = moved.json()["undo_token"]
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    undone = api.post("/api/jobs/bulk/undo", token=both["alex"], json={"token": token})
    assert undone.status_code == 200, undone.text
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    assert _marks(factory, both["letter"]) == {1: "applied"}
    # The undo of `applied` itself: the versions stay frozen.
    moved = api.post(
        "/api/jobs/bulk",
        token=both["alex"],
        json={"ids": [str(both["posting"])], "set": {"status": "found"}},
    )
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    undone = api.post(
        "/api/jobs/bulk/undo", token=both["alex"], json={"token": moved.json()["undo_token"]}
    )
    assert undone.status_code == 200, undone.text
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}


def test_a_status_that_returns_to_applied_does_not_freeze_a_later_edit(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    _status(api, both["alex"], both["posting"], "applied")
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, {**CV, "headline": "Third"})
    _status(api, both["alex"], both["posting"], "interview")
    _status(api, both["alex"], both["posting"], "applied")
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied", 3: None}


def test_an_undo_back_to_applied_does_not_freeze_a_later_approved_edit(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    # SPEC-documents.md, "Versions and freezing": the owner's reading of "becoming applied".
    _status(api, both["alex"], both["posting"], "applied")
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, {**CV, "headline": "Third"})
    _approve(api, both["alex"], both["cv"], 3)
    moved = api.post(
        "/api/jobs/bulk",
        token=both["alex"],
        json={"ids": [str(both["posting"])], "set": {"status": "interview"}},
    )
    undone = api.post(
        "/api/jobs/bulk/undo", token=both["alex"], json={"token": moved.json()["undo_token"]}
    )
    assert undone.status_code == 200, undone.text
    with factory() as db:
        job = db.get(Job, both["posting"])
        assert job is not None
        assert job.status is JobStatus.applied
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied", 3: None}
    assert _marks(factory, both["letter"]) == {1: "applied"}


def test_a_status_change_of_the_mail_module_freezes_in_its_transaction(
    both: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        job = jobs_service.lock_job(db, both["alex_id"], both["posting"])
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.applied, clock(), via="mail")
        db.commit()
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    assert _marks(factory, both["letter"]) == {1: "applied"}


def test_no_rendered_version_freezes_nothing_and_fails_nothing(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    empty = _posting(factory, two["alex_id"])
    _status(api, two["alex"], empty, "applied")  # no document at all
    drafted = _posting(factory, two["alex_id"])
    cv = _write(factory, clock, two["alex_id"], drafted, Kind.cv, render=False)  # a draft only
    with factory() as db:  # a version row that never received its PDF
        store.snapshot(db, two["alex_id"], cv, Origin.edited, at=clock())
        db.commit()
    _status(api, two["alex"], drafted, "applied")
    assert _marks(factory, cv) == {1: None}


def test_a_rollback_of_the_status_transaction_rolls_the_freeze_back(
    both: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        job = jobs_service.lock_job(db, both["alex_id"], both["posting"])
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.applied, clock())
        assert _marks_in(db, both["cv"]) == {1: None, 2: "applied"}  # frozen inside
        db.rollback()
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    with factory() as db:
        job = db.get(Job, both["posting"])
        assert job is not None
        assert job.status is not JobStatus.applied


def _marks_in(db: Session, document: uuid.UUID) -> dict[int, str | None]:
    rows = db.execute(
        sa.select(DocumentVersion.number, DocumentVersion.frozen_reason).where(
            DocumentVersion.document_id == document
        )
    ).all()
    return {n: reason.value if reason else None for n, reason in sorted(rows)}


def test_a_failure_after_the_hook_takes_the_freeze_with_the_status(
    both: dict[str, Any],
    factory: sessionmaker[Session],
    clock: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(db: Session, job: Job, previous: JobStatus, status: JobStatus, at: datetime) -> None:
        raise RuntimeError("a later callback fails")

    monkeypatch.setattr(jobs_service, "_status_callbacks", [freeze.on_status, boom])
    with factory() as db:
        job = jobs_service.lock_job(db, both["alex_id"], both["posting"])
        assert job is not None
        with pytest.raises(RuntimeError):
            jobs_service.set_status(db, job, JobStatus.applied, clock())
        db.rollback()
    assert _marks(factory, both["cv"]) == {1: None, 2: None}


def test_a_render_holding_the_document_makes_the_status_change_busy_not_lost(
    both: dict[str, Any],
    api: Api,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "LOCK_TIMEOUT_MS", 30)
    with factory() as holder:
        store.lock_document(holder, both["alex_id"], both["cv"])  # a render attaching its PDF
        busy = api.request(
            "PATCH", f"/api/jobs/{both['posting']}", token=both["alex"], json={"status": "applied"}
        )
        assert busy.status_code == 409
        holder.rollback()
    with factory() as db:
        job = db.get(Job, both["posting"])
        assert job is not None
        assert job.status is JobStatus.cv_ready  # not applied: nothing half done
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    _status(api, both["alex"], both["posting"], "applied")
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}


def _apply_while_rendering(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], approve: int | None
) -> tuple[FakeRenderer, dict[str, Any]]:
    """A renderer whose call is the window between the render's claim (committed: the version
    is there, its PDF is not) and the store of its PDF. Another session, the person's, approves
    a version in that window and moves the posting to `applied`; `seen` keeps the marks then."""
    seen: dict[str, Any] = {}

    def person() -> None:
        if approve is not None:
            _approve(api, both["alex"], both["cv"], approve)
        _status(api, both["alex"], both["posting"], "applied")
        seen["cv"], seen["letter"] = _marks(factory, both["cv"]), _marks(factory, both["letter"])

    return FakeRenderer(during=person), seen


def test_the_approved_version_still_rendering_is_frozen_when_its_render_lands(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    renderer, seen = _apply_while_rendering(both, api, factory, approve=3)
    third = {**CV, "headline": "Third"}
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, third, renderer)
    # In the window: nothing older frozen in place of the approved version, the letter frozen.
    assert seen == {"cv": {1: None, 2: None, 3: None}, "letter": {1: "applied"}}
    assert _marks(factory, both["cv"]) == {1: None, 2: None, 3: "applied"}
    with factory() as db:
        landed = store.version(db, both["alex_id"], both["cv"], 3)
        assert landed is not None
        assert landed.pdf_sha256 is not None
        assert landed.approved_at is not None
        row = store.get(db, both["alex_id"], both["cv"])
        assert row is not None
        assert row.render_state is RenderState.idle
    current = _current(factory, both)
    assert current.cv.version is not None
    assert (current.cv.version.number, current.cv.version.frozen) == (3, True)


def test_without_an_approval_a_render_in_flight_changes_nothing_of_the_freeze(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    renderer, seen = _apply_while_rendering(both, api, factory, approve=None)
    third = {**CV, "headline": "Third"}
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, third, renderer)
    assert seen["cv"] == {1: None, 2: "applied", 3: None}  # the latest rendered, at once
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied", 3: None}


def test_an_approved_older_version_freezes_at_once_while_a_newer_one_renders(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    renderer, seen = _apply_while_rendering(both, api, factory, approve=1)
    third = {**CV, "headline": "Third"}
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, third, renderer)
    assert seen["cv"] == {1: "applied", 2: None, 3: None}
    assert _marks(factory, both["cv"]) == {1: "applied", 2: None, 3: None}


def test_an_approved_version_whose_render_failed_is_frozen_by_the_render_that_lands(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    renderer, seen = _apply_while_rendering(both, api, factory, approve=3)
    renderer.fail = "render_failed"
    third = {**CV, "headline": "Third"}
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, third, renderer)
    assert seen["cv"] == {1: None, 2: None, 3: None}
    assert _marks(factory, both["cv"]) == {1: None, 2: None, 3: None}  # nothing to send yet
    # "Try again": the same draft renders the same version, and its PDF freezes it.
    _write(factory, clock, both["alex_id"], both["posting"], Kind.cv, third)
    assert _marks(factory, both["cv"]) == {1: None, 2: None, 3: "applied"}


def test_a_render_that_lands_after_applied_with_nothing_approved_freezes_nothing(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    posting = _posting(factory, two["alex_id"])
    _status(api, two["alex"], posting, "applied")
    cv = _write(factory, clock, two["alex_id"], posting, Kind.cv)
    assert _marks(factory, cv) == {1: None}


def _current(factory: sessionmaker[Session], both: dict[str, Any]) -> freeze.Current:
    with factory() as db:
        return freeze.current(db, both["alex_id"], both["posting"])


def test_a_waiting_generation_is_left_alone_by_the_freeze(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        generation = store.create_generation(
            db,
            both["alex_id"],
            both["cv"],
            GenerationMode.posting,
            GenerationTrigger.auto,
            at=clock(),
            estimate={},
            token_ceiling=1000,
            status=GenerationStatus.waiting,
            wait_reason=GenerationWait.consent,
        )
        db.commit()
        generation_id = generation.id
    _status(api, both["alex"], both["posting"], "applied")
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}
    with factory() as db:
        kept = db.get(DocumentGeneration, generation_id)
        assert kept is not None
        assert (kept.status, kept.wait_reason) == (GenerationStatus.waiting, GenerationWait.consent)


# --- Mark as sent --------------------------------------------------------------------------------


def test_mark_sent_freezes_once_and_twice_changes_nothing(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session]
) -> None:
    url = f"/api/documents/{both['cv']}/versions/1/mark-sent"
    first = api.post(url, token=both["alex"])
    assert first.status_code == 200, first.text
    assert first.json()["frozen_reason"] == "marked_sent"
    assert first.headers["cache-control"] == "private, no-store"
    again = api.post(url, token=both["alex"])
    assert again.status_code == 200
    assert again.json()["frozen_at"] == first.json()["frozen_at"]
    assert _marks(factory, both["cv"]) == {1: "marked_sent", 2: None}
    with factory() as db:  # the status did not move
        job = db.get(Job, both["posting"])
        assert job is not None
        assert job.status is not JobStatus.applied
    # Applied later: the document already has its sent version, nothing else freezes.
    _status(api, both["alex"], both["posting"], "applied")
    assert _marks(factory, both["cv"]) == {1: "marked_sent", 2: None}
    assert _marks(factory, both["letter"]) == {1: "applied"}


def _bare_version(
    factory: sessionmaker[Session], clock: Any, user: uuid.UUID, posting: uuid.UUID
) -> uuid.UUID:
    """A CV whose only version never received its PDF (a render that has not finished)."""
    document = _write(factory, clock, user, posting, Kind.cv, render=False)
    with factory() as db:
        store.snapshot(db, user, document, Origin.edited, at=clock())
        db.commit()
    return document


def test_mark_sent_refusals(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    def post(token: str, document: uuid.UUID, number: int) -> Any:
        return api.post(f"/api/documents/{document}/versions/{number}/mark-sent", token=token)

    assert post(both["alex"], both["cv"], 9).status_code == 404  # no such version
    assert post(both["blake"], both["cv"], 1).status_code == 404  # another person's
    assert post(both["alex"], uuid.uuid4(), 1).status_code == 404
    assert post(both["alex"], both["cv"], 0).status_code == 422
    bare = _bare_version(factory, clock, both["alex_id"], _posting(factory, both["alex_id"]))
    refused = post(both["alex"], bare, 1)
    assert refused.status_code == 409  # nothing to have been sent
    assert refused.headers["cache-control"] == "private, no-store"
    assert _marks(factory, bare) == {1: None}
    with factory() as db:  # a base CV is not something that is sent
        base = store.create_base(
            db, both["alex_id"], "Main", BaseSource.built, at=clock(), content=CV, limit=10
        )
        store.snapshot(db, both["alex_id"], base.id, Origin.edited, at=clock())
        db.commit()
        base_id = base.id
    assert post(both["alex"], base_id, 1).status_code == 404
    assert _marks(factory, base_id) == {1: None}


# --- current and freeze for the apply module -----------------------------------------------------


def test_current_is_the_approved_version_else_the_latest_rendered(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        now = freeze.current(db, both["alex_id"], both["posting"])
    assert now.cv.version is not None
    assert now.cv.version.number == 2
    assert now.letter.version is not None
    assert now.letter.version.number == 1
    assert (now.cv.coming, now.letter.coming) == (None, None)
    assert now.cv.version.fit is FitResult.one_page
    _approve(api, both["alex"], both["cv"], 1)
    with factory() as db:
        approved = freeze.current(db, both["alex_id"], both["posting"]).cv.version
    assert approved is not None
    assert (approved.number, approved.approved) == (1, True)
    # A newer version without a PDF is not a candidate.
    _write(
        factory,
        clock,
        both["alex_id"],
        both["posting"],
        Kind.letter,
        {**LETTER, "subject": "Not rendered"},
        render=False,
    )
    with factory() as db:
        store.snapshot(db, both["alex_id"], both["letter"], Origin.edited, at=clock())
        db.commit()
        letter = freeze.current(db, both["alex_id"], both["posting"]).letter.version
    assert letter is not None
    assert letter.number == 1


def test_current_says_why_a_document_is_not_there_yet(
    two: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    posting = _posting(factory, two["alex_id"])
    with factory() as db:
        assert freeze.current(db, two["alex_id"], posting) == freeze.Current(
            cv=freeze.Slot(None, None), letter=freeze.Slot(None, None)
        )
        cv = store.get_or_create(db, two["alex_id"], posting, Kind.cv, at=clock())
        store.create_generation(
            db,
            two["alex_id"],
            cv.id,
            GenerationMode.posting,
            GenerationTrigger.auto,
            at=clock(),
            estimate={},
            token_ceiling=1000,
            status=GenerationStatus.waiting,
            wait_reason=GenerationWait.credential,
        )
        letter = store.get_or_create(db, two["alex_id"], posting, Kind.letter, at=clock())
        letter.render_state = RenderState.queued
        db.flush()
        now = freeze.current(db, two["alex_id"], posting)
        db.rollback()
    assert now.cv == freeze.Slot(None, "credential")
    assert now.letter == freeze.Slot(None, freeze.RENDERING)


def test_freeze_for_apply_never_refuses_on_content_and_returns_fit_and_corrections(
    two: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    posting = _posting(factory, two["alex_id"])
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][0] += " 漢字"  # a correction is recorded
    cv = _write(factory, clock, two["alex_id"], posting, Kind.cv, content, FakeRenderer(pages=2))
    with factory() as db:
        version = freeze.current(db, two["alex_id"], posting).cv.version
        assert version is not None
        assert version.fit is FitResult.overflow
        sent = freeze.freeze(db, two["alex_id"], version.version_id, FreezeReason.apply, clock())
        db.commit()
    assert sent.fit is FitResult.overflow
    assert sent.pages == 2
    assert sent.corrections
    assert sent.frozen
    assert sent.frozen_reason is FreezeReason.apply
    assert _marks(factory, cv) == {1: "apply"}
    # Freezing again, or by the status, changes nothing: the first reason is the fact kept.
    with factory() as db:
        again = freeze.freeze(
            db, two["alex_id"], sent.version_id, FreezeReason.marked_sent, clock()
        )
        assert again.frozen_reason is FreezeReason.apply
        assert again.frozen_at == sent.frozen_at
        job = jobs_service.lock_job(db, two["alex_id"], posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.applied, clock(), via="apply")
        db.commit()
    assert _marks(factory, cv) == {1: "apply"}


def test_freeze_is_the_persons_own_posting_documents_only(
    both: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        version = freeze.current(db, both["alex_id"], both["posting"]).cv.version
        assert version is not None
        with pytest.raises(store.NotFoundError):
            freeze.freeze(db, both["blake_id"], version.version_id, FreezeReason.apply, clock())
        with pytest.raises(store.NotFoundError):
            freeze.freeze(db, both["alex_id"], uuid.uuid4(), FreezeReason.apply, clock())
        theirs = freeze.current(db, both["blake_id"], both["posting"])
        assert theirs.cv == freeze.Slot(None, None)
        assert theirs.letter == freeze.Slot(None, None)
        assert freeze.pdf(db, both["blake_id"], version.version_id) is None
        db.rollback()
    assert _marks(factory, both["cv"]) == {1: None, 2: None}


def test_pdf_on_demand_stored_or_the_command_lines_file(
    both: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        version = freeze.current(db, both["alex_id"], both["posting"]).cv.version
        assert version is not None
        stored = freeze.pdf(db, both["alex_id"], version.version_id)
        assert stored is not None
        assert stored.startswith(b"%PDF")
        file_id = add_job_file(db, both["posting"])
        legacy, made = store.snapshot(
            db,
            both["alex_id"],
            both["cv"],
            Origin.legacy,
            at=clock(),
            legacy_file_id=file_id,
        )
        assert made
        assert freeze.pdf(db, both["alex_id"], legacy.id) == PDF
        assert freeze.rendered(legacy)
        db.rollback()


# --- a frozen version through the routes of D6 ---------------------------------------------------


def _frozen_state(
    factory: sessionmaker[Session], document: uuid.UUID
) -> dict[int, tuple[Any, ...]]:
    with factory() as db:
        rows = db.execute(
            sa.select(
                DocumentVersion.number,
                DocumentVersion.content_hash,
                DocumentVersion.pdf_sha256,
                DocumentVersion.template_slug,
                DocumentVersion.language,
                DocumentVersion.fit,
                DocumentVersion.corrections,
                DocumentVersion.frozen_at,
                DocumentVersion.approved_at,
            ).where(DocumentVersion.document_id == document, DocumentVersion.frozen_at.is_not(None))
        ).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def test_a_frozen_version_does_not_change_through_any_route_of_d6(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    token, document = both["alex"], both["cv"]
    _status(api, token, both["posting"], "applied")  # freezes v2
    before = _frozen_state(factory, document)
    assert list(before) == [2]

    def save(content: dict[str, Any]) -> Any:
        found = api.get(f"/api/documents/{document}", token=token).json()
        return api.request(
            "PUT",
            f"/api/documents/{document}/draft",
            token=token,
            json=content,
            headers={"If-Match": str(found["draft_revision"])},
        )

    # The draft equals the frozen latest version: a render keeps that version as it is.
    assert api.post(f"/api/documents/{document}/render", token=token).status_code == 202
    assert api.post(f"/api/documents/{document}/versions/2/approve", token=token).status_code == 409
    assert (
        api.post(f"/api/documents/{document}/versions/2/mark-sent", token=token).status_code == 200
    )
    assert _frozen_state(factory, document) == before
    # An edit, a restore and a template switch make new versions; the frozen one stays.
    assert save({**CV, "headline": "After the send"}).status_code == 200
    ctx = context(factory, FakeRenderer(), clock())
    asyncio.run(render_job.render_document(ctx, str(document), 3))
    restored = api.post(f"/api/documents/{document}/versions/2/restore", token=token)
    assert restored.status_code == 200
    switched = api.request(
        "PATCH", f"/api/documents/{document}", token=token, json={"template_slug": "sidebar"}
    )
    assert switched.status_code == 200, switched.text
    assert _frozen_state(factory, document) == before
    listed = api.get(f"/api/documents/{document}/versions", token=token).json()
    assert [v["number"] for v in listed][-1] == 1
    assert len(listed) >= 3
    assert next(v for v in listed if v["number"] == 2)["frozen_reason"] == "applied"
    pdf = api.get(f"/api/documents/{document}/versions/2/pdf", token=token)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def _unfreeze_all(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        db.execute(sa.text("UPDATE document_versions SET frozen_at = NULL, frozen_reason = NULL"))
        db.flush()


def test_the_trigger_refuses_what_the_code_does_not_attempt(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session]
) -> None:
    _status(api, both["alex"], both["posting"], "applied")
    with pytest.raises(sa.exc.DBAPIError):
        _unfreeze_all(factory)
    assert _marks(factory, both["cv"]) == {1: None, 2: "applied"}


# --- registry of the jobs service ----------------------------------------------------------------


def test_status_callbacks_run_in_order_once_per_change_and_not_for_a_non_change(
    db: Session, clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, JobStatus, JobStatus]] = []

    def first(
        session: Session, job: Job, previous: JobStatus, status: JobStatus, at: datetime
    ) -> None:
        seen.append(("first", previous, status))

    def second(
        session: Session, job: Job, previous: JobStatus, status: JobStatus, at: datetime
    ) -> None:
        seen.append(("second", previous, status))

    monkeypatch.setattr(jobs_service, "_status_callbacks", [])
    jobs_service.register_status_callback(first)
    jobs_service.register_status_callback(second)
    jobs_service.register_status_callback(first)  # again: nothing changes
    user = add_user(db, "e2e-freeze-registry@example.org")
    posting = add_posting(db, user)
    job = jobs_service.lock_job(db, user, posting)
    assert job is not None
    assert jobs_service.set_status(db, job, JobStatus.shortlisted, clock())
    assert not jobs_service.set_status(db, job, JobStatus.shortlisted, clock())
    assert seen == [
        ("first", JobStatus.found, JobStatus.shortlisted),
        ("second", JobStatus.found, JobStatus.shortlisted),
    ]
    db.rollback()


def test_the_hook_is_registered_by_importing_the_module_and_only_once() -> None:
    assert jobs_service._status_callbacks.count(freeze.on_status) == 1


def test_the_worker_imports_the_module_so_the_hook_runs_there_too() -> None:
    assert "app.documents.freeze" in worker.registration_modules()


# --- privacy -------------------------------------------------------------------------------------


def test_nothing_of_a_document_reaches_the_logs_when_it_is_frozen(
    two: dict[str, Any],
    api: Api,
    factory: sessionmaker[Session],
    clock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = marker()
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][0] += f" {secret}"
    posting = _posting(factory, two["alex_id"])
    cv = _write(factory, clock, two["alex_id"], posting, Kind.cv, content)
    caplog.set_level(logging.DEBUG)
    api.post(f"/api/documents/{cv}/versions/1/mark-sent", token=two["alex"])
    _status(api, two["alex"], posting, "applied")
    with factory() as db:
        version = freeze.current(db, two["alex_id"], posting).cv.version
        assert version is not None
        freeze.freeze(db, two["alex_id"], version.version_id, FreezeReason.apply, clock())
        db.rollback()
    assert any(r.name == "app.documents" for r in caplog.records)
    assert find([secret], logs=caplog.records) == []


# --- another person -----------------------------------------------------------------------------


def test_another_persons_status_change_freezes_nothing_of_mine(
    both: dict[str, Any], api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    theirs = _posting(factory, both["blake_id"])
    _write(factory, clock, both["blake_id"], theirs, Kind.cv)
    response = api.request(
        "PATCH", f"/api/jobs/{both['posting']}", token=both["blake"], json={"status": "applied"}
    )
    assert response.status_code == 404
    _status(api, both["blake"], theirs, "applied")
    assert _marks(factory, both["cv"]) == {1: None, 2: None}
    assert _marks(factory, both["letter"]) == {1: None}
