"""Measure the CV and cover letter writers on invented data (harness2 study, step 3; owner
decision 2026-10-29). Not part of the product: a tool for the owner's laptop that tells whether
tuning the writers' prompts (`prompts/cv.md`, `prompts/letter.md`) has room, before anyone builds
a harness2 adapter.

    cd api
    uv run python -m app.documents.measure [--runs 3] [--writer cv|letter|both]
        [--pairs alex:lumen,blake:brise] [--max-pairs 5] [--json PATH] [--database-url URL]

What it does. For each fixture pair (a profile with its base CV, and a posting, all invented, in
`tests/documents/fixtures`) and each repetition it builds a person in a throwaway database
(`e2e-measure-*@example.org`, the fixture profile, the fixture base CV and posting), starts the
app's own generation (`generate.create` and `generate.start`, the ceiling of `estimate`), and
runs it through the app's own agent runtime (`runner.execute`, the real Claude Agent SDK): the
same prompt, tools, limits, checks and correction pass as a person's click. Then it scores:

- the **first** output: the writer's first valid submit (a submit refused for its structure is
  not kept), after the dash fix and before the correction pass: the closest the tool gets to what
  the prompt alone produced;
- the **final** output: what is stored, after the correction pass and the removal by code;

each with `checks.run` (issues by level: `claim`, `separator`, `note`, and by code), plus from
the run record the submit rounds (the `tool_call` events of the submit tool), turns, tool calls,
tokens, cost and seconds. A table of mean, min, max and spread per pair and writer goes to
stdout, and a JSON file of everything to the path asked for (default: a folder of the home
directory; a path inside this repository is refused).

The credential. It is read from the environment of the person running the command, nowhere
else: CLAUDE_CODE_OAUTH_TOKEN (a Claude subscription token, `claude setup-token`) or
ANTHROPIC_API_KEY. Without either the command stops before doing anything. The app's runtime
reads the model credential from the `model_credentials` table, so the value is saved there,
encrypted with a key made in memory for this process, in the throwaway database that is dropped
when the command ends. It is never printed, logged or written to the JSON (everything printed
passes `logging_config.clean_text`, and the run is inside `logging_config.hidden`).

The database. `--database-url`, else MEASURE_DATABASE_URL, else TEST_DATABASE_URL: a Postgres
server on this machine (the throwaway one `ops/agent-test.sh` starts). The command makes a
database of its own on it, migrates it, and drops it at the end (it creates and drops that one
and issues no other statement on the server); the server is not taken from the app's DATABASE_URL,
which is replaced for the run (`process_settings`). It must be on this machine: the host of the URL
is loopback with no host, hostaddr, port or service in its query, and the connection that libpq
made must end on a loopback address or a Unix socket (PGHOSTADDR, PGSERVICE and a service file
are caught there). Ctrl+C, SIGTERM and SIGHUP drop the database on the way out; SIGKILL cannot
(the runbook says how to find a leftover `measure_` database). Only the fixtures are read for data
(`Fixtures`); nothing of a real person is.

Declared open points: the letter writer, alone, finds no CV of the posting (a state of its own in
production: "No CV for this posting yet"); `--writer both` writes the CV first and the letter
from it, as production does. The person's writing rule is the dash rule on, no banned phrase.
The shortening pass after a render overflow is not run: the tool queues no render (`NullQueue`).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import statistics
import sys
import threading
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import psycopg
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, runner
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentRun, CredentialKind
from app.agents.runner import RunContext
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import checks, estimate, generate, store, submit, writers  # noqa: F401
from app.documents import content as c
from app.documents.models import (
    BaseSource,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
)
from app.documents.settings import DocumentSettings
from app.logging_config import SecretFilter, clean_text, hidden
from app.profile import service as profile_service

API_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = API_DIR.parent
FIXTURES = API_DIR / "tests" / "documents" / "fixtures"

ENV_SUBSCRIPTION = "CLAUDE_CODE_OAUTH_TOKEN"
ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_DATABASES = ("MEASURE_DATABASE_URL", "TEST_DATABASE_URL")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
# What libpq also takes from the query of a URL, over the host checked: another machine, a service.
QUERY_TARGETS = frozenset({"host", "hostaddr", "port", "service"})
# The signals that end a process with no `finally` (a `kill`, a closed terminal).
TERMINATIONS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)
DATABASE_PREFIX = "measure_"

WRITERS = ("cv", "letter", "both")
DEFAULT_RUNS = 3
MAX_RUNS = 10
DEFAULT_MAX_PAIRS = 5
SUBMIT_TOOLS = {"cv": "submit_cv", "letter": "submit_letter"}
LEVELS = ("claim", "separator", "note")

# The figures of the table, in order, with the digits their numbers are shown with.
FIGURES: dict[str, int] = {
    "rounds": 2,
    "turns": 2,
    "tool_calls": 2,
    "tokens": 0,
    "cost_usd": 4,
    "seconds": 1,
    "first_claims": 2,
    "first_separators": 2,
    "first_notes": 2,
    "final_claims": 2,
    "final_separators": 2,
    "final_notes": 2,
    "fixed_by_writer": 2,
    "fixed_by_code": 2,
}


class MeasureError(Exception):
    """The command cannot start or go on; the message is one sentence for the person."""


# --- the credential -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Credential:
    kind: CredentialKind
    secret: str = field(repr=False)
    variable: str = ""


def credential_from(env: Mapping[str, str]) -> Credential:
    """The model credential of the person's environment, else MeasureError. The subscription
    token wins when both are set (the free one). The message never quotes a value."""
    token = env.get(ENV_SUBSCRIPTION, "").strip()
    key = env.get(ENV_API_KEY, "").strip()
    if not token and not key:
        raise MeasureError(
            f"Set {ENV_SUBSCRIPTION} (a Claude subscription token from claude setup-token) "
            f"or {ENV_API_KEY} in the environment of this command, then run it again."
        )
    kind, raw, variable = (
        (CredentialKind.claude_subscription, token, ENV_SUBSCRIPTION)
        if token
        else (CredentialKind.anthropic_api_key, key, ENV_API_KEY)
    )
    try:
        return Credential(kind, credentials.clean_secret(kind, raw), variable)
    except credentials.FormatError as exc:
        raise MeasureError(f"{variable} cannot be used: {exc}") from exc


# --- the fixtures -------------------------------------------------------------------------------

_NAME = re.compile(r"[a-z0-9_]+\.json")
_PART = re.compile(r"[a-z0-9_]+")


class Fixtures:
    """The one reader of data: JSON files directly inside the fixtures folder, by a plain name.
    `read` lists the names read, for the test that only these are."""

    def __init__(self, root: Path = FIXTURES) -> None:
        self.root = root.resolve()
        self.read: list[str] = []

    def json(self, name: str) -> Any:
        if not _NAME.fullmatch(name):
            raise MeasureError(f"{name!r} is not the name of a fixture file.")
        path = self.root / name
        if not path.is_file():
            raise MeasureError(f"There is no fixture {name} in {self.root}.")
        self.read.append(name)
        return json.loads(path.read_text(encoding="utf-8"))

    def parts(self, prefix: str) -> list[str]:
        """The names after `prefix_` of the JSON files that start with it."""
        found = []
        for path in sorted(self.root.glob(f"{prefix}_*.json")):
            part = path.name[len(prefix) + 1 : -len(".json")]
            if _PART.fullmatch(part):
                found.append(part)
        return found


@dataclass(frozen=True)
class Pair:
    profile: str
    posting: str

    @property
    def name(self) -> str:
        return f"{self.profile}:{self.posting}"


def all_pairs(fixtures: Fixtures) -> list[Pair]:
    """Every profile that has a base CV with every posting, ordered so that the first N cover N
    different profiles and N different postings (diagonals of the grid)."""
    based = set(fixtures.parts("base_cv"))
    profiles = [name for name in fixtures.parts("profile") if name in based]
    postings = fixtures.parts("posting")
    found: list[Pair] = []
    for shift in range(len(postings)):
        for i, profile in enumerate(profiles):
            found.append(Pair(profile, postings[(i + shift) % len(postings)]))
    return found


def chosen_pairs(fixtures: Fixtures, asked: str | None, limit: int) -> list[Pair]:
    known = all_pairs(fixtures)
    if asked is None:
        return known[:limit]
    by_name = {pair.name: pair for pair in known}
    chosen: list[Pair] = []
    for name in (part.strip() for part in asked.split(",")):
        if name not in by_name:
            raise MeasureError(
                f"{name or '(empty)'} is not a fixture pair: choose from "
                + ", ".join(sorted(by_name))
                + "."
            )
        if by_name[name] not in chosen:
            chosen.append(by_name[name])
    return chosen


# --- the database -------------------------------------------------------------------------------


def database_server(arg: str | None, env: Mapping[str, str]) -> str:
    """The URL of the Postgres server to make the throwaway database on."""
    url = arg or next((env[name] for name in ENV_DATABASES if env.get(name)), "")
    if not url:
        raise MeasureError(
            "Give a throwaway Postgres on this machine with --database-url or "
            f"{ENV_DATABASES[0]} (ops/agent-test.sh starts one)."
        )
    try:
        parsed = make_url(url)
    except (sa.exc.ArgumentError, ValueError) as exc:  # ValueError: two hosts and a port
        raise MeasureError("The database URL is not a valid Postgres URL.") from exc
    # libpq takes host, hostaddr, port and service from the query too, over the host checked here.
    if parsed.host not in LOOPBACK or QUERY_TARGETS & {key.lower() for key in parsed.query}:
        raise MeasureError(
            "The database must be a throwaway Postgres on this machine (host 127.0.0.1 or "
            "localhost, the address in the URL itself), not a shared or production one."
        )
    return url


def reached_this_machine(info: Any) -> bool:
    """Whether the connection that libpq made ended on this machine: a loopback address or a Unix
    socket. The URL is only what was asked: PGHOSTADDR, PGHOST, PGSERVICE and a service file
    can send the connection elsewhere, so the answer is read from the connection itself."""
    try:
        address, host = info.hostaddr, info.host
    except psycopg.NotSupportedError:  # a libpq too old to say: not proven, so refused
        return False
    if address:
        try:
            found = ipaddress.ip_address(address)
        except ValueError:
            return False
        return bool((getattr(found, "ipv4_mapped", None) or found).is_loopback)
    return str(host).startswith("/")


def _migrate(url: sa.engine.URL) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.attributes["configure_logger"] = False
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
    finally:
        engine.dispose()


@contextmanager
def throwaway_database(server_url: str) -> Iterator[str]:
    """A database made on the server, migrated to head, dropped at the end whatever happens."""
    base = make_url(server_url)
    name = f"{DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    admin = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        if not reached_this_machine(conn.info):
            raise MeasureError(
                "The database server answered from another machine (PGHOSTADDR, PGHOST or "
                "PGSERVICE?): only a throwaway Postgres on this machine is accepted."
            )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base.set(database=name, drivername="postgresql+psycopg")
    try:
        _migrate(url)
        yield url.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@contextmanager
def interrupt_on_termination() -> Iterator[None]:
    """SIGTERM and SIGHUP (a closed terminal, a `kill`) end Python at once, without the `finally`
    that drops the throwaway database. For the run they raise KeyboardInterrupt, as Ctrl+C does,
    and the database goes on the way out. SIGKILL and a power cut cannot be caught: the runbook
    says how to find and drop what they leave. Only the main thread can take a handler."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(number: int, frame: Any) -> None:
        raise KeyboardInterrupt

    before = {number: signal.signal(number, stop) for number in TERMINATIONS}
    try:
        yield
    finally:
        for number, handler in before.items():
            signal.signal(number, handler if handler is not None else signal.SIG_DFL)


