"""The check of a subscription token by one Claude Code run in the worker (token_check.py),
against a real Postgres and Redis, on the fake backend (no model call): the POST answer and its
idempotence, GET carrying `check`, every outcome of the job, a token saved again or removed while
it is checked, a lost check, the queue down, the real worker end to end, and the token never in a
log line, the Redis value or the queued job."""

import asyncio
import io
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from arq.constants import job_key_prefix
from arq.worker import Worker
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.agents import credentials, runs, token_check
from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import CredentialKind, ModelCredential
from app.agents.verify import Verifier
from app.config import Settings
from app.identity.models import User
from app.main import create_app
from tests.agents.conftest import ALICE, API_KEY, OAUTH, FakeAnthropic
from tests.agents.run_helpers import Backends, add_user, context, credential_row, save_credential
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock
from tests.jobs.test_worker import flush_redis, redis_url, serve_until, wait_for

__all__ = ["redis_url"]

PATH = "/api/me/model-credential"
VERIFY = f"{PATH}/verify"
SUB = CredentialKind.claude_subscription
CHECKING = {"state": "checking", "message": token_check.CHECKING}


@pytest.fixture
def settings(migrated_url: str, redis_url: str, key: str, tmp_path: Path) -> Settings:
    flush_redis(redis_url)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(
        migrated_url,
        redis_url=redis_url,
        credentials_key=key,
        agents_fake_backend=True,
        agents_scratch_dir=str(scratch),
    )


@pytest.fixture
def scratch_root(settings: Settings) -> Path:
    assert settings.agents_scratch_dir is not None
    return Path(settings.agents_scratch_dir)


@pytest.fixture
def app(settings: Settings, clock: Clock, anthropic_api: FakeAnthropic) -> FastAPI:
    built = create_app(settings, clock=clock)
    built.state.model_verifier = Verifier(transport=httpx2.MockTransport(anthropic_api))
    return built


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


def put(api: Api, token: str, kind: str = SUB.value, secret: str = OAUTH) -> Any:
    response = api.request("PUT", PATH, token=token, json={"kind": kind, "secret": secret})
    assert response.status_code == 200, response.text
    return response.json()


def user_id(factory: sessionmaker[Session], email: str = ALICE) -> uuid.UUID:
    with factory() as db:
        return db.scalars(sa.select(User.id).where(User.email == email)).one()


def in_redis[T](url: str, work: Callable[[Any], Awaitable[T]]) -> T:
    async def go() -> T:
        async with aioredis.from_url(url) as client:
            return await work(client)

    return asyncio.run(go())


def stored_value(url: str, user: uuid.UUID) -> str | None:
    raw = in_redis(url, lambda client: client.get(token_check.Store.key(user)))
    return raw.decode() if raw is not None else None


def queued_checks(url: str) -> list[dict[str, Any]]:
    """The check jobs waiting on the agents queue, as arq stored them."""

    async def read(client: Any) -> list[dict[str, Any]]:
        found = []
        for job_id in await client.zrange(queues.AGENTS, 0, -1):
            raw = await client.get(job_key_prefix.encode() + job_id)
            job = queues.deserialize(raw)
            if job["f"] == token_check.JOB:
                found.append({"id": job_id.decode(), "raw": raw.decode(), **job})
        return found

    return in_redis(url, read)


# --- the api ---------------------------------------------------------------------------------


def test_verify_queues_one_check_and_answers_checking(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    redis_url: str,
    anthropic_api: FakeAnthropic,
) -> None:
    saved = put(api, alice)
    assert saved["check"] is None  # a new save: nothing checked yet
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "checking"
    assert body["message"] == token_check.CHECKING
    assert body["credential"]["check"] == CHECKING
    assert body["credential"]["verified_at"] is None
    assert body["credential"]["updated_at"] == saved["updated_at"]
    assert OAUTH not in response.text
    assert anthropic_api.requests == []  # no call from the api
    [job] = queued_checks(redis_url)
    uid = user_id(factory)
    assert job["a"][:2] == [str(uid), token_check.stamp(credential_row(factory, uid).updated_at)]  # type: ignore[union-attr]
    assert job["id"] == queues.token_check_job_id(job["a"][2])
    assert OAUTH not in job["raw"]
    # Asked again while it waits: the same answer, nothing queued twice.
    again = api.post(VERIFY, token=alice)
    assert again.status_code == 200
    assert again.json() == body
    assert len(queued_checks(redis_url)) == 1
    assert api.get(PATH, token=alice).json()["check"] == CHECKING
    value = stored_value(redis_url, uid)
    assert value is not None
    assert OAUTH not in value


