"""The consent texts are tied to their versions (review of PR #112, B2). No database here.

A consent row records a version, so the words of a version are what the row means. Each
version's text is pinned by a SHA-256: an edit of the words without a new version fails here,
and so does a new version added without its digest. The current version is the highest, so a
consent to an older version never counts for the new words (the route side of that is
`test_a_new_text_asks_again_and_keeps_what_was_accepted_before` in `test_estimate_api.py`).
"""

import hashlib

import pytest

from app.ingestion import estimate_router

# The digest of each version's text, written once when the version is added and never changed.
# Version 1 is pinned as the words stand in PR #112; the owner approves the wording separately,
# and no production row exists yet.
PINNED: dict[int, str] = {
    1: "9a333e84a46593030cf5934de2058120888ca6b197d6a30b26faa3b5ed65baff",
}

RAISE_THE_VERSION = (
    "The words of consent version {version} changed. A user who accepted them accepted the old "
    "words: restore them, and add the new words as version {next} in CONSENT_TEXTS with its "
    "own digest here. Never edit the text or the digest of an existing version."
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def problems(texts: dict[int, str], pinned: dict[int, str], current: int) -> list[str]:
    """Why these texts and this current version do not agree with the pinned digests."""
    found = []
    if set(texts) != set(pinned):
        found.append("every version has a pinned digest, and every digest a version")
    if list(sorted(texts)) != list(range(1, len(texts) + 1)):
        found.append("versions are 1, 2, 3 and so on, none removed")
    if not texts or current != max(texts):
        found.append("the current version is the highest")
    for version in sorted(set(texts) & set(pinned)):
        if digest(texts[version]) != pinned[version]:
            found.append(RAISE_THE_VERSION.format(version=version, next=max(texts) + 1))
    return found


def test_every_version_of_the_consent_text_is_the_one_pinned() -> None:
    assert problems(estimate_router.CONSENT_TEXTS, PINNED, estimate_router.CONSENT_VERSION) == []
    assert set(PINNED) == set(estimate_router.CONSENT_TEXTS)
    assert max(estimate_router.CONSENT_TEXTS) == estimate_router.CONSENT_VERSION
    for version, text in estimate_router.CONSENT_TEXTS.items():
        assert digest(text) == PINNED[version], RAISE_THE_VERSION.format(
            version=version, next=max(PINNED) + 1
        )


def test_the_current_text_is_the_text_of_the_current_version() -> None:
    current = estimate_router.CONSENT_VERSION
    assert estimate_router.CONSENT_TEXTS[current] == estimate_router.CONSENT_TEXT
    assert estimate_router.consent_text(current) == estimate_router.CONSENT_TEXT
    with pytest.raises(LookupError, match="version 2"):
        estimate_router.consent_text(current + 1)


def test_rewording_a_version_without_raising_it_is_caught() -> None:
    """The scenario of the review: a later task adds a sentence and leaves the version at 1."""
    reworded = {1: estimate_router.CONSENT_TEXTS[1] + " Run transcripts are kept."}
    assert problems(reworded, PINNED, 1) == [RAISE_THE_VERSION.format(version=1, next=2)]


def test_a_new_version_needs_its_own_digest_and_becomes_the_current_one() -> None:
    old = estimate_router.CONSENT_TEXTS[1]
    new = old + " Run transcripts are kept."
    # Added without a digest, or with the version constant left behind: caught.
    assert problems({1: old, 2: new}, PINNED, 2) == [
        "every version has a pinned digest, and every digest a version"
    ]
    assert problems({1: old, 2: new}, {**PINNED, 2: digest(new)}, 1) == [
        "the current version is the highest"
    ]
    # Added properly: the old words stay readable under their version.
    assert problems({1: old, 2: new}, {**PINNED, 2: digest(new)}, 2) == []
    # An old version dropped, or replaced by the new words under the old number: caught.
    assert problems({2: new}, {2: digest(new)}, 2) == [
        "versions are 1, 2, 3 and so on, none removed"
    ]
    assert problems({1: new}, PINNED, 1) == [RAISE_THE_VERSION.format(version=1, next=2)]
