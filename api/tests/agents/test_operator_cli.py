"""python -m app.agents.cli start against a real Postgres: the operator's start of a run, through
the functions the routes call (app/agents/operator.py), as tests/agents_real_run_jetson.sh uses it.
The queue is a fake that records or refuses; no model is called."""

import io
import json
import uuid
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import cli, credentials, operator, runs
from app.agents.crypto import Keyring
from app.agents.models import (
    AgentMessage,
    AgentRun,
    AgentThread,
    CredentialKind,
    ModelCredential,
    RunStatus,
)
from app.config import get_settings
from app.identity.models import Role, User
from tests.agents.conftest import API_KEY, OAUTH, new_key
from tests.agents.run_helpers import add_job
from tests.identity.api_helpers import Clock, make_user

OWNER = "e2e-operator@example.org"
MESSAGE = "What are the two strongest points of my profile for this role?"
ONLY = ["--role", "admin", "--credential-kind", "claude_subscription"]


class FakeQueue:
    jobs: ClassVar[list[tuple[str, tuple[Any, ...], str, str]]] = []
    down: ClassVar[bool] = False

    def __init__(self, url: str) -> None:
        self.url = url

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if FakeQueue.down:
            raise queues.QueueUnavailableError("down")
        FakeQueue.jobs.append((function, args, queue, job_id))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> type[FakeQueue]:
    FakeQueue.jobs = []
    FakeQueue.down = False
    monkeypatch.setattr(queues, "Queue", FakeQueue)
    return FakeQueue


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch, migrated_url: str) -> str:
    value = new_key()
    monkeypatch.setenv("CREDENTIALS_KEY", value)
    get_settings.cache_clear()
    return value


def account(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    email: str = OWNER,
    role: Role = Role.admin,
    kind: CredentialKind | None = CredentialKind.claude_subscription,
) -> tuple[uuid.UUID, uuid.UUID]:
    user = make_user(factory, email=email, role=role)
    if kind is not None:
        secret = OAUTH if kind is CredentialKind.claude_subscription else API_KEY
        with factory() as db, db.begin():
            credentials.save(db, Keyring.from_text(key), user.id, kind, secret, clock.now)
    return user.id, add_job(factory, user.id)


def start(
    capsys: pytest.CaptureFixture[str],
    kind: str,
    job_id: uuid.UUID,
    owner: str = OWNER,
    message: str = MESSAGE,
    only: list[str] = ONLY,
) -> tuple[int, dict[str, Any], str]:
    stdin = io.StringIO(json.dumps({"owner": owner, "message": message}))
    code = cli.main(["start", kind, str(job_id), *only], stdin=stdin)
    printed = capsys.readouterr()
    lines = printed.out.strip().splitlines()
    assert len(lines) == 1, printed.out
    return code, json.loads(lines[0]), printed.out + printed.err


def counts(factory: sessionmaker[Session]) -> tuple[int, int, int]:
    with factory() as db:
        return (
            db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) or 0,
            db.scalar(sa.select(sa.func.count()).select_from(AgentThread)) or 0,
            db.scalar(sa.select(sa.func.count()).select_from(AgentMessage)) or 0,
        )


