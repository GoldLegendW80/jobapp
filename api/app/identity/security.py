"""Security primitives: password hashing and rules, opaque tokens and their hashes.

Passwords are NFKC-normalised before every rule and before hashing, so the same password typed
with composed or decomposed accents (or full-width digits) verifies the same way. Hashes are
argon2id with the argon2-cffi defaults (RFC 9106 low-memory profile). Tokens are 32 random
bytes, URL-safe base64 without padding (43 characters); only their SHA-256 is ever stored.
Nothing here logs: callers must never log a password, a token or a hash either.
"""

import contextlib
import hashlib
import hmac
import re
import secrets
import unicodedata
from functools import cache
from pathlib import Path

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
TOKEN_BYTES = 32
TOKEN_LENGTH = 43  # len(base64url(32 bytes)) without padding
COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")

_TOKEN_RE = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")
# The production cost, pinned: argon2id, RFC 9106 low-memory profile (t=3, m=64 MiB, p=4), the
# argon2-cffi default, named here so an upgrade of the library cannot change it unseen. Nothing
# reads a setting for it. The test suite swaps `_hasher` for a cheap one in-process
# (tests/conftest.py), and tests/identity/test_security.py checks that a fresh interpreter still
# gets exactly these parameters.
PASSWORD_HASH_PARAMETERS = profiles.RFC_9106_LOW_MEMORY
_hasher = PasswordHasher.from_parameters(PASSWORD_HASH_PARAMETERS)


class PasswordRuleError(ValueError):
    """A password breaks a rule. `code` is stable for the API; the message is user-facing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def _encodable(password: str) -> bool:
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates, which JSON can carry
        return False
    return True


@cache
def common_passwords() -> frozenset[str]:
    """The bundled list, casefolded. Loaded once, on first use."""
    lines = COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(
        normalize_password(line).casefold() for line in lines if line and not line.startswith("#")
    )


def check_password_rules(password: str, email: str | None = None) -> str:
    """Return the normalised password, or raise PasswordRuleError naming the first broken rule.

    Length is counted in characters (code points) after NFKC normalisation.
    """
    normalized = normalize_password(password)
    if not _encodable(normalized):
        raise PasswordRuleError("invalid", "The password contains characters that are not valid.")
    if len(normalized) < PASSWORD_MIN_LENGTH:
        raise PasswordRuleError(
            "too_short", f"Use at least {PASSWORD_MIN_LENGTH} characters for the password."
        )
    if len(normalized) > PASSWORD_MAX_LENGTH:
        raise PasswordRuleError(
            "too_long", f"Use at most {PASSWORD_MAX_LENGTH} characters for the password."
        )
    folded = normalized.casefold()
    if email is not None and folded == normalize_password(email).strip().casefold():
        raise PasswordRuleError("same_as_email", "The password cannot be your email address.")
    if folded in common_passwords():
        raise PasswordRuleError(
            "too_common", "This password is too common. Choose one that is harder to guess."
        )
    return normalized


def hash_password(password: str) -> str:
    """argon2id hash of the normalised password. Rules are checked separately."""
    normalized = normalize_password(password)
    if not _encodable(normalized):
        raise PasswordRuleError("invalid", "The password contains characters that are not valid.")
    return _hasher.hash(normalized)


def verify_password(password_hash: str, password: str) -> bool:
    """True when the password matches. A malformed hash or bad input is False, never an error."""
    normalized = normalize_password(password)
    if not _encodable(normalized):
        hash_dummy()
        return False
    try:
        return _hasher.verify(password_hash, normalized)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with other parameters than the current ones."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def hash_dummy(password: str = "") -> None:
    """Spend the time of one verification, for an unknown email, so timing reveals nothing."""
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_dummy_hash(), normalize_password(password).encode("utf-8", "replace"))


def new_token() -> str:
    """A fresh opaque token for a cookie or a link: 32 random bytes, URL-safe, 43 characters."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def is_well_formed_token(token: object) -> bool:
    """Cheap shape check before any database work: right length and alphabet only."""
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None


def hash_token(token: str) -> bytes:
    """SHA-256 of the token, 32 bytes: what the database stores and looks up."""
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()


def tokens_equal(a: bytes, b: bytes) -> bool:
    """Constant-time comparison of two token hashes."""
    return hmac.compare_digest(a, b)