@contextmanager
def process_settings(url: str, fake: bool) -> Iterator[None]:
    """The app code reads `get_settings()` in places (the limits of the store): for the run it
    describes the throwaway database, whatever the shell of the person holds."""
    names = {"DATABASE_URL": url, "APP_ENV": "development", "AGENTS_FAKE_BACKEND": str(fake)}
    before = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


# --- the runtime: what the tests replace --------------------------------------------------------


@dataclass(frozen=True)
class Runtime:
    """The model side of a run. `backend_factory` makes the backend of one run; the real one is
    the Claude Agent SDK's. `fake` turns on `AGENTS_FAKE_BACKEND` for the tests' recorded
    streams, and `script_for(writer, pair, repetition)` names the stream of a run."""

    backend_factory: Callable[[], Any]
    fake: bool = False
    script_for: Callable[[str, str, int], str | None] = lambda writer, pair, repetition: None


def sdk_runtime() -> Runtime:
    """The real model. The SDK's `claude -v` probe starts without the run's environment: off,
    as the worker does (app/worker.py `skip_sdk_version_probe`)."""
    os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    try:
        from app.agents.backend import SdkBackend
    except ImportError as exc:
        raise MeasureError(
            "The Claude Agent SDK is not installed: run uv sync in api/ (the agents group)."
        ) from exc
    return Runtime(SdkBackend)


