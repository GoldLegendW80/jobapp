"""Unit tests of app.identity.security: no database."""

import base64
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app.identity import security
from app.identity.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PasswordRuleError,
    check_password_rules,
    common_passwords,
    hash_dummy,
    hash_password,
    hash_token,
    is_well_formed_token,
    needs_rehash,
    new_token,
    normalize_password,
    tokens_equal,
    verify_password,
)

GOOD = "correct horse battery staple"


# --- hashing ---------------------------------------------------------------------------------


def test_hash_round_trip_is_argon2id() -> None:
    hashed = hash_password(GOOD)
    assert hashed.startswith("$argon2id$")
    assert GOOD not in hashed
    assert verify_password(hashed, GOOD)


def test_same_password_hashes_differently() -> None:
    assert hash_password(GOOD) != hash_password(GOOD)  # random salt


@pytest.mark.parametrize(
    "attempt",
    [
        "correct horse battery stapl",
        "Correct horse battery staple",
        "correct horse battery staple ",
        "",
        "correct horse battery staple\x00",
    ],
)
def test_wrong_password_is_rejected(attempt: str) -> None:
    assert not verify_password(hash_password(GOOD), attempt)


@pytest.mark.parametrize(
    "bad_hash", ["", "not a hash", "$argon2id$v=19$m=1,t=1,p=1$xx$yy", "x" * 5000]
)
def test_malformed_hash_verifies_false_without_raising(bad_hash: str) -> None:
    assert not verify_password(bad_hash, GOOD)


def test_emoji_password_round_trip() -> None:
    password = "🔐🦄 unicorn vault 🚀🌍"
    hashed = hash_password(password)
    assert verify_password(hashed, password)
    assert not verify_password(hashed, "🔐🦄 unicorn vault 🚀🌎")


def test_combining_characters_are_normalised() -> None:
    composed = "café au lait fort"  # é as one code point
    decomposed = "café au lait fort"  # e + combining acute accent
    assert composed != decomposed
    hashed = hash_password(decomposed)
    assert verify_password(hashed, composed)
    assert verify_password(hash_password(composed), decomposed)


def test_compatibility_forms_are_normalised() -> None:
    fullwidth = "ｐａｓｓｗｏｒｄ　ｈｅｒｅ１２"  # full-width letters, digits, ideographic space
    assert normalize_password(fullwidth) == "password here12"
    assert verify_password(hash_password(fullwidth), "password here12")


def test_lone_surrogate_cannot_be_hashed_and_never_verifies() -> None:
    bad = "abcdefghij\ud800"
    with pytest.raises(PasswordRuleError) as err:
        hash_password(bad)
    assert err.value.code == "invalid"
    assert not verify_password(hash_password(GOOD), bad)
    with pytest.raises(PasswordRuleError):
        check_password_rules(bad)


def test_max_length_password_round_trip() -> None:
    password = "Ω" * PASSWORD_MAX_LENGTH  # 256 bytes in UTF-8
    assert verify_password(hash_password(password), password)


def test_needs_rehash() -> None:
    assert not needs_rehash(hash_password(GOOD))
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(GOOD)
    assert needs_rehash(weak)
    assert verify_password(weak, GOOD)  # old hashes still verify, then get upgraded
    assert needs_rehash("garbage")


@pytest.mark.production_argon2
def test_hash_dummy_spends_comparable_time() -> None:
    hashed = hash_password(GOOD)
    hash_dummy()  # warm the cached dummy hash

    def timed(fn: object) -> float:
        start = time.perf_counter()
        for _ in range(3):
            fn()  # type: ignore[operator]
        return (time.perf_counter() - start) / 3

    real = timed(lambda: verify_password(hashed, "wrong password here"))
    dummy = timed(lambda: hash_dummy("wrong password here"))
    assert 0.5 < dummy / real < 2.0, (real, dummy)


@pytest.mark.parametrize("password", ["", "abc", "x" * 200, "abcdefghij\ud800", "🔐" * 5])
def test_hash_dummy_never_raises(password: str) -> None:
    hash_dummy(password)


