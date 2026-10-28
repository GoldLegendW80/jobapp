"""What the deletion of 2026-10-28 must not drop (test_deleted_sources.py checks what it drops): a
profile an older release stored, read and then saved by an older tab sending the deleted keys and
leaving out the website fields, keeps its job websites (custom ones on ATS and company hosts
included), their instructions, the LinkedIn consent row, the platform consents, the plain and
regular expression lists and the never apply rules. Invented data only."""

import copy
from typing import Any

from tests.profile.conftest import full
from tests.profile.test_api import read, save_ok
from tests.profile.test_deleted_sources import (  # noqa: F401
    OLD_BOARD,
    OLD_FEED,
    OLD_READERS,
    owner,
    store,
    stored,
)

SITES = [
    {"platform": "custom", "url": url, "enabled": enabled, "mode": "llm_browser"}
    for url, enabled in [
        ("https://boards.greenhouse.io/example-robotics", True),
        ("https://jobs.lever.co/example-robotics", True),
        ("https://jobs.ashbyhq.com/example-robotics", False),
        ("https://careers.example-robotics.example/jobs", True),
    ]
]
LINKEDIN = {
    "kind": "linkedin",
    "query": "robot",
    "location": "Paris",
    "enabled": True,
    "interactive_only": True,
    "accepted_at": "2026-10-20T10:00:00+02:00",
    "text_version": 1,
}
CONSENTS = {
    "indeed": {"enabled": True, "accepted_at": "2026-10-21T09:00:00+00:00", "text_version": 1}
}
INSTRUCTIONS = "Only robotics roles."
LISTS = ("title_include", "title_exclude", "location", "location_exclude", "never_apply")


def live_search() -> dict[str, Any]:
    data = full("search")
    data["interactive_sites"] = copy.deepcopy(SITES)
    data["interactive_instructions"] = INSTRUCTIONS
    data["browser_sources"] = [*data["browser_sources"], LINKEDIN]
    data["platform_consents"] = CONSENTS
    return data


def old_stored() -> dict[str, Any]:
    data = live_search()
    data["boards"] = [OLD_BOARD]
    data["feeds"] = [OLD_FEED]
    data["browser_sources"] = [*OLD_READERS, *data["browser_sources"]]
    return data


def test_an_old_profile_and_an_older_tab_keep_every_live_field(api, factory, owner) -> None:  # noqa: F811
    token, user_id = owner
    store(factory, user_id, search=old_stored())
    shown = read(api, token)["sections"]["search"]
    assert shown["interactive_sites"] == SITES
    assert shown["platform_consents"] == CONSENTS
    assert LINKEDIN in shown["browser_sources"]
    older = old_stored()
    del older["interactive_sites"], older["interactive_instructions"], older["platform_consents"]
    older["min_fit"] = 42
    save_ok(api, token, "search", older, 1)
    kept = stored(factory, user_id)["search"]
    assert kept["min_fit"] == 42
    assert kept["interactive_sites"] == SITES
    assert kept["interactive_instructions"] == INSTRUCTIONS
    assert kept["platform_consents"] == CONSENTS
    assert [row["kind"] for row in kept["browser_sources"]] == ["freework", "linkedin"]
    assert next(row for row in kept["browser_sources"] if row["kind"] == "linkedin") == LINKEDIN
    for name in LISTS:
        assert kept[name] == live_search()[name], name
    assert "boards" not in kept
    assert "feeds" not in kept
