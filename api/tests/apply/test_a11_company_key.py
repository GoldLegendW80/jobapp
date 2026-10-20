"""Apply A11, the pure rules: the company key of a cap, the ATS organisation, the person's day and
the pace of each host. Every company is invented."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.apply import caps

SAME = "examplerobotics"

# One company, under every spelling a posting, a board or the person may use.
ONE_COMPANY = [
    "Example Robotics",
    "example robotics",
    "EXAMPLE ROBOTICS",
    "  Example   Robotics  ",
    "Example Robotics",
    "Example\tRobotics",
    "Exam\u200bple Robotics",
    "Exam\u00adple Robotics",
    "Example-Robotics",
    "Example‐Robotics",
    "example_robotics",
    "example.robotics",
    "examplerobotics",
    "ExampleRobotics",
    "Exämple Robotics",
    "Éxample Röbotics",
    "Exámple Robotics",
    "ＥＸＡＭＰＬＥ Robotics",
    "Example Robotics Inc.",
    "Example Robotics, Inc.",
    "Example Robotics Inc",
    "Example Robotics Incorporated",
    "Example Robotics SAS",
    "Example Robotics S.A.S.",
    "Example Robotics S.A.",
    "Example Robotics SARL",
    "Example Robotics GmbH",
    "Example Robotics GmbH & Co. KG",
    "Example Robotics Ltd",
    "Example Robotics Ltd.",
    "Example Robotics Limited",
    "Example Robotics LLC",
    "Example Robotics B.V.",
    "Example Robotics AB",
    "Example Robotics Oy",
    "Example Robotics Pty Ltd",
    "The Example Robotics Company",
    "Example Robotics Group",
    "Example Robotics Holdings",
    "Example and Robotics",
    "Example & Robotics",
    "Example et Robotics",
    # Letters of other scripts drawn like Latin ones, folded as the never apply rules fold them.
    "\u0415xample Robotics",
    "\u0395xample Robotics",
    "\u1d07xample Robotics",
    "\u0415\u0445\u0430mple R\u043eb\u043et\u0456\u0441s",
]

# Other companies: each must keep its own key.
OTHERS = [
    "Example Robotic",
    "Example Robots",
    "Example Robotics Labs",
    "Example Robotics 2",
    "Robotics Example",
    "Exampel Robotics",
    "Example",
    "Northwind Labs",
    "Example Robotics Paris",
]


@pytest.mark.parametrize("name", ONE_COMPANY)
def test_one_company_under_every_spelling(name: str) -> None:
    assert caps.company_key(name) == SAME


@pytest.mark.parametrize("name", OTHERS)
def test_other_companies_keep_their_own_key(name: str) -> None:
    assert caps.company_key(name) not in ("", SAME)


@pytest.mark.parametrize(
    ("one", "two"),
    [
        ("Procter & Gamble", "Procter and Gamble"),
        ("AT&T", "AT and T"),
        ("A.B. Testing", "AB Testing"),
        ("Northwind Labs SAS", "northwind-labs"),
        ("株式会社サンプル", "株式会社 サンプル"),
        ("Inc", "inc."),
    ],
)
def test_neighbours_that_are_one_company(one: str, two: str) -> None:
    assert caps.company_key(one) == caps.company_key(two) != ""


@pytest.mark.parametrize("name", ["", "   ", "!!!", "&", "-", ".", "​"])
def test_nothing_left_is_an_empty_key(name: str) -> None:
    assert caps.company_key(name) == ""


# Issue #362 (a): an unseen character glued to a legal form or a word must not hide the company
# from a cap. One row per unseen character, then the true neighbours that must stay other keys.
@pytest.mark.parametrize(
    "name",
    [
        "Example Robotics\u200bInc",
        "Example Robotics\u00adInc.",
        "Example Robotics\u2060SAS",
        "Example Robotics\ufeffGmbH",
        "Example\u200bRobotics\u200bInc",
        "Example Robotics\u200b",
        "\u200bExample Robotics",
    ],
)
def test_an_unseen_character_still_answers_to_the_company(name: str) -> None:
    assert SAME in caps.company_keys(name, "other", "https://example.org/jobs/1")


@pytest.mark.parametrize(
    "name",
    ["Example Robotics\u200bLabs", "Example Robotics\u00ad2", "Northwind\u200bLabs"],
)
def test_an_unseen_character_never_makes_another_company_this_one(name: str) -> None:
    assert SAME not in caps.company_keys(name, "other", "https://example.org/jobs/1")


def test_a_huge_name_is_cut() -> None:
    assert len(caps.company_key("Example " * 10_000)) == 200


@pytest.mark.parametrize(
    ("ats", "url", "org"),
    [
        ("ashby", "https://jobs.ashbyhq.com/example-robotics/123", SAME),
        ("ashby", "https://jobs.ashbyhq.com/ExampleRobotics/123/application", SAME),
        ("greenhouse", "https://boards.greenhouse.io/examplerobotics/jobs/123", SAME),
        ("greenhouse", "https://job-boards.eu.greenhouse.io/examplerobotics/jobs/1", SAME),
        (
            "greenhouse",
            "https://boards.greenhouse.io/embed/job_app?for=examplerobotics&token=1",
            SAME,
        ),
        ("lever", "https://jobs.lever.co/example-robotics/abc", SAME),
        ("lever", "https://jobs.eu.lever.co/example_robotics/abc/apply", SAME),
        ("ashby", "https://jobs.ashbyhq.com.example.org/example-robotics/1", None),
        ("other", "https://example.org/careers/example-robotics", None),
        ("greenhouse", "https://boards.greenhouse.io/embed/job_app?token=1", None),
        ("ashby", "not a url", None),
    ],
)
def test_the_ats_organisation_of_a_form(ats: str, url: str, org: str | None) -> None:
    keys = caps.company_keys("Northwind Labs", ats, url)
    assert caps.company_key("Northwind Labs") in keys
    assert (SAME in keys) is (org is not None)


@pytest.mark.parametrize(
    ("now", "zone", "start"),
    [
        # 00:30 in Paris on the 15th: the day began at 22:00 UTC on the 14th
        (datetime(2026, 10, 14, 22, 30, tzinfo=UTC), "Europe/Paris", datetime(2026, 10, 14, 22, 0)),
        # 23:59 in Paris on the 14th
        (datetime(2026, 10, 14, 21, 59, tzinfo=UTC), "Europe/Paris", datetime(2026, 10, 13, 22, 0)),
        # 23:00 in Los Angeles on the 14th, already the 15th in UTC
        (
            datetime(2026, 10, 15, 6, 0, tzinfo=UTC),
            "America/Los_Angeles",
            datetime(2026, 10, 14, 7),
        ),
        # the day the clocks go back in Paris (25 hours): it began at 22:00 UTC the day before
        (datetime(2026, 10, 25, 22, 30, tzinfo=UTC), "Europe/Paris", datetime(2026, 10, 24, 22, 0)),
        (datetime(2026, 10, 14, 9, 0, tzinfo=UTC), "UTC", datetime(2026, 10, 14, 0, 0)),
        (datetime(2026, 10, 14, 9, 0, tzinfo=UTC), "Not/AZone", datetime(2026, 10, 14, 0, 0)),
    ],
)
def test_the_day_starts_at_midnight_in_the_persons_time_zone(
    now: datetime, zone: str, start: datetime
) -> None:
    assert caps.day_start(now, zone) == start.replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("ats", "url", "host", "interval_s", "daily"),
    [
        ("ashby", "https://jobs.ashbyhq.com/example-robotics/1", "ashby", 180, 20),
        ("greenhouse", "https://example.org/careers?gh_jid=1", "greenhouse", 180, 20),
        ("lever", "https://jobs.lever.co/example-robotics/1", "lever", 180, 20),
        ("other", "https://www.linkedin.com/jobs/view/1", "linkedin", 600, 15),
        ("other", "https://fr.linkedin.com/jobs/view/1", "linkedin", 600, 15),
        # the host decides before the ATS: a LinkedIn address is LinkedIn whatever its query says
        ("greenhouse", "https://www.linkedin.com/jobs/view/1?gh_jid=1", "linkedin", 600, 15),
        ("other", "https://www.linkedin.com/jobs/view/1?gh_jid=1", "linkedin", 600, 15),
        ("other", "https://linkedin.com.example.org/jobs/1", "linkedin.com.example.org", 180, 20),
        ("other", "https://www.Careers.Example.org/apply/1", "careers.example.org", 180, 20),
        ("other", "", "unknown", 180, 20),
    ],
)
def test_the_host_of_a_form_and_its_pace(
    ats: str, url: str, host: str, interval_s: int, daily: int
) -> None:
    assert caps.host_of(ats, url) == host
    assert caps.pace_of(host) == caps.Pace(timedelta(seconds=interval_s), daily)
