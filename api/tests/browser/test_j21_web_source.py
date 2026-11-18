"""Jobs J21: "Anywhere on the web", the sweep's search of the whole web (fakes only)."""

import asyncio
import json

from app.agents import definitions, runner, sdk_options
from app.browser import finder
from app.jobs import sweep
from app.profile.schemas import SearchSection

# Claude Code's WebSearch answer as CLI 2.1.286 and 2.1.325 write it (invented results).
ANSWER = (
    'Web search results for query: "data scientist CDI La Reunion"\n\n'
    'Links: [{"title":"Data scientist (CDI)","url":"https://jobs.example-ai-board.test/p/1"},'
    '{"title":"Offre 123ABC","url":"https://candidat.francetravail.fr/offres/recherche/detail/123ABC"}]\n\n'
    "A summary the model wrote.\n"
)


def test_results_are_counted_from_the_links_lines() -> None:
    assert runner.web_search_results(ANSWER) == 2
    assert (
        runner.web_search_results(ANSWER + 'Links: [{"title":"x","url":"https://a.test/"}]\n') == 3
    )
    assert runner.web_search_results("No links found.\n") == 0
    assert runner.web_search_results("Links: [not json]\n") == 0


def test_the_web_agent_has_thirty_searches_and_the_website_agent_twenty() -> None:
    web = definitions.get(finder.WEB_NAME, "test")
    site = definitions.get(finder.INTERACTIVE_NAME, "test")
    assert web.web_searches_max == sdk_options.WEB_SEARCHES_WEB_MAX == 30
    assert site.web_searches_max == sdk_options.WEB_SEARCHES_MAX == 20
    assert web.tools == site.tools
    assert web.web_search is site.web_search
    assert "30 searches" in web.system_prompt
    assert "site:candidat.francetravail.fr" in web.system_prompt
    for dash in ("—", "–", " - "):
        assert dash not in web.system_prompt


def test_the_gate_refuses_the_thirty_first_search() -> None:
    async def denied(name: str) -> None:
        raise AssertionError(name)

    asked: list[str] = []
    gate = runner.make_gate(
        frozenset(), denied, None, lambda found: asked.append(found["query"]), 30
    )

    async def search(n: int) -> dict:
        call = {"tool_name": "WebSearch", "tool_input": {"query": f"q{n}"}}
        return await gate(call, f"t{n}", None)

    answers = [asyncio.run(search(n)) for n in range(31)]
    assert all(answer == {} for answer in answers[:30])
    refusal = answers[30]["hookSpecificOutput"]["permissionDecisionReason"]
    assert "at most 30" in refusal
    assert len(asked) == 30


def test_a_search_is_ticked_by_default_and_frozen_as_a_source() -> None:
    search = SearchSection.model_validate({})
    assert search.web_search is True
    assert sweep.has_web(search)
    assert sweep.has_sources(search)
    off = SearchSection.model_validate({"web_search": False})
    assert not sweep.has_web(off)
    entry = sweep.web_entry()
    assert entry["mode"] == "web"
    assert entry["source_id"] == sweep._source_id("web", "anywhere")


def test_the_web_scope_reads_no_website() -> None:
    scope = finder.InteractiveScope(
        start=finder.WEB_START, titles=("Data scientist",), places=("La Reunion",), web=True
    )
    assert scope.tool_scope() == {"start": finder.WEB_START}
    assert finder.canonical(finder.WEB_START) == finder.WEB_START
    assert json.dumps(scope.tool_scope())