def test_triage_is_queued_as_the_route_queues_it(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, job = account(factory, clock, key)
    code, out, printed = start(capsys, "triage", job)
    assert (code, out["http"], out["enqueued"]) == (0, 202, True)
    run_id = uuid.UUID(out["run_id"])
    with factory() as db:
        row = db.get(AgentRun, run_id)
        assert row is not None
        assert (row.agent, row.job_id, row.status) == ("triage", job, RunStatus.queued)
    assert fake_queue.jobs == [
        ("run_agent", (str(run_id),), queues.AGENTS, queues.agent_run_job_id(run_id))
    ]
    assert OWNER not in printed


def test_chat_opens_the_thread_stores_the_message_and_refuses_a_second_while_pending(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, job = account(factory, clock, key)
    code, out, printed = start(capsys, "chat", job)
    assert (code, out["http"], out["thread_created"]) == (0, 202, True)
    with factory() as db:
        stored = db.scalars(sa.select(AgentMessage)).one()
        assert stored.content == {"text": MESSAGE}
        assert stored.run_id == uuid.UUID(out["run_id"])
    assert MESSAGE not in printed
    assert OWNER not in printed
    code, again, _ = start(capsys, "chat", job)
    assert (code, again["http"], again["detail"]) == (1, 409, operator.BUSY)
    assert again["active_run_id"] == out["run_id"]
    assert counts(factory) == (1, 1, 1)


@pytest.mark.parametrize(
    ("role", "kind"),
    [
        (Role.member, CredentialKind.claude_subscription),
        (Role.admin, CredentialKind.anthropic_api_key),
        (Role.member, CredentialKind.anthropic_api_key),
    ],
)
@pytest.mark.parametrize("what", ["triage", "chat"])
def test_another_kind_of_account_is_refused_and_nothing_is_written(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    role: Role,
    kind: CredentialKind,
    what: str,
) -> None:
    _, job = account(factory, clock, key, role=role, kind=kind)
    code, out, printed = start(capsys, what, job)
    assert (code, out["http"], out["detail"]) == (1, 403, operator.NOT_ALLOWED)
    assert counts(factory) == (0, 0, 0)
    assert fake_queue.jobs == []
    assert OWNER not in printed


def change_inside_the_start(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    user_id: uuid.UUID,
    change: str,
    at_call: int,
) -> list[int]:
    """`credentials.get` of the start, its `at_call`th call, commits `change` from another
    session after reading: inside the transaction that writes, before the check."""
    real = credentials.get
    calls: list[int] = []

    def get(db: Session, user: uuid.UUID) -> Any:
        found = real(db, user)
        calls.append(len(calls) + 1)
        if len(calls) == at_call:
            with factory() as other, other.begin():
                if change == "demote":
                    other.execute(
                        sa.update(User).where(User.id == user_id).values(role=Role.member)
                    )
                else:
                    kind = CredentialKind.anthropic_api_key
                    keyring = Keyring.from_text(key)
                    credentials.save(other, keyring, user_id, kind, API_KEY, clock.now)
        return found

    monkeypatch.setattr(operator.credentials, "get", get)
    return calls


@pytest.mark.parametrize("change", ["demote", "api_key"])
def test_a_triage_sees_the_account_changed_inside_its_transaction(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    user_id, job = account(factory, clock, key)
    calls = change_inside_the_start(monkeypatch, factory, clock, key, user_id, change, 1)
    code, out, _ = start(capsys, "triage", job)
    assert calls == [1]
    assert (code, out["http"], out["detail"]) == (1, 403, operator.NOT_ALLOWED)
    assert counts(factory) == (0, 0, 0)
    assert fake_queue.jobs == []


@pytest.mark.parametrize("change", ["demote", "api_key"])
def test_a_chat_sees_the_account_changed_between_thread_and_message(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    user_id, job = account(factory, clock, key)
    calls = change_inside_the_start(monkeypatch, factory, clock, key, user_id, change, 2)
    code, out, _ = start(capsys, "chat", job)
    assert calls == [1, 2]  # the thread's check, then the message's
    assert (code, out["http"], out["detail"]) == (1, 403, operator.NOT_ALLOWED)
    with factory() as db:
        thread = db.scalars(sa.select(AgentThread)).one()
    assert (out["thread_id"], out["thread_created"]) == (str(thread.id), True)
    assert "run_id" not in out
    assert counts(factory) == (0, 1, 0)
    assert fake_queue.jobs == []


@pytest.mark.parametrize("what", ["triage", "chat"])
def test_a_demotion_after_the_check_waits_for_the_start_to_commit(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    what: str,
) -> None:
    user_id, job = account(factory, clock, key)
    real = operator._check
    waited: list[str] = []

    def check(db: Session, user: uuid.UUID, only: operator.Only | None) -> None:
        real(db, user, only)
        for statement in [
            sa.update(User).where(User.id == user_id).values(role=Role.member),
            sa.delete(ModelCredential).where(ModelCredential.user_id == user_id),
        ]:
            try:
                with factory() as other, other.begin():
                    other.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                    other.execute(statement)
            except OperationalError as exc:
                waited.append("waited" if "lock timeout" in str(exc) else "other")

    monkeypatch.setattr(operator, "_check", check)
    code, out, _ = start(capsys, what, job)
    assert (code, out["http"]) == (0, 202)
    checks = 1 if what == "triage" else 2
    assert waited == ["waited", "waited"] * checks


def test_no_credential_unknown_job_and_unknown_account(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_id, job = account(factory, clock, key, kind=None)
    assert start(capsys, "triage", job)[1] == {"http": 409, "detail": operator.NO_CREDENTIAL}
    assert start(capsys, "chat", job)[1] == {"http": 409, "detail": operator.NO_CREDENTIAL}
    with factory() as db, db.begin():
        kind = CredentialKind.claude_subscription
        credentials.save(db, Keyring.from_text(key), user_id, kind, OAUTH, clock.now)
    _, other_job = account(factory, clock, key, email="e2e-operator-2@example.org")
    for what in ["triage", "chat"]:
        elsewhere = start(capsys, what, other_job)[1]
        assert elsewhere == {"http": 404, "detail": operator.JOB_NOT_FOUND}
    for owner in ["e2e-nobody@example.org", "not an address"]:
        code, out, printed = start(capsys, "triage", job, owner=owner)
        assert (code, out) == (1, {"http": 404, "detail": cli.NO_ACCOUNT})
        assert owner not in printed
    assert counts(factory) == (0, 0, 0)


def test_queue_down_fails_the_run_at_once(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, job = account(factory, clock, key)
    fake_queue.down = True
    code, out, _ = start(capsys, "triage", job)
    assert (code, out["http"], out["detail"]) == (1, 503, runs.QUEUE_DOWN)
    with factory() as db:
        row = db.get(AgentRun, uuid.UUID(out["run_id"]))
        assert row is not None
        assert row.status is RunStatus.failed


def test_without_credentials_key_nothing_starts(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, job = account(factory, clock, key)
    monkeypatch.delenv("CREDENTIALS_KEY")
    get_settings.cache_clear()
    assert start(capsys, "triage", job)[1] == {"http": 503, "detail": operator.NOT_CONFIGURED}
    assert counts(factory) == (0, 0, 0)


@pytest.mark.parametrize(
    ("message", "detail"), [("   ", operator.EMPTY), ("x" * 8001, cli.TOO_LONG)]
)
def test_a_message_the_route_refuses_is_refused(
    factory: sessionmaker[Session],
    clock: Clock,
    key: str,
    fake_queue: type[FakeQueue],
    capsys: pytest.CaptureFixture[str],
    message: str,
    detail: str,
) -> None:
    _, job = account(factory, clock, key)
    assert start(capsys, "chat", job, message=message)[1] == {"http": 422, "detail": detail}
    assert counts(factory) == (0, 0, 0)


@pytest.mark.parametrize("given", ["", "not json", "[]", '{"message": "hi"}', '{"owner": 7}'])
def test_stdin_that_is_not_the_object_is_refused(
    key: str, fake_queue: type[FakeQueue], capsys: pytest.CaptureFixture[str], given: str
) -> None:
    job = str(uuid.uuid4())
    assert cli.main(["start", "triage", job, *ONLY], stdin=io.StringIO(given)) == 2
    assert json.loads(capsys.readouterr().out)["http"] == 400


def test_role_and_credential_kind_are_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["start", "triage", str(uuid.uuid4())], stdin=io.StringIO("{}"))
    capsys.readouterr()