class NullQueue:
    """Stands for the Redis queue: the render a finished generation asks for is not wanted."""

    async def enqueue(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def close(self) -> None:
        return None


class TakeRecorder:
    """Keeps what `submit.take` hands the generation's final hook, which forgets it: the first
    and the final output of each run, the corrections, the submits."""

    def __init__(self) -> None:
        self.taken: dict[uuid.UUID, submit.Taken] = {}
        self._real = submit.take

    def _take(self, run_id: uuid.UUID) -> submit.Taken:
        taken = self._real(run_id)
        self.taken[run_id] = taken
        return taken

    def __enter__(self) -> TakeRecorder:
        self._real = submit.take
        submit.take = self._take  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        submit.take = self._real  # type: ignore[assignment]


# --- one run ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    by_level: dict[str, int]
    by_code: dict[str, int]

    def level(self, name: str) -> int:
        return self.by_level.get(name, 0)


def score(kind: c.Kind, content: object, context: submit.WriterContext) -> Score:
    """`checks.run` on a document, with the sources and rules of the run that wrote it."""
    found = checks.run(
        kind,
        content,
        context.sources,
        context.settings,
        employer_keys=context.employer_keys,
        soft_limits=context.soft_limits,
        today=context.today,
    )
    return Score(
        dict(Counter(issue.level for issue in found.issues)),
        dict(Counter(issue.code for issue in found.issues)),
    )


@dataclass
class Sample:
    pair: str
    writer: str
    repetition: int
    status: str  # done | failed
    error: str | None = None
    model: str | None = None
    credential_kind: str | None = None
    figures: dict[str, float] = field(default_factory=dict)
    first_codes: dict[str, int] = field(default_factory=dict)
    final_codes: dict[str, int] = field(default_factory=dict)
    first: dict[str, Any] | None = None
    final: dict[str, Any] | None = None


@dataclass
class Env:
    """What every run shares."""

    sessions: sessionmaker[Session]
    settings: Settings
    keyring: Keyring
    credential: Credential
    fixtures: Fixtures
    runtime: Runtime
    clock: Callable[[], datetime]
    recorder: TakeRecorder
    log: Callable[[str], None]

    def context(self) -> RunContext:
        return RunContext(
            settings=self.settings,
            sessions=self.sessions,
            clock=self.clock,
            keyring=self.keyring,
            backend_factory=self.runtime.backend_factory,
            services={generate.QUEUE_SERVICE: NullQueue()},
        )


@dataclass(frozen=True)
class World:
    user_id: uuid.UUID
    posting_id: uuid.UUID
    base_id: uuid.UUID


def seed(env: Env, pair: Pair) -> World:
    """A person for one run: an `e2e-measure-*@example.org` account with the fixture profile and
    base CV, the fixture posting and the credential of the environment."""
    at = env.clock()
    posting = env.fixtures.json(f"posting_{pair.posting}.json")
    profile = env.fixtures.json(f"profile_{pair.profile}.json")
    base = env.fixtures.json(f"base_cv_{pair.profile}.json")
    with env.sessions() as db:
        user = db.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) "
                "VALUES (:e, 'Measure', 'h') RETURNING id"
            ),
            {"e": f"e2e-measure-{uuid.uuid4().hex[:12]}@example.org"},
        ).scalar_one()
        job = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text) "
                "VALUES (:u, :s, :c, :t, :x) RETURNING id"
            ),
            {
                "u": user,
                "s": f"measure-{uuid.uuid4().hex[:8]}",
                "c": posting["company"],
                "t": posting["title"],
                "x": posting["text"],
            },
        ).scalar_one()
        found = profile_service.get_profile(db, user, at)
        found.sections = profile["sections"]
        found.revision = profile["revision"]
        db.flush()  # before the saves below, which expire what the session holds
        store.save_settings(db, user, DocumentSettings(no_dash_separators=True), at)
        credentials.save(db, env.keyring, user, env.credential.kind, env.credential.secret, at)
        base_cv = store.create_base(db, user, "My CV", BaseSource.profile, at=at, content=base)
        db.commit()
        return World(user, job, base_cv.id)


