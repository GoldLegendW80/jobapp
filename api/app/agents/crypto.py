"""Encryption of the model credentials at rest: AES-256-GCM with a key from the environment.

Keys. CREDENTIALS_KEY is 32 random bytes, base64 (standard or url-safe alphabet, padding
optional). Generate one with:

    python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"

Every ciphertext is stored with the `key_version` of the key that wrote it: a 31 bit fingerprint
of the key (HMAC-SHA256 of a fixed label), so no counter has to be kept by hand, and a row
written with a key this process does not hold is recognised as such instead of failing as
tampered.

Rotation. Put the old key in CREDENTIALS_KEY_PREVIOUS and a new one in CREDENTIALS_KEY: every
read decrypts with whichever key wrote the row, and a row written by the previous key is
re-encrypted with the current one when it is read (`credentials.reveal`), or all at once by
`python -m app.agents.cli rotate`. Once no row carries the previous version, remove
CREDENTIALS_KEY_PREVIOUS.

Associated data. The ciphertext is bound to the owner's user id and the credential kind
(`associated_data`): a ciphertext copied into another user's row, or relabelled as the other
kind, fails authentication instead of decrypting.

Layout of `secret_encrypted`: 12 byte random nonce, then the AES-GCM ciphertext and its 16 byte
tag. Nothing here logs a key or a plaintext, and no exception carries one.
"""

import base64
import binascii
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
_AAD_PREFIX = b"jobapp/model_credentials/v1"
_FINGERPRINT_LABEL = b"jobapp credentials key id v1"


class KeyFormatError(ValueError):
    """The configured key is not 32 bytes of base64."""


class EncryptError(ValueError):
    """The plaintext cannot be sealed: it is not text that UTF-8 can encode (a lone surrogate).
    Says nothing of the plaintext: the codec's own error carries all of it (issue #670)."""


class DecryptError(Exception):
    """The ciphertext cannot be decrypted: unknown key version, or it fails authentication
    (tampered, truncated, or bound to another user or kind)."""


def parse_key(text: str) -> bytes:
    """The 32 key bytes of a base64 string (standard or url-safe, padding optional)."""
    raw = text.strip()
    if not raw:
        raise KeyFormatError("is empty")
    padded = raw + "=" * (-len(raw) % 4)
    standard = padded.replace("-", "+").replace("_", "/")
    try:
        key = base64.b64decode(standard, validate=True)
    except (binascii.Error, ValueError):
        raise KeyFormatError("must be base64 (32 random bytes)") from None
    if len(key) != KEY_BYTES:
        raise KeyFormatError(f"must decode to {KEY_BYTES} bytes, not {len(key)}")
    return key


def fingerprint(key: bytes) -> int:
    """The key's version number: 31 bits of HMAC-SHA256(key, label), never the key itself."""
    digest = hmac.new(key, _FINGERPRINT_LABEL, hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF


def associated_data(user_id: uuid.UUID, kind: str) -> bytes:
    return b"|".join((_AAD_PREFIX, user_id.bytes, kind.encode("ascii")))


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes
    key_version: int


@dataclass(frozen=True)
class Opened:
    plaintext: str = field(repr=False)
    # True when the row was written by the previous key: the caller re-encrypts it.
    stale: bool = False


@dataclass(frozen=True)
class OpenedBytes:
    plaintext: bytes = field(repr=False)
    # As with Opened, the caller decides whether to re-encrypt a stale value.
    stale: bool = False


@dataclass(frozen=True, repr=False)
class Keyring:
    """The current key (writes and reads) and optionally the previous one (reads only)."""

    current: bytes
    previous: bytes | None = None
    _by_version: dict[int, bytes] = field(init=False, compare=False)

    def __post_init__(self) -> None:
        for key in (self.current, self.previous):
            if key is not None and len(key) != KEY_BYTES:
                raise KeyFormatError(f"must be {KEY_BYTES} bytes")
        versions = {fingerprint(self.current): self.current}
        if self.previous is not None:
            versions.setdefault(fingerprint(self.previous), self.previous)
        object.__setattr__(self, "_by_version", versions)

    def __repr__(self) -> str:  # never print key bytes
        return f"Keyring(version={self.version}, previous={self.previous is not None})"

    @classmethod
    def from_text(cls, current: str, previous: str | None = None) -> "Keyring":
        return cls(parse_key(current), parse_key(previous) if previous else None)

    @property
    def version(self) -> int:
        return fingerprint(self.current)

    def encrypt(self, plaintext: str, user_id: uuid.UUID, kind: str) -> Sealed:
        try:
            data = plaintext.encode("utf-8")
        except UnicodeEncodeError:
            # Raised outside the handler: UnicodeEncodeError holds the whole plaintext, and
            # would ride along as the context of anything raised inside it.
            pass
        else:
            return self.encrypt_bytes(data, user_id, kind)
        raise EncryptError("not UTF-8 text")

    def encrypt_bytes(self, plaintext: bytes | bytearray, user_id: uuid.UUID, kind: str) -> Sealed:
        """Seal bytes with the existing layout and owner/kind binding.

        For originals, callers supply an ASCII purpose and final document UUID as kind,
        for example ``cv_original/v1/<upload_id.hex>``. This method does not store a file.
        """
        nonce = os.urandom(NONCE_BYTES)
        body = AESGCM(self.current).encrypt(nonce, plaintext, associated_data(user_id, kind))
        return Sealed(nonce + body, self.version)

    def decrypt(self, ciphertext: bytes, key_version: int, user_id: uuid.UUID, kind: str) -> Opened:
        opened = self.decrypt_bytes(ciphertext, key_version, user_id, kind)
        try:
            plaintext = opened.plaintext.decode("utf-8")
        except UnicodeDecodeError:
            # Raise outside the handler: UnicodeDecodeError itself carries the plaintext.
            pass
        else:
            return Opened(plaintext, stale=opened.stale)
        raise DecryptError("not UTF-8 text") from None

    def decrypt_bytes(
        self, ciphertext: bytes, key_version: int, user_id: uuid.UUID, kind: str
    ) -> OpenedBytes:
        """Authenticate bytes using the writing key; previous-key reads are marked stale."""
        key = self._by_version.get(key_version)
        if key is None:
            raise DecryptError("written with a key this server does not hold")
        if len(ciphertext) < NONCE_BYTES + TAG_BYTES:
            raise DecryptError("too short")
        nonce, body = ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:]
        try:
            plain = AESGCM(key).decrypt(nonce, body, associated_data(user_id, kind))
        except InvalidTag:
            raise DecryptError("fails authentication") from None
        return OpenedBytes(plain, stale=key_version != self.version)
