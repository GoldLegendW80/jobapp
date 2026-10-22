"""The shape of a model credential, checked wherever a decrypted one is used (issue #155, point
2): one rule in credentials.py (`shape_error`: the prefix, the characters, the length), which the
save applies with its own messages, and which a run and Verify apply again to what they decrypt.
A row written some other way than through the save, off that shape, fails the run with UNUSABLE
and is refused by Verify, and nothing of it reaches the model, the log registry, a log line or
an answer. Made up credentials only, each holding a random marker."""

import asyncio
import json
import logging
import random
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import logging_config
from app.agents import credentials, failures, runner
from app.agents import router as agents_router
from app.agents import verify as agents_verify
from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import CredentialKind, ModelCredential, RunStatus
from app.config import Settings
from app.identity.models import User
from tests.agents.conftest import ALICE, API_KEY, OAUTH, OTHER_KEY, FakeAnthropic
from tests.agents.run_helpers import add_user, context, events, new_run, now, run_row
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

API = CredentialKind.anthropic_api_key
SUBSCRIPTION = CredentialKind.claude_subscription
CHARACTERS = "Only letters, digits, hyphens and underscores belong in the value."
SHORT = "This is too short: paste the whole value."
LONG = f"This is too long: a credential has at most {credentials.MAX_LENGTH} characters."
HEAD = "sk-ant-api03-"

# --- the rule -----------------------------------------------------------------------------------

# Shaped: every one of these has the shape, whatever its kind (the save checks the kind apart).
SHAPED = {
    "api key": API_KEY,
    "another api key": OTHER_KEY,
    "subscription token": OAUTH,
    "shortest": HEAD + "s7Q" * 9,
    "longest": HEAD + "Zq8_" * 96 + "End",
    "prefix and 33 letters": "sk-ant-" + "a" * 33,
    "digits only": "sk-ant-" + "0123456789" * 4,
    "hyphens only": "sk-ant-" + "-" * 33,
    "underscores only": "sk-ant-" + "_" * 33,
    "upper case after the prefix": "sk-ant-API03-" + "ABCD" * 8,
    "a kind not known today": "sk-ant-xyz99-" + "Fu7_" * 10,
    "an admin key": "sk-ant-admin01-" + "a" * 60,
    "a session key": "sk-ant-sid01-" + "a" * 60,
    "a token with hyphens": "sk-ant-oat01-" + "Oa7-" * 30,
    "the prefix twice": "sk-ant-sk-ant-" + "b" * 40,
    "one under the longest": HEAD + "y" * 386,
    "one over the shortest": HEAD + "z" * 28,
    "every allowed character": HEAD + "aZ09_-" * 10,
    "the longest of one letter": "sk-ant-" + "Q" * 393,
    "a key with a dash inside": HEAD + "Ab1_" * 20 + "-" + "Cd2-" * 5 + "AA",
}

PREFIX = credentials.NOT_ANTHROPIC
BODY = HEAD + "Nm4_" * 10  # 53 characters of the shape, to put a near miss in

