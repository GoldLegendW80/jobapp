"""Before an application's run starts (app/apply/posting_check.py; SPEC-apply, "The flow of one
application"; SPEC-jobs, "Liveness"): a queued application whose posting's own page says it is
closed ends `posting_closed` before anything of the run, and its posting is closed; whatever the
check cannot tell lets the run go on. A fake resolver and transport: no request leaves the test,
and the site is invented."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import anyio
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.apply import posting_check, service, states
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.browser.sources import MemoryPacer
from app.jobs import service as jobs_service
from app.jobs.liveness_check import Checker
from app.jobs.models import Job, JobStatus
from tests.apply.conftest import AT, Person

PUBLIC = "203.0.114.10"
POSTING = "https://jobs.example-robotics.example/careers/4012345-applied-ai-engineer"
COPY = "Example Robotics builds warehouse robots and hires engineers who like clear code. " * 6
CLOSED_PAGE = f"<p>{COPY}</p><h2>This job is no longer available.</h2>"
OPEN_PAGE = f"<h1>Applied AI Engineer</h1><p>{COPY}</p><a>Apply</a>"


def checker(text: str, status: int = 200) -> tuple[Checker, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"content-type": "text/html"}
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(text.encode()))

    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC]

    async def instant(seconds: float) -> None:
        await asyncio.sleep(0)

    made = Checker(
        MemoryPacer(), transport=httpx.MockTransport(answer), resolve=resolve, sleep=instant
    )
    return made, seen


def queued(db: Session, person: Person) -> uuid.UUID:
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(location="Paris, France"))
    db.commit()
    application = service.start(
        db, person.user_id, person.posting_id, Mode.review, False, now=AT
    ).application.id
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(url=POSTING))
    db.commit()
    return application


def before_fill(factory: sessionmaker[Session], made: Checker, application_id: uuid.UUID) -> bool:
    return anyio.run(posting_check.before_fill, made, factory, lambda: AT, application_id)


def state(factory: sessionmaker[Session], application_id: uuid.UUID) -> Application:
    with factory() as db:
        found = db.get(Application, application_id)
        assert found is not None
        db.expunge(found)
        return found


def posting_status(factory: sessionmaker[Session], person: Person) -> JobStatus:
    with factory() as db:
        return db.execute(sa.select(Job.status).where(Job.id == person.posting_id)).scalar_one()


def ended_events(factory: sessionmaker[Session], application_id: uuid.UUID) -> list[dict]:
    with factory() as db:
        return [
            e.data
            for e in db.scalars(
                sa.select(ApplicationEvent).where(
                    ApplicationEvent.application_id == application_id,
                    ApplicationEvent.kind == "ended",
                )
            )
        ]


def test_a_closed_page_ends_the_application_before_its_run(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    application_id = queued(db, person_a)
    made, seen = checker(CLOSED_PAGE)
    assert before_fill(factory, made, application_id) is True
    ended = state(factory, application_id)
    assert ended.state is State.posting_closed
    assert ended.error_code == "posting_closed"
    assert ended_events(factory, application_id) == [{"code": "posting_closed"}]
    assert posting_status(factory, person_a) is JobStatus.closed
    assert len(seen) == 1


@pytest.mark.parametrize(("text", "status"), [(OPEN_PAGE, 200), ("Access denied", 403), ("", 503)])
def test_anything_else_lets_the_run_go_on(
    db: Session, factory: sessionmaker[Session], person_a: Person, text: str, status: int
) -> None:
    application_id = queued(db, person_a)
    made, seen = checker(text, status)
    assert before_fill(factory, made, application_id) is False
    assert state(factory, application_id).state is State.queued
    assert posting_status(factory, person_a) is JobStatus.found
    assert len(seen) == 1


def test_a_posting_the_person_closed_ends_it_without_a_request(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    application_id = queued(db, person_a)
    job = jobs_service.lock_job(db, person_a.user_id, person_a.posting_id)
    assert job is not None
    jobs_service.set_status(db, job, JobStatus.closed, AT)
    db.commit()
    made, seen = checker(OPEN_PAGE)
    assert before_fill(factory, made, application_id) is True
    assert state(factory, application_id).state is State.posting_closed
    assert seen == []


def test_an_application_no_longer_queued_is_left_alone(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    application_id = queued(db, person_a)
    found = db.get(Application, application_id)
    assert found is not None
    states.move(found, State.cancelled, AT)
    db.commit()
    made, seen = checker(CLOSED_PAGE)
    assert before_fill(factory, made, application_id) is False
    assert state(factory, application_id).state is State.cancelled
    assert seen == []
    assert posting_status(factory, person_a) is JobStatus.found


def test_the_fill_job_asks_first(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    application_id = queued(db, person_a)
    made, _ = checker(CLOSED_PAGE)
    ctx = {"apply_services": SimpleNamespace(sessions=factory, clock=lambda: AT), "liveness": made}
    # The run itself would need a lease and a form: the check ends the job before either.
    anyio.run(worker.apply_fill, ctx, str(application_id))
    assert state(factory, application_id).state is State.posting_closed
