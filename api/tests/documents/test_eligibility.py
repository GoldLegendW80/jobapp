"""D10: the eligible posting as a table (SPEC-documents.md, "Eligible posting"), pure: the
threshold at, under and above, no fit, every status, the window at its edges, the legacy files,
the never apply rules (the sweep's own parser, with near misses), `auto` off; the order; the
retry once (after the wait, at once with a raised ceiling after a stop at the ceiling, never
after the person stopped it); and the day of the ceiling in the person's time zone."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.documents import eligibility as e
from app.jobs.models import JobStatus
from app.jobs.sweep import NeverApplyRule

TODAY = date(2026, 10, 14)
ON = e.Setting(auto=True, min_fit=70, daily_ceiling=100)
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def posting(**changes: object) -> e.Posting:
    values: dict[str, object] = {
        "status": JobStatus.found,
        "fit": 80,
        "found_on": TODAY,
        "company": "Example Corp",
        "title": "Platform engineer",
    }
    values.update(changes)
    return e.Posting(**values)  # type: ignore[arg-type]


def refused(found: e.Posting, setting: e.Setting = ON, rules: tuple[str, ...] = ()) -> object:
    parsed = [NeverApplyRule.parse(text) for text in rules]
    return e.refusal(found, setting, parsed, TODAY, 30)


@pytest.mark.parametrize(
    ("fit", "threshold", "expected"),
    [
        (70, 70, None),
        (71, 70, None),
        (100, 70, None),
        (69, 70, e.Refusal.under_threshold),
        (0, 70, e.Refusal.under_threshold),
        (None, 70, e.Refusal.no_fit),
        (None, 0, e.Refusal.no_fit),
        (0, 0, None),
        (50, 50, None),
        (49, 50, e.Refusal.under_threshold),
        (100, 100, None),
        (99, 100, e.Refusal.under_threshold),
    ],
)
def test_the_threshold_is_inclusive_and_a_missing_fit_is_never_written(
    fit: int | None, threshold: int, expected: object
) -> None:
    assert refused(posting(fit=fit), e.Setting(True, threshold, 100)) == expected


@pytest.mark.parametrize("status", list(JobStatus))
def test_only_found_and_shortlisted_postings_qualify(status: JobStatus) -> None:
    expected = None if status in (JobStatus.found, JobStatus.shortlisted) else e.Refusal.status
    assert refused(posting(status=status)) == expected


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [
        (0, None),
        (1, None),
        (28, None),
        (29, None),  # the 30th day, today included
        (30, e.Refusal.too_old),
        (31, e.Refusal.too_old),
        (365, e.Refusal.too_old),
        (-1, None),  # found "tomorrow" in another time zone than the person's
    ],
)
def test_the_window_is_the_last_30_days_today_included(days_ago: int, expected: object) -> None:
    assert refused(posting(found_on=TODAY - timedelta(days=days_ago))) == expected


def test_auto_off_writes_nothing_whatever_the_fit() -> None:
    assert refused(posting(fit=100), e.Setting(False, 0, 100)) == e.Refusal.auto_off


def test_a_posting_with_files_of_the_command_line_is_never_written() -> None:
    assert refused(posting(legacy=True)) == e.Refusal.legacy


RULE = "No security roles: security, pentest, threat (any company)"


@pytest.mark.parametrize(
    ("company", "title", "expected"),
    [
        ("Example Corp", "Security engineer", e.Refusal.never_apply),
        ("Example Corp", "Senior Pentest lead", e.Refusal.never_apply),
        ("Example Corp", "THREAT analyst", e.Refusal.never_apply),
        ("Security Labs", "Platform engineer", e.Refusal.never_apply),
        ("Example Corp", "Platform engineer", None),
        ("Example Corp", "Secure payments engineer", None),  # a near miss: not the term
        ("Example Corp", "Threats", e.Refusal.never_apply),  # the plural, as the sweep reads it
        ("Example Corp", "", None),
        ("", "", None),
    ],
)
def test_a_never_apply_rule_is_matched_as_the_sweep_matches_it(
    company: str, title: str, expected: object
) -> None:
    assert refused(posting(company=company, title=title), rules=(RULE,)) == expected


def test_a_rule_without_terms_never_excludes() -> None:
    assert refused(posting(title="Security engineer"), rules=("No security roles",)) is None


def test_the_order_is_highest_fit_then_newest_then_id() -> None:
    rows = [
        (80, TODAY - timedelta(days=2), "b"),
        (95, TODAY - timedelta(days=9), "c"),
        (80, TODAY, "d"),
        (80, TODAY, "a"),
        (None, TODAY, "e"),
    ]
    ordered = sorted(rows, key=lambda row: e.order_key(*row))
    assert [row[2] for row in ordered] == ["c", "a", "d", "b", "e"]


def failure(error: str | None, seconds_ago: int, ceiling: int = 100) -> e.Failure:
    return e.Failure(error, NOW - timedelta(seconds=seconds_ago), ceiling)


@pytest.mark.parametrize(
    ("failures", "stopped", "expected"),
    [
        ([], False, 50),  # a first try: the fresh ceiling
        ([], True, None),  # the person stopped it: never again by the tick
        ([failure("run_failed", 599)], False, None),  # before the 10 minutes
        ([failure("run_failed", 600)], False, 50),  # at the 10 minutes
        ([failure("run_failed", 601)], False, 50),
        ([failure(None, 3600)], False, 50),
        ([failure("token_limit", 0, ceiling=100)], False, 120),  # at once, one more high estimate
        ([failure("token_limit", 0, ceiling=10)], False, 50),  # never under the fresh ceiling
        ([failure("run_failed", 9000), failure("run_failed", 9000)], False, None),  # once only
        ([failure("token_limit", 0), failure("token_limit", 0)], False, None),
        ([failure("run_failed", 9000)], True, None),
    ],
)
def test_a_failed_generation_is_tried_once_more(
    failures: list[e.Failure], stopped: bool, expected: int | None
) -> None:
    found = e.next_try(failures, stopped=stopped, now=NOW, retry_after_s=600, ceiling=50, high=20)
    assert (found.token_ceiling if found is not None else None) == expected


@pytest.mark.parametrize(
    ("now", "timezone", "day"),
    [
        (datetime(2026, 10, 15, 3, 59, tzinfo=UTC), "America/New_York", date(2026, 10, 14)),
        (datetime(2026, 10, 15, 4, 0, tzinfo=UTC), "America/New_York", date(2026, 10, 15)),
        (datetime(2026, 10, 14, 21, 59, tzinfo=UTC), "Europe/Paris", date(2026, 10, 14)),
        (datetime(2026, 10, 14, 22, 0, tzinfo=UTC), "Europe/Paris", date(2026, 10, 15)),
        (datetime(2026, 10, 14, 23, 59, tzinfo=UTC), "UTC", date(2026, 10, 14)),
        (datetime(2026, 10, 14, 22, 0, tzinfo=UTC), None, date(2026, 10, 15)),  # the default
        (datetime(2026, 10, 14, 22, 0, tzinfo=UTC), "Not/AZone", date(2026, 10, 15)),
    ],
)
def test_the_day_of_the_ceiling_is_the_persons_day(
    now: datetime, timezone: str | None, day: date
) -> None:
    assert e.ceiling_day(now, timezone) == day


@pytest.mark.parametrize(
    ("now", "day"),
    [
        (datetime(2026, 10, 14, 21, 59, 59, tzinfo=UTC), date(2026, 10, 14)),
        (datetime(2026, 10, 14, 22, 0, tzinfo=UTC), date(2026, 10, 14)),  # midnight in Paris
        (datetime(2026, 10, 14, 23, 59, 59, tzinfo=UTC), date(2026, 10, 14)),
        (datetime(2026, 10, 15, 0, 0, tzinfo=UTC), date(2026, 10, 15)),
        (datetime(2026, 10, 15, 0, 30, tzinfo=ZoneInfo("Europe/Paris")), date(2026, 10, 14)),
        (datetime(2026, 10, 14, 20, 0, tzinfo=ZoneInfo("America/New_York")), date(2026, 10, 15)),
    ],
)
def test_the_window_ends_on_the_utc_date_that_found_on_is_stamped_in(
    now: datetime, day: date
) -> None:
    """`found_on` is the UTC date of the clock, so the window never follows the local day."""
    assert e.found_day(now) == day


@pytest.mark.parametrize(("ceiling", "started", "left"), [(100, 0, 100), (1, 1, 0), (1, 5, 0)])
def test_the_room_of_the_day_is_never_negative(ceiling: int, started: int, left: int) -> None:
    assert e.room(ceiling, started) == left
