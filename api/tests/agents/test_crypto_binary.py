"""Binary Keyring contract, without enabling retained original storage."""

import hashlib
import json
import traceback
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.agents import crypto
from app.agents.crypto import NONCE_BYTES, DecryptError, Keyring, Opened, OpenedBytes
from app.mail.models import TOKEN_KIND

USER = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
DOCUMENT = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
KIND = f"cv_original/v1/{DOCUMENT.hex}"
CURRENT = bytes(range(32))
PREVIOUS = bytes(range(32, 64))
FIXTURE = Path(__file__).parent / "fixtures/credentials_cryptography_46_0_7.json"
ROWS = json.loads(FIXTURE.read_text())["rows"]


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", b"\xff\xfe\x80\xc0", bytes(range(256)), b"\x00" * 4096],
    ids=["empty", "zero", "invalid-utf8", "all-byte-values", "zero-block"],
)
def test_arbitrary_bytes_roundtrip_without_encoding(payload: bytes) -> None:
    keys = Keyring(CURRENT)
    assert callable(getattr(keys, "encrypt_bytes", None)), "Keyring needs binary encryption"
    assert callable(getattr(keys, "decrypt_bytes", None)), "Keyring needs binary decryption"
    sealed = keys.encrypt_bytes(payload, USER, KIND)
    assert sealed.key_version == keys.version
    assert type(sealed.ciphertext) is bytes
    assert len(sealed.ciphertext) == len(payload) + 28
    opened = keys.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)
    assert type(opened.plaintext) is bytes
    assert opened.plaintext == payload
    assert opened.stale is False


def test_bytearray_is_accepted_without_mutating_input() -> None:
    payload = bytearray(range(256))
    before = bytes(payload)
    keys = Keyring(CURRENT)
    assert callable(getattr(keys, "encrypt_bytes", None)), "Keyring needs binary encryption"
    sealed = keys.encrypt_bytes(payload, USER, KIND)
    assert bytes(payload) == before
    opened = keys.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)
    assert opened.plaintext == before
    payload[:] = b"x" * len(payload)
    assert opened.plaintext == before


def test_repeated_binary_encryption_uses_fresh_nonces() -> None:
    keys = Keyring(CURRENT)
    assert callable(getattr(keys, "encrypt_bytes", None)), "Keyring needs binary encryption"
    first = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    second = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    assert first.ciphertext[:NONCE_BYTES] != second.ciphertext[:NONCE_BYTES]
    assert first.ciphertext != second.ciphertext


def test_string_api_refuses_non_utf8_without_carrying_plaintext(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = Keyring(CURRENT)
    payload = b"\xffe0-binary-boundary-canary\x00\x80"
    sealed = keys.encrypt_bytes(payload, USER, KIND)
    with pytest.raises(DecryptError, match=r"^not UTF-8 text$") as refused:
        keys.decrypt(sealed.ciphertext, sealed.key_version, USER, KIND)
    assert refused.value.args == ("not UTF-8 text",)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert refused.value.__suppress_context__ is True
    output = capsys.readouterr()
    visible = (
        str(refused.value)
        + repr(refused.value)
        + "".join(traceback.format_exception(refused.value))
    )
    visible += caplog.text + output.out + output.err
    assert "e0-binary-boundary-canary" not in visible


@pytest.mark.parametrize(
    ("user", "kind"),
    [
        (uuid.UUID("00000000-0000-4000-8000-0000000000b2"), KIND),
        (USER, "cv_original/v1/000000000000400080000000000000d2"),
        (USER, f"cv_preview/v1/{DOCUMENT.hex}"),
        (USER, f"cv_original/v2/{DOCUMENT.hex}"),
        (USER, "anthropic_api_key"),
        (USER, TOKEN_KIND),
    ],
    ids=["owner", "document", "purpose", "purpose-version", "credential", "gmail"],
)
def test_binary_ciphertext_cannot_move_to_other_bindings(user: uuid.UUID, kind: str) -> None:
    keys = Keyring(CURRENT)
    sealed = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    with pytest.raises(DecryptError, match=r"^fails authentication$"):
        keys.decrypt_bytes(sealed.ciphertext, sealed.key_version, user, kind)


def test_previous_key_reads_are_stale_and_writes_use_only_current() -> None:
    payload = bytes(range(256))
    old = Keyring(PREVIOUS)
    sealed = old.encrypt_bytes(payload, USER, KIND)
    rotated = Keyring(CURRENT, PREVIOUS)
    opened = rotated.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)
    assert opened == OpenedBytes(payload, stale=True)
    fresh = rotated.encrypt_bytes(opened.plaintext, USER, KIND)
    current_only = Keyring(CURRENT)
    assert fresh.key_version == current_only.version != sealed.key_version
    assert current_only.decrypt_bytes(
        fresh.ciphertext, fresh.key_version, USER, KIND
    ) == OpenedBytes(payload, stale=False)
    # Also verify the actual writing key, independently of Keyring's decryption choice.
    assert (
        AESGCM(CURRENT).decrypt(
            fresh.ciphertext[:12], fresh.ciphertext[12:], crypto.associated_data(USER, KIND)
        )
        == payload
    )
    with pytest.raises(DecryptError, match=r"^written with a key this server does not hold$"):
        current_only.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)


