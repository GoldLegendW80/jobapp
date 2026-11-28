"""Agents A13 in apply: the apply agent refused as busy before it did anything is read as `busy`
(the application's own retry, its browser lease let go), never as `agent_failed`; refused after
a first call, it keeps today's failure (`applier.ran_of`, on runs the fake backend refused)."""

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runner
from app.agents.crypto import Keyring
from app.apply import applier
from tests.agents.conftest import new_key
from tests.agents.run_helpers import add_job, add_user, context, new_run, save_credential
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("script", "stopped"),
    [("busy", "busy"), ("busy_after_tool", "error"), ("echo", "")],
    ids=["refused before any act", "refused after a call", "done"],
)
def test_the_apply_agent_refused_before_any_act_is_busy_and_after_a_call_an_error(
    migrated_url: str,
    factory: sessionmaker[Session],
    tmp_path: Path,
    script: str,
    stopped: str,
) -> None:
    key = new_key()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    settings = make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))
    keyring = Keyring.from_text(key)
    user = add_user(factory, f"e2e-busy-{uuid.uuid4().hex[:8]}@example.org")
    save_credential(factory, keyring, user)
    job_id = add_job(factory, user)
    run_id = new_run(factory, user, {"message": "hello", "script": script}, job_id)
    # As the runtime applier runs it: in its own process, never waiting.
    asyncio.run(runner.execute(context(settings, factory, keyring), run_id))
    with factory() as db:
        assert applier.ran_of(db, run_id).stopped == stopped