def _start(
    env: Env, world: World, kind: Kind, script: str | None, ceiling: int | None
) -> tuple[uuid.UUID, uuid.UUID]:
    """The generation of `kind` and its run, created as a click would (the ceiling of the
    estimate, or `ceiling` for a letter that follows a CV). Trigger `chat`: nothing chains a
    letter after the CV, this tool starts it itself."""
    at = env.clock()
    with env.sessions() as db:
        figures = estimate.for_posting(db, world.user_id, world.posting_id, world.base_id)
        document = store.get_or_create(db, world.user_id, world.posting_id, kind, at=at)
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.chat,
            world.base_id,
            estimate.as_json(figures),
            figures.ceiling if ceiling is None else ceiling,
            at=at,
        )
        run = generate.start(db, generation, at=at, script=script, settings=env.settings)
        db.commit()
        return generation.id, run.id


def _get(db: Session, model: Any, key: uuid.UUID) -> Any:
    found = db.get(model, key)
    if found is None:
        raise LookupError(model.__name__)
    return found


def _read_run(env: Env, run_id: uuid.UUID, tool: str) -> tuple[AgentRun, int]:
    with env.sessions() as db:
        run = _get(db, AgentRun, run_id)
        rounds = db.scalar(
            sa.select(sa.func.count())
            .select_from(AgentEvent)
            .where(
                AgentEvent.run_id == run_id,
                AgentEvent.kind == "tool_call",
                AgentEvent.data["name"].astext.endswith(tool),
            )
        )
        return run, int(rounds or 0)