def test_get_reads_the_check_of_this_save_only(
    api: Api, alice: str, factory: sessionmaker[Session], redis_url: str, clock: Clock
) -> None:
    put(api, alice)
    api.post(VERIFY, token=alice)
    uid = user_id(factory)
    [job] = queued_checks(redis_url)
    attempt = job["a"][2]

    async def finish(client: Any) -> bool:
        store = token_check.Store(lambda: client)
        return await store.finish(uid, attempt, "invalid", token_check.INVALID)

    assert in_redis(redis_url, finish)
    assert api.get(PATH, token=alice).json()["check"] == {
        "state": "invalid",
        "message": token_check.INVALID,
    }
    # Saved again: the earlier outcome belongs to the earlier token.
    clock.advance(seconds=5)
    assert put(api, alice)["check"] is None
    assert api.get(PATH, token=alice).json()["check"] is None
    response = api.post(VERIFY, token=alice)
    assert response.json()["credential"]["check"] == CHECKING
    assert len(queued_checks(redis_url)) == 2


def test_a_lost_check_reads_null_and_is_queued_again(
    api: Api, alice: str, factory: sessionmaker[Session], redis_url: str
) -> None:
    put(api, alice)
    api.post(VERIFY, token=alice)
    uid = user_id(factory)
    value = stored_value(redis_url, uid)
    assert value is not None
    # Its worker died: the time it had on its way has passed.
    lost = json.dumps({**json.loads(value), "until": time.time() - 1})
    in_redis(redis_url, lambda client: client.set(token_check.Store.key(uid), lost))
    assert api.get(PATH, token=alice).json()["check"] is None
    assert api.post(VERIFY, token=alice).json()["credential"]["check"] == CHECKING
    assert len(queued_checks(redis_url)) == 2


