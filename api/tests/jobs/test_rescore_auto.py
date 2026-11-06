"""The rescore that follows a change of the skills section (app/jobs/rescore.py; product audit,
chapter 2, owner answer 4 of 2026-11-05), on a real Postgres: whoever writes the skills asks for
it in the same transaction, the tick runs it with the counts at the documents threshold, never
two of one person at once, a change during a run gives one more run after it, a claim left by a
dead worker is taken again, and GET /api/jobs/rescore/status says where it stands. Every account
is `e2e-*@example.org`; the postings are invented."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import rescore, scoring, service
from app.jobs.models import Job, JobRescore
from app.profile import service as profile_service
from app.profile.models import Profile
from app.profile.schemas import SECTIONS
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW, new_user
from tests.jobs.test_api import seed, sign_in
from tests.jobs.test_scoring import skills as make_skills
from tests.profile.conftest import full

# The person's skills before (no agents) and after the change (agents and python): the first two
# postings reach 70 with the change, the third is there already, the fourth stays out.
BEFORE = make_skills(have=[{"name": "dotnet", "terms": ["c#", ".net"]}])
AFTER = make_skills()
TEXTS = (
    ("Build an agent with tool use in Python. Some RAG.", 50, 88),
    ("Build agentic systems with tool use in Python and FastAPI.", 0, 100),
    ("Write C# and .NET services.", 100, 100),
    ("Research with PyTorch and a publication record.", 0, 0),
)


def data(skills: Any) -> dict[str, Any]:
    return skills.model_dump(mode="json")


def save(factory: sessionmaker[Session], user: uuid.UUID, section: str, value: Any) -> int:
    with factory() as db:
        profile = profile_service.get_profile(db, user, NOW)
        saved = profile_service.save_section(db, user, section, value, profile.revision, NOW)
        db.commit()
        return saved.revision


def row(factory: sessionmaker[Session], user: uuid.UUID) -> JobRescore | None:
    with factory() as db:
        found = db.get(JobRescore, user)
        if found is not None:
            db.expunge(found)
        return found


def stored(factory: sessionmaker[Session], user: uuid.UUID) -> JobRescore:
    found = row(factory, user)
    assert found is not None
    return found


def postings(factory: sessionmaker[Session], user: uuid.UUID) -> list[Job]:
    """The four postings of TEXTS scored with BEFORE, and one whose fit was set by hand."""
    rows: list[dict[str, Any]] = [
        {"title": "Engineer", "posting_text": text, "fit": before} for text, before, _ in TEXTS
    ]
    rows.append(
        {"title": "Engineer", "posting_text": TEXTS[0][0], "fit": 20, "fit_overridden": True}
    )
    return seed(factory, user, rows)


def state(factory: sessionmaker[Session], user: uuid.UUID) -> str:
    with factory() as db:
        return rescore.status(db, user).state


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def person(factory: sessionmaker[Session]) -> uuid.UUID:
    user = new_user(factory, "e2e-rescore-a@example.org")
    save(factory, user, "skills", data(BEFORE))
    with factory() as db:
        db.execute(sa.delete(JobRescore).where(JobRescore.user_id == user))  # the first save's ask
        db.commit()
    return user


# --- asked for, whoever writes the skills ------------------------------------------------------


def test_a_skills_change_asks_for_a_rescore_and_nothing_else_does(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    assert row(factory, person) is None
    save(factory, person, "search", full("search"))
    assert row(factory, person) is None  # another section asks nothing
    save(factory, person, "skills", data(BEFORE))
    assert row(factory, person) is None  # the same skills saved again change nothing
    revision = save(factory, person, "skills", data(AFTER))
    found = stored(factory, person)
    assert (found.wanted_revision, found.done_revision, found.running_since) == (revision, 0, None)
    assert state(factory, person) == "waiting"


def test_an_import_with_other_skills_asks_and_a_rolled_back_save_does_not(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    with factory() as db:
        profile = profile_service.get_profile(db, person, NOW)
        profile_service.save_section(db, person, "skills", data(AFTER), profile.revision, NOW)
        db.rollback()
    assert row(factory, person) is None
    sections = {name: full(name) for name in SECTIONS}
    sections["skills"] = data(AFTER)
    with factory() as db:
        imported = profile_service.import_profile(db, person, sections, None, None, NOW)
        db.commit()
        revision = imported.revision
    found = stored(factory, person)
    assert found.wanted_revision == revision


# --- the run -----------------------------------------------------------------------------------


def test_the_tick_rescores_every_posting_and_counts_those_that_reach_the_threshold(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    made = postings(factory, person)
    revision = save(factory, person, "skills", data(AFTER))
    assert rescore.tick(factory, Clock()) == 1
    with factory() as db:
        fits = [db.get(Job, job.id).fit for job in made]  # type: ignore[union-attr]
    assert fits == [after for _, _, after in TEXTS] + [20]  # the fit set by hand is kept
    found = stored(factory, person)
    assert (found.done_revision, found.running_since, found.finished_at) == (revision, None, NOW)
    outcome = {k: v for k, v in found.outcome.items() if k != "changed"}  # competencies count
    assert outcome == {"scored": 5, "up": 2, "down": 0, "skipped_busy": 0, "threshold": 70}
    assert state(factory, person) == "idle"
    assert rescore.tick(factory, Clock()) == 0  # nothing asked any more


def test_the_threshold_is_the_persons_documents_threshold_and_falls_are_counted(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    postings(factory, person)
    save(factory, person, "search", {**full("search"), "documents": {"min_fit": 40}})
    bare = make_skills(have=[])
    save(factory, person, "skills", data(bare))
    rescore.tick(factory, Clock())
    after = [scoring.score(text, bare, title="Engineer").fit for text, _, _ in TEXTS]
    falls = sum(1 for (_, before, _), now in zip(TEXTS, after, strict=True) if before >= 40 > now)
    assert falls >= 1  # the example has one at least, so the count below can be wrong
    found = stored(factory, person)
    assert (found.outcome["threshold"], found.outcome["up"]) == (40, 0)
    assert found.outcome["down"] == falls


def test_a_change_during_a_rescore_runs_once_more_after_it_never_beside_it(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    postings(factory, person)
    first = save(factory, person, "skills", data(AFTER))
    with factory() as db:
        assert rescore.claim(db, person, NOW) == first
        db.commit()
    with factory() as db:
        assert rescore.claim(db, person, NOW) is None  # a second tick: never two at once
        assert rescore.due(db, NOW) == []
    # Two changes while it runs: they make one more run after it.
    save(factory, person, "skills", data(BEFORE))
    last = save(factory, person, "skills", data(make_skills(have=[])))
    assert rescore.tick(factory, Clock()) == 0  # still running: nothing starts beside it
    with factory() as db:
        assert rescore.finish(db, person, NOW, first, {"scored": 0}, NOW)
        db.commit()
    assert state(factory, person) == "waiting"
    assert rescore.tick(factory, Clock()) == 1
    found = stored(factory, person)
    assert found.done_revision == last
    assert rescore.tick(factory, Clock()) == 0


def test_a_claim_left_by_a_dead_worker_is_taken_again_and_its_late_end_changes_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    revision = save(factory, person, "skills", data(AFTER))
    with factory() as db:
        rescore.claim(db, person, NOW)
        db.commit()
    late = NOW + rescore.STALE_AFTER
    with factory() as db:
        assert rescore.due(db, late - timedelta(seconds=1)) == []
        assert rescore.due(db, late) == [person]
        assert rescore.claim(db, person, late) == revision
        db.commit()
    with factory() as db:
        assert not rescore.finish(db, person, NOW, revision, {"scored": 9}, late)
        assert rescore.finish(db, person, late, revision, {"scored": 1}, late)
        db.commit()
    found = stored(factory, person)
    assert found.outcome == {"scored": 1}


def test_skills_that_no_longer_validate_score_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    made = postings(factory, person)
    save(factory, person, "skills", data(AFTER))
    with factory() as db:
        db.execute(
            sa.update(Profile)
            .where(Profile.user_id == person)
            .values(sections={"skills": {"have": "not a list"}})
        )
        db.commit()
    assert rescore.tick(factory, Clock()) == 1
    found = stored(factory, person)
    assert found.outcome == {"no_skills": True, "threshold": 70}
    with factory() as db:
        assert db.get(Job, made[0].id).fit == 50  # type: ignore[union-attr]


def test_a_run_that_raises_is_done_as_failed_and_not_tried_every_tick(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    postings(factory, person)
    revision = save(factory, person, "skills", data(AFTER))

    def broken(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("example failure")

    monkeypatch.setattr(service, "rescore_batch", broken)
    assert rescore.tick(factory, Clock()) == 1
    found = stored(factory, person)
    assert (found.outcome, found.done_revision, found.running_since) == (
        {"failed": True},
        revision,
        None,
    )
    assert rescore.tick(factory, Clock()) == 0


def test_a_posting_held_by_another_change_is_tried_once_more_at_the_end(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = postings(factory, person)
    save(factory, person, "skills", data(AFTER))
    real = service.rescore_batch
    calls: list[Any] = []

    def first_one_busy(*args: Any, **kwargs: Any) -> int:
        calls.append(kwargs["ids"])
        if len(calls) == 1:
            with factory() as other:
                other.execute(sa.select(Job).where(Job.id == made[0].id).with_for_update())
                return real(*args, **kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "rescore_batch", first_one_busy)
    rescore.tick(factory, Clock())
    assert [made[0].id] in calls  # the one more try, on the held posting alone
    with factory() as db:
        assert db.get(Job, made[0].id).fit == 88  # type: ignore[union-attr]
    found = stored(factory, person)
    assert found.outcome["skipped_busy"] == 0


# --- the status route --------------------------------------------------------------------------


def test_the_status_route_follows_a_save_and_is_the_callers_own(
    factory: sessionmaker[Session], api: Api
) -> None:
    alex = sign_in(factory, api, "e2e-rescore-alex@example.org")
    blake = sign_in(factory, api, "e2e-rescore-blake@example.org")
    path = "/api/jobs/rescore/status"
    idle = api.get(path, token=alex.token)
    assert (idle.status_code, idle.headers["cache-control"]) == (200, "no-store")
    assert idle.json() == {
        "state": "idle",
        "wanted_revision": None,
        "done_revision": 0,
        "finished_at": None,
        "outcome": None,
    }
    seed(factory, alex.id, [{"title": "Engineer", "posting_text": TEXTS[0][0], "fit": 50}])
    put = api.request(
        "PUT",
        "/api/profile/skills",
        token=alex.token,
        json={"base_revision": 0, "data": data(AFTER)},
    )
    assert put.status_code == 200, put.text
    revision = put.json()["revision"]
    waiting = api.get(path, token=alex.token).json()
    assert (waiting["state"], waiting["wanted_revision"]) == ("waiting", revision)
    assert api.get(path, token=blake.token).json()["state"] == "idle"
    rescore.tick(factory, Clock())
    done = api.get(path, token=alex.token).json()
    assert (done["state"], done["done_revision"]) == ("idle", revision)
    assert (done["outcome"]["up"], done["outcome"]["threshold"]) == (1, 70)
    assert api.get(path).status_code == 401


def test_the_found_window_does_not_limit_the_rescore(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    old = seed(
        factory,
        person,
        [
            {
                "title": "Engineer",
                "posting_text": TEXTS[1][0],
                "fit": 0,
                "found_on": date(2025, 1, 2),
            }
        ],
    )
    save(factory, person, "skills", data(AFTER))
    rescore.tick(factory, Clock())
    with factory() as db:
        assert db.get(Job, old[0].id).fit == 100  # type: ignore[union-attr]