# --- password rules --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("length", "ok"),
    [
        (PASSWORD_MIN_LENGTH - 1, False),
        (PASSWORD_MIN_LENGTH, True),
        (PASSWORD_MAX_LENGTH, True),
        (PASSWORD_MAX_LENGTH + 1, False),
    ],
)
def test_length_bounds(length: int, ok: bool) -> None:
    password = ("Zq7#mK2!vX" * 20)[:length]
    if ok:
        assert check_password_rules(password) == password
    else:
        with pytest.raises(PasswordRuleError) as err:
            check_password_rules(password)
        assert err.value.code == ("too_short" if length < PASSWORD_MIN_LENGTH else "too_long")


def test_empty_password() -> None:
    with pytest.raises(PasswordRuleError, match="at least 10"):
        check_password_rules("")


def test_length_counts_characters_not_bytes() -> None:
    assert check_password_rules("🦄" * 10) == "🦄" * 10  # 40 bytes, 10 characters
    with pytest.raises(PasswordRuleError):
        check_password_rules("🦄" * 9)


def test_length_is_counted_after_normalisation() -> None:
    # "ﬁ" (one ligature character) becomes "fi" under NFKC: 9 typed characters become 10.
    typed = "ﬁxture7Kq"
    assert len(typed) == 9
    assert check_password_rules(typed) == "fixture7Kq"
    # Decomposed accents compose: 10 code points typed, 8 after NFKC, so too short.
    decomposed = "é" * 2 + "Zq7#mK"
    assert len(decomposed) == 10
    with pytest.raises(PasswordRuleError) as err:
        check_password_rules(decomposed)
    assert err.value.code == "too_short"


def test_rule_returns_the_normalised_password() -> None:
    assert check_password_rules("café Zq7#mK2!") == "café Zq7#mK2!"


@pytest.mark.parametrize(
    "variant", ["password123", "PASSWORD123", "PassWord123", "ｐａｓｓｗｏｒｄ123"]
)
def test_common_password_in_any_case_or_width(variant: str) -> None:
    with pytest.raises(PasswordRuleError) as err:
        check_password_rules(variant)
    assert err.value.code == "too_common"


@pytest.mark.parametrize("common", ["1234567890", "qwertyuiop", "1q2w3e4r5t", "iloveyou123"])
def test_well_known_common_passwords(common: str) -> None:
    with pytest.raises(PasswordRuleError, match="too common"):
        check_password_rules(common)


def test_common_list_is_loaded_casefolded_and_sized() -> None:
    words = common_passwords()
    assert 9_000 <= len(words) <= 11_000
    assert all(w == unicodedata.normalize("NFKC", w).casefold() for w in words)
    assert not any(w.startswith("#") for w in words)
    assert all(PASSWORD_MIN_LENGTH <= len(w) <= PASSWORD_MAX_LENGTH for w in words)


def test_common_list_has_a_licence_note() -> None:
    head = security.COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()[:3]
    assert any("MIT License" in line for line in head)
    assert security.COMMON_PASSWORDS_FILE.with_name("common_passwords.LICENSE").exists()


@pytest.mark.parametrize(
    ("password", "email"),
    [
        ("harald@example.org", "harald@example.org"),
        ("HARALD@Example.ORG", "harald@example.org"),
        ("harald@example.org", "  Harald@Example.org "),
        ("ｈａｒａｌｄ@example.org", "harald@example.org"),
    ],
)
def test_password_equal_to_email(password: str, email: str) -> None:
    with pytest.raises(PasswordRuleError) as err:
        check_password_rules(password, email)
    assert err.value.code == "same_as_email"


def test_password_containing_email_is_allowed() -> None:
    assert check_password_rules("harald@example.org!", "harald@example.org")


def test_good_password_passes_with_email() -> None:
    assert check_password_rules(GOOD, "harald@example.org") == GOOD


def test_rule_messages_have_no_dash_separators() -> None:
    cases = ["short", "x" * 129, "password123", "a@b.co.uk.example"]
    for password in cases:
        with pytest.raises(PasswordRuleError) as err:
            check_password_rules(password, "a@b.co.uk.example")
        message = str(err.value)
        for dash in (" - ", "\u2014", "\u2013"):
            assert dash not in message


# --- tokens ----------------------------------------------------------------------------------


def test_token_shape() -> None:
    token = new_token()
    assert len(token) == security.TOKEN_LENGTH == 43
    assert is_well_formed_token(token)
    raw = base64.urlsafe_b64decode(token + "=")
    assert len(raw) == 32


