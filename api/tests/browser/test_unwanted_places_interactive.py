"""J16 with PR #715: a real Unwanted places list reaches the website searches."""

from app.browser.sources.interactive import searches_for
from tests.browser.test_b9_site import ScriptedFinder
from tests.browser.test_multi_search import SearchSite, run, searching
from tests.browser.test_selected_interactive import selected


def test_a_real_unwanted_place_list_is_context_and_dropped_after_reading():
    site = SearchSite(results={"robot": ["Robot Engineer", "Robot Sales Lead"]})
    search = selected(location_exclude={"terms": ["Paris"], "advanced_regex": False})
    [scope] = searches_for(search).scopes
    assert '"location_exclude"' in scope.criteria
    entry, _, _ = run(search, site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["unwanted"]) == (0, 2)
    other = selected(location_exclude={"terms": ["Lyon"], "advanced_regex": False})
    entry, _, _ = run(other, site, ScriptedFinder(searching(site)))
    assert entry["found"] == 2
    assert "unwanted" not in entry
