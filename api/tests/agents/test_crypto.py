"""crypto.py without a database: AES-GCM round trip, wrong key, rotation, the associated data
binding a ciphertext to its user and kind, tampering, and the key format."""

import base64
import secrets
import uuid

import pytest

from app.agents.crypto import (
    KEY_BYTES,
    NONCE_BYTES,
    TAG_BYTES,
    DecryptError,
    KeyFormatError,
    Keyring,
    fingerprint,
    parse_key,
)

ALICE = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
BOB = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
API = "anthropic_api_key"
SUB = "claude_subscription"
SECRET = "sk-ant-api03-" + "x" * 90


def ring(previous: bool = False) -> Keyring:
    return Keyring(
        secrets.token_bytes(KEY_BYTES), secrets.token_bytes(KEY_BYTES) if previous else None
    )


def test_round_trip() -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    assert sealed.key_version == keys.version
    assert len(sealed.ciphertext) == NONCE_BYTES + len(SECRET) + TAG_BYTES
    assert SECRET.encode() not in sealed.ciphertext
    opened = keys.decrypt(sealed.ciphertext, sealed.key_version, ALICE, API)
    assert opened.plaintext == SECRET
    assert opened.stale is False


def test_unicode_and_long_values_round_trip() -> None:
    keys = ring()
    for text in ("é" * 3000, "日本語🔑", ""):
        sealed = keys.encrypt(text, ALICE, SUB)
        assert keys.decrypt(sealed.ciphertext, sealed.key_version, ALICE, SUB).plaintext == text


def test_every_encryption_uses_a_fresh_nonce() -> None:
    keys = ring()
    first = keys.encrypt(SECRET, ALICE, API).ciphertext
    second = keys.encrypt(SECRET, ALICE, API).ciphertext
    assert first[:NONCE_BYTES] != second[:NONCE_BYTES]
    assert first != second


def test_wrong_key_is_refused() -> None:
    sealed = ring().encrypt(SECRET, ALICE, API)
    other = ring()
    # Unknown version: refused before any decryption.
    with pytest.raises(DecryptError, match="does not hold"):
        other.decrypt(sealed.ciphertext, sealed.key_version, ALICE, API)
    # Even when the version is forged to the other key's, authentication fails.
    with pytest.raises(DecryptError, match="authentication"):
        other.decrypt(sealed.ciphertext, other.version, ALICE, API)


def test_rotation_reads_the_previous_key_and_marks_the_row_stale() -> None:
    old = ring()
    sealed = old.encrypt(SECRET, ALICE, API)
    rotated = Keyring(secrets.token_bytes(KEY_BYTES), old.current)
    opened = rotated.decrypt(sealed.ciphertext, sealed.key_version, ALICE, API)
    assert opened.plaintext == SECRET
    assert opened.stale is True
    resealed = rotated.encrypt(opened.plaintext, ALICE, API)
    assert resealed.key_version == rotated.version != sealed.key_version
    # Once the previous key is gone, only the re-encrypted row still reads.
    after = Keyring(rotated.current)
    assert after.decrypt(resealed.ciphertext, resealed.key_version, ALICE, API).stale is False
    with pytest.raises(DecryptError):
        after.decrypt(sealed.ciphertext, sealed.key_version, ALICE, API)


def test_previous_key_never_writes() -> None:
    keys = ring(previous=True)
    assert keys.encrypt(SECRET, ALICE, API).key_version == fingerprint(keys.current)


def test_ciphertext_of_one_user_fails_for_another() -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    with pytest.raises(DecryptError, match="authentication"):
        keys.decrypt(sealed.ciphertext, sealed.key_version, BOB, API)


def test_ciphertext_of_one_kind_fails_for_the_other() -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    with pytest.raises(DecryptError, match="authentication"):
        keys.decrypt(sealed.ciphertext, sealed.key_version, ALICE, SUB)


@pytest.mark.parametrize("where", [0, NONCE_BYTES, NONCE_BYTES + 5, -1])
def test_tampered_ciphertext_is_refused(where: int) -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    data = bytearray(sealed.ciphertext)
    data[where] ^= 0x01
    with pytest.raises(DecryptError, match="authentication"):
        keys.decrypt(bytes(data), sealed.key_version, ALICE, API)


@pytest.mark.parametrize("cut", [0, 1, NONCE_BYTES, NONCE_BYTES + TAG_BYTES - 1])
def test_truncated_ciphertext_is_refused(cut: int) -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    with pytest.raises(DecryptError):
        keys.decrypt(sealed.ciphertext[:cut], sealed.key_version, ALICE, API)


def test_truncated_tag_is_refused() -> None:
    keys = ring()
    sealed = keys.encrypt(SECRET, ALICE, API)
    with pytest.raises(DecryptError):
        keys.decrypt(sealed.ciphertext[:-1], sealed.key_version, ALICE, API)


def test_fingerprint_is_stable_and_31_bits() -> None:
    key = b"\x01" * KEY_BYTES
    assert fingerprint(key) == fingerprint(bytes(key))
    assert 0 <= fingerprint(key) < 2**31
    assert fingerprint(key) != fingerprint(b"\x02" * KEY_BYTES)


def test_repr_never_shows_key_or_secret() -> None:
    keys = ring(previous=True)
    text = repr(keys)
    assert keys.current.hex() not in text
    assert base64.b64encode(keys.current).decode() not in text
    opened = keys.decrypt(*_sealed(keys), ALICE, API)
    assert SECRET not in repr(opened)


def _sealed(keys: Keyring) -> tuple[bytes, int]:
    sealed = keys.encrypt(SECRET, ALICE, API)
    return sealed.ciphertext, sealed.key_version


# --- key format -------------------------------------------------------------------------------


def test_parse_key_accepts_standard_and_url_safe_base64() -> None:
    raw = bytes(range(250, 256)) + secrets.token_bytes(26)
    standard = base64.b64encode(raw).decode()
    urlsafe = base64.urlsafe_b64encode(raw).decode()
    assert parse_key(standard) == raw
    assert parse_key(urlsafe) == raw
    assert parse_key(urlsafe.rstrip("=")) == raw
    assert parse_key(f"  {standard}\n") == raw


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is empty"),
        ("   ", "is empty"),
        ("not base64 at all!", "must be base64"),
        (base64.b64encode(b"x" * 16).decode(), "must decode to 32 bytes, not 16"),
        (base64.b64encode(b"x" * 33).decode(), "must decode to 32 bytes, not 33"),
        ("change-me", "must"),
    ],
)
def test_parse_key_refuses(text: str, message: str) -> None:
    with pytest.raises(KeyFormatError, match=message):
        parse_key(text)


def test_keyring_refuses_short_keys() -> None:
    with pytest.raises(KeyFormatError):
        Keyring(b"short")
    with pytest.raises(KeyFormatError):
        Keyring(b"k" * KEY_BYTES, b"short")


def test_from_text() -> None:
    current = base64.b64encode(b"c" * KEY_BYTES).decode()
    previous = base64.b64encode(b"p" * KEY_BYTES).decode()
    keys = Keyring.from_text(current, previous)
    assert keys.current == b"c" * KEY_BYTES
    assert keys.previous == b"p" * KEY_BYTES
    assert Keyring.from_text(current).previous is None