def test_tokens_are_unique_over_10k() -> None:
    tokens = {new_token() for _ in range(10_000)}
    assert len(tokens) == 10_000
    assert len({hash_token(t) for t in tokens}) == 10_000
    assert all(is_well_formed_token(t) for t in tokens)


def test_token_hash_is_sha256_bytes() -> None:
    token = new_token()
    digest = hash_token(token)
    assert isinstance(digest, bytes)
    assert len(digest) == 32
    assert hash_token(token) == digest  # deterministic
    assert hash_token(token + "x") != digest
    assert digest.hex() == __import__("hashlib").sha256(token.encode()).hexdigest()


@pytest.mark.parametrize(
    "token",
    [
        "",
        "a" * 42,
        "a" * 44,
        "x" * 10_000,
        "a" * 42 + "=",
        "a" * 42 + "+",
        "a" * 42 + "/",
        "a" * 42 + "é",
        "a" * 42 + "\x00",
        "a" * 42 + "\n",
        "' OR 1=1 --" + "a" * 32,
        None,
        12345,
        b"a" * 43,
    ],
)
def test_malformed_tokens(token: object) -> None:
    assert not is_well_formed_token(token)


def test_hash_token_handles_any_string() -> None:
    assert len(hash_token("")) == 32
    assert len(hash_token("x" * 10_000)) == 32
    assert len(hash_token("\ud800")) == 32  # lone surrogate does not raise


def test_tokens_equal() -> None:
    a = hash_token(new_token())
    assert tokens_equal(a, bytes(a))
    assert not tokens_equal(a, hash_token(new_token()))
    assert not tokens_equal(a, a[:-1])
    assert not tokens_equal(a, b"")


# --- cost ------------------------------------------------------------------------------------

PRODUCTION = (3, 65536, 4, 32, 16, "ID")  # RFC 9106 low-memory profile


def params(hasher: PasswordHasher) -> tuple[object, ...]:
    return (
        hasher.time_cost,
        hasher.memory_cost,
        hasher.parallelism,
        hasher.hash_len,
        hasher.salt_len,
        hasher.type.name,
    )


def test_production_parameters_are_unchanged_outside_the_test_suite() -> None:
    """A fresh interpreter, without the test fixtures, hashes with the production parameters:
    the cheap test hasher exists only inside this test process."""
    code = (
        "from app.identity import security as s; h = s._hasher; "
        "print(h.time_cost, h.memory_cost, h.parallelism, h.hash_len, h.salt_len, h.type.name); "
        "print(s.hash_password('correct horse battery staple'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tuple(out[:6]) == tuple(str(v) for v in PRODUCTION)
    assert out[6].startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert params(PasswordHasher.from_parameters(security.PASSWORD_HASH_PARAMETERS)) == PRODUCTION


def test_the_suite_hashes_cheaply_unless_marked() -> None:
    assert params(security._hasher)[:3] == (1, 1024, 1)
    assert hash_password(GOOD).startswith("$argon2id$v=19$m=1024,t=1,p=1$")


@pytest.mark.production_argon2
def test_the_marker_restores_the_production_hasher() -> None:
    assert params(security._hasher) == PRODUCTION
    hashed = hash_password(GOOD)
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert not needs_rehash(hashed)
    # A hash made at the cheap test cost would be upgraded at the next sign in.
    assert needs_rehash(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(GOOD))


@pytest.mark.production_argon2
def test_argon2_cost_is_measured_and_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    """Reports the hashing and verification time on this machine (printed even without -s).
    The bound is loose: the point is to notice a parameter change that makes sign in slow."""
    rounds = 5
    start = time.perf_counter()
    hashes = [hash_password(GOOD) for _ in range(rounds)]
    hash_ms = (time.perf_counter() - start) / rounds * 1000
    start = time.perf_counter()
    for hashed in hashes:
        verify_password(hashed, GOOD)
    verify_ms = (time.perf_counter() - start) / rounds * 1000
    params = security._hasher
    with capsys.disabled():
        print(
            f"\nargon2id t={params.time_cost} m={params.memory_cost}KiB p={params.parallelism}: "
            f"hash {hash_ms:.1f} ms, verify {verify_ms:.1f} ms (mean of {rounds})"
        )
    assert hash_ms < 1000
    assert verify_ms < 1000
