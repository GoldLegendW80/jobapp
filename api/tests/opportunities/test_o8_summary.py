"""Opportunities O8: what the web reads of a sweep's freelance and project feeds, and the words of
the catalogue it shows. The summary of a sweep carries, per feed, the postings the never apply
rules set aside (`skipped_by_rule`), so the web can say so in plain words; a browser source and an
older stats entry without the counter read 0. No text of the catalogue uses a dash as a
separator, since the Search tab shows every one. No database: `sweep_out` reads a row's stats."""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.jobs import sweep_router
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.opportunities.sources import ERRORS, catalogue

DASHES = re.compile(r"[‒–—―]|\s-\s")


def row(sources: list[Any]) -> Sweep:
    return Sweep(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        trigger=SweepTrigger.manual,
        status=SweepStatus.done,
        started_at=datetime(2026, 10, 21, 8, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 21, 8, 1, tzinfo=UTC),
        error=None,
        stats={"sources": sources, "skipped_by_rule": 3},
    )


def feed_entry(kind: str, **counts: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "query": "",
        "location": None,
        "result_pages": 0,
        "found": 4,
        "postings": 2,
        "skipped_by_rule": 0,
        "error": None,
        **counts,
    }


def test_each_feed_reports_what_the_rules_set_aside() -> None:
    out = sweep_router.sweep_out(
        row(
            [
                feed_entry("remoteok", skipped_by_rule=1),
                feed_entry("hackernews_freelance", skipped_by_rule=2, error="time_budget"),
                # A browser source (B8) has no such counter: it reads 0.
                {"kind": "google_careers", "query": "ai", "found": 1, "postings": 1},
            ]
        )
    )
    assert [(s.kind, s.found, s.new, s.skipped_by_rule, s.error) for s in out.sources] == [
        ("remoteok", 4, 2, 1, None),
        ("hackernews_freelance", 4, 2, 2, "time_budget"),
        ("google_careers", 1, 1, 0, None),
    ]
    assert out.skipped_by_rule == 3


@pytest.mark.parametrize("value", [None, "2", True, -1.5, [1]])
def test_a_counter_of_the_wrong_type_reads_zero(value: Any) -> None:
    out = sweep_router.sweep_out(row([feed_entry("remoteok", skipped_by_rule=value)]))
    assert out.sources[0].skipped_by_rule == 0


def test_the_summary_answer_names_the_counter() -> None:
    body = sweep_router.sweep_out(row([feed_entry("weworkremotely")])).model_dump(mode="json")
    assert body["sources"][0]["skipped_by_rule"] == 0
    assert set(body["sources"][0]) == {
        "kind",
        "query",
        "location",
        "result_pages",
        "found",
        "new",
        "skipped_by_rule",
        "items_skipped",
        # A website searched in the browser (jobs J18): the searches its agent ran.
        "searches",
        "searched",
        # And the saved titles and places its search could not carry (jobs J18).
        "titles_left_out",
        "places_left_out",
        "error",
        # Jobs J9: the frozen source identity and progress.
        "source_id",
        "mode",
        "host",
        "phase",
        "action",
        "outcome",
        "reason",
    }
    metadata = ("source_id", "mode", "host", "phase", "action", "outcome", "reason")
    assert {key: body["sources"][0][key] for key in metadata} == dict.fromkeys(metadata)


def test_the_web_has_words_for_every_feed_code() -> None:
    # web/src/features/opportunities/sources/source-words.ts FEED_PROBLEMS pins the same list.
    assert set(ERRORS) == {
        "not_found",
        "blocked",
        "rate_limited",
        "upstream_error",
        "timeout",
        "network",
        "too_large",
        "invalid_response",
        "too_many",
        "store_failed",
        "time_budget",
        "stub_missing",
    }


def test_no_catalogue_text_uses_a_dash_as_a_separator() -> None:
    texts = [
        value
        for group in catalogue.as_json().values()
        for entry in group
        for key, value in entry.items()
        if key not in ("terms_url", "kind", "checked_on")
    ]
    assert texts
    for text in texts:
        assert not DASHES.search(text), text


def test_every_platform_has_a_one_line_reason_and_a_date() -> None:
    for platform in catalogue.as_json()["not_supported"]:
        assert platform["reason"].strip()
        assert "\n" not in platform["reason"]
        datetime.strptime(platform["checked_on"], "%Y-%m-%d")


@pytest.mark.parametrize("value", [None, "2", True, False, -1.5, [1], -1])
def test_a_bad_parser_skip_count_reads_zero_without_changing_old_counters(value: Any) -> None:
    out = sweep_router.sweep_out(
        row([feed_entry("remoteok", items_skipped=value, found=-2, skipped_by_rule=-3)])
    )
    source = out.sources[0]
    assert source.items_skipped == 0
    assert (source.found, source.new, source.skipped_by_rule) == (-2, 2, -3)


def test_parser_skips_survive_serialization_alongside_rules_and_errors() -> None:
    body = sweep_router.sweep_out(
        row([feed_entry("remoteok", items_skipped=2, skipped_by_rule=1, error="store_failed")])
    ).model_dump(mode="json")
    assert body["sources"][0] == {
        "kind": "remoteok",
        "query": "",
        "location": None,
        "result_pages": 0,
        "found": 4,
        "new": 2,
        "skipped_by_rule": 1,
        "items_skipped": 2,
        "searches": 0,
        "searched": [],
        "titles_left_out": 0,
        "places_left_out": 0,
        "error": "store_failed",
        # Jobs J9: the frozen source identity and progress, absent from an older stats row.
        "source_id": None,
        "mode": None,
        "host": None,
        "phase": None,
        "action": None,
        "outcome": None,
        "reason": None,
    }


def test_old_and_browser_sources_default_to_no_parser_skips() -> None:
    out = sweep_router.sweep_out(row([feed_entry("remoteok"), {"kind": "google_careers"}]))
    assert [source.items_skipped for source in out.sources] == [0, 0]
    assert sweep_router.sweep_out(row([])).sources == []
