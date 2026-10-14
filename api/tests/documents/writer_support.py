"""What the tests of D7 (the CV writer and its pipeline) share: a person with the invented profile
of "Alex Example" (e2e-*@example.org), a base CV, a posting, a made up credential, and the
runner on the fake backend replaying the recorded streams `documents_cv_*` of
app/agents/fake_streams/. No model is called and no key is read: the credential is a made up
value of the right shape, and the fake backend never sends it anywhere.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.agents.runner import RunContext
from app.config import Settings
from app.documents import content as c
from app.documents import generate, store, writers  # noqa: F401 (registers the writer)
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationTrigger,
    Kind,
)
from tests.agents.conftest import API_KEY, new_key
from tests.agents.run_helpers import Backends
from tests.conftest import make_settings
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
from tests.documents.store_support import AT, add_posting, add_user

POSTING = load("posting_lumen.json")["text"]
ESTIMATE = {"low": 9_000, "high": 20_000}
CEILING = 100_000


@dataclass
class Person:
    """One person's world: the ids, the settings and the runner context."""

    factory: sessionmaker[Session]
    settings: Settings
    keyring: Keyring
    user: uuid.UUID
    posting: uuid.UUID
    base: uuid.UUID
    queue: RecordingQueue = field(default_factory=RecordingQueue)
    backends: Backends = field(default_factory=lambda: Backends(speed=0.0))

    def context(self) -> RunContext:
        return RunContext(
            settings=self.settings,
            sessions=self.factory,
            clock=lambda: datetime.now(UTC),
            keyring=self.keyring,
            backend_factory=self.backends,
            cancel_poll_s=0.02,
            interrupt_grace_s=1.0,
            services={generate.QUEUE_SERVICE: self.queue},
        )

    def runtime(self) -> generate.Runtime:
        return generate.Runtime(self.factory, lambda: AT, self.settings, self.queue)


def person(
    factory: sessionmaker[Session],
    migrated_url: str,
    email: str,
    posting_text: str = POSTING,
    facts_extra: str = "",
) -> Person:
    key = new_key()
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    keyring = Keyring.from_text(key)
    with factory() as db:
        user = add_user(db, email)
        posting = add_posting(db, user, posting_text)
        db.commit()
    give_profile(factory, user, AT)
    with factory() as db:
        if facts_extra:
            row = db.execute(
                sa.text("SELECT sections FROM profiles WHERE user_id = :u"), {"u": user}
            ).scalar_one()
            row["facts"]["blocks"].append({"title": "Planted", "body": facts_extra})
            db.execute(
                sa.text("UPDATE profiles SET sections = CAST(:s AS jsonb) WHERE user_id = :u"),
                {"s": json.dumps(row), "u": user},
            )
        credentials.save(db, keyring, user, CredentialKind.anthropic_api_key, API_KEY, AT)
        base = store.create_base(
            db, user, "My CV", BaseSource.profile, at=AT, content=load("base_cv_alex.json")
        )
        db.commit()
        base_id = base.id
    return Person(factory, settings, keyring, user, posting, base_id)


def start(
    who: Person, script: str | None, *, ceiling: int = CEILING
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A posting CV generation started: (document id, generation id, run id)."""
    with who.factory() as db:
        document = store.get_or_create(db, who.user, who.posting, Kind.cv, at=AT)
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            None,
            ESTIMATE,
            ceiling,
            at=AT,
        )
        run = generate.start(db, generation, at=AT, script=script, settings=who.settings)
        db.commit()
        return document.id, generation.id, run.id


def execute(who: Person, run_id: uuid.UUID) -> None:
    from app.agents import runner

    asyncio.run(runner.execute(who.context(), run_id))


def write(
    who: Person,
    script: str | None,
    *,
    ceiling: int = CEILING,
    before: Callable[[], None] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    document, generation, run = start(who, script, ceiling=ceiling)
    if before is not None:
        before()
    execute(who, run)
    return document, generation, run


def generation_row(factory: sessionmaker[Session], generation_id: uuid.UUID) -> DocumentGeneration:
    with factory() as db:
        found = db.get(DocumentGeneration, generation_id)
        assert found is not None
        return found


def run_row(factory: sessionmaker[Session], run_id: uuid.UUID) -> AgentRun:
    with factory() as db:
        found = db.get(AgentRun, run_id)
        assert found is not None
        return found


def versions(factory: sessionmaker[Session], document_id: uuid.UUID) -> list[DocumentVersion]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.number)
                .options(undefer(DocumentVersion.content))
            )
        )


def document_row(factory: sessionmaker[Session], document_id: uuid.UUID) -> Document:
    with factory() as db:
        found = db.get(Document, document_id)
        assert found is not None
        return found


def printed(content: dict[str, Any]) -> str:
    """Every text of a CV, joined: what a test looks for a claim in."""
    return "\n".join(text for _, text in c.text_fields(c.parse("cv", content)))