def _context(env: Env, world: World, generation_id: uuid.UUID, kind: c.Kind) -> Any:
    with env.sessions() as db:
        generation = _get(db, DocumentGeneration, generation_id)
        binding = {
            "generation_id": str(generation.id),
            "document_id": str(generation.document_id),
        }
        return submit.context_for(db, world.user_id, binding, kind)


def run_writer(
    env: Env,
    world: World,
    pair: Pair,
    writer: str,
    repetition: int,
    ceiling: int | None = None,
) -> tuple[Sample, uuid.UUID | None]:
    """One run of a writer, scored. The second value is the CV generation's id (to size the
    letter that follows)."""
    kind = Kind.cv if writer == "cv" else Kind.letter
    script = env.runtime.script_for(writer, pair.name, repetition)
    generation_id, run_id = _start(env, world, kind, script, ceiling)
    asyncio.run(runner.execute(env.context(), run_id))
    run, rounds = _read_run(env, run_id, SUBMIT_TOOLS[writer])
    with env.sessions() as db:
        generation = _get(db, DocumentGeneration, generation_id)
        status, error = generation.status, generation.error
    taken = env.recorder.taken.get(run_id)
    sample = Sample(
        pair.name,
        writer,
        repetition,
        "failed",
        error=error or run.error,
        model=run.model,
        credential_kind=run.credential_kind.value if run.credential_kind else None,
    )
    counts = run.output or {}
    sample.figures = {
        "rounds": rounds,
        "turns": int(counts.get("turns", 0)),
        "tool_calls": int(counts.get("tool_calls", 0)),
        "tokens": (run.input_tokens or 0) + (run.output_tokens or 0),
    }
    if run.cost_usd is not None:
        sample.figures["cost_usd"] = float(run.cost_usd)
    if run.started_at is not None and run.finished_at is not None:
        sample.figures["seconds"] = (run.finished_at - run.started_at).total_seconds()
    if status is GenerationStatus.done and taken is not None and taken.accepted is not None:
        accepted = taken.accepted
        context = _context(env, world, generation_id, kind.value)
        first = accepted.written if accepted.written is not None else accepted.content
        scores = {
            "first": score(kind.value, first, context),
            "final": score(kind.value, accepted.content, context),
        }
        for which, found in scores.items():
            for level in LEVELS:
                sample.figures[f"{which}_{level}s"] = found.level(level)
        sample.figures["fixed_by_writer"] = sum(1 for k in accepted.corrections if k.by == "writer")
        sample.figures["fixed_by_code"] = sum(
            1 for k in accepted.corrections if k.by == "code" and k.before != k.after
        )
        sample.first_codes, sample.final_codes = scores["first"].by_code, scores["final"].by_code
        sample.first, sample.final = c.dump(first), c.dump(accepted.content)
        sample.status, sample.error = "done", None
    env.log(
        f"{pair.name} {writer} #{repetition + 1}: {sample.status}"
        + (f" ({sample.error})" if sample.error else "")
        + f", rounds {rounds}, tokens {sample.figures['tokens']}"
    )
    return sample, generation_id