def test_queue_down_is_503_and_leaves_nothing_checking(
    app: FastAPI,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(*_: Any, **__: Any) -> bool:
        raise queues.QueueUnavailableError("down")

    put(api, alice)
    # The same Queue keeps its pool, which the store shares and the app closes at its end.
    monkeypatch.setattr(app.state.queue, "enqueue", down)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 503
    assert response.json() == {"detail": runs.QUEUE_DOWN}
    assert stored_value(redis_url, user_id(factory)) is None
    assert api.get(PATH, token=alice).json()["check"] is None


def test_redis_down_leaves_get_answering(app: FastAPI, api: Api, alice: str) -> None:
    put(api, alice)

    async def down() -> Any:
        raise ConnectionError("down")

    app.state.token_checks = token_check.Store(down)
    body = api.get(PATH, token=alice).json()
    assert body["kind"] == SUB.value
    assert body["check"] is None
    assert api.post(VERIFY, token=alice).status_code == 503


def test_an_api_key_is_still_checked_at_once(
    api: Api, alice: str, redis_url: str, anthropic_api: FakeAnthropic
) -> None:
    put(api, alice, kind="anthropic_api_key", secret=API_KEY)
    body = api.post(VERIFY, token=alice).json()
    assert body["result"] == "verified"
    assert body["credential"]["check"] is None
    assert len(anthropic_api.requests) == 1
    assert queued_checks(redis_url) == []
    assert api.get(PATH, token=alice).json()["check"] is None


# --- the job ---------------------------------------------------------------------------------


@dataclass
class Scripted(Backends):
    """Fake backends that replay `script` in place of the check's own stream."""

    script: str = token_check.SCRIPT

    def __call__(self) -> FakeBackend:
        backend = super().__call__()
        replay = backend.stream
        backend.stream = lambda request: replay(replace(request, script=self.script))  # type: ignore[method-assign]
        return backend


def run_check(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    user: uuid.UUID,
    backends: Backends,
    timeout_s: float = token_check.RUN_TIMEOUT_S,
    before: Callable[[], None] | None = None,
) -> tuple[str | None, token_check.Check | None]:
    """Queue a check of the user's token as the api does, then run the job: what it answered
    and the Redis value after it."""
    row = credential_row(factory, user)
    assert row is not None

    async def go(client: Any) -> tuple[str | None, token_check.Check | None]:
        store = token_check.Store(lambda: client)
        attempt = await store.start(user, row.updated_at, time.time())
        assert attempt is not None
        if before is not None:
            before()
        state = await token_check.execute(
            context(settings, factory, keyring, backends),
            store,
            user,
            row.updated_at,
            attempt,
            timeout_s=timeout_s,
        )
        return state, await store.read(user)

    return in_redis(redis_url, go)


@pytest.fixture
def subscriber(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "sub@example.org")
    save_credential(factory, keyring, user, OAUTH, SUB)
    return user


@pytest.mark.parametrize(
    ("script", "state", "message"),
    [
        (token_check.SCRIPT, "verified", token_check.VERIFIED),
        ("refused", "invalid", token_check.INVALID),
        ("busy", "busy", token_check.BUSY),
        ("no_credit", "busy", token_check.NO_USAGE),
        ("apply_check_down", "unavailable", token_check.UNAVAILABLE),  # Claude Code cannot start
        ("missing_cli", "unavailable", token_check.UNAVAILABLE),
        ("crash", "unavailable", token_check.UNAVAILABLE),  # the process dies
    ],
)
def test_the_job_records_each_outcome(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
    scratch_root: Path,
    script: str,
    state: str,
    message: str,
) -> None:
    row = credential_row(factory, subscriber)
    assert row is not None
    if script == "refused":
        # Verified by an earlier run: a refusal clears it.
        with factory() as db, db.begin():
            credentials.set_verified_if_unchanged(
                db, subscriber, SUB, row.updated_at, row.updated_at
            )
    backends = Scripted(script=script)
    answered, found = run_check(settings, factory, keyring, redis_url, subscriber, backends)
    assert answered == state
    assert token_check.current(found, row.updated_at) is not None
    assert found is not None
    assert (found.state, found.message) == (state, message)
    assert found.attempt is None
    after = credential_row(factory, subscriber)
    assert after is not None
    assert (after.verified_at is not None) is (state == "verified")
    # One turn, no tool, the smallest model, the token in Claude Code's own variable only.
    [backend] = backends.made
    assert backend.seen_prompt == token_check.PROMPT
    options = backend.seen_options
    assert (options.model, options.max_turns, options.tools) == (token_check.MODEL, 1, [])
    assert options.mcp_servers == {}
    env = backend.seen_env or {}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAUTH
    assert env.get("ANTHROPIC_API_KEY", "") == ""
    assert list(scratch_root.iterdir()) == []


def test_a_check_that_takes_too_long_is_unavailable(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
    scratch_root: Path,
) -> None:
    backends = Scripted(script="stall", speed=1.0)
    answered, found = run_check(
        settings, factory, keyring, redis_url, subscriber, backends, timeout_s=0.3
    )
    assert answered == "unavailable"
    assert found is not None
    assert found.message == token_check.UNAVAILABLE
    assert list(scratch_root.iterdir()) == []


def test_a_token_saved_again_during_the_check_is_not_marked(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
) -> None:
    replaced = OAUTH[:-4] + "New9"

    class Replacing(Scripted):
        def __call__(self) -> FakeBackend:
            save_credential(factory, keyring, subscriber, replaced, SUB)
            return super().__call__()

    for script in (token_check.SCRIPT, "refused", "busy"):
        save_credential(factory, keyring, subscriber, OAUTH, SUB)
        answered, found = run_check(
            settings, factory, keyring, redis_url, subscriber, Replacing(script=script)
        )
        assert answered is None
        assert found is None  # nothing said about either token
        row = credential_row(factory, subscriber)
        assert row is not None
        assert row.last4 == "New9"
        assert row.verified_at is None


def test_a_token_saved_again_or_removed_before_the_job_starts_is_not_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
) -> None:
    def save_again() -> None:
        save_credential(factory, keyring, subscriber, OAUTH[:-4] + "New9", SUB)

    def remove() -> None:
        with factory() as db, db.begin():
            db.execute(sa.delete(ModelCredential))

    for change in (save_again, remove):
        backends = Scripted()
        answered, found = run_check(
            settings, factory, keyring, redis_url, subscriber, backends, before=change
        )
        assert (answered, found, backends.made) == (None, None, [])


def test_a_token_removed_during_the_check_writes_nothing(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
) -> None:
    class Removing(Scripted):
        def __call__(self) -> FakeBackend:
            with factory() as db, db.begin():
                db.execute(sa.delete(ModelCredential))
            return super().__call__()

    answered, found = run_check(settings, factory, keyring, redis_url, subscriber, Removing())
    assert (answered, found) == (None, None)
    assert credential_row(factory, subscriber) is None


