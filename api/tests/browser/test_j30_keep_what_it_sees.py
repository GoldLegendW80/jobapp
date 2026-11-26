"""Jobs J30 (#829) in the search agents: they report a posting in a wanted place (or remote) that
fits the profile whatever its contract, its work mode and its age, the tools say what the app
verifies (B27), the reports carry the assistant's own count of the results it read, and every
figure of what happened to what a source reported is served (`SourceOut`). Invented hosts only."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.browser import finder, finder_tools, web_plan
from app.jobs import sweep_router
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.browser.test_b20_agent import POSTING, Site, run, selected, tool


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("prompt", [finder.INTERACTIVE_PROMPT, finder.WEB_PROMPT])
def test_both_prompts_report_whatever_the_contract_the_work_mode_and_the_age(prompt: str) -> None:
    words = flat(prompt)
    for said in (
        "every open posting in a wanted place, or remote, whose role matches a saved title",
        "Report it whatever its contract, its work mode and its age: a freelance mission, an "
        "internship, a remote role or a posting older than the",
        "the app marks for the person what is outside the criteria",
        "Leave a posting only when it is in another place, closed or expired, of a title or a "
        "place the person does not want, or no job posting at all",
        "keeps as skipped the ones the person's own never apply rules exclude",
        "results_read",
    ):
        assert said in words, said
    for gone in (
        "clearly older than",
        "of a title or a contract the person does not want",
        "excluded by the person's own never apply rules, or no job posting at all",
        "nor one of another contract",
        "Leave only a posting whose date is clearly older",
        "(or remote, when the person wants remote)",
    ):
        assert gone not in words, gone
    assert not any(dash in prompt for dash in ("–", "—", " - "))


def test_the_web_prompt_and_its_plan_never_leave_a_posting_for_its_date_or_contract() -> None:
    web = flat(finder.WEB_PROMPT)
    assert "7. Dates: never leave a posting for its date." in web
    assert "report every other one that fits, whatever its contract, work mode, age" in web
    report = web_plan.shown(())["report"]
    assert "its contract, its work mode or its age" in report
    assert "marks what is outside the criteria" in report


def test_the_report_tools_say_what_the_app_verifies_and_take_the_count() -> None:
    postings = finder_tools.DESCRIPTIONS["report_postings"]
    assert "whose own page you opened" not in postings
    assert "opened or saw on a page, in a snapshot or in a script result" in postings
    assert "The app opens an address you saw and did not open" in postings
    assert "whatever their contract, work mode or age" in postings
    for name in ("report_postings", "report_no_postings"):
        assert "results_read" in finder_tools.DESCRIPTIONS[name]
        schema = finder_tools.SCHEMAS[name]
        assert schema["properties"]["results_read"] == {
            "type": "integer",
            "minimum": 0,
            "maximum": finder.RESULTS_READ_MAX,
        }
        assert "results_read" not in schema["required"]  # optional


async def reports(session: finder.FinderSession, counts: list[Any]) -> finder.Ran:
    assert not (await session.navigate(POSTING))[1]
    for count in counts:
        posting = {"title": "Robot Engineer", "url": POSTING, "text": "Build robots."}
        await tool(session, "report_postings", postings=[posting], results_read=count)
    await tool(session, "report_no_postings", reason="no_match", results_read=counts[-1])
    return finder.Ran()


@pytest.mark.parametrize(
    ("counts", "kept"),
    [
        ([40, 120, 75], 120),  # the largest it gave
        ([0], 0),
        ([finder.RESULTS_READ_MAX], finder.RESULTS_READ_MAX),
        ([finder.RESULTS_READ_MAX + 1], None),
        ([-1], None),
        (["40"], None),
        ([True], None),
        ([40.0], None),
        ([None], None),
        ([12, "1e9", -5], 12),
    ],
)
def test_the_assistants_own_count_is_bounded_and_stored_as_it_said(
    counts: list[Any], kept: int | None
) -> None:
    entry, _, _ = run(selected(), Site(), lambda session: reports(session, counts))
    assert entry.get("results_read") == kept
    assert entry["found"] == 1  # never read by code to decide


def test_a_run_whose_agent_gives_no_count_serves_none() -> None:
    entry, _, _ = run(selected(), Site(), lambda session: reports(session, [None]))
    assert "results_read" not in entry
    [source] = sweep_router.sweep_out(row([entry])).sources
    assert source.results_read is None


def row(sources: list[Any]) -> Sweep:
    return Sweep(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        trigger=SweepTrigger.manual,
        status=SweepStatus.done,
        started_at=datetime(2026, 11, 26, 8, 30, tzinfo=UTC),
        finished_at=datetime(2026, 11, 26, 8, 40, tzinfo=UTC),
        error=None,
        stats={"sources": sources, "outside": 3},
    )


def website(**fields: Any) -> dict[str, Any]:
    return {"kind": "custom", "query": "", "mode": "llm_browser", "found": 9, "postings": 4,
            **fields}  # fmt: skip


def test_source_out_serves_what_happened_to_what_a_source_reported() -> None:
    out = sweep_router.sweep_out(
        row(
            [
                website(
                    duplicates=2,
                    outside=3,
                    unwanted=1,
                    skipped_by_rule=1,
                    refused={"not_seen": 2, "detail_not_opened": 1, "Bad Key": 4, "x": True},
                    opened_by_app=5,
                    results_read=1490,
                    other_contract=6,
                )
            ]
        )
    )
    [source] = out.sources
    assert (source.new, source.duplicates, source.outside, source.unwanted) == (4, 2, 3, 1)
    assert source.refused == {"not_seen": 2, "detail_not_opened": 1}
    assert (source.opened_by_app, source.results_read, source.other_contract) == (5, 1490, 6)
    assert source.skipped_by_rule == 1
    assert out.outside == 3


@pytest.mark.parametrize(
    "fields",
    [
        {},  # a sweep from before J30
        {
            "duplicates": "2",
            "outside": -3,
            "unwanted": None,
            "refused": ["not_seen"],
            "opened_by_app": True,
            "results_read": -1,
            "other_contract": 1.5,
        },
        {"results_read": "1490"},
        {"results_read": False},
    ],
)
def test_an_older_or_malformed_entry_reads_as_nothing_happened(fields: dict[str, Any]) -> None:
    [source] = sweep_router.sweep_out(row([website(**fields)])).sources
    assert (source.duplicates, source.outside, source.unwanted, source.refused) == (0, 0, 0, {})
    assert (source.opened_by_app, source.results_read, source.other_contract) == (0, None, 0)