def run_pair(env: Env, pair: Pair, writer: str, repetition: int) -> list[Sample]:
    """The repetition of a pair: a fresh person each time, so that nothing of one run is read by
    the next. `both`: the CV, then the letter from it, with what the CV left of the ceiling."""
    world = seed(env, pair)
    samples: list[Sample] = []
    cv_generation: uuid.UUID | None = None
    if writer in ("cv", "both"):
        sample, cv_generation = run_writer(env, world, pair, "cv", repetition)
        samples.append(sample)
    if writer in ("letter", "both"):
        ceiling = None
        if cv_generation is not None:
            if samples[-1].status != "done":
                samples.append(Sample(pair.name, "letter", repetition, "failed", error="cv_failed"))
                env.log(f"{pair.name} letter #{repetition + 1}: failed (cv_failed)")
                return samples
            with env.sessions() as db:
                generation = _get(db, DocumentGeneration, cv_generation)
                ceiling = generate.letter_ceiling(db, generation)
            if not ceiling:
                samples.append(Sample(pair.name, "letter", repetition, "failed", error="no_budget"))
                env.log(f"{pair.name} letter #{repetition + 1}: failed (no_budget)")
                return samples
        sample, _ = run_writer(env, world, pair, "letter", repetition, ceiling)
        samples.append(sample)
    return samples