def test_an_attempt_replaced_by_another_does_nothing(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
) -> None:
    row = credential_row(factory, subscriber)
    assert row is not None

    async def go(client: Any) -> tuple[str | None, token_check.Check | None]:
        store = token_check.Store(lambda: client)
        first = await store.start(subscriber, row.updated_at, time.time())
        assert first is not None
        # On its way: a second start for the same save is refused.
        assert await store.start(subscriber, row.updated_at, time.time()) is None
        # Lost long ago, then started again: the first attempt's job comes too late.
        assert await store.start(subscriber, row.updated_at, time.time() + 3600) is not None
        backends = Scripted()
        state = await token_check.execute(
            context(settings, factory, keyring, backends), store, subscriber, row.updated_at, first
        )
        assert backends.made == []
        return state, await store.read(subscriber)

    answered, found = in_redis(redis_url, go)
    assert answered is None
    assert found is not None
    assert found.state == "checking"


@pytest.mark.parametrize(
    ("result", "errors", "state"),
    [
        (None, set(), "unavailable"),
        (
            SimpleNamespace(is_error=False, subtype="success", api_error_status=None),
            set(),
            "verified",
        ),
        # A turn limit means Claude answered.
        (
            SimpleNamespace(is_error=True, subtype="error_max_turns", api_error_status=None),
            set(),
            "verified",
        ),
        (SimpleNamespace(is_error=True, subtype="success", api_error_status=403), set(), "invalid"),
        (
            SimpleNamespace(is_error=True, subtype="success", api_error_status=None),
            {"authentication_failed"},
            "invalid",
        ),
        (SimpleNamespace(is_error=True, subtype="success", api_error_status=529), set(), "busy"),
        (
            SimpleNamespace(is_error=True, subtype="success", api_error_status=None),
            {"rate_limit"},
            "busy",
        ),
        (
            SimpleNamespace(is_error=True, subtype="success", api_error_status=500),
            set(),
            "unavailable",
        ),
        (
            SimpleNamespace(is_error=True, subtype="error_during_execution", api_error_status=None),
            set(),
            "unavailable",
        ),
    ],
)
def test_outcome_of_follows_the_runs_classification(
    result: Any, errors: set[str], state: str
) -> None:
    assert token_check.outcome_of(result, errors)[0] == state


# --- the secret ------------------------------------------------------------------------------


def test_the_check_hides_the_token_from_every_log_line(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    redis_url: str,
    subscriber: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Claude Code's side logging the token (the SDK logging its options): with the shape turned
    off, only the registration around the run hides it."""
    from app import logging_config

    monkeypatch.setattr(logging_config, "SHAPE", re.compile(r"(?!)"))

    class Loud(Scripted):
        def __call__(self) -> FakeBackend:
            backend = super().__call__()
            replay = backend.stream

            def stream(request: Any) -> Any:
                token = request.options.env["CLAUDE_CODE_OAUTH_TOKEN"]
                logging.getLogger("claude_agent_sdk").error("spawn token=%s", token)
                return replay(request)

            backend.stream = stream  # type: ignore[method-assign]
            return backend

    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logging_config.JsonFormatter())
    handler.addFilter(logging_config.SecretFilter())
    logging.getLogger().addHandler(handler)
    caplog.set_level("DEBUG")
    try:
        for script in (token_check.SCRIPT, "crash", "refused"):
            run_check(settings, factory, keyring, redis_url, subscriber, Loud(script=script))
            value = stored_value(redis_url, subscriber)
            assert value is not None
            assert OAUTH not in value
    finally:
        logging.getLogger().removeHandler(handler)
    logged = out.getvalue()
    assert "spawn token=[redacted]" in logged  # the capture works
    assert OAUTH not in logged
    assert OAUTH not in capsys.readouterr().out
    assert logging_config.clean_text(OAUTH) == OAUTH  # and nothing stays hidden after


# --- the worker ------------------------------------------------------------------------------


def test_the_worker_runs_the_check_queued_by_the_api(
    api: Api, alice: str, settings: Settings, factory: sessionmaker[Session], redis_url: str
) -> None:
    put(api, alice)
    assert api.post(VERIFY, token=alice).status_code == 200
    uid = user_id(factory)
    ctx = worker.make_context(settings)
    ctx["agent_backend"] = Backends(speed=0.01)

    def verified() -> bool:
        row = credential_row(factory, uid)
        return row is not None and row.verified_at is not None

    async def scenario(_: list[Worker]) -> None:
        await wait_for(verified)

    serve_until(settings, ctx, scenario)
    body = api.get(PATH, token=alice).json()
    assert body["verified_at"] is not None
    assert body["check"] == {"state": "verified", "message": token_check.VERIFIED}
    assert queued_checks(redis_url) == []
