"""A deleted document and the nightly backups (G15): a dump taken after the delete holds none of it.

The dump is the one `ops/db.sh backup` writes, `pg_dump -Fc` run inside the database container,
read back to SQL with `pg_restore` the way a restore reads it. A random token planted in the
document's text, its file name, an agent event and the quote of a profile item is found in a
dump taken before the delete (so the search does see a compressed archive) and in none taken
after. The dumps written before the delete age out with the 7 nightly backups of T10, which the
dialog's sentence promises; that retention is tested by tests/backup_test.sh, not here.

Marked `docker`: the test starts a Postgres container of its own, since the dump runs inside
it, and so it replaces the shared `database_url` of the other tests in this module only."""

import secrets
import subprocess
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs as agent_runs
from app.config import Settings
from app.ingestion import deletion
from app.ingestion.models import IngestionDocument
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion import merge_helpers as mh
from tests.ingestion.extract_helpers import key, keyring, settings
from tests.pg import DATABASE, USER, Postgres, docker_available, start_postgres

pytestmark = [pytest.mark.docker]

_FIXTURES = (key, keyring, settings)
Factory = sessionmaker[Session]


@pytest.fixture
def postgres() -> Iterator[Postgres]:
    if not docker_available():
        pytest.skip("Docker is not available")
    pg = start_postgres(durable=False)
    yield pg
    pg.remove()


@pytest.fixture
def database_url(postgres: Postgres) -> str:
    return postgres.url()


def dump(postgres: Postgres) -> str:
    """A backup as `ops/db.sh backup` takes it, then read back as SQL by `pg_restore`."""
    archive = subprocess.run(
        ["docker", "exec", postgres.name, "pg_dump", "-U", USER, "-d", DATABASE, "-Fc"],
        capture_output=True,
        check=True,
    ).stdout
    assert archive.startswith(b"PGDMP")  # the custom format, compressed
    return subprocess.run(
        ["docker", "exec", "-i", postgres.name, "pg_restore", "-f", "-"],
        input=archive,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")


def test_a_dump_taken_after_a_delete_holds_none_of_the_document(
    postgres: Postgres, factory: Factory, keyring: object, settings: Settings
) -> None:
    alice = add_user(factory, "e2e-g15-backup@example.org")
    save_credential(factory, keyring, alice)  # type: ignore[arg-type]
    token = f"tok{secrets.token_hex(8)}"
    text = f"{mh.CV}Reference {token}.\n"
    run_id, (document,) = mh.merged(
        factory,
        alice,
        settings,
        [text],
        [[("identity", "headline", "Engineer", f"Reference {token}")]],
    )
    (step,) = eh.steps_of(factory, run_id)
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == document)
            .values(name=f"cv-{token}.txt")
        )
        assert step.agent_run_id is not None
        seq = agent_runs.next_seq(db, step.agent_run_id)
        agent_runs.append_event(db, step.agent_run_id, seq, "text", {"text": token}, now())

    before = dump(postgres)
    assert before.count(token) >= 4  # the text, the file name, the event, the item's quote

    with factory() as db, db.begin():
        assert deletion.delete(db, alice, document, now()) is True

    after = dump(postgres)
    assert token not in after
    assert str(alice) in after  # the dump is of the same database, the user is still there
