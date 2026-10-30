"""python -m app.agents.cli rotate against a real Postgres: rows of the previous key re-encrypted,
rows of the current key untouched, a row of an unknown key refusing the whole run, and no key.
The job feed keys (Adzuna's) are rotated with the model credentials."""

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import cli, credentials
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, ModelCredential
from app.config import get_settings
from app.jobs.feeds import keys as feed_keys
from tests.agents.conftest import API_KEY, OAUTH, new_key
from tests.identity.api_helpers import Clock, make_user


def seed(
    factory: sessionmaker[Session],
    clock: Clock,
    keyring: Keyring,
    email: str,
    secret: str,
    kind: CredentialKind,
) -> None:
    user = make_user(factory, email=email)
    with factory() as db, db.begin():
        credentials.save(db, keyring, user.id, kind, secret, clock.now)


def rows(factory: sessionmaker[Session]) -> dict[str, tuple[int, bytes]]:
    with factory() as db:
        found = db.execute(
            sa.select(
                ModelCredential.last4, ModelCredential.key_version, ModelCredential.secret_encrypted
            )
        ).all()
    return {last4: (version, data) for last4, version, data in found}


def use_keys(
    monkeypatch: pytest.MonkeyPatch, current: str | None, previous: str | None = None
) -> None:
    if current:
        monkeypatch.setenv("CREDENTIALS_KEY", current)
    if previous:
        monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", previous)
    get_settings.cache_clear()


def test_rotate_re_encrypts_the_rows_of_the_previous_key(
    migrated_url: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = new_key(), new_key()
    seed(
        factory,
        clock,
        Keyring.from_text(old),
        "a@example.org",
        API_KEY,
        CredentialKind.anthropic_api_key,
    )
    seed(
        factory,
        clock,
        Keyring.from_text(new),
        "b@example.org",
        OAUTH,
        CredentialKind.claude_subscription,
    )
    before = rows(factory)
    use_keys(monkeypatch, new, old)
    assert cli.main(["rotate"]) == 0
    assert "1 credential re-encrypted" in capsys.readouterr().out
    after = rows(factory)
    version = Keyring.from_text(new).version
    assert after[API_KEY[-4:]][0] == version
    assert after[API_KEY[-4:]][1] != before[API_KEY[-4:]][1]
    assert after[OAUTH[-4:]] == before[OAUTH[-4:]]
    # Everything now reads with the new key alone.
    alone = Keyring.from_text(new)
    with factory() as db:
        for row in db.scalars(sa.select(ModelCredential)):
            opened = alone.decrypt(
                row.secret_encrypted, row.key_version, row.user_id, row.kind.value
            )
            assert opened.plaintext in (API_KEY, OAUTH)
    assert cli.main(["rotate"]) == 0
    out = capsys.readouterr().out
    assert "0 credentials re-encrypted" in out
    assert API_KEY not in out


def test_rotate_refuses_a_row_of_an_unknown_key_and_changes_nothing(
    migrated_url: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, lost, new = new_key(), new_key(), new_key()
    seed(
        factory,
        clock,
        Keyring.from_text(old),
        "a@example.org",
        API_KEY,
        CredentialKind.anthropic_api_key,
    )
    seed(
        factory,
        clock,
        Keyring.from_text(lost),
        "b@example.org",
        OAUTH,
        CredentialKind.claude_subscription,
    )
    before = rows(factory)
    use_keys(monkeypatch, new, old)
    assert cli.main(["rotate"]) == 1
    assert "does not hold" in capsys.readouterr().err
    assert rows(factory) == before


def test_rotate_re_encrypts_the_job_feed_keys(
    migrated_url: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = new_key(), new_key()
    key = {"app_id": "ab" * 4, "app_key": "q" * 28 + "9876"}  # a made-up key of Adzuna's shape
    user = make_user(factory, email="a@example.org")
    with factory() as db, db.begin():
        feed_keys.save(db, Keyring.from_text(old), user.id, "adzuna", key, clock.now)
    use_keys(monkeypatch, new, old)
    assert cli.main(["rotate"]) == 0
    out = capsys.readouterr().out
    assert "1 credential re-encrypted" in out
    assert key["app_key"] not in out
    alone = Keyring.from_text(new)  # the old key is gone from .env
    with factory() as db:
        assert feed_keys.reveal(db, alone, user.id, "adzuna") == key
        row = feed_keys.get(db, user.id, "adzuna")
        assert row is not None
        assert row.key_version == alone.version
    assert cli.main(["rotate"]) == 0
    assert "0 credentials re-encrypted" in capsys.readouterr().out


def test_rotate_without_a_key(
    migrated_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_keys(monkeypatch, None)
    assert cli.main(["rotate"]) == 2
    assert "CREDENTIALS_KEY is not set" in capsys.readouterr().err


def test_rotate_with_a_broken_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rotate"]) == 2  # no DATABASE_URL
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_rotate_database_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/x")
    use_keys(monkeypatch, new_key())
    assert cli.main(["rotate"]) == 1
    assert "Database error" in capsys.readouterr().err
