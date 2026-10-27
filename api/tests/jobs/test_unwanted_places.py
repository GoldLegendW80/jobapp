"""Unwanted places (`search.location_exclude`, owner 2026-10-27, #626): a posting whose location
matches one is not kept, through the same matcher as the unwanted titles
(app/profile/matching.py), on a fake board over real HTTP, and in the keep decision's table.

The table states what the shared matcher does with places, both ways: whole words in any case,
an optional plural, punctuation and spaces as word edges; and what it leaves to the person (no
accent folding, no synonyms or translations, words written as typed). An unwanted place wins over
a wanted one, as an unwanted title wins over a wanted title."""

import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import ats
from app.jobs.models import SweepStatus
from app.jobs.sweep import SearchFilter
from app.profile.schemas import SearchSection
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import (
    board,
    fake_ats,
    jobs_of,
    new_sweep,
    run,
    search,
    set_profile,
)

__all__ = ["fake_ats"]

TODAY = date(2026, 10, 27)
SINCE = "2026-09-27"
PUBLISHED = "2026-10-20"


def keep_filter(
    unwanted: list[str], advanced: bool = False, wanted: list[str] | None = None
) -> SearchFilter:
    return SearchFilter.of(
        SearchSection.model_validate(
            {
                "title_include": {"terms": ["ai engineer"]},
                "title_exclude": {"terms": ["account executive"]},
                "location": {"terms": wanted or []},
                "location_exclude": {"terms": unwanted, "advanced_regex": advanced},
            }
        )
    )


def reason(found: SearchFilter, location: str, title: str = "AI Engineer") -> str | None:
    return found.terms_reason(title, location, PUBLISHED, None, SINCE)


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
    "Pâris",  # no accent folding: the person writes the form the boards use
    "Ｐａｒｉｓ",  # full width letters are other characters
    "États-Unis",  # no translation
    "USA",  # no synonyms
    "US",
    "Remote, Europe",
]


@pytest.mark.parametrize("location", DROPPED)
def test_an_unwanted_place_leaves_the_posting_out(location: str) -> None:
    assert reason(keep_filter(PLAIN), location) == "location_excluded"


@pytest.mark.parametrize("location", KEPT)
def test_other_places_are_kept(location: str) -> None:
    assert reason(keep_filter(PLAIN), location) is None


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Austin, US", "location_excluded"),
        ("Austin, U.S.A.", "location_excluded"),
        ("London, United Kingdom", "location_excluded"),
        ("Remote, United States", "location_excluded"),
        ("Australia", None),
        ("Busan, South Korea", None),
        ("Remote", None),
    ],
)
def test_a_regular_expression_list_is_searched_as_written(
    location: str, expected: str | None
) -> None:
    found = keep_filter([r"\bU\.?S\.?A?\.?(?!\w)", "united (states|kingdom)"], advanced=True)
    assert reason(found, location) == expected


@pytest.mark.parametrize(
    ("title", "location", "expected"),
    [
        ("AI Engineer", "Remote, United States", "location_excluded"),  # unwanted wins
        ("AI Engineer", "Remote, Europe", None),
        ("AI Engineer", "New York, United States", "location"),  # not a wanted place first
        ("AI Engineer", "Berlin", "location"),
        (
            "AI Engineer, Account Executive",
            "Remote, United States",
            "excluded",
        ),  # the title decides first
        ("Data Analyst", "Remote, United States", "title"),
    ],
)
def test_with_wanted_places_the_unwanted_one_wins(
    title: str, location: str, expected: str | None
) -> None:
    found = keep_filter(["united states"], wanted=["remote", "europe"])
    assert reason(found, location, title) == expected


def test_an_empty_list_leaves_nothing_out() -> None:
    found = keep_filter([])
    assert found.location_exclude is None
    for location in [*DROPPED, *KEPT]:
        assert reason(found, location) is None


def test_a_section_saved_before_the_field_filters_as_before() -> None:
    old = SearchSection.model_validate({"location": {"terms": ["paris"]}})
    assert SearchFilter.of(old).location_exclude is None
    assert SearchFilter.of(old).terms_reason("AI Engineer", "Paris", PUBLISHED, None, SINCE) is None


def test_the_contract_step_still_runs_after_the_places() -> None:
    found = SearchFilter.of(
        SearchSection.model_validate(
            {"location_exclude": {"terms": ["london"]}, "contract_types": ["VIE"]}
        )
    )
    since = found.since(TODAY)
    london = ats.Posting(
        "AI Engineer", "London", "https://jobs.example.org/1", PUBLISHED, "", False
    )
    paris = ats.Posting("AI Engineer", "Paris", "https://jobs.example.org/2", PUBLISHED, "", False)
    assert found.reason(london, None, since) == "location_excluded"
    assert found.reason(paris, None, since) == "contract"


# --- a fake board, end to end -------------------------------------------------------------------


def test_an_unwanted_place_filters_a_posting_of_a_fake_board(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    # fake-ok: "AI Engineer" in Paris, "Applied AI Engineer, Agents" in London, United Kingdom,
    # and "Account Executive" (an unwanted title).
    unwanted = {"terms": ["United Kingdom"], "advanced_regex": False}
    set_profile(factory, alice, search(board("fake-ok"), location_exclude=unwanted))
    row = run(factory, new_sweep(factory, alice), fake_ats)
    assert row.status is SweepStatus.done
    assert [(j.title, j.location) for j in jobs_of(factory, alice)] == [
        ("AI Engineer", "Paris, France")
    ]
    assert (row.stats["seen"], row.stats["matched"], row.stats["new"]) == (3, 1, 1)
    assert row.stats["search_scope"]["search"]["location_exclude"] == unwanted

    # Without the list the same board keeps both postings: the list made the difference.
    set_profile(factory, alice, search(board("fake-ok")))
    again = run(factory, new_sweep(factory, alice), fake_ats)
    assert (again.stats["matched"], again.stats["new"], again.stats["duplicates"]) == (2, 1, 1)
    assert {j.location for j in jobs_of(factory, alice)} == {
        "Paris, France",
        "London, United Kingdom",
    }


def test_the_run_uses_the_places_frozen_at_its_start(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok")))
    sweep_id = new_sweep(factory, alice)
    # Saved after Start: this run keeps reading what was frozen, the next one reads the new list.
    set_profile(
        factory,
        alice,
        search(board("fake-ok"), location_exclude={"terms": ["paris"], "advanced_regex": False}),
    )
    row = run(factory, sweep_id, fake_ats)
    assert row.stats["new"] == 2
    later = run(factory, new_sweep(factory, alice), fake_ats)
    assert (later.stats["matched"], later.stats["new"]) == (1, 0)