# Off the shape: (value, what `shape_error` says). The first part that fails is named.
OFF = {
    "upper case prefix": ("SK-ANT-" + "api03-" + "Nm4_" * 10, PREFIX),
    "capital first letter": ("Sk-ant-api03-" + "Nm4_" * 10, PREFIX),
    "underscores for hyphens": ("sk_ant_api03_" + "Nm4_" * 10, PREFIX),
    "an underscore after ant": ("sk-ant_api03-" + "Nm4_" * 10, PREFIX),
    "no hyphen after ant": ("sk-ant" + "Nm4_" * 10, PREFIX),
    "en dashes": ("sk\u2013ant\u2013api03-" + "Nm4_" * 10, PREFIX),
    "unicode hyphens": ("sk\u2010ant\u2010api03-" + "Nm4_" * 10, PREFIX),
    "a non breaking hyphen": ("sk-ant\u2011api03-" + "Nm4_" * 10, PREFIX),
    "full width letters": ("\uff53\uff4b-ant-api03-" + "Nm4_" * 10, PREFIX),
    "a space first": (" " + BODY, PREFIX),
    "a line break first": ("\n" + BODY, PREFIX),
    "a letter before": ("x" + BODY, PREFIX),
    "without its head": (BODY[len("sk-ant-") :], PREFIX),
    "another vendor": ("sk-proj-" + "Nm4_" * 10, PREFIX),
    "only sk": ("sk-" + "Nm4_" * 10, PREFIX),
    "empty": ("", PREFIX),
    "a slash": (BODY + "/", CHARACTERS),
    "a plus": (BODY + "+x", CHARACTERS),
    "base64 padding": (BODY + "==", CHARACTERS),
    "a dot": (BODY[:30] + "." + BODY[30:], CHARACTERS),
    "a colon": (BODY[:30] + ":" + BODY[30:], CHARACTERS),
    "a space inside": (BODY[:30] + " " + BODY[30:], CHARACTERS),
    "a line break last": (BODY + "\n", CHARACTERS),
    "a tab last": (BODY + "\t", CHARACTERS),
    "a nul": (BODY + "\x00", CHARACTERS),
    "a zero width space": (BODY[:30] + "\u200b" + BODY[30:], CHARACTERS),
    "a soft hyphen": (BODY[:30] + "\u00ad" + BODY[30:], CHARACTERS),
    "an en dash inside": (BODY[:30] + "\u2013" + BODY[30:], CHARACTERS),
    "an accent": (BODY + "\u00e9", CHARACTERS),
    "a sharp s": (BODY + "\u00df", CHARACTERS),
    "an arabic indic digit": (BODY + "\u0663", CHARACTERS),
    "a full width letter": (BODY + "\uff41", CHARACTERS),
    "an emoji": (BODY + "\U0001f600", CHARACTERS),
    "a bad character and too short": ("sk-ant-a/b", CHARACTERS),
    "one under the shortest": (HEAD + "s7Q" * 8 + "s7", SHORT),
    "the prefix alone": ("sk-ant-", SHORT),
    "the head alone": (HEAD, SHORT),
    "one over the longest": (HEAD + "Zq8_" * 96 + "Endx", LONG),
    "ten thousand": (HEAD + "w" * 10_000, LONG),
    "a million": (HEAD + "w" * 1_000_000, LONG),
}


def kind_of(secret: str) -> CredentialKind:
    """The kind the save would take `secret` as (an API key's prefix, else a token)."""
    return API if secret.startswith(credentials.API_KEY_PREFIX) else SUBSCRIPTION


def saved(kind: CredentialKind, raw: str) -> tuple[str | None, str | None]:
    """What the save keeps of `raw`, or what it tells the person instead."""
    try:
        return credentials.clean_secret(kind, raw), None
    except credentials.FormatError as exc:
        return None, str(exc)


@pytest.mark.parametrize("secret", list(SHAPED.values()), ids=list(SHAPED))
def test_every_shaped_value_passes_and_the_log_shape_covers_all_of_it(secret: str) -> None:
    assert credentials.shape_error(secret) is None
    assert credentials.has_shape(secret)
    assert logging_config.SHAPE.fullmatch(secret) is not None
    assert logging_config.clean_text(f"key={secret};") == f"key={logging_config.REDACTED};"


@pytest.mark.parametrize(("secret", "said"), list(OFF.values()), ids=list(OFF))
def test_every_near_miss_is_off_the_shape_with_the_first_part_that_fails(
    secret: str, said: str
) -> None:
    assert credentials.shape_error(secret) == said
    assert not credentials.has_shape(secret)


@pytest.mark.parametrize(("secret", "said"), list(OFF.values()), ids=list(OFF))
def test_the_save_refuses_each_near_miss_or_stores_it_trimmed_to_the_shape(
    secret: str, said: str
) -> None:
    """The save trims and refuses inner whitespace with its own message before the rule;
    everything else it refuses with the rule's own words."""
    stored, message = saved(kind_of(secret.strip()), secret)
    if stored is not None:
        assert secret != secret.strip()  # only surrounding whitespace is ever forgiven
        assert credentials.has_shape(stored)
        return
    assert message is not None
    if secret.strip() and not any(char.isspace() for char in secret.strip()):
        assert message == said
    assert secret.strip() == "" or secret.strip() not in message


@pytest.mark.parametrize("secret", list(SHAPED.values()), ids=list(SHAPED))
def test_the_save_keeps_every_shaped_value_of_its_kind(secret: str) -> None:
    if secret.startswith(credentials.ADMIN_KEY_PREFIX):
        with pytest.raises(credentials.FormatError, match="Admin API key"):
            credentials.clean_secret(kind_of(secret), secret)
        return
    assert credentials.clean_secret(kind_of(secret), f"  {secret}\n") == secret


HEADS = ("sk-ant-api03-", "sk-ant-oat01-", "sk-ant-", "sk-ant", "SK-ANT-", "sk_ant-", "", "x")
GOOD = "abcXYZ0189_-"
BAD = (" ", "\n", "/", "+", "=", ".", "\u00e9", "\u200b", "\u2013", "\u0663", "\uff41", ":")


