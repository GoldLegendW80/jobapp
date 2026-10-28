"""search.sweep_schedule: when the search runs automatically (jobs J5 schedules it and owns its
editor). Off by default, backward compatible (a section saved without it reads as off), validated
strictly, kept by every save of the Search tab and by export then import."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.schemas import WEEKDAYS, SearchSection, SweepSchedule, validate_section
from tests.identity.api_helpers import Api
from tests.profile.conftest import full
from tests.profile.test_api import read, save_ok
from tests.profile.test_export_import import document, export, import_ok

ON = {
    "enabled": True,
    "days": ["monday", "wednesday", "friday"],
    "times": ["06:17", "18:30"],
    "timezone": "America/New_York",
}


def test_defaults_off_every_day_at_0617_paris() -> None:
    assert SweepSchedule().model_dump() == {
        "enabled": False,
        "days": list(WEEKDAYS),
        "times": ["06:17"],
        "timezone": "Europe/Paris",
    }


def test_a_section_saved_before_the_field_reads_as_off() -> None:
    legacy = full("search")
    legacy.pop("sweep_schedule", None)
    section = SearchSection.model_validate(legacy)
    assert section.sweep_schedule.enabled is False
    assert section.model_dump(mode="json")["sweep_schedule"] == SweepSchedule().model_dump()


def test_partial_schedule_takes_defaults_for_the_rest() -> None:
    schedule = SweepSchedule.model_validate({"enabled": True})
    assert schedule.days == list(WEEKDAYS)
    assert schedule.times == ["06:17"]


def test_a_full_schedule_is_kept_as_written() -> None:
    assert SweepSchedule.model_validate(ON).model_dump() == ON


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"enabled": "yes"}, "valid boolean"),
        ({"days": []}, "at least 1 item"),
        ({"days": ["Monday"]}, "'monday'"),
        ({"days": ["mon"]}, "'monday'"),
        ({"days": [*WEEKDAYS, "monday"]}, "at most 7 items"),
        ({"times": []}, "at least 1 item"),
        ({"times": ["6:17"]}, "is not a time as HH:MM"),
        ({"times": ["24:00"]}, "is not a time as HH:MM"),
        ({"times": ["06:60"]}, "is not a time as HH:MM"),
        ({"times": ["06:17:00"]}, "is not a time as HH:MM"),
        ({"times": ["01:00", "02:00", "03:00", "04:00", "05:00"]}, "at most 4 items"),
        ({"times": [617]}, "valid string"),
        ({"timezone": "Paris"}, "IANA time zone"),
        ({"timezone": "Europe/Atlantis"}, "IANA time zone"),
        ({"timezone": ""}, "at least 1 character"),
        ({"timezone": "../../etc/passwd"}, "IANA time zone"),
        ({"timezone": "GMT"}, "IANA time zone"),
        ({"timezone": "EST5EDT"}, "IANA time zone"),
        ({"unknown": 1}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_schedules_are_refused(change: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        SweepSchedule.model_validate({**ON, **change})


def test_duplicates_are_dropped_and_the_order_normalised() -> None:
    schedule = SweepSchedule.model_validate(
        {**ON, "days": ["friday", "monday", "friday"], "times": ["18:30", "06:17", "18:30"]}
    )
    assert schedule.days == ["monday", "friday"]
    assert schedule.times == ["06:17", "18:30"]
    assert SweepSchedule.model_validate({**ON, "timezone": "UTC"}).timezone == "UTC"


def test_the_draft_sweep_nightly_key_is_dropped() -> None:
    section = SearchSection.model_validate({**full("search"), "sweep_nightly": True})
    assert section.sweep_schedule.enabled is False


def test_the_search_section_reports_where() -> None:
    data = {"sweep_schedule": {**ON, "times": ["25:00"]}}
    with pytest.raises(ValidationError) as caught:
        validate_section("search", data)
    assert caught.value.errors()[0]["loc"] == ("sweep_schedule", "times")


def test_a_save_stores_and_returns_the_schedule(api: Api, alice: str) -> None:
    search = {**full("search"), "sweep_schedule": ON}
    saved = save_ok(api, alice, "search", search, 0)
    assert saved["sections"]["search"]["sweep_schedule"] == ON
    # Saving the tab again with the value it read keeps it unchanged.
    again = save_ok(api, alice, "search", saved["sections"]["search"], saved["revision"])
    assert again["sections"]["search"]["sweep_schedule"] == ON
    assert read(api, alice)["sections"]["search"]["sweep_schedule"] == ON


def test_export_then_import_round_trips_the_schedule(api: Api, alice: str, bob: str) -> None:
    sections = {name: full(name) for name in ("identity", "experience", "facts", "skills")}
    sections["search"] = {**full("search"), "sweep_schedule": ON}
    sections["answers"] = full("answers")
    import_ok(api, alice, document(sections))
    file = export(api, alice)
    assert file["sections"]["search"]["sweep_schedule"] == ON
    imported = import_ok(api, bob, copy.deepcopy(file))
    assert imported["sections"]["search"]["sweep_schedule"] == ON
    assert export(api, bob)["sections"] == file["sections"]


def test_an_old_export_without_the_schedule_imports_as_off(api: Api, alice: str) -> None:
    sections = {name: full(name) for name in ("identity", "experience", "facts", "skills")}
    search = full("search")
    search.pop("sweep_schedule", None)
    sections["search"] = search
    sections["answers"] = full("answers")
    imported = import_ok(api, alice, document(sections))
    assert imported["sections"]["search"]["sweep_schedule"]["enabled"] is False
