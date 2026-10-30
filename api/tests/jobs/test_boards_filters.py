"""Company boards: the saved criteria applied after reading (app/jobs/boards/filters.py)."""

from datetime import date
from typing import Any

import pytest

from app.jobs.boards.ats import BoardPosting
from app.jobs.boards.filters import BoardFilter
from app.jobs.models import ContractType
from app.profile.schemas import SearchSection
from tests.jobs.sweep_helpers import search

TODAY = date(2026, 10, 8)


def wanted(**overrides: Any) -> BoardFilter:
    return BoardFilter.of(SearchSection.model_validate(search(**overrides)), TODAY)


def posting(
    title: str = "AI Engineer",
    location: str = "Paris, France",
    published: str = "2026-10-01",
    contract: ContractType | None = None,
    url: str = "https://jobs.ashbyhq.com/example/1",
) -> BoardPosting:
    return BoardPosting(title, location, url, published, "text", contract)


@pytest.mark.parametrize(
    ("one", "reason"),
    [
        (posting(), None),
        (posting(title="AI Engineers"), None),  # optional plural
        (posting(title="Data Engineer"), "title"),
        (posting(title="AI Engineer, Sales"), "excluded"),
        (posting(location="Tokyo"), "location"),
        (posting(location="Remote"), None),
        (posting(published="2026-09-07"), "too_old"),
        (posting(published="2026-09-08"), None),  # 30 days back, included
        (posting(published=""), None),  # an unknown date is kept
    ],
)
def test_saved_criteria(one: BoardPosting, reason: str | None) -> None:
    assert wanted().reason(one) == reason


@pytest.mark.parametrize("place", ["everywhere", "Worldwide", "partout"])
def test_a_place_without_limit_reads_every_location(place: str) -> None:
    keep = wanted(location={"terms": ["paris", place], "advanced_regex": False})
    assert keep.reason(posting(location="Tokyo")) is None
    assert keep.reason(posting(location="")) is None


def test_no_place_saved_reads_every_location() -> None:
    assert wanted(location={"terms": [], "advanced_regex": False}).reason(
        posting(location="Tokyo")
    ) is None  # fmt: skip


def test_unwanted_places() -> None:
    keep = wanted(location_exclude={"terms": ["london"], "advanced_regex": False})
    assert keep.reason(posting(location="London, UK")) == "location_excluded"
    assert keep.reason(posting(location="Paris")) is None


def test_contract_stated_by_the_ats_else_read_from_the_title() -> None:
    cdi = wanted(contract_types=["CDI"])
    assert cdi.reason(posting(contract=ContractType.Stage)) == "contract"
    assert cdi.reason(posting(contract=ContractType.CDI)) is None
    assert cdi.reason(posting()) is None  # nothing stated, nothing in the title: CDI
    assert cdi.reason(posting(title="AI Engineer Intern")) == "contract"
    assert wanted(contract_types=[]).reason(posting(contract=ContractType.Stage)) is None


def test_regular_expressions_saved_before_keep_filtering_as_such() -> None:
    keep = wanted(title_include={"terms": ["forward.deployed"], "advanced_regex": True})
    assert keep.reason(posting(title="Forward-Deployed Engineer")) is None
    assert keep.reason(posting(title="AI Engineer")) == "title"
