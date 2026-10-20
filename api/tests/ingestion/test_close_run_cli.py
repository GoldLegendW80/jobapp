"""python -m app.ingestion.cli close-run against a real Postgres: the operator's close of a run
that cannot move, the same close the cron makes after its last failure; a dry run, a second
call and an unknown run change nothing; only ids, statuses and counts are printed."""

import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.agents.models import AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import cli, engine
from app.ingestion.runs_models import RunStatus
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion.extract_helpers import key, keyring, settings

__all__ = ["key", "keyring", "settings"]

EMAIL = "e2e-closer@example.org"


@pytest.fixture
def owner(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, EMAIL)
    save_credential(factory, keyring, user)
    return user


def stuck(factory: sessionmaker[Session], user: uuid.UUID, settings: Settings) -> uuid.UUID:
    """A run of three steps, its first step's agent run at work."""
    run_id = eh.start(factory, user, eh.documents(factory, user, 3))
    assert eh.advance(factory, run_id, settings).enqueue is not None
    eh.set_agent(
        factory,
        eh.running(factory, run_id).agent_run_id,
        status=AgentStatus.running,
        started_at=now(),
    )
    return run_id


def close(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["close-run", *args])
    printed = capsys.readouterr()
    return code, printed.out + printed.err


def test_dry_run_then_close_then_again(
    factory: sessionmaker[Session],
    owner: uuid.UUID,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = stuck(factory, owner, settings)
    agent_id = eh.running(factory, run_id).agent_run_id
    before = eh.statuses(factory, run_id)
    assert before == ["running", "planned", "planned"]

    code, out = close(capsys, str(run_id), "--dry-run")
    assert code == 0
    assert out.startswith(f"Dry run: run {run_id} is running. Closing it would mark it failed")
    assert f"2 planned steps cancelled, the agent run {agent_id} of its running step" in out
    assert "Nothing was changed." in out
    assert eh.run_of(factory, run_id).status is RunStatus.running
    assert eh.statuses(factory, run_id) == before
    with factory() as db:
        assert db.get_one(AgentRun, agent_id).cancel_requested_at is None

    with caplog.at_level("INFO", logger="app.ingestion.engine"):
        code, out = close(capsys, str(run_id))
    assert (code, out.strip()) == (
        0,
        f"Run {run_id} closed: failed, 2 planned steps cancelled, the agent run {agent_id} "
        "of its running step asked to stop.",
    )
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.closed_reason, closed.partial) == (
        RunStatus.failed,
        engine.STUCK,
        True,
    )
    assert eh.statuses(factory, run_id) == ["running", "cancelled", "cancelled"]
    with factory() as db:
        assert db.get_one(AgentRun, agent_id).cancel_requested_at is not None
        assert engine.active_run_id(db, owner) is None  # the user can start a new run
    logged = [one for one in caplog.records if one.getMessage() == "ingestion_run_closed_by_hand"]
    assert len(logged) == 1
    assert (logged[0].__dict__["ingestion_run_id"], logged[0].__dict__["user_id"]) == (
        str(run_id),
        str(owner),
    )

    finished = closed.finished_at
    with caplog.at_level("INFO", logger="app.ingestion.engine"):
        code, out = close(capsys, str(run_id))
    assert (code, out.strip()) == (
        0,
        f"Run {run_id} has already ended (failed). Nothing was changed.",
    )
    assert eh.run_of(factory, run_id).finished_at == finished
    logged = [one for one in caplog.records if one.getMessage() == "ingestion_run_closed_by_hand"]
    assert len(logged) == 1
    assert EMAIL not in caplog.text

    # The step still at work is recorded by the cron once its agent run ends, as after the
    # cron's own close.
    eh.end_agent(factory, agent_id, AgentStatus.cancelled, tokens=7_000)
    assert asyncio.run(engine.tick(factory, None, now, settings)) == 1
    late = eh.run_of(factory, run_id)
    assert (late.status, late.tokens_spent, late.finished_at) == (RunStatus.failed, 7_000, finished)


def test_a_paused_run_is_closed_too(
    factory: sessionmaker[Session],
    owner: uuid.UUID,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = stuck(factory, owner, settings)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=1_000)
    eh.advance(factory, run_id, settings)
    eh.set_run(factory, run_id, status=RunStatus.paused, paused_reason="queue", paused_at=now())
    code, out = close(capsys, str(run_id))
    assert code == 0
    assert out.startswith(f"Run {run_id} closed: failed")
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.paused_reason, closed.paused_at) == (RunStatus.failed, None, None)
    assert "planned" not in eh.statuses(factory, run_id)


def test_an_unknown_run_changes_nothing(
    migrated_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = uuid.uuid4()
    assert close(capsys, str(run_id)) == (1, f"No ingestion run {run_id}. Nothing was changed.\n")
    assert close(capsys, str(run_id), "--dry-run")[0] == 1


@pytest.mark.parametrize("given", ["", "not-a-uuid", "1234"])
def test_an_id_that_is_not_a_uuid_is_refused(
    given: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["close-run", given])
    assert exited.value.code == 2
    capsys.readouterr()