@pytest.mark.parametrize("position", [0, NONCE_BYTES, NONCE_BYTES + 5, -1])
def test_binary_nonce_body_and_tag_tampering_are_refused(position: int) -> None:
    keys = Keyring(CURRENT)
    sealed = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    damaged = bytearray(sealed.ciphertext)
    damaged[position] ^= 1
    with pytest.raises(DecryptError, match=r"^fails authentication$") as refused:
        keys.decrypt_bytes(bytes(damaged), sealed.key_version, USER, KIND)
    assert refused.value.args == ("fails authentication",)
    assert refused.value.__cause__ is None


@pytest.mark.parametrize("length", [0, 1, 11, 12, 27, 28, 100, 283])
def test_binary_truncation_is_a_controlled_error(length: int) -> None:
    keys = Keyring(CURRENT)
    sealed = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    message = "too short" if length < 28 else "fails authentication"
    with pytest.raises(DecryptError, match=f"^{message}$"):
        keys.decrypt_bytes(sealed.ciphertext[:length], sealed.key_version, USER, KIND)


def test_binary_unknown_version_and_wrong_key_are_controlled_errors() -> None:
    keys = Keyring(CURRENT)
    sealed = keys.encrypt_bytes(bytes(range(256)), USER, KIND)
    stranger = Keyring(PREVIOUS)
    with pytest.raises(DecryptError, match=r"^written with a key this server does not hold$"):
        stranger.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)
    with pytest.raises(DecryptError, match=r"^fails authentication$"):
        stranger.decrypt_bytes(sealed.ciphertext, stranger.version, USER, KIND)


def test_binary_plaintext_is_absent_from_repr_errors_and_logs(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = b"e0-repr-canary-701dfde2\x00\xff"
    keys = Keyring(CURRENT)
    sealed = keys.encrypt_bytes(payload, USER, KIND)
    opened = keys.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, KIND)
    assert repr(opened) == "OpenedBytes(stale=False)"
    with pytest.raises(DecryptError, match=r"^fails authentication$") as refused:
        keys.decrypt_bytes(sealed.ciphertext, sealed.key_version, USER, "other-purpose")
    output = capsys.readouterr()
    visible = repr(opened) + repr(refused.value) + str(refused.value) + caplog.text
    visible += "".join(traceback.format_exception(refused.value)) + output.out + output.err
    for marker in ("e0-repr-canary-701dfde2", payload.hex()):
        assert marker not in visible


@pytest.mark.parametrize("row", ROWS, ids=[row["name"] for row in ROWS])
def test_binary_methods_read_and_write_existing_46_0_7_fixtures(
    row: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = hashlib.sha256(b"jobapp test fixture key, not a secret: current").digest()
    previous = hashlib.sha256(b"jobapp test fixture key, not a secret: previous").digest()
    keys = Keyring(current, previous)
    ciphertext = bytes.fromhex(row["ciphertext_hex"])
    user = uuid.UUID(row["user_id"])
    opened = keys.decrypt_bytes(ciphertext, row["key_version"], user, row["kind"])
    assert opened == OpenedBytes(row["plaintext"].encode("utf-8"), stale=row["key"] == "previous")
    monkeypatch.setattr(crypto.os, "urandom", lambda size: ciphertext[:size])
    writer = Keyring(current if row["key"] == "current" else previous)
    rewritten = writer.encrypt_bytes(opened.plaintext, user, row["kind"])
    assert rewritten.ciphertext == ciphertext
    assert rewritten.key_version == row["key_version"]


@pytest.mark.parametrize("kind", ["anthropic_api_key", "claude_subscription", TOKEN_KIND])
@pytest.mark.parametrize("use_previous", [False, True], ids=["current", "previous"])
def test_string_binary_compatibility_preserves_old_layout_and_stale_semantics(
    kind: str, use_previous: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = bytes(range(12))
    text = "e0-invented-token-é-日本語"
    key = PREVIOUS if use_previous else CURRENT
    aad = b"jobapp/model_credentials/v1|" + USER.bytes + b"|" + kind.encode("ascii")
    expected = nonce + AESGCM(key).encrypt(nonce, text.encode("utf-8"), aad)
    monkeypatch.setattr(crypto.os, "urandom", lambda size: nonce[:size])
    writer = Keyring(key)
    binary = writer.encrypt_bytes(text.encode("utf-8"), USER, kind)
    string = writer.encrypt(text, USER, kind)
    assert binary == string
    assert string.ciphertext == expected
    keys = Keyring(CURRENT, PREVIOUS)
    assert keys.decrypt(expected, string.key_version, USER, kind) == Opened(
        text, stale=use_previous
    )
    assert keys.decrypt_bytes(expected, string.key_version, USER, kind) == OpenedBytes(
        text.encode("utf-8"), stale=use_previous
    )
