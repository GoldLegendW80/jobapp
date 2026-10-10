"""Credentials written before an upgrade of `cryptography` stay readable after it (issue #19).

`fixtures/credentials_cryptography_46_0_7.json` holds ciphertexts written through
`Keyring.encrypt` by cryptography 46.0.7 (OpenSSL 3.5.6) inside the api's base image on arm64,
the version and the architecture that wrote the rows stored on the Jetson. The installed version
must read every one of them, and must write the very same bytes for the same key, nonce and
plaintext, so a rollback to the previous image reads what the new one wrote.

Nothing here is a secret: the two keys are the SHA-256 of the public labels below, the nonces
were fixed when the fixture was written, and the plaintexts are made up.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import cryptography
import pytest

from app.agents import crypto
from app.agents.crypto import NONCE_BYTES, TAG_BYTES, DecryptError, Keyring, fingerprint

FIXTURE = Path(__file__).parent / "fixtures" / "credentials_cryptography_46_0_7.json"
DOCUMENT = json.loads(FIXTURE.read_text(encoding="utf-8"))
ROWS: list[dict[str, Any]] = DOCUMENT["rows"]

# Throwaway test keys, derived from public text: never used outside this file.
CURRENT = hashlib.sha256(b"jobapp test fixture key, not a secret: current").digest()
PREVIOUS = hashlib.sha256(b"jobapp test fixture key, not a secret: previous").digest()
KEYS = {"current": CURRENT, "previous": PREVIOUS}
BOB = uuid.UUID("00000000-0000-4000-8000-0000000000b2")

by_name = pytest.mark.parametrize("row", ROWS, ids=[row["name"] for row in ROWS])


def ring() -> Keyring:
    return Keyring(CURRENT, PREVIOUS)


def test_the_fixture_is_from_the_old_version_and_this_run_is_not() -> None:
    assert DOCUMENT["written_by"]["cryptography"] == "46.0.7"
    assert DOCUMENT["written_by"]["machine"] == "aarch64"
    # 50.0.0 is the first release without the advisories of issue #19.
    assert int(cryptography.__version__.split(".")[0]) >= 50
    # Every shape that matters: both kinds, both keys, empty, unicode and a long value.
    assert {row["kind"] for row in ROWS} == {"anthropic_api_key", "claude_subscription"}
    assert {row["key"] for row in ROWS} == {"current", "previous"}
    assert any(row["plaintext"] == "" for row in ROWS)
    assert any(not row["plaintext"].isascii() for row in ROWS)
    assert any(len(row["plaintext"]) >= 4096 for row in ROWS)


@by_name
def test_old_ciphertext_decrypts(row: dict[str, Any]) -> None:
    ciphertext = bytes.fromhex(row["ciphertext_hex"])
    assert len(ciphertext) == NONCE_BYTES + len(row["plaintext"].encode()) + TAG_BYTES
    assert row["key_version"] == fingerprint(KEYS[row["key"]])
    opened = ring().decrypt(ciphertext, row["key_version"], uuid.UUID(row["user_id"]), row["kind"])
    assert opened.plaintext == row["plaintext"]
    # A row of the previous key is re-encrypted by its reader, as before the upgrade.
    assert opened.stale is (row["key"] == "previous")


@by_name
def test_old_ciphertext_keeps_its_guarantees(row: dict[str, Any]) -> None:
    ciphertext = bytes.fromhex(row["ciphertext_hex"])
    user = uuid.UUID(row["user_id"])
    other_kind = (
        "claude_subscription" if row["kind"] == "anthropic_api_key" else "anthropic_api_key"
    )
    for position in (0, NONCE_BYTES, -1):
        tampered = bytearray(ciphertext)
        tampered[position] ^= 0x01
        with pytest.raises(DecryptError, match="authentication"):
            ring().decrypt(bytes(tampered), row["key_version"], user, row["kind"])
    with pytest.raises(DecryptError, match="authentication"):
        ring().decrypt(ciphertext, row["key_version"], BOB, row["kind"])
    with pytest.raises(DecryptError, match="authentication"):
        ring().decrypt(ciphertext, row["key_version"], user, other_kind)
    with pytest.raises(DecryptError):
        ring().decrypt(ciphertext[:-1], row["key_version"], user, row["kind"])
    # A server that no longer holds the writing key says so instead of "tampered".
    stranger = Keyring(hashlib.sha256(b"jobapp test fixture key, not a secret: other").digest())
    with pytest.raises(DecryptError, match="does not hold"):
        stranger.decrypt(ciphertext, row["key_version"], user, row["kind"])


@by_name
def test_this_version_writes_the_same_bytes(
    row: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same key, nonce, plaintext and associated data: the same ciphertext and tag, so the old
    version reads what this one writes (a rollback of the image loses no credential)."""
    old = bytes.fromhex(row["ciphertext_hex"])
    monkeypatch.setattr(crypto.os, "urandom", lambda size: old[:size])
    sealed = Keyring(KEYS[row["key"]]).encrypt(
        row["plaintext"], uuid.UUID(row["user_id"]), row["kind"]
    )
    assert sealed.ciphertext == old
    assert sealed.key_version == row["key_version"]


def test_rotation_of_an_old_row_ends_on_the_current_key() -> None:
    row = next(row for row in ROWS if row["key"] == "previous")
    user = uuid.UUID(row["user_id"])
    opened = ring().decrypt(
        bytes.fromhex(row["ciphertext_hex"]), row["key_version"], user, row["kind"]
    )
    resealed = ring().encrypt(opened.plaintext, user, row["kind"])
    assert resealed.key_version == fingerprint(CURRENT)
    after = Keyring(CURRENT)
    assert (
        after.decrypt(resealed.ciphertext, resealed.key_version, user, row["kind"]).plaintext
        == (row["plaintext"])
    )
