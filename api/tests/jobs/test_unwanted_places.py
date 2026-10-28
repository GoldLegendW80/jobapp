"""Unwanted places (`search.location_exclude`, owner 2026-10-27, #626): a posting whose location
matches one is not kept, through the same matcher as the unwanted titles
(app/profile/matching.py). The website search leaves such postings out before the store
(tests/browser/test_unwanted_places_interactive.py, tests/jobs/test_interactive_findings.py);
this file states what the matcher does with places, both ways, and that a sweep keeps the list
frozen at its start.

Whole words in any case, an optional plural, punctuation and spaces as word edges; what it leaves
to the person: no accent folding, no synonyms or translations, words written as typed."""

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.profile import matching
from tests.jobs.sweep_helpers import new_sweep, now, read_sweep, search, set_profile


def unwanted(location: str, terms: list[str], advanced: bool = False) -> bool:
    return matching.matches(terms, advanced, location)


PLAIN = ["United States", "paris"]

# Left out by the plain list above.
DROPPED = [
    "United States",
    "united states",
    "UNITED STATES",
    "New York, United States",
    "Remote, United States",
    "Remote (United States)",
    "United States of America",
    "Paris",
    "PARIS",
    "paris",
    "Paris, France",
    "Paris 75008",
    "Paris/Remote",
    "Lyon; Paris",
    "Paris.",
    "Paris-Saclay",  # a hyphen is a word edge, as for every term list
    "Paris France",  # a no-break space too
    "Hybrid: Paris or Lyon",
    "Parises",  # the optional plural of every plain term
    "x " * 50_000 + "Paris",  # a long location is read whole
]

# Kept by the plain list above: other places, near misses, and the declared limits.
KEPT = [
    "",
    "Remote",
    "Lyon, France",
    "United Kingdom",
    "United",
    "States",
    "Parisian Quarter, Lyon",  # a longer word
    "Comparis Office",
    "Unitedstates",
    "United  States",  # words as typed: two spaces are another text
    "Pâris",  # no accent folding: the person writes the form the websites use
    "Ｐａｒｉｓ",  # full width letters are other characters
    "États-Unis",  # no translation
    "USA",  # no synonyms
    "US",
    "Remote, Europe",
]


@pytest.mark.parametrize("location", DROPPED)
def test_an_unwanted_place_leaves_the_posting_out(location: str) -> None:
    assert unwanted(location, PLAIN)


@pytest.mark.parametrize("location", KEPT)
def test_other_places_are_kept(location: str) -> None:
    assert not unwanted(location, PLAIN)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Austin, US", True),
        ("Austin, U.S.A.", True),
        ("London, United Kingdom", True),
        ("Remote, United States", True),
        ("Australia", False),
        ("Busan, South Korea", False),
        ("Remote", False),
    ],
)
def test_a_regular_expression_list_is_searched_as_written(location: str, expected: bool) -> None:
    terms = [r"\bU\.?S\.?A?\.?(?!\w)", "united (states|kingdom)"]
    assert unwanted(location, terms, advanced=True) is expected


def test_an_empty_list_leaves_nothing_out() -> None:
    assert matching.compile_terms([], False) is None
    for location in [*DROPPED, *KEPT]:
        assert not unwanted(location, [])


def test_the_run_uses_the_places_frozen_at_its_start(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    listed = {"terms": ["United Kingdom"], "advanced_regex": False}
    set_profile(factory, alice, search(location_exclude=listed))
    sweep_id = new_sweep(factory, alice)
    # Saved after Start: this run keeps reading what was frozen, the next one reads the new list.
    later = {"terms": ["paris"], "advanced_regex": False}
    set_profile(factory, alice, search(location_exclude=later))
    assert read_sweep(factory, sweep_id).stats["search_scope"]["search"]["location_exclude"] == (
        listed
    )
    plan = sweep.claim(factory, sweep_id, now())
    assert plan is not None
    assert plan.search is not None
    assert list(plan.search.location_exclude.terms) == ["United Kingdom"]
    sweep.finish_done(factory, sweep_id, sweep.Stats(), now())
    plan = sweep.claim(factory, new_sweep(factory, alice), now())
    assert plan is not None
    assert plan.search is not None
    assert list(plan.search.location_exclude.terms) == ["paris"]


def test_a_section_saved_before_the_field_reads_with_an_empty_list() -> None:
    old = sweep.SearchSection.model_validate({"location": {"terms": ["paris"]}})
    assert list(old.location_exclude.terms) == []