# --- the numbers --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stat:
    n: int
    mean: float
    min: float
    max: float
    spread: float  # max minus min


@dataclass(frozen=True)
class Group:
    pair: str  # a pair, or ALL
    writer: str
    runs: int
    done: int
    stats: dict[str, Stat]
    errors: dict[str, int]


ALL = "all pairs"


def stat_of(values: Sequence[float]) -> Stat:
    return Stat(
        len(values),
        statistics.fmean(values),
        min(values),
        max(values),
        max(values) - min(values),
    )


def group_of(pair: str, writer: str, samples: Sequence[Sample]) -> Group:
    done = [s for s in samples if s.status == "done"]
    stats = {}
    for name in FIGURES:
        values = [s.figures[name] for s in done if name in s.figures]
        if values:
            stats[name] = stat_of(values)
    errors = Counter(s.error or "unknown" for s in samples if s.status != "done")
    return Group(pair, writer, len(samples), len(done), stats, dict(errors))


def summarize(samples: Sequence[Sample]) -> list[Group]:
    """One group per pair and writer in the order they ran, then one per writer over all pairs.
    The figures are those of the runs that finished; the others are counted under `errors`."""
    order: list[tuple[str, str]] = []
    for sample in samples:
        if (sample.pair, sample.writer) not in order:
            order.append((sample.pair, sample.writer))
    groups = [
        group_of(pair, writer, [s for s in samples if (s.pair, s.writer) == (pair, writer)])
        for pair, writer in order
    ]
    for writer in dict.fromkeys(w for _, w in order):
        mine = [s for s in samples if s.writer == writer]
        if len({s.pair for s in mine}) > 1:
            groups.append(group_of(ALL, writer, mine))
    return groups


def number(value: float, digits: int) -> str:
    text = f"{value:.{max(digits, 0)}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def table(groups: Sequence[Group]) -> str:
    """The groups as text: a block per pair and writer, one line per figure."""
    lines: list[str] = []
    for g in groups:
        lines.append(f"{g.pair}  {g.writer}  ({g.runs} runs, {g.done} finished)")
        if g.stats:
            lines.append(f"  {'figure':<18}{'mean':>12}{'min':>12}{'max':>12}{'spread':>12}")
        for name, s in g.stats.items():
            digits = FIGURES[name]
            cells = [number(v, digits) for v in (s.mean, s.min, s.max, s.spread)]
            lines.append(f"  {name:<18}" + "".join(f"{cell:>12}" for cell in cells))
        for error, count in sorted(g.errors.items()):
            lines.append(f"  not finished: {error} x{count}")
        lines.append("")
    return "\n".join(lines)


def report(
    args: Mapping[str, Any], credential: Credential, samples: Sequence[Sample], started: datetime
) -> dict[str, Any]:
    """Everything, as the JSON file holds it. The credential's kind and the name of its
    variable, never its value."""
    return {
        "tool": "app.documents.measure",
        "started": started.isoformat(),
        "args": dict(args),
        "credential": {"kind": credential.kind.value, "variable": credential.variable},
        "figures": list(FIGURES),
        "summary": [asdict(g) for g in summarize(samples)],
        "samples": [asdict(s) for s in samples],
    }


def default_json_path(started: datetime) -> Path:
    stamp = started.strftime("%Y%m%d-%H%M%S")
    return Path.home() / "jobapp-measure" / f"measure-{stamp}.json"


def checked_json_path(asked: str | None, started: datetime) -> Path:
    path = Path(asked).expanduser() if asked else default_json_path(started)
    path = path.resolve()
    if path == REPO_DIR or REPO_DIR in path.parents:
        raise MeasureError(
            "The JSON file must be outside this repository: choose another path with --json."
        )
    return path


def write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


