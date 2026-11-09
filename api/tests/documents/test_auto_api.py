"""D10: "Write now" and the spend through the app, two e2e people. A click under the threshold
(written), with today's ceiling reached (written, not counted), from another base CV, in place of
an automatic generation that waited (which gives way), while one runs (409), without a credential
(waiting, started by the next tick); another person's posting and base CV (404); the spend of the
module's runs only, never another person's, `?min_fit=` over the last 30 days only, and what a
lower threshold would add (D13's card shows it before the person saves it)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.documents import generate, store, tick
from app.documents.models import (
    Author,
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Origin,
)
from app.jobs import service as jobs_service
from tests.agents.conftest import API_KEY, new_key
from tests.conftest import make_settings
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-auto-alex@example.org"
BLAKE = "e2e-docs-auto-blake@example.org"
SPEND = "/api/documents/spend"


def write(job: uuid.UUID) -> str:
    return f"/api/jobs/{job}/documents/write"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


class Who:
    def __init__(self, factory: sessionmaker[Session], api: Api, email: str, clock: Any):
        self.factory, self.clock = factory, clock
        self.user = make_user(factory, email=email, name=email.split("@")[0]).id
        give_profile(factory, self.user, clock())
        self.token = api.login_token(email)
        with factory() as db:
            self.base = store.create_base(
                db,
                self.user,
                "My CV",
                BaseSource.profile,
                at=clock(),
                content=load("base_cv_alex.json"),
            ).id
            db.commit()

    def credential(self) -> None:
        with self.factory() as db:
            keyring = Keyring.from_text(new_key())
            credentials.save(
                db, keyring, self.user, CredentialKind.anthropic_api_key, API_KEY, self.clock()
            )
            db.commit()

    def posting(self, fit: int | None, days_ago: int = 0) -> uuid.UUID:
        with self.factory() as db:
            made = db.execute(
                sa.text(
                    "INSERT INTO jobs (user_id, slug, company, title, posting_text, fit, found_on)"
                    " VALUES (:u, :s, 'Example Corp', 'Platform engineer', 'Example text.', :f,"
                    " :d) RETURNING id"
                ),
                {
                    "u": self.user,
                    "s": f"example-{uuid.uuid4().hex[:8]}",
                    "f": fit,
                    "d": self.clock().date() - timedelta(days=days_ago),
                },
            ).scalar_one()
            db.commit()
            return made

    def generations(self, job: uuid.UUID) -> list[tuple[Kind, GenerationStatus, Any, Any]]:
        with self.factory() as db:
            return [
                tuple(row)  # type: ignore[misc]
                for row in db.execute(
                    sa.select(
                        Document.kind,
                        DocumentGeneration.status,
                        DocumentGeneration.trigger,
                        DocumentGeneration.base_document_id,
                    )
                    .join(Document, Document.id == DocumentGeneration.document_id)
                    .where(Document.posting_id == job)
                    .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
                )
            ]


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Who:
    who = Who(factory, api, ALEX, clock)
    who.credential()
    return who


def test_a_click_under_the_threshold_writes_the_cv_and_the_letter_follows(
    alex: Who, api: Api, queue: RecordingQueue
) -> None:
    job = alex.posting(40)
    response = api.request("POST", write(job), token=alex.token, json={"kinds": ["cv", "letter"]})
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()["generations"]
    assert [(g["kind"], g["status"]) for g in body] == [("cv", "running")]
    assert [j["function"] for j in queue.jobs] == ["run_agent"]
    assert alex.generations(job) == [
        (Kind.cv, GenerationStatus.running, GenerationTrigger.click, alex.base)
    ]


def test_a_click_for_both_kinds_marks_the_cv_so_the_letter_follows_it_and_one_kind_does_not(
    alex: Who, api: Api, factory: sessionmaker[Session]
) -> None:
    both, one = alex.posting(40), alex.posting(41)
    for job, kinds in ((both, ["letter", "cv"]), (one, ["cv"])):
        response = api.request("POST", write(job), token=alex.token, json={"kinds": kinds})
        assert response.status_code == 202, response.text
    with factory() as db:
        marks = dict(
            db.execute(
                sa.select(Document.posting_id, DocumentGeneration.estimate)
                .join(Document, Document.id == DocumentGeneration.document_id)
                .where(DocumentGeneration.user_id == alex.user)
            ).all()
        )
    assert marks[both].get(generate.LETTER_FOLLOWS) is True
    assert generate.LETTER_FOLLOWS not in marks[one]
    assert [g[0] for g in alex.generations(both)] == [Kind.cv]  # the letter waits for the CV


def test_a_recorded_stream_named_by_the_click_is_the_writers_and_the_letters(
    alex: Who,
    api: Api,
    factory: sessionmaker[Session],
    clock: Any,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(migrated_url, agents_fake_backend=True)
    monkeypatch.setattr(generate, "get_settings", lambda: settings)
    job = alex.posting(80)
    response = api.request(
        "POST", write(job), token=alex.token, json={"kinds": ["cv", "letter"], "script": "journey"}
    )
    assert response.status_code == 202, response.text
    with factory() as db:
        cv = db.execute(sa.select(DocumentGeneration)).scalar_one()
        cv.status = GenerationStatus.done  # the CV is written: the letter follows
        ran = db.get(AgentRun, cv.run_id)
        assert ran is not None
        ran.input_tokens, ran.output_tokens = 0, 0  # the counts the runner writes
        db.commit()
        assert db.get(AgentRun, cv.run_id).input["script"] == "documents_cv_journey"  # type: ignore[union-attr]
        assert cv.estimate[generate.LETTER_SCRIPT] == "documents_letter_journey"
    run = generate.start_letter(factory, cv.run_id, clock(), settings)  # type: ignore[arg-type]
    assert run is not None
    with factory() as db:
        assert db.get(AgentRun, run).input["script"] == "documents_letter_journey"  # type: ignore[union-attr]
        letter = db.execute(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == run)
        ).scalar_one()
        assert generate.LETTER_SCRIPT not in letter.estimate


def test_a_click_from_another_base_cv_names_it(alex: Who, api: Api) -> None:
    with alex.factory() as db:
        other = store.create_base(db, alex.user, "Other", BaseSource.built, at=alex.clock()).id
        db.commit()
    job = alex.posting(80)
    response = api.request(
        "POST", write(job), token=alex.token, json={"kinds": ["cv"], "base_document_id": str(other)}
    )
    assert response.status_code == 202, response.text
    assert alex.generations(job)[0][3] == other


def test_a_click_waits_behind_a_render_holding_the_posting_and_both_commit(
    alex: Who, api: Api, factory: sessionmaker[Session], engine: sa.Engine, clock: Any
) -> None:
    """D6's render stores a one page CV under the posting's lock, then the CV's (`_store_once`).
    A click on that CV, which has a version, once locked the CV, then needed the posting for its
    run's row: with the render between the two, a circle Postgres broke. The click now takes the
    posting first."""
    job = alex.posting(40)
    with factory() as db:
        cv = store.get_or_create(db, alex.user, job, Kind.cv, at=clock())
        store.snapshot(db, alex.user, cv.id, Origin.edited, at=clock())
        db.commit()
        cv_id = cv.id
    outcome: dict[str, Any] = {}
    with factory() as render:
        assert jobs_service.lock_job(render, alex.user, job) is not None
        thread = in_thread(
            lambda: api.request("POST", write(job), token=alex.token, json={"kinds": ["cv"]}),
            outcome,
        )
        wait_until_blocked(engine, [thread], 1)
        store.lock_document(render, alex.user, cv_id)
        store.commit(render)
    thread.join()
    assert "error" not in outcome, outcome.get("error")
    response = outcome["result"]
    assert response.status_code == 202, response.text
    assert [(g["kind"], g["status"]) for g in response.json()["generations"]] == [("cv", "running")]


def test_without_a_credential_a_click_waits_and_the_tick_starts_it(
    factory: sessionmaker[Session],
    api: Api,
    clock: Any,
    queue: RecordingQueue,
    migrated_url: str,
) -> None:
    who = Who(factory, api, ALEX, clock)
    job = who.posting(10)
    response = api.request("POST", write(job), token=who.token, json={"kinds": ["cv"]})
    assert response.status_code == 202, response.text
    assert response.json()["generations"][0]["wait_reason"] == GenerationWait.credential.value
    assert queue.jobs == []
    who.credential()
    settings = make_settings(migrated_url, agents_fake_backend=True)
    runs = tick.tick(factory, clock(), settings, only=[who.user])
    assert len(runs) == 1
    assert alex_status(who, job) == [GenerationStatus.running]


def alex_status(who: Who, job: uuid.UUID) -> list[GenerationStatus]:
    return [g[1] for g in who.generations(job)]


def test_another_persons_posting_and_base_cv_answer_404(
    alex: Who, api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    blake = Who(factory, api, BLAKE, clock)
    theirs = blake.posting(90)
    response = api.request("POST", write(theirs), token=alex.token, json={"kinds": ["cv"]})
    assert response.status_code == 404
    mine = alex.posting(90)
    response = api.request(
        "POST",
        write(mine),
        token=alex.token,
        json={"kinds": ["cv"], "base_document_id": str(blake.base)},
    )
    assert response.status_code == 404
    assert blake.generations(theirs) == []
    assert alex.generations(mine) == []
    bad = api.request("POST", write(mine), token=alex.token, json={"kinds": []})
    assert bad.status_code == 422


# Around both midnights: UTC, and Paris (summer, UTC+2, and winter, UTC+1). `found_on` is the UTC
# date of the clock, so a posting found 29 days ago counts at every one of these instants.
BOUNDARIES = [
    datetime(2026, 10, 14, 21, 59, 59, tzinfo=UTC),  # 23:59:59 in Paris
    datetime(2026, 10, 14, 22, 0, 0, tzinfo=UTC),  # midnight in Paris, the 15th there
    datetime(2026, 10, 14, 23, 59, 59, tzinfo=UTC),
    datetime(2026, 10, 15, 0, 0, 0, tzinfo=UTC),
    datetime(2026, 1, 14, 22, 59, 59, tzinfo=UTC),  # 23:59:59 in Paris, winter
    datetime(2026, 1, 14, 23, 0, 0, tzinfo=UTC),  # midnight in Paris, winter
    datetime(2026, 3, 28, 23, 30, 0, tzinfo=UTC),  # the night the clocks change
]


def test_writing_the_cv_again_from_another_base_cv_takes_the_written_letter_along(
    alex: Who, api: Api
) -> None:
    """D12 "Write it again from this CV" sends both kinds: the CV is written now from the base CV
    chosen and marked so the letter follows it (generate.start_letter), even though the letter has
    a version; nothing writes the letter from the old CV in between."""
    job = alex.posting(80)
    with alex.factory() as db:
        other = store.create_base(db, alex.user, "Other", BaseSource.built, at=alex.clock()).id
        for kind, content in ((Kind.cv, "cv_typical"), (Kind.letter, "letter_typical")):
            document = store.get_or_create(db, alex.user, job, kind, at=alex.clock())
            store.save_draft(
                db,
                alex.user,
                document.id,
                load(f"written/{content}.json"),
                document.draft_revision,
                Author.user,
                at=alex.clock(),
            )
            store.snapshot(db, alex.user, document.id, Origin.edited, at=alex.clock())
        db.commit()
    body = {"kinds": ["cv", "letter"], "base_document_id": str(other)}
    response = api.request("POST", write(job), token=alex.token, json=body)
    assert response.status_code == 202, response.text
    assert [g["kind"] for g in response.json()["generations"]] == ["cv"]
    [(kind, _, trigger, base)] = alex.generations(job)
    assert (kind, trigger, base) == (Kind.cv, GenerationTrigger.click, other)
    with alex.factory() as db:
        estimate = db.execute(
            sa.select(DocumentGeneration.estimate).where(DocumentGeneration.user_id == alex.user)
        ).scalar_one()
    assert estimate.get(generate.LETTER_FOLLOWS) is True
    # While that CV is written, a second click is refused rather than written from the old CV.
    again = api.request("POST", write(job), token=alex.token, json=body)
    assert again.status_code == 409
