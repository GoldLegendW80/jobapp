"""Security primitives: password hashing and rules, opaque tokens and their hashes.

Passwords are NFKC-normalised before every rule and before hashing, so the same password typed
with composed or decomposed accents (or full-width digits) verifies the same way. Hashes are
argon2id with the argon2-cffi defaults (RFC 9106 low-memory profile). Tokens are 32 random
bytes, URL-safe base64 without padding (43 characters); only their SHA-256 is ever stored.
Nothing here logs: callers must never log a password, a token or a hash either.

Every argon2 computation of this module runs inside `password_slots.hold()` (hash_slots.py), so
no caller, present or future, can run more of them at once than the memory budget allows; the
functions that hash raise `HashBusyError` when no slot comes free in time.
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

from app.identity.hash_slots import HashBusyError, HashSlots

__all__ = ["HashBusyError", "HashSlots"]

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

# How many argon2 computations may run at once in this process (issue #108). Each one holds
# m = 64 MiB while it runs, and every caller can ask for one, a caller with no session included
# (sign in, a link's new password). The arithmetic, from compose.yml (api: mem_limit 512m, one
# uvicorn process):
#   512 MiB  the container's limit
# - 173 MiB  the api with its ordinary load, the peak measured before any sign in (issue #108;
#            about 112 MiB at rest, tasks/evidence/platform-100)
# = 339 MiB  to share between password hashes and request bodies in flight.
#   2 slots x 64 MiB = 128 MiB, so the api peaks near 301 MiB under any flood of sign ins and
#   keeps about 210 MiB for bodies. 4 slots (256 MiB, a peak near 429 MiB) would leave 83 MiB,
#   too thin; 1 slot would make every sign in wait behind a single slow one.
# Up to PASSWORD_HASH_MAX_WAITING callers wait for a slot, at most PASSWORD_HASH_MAX_WAIT_S
# each; beyond either bound the answer is 503 with Retry-After. A waiter holds a threadpool
# thread and nothing else (no database connection, no hash memory): 2 + 16 = 18 of the 40
# threads at the worst. A full queue empties in 16 / 2 = 8 rounds of one hash: 0.4 s at the
# 50 ms a hash takes on a development Mac, 3 s if the Jetson needs 375 ms per hash with two
# running together (assumed, not measured there), which is why the wait bound is 3 s.
PASSWORD_HASH_MEMORY_MIB = PASSWORD_HASH_PARAMETERS.memory_cost // 1024
PASSWORD_HASH_SLOTS = 2
PASSWORD_HASH_MAX_WAITING = 16
PASSWORD_HASH_MAX_WAIT_S = 3.0
PASSWORD_HASH_RETRY_AFTER_S = 2
password_slots = HashSlots(
    PASSWORD_HASH_SLOTS,
    PASSWORD_HASH_MAX_WAITING,
    PASSWORD_HASH_MAX_WAIT_S,
    PASSWORD_HASH_RETRY_AFTER_S,
)


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
    with password_slots.hold():
        return _hasher.hash(normalized)


def verify_password(password_hash: str, password: str) -> bool:
    """True when the password matches. A malformed hash or bad input is False, never an error."""
    normalized = normalize_password(password)
    if not _encodable(normalized):
        hash_dummy()
        return False
    try:
        with password_slots.hold():
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
    with password_slots.hold():
        return _hasher.hash(secrets.token_urlsafe(16))


def hash_dummy(password: str = "") -> None:
    """Spend the time of one verification, for an unknown email, so timing reveals nothing.
    It holds a slot like the real verification it stands for (HashBusyError when none)."""
    with (
        password_slots.hold(),
        contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError),
    ):
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
