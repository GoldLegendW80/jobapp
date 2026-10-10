"""The kind of credential a run used (issue #33): written on the run by the worker when it reads
the credential, returned by the api, so the web can say whether `cost_usd` is a cost (an API key)
or an estimate at API prices that nobody is billed (a subscription). Three cases: an API key, a
subscription token, and a run with no recorded kind (older than the column, or failed before its
credential was read). And migration 0010: the column, down to 0009 and up again."""

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.config import Settings
from tests.agents.conftest import API_KEY, OAUTH, OTHER_KEY
from tests.agents.run_helpers import (
    add_user,
    context,
    events,
    execute,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.agents.test_runs_migration import add_run, at_head
from tests.agents.test_runs_migration import add_user as add_user_row
from tests.conftest import make_settings
from tests.identity.api_helpers import Api
from tests.test_migrations import alembic_config

__all__ = ["at_head"]


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


def test_a_run_with_an_api_key_records_that_kind(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    run_id = new_run(factory, user)
    assert run_row(factory, run_id).credential_kind is None  # nothing is known while queued
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    assert row.credential_kind is CredentialKind.anthropic_api_key
    assert float(row.cost_usd or 0) == pytest.approx(0.0123)


def test_a_run_with_a_subscription_token_records_that_kind(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    run_id = new_run(factory, user)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    assert row.credential_kind is CredentialKind.claude_subscription
    # The SDK reports an amount on a subscription too: it is stored as it always was.
    assert float(row.cost_usd or 0) == pytest.approx(0.0123)


@pytest.mark.parametrize(
    ("kind", "secret"),
    [
        (CredentialKind.anthropic_api_key, API_KEY),
        (CredentialKind.claude_subscription, OAUTH),
    ],
)
def test_the_usage_event_carries_the_kind_with_the_amount(
    kind: CredentialKind,
    secret: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
) -> None:
    """Issue #80: the browser words the amount by the kind of credential. The usage step now
    holds both, so its line is right the moment it arrives: the detail read while the run was
    queued knows no kind yet, and the label flickered until it was read again."""
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user, secret, kind)
    run_id = new_run(factory, user)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    [usage] = [data for name, data in events(factory, run_id) if name == "usage"]
    assert usage["credential_kind"] == kind.value
    assert usage["credential_kind"] == run_row(factory, run_id).credential_kind.value
    assert usage["cost_usd"] == pytest.approx(0.0123)
    # The kind only: nothing of the secret, in this event or any other.
    assert secret not in str(events(factory, run_id))
    assert secret[-4:] not in str(usage)


def test_a_usage_event_without_a_credential_says_so() -> None:
    """The handler alone, as a backend other than the runner's own would drive it: with no
    kind known the event holds None, which the browser reads as "not recorded"."""
    import asyncio

    from claude_agent_sdk import ResultMessage

    from app.agents import runner

    written: list[tuple[str, dict[str, object]]] = []

    class Kept(runner.Recorder):
        def _write(self, seq: int, kind: str, data: dict[str, object]) -> None:
            written.append((kind, data))

    message = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        total_cost_usd=0.5,
    )
    recorder = Kept(context=None, run_id=uuid.uuid4(), seq=1)  # type: ignore[arg-type]
    asyncio.run(runner._handle(message, runner._State(), recorder))
    assert written == [("usage", {**runner.usage_of(message), "credential_kind": None})]


def test_the_run_keeps_the_kind_it_used_when_the_credential_changes_later(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    first = new_run(factory, user)
    assert execute(context(settings, factory, keyring), first) is RunStatus.done
    save_credential(factory, keyring, user, OTHER_KEY, CredentialKind.anthropic_api_key)
    second = new_run(factory, user)
    assert execute(context(settings, factory, keyring), second) is RunStatus.done
    assert run_row(factory, first).credential_kind is CredentialKind.claude_subscription
    assert run_row(factory, second).credential_kind is CredentialKind.anthropic_api_key


def test_a_failed_or_cancelled_run_still_says_which_kind_it_used(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    run_id = new_run(factory, user, {"message": "x", "script": "crash"})
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).credential_kind is CredentialKind.claude_subscription


def test_a_run_that_never_read_a_credential_has_no_kind(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")  # no credential saved
    run_id = new_run(factory, user)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).credential_kind is None
    other = add_user(factory, "bob@example.org")
    save_credential(factory, keyring, other)
    unreadable = new_run(factory, other)
    assert execute(context(settings, factory, None), unreadable) is RunStatus.failed
    assert run_row(factory, unreadable).credential_kind is None


def test_only_the_kind_is_written_never_the_secret(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    run_id = new_run(factory, user)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    with factory() as db:
        row = db.execute(
            sa.text("SELECT to_jsonb(r)::text FROM agent_runs r WHERE id = :id"), {"id": run_id}
        ).scalar_one()
    assert API_KEY not in row
    assert API_KEY[-8:] not in row
    assert '"credential_kind": "anthropic_api_key"' in row


def set_run(factory: sessionmaker[Session], run_id: uuid.UUID, **values: object) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(AgentRun).where(AgentRun.id == run_id).values(**values))


def test_the_api_returns_the_kind_in_the_three_cases(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    me = uuid.UUID(api.me(alice).json()["id"])
    with_key, with_subscription, unknown = (new_run(factory, me) for _ in range(3))
    for run_id in (with_key, with_subscription, unknown):
        with factory() as db, db.begin():
            runs.claim(db, run_id, now())
            runs.finish(db, run_id, runs.Outcome(status=RunStatus.done, cost_usd=None), now())
        set_run(factory, run_id, cost_usd=0.193)
    with factory() as db, db.begin():
        runs.record_credential_kind(db, with_key, CredentialKind.anthropic_api_key)
        runs.record_credential_kind(db, with_subscription, CredentialKind.claude_subscription)

    def kind(run_id: uuid.UUID) -> object:
        body = api.get(f"/api/agents/runs/{run_id}", token=alice).json()
        assert body["cost_usd"] == pytest.approx(0.193)
        return body["credential_kind"]

    assert kind(with_key) == "anthropic_api_key"
    assert kind(with_subscription) == "claude_subscription"
    assert kind(unknown) is None  # as a run stored before the column existed
    listed = api.get("/api/agents/runs", token=alice).json()["items"]
    assert {item["id"]: item["credential_kind"] for item in listed} == {
        str(with_key): "anthropic_api_key",
        str(with_subscription): "claude_subscription",
        str(unknown): None,
    }
    # Another user learns nothing about the run, its kind included.
    assert api.get(f"/api/agents/runs/{with_key}", token=bob).status_code == 404


# --- migration 0010 -------------------------------------------------------------------------------


def test_migration_adds_a_nullable_column_of_the_existing_enum(at_head: sa.Engine) -> None:
    columns = {c["name"]: c for c in sa.inspect(at_head).get_columns("agent_runs")}
    column = columns["credential_kind"]
    assert column["nullable"] is True
    assert column["default"] is None
    assert column["type"].name == "model_credential_kind"  # type: ignore[attr-defined]
    with at_head.begin() as conn:
        user = add_user_row(conn)
        run = add_run(conn, user)
        read = sa.text("SELECT credential_kind FROM agent_runs WHERE id = :r")
        assert conn.execute(read, {"r": run}).scalar_one() is None
        for value in ("claude_subscription", "anthropic_api_key"):
            conn.execute(
                sa.text(
                    "UPDATE agent_runs SET credential_kind = CAST(:k AS model_credential_kind) "
                    "WHERE id = :r"
                ),
                {"k": value, "r": run},
            )
            assert conn.execute(read, {"r": run}).scalar_one() == value
    with pytest.raises(sa.exc.DataError), at_head.begin() as conn:
        conn.execute(
            sa.text("UPDATE agent_runs SET credential_kind = 'password' WHERE agent = 'echo'")
        )


def test_migration_keeps_existing_runs_with_no_kind_and_goes_down_and_up(
    at_head: sa.Engine,
) -> None:
    command.downgrade(alembic_config(), "0009")
    assert "credential_kind" not in {
        c["name"] for c in sa.inspect(at_head).get_columns("agent_runs")
    }
    with at_head.begin() as conn:
        # A run from before the column, with the cost the SDK reported for it.
        user = add_user_row(conn)
        old = add_run(conn, user, s="done")
        conn.execute(sa.text("UPDATE agent_runs SET cost_usd = 0.193 WHERE id = :r"), {"r": old})
        # The enum of the credentials is still there for model_credentials.
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM pg_type WHERE typname = 'model_credential_kind'")
            ).scalar_one()
            == 1
        )
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())
    with at_head.connect() as conn:
        row = conn.execute(
            sa.text("SELECT credential_kind, cost_usd FROM agent_runs WHERE id = :r"), {"r": old}
        ).one()
    assert row.credential_kind is None  # not guessed
    assert float(row.cost_usd) == pytest.approx(0.193)


def test_migration_offline_sql(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0009:0010", sql=True)
    out = capsys.readouterr().out
    assert "ALTER TABLE agent_runs ADD COLUMN credential_kind model_credential_kind" in out
    assert "CREATE TYPE" not in out
    command.downgrade(alembic_config(), "0010:0009", sql=True)
    assert "ALTER TABLE agent_runs DROP COLUMN credential_kind" in capsys.readouterr().out