# --- the command --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.documents.measure",
        description="Run the CV and letter writers several times on invented profile and "
        "posting pairs and score each output with the app's checker.",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="runs per pair and writer")
    parser.add_argument("--writer", choices=WRITERS, default="both")
    parser.add_argument("--pairs", help="comma separated profile:posting, default all in turn")
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS)
    parser.add_argument("--json", help="where to write the JSON file (outside this repository)")
    parser.add_argument("--database-url", help="a throwaway Postgres server on this machine")
    return parser


@contextmanager
def _logging_to(stream: TextIO) -> Iterator[None]:
    """Warnings and worse to `stream` while the command runs, every record through the app's
    secret filter. The other handlers of the process are left as they are."""
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.WARNING)
    handler.addFilter(SecretFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def _run_all(env: Env, pairs: Sequence[Pair], writer: str, runs: int) -> list[Sample]:
    samples: list[Sample] = []
    for pair in pairs:
        for repetition in range(runs):
            samples += run_pair(env, pair, writer, repetition)
    return samples


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runtime: Runtime | None = None,
    fixtures: Fixtures | None = None,
    clock: Callable[[], datetime] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The command. 0 done, 1 no run finished, 2 refused (the reason on stderr, one sentence)."""
    out, err = out or sys.stdout, err or sys.stderr
    variables = os.environ if env is None else env
    args = _parser().parse_args(argv)
    clock = clock or (lambda: datetime.now(UTC))

    def say(stream: TextIO, text: str) -> None:
        print(clean_text(text), file=stream, flush=True)

    try:
        # Before anything else: no credential, no database touched.
        credential = credential_from(variables)
        if not 1 <= args.runs <= MAX_RUNS:
            raise MeasureError(f"--runs is a number from 1 to {MAX_RUNS}.")
        if args.max_pairs < 1:
            raise MeasureError("--max-pairs is at least 1.")
        reader = fixtures or Fixtures()
        pairs = chosen_pairs(reader, args.pairs, args.max_pairs)
        if not pairs:
            raise MeasureError("There is no fixture pair to run.")
        started = clock()
        path = checked_json_path(args.json, started)
        server = database_server(args.database_url, variables)
        runtime = runtime or sdk_runtime()
        with hidden(credential.secret), _logging_to(err):
            say(
                err,
                f"Credential: {credential.variable} ({credential.kind.value}); "
                f"{len(pairs)} pairs, {args.runs} runs, writer {args.writer}.",
            )
            with (
                interrupt_on_termination(),
                throwaway_database(server) as url,
                process_settings(url, runtime.fake),
                TakeRecorder() as recorder,
            ):
                key = base64.b64encode(secrets.token_bytes(32)).decode()
                settings = Settings(  # type: ignore[call-arg]
                    database_url=url,
                    app_env="development",
                    credentials_key=key,
                    agents_fake_backend=runtime.fake,
                    model_api_base_url=None,  # the model's own address, whatever the shell says
                )
                engine = build_engine(settings)
                try:
                    shared = Env(
                        build_sessionmaker(engine),
                        settings,
                        Keyring.from_text(key),
                        credential,
                        reader,
                        runtime,
                        clock,
                        recorder,
                        lambda line: say(err, line),
                    )
                    samples = _run_all(shared, pairs, args.writer, args.runs)
                finally:
                    engine.dispose()
            say(out, table(summarize(samples)))
            body = report(
                {
                    "runs": args.runs,
                    "writer": args.writer,
                    "pairs": [pair.name for pair in pairs],
                },
                credential,
                samples,
                started,
            )
            write_json(path, clean_text(json.dumps(body, indent=2, ensure_ascii=False)) + "\n")
            say(err, f"JSON written to {path}")
        return 0 if any(s.status == "done" for s in samples) else 1
    except MeasureError as exc:
        say(err, str(exc))
        return 2
    except Exception as exc:
        # The type and the place, never the message: it could quote what the SDK was given.
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        say(
            err,
            f"The measure stopped on {type(exc).__name__} at {Path(frame.filename).name}:"
            f"{frame.lineno}.",
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
