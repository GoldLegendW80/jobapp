"""Jobs J21: "Anywhere on the web", the sweep's search of the whole web (fakes only)."""

import asyncio
import json

from app.agents import definitions, runner, sdk_options
from app.agents import tools as app_tools
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


def test_the_web_agent_has_no_cap_and_the_website_agent_twenty() -> None:
    # Owner 2026-11-18, "No cap, like the Mac": no gate cap, Claude Code's default session cap.
    web = definitions.get(finder.WEB_NAME, "test")
    site = definitions.get(finder.INTERACTIVE_NAME, "test")
    assert web.web_searches_max is None
    assert site.web_searches_max == sdk_options.WEB_SEARCHES_MAX == 20
    assert web.tools == site.tools
    assert web.web_search is site.web_search
    assert web.model == site.model  # owner 2026-11-18: "Keep Sonnet"
    assert "at most" not in web.system_prompt.split("Method:")[1].split("2.")[0]
    assert "site:candidat.francetravail.fr" in web.system_prompt
    for dash in ("—", "–", " - "):
        assert dash not in web.system_prompt


def _searches(most: int | None, count: int) -> tuple[list[dict], list[str]]:
    async def denied(name: str) -> None:
        raise AssertionError(name)

    asked: list[str] = []
    gate = runner.make_gate(
        frozenset(), denied, None, lambda found: asked.append(found["query"]), most
    )

    async def search(n: int) -> dict:
        call = {"tool_name": "WebSearch", "tool_input": {"query": f"q{n}"}}
        return await gate(call, f"t{n}", None)

    return [asyncio.run(search(n)) for n in range(count)], asked


def test_the_gate_has_no_cap_for_the_web_agent() -> None:
    answers, asked = _searches(None, 60)
    assert all(answer == {} for answer in answers)
    assert len(asked) == 60


def test_the_gate_still_refuses_the_website_agents_twenty_first_search() -> None:
    answers, asked = _searches(sdk_options.WEB_SEARCHES_MAX, 21)
    assert all(answer == {} for answer in answers[:20])
    refusal = answers[20]["hookSpecificOutput"]["permissionDecisionReason"]
    assert refusal == runner.WEB_SEARCHES_SPENT
    assert len(asked) == 20


def test_the_web_agent_leaves_claude_codes_session_cap_unset(tmp_path) -> None:
    tools = app_tools.build(None, finder.uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]

    async def gate(*_: object) -> dict:
        return {}

    env = {"ANTHROPIC_API_KEY": "sk-test"}
    web = runner.build_options(finder.WEB_FINDER, tools, env, tmp_path, gate, web_search=True)
    assert web.tools == ["WebSearch"]
    assert sdk_options.WEB_SEARCHES_ENV not in web.env
    site = runner.build_options(
        finder.INTERACTIVE_FINDER, tools, env, tmp_path, gate, web_search=True
    )
    assert site.env[sdk_options.WEB_SEARCHES_ENV] == str(sdk_options.WEB_SEARCHES_MAX)


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
