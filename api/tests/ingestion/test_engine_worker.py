"""The ingestion chain in the worker (ingestion G10): the cron that is its only guarantee, the
limits of the agents queue computed once the ingestion agents are registered, and what the
`on_final` hook is given to start the next step."""

import asyncio
import uuid
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.agents import definitions
from app.agents.crypto import Keyring
from app.config import Settings
from app.ingestion import agents, engine
from app.ingestion.runs_models import RunStatus
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion.extract_helpers import key, keyring, settings

_FIXTURES = (key, keyring, settings)


class Pool:
    """Stands for the arq pool of a job context: remembers what it was asked to queue."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Any, ...]] = []

    async def enqueue_job(self, function: str, *args: Any, **options: Any) -> object:
        self.jobs.append((function, args, options))
        return object()


def test_the_cron_runs_every_minute_on_the_ingestion_queue_and_keeps_no_result() -> None:
    spec = worker.ingestion_queue()
    (cron,) = [one for one in spec.cron_jobs if one.name == "cron:advance_ingestion_runs"]
    # Once in every minute: a second, and no restriction on the minute or the hour.
    assert cron.second == worker.INGESTION_ADVANCE_SECOND
    assert (cron.minute, cron.hour) == (None, None)
    assert cron.keep_result_s == 0
    (function,) = [one for one in spec.functions if one.name == "advance_ingestion_runs"]
    assert (function.keep_result_s, function.max_tries) == (0, 1)


def test_the_shared_agents_queue_keeps_ingestion_budgets_and_allows_paused_apply() -> None:
    """The agents queue's limit is the longest agent of the queue plus the margin, whichever
    module registers it (today apply's form agent, whose run holds a sign in hand off's wait).
    The ingestion agents are counted in it and keep their own wall time: a longer agent elsewhere
    raises the queue's limit, never the extract step's."""
    spec = worker.agents_queue()
    from app.apply import form_agent

    assert agents.EXTRACT_NAME in definitions.AGENTS  # registered by building the queue
    longest = max(agent.wall_time_s for agent in definitions.AGENTS.values())
    assert definitions.AGENTS[agents.EXTRACT_NAME].wall_time_s == agents.EXTRACT_WALL_TIME_S == 900
    assert (
        definitions.AGENTS[agents.TRANSCRIBE_NAME].wall_time_s
        == agents.TRANSCRIBE_WALL_TIME_S
        == 900
    )
    # Apply's aggregate transport allowance includes bounded person-owned pauses. It does
    # not give ingestion or the form agent more than their original 900 seconds of work.
    assert form_agent.WALL_TIME_S == 900
    assert form_agent.SIGNIN_WAIT_S == 1800
    assert form_agent.TAKEOVER_WAIT_S == 900
    assert definitions.AGENTS[form_agent.NAME].wall_time_s == 3600
    assert spec.name == queues.AGENTS
    assert spec.max_jobs == worker.MAX_AGENT_RUNS == 2
    assert spec.job_timeout_s == 3720  # 3600 aggregate transport, 120 runner cleanup
    assert spec.job_timeout_s == longest + worker.AGENT_JOB_MARGIN_S
    assert all(agent.wall_time_s <= spec.job_timeout_s for agent in definitions.AGENTS.values())
    (run_agent,) = [one for one in spec.functions if one.name == "run_agent"]
    assert run_agent.timeout_s == spec.job_timeout_s == 3720
    assert run_agent.max_tries == 1
    assert worker.agent_stale_running().total_seconds() == 3900  # 3600 plus 300 stale grace
    assert worker.agent_stale_running().total_seconds() == longest + worker.AGENT_STALE_MARGIN_S


def test_an_agent_longer_than_the_job_timeout_registered_when_the_queue_is_built(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = worker.agents_queue()
    longer = replace(agents.EXTRACT, name="g10_longer", wall_time_s=spec.job_timeout_s + 1)
    monkeypatch.setitem(definitions.AGENTS, longer.name, longer)
    # The worker refuses to start: arq would cut that agent's run and the step would run twice.
    with pytest.raises(worker.AgentTooLongError, match="g10_longer"):
        worker.check_agents_fit(spec.job_timeout_s)
    monkeypatch.setattr(worker, "queues", lambda settings=None: [spec])
    with pytest.raises(worker.AgentTooLongError):
        worker.checked_queues(settings)  # what `main` serves
    updated = worker.agents_queue()
    assert updated.job_timeout_s == longer.wall_time_s + worker.AGENT_JOB_MARGIN_S
    (run_agent,) = [one for one in updated.functions if one.name == "run_agent"]
    assert run_agent.timeout_s == updated.job_timeout_s
    assert updated.max_jobs == 2
    assert worker.agent_stale_running().total_seconds() == (
        longer.wall_time_s + worker.AGENT_STALE_MARGIN_S
    )
    assert definitions.AGENTS[agents.EXTRACT_NAME].wall_time_s == 900
    assert definitions.AGENTS[agents.TRANSCRIBE_NAME].wall_time_s == 900


def test_the_runner_is_given_what_the_hook_needs_to_queue_the_next_step(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    pool = Pool()
    ctx = {"settings": settings, "sessions": factory, "clock": now, "redis": pool}
    context = worker.agent_context(ctx)
    enqueue = context.services[engine.ENQUEUE_SERVICE]
    agent_run_id = uuid.uuid4()
    asyncio.run(enqueue(agent_run_id))
    assert pool.jobs == [
        (
            "run_agent",
            (str(agent_run_id),),
            {"_job_id": queues.agent_run_job_id(agent_run_id), "_queue_name": queues.AGENTS},
        )
    ]
    assert worker.agent_context({**ctx, "redis": None}).services == {}


def test_the_cron_advances_every_run_and_answers_a_count(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    alice = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, alice)
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    pool = Pool()
    ctx = {"settings": settings, "sessions": factory, "clock": now, "redis": pool}
    assert asyncio.run(worker.advance_ingestion_runs(ctx)) == 1  # a count, nothing of the run
    step = eh.running(factory, run_id)
    assert [job[1] for job in pool.jobs] == [(str(step.agent_run_id),)]

    eh.end_agent(factory, step.agent_run_id)  # the hook was never called
    asyncio.run(worker.advance_ingestion_runs(ctx))
    assert eh.statuses(factory, run_id) == ["done", "running"]
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id)
    asyncio.run(worker.advance_ingestion_runs(ctx))
    assert eh.run_of(factory, run_id).status is RunStatus.done
    assert asyncio.run(worker.advance_ingestion_runs(ctx)) == 0  # nothing left to move


def test_redis_refusing_the_job_in_the_worker_pauses_the_run(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    class Down:
        async def enqueue_job(self, *args: Any, **options: Any) -> object:
            raise ConnectionError("down")

    alice = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, alice)
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    ctx = {"settings": settings, "sessions": factory, "clock": now, "redis": Down()}
    asyncio.run(worker.advance_ingestion_runs(ctx))
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, engine.QUEUE_DOWN)
