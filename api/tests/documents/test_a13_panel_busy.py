"""Agents A13 and the hiring panel (documents D29) on the agents runtime with the fake backend, a
real Postgres and an injected clock. A run of the panel Anthropic refuses as busy before it did
anything waits inside the panel's job only for a try within `panel.waits_s` of the job's start,
so the job ends within its own timeout and the documents tick never takes a live panel for lost;
and the panel's runs do not count toward the pace of the agents queue, so a posting's letter
starts as soon as its CV's panel ends."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.agents import failures, runner
from app.agents.models import AgentEvent, AgentRun, RunStatus
from app.documents import panel, tick
from app.documents.models import DocumentPanel, Kind
from tests.documents.store_support import AT, engine, factory, migrated_url
from tests.documents.test_panel_runtime import SCREEN_TWICE, panel_of, with_panel
from tests.documents.writer_support import Person, person, start

__all__ = ["engine", "factory", "migrated_url"]

EVERY_RUN_BUSY: dict[str, str | list[str]] = {agent: "busy" for agent in panel.AGENT_NAMES}


class Clock:
    """The job's clock: it moves `step` on every read (time passing in the job), or stands."""

    def __init__(self, at: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self.at, self.step = at, step

    def __call__(self) -> datetime:
        self.at += self.step
        return self.at


def _written(factory: sessionmaker[Session], migrated_url: str, email: str) -> Any:
    """A person whose posting CV was written: (the person, the CV document, its panel)."""
    who = with_panel(person(factory, migrated_url, email))
    document, _, run = start(who, "documents_cv_valid")
    asyncio.run(runner.execute(who.context(), run, may_wait=True, paced=True))
    [job] = [job for job in who.queue.jobs if job["function"] == panel.FUNCTION]
    return who, document, uuid.UUID(job["args"][0])


def _panel(factory: sessionmaker[Session], panel_id: uuid.UUID) -> DocumentPanel:
    with factory() as db:
        found = db.get(DocumentPanel, panel_id)
        assert found is not None
        db.expunge(found)
        return found


def _on(who: Person, clock: Clock) -> Any:
    return dataclasses.replace(who.context(), clock=clock)


def _panel_runs(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[AgentRun]:
    with factory() as db:
        found = list(
            db.scalars(
                sa.select(AgentRun).where(
                    AgentRun.user_id == user_id, AgentRun.agent.like("documents_panel_%")
                )
            )
        )
        for one in found:
            db.expunge(one)
        return found


def _waits(factory: sessionmaker[Session], run_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    with factory() as db:
        return [
            dict(data)
            for data in db.scalars(
                sa.select(AgentEvent.data).where(
                    AgentEvent.run_id.in_(run_ids), AgentEvent.kind == "waiting"
                )
            )
        ]


def test_the_panel_job_s_timeout_is_its_own_and_under_the_tick_s_stale_bound() -> None:
    [spec] = [one for one in worker.agents_queue().functions if one.name == panel.FUNCTION]
    assert spec.timeout_s == panel.JOB_TIMEOUT_S == worker.DOCUMENTS_PANEL_TIMEOUT_S
    assert panel.JOB_TIMEOUT_S < panel.STALE_S


def test_a_panel_whose_every_run_is_refused_for_ever_ends_failed_within_its_timeout(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who, document, panel_id = _written(factory, migrated_url, "e2e-a13-panel-one@example.org")
    clock = Clock(AT + timedelta(minutes=5))
    status = asyncio.run(panel.job(_on(who, clock), panel_id, scripts=EVERY_RUN_BUSY))
    assert status == "failed"
    found = panel_of(factory, document)
    assert (found.status, found.failed) == ("failed", ["reviews"])
    assert found.started_at is not None
    assert found.finished_at is not None
    reviews = _panel_runs(factory, who.user)
    assert len(reviews) == len(panel.ROLES)
    assert {(one.status, one.error) for one in reviews} == {(RunStatus.failed, failures.BUSY)}
    waits = _waits(factory, [one.id for one in reviews])
    assert waits  # they waited, and never for a try past the bound
    bound = found.started_at + timedelta(seconds=panel.waits_s(who.settings))
    assert max(datetime.fromisoformat(wait["at"]) for wait in waits) <= bound
    assert found.finished_at <= found.started_at + timedelta(seconds=panel.JOB_TIMEOUT_S)


def _second_panel(factory: sessionmaker[Session], who: Person, document: uuid.UUID) -> uuid.UUID:
    """Another panel of the person, waiting its turn (a CV of another posting stands for it)."""
    with factory() as db:
        made = DocumentPanel(
            id=uuid.uuid4(),
            user_id=who.user,
            document_id=document,
            kind=Kind.cv.value,
            reviewed_version=1,
            created_at=AT + timedelta(minutes=1),
        )
        db.add(made)
        db.commit()
        return made.id


async def _until_a_run_waits(factory: sessionmaker[Session], user_id: uuid.UUID) -> None:
    for _ in range(3000):
        if _waits(factory, [one.id for one in _panel_runs(factory, user_id)]):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no run of the panel waited")


def test_while_a_panel_job_waits_the_tick_leaves_it_and_starts_no_second_panel(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who, document, panel_id = _written(factory, migrated_url, "e2e-a13-panel-two@example.org")
    second = _second_panel(factory, who, document)
    clock = Clock(AT + timedelta(minutes=5))

    async def scenario() -> tuple[DocumentPanel, list[uuid.UUID], list[uuid.UUID], str]:
        job = asyncio.create_task(panel.job(_on(who, clock), panel_id, scripts=EVERY_RUN_BUSY))
        await _until_a_run_waits(factory, who.user)
        clock.step = timedelta(0)  # a run waits for a time the job's clock does not reach
        started = _panel(factory, panel_id).started_at
        assert started is not None
        # The latest a live job can run (arq ends it at JOB_TIMEOUT_S, and its waits end first).
        latest = started + timedelta(seconds=panel.JOB_TIMEOUT_S)

        def recovered() -> list[uuid.UUID]:
            with factory() as db:
                letters = panel.recover(db, who.user, latest)
                db.commit()
                return letters

        letters = await asyncio.to_thread(recovered)
        await asyncio.to_thread(tick.tick, factory, latest, who.settings, [who.user])
        turns = await asyncio.to_thread(panel.turns, factory, latest)
        live = _panel(factory, panel_id)
        clock.step = timedelta(seconds=1)
        return live, letters, turns, await job

    live, letters, turns, status = asyncio.run(scenario())
    assert (live.id, live.status, live.failed, live.finished_at) == (panel_id, "running", [], None)
    assert letters == []
    assert turns == []  # the person's next panel waits for the slot the live job holds
    assert status == "failed"
    # Its own end closed the panel and gave the slot to the next one.
    assert _panel(factory, panel_id).failed == ["reviews"]
    handed = [job for job in who.queue.jobs if job["function"] == panel.FUNCTION]
    assert [uuid.UUID(job["args"][0]) for job in handed] == [panel_id, second]


def test_a_panel_one_of_whose_runs_is_refused_once_still_ends_well(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who, document, panel_id = _written(factory, migrated_url, "e2e-a13-panel-three@example.org")
    scripts = {**SCREEN_TWICE, "documents_panel_reader_ceo": "documents_panel_reader_ceo_busy_once"}
    clock = Clock(AT + timedelta(minutes=5))
    assert asyncio.run(panel.job(_on(who, clock), panel_id, scripts=scripts)) == "done"
    found = panel_of(factory, document)
    assert {key for key, value in found.reviews.items() if "missing" not in value} == {
        role.key for role in panel.ROLES
    }
    [ceo] = [one for one in _panel_runs(factory, who.user) if one.agent.endswith("reader_ceo")]
    assert (ceo.status, ceo.error) == (RunStatus.done, None)
    assert [wait["retry"] for wait in _waits(factory, [ceo.id])] == ["refused"]


def test_a_posting_s_letter_starts_at_once_after_its_cv_s_panel(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    """The panel's runs do not count toward the pace of the agents queue: one posting's CV, its
    panel's ten runs, then its letter, all in one minute, and the letter waits for no turn."""
    who = with_panel(person(factory, migrated_url, "e2e-a13-panel-four@example.org"))
    settings = who.settings.model_copy(update={"agents_starts_per_minute": 6})
    who = dataclasses.replace(who, settings=settings)
    context = _on(who, Clock(AT + timedelta(minutes=5), step=timedelta(0)))
    _, _, run = start(who, "documents_cv_valid")
    assert asyncio.run(runner.execute(context, run, may_wait=True, paced=True)) is RunStatus.done
    [job] = [job for job in who.queue.jobs if job["function"] == panel.FUNCTION]
    assert asyncio.run(panel.job(context, uuid.UUID(job["args"][0]), scripts=SCREEN_TWICE)) == (
        "done"
    )
    # The seven reviews, the merge and the screen again, all started in the minute (and the
    # rebuild, a run of the CV writer in the panel's job).
    assert len(_panel_runs(factory, who.user)) == len(panel.ROLES) + 2
    [letter] = [
        uuid.UUID(job["args"][0]) for job in who.queue.jobs if job["function"] == "run_agent"
    ]
    status = asyncio.run(runner.execute(context, letter, may_wait=True, paced=True))
    assert status is RunStatus.done
    assert _waits(factory, [letter]) == []
