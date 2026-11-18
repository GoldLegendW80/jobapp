"""Jobs J21: "Anywhere on the web", the sweep's search of the whole web (fakes only)."""

import asyncio
import json
import uuid
from collections import Counter
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ToolResultBlock, ToolUseBlock

from app.agents import definitions, runner, sdk_options
from app.agents import tools as app_tools
from app.browser import finder
from app.jobs import sweep
from app.profile.schemas import SearchSection

# Claude Code's WebSearch answer as CLI 2.1.286 and 2.1.325 write it (invented results).
ANSWER = (
    'Web search results for query: "data scientist CDI La Reunion"\n\n'
    'Links: [{"title":"Data scientist (CDI)","url":"https://jobs.example-ai-board.test/p/1"},'
    '{"title":"Offre 123ABC","url":"https://candidat.francetravail.test/offres/recherche/detail/123ABC"}]\n\n'
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


def recorder(*, detail: bool = True, private: bool = True) -> runner.Recorder:
    made = runner.Recorder(
        None, uuid.uuid4(), 1, private=private, detail=detail, secret="TOKEN9f3a"
    )  # type: ignore[arg-type]
    made.known_tools = frozenset(runner.FORBIDDEN_TOOLS) | {sdk_options.WEB_SEARCH}
    return made


def search_call(query: object) -> ToolUseBlock:
    return ToolUseBlock(id="s1", name=sdk_options.WEB_SEARCH, input={"query": query})


def test_a_private_detailed_run_keeps_the_query_bounded_and_scrubbed_and_the_result_count() -> None:
    record = recorder()
    query = "  TOKEN9f3a data scientist CDI La Reunion " + "x" * 300
    call = record.tool_call(search_call(query))
    kept = call["preview"]["query"]
    assert call["name"] == sdk_options.WEB_SEARCH
    assert "TOKEN9f3a" not in kept
    assert kept.startswith(runner.REDACTED + " data scientist")
    assert kept.endswith(" more]")
    assert len(kept.split("...")[0]) == runner.WEB_QUERY_MAX
    answer = ToolResultBlock(tool_use_id="s1", content=ANSWER, is_error=None)
    result = record.tool_result(answer)
    assert result["results"] == 2
    # The answer's text is never kept.
    assert "content" not in result
    assert "error" not in result


@pytest.mark.parametrize("query", ["", "   ", None, 42])
def test_a_search_without_a_query_text_keeps_no_preview(query: object) -> None:
    assert "preview" not in recorder().tool_call(search_call(query))


def test_without_step_detail_neither_query_nor_count_is_kept() -> None:
    record = recorder(detail=False)
    assert "preview" not in record.tool_call(search_call("data scientist La Reunion"))
    answer = ToolResultBlock(tool_use_id="s1", content=ANSWER, is_error=None)
    assert "results" not in record.tool_result(answer)


def test_another_tools_result_is_not_counted_as_a_search() -> None:
    record = recorder()
    record.tool_call(ToolUseBlock(id="r1", name="Read", input={"file_path": "/x"}))
    answer = ToolResultBlock(tool_use_id="r1", content=ANSWER, is_error=None)
    assert "results" not in record.tool_result(answer)


def session(*, web: bool, routes: set[str], web_search_on: bool = True, found: int = 0):
    return SimpleNamespace(
        found=[object()] * found,
        verified_count=0,
        asked=0,
        refused=Counter(),
        routes=dict.fromkeys(routes),
        web_search_on=web_search_on,
        interactive=SimpleNamespace(web=web),
    )


@pytest.mark.parametrize("reason", ["no_match", "no_search_control"])
def test_the_web_scope_needs_only_its_search_before_it_reports_nothing(reason: str) -> None:
    left = finder.FinderSession.routes_left
    assert left(session(web=True, routes=set()), reason) == ["web_search"]  # type: ignore[arg-type]
    assert left(session(web=True, routes={"web_search"}), reason) == []  # type: ignore[arg-type]
    # The website routes never count for it, and a website still needs them.
    others = set(finder.ROUTES) - {"web_search"}
    assert left(session(web=True, routes=others), reason) == ["web_search"]  # type: ignore[arg-type]
    assert left(session(web=False, routes={"web_search"}), "no_match") != []  # type: ignore[arg-type]
    assert left(session(web=True, routes=set(), found=1), reason) == []  # type: ignore[arg-type]
