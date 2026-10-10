"""One real agent run through the real Claude Agent SDK and Claude Code CLI, opt-in only: set
JOBAPP_LIVE_ANTHROPIC_API_KEY to an Anthropic API key (a few cents per run). Skipped otherwise,
and never in CI. The echo agent reads a job with get_job and answers; the run must end done,
with tokens and cost recorded, no secret in its events, and its scratch directory removed. A3:
one real triage run on a made up posting and profile must end done with at least one pending
proposal on the job and the job itself unchanged."""

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.backend import SdkBackend
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from tests.agents.run_helpers import (
    add_job,
    add_user,
    context,
    events,
    execute,
    new_run,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

LIVE_KEY = os.environ.get("JOBAPP_LIVE_ANTHROPIC_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY, reason="set JOBAPP_LIVE_ANTHROPIC_API_KEY to run one real agent run"
)


def test_one_real_run(
    migrated_url: str, key: str, factory: sessionmaker[Session], tmp_path: Path
) -> None:
    settings = make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(tmp_path))
    keyring = Keyring.from_text(key)
    user: uuid.UUID = add_user(factory, "live@example.org")
    save_credential(factory, keyring, user, LIVE_KEY)
    job = add_job(factory, user, "Live Test Corp", "Agent Runtime Engineer")
    run_id = new_run(factory, user, {"message": "Hello from the live test."}, job_id=job)
    assert execute(context(settings, factory, keyring, SdkBackend), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    assert row.input_tokens
    assert row.output_tokens
    assert row.cost_usd is not None
    kinds = [kind for kind, _ in events(factory, run_id)]
    assert "tool_call" in kinds
    for _, data in events(factory, run_id):
        assert LIVE_KEY not in str(data)
    assert list(tmp_path.iterdir()) == []


def test_one_real_triage_run(
    migrated_url: str, key: str, factory: sessionmaker[Session], tmp_path: Path
) -> None:
    from app.agents import proposals
    from app.jobs import service as jobs_service
    from tests.agents.test_triage import FACTS, SEARCH, SKILLS, save_profile

    settings = make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(tmp_path))
    keyring = Keyring.from_text(key)
    user: uuid.UUID = add_user(factory, "live-triage@example.org")
    save_credential(factory, keyring, user, LIVE_KEY)
    save_profile(factory, user, facts=FACTS, skills=SKILLS, search=SEARCH)
    job = add_job(
        factory,
        user,
        "Live Test Corp",
        "Agent Engineer",
        "Build LLM agents with tool calling and evaluation. Three years of Python.",
    )
    run_id = new_run(factory, user, {}, job_id=job, agent="triage")
    assert execute(context(settings, factory, keyring, SdkBackend), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    assert row.cost_usd is not None
    with factory() as db:
        made = proposals.list_proposals(db, user, proposals.Filters(run_id=run_id), 10)
        assert made
        assert all(p.status.value == "pending" for p in made)
        found = jobs_service.get_job(db, user, job)
        assert found is not None
        assert found.notes == ""
        assert found.status.value == "found"
    for _, data in events(factory, run_id):
        assert LIVE_KEY not in str(data)
