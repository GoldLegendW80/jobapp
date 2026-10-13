"""Follow-ups of the security review of PR #87 (issue #90), on the runner's side: the run's
credential is out of the log lines of every logger for as long as the run holds it, a credential
too short to be scrubbed safely never reaches a run, the end of a run does not wait for a live
text pump that cannot be stopped, and the job text a tool hands to the model is scrubbed before
it is cut. Made up credentials only. The logging filter itself is in tests/test_logging_secrets.py
and the live text store in test_live_hardening.py."""

import asyncio
import io
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import logging_config
from app.agents import credentials, failures, runner
from app.agents import tools as app_tools
from app.agents.backend import BackendRequest
from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import RunStatus
from app.config import Settings
from app.jobs import service as jobs_service
from tests.agents.conftest import API_KEY
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    add_user,
    context,
    events,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

SDK_LOGGER = "claude_agent_sdk._internal.query"
NO_SHAPE = re.compile(r"(?!)")  # matches nothing


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def alice(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


@pytest.fixture
def written() -> Iterator[io.StringIO]:
    """What the process's log handler writes (the handler of `configure_logging`: the JSON
    formatter behind the filter), for every logger."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logging_config.JsonFormatter())
    handler.addFilter(logging_config.SecretFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield out
    finally:
        root.removeHandler(handler)


def lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


# --- point 1: the loggers of the libraries, during a run ----------------------------------------


class Loud(FakeBackend):
    """A backend whose library logs the credential on its own logger, as the SDK does with the
    beginning of a line it cannot read, and reports whether the credential is hidden then."""

    seen: ClassVar[list[str]] = []

    async def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        logging.getLogger(SDK_LOGGER).error("Fatal error in message reader: %s", API_KEY)
        logging.getLogger("httpx").error("HTTP Request", extra={"headers": {"x-api-key": API_KEY}})
        Loud.seen.append(logging_config.clean_text(API_KEY))
        async for message in super().stream(request):
            yield message


def test_the_runs_credential_is_out_of_every_loggers_lines_while_the_run_holds_it(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice: uuid.UUID,
    written: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the shape turned off, so that only the run telling the log what it holds can hide
    it: the runner does, from the moment it decrypts the credential to its end."""
    monkeypatch.setattr(logging_config, "SHAPE", NO_SHAPE)
    monkeypatch.setattr(Loud, "seen", [])
    save_credential(factory, keyring, alice)
    run_id = new_run(factory, alice, {"message": "hello"})
    rc = context(settings, factory, keyring, lambda: Loud(speed=0.0))
    assert logging_config.clean_text(API_KEY) == API_KEY  # nothing is hidden before
    assert asyncio.run(runner.execute(rc, run_id)) is RunStatus.done
    assert Loud.seen == [logging_config.REDACTED]
    assert logging_config.clean_text(API_KEY) == API_KEY  # and nothing stays hidden after
    assert API_KEY not in written.getvalue()
    by_logger = {line["logger"]: line for line in lines(written)}
    assert by_logger[SDK_LOGGER]["msg"] == "Fatal error in message reader: [redacted]"
    assert by_logger["httpx"]["headers"] == {"x-api-key": "[redacted]"}


class Crashing(FakeBackend):
    async def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        raise RuntimeError(f"spawn failed with ANTHROPIC_API_KEY={API_KEY}")
        yield  # pragma: no cover


def test_a_crash_that_quotes_the_credential_leaves_it_nowhere(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice: uuid.UUID,
    written: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not in the log (the app's own line, with its traceback), not in what the job answers
    (the worker's arq result is this status, never an exception), not in the stored run."""
    monkeypatch.setattr(logging_config, "SHAPE", NO_SHAPE)
    save_credential(factory, keyring, alice)
    run_id = new_run(factory, alice, {"message": "hello"})
    rc = context(settings, factory, keyring, lambda: Crashing(speed=0.0))
    answered = asyncio.run(runner.execute(rc, run_id))
    assert answered is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error
    assert API_KEY not in json.dumps([row.error, row.output, events(factory, run_id)], default=str)
    assert '"error": "RuntimeError"' in written.getvalue()  # the app's own line of the failure
    assert API_KEY not in written.getvalue()


# --- point 2: a credential too short to be scrubbed ---------------------------------------------


@pytest.mark.parametrize("length", [4, 8, credentials.MIN_LENGTH - 1])
def test_a_stored_credential_under_the_minimum_length_never_reaches_a_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice: uuid.UUID,
    length: int,
) -> None:
    """A row written some other way than through the save (which checks the length; the table
    itself only asks for four characters): scrubbing a few characters out of everything would
    mangle the whole run. The run fails before it starts, with a message of its own, and
    nothing it stores is touched."""
    short = ("task" + "-sk-ant-api03-" + "Sh0_" * 6)[:length]
    save_credential(factory, keyring, alice, secret=short)
    run_id = new_run(factory, alice, {"message": f"a {short} for the {short} force"})
    made: list[FakeBackend] = []

    def backends() -> FakeBackend:
        made.append(FakeBackend(speed=0.0))
        return made[-1]

    assert asyncio.run(runner.execute(context(settings, factory, keyring, backends), run_id)) is (
        RunStatus.failed
    )
    row = run_row(factory, run_id)
    assert row.error == failures.UNUSABLE == runner.UNUSABLE
    assert row.error == "The saved credential is not a whole one. Paste it again."
    assert made == []  # no model was asked anything
    assert row.credential_kind is None  # and the run did not use the credential
    assert runner.REDACTED not in json.dumps(events(factory, run_id), default=str)
    assert failures.is_provider_failure(row.error)  # the user fixes the credential, then resumes


def test_a_credential_of_exactly_the_minimum_length_runs(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice: uuid.UUID
) -> None:
    shortest = "sk-ant-api03-" + "s7Q" * 9
    assert len(shortest) == credentials.MIN_LENGTH
    save_credential(factory, keyring, alice, secret=shortest)
    run_id = new_run(factory, alice, {"message": f"my key is {shortest}, keep it"})
    assert asyncio.run(runner.execute(context(settings, factory, keyring), run_id)) is (
        RunStatus.done
    )
    row = run_row(factory, run_id)
    stored = json.dumps([row.output, events(factory, run_id)], default=str)
    assert shortest not in stored
    assert shortest[:20] not in stored


# --- point 3: the end of the run does not wait for a pump that cannot be stopped ----------------


class Deaf(RecordingStore):
    """A store whose calls neither answer nor let themselves be cancelled, until `free`."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.free = asyncio.Event()

    async def _deaf(self) -> None:
        self.calls += 1
        while not self.free.is_set():
            try:
                await self.free.wait()
            except asyncio.CancelledError:
                continue

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        await self._deaf()

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        await self._deaf()

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        await self._deaf()


def test_a_pump_that_ignores_its_cancellation_does_not_hold_the_end_of_the_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pump is cancelled and awaited before the run's end is recorded. Before issue #90
    that wait had no bound: a store call that swallows the cancellation kept the run `running`
    for ever. Now the wait is `pump_stop_s`, and the clear that follows is bounded as well."""
    save_credential(factory, keyring, alice)
    run_id = new_run(factory, alice, {"message": "hello"})

    async def go() -> tuple[RunStatus | None, Deaf]:
        store = Deaf()
        rc = context(settings, factory, keyring, lambda: FakeBackend(speed=0.0))
        rc = replace(rc, live_store=store, live_flush_s=0.0, live_timeout_s=0.05, pump_stop_s=0.05)
        try:
            return await asyncio.wait_for(runner.execute(rc, run_id), timeout=20), store
        finally:
            store.free.set()  # so that the test's own loop can end

    with caplog.at_level(logging.WARNING):
        status, store = asyncio.run(go())
    assert status is RunStatus.done
    row = run_row(factory, run_id)
    assert row.status is RunStatus.done
    assert row.finished_at is not None
    assert store.calls == 1  # the one call that never ended: nothing waited for a second one
    assert caplog.text.count("live text pump did not stop") == 1


def test_the_bound_of_the_pump_is_a_setting_of_the_run_context(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    assert context(settings, factory, keyring).pump_stop_s == runner.PUMP_STOP_S == 2.0


# --- point 6: what "every occurrence" means -----------------------------------------------------


def test_two_copies_that_overlap_leave_the_rest_of_the_second() -> None:
    """Said in `scrub`'s docstring: only a credential whose end repeats its own beginning can
    overlap itself, which a random token after a fixed prefix does not."""
    looping = "sk-ant-" + "Lp5_" * 9 + "sk-ant-"
    twice = looping + looping[len("sk-ant-") :]
    assert twice.count(looping) == 1  # the second copy shares its beginning with the first
    assert runner.scrub(twice, looping) == runner.REDACTED + looping[len("sk-ant-") :]
    assert runner.scrub(f"{API_KEY}{API_KEY}", API_KEY) == runner.REDACTED * 2


# --- point 7: the job text a tool hands to the model is scrubbed, then cut -----------------------


def fragment(text: str, secret: str, least: int = 8) -> str | None:
    """A piece of the secret of at least `least` characters that `text` holds, if any."""
    for size in range(len(secret), least - 1, -1):
        for at in range(0, len(secret) - size + 1):
            if secret[at : at + size] in text:
                return secret[at : at + size]
    return None


@pytest.mark.parametrize("inside", [1, 8, 13, 50, len(API_KEY) - 1])
def test_get_job_scrubs_the_job_text_before_it_cuts_it(
    factory: sessionmaker[Session], alice: uuid.UUID, inside: int
) -> None:
    """The credential pasted into a job's description and posting so that each limit falls
    `inside` characters into it: the cut used to come first and keep those characters."""

    def around(limit: int) -> str:
        return "." * (limit - inside) + API_KEY + " and what follows"

    with factory() as db, db.begin():
        job = jobs_service.create_job(
            db,
            alice,
            jobs_service.NewJob(
                company="Acme",
                title="Engineer",
                description=around(app_tools.DESCRIPTION_MAX),
                posting_text=around(app_tools.POSTING_MAX),
            ),
            now(),
        )
        job_id = job.id
    tools = app_tools.build(
        factory, alice, ("get_job",), redact=lambda value: runner.scrub(value, API_KEY)
    )
    found = asyncio.run(tools.by_name["mcp__app__get_job"].handler({"job_id": str(job_id)}))
    payload = json.loads(found["content"][0]["text"])
    for key, limit in (
        ("untrusted_description", app_tools.DESCRIPTION_MAX),
        ("untrusted_posting_text", app_tools.POSTING_MAX),
    ):
        assert fragment(payload[key], API_KEY) is None, key
        whole = "." * (limit - inside) + runner.REDACTED + " and what follows"
        assert payload[key] == whole[:limit], key
    # Whether something was cut is said of the text as it is handed over, scrubbed.
    assert payload["description_truncated"] is (inside < len(runner.REDACTED + " and what follows"))
