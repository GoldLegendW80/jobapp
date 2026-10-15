"""D10: "Write now" and the spend through the app, two e2e people. A click under the threshold
(written), with today's ceiling reached (written, not counted), from another base CV, in place of
an automatic generation that waited (which gives way), while one runs (409), without a credential
(waiting, started by the next tick); another person's posting and base CV (404); the spend of the
module's runs only, never another person's, `?min_fit=` over the last 30 days only, and what a
lower threshold would add (D13's card shows it before the person saves it)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.documents import consent, estimate, generate, store, tick
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
)
from tests.agents.conftest import API_KEY, new_key
from tests.conftest import make_settings
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
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
            consent.record(db, self.user, clock())
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


@pytest.mark.parametrize("script", ["", "Journey", "journey-1", "journey one", "a" * 31])
def test_a_script_that_is_not_a_stream_suffix_is_refused(alex: Who, api: Api, script: str) -> None:
    job = alex.posting(80)
    response = api.request(
        "POST", write(job), token=alex.token, json={"kinds": ["cv"], "script": script}
    )
    assert response.status_code == 422


def test_a_click_with_the_ceiling_reached_is_written_and_never_counted(
    alex: Who, api: Api, factory: sessionmaker[Session], clock: Any, migrated_url: str
) -> None:
    with factory() as db:
        db.execute(
            sa.text(
                "UPDATE profiles SET sections = jsonb_set(sections, '{search}', "
                "CAST(:s AS jsonb)) WHERE user_id = :u"
            ),
            {"s": '{"documents": {"daily_ceiling": 1}}', "u": alex.user},
        )
        db.commit()
    auto = alex.posting(90)
    settings = make_settings(migrated_url, agents_fake_backend=True)
    tick.tick(factory, clock(), settings, only=[alex.user])
    clicked = alex.posting(90)
    response = api.request("POST", write(clicked), token=alex.token, json={"kinds": ["cv"]})
    assert response.status_code == 202, response.text
    assert response.json()["generations"][0]["status"] == "running"
    spent = api.request("GET", SPEND, token=alex.token).json()
    assert spent["written_today"] == 1  # the automatic one only
    assert alex.generations(auto)[0][2] is GenerationTrigger.auto


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


def test_an_automatic_generation_that_waits_gives_way_to_a_click_and_a_running_one_is_409(
    alex: Who, api: Api, factory: sessionmaker[Session], clock: Any, migrated_url: str
) -> None:
    first, second = alex.posting(90), alex.posting(85)
    settings = make_settings(migrated_url, agents_fake_backend=True)
    tick.tick(factory, clock(), settings, only=[alex.user])
    assert [g[1] for g in alex.generations(second)] == [GenerationStatus.queued]
    response = api.request("POST", write(second), token=alex.token, json={"kinds": ["cv"]})
    assert response.status_code == 202, response.text
    # one frozen clock: both rows have the same created_at, so compare them as a set
    assert {g[1:3] for g in alex.generations(second)} == {
        (GenerationStatus.cancelled, GenerationTrigger.auto),
        (GenerationStatus.running, GenerationTrigger.click),
    }
    busy = api.request("POST", write(first), token=alex.token, json={"kinds": ["cv"]})
    assert busy.status_code == 409


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


def test_the_spend_counts_the_modules_runs_only_and_never_another_persons(
    alex: Who, api: Api, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    blake = Who(factory, api, BLAKE, clock)
    blake.credential()
    for who in (alex, blake):
        job = who.posting(10)
        response = api.request("POST", write(job), token=who.token, json={"kinds": ["cv"]})
        assert response.status_code == 202, response.text
    with factory() as db:
        db.execute(
            sa.text(
                "UPDATE agent_runs SET input_tokens = 1000, output_tokens = 200, cost_usd = 0.5 "
                "WHERE user_id = :u"
            ),
            {"u": alex.user},
        )
        db.execute(
            sa.text(
                "UPDATE agent_runs SET input_tokens = 7, output_tokens = 7, cost_usd = 9 "
                "WHERE user_id = :u"
            ),
            {"u": blake.user},
        )
        # a run of another module of Alex: never part of the documents' spend
        db.execute(
            sa.text(
                "INSERT INTO agent_runs (user_id, agent, input, status, input_tokens, "
                "output_tokens, cost_usd) SELECT user_id, 'echo', input, status, 5000, 5000, 3 "
                "FROM agent_runs WHERE user_id = :u LIMIT 1"
            ),
            {"u": alex.user},
        )
        db.commit()
    spent = api.request("GET", SPEND, token=alex.token)
    assert spent.status_code == 200, spent.text
    body = spent.json()
    assert (body["today"]["input"], body["today"]["output"]) == (1000, 200)
    assert Decimal(body["last_30_days"]["cost_usd"]) == Decimal("0.5")
    assert body["priced"] is True


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


@pytest.mark.parametrize("now", BOUNDARIES, ids=lambda at: at.isoformat())
def test_min_fit_counts_the_last_30_days_only(
    alex: Who, api: Api, clock: Any, now: datetime
) -> None:
    clock.now = now
    token = api.login_token(ALEX)
    alex.posting(60)
    alex.posting(60, days_ago=29)
    alex.posting(60, days_ago=30)
    alex.posting(40)
    alex.posting(None)
    at_default = api.request("GET", SPEND, token=token).json()
    assert (at_default["min_fit"], at_default["postings_30_days"]) == (70, 0)
    lowered = api.request("GET", SPEND, params={"min_fit": 50}, token=token).json()
    assert (lowered["min_fit"], lowered["postings_30_days"], lowered["to_write"]) == (50, 2, 2)
    assert lowered["to_write_estimate"]["tokens"]["high"] > 0
    assert api.request("GET", SPEND, params={"min_fit": 101}, token=token).status_code == 422


def test_a_lower_threshold_counts_the_postings_it_adds_with_their_estimate(
    alex: Who, api: Api, factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        db.execute(
            sa.text(
                "UPDATE profiles SET sections = jsonb_set(sections, '{search}', "
                "CAST(:s AS jsonb)) WHERE user_id = :u"
            ),
            {"s": '{"documents": {"min_fit": 65}}', "u": alex.user},
        )
        db.commit()
    for fit in (80, 65, 64, 55):
        alex.posting(fit)
    alex.posting(50, days_ago=30)
    alex.posting(None)

    def asked(min_fit: int | None = None) -> dict[str, Any]:
        params = {} if min_fit is None else {"min_fit": min_fit}
        response = api.request("GET", SPEND, params=params, token=alex.token)
        assert response.status_code == 200, response.text
        return response.json()

    saved = asked()
    assert (saved["min_fit"], saved["to_write"], saved["newly"]) == (65, 2, 0)
    assert saved["newly_estimate"]["tokens"] == {"low": 0, "high": 0}
    with factory() as db:
        each = consent.figures(db, alex.user, clock().date()).posting
    two = estimate.times(each, 2)
    for lowered in (asked(55), asked(0)):
        assert (lowered["to_write"], lowered["newly"]) == (4, 2)
        assert lowered["newly_estimate"]["tokens"] == {"low": two.low, "high": two.high}
        assert lowered["newly_estimate"]["cost"] is not None  # an API key
    assert (asked(64)["newly"], asked(65)["newly"], asked(100)["newly"]) == (1, 0, 0)
    assert asked(80)["to_write"] == 1