def candidates(count: int) -> Iterator[str]:
    """Values around every bound of the rule, from a seeded generator of its own: a head, then
    allowed characters with now and then one that is not, of a length near a bound."""
    rng = random.Random(155)  # noqa: S311 (test data, not a secret)
    lengths = (0, 1, 30, 39, 40, 41, 100, 399, 400, 401, 450)
    for _ in range(count):
        head = rng.choice(HEADS)
        size = max(rng.choice(lengths) + rng.randint(-2, 2) - len(head), 0)
        body = [rng.choice(GOOD) for _ in range(size)]
        if body and rng.random() < 0.4:
            body[rng.randrange(len(body))] = rng.choice(BAD)
        yield head + "".join(body)


def test_what_the_save_keeps_always_has_the_shape_and_it_refuses_with_the_rules_words() -> None:
    """The property, on 3,000 values of an independent generator, for both kinds: whatever
    `clean_secret` returns has the shape (and the log filter's shape covers all of it), and a
    value without whitespace of its own kind is refused exactly when the rule refuses it, with
    the rule's message."""
    kept = by_the_rule = 0
    for value in candidates(3_000):
        for kind in (API, SUBSCRIPTION):
            stored, message = saved(kind, value)
            if stored is not None:
                kept += 1
                assert credentials.has_shape(stored), value
                assert logging_config.SHAPE.fullmatch(stored) is not None
                continue
            own_kind = kind is kind_of(value)
            plain = value and not any(char.isspace() for char in value)
            if own_kind and plain and not value.startswith(credentials.ADMIN_KEY_PREFIX):
                by_the_rule += 1
                assert message == credentials.shape_error(value), value
    # Both sides of the rule were reached.
    assert kept > 150
    assert by_the_rule > 1_000


# --- a run --------------------------------------------------------------------------------------


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def marker() -> str:
    return "Mk" + secrets.token_hex(10)


# Rows written around the save (each holds the marker): (kind, value).
AROUND: dict[str, Callable[[str], tuple[CredentialKind, str]]] = {
    "wrong prefix": lambda m: (API, "sk-ant_api03-" + m + "Ab1_" * 8),
    "upper case prefix": lambda m: (API, "SK-ANT-API03-" + m + "Ab1_" * 8),
    "without its head": lambda m: (API, "api03-" + m + "Ab1_" * 8),
    "a character outside the set": lambda m: (API, HEAD + m + "/" + "Ab1_" * 8),
    "a space inside": lambda m: (API, HEAD + m + " " + "Ab1_" * 8),
    "a line break last": lambda m: (SUBSCRIPTION, "sk-ant-oat01-" + m + "Ab1_" * 8 + "\n"),
    "too long": lambda m: (API, HEAD + m + "Zq8_" * 100),
    "too short": lambda m: (API, HEAD + m + "s7Qa"),
}


def test_the_rows_written_around_the_save_are_off_the_shape_for_their_reason() -> None:
    expected = {"wrong prefix": PREFIX, "upper case prefix": PREFIX, "without its head": PREFIX}
    expected |= {"a character outside the set": CHARACTERS, "a space inside": CHARACTERS}
    expected |= {"a line break last": CHARACTERS, "too long": LONG, "too short": SHORT}
    marker = "Mk" + secrets.token_hex(10)
    for name, row in AROUND.items():
        _, secret = row(marker)
        assert credentials.shape_error(secret) == expected[name], name
    assert len(AROUND["too short"](marker)[1]) == credentials.MIN_LENGTH - 1


class Spy:
    """`hidden` of the log registry, recording what it was asked to hide."""

    def __init__(self, real: Callable[[str], AbstractContextManager[None]]) -> None:
        self.real = real
        self.asked: list[str] = []

    def __call__(self, secret: str) -> AbstractContextManager[None]:
        self.asked.append(secret)
        return self.real(secret)


def save_row(
    factory: sessionmaker[Session],
    keyring: Keyring,
    user_id: uuid.UUID,
    kind: CredentialKind,
    secret: str,
) -> None:
    """Straight into the table, as a row written some other way than the save would be."""
    with factory() as db, db.begin():
        credentials.save(db, keyring, user_id, kind, secret, now())


def everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every record as it was made, before any filter: its message and all its fields."""
    return "\n".join(
        record.getMessage() + json.dumps(vars(record), default=str) for record in caplog.records
    )


@pytest.mark.parametrize("row", list(AROUND.values()), ids=list(AROUND))
def test_a_stored_credential_off_the_shape_fails_the_run_and_is_never_used(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    marker: str,
    row: Callable[[str], tuple[CredentialKind, str]],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kind, secret = row(marker)
    user = add_user(factory, "alice@example.org")
    save_row(factory, keyring, user, kind, secret)
    run_id = new_run(factory, user, {"message": "hello"})
    spy = Spy(runner.hidden_from_logs)
    monkeypatch.setattr(runner, "hidden_from_logs", spy)
    made: list[FakeBackend] = []

    def backends() -> FakeBackend:
        made.append(FakeBackend(speed=0.0))
        return made[-1]

    with caplog.at_level(logging.DEBUG):
        status = asyncio.run(runner.execute(context(settings, factory, keyring, backends), run_id))
    assert status is RunStatus.failed
    found = run_row(factory, run_id)
    assert found.error == runner.UNUSABLE == failures.UNUSABLE
    assert made == []  # no model was asked anything
    assert spy.asked == []  # the log registry never held it
    assert found.credential_kind is None  # and the run did not use it
    assert found.output is None
    assert marker not in json.dumps(events(factory, run_id), default=str)
    assert marker not in everything_logged(caplog)
    assert failures.is_provider_failure(found.error)  # the user saves it again, then resumes


def test_a_shaped_credential_is_used_and_hidden_for_the_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    marker: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control of the test above: the same spy sees a credential of the shape."""
    secret = HEAD + marker + "Ab1_" * 8
    user = add_user(factory, "alice@example.org")
    save_row(factory, keyring, user, API, secret)
    run_id = new_run(factory, user, {"message": "hello"})
    spy = Spy(runner.hidden_from_logs)
    monkeypatch.setattr(runner, "hidden_from_logs", spy)
    rc = context(settings, factory, keyring, lambda: FakeBackend(speed=0.0))
    assert asyncio.run(runner.execute(rc, run_id)) is RunStatus.done
    assert spy.asked == [secret]
    assert run_row(factory, run_id).credential_kind is API


# --- Verify -------------------------------------------------------------------------------------

VERIFY = "/api/me/model-credential/verify"


class Checker:
    """The credential check, recording every secret it is handed."""

    def __init__(self) -> None:
        self.handed: list[str] = []

    def check(self, kind: CredentialKind, secret: str) -> agents_verify.Result:
        self.handed.append(secret)
        return agents_verify.Result(agents_verify.Outcome.verified)


def alice_id(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory() as db:
        return db.scalars(sa.select(User.id).where(User.email == ALICE)).one()


def stored_row(factory: sessionmaker[Session], user_id: uuid.UUID) -> tuple[Any, ...]:
    with factory() as db:
        row = db.get(ModelCredential, user_id)
        assert row is not None
        return (row.kind, row.secret_encrypted, row.verified_at, row.updated_at)


@pytest.mark.parametrize("row", list(AROUND.values()), ids=list(AROUND))
def test_verify_refuses_a_stored_credential_off_the_shape_before_using_it(
    app: FastAPI,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    key: str,
    anthropic_api: FakeAnthropic,
    marker: str,
    row: Callable[[str], tuple[CredentialKind, str]],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kind, secret = row(marker)
    user = alice_id(factory)
    save_row(factory, Keyring.from_text(key), user, kind, secret)
    before = stored_row(factory, user)
    checker = Checker()
    app.state.model_verifier = checker
    spy = Spy(agents_router.hidden)
    monkeypatch.setattr(agents_router, "hidden", spy)
    with caplog.at_level(logging.DEBUG):
        response = api.post(VERIFY, token=alice)
    assert response.status_code == 409
    assert response.json() == {"detail": agents_router.UNUSABLE}
    assert "Paste it again" in agents_router.UNUSABLE
    assert checker.handed == []  # the check never had it
    assert spy.asked == []  # nor the log registry
    assert anthropic_api.requests == []
    assert marker not in response.text
    assert marker not in everything_logged(caplog)
    assert stored_row(factory, user) == before  # the row is left as it was


def test_verify_checks_a_shaped_credential_and_hides_it_meanwhile(
    app: FastAPI,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    key: str,
    marker: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control of the test above, through the same spies."""
    secret = HEAD + marker + "Ab1_" * 8
    save_row(factory, Keyring.from_text(key), alice_id(factory), API, secret)
    checker = Checker()
    app.state.model_verifier = checker
    spy = Spy(agents_router.hidden)
    monkeypatch.setattr(agents_router, "hidden", spy)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "verified"
    assert checker.handed == [secret]
    assert spy.asked == [secret]
    assert marker not in response.text
