"""Browser B10: LinkedIn's reader (sources/linkedin.py) on snapshots of invented postings, the
Runner's guard check, the pacing of 10 to 30 s on the shared per host pacer, the 5 result pages,
the sign in wall and the checkpoint, and the schema of the acceptance. No database: the guard's
rules over Postgres are tests/jobs/test_b10_linkedin_sweep.py."""

import asyncio
import random
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser.sources import MemoryPacer, Runner, SourceError, parse_snapshot
from app.browser.sources import linkedin as li
from app.profile.schemas import BrowserSource, SearchSection
from tests.browser import b10_pages as pages
from tests.browser.test_b8_sources import (
    FakeLeases,
    FakeTools,
    Known,
    Plan,
    RedirectTools,
    Stats,
)

EXTERNAL = "https://boards.example.org/acme/jobs/123?gh_src=li"


def src(**kw: Any) -> BrowserSource:
    return BrowserSource.model_validate(
        {"kind": "linkedin", "query": "AI Engineer", "enabled": True} | kw
    )


def site(made: list[pages.Offer], per_page: int | None = None) -> dict[str, str]:
    found: dict[str, str] = {}
    per = per_page or len(made) or 1
    for page in range(1, 7):
        chunk = made[(page - 1) * per : page * per]
        found[pages.search_url(page)] = pages.results(chunk)
    for o in made:
        found[pages.view(o)] = pages.posting(o)
    return found


async def arun(
    tools: FakeTools,
    check: Any = "allow",
    pacer: MemoryPacer | None = None,
    plan: Plan | None = None,
) -> tuple[Stats, list[tuple[str, list[Any]]], list[float], FakeLeases]:
    leases, slept, stored, now = FakeLeases(), [], [], [0.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    async def store(name: str, found: Any) -> None:
        stored.append((name, list(found)))

    async def progress() -> None:
        return None

    async def allow(plan: Any) -> str | None:
        return None

    checks = {} if check is None else {"linkedin": allow if check == "allow" else check}
    stats = Stats()
    runner = Runner(
        leases,  # type: ignore[arg-type]
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=random.Random(3),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: now[0],
        pacer=pacer or MemoryPacer(lambda: now[0]),
        checks=checks,
    )
    await runner(plan or Plan([src()]), stats, store, progress, 1e9)
    return stats, stored, slept, leases


def run(tools: FakeTools, **kw: Any) -> Any:
    return asyncio.run(arun(tools, **kw))


# --- results and postings -----------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 25])
def test_results_of_0_1_and_25(count: int) -> None:
    made = pages.offers(count)
    cards = li.parse_results(parse_snapshot(pages.results(made)), pages.search_url())
    assert [c.title for c in cards] == [o.title for o in made]
    if made:
        assert cards[0].company == "Example Robotics"
        assert cards[0].location == "Paris, Île-de-France, France"
        assert cards[0].url == f"https://www.linkedin.com/jobs/view/{made[0].number}/"


@pytest.mark.parametrize(
    "text",
    [
        '- main:\n  - heading "Something else" [level=1]',
        "- main:\n  - paragraph: no heading",
        pages.results(pages.offers(1)).replace("[level=3]", "[level=5]"),  # a card without title
        pages.results(pages.offers(1)).replace("[level=4]", "[level=6]"),  # without company
    ],
)
def test_results_layout_changed(text: str) -> None:
    with pytest.raises(SourceError) as caught:
        li.parse_results(parse_snapshot(text), pages.search_url())
    assert caught.value.code == "layout_changed"


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        (
            "https://fr.linkedin.com/jobs/view/ai-engineer-at-x-4100000001?position=1",
            "https://www.linkedin.com/jobs/view/4100000001/",
        ),
        (
            "https://www.linkedin.com/jobs/view/4100000001/",
            "https://www.linkedin.com/jobs/view/4100000001/",
        ),
        ("/jobs/view/4100000001", "https://www.linkedin.com/jobs/view/4100000001/"),
        (
            "https://WWW.LINKEDIN.COM/jobs/view/4100000001/",
            "https://www.linkedin.com/jobs/view/4100000001/",
        ),
        ("http://www.linkedin.com/jobs/view/4100000001/", None),  # not https
        ("https://linkedin.com.example.org/jobs/view/4100000001/", None),  # another host
        ("https://evil.example.org/jobs/view/4100000001/", None),
        ("https://user@www.linkedin.com/jobs/view/4100000001/", None),
        ("https://www.linkedin.com:8443/jobs/view/4100000001/", None),
        ("https://www.linkedin.com/jobs/view/abc/", None),
        ("https://www.linkedin.com/jobs/view/123/", None),  # too short to be an id
        ("https://www.linkedin.com/jobs/search/?keywords=x", None),
        ("https://www.linkedin.com/company/example", None),
        ("javascript:alert(1)", None),
        ("", None),
        ("https://www.linkedin.com/jobs/view/4100000001/" + "a" * 3000, None),
    ],
)
def test_posting_url(href: str, kept: str | None) -> None:
    assert li.posting_url(href, "https://www.linkedin.com/jobs/search/?keywords=x") == kept


def test_posting_url_on_the_fixture_server_keeps_its_prefix() -> None:
    page = "http://pages:8080/linkedin/jobs/search/?keywords=x"
    got = li.posting_url("/linkedin/jobs/view/ai-4100000001?x=1", page)
    assert got == "http://pages:8080/linkedin/jobs/view/4100000001/"
    assert li.posting_url("http://other:8080/linkedin/jobs/view/4100000001/", page) is None


GO = "https://www.linkedin.com/safety/go/?url="


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        (
            GO + "https%3A%2F%2Fboards.example.org%2Facme%2Fjobs%2F123&urlhash=x",
            "https://boards.example.org/acme/jobs/123",
        ),
        (GO + "https%3A%2F%2FJobs.Example.org%2Fa%3Fid%3D1", "https://jobs.example.org/a?id=1"),
        (
            "https://www.linkedin.com/jobs/view/externalApply/41?url=https%3A%2F%2Fa.example.org%2Fx",
            "https://a.example.org/x",
        ),
        ("/safety/go/?url=https%3A%2F%2Fa.example.org%2Fx", "https://a.example.org/x"),
        (GO + "https%3A%2F%2Fwww.linkedin.com%2Fjobs", None),  # back to LinkedIn
        (GO + "javascript%3Aalert(1)", None),
        (GO + "ftp%3A%2F%2Fa.example.org%2Fx", None),
        (GO + "https%3A%2F%2Fuser%3Apw%40a.example.org%2F", None),
        (GO + "https%3A%2F%2Fa.example.org%3Abad%2F", None),
        (GO, None),
        ("https://a.example.org/x", None),  # not through LinkedIn's redirect
        ("https://evil.example.org/safety/go/?url=https%3A%2F%2Fa.example.org", None),
        ("https://www.linkedin.com/jobs/view/41/?url=https%3A%2F%2Fa.example.org", None),
        (GO + "https%3A%2F%2Fa.example.org%2F" + "a" * 3000, None),
    ],
)
def test_external_url(href: str, kept: str | None) -> None:
    assert li.external_url(href, "https://www.linkedin.com/jobs/view/41/") == kept


def test_posting_text_and_external_link() -> None:
    o = pages.Offer("AI Engineer 1", external=EXTERNAL)
    text, external = li.parse_posting(parse_snapshot(pages.posting(o)), o.title, pages.view(o)) or (
        "",
        "",
    )
    assert external == EXTERNAL
    assert text.splitlines()[0] == "AI Engineer 1"
    assert "Build agents with Python" in text
    assert "Ship retrieval" in text
    assert "builds robots" not in text  # the next level 2 heading ends the description


def test_easy_apply_has_no_external_link() -> None:
    o = pages.Offer("AI Engineer 1")
    got = li.parse_posting(parse_snapshot(pages.posting(o)), o.title, pages.view(o))
    assert got is not None
    assert got[1] is None


def test_a_french_posting_page_is_read() -> None:
    o = pages.Offer("Ingénieur IA", external=EXTERNAL)
    text = (
        pages.posting(o)
        .replace("About the job", "À propos de l’offre d’emploi")
        .replace("Apply on company website", "Postuler sur le site de l’entreprise")
    )
    got = li.parse_posting(parse_snapshot(text), o.title, pages.view(o))
    assert got is not None
    assert got[1] == EXTERNAL


def test_a_posting_page_of_another_title_is_skipped_and_one_without_description_changed() -> None:
    o = pages.Offer("AI Engineer 1")
    assert (
        li.parse_posting(parse_snapshot(pages.posting(o)), "Data Engineer", pages.view(o)) is None
    )
    with pytest.raises(SourceError):
        li.parse_posting(
            parse_snapshot(pages.posting(o).replace("About the job", "Other")),
            o.title,
            pages.view(o),
        )


# --- walls --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://www.linkedin.com/checkpoint/challenge/AgH?x=1", "linkedin_checkpoint"),
        ("https://www.linkedin.com/checkpoint/lg/login-submit", "linkedin_checkpoint"),
        ("https://www.linkedin.com/login?session_redirect=x", "linkedin_signin"),
        ("https://www.linkedin.com/authwall?trk=x", "linkedin_signin"),
        ("https://www.linkedin.com/uas/login", "linkedin_signin"),
        ("https://www.linkedin.com/signup/cold-join", "linkedin_signin"),
        ("http://pages:8080/linkedin/checkpoint/challenge/", "linkedin_checkpoint"),
        ("http://pages:8080/linkedin/login", "linkedin_signin"),
        ("https://www.linkedin.com/jobs/view/login-engineer-4100000001/", None),
        ("https://www.linkedin.com/jobs/search/?keywords=checkpoint", None),
        ("https://www.linkedin.com/jobs/view/4100000001/", None),
        ("https://www.linkedin.com/feed/", None),
    ],
)
def test_wall_by_url(url: str, code: str | None) -> None:
    base = "http://pages:8080" if "pages:8080" in url else None
    assert li.wall_by_url(url, base) == code


@pytest.mark.parametrize(
    ("heading", "code"),
    [
        ("Let's do a quick security check", "linkedin_checkpoint"),
        ("Let’s do a quick security check", "linkedin_checkpoint"),
        ("Security verification", "linkedin_checkpoint"),
        ("We've detected unusual activity", "linkedin_checkpoint"),
        ("Vérification de sécurité", "linkedin_checkpoint"),
        ("Sign in", "linkedin_signin"),
        ("SIGN IN", "linkedin_signin"),
        ("Join LinkedIn", "linkedin_signin"),
        ("S’identifier", "linkedin_signin"),
        ("Sign in to apply", None),
        ("Signing engineer", None),
        ("About the job", None),
        ("", None),
    ],
)
def test_wall_by_heading(heading: str, code: str | None) -> None:
    assert li.wall_by_heading(parse_snapshot(f'- heading "{heading}" [level=1]')) == code


# --- the runner ---------------------------------------------------------------------------------


def test_a_sweep_reads_paced_10_to_30_s_and_stores_the_external_link() -> None:
    made = [
        pages.Offer("AI Engineer 1", external=EXTERNAL),
        pages.Offer("AI Engineer 2", number=4100000002),
    ]
    tools = FakeTools(site(made))
    stats, stored, slept, leases = run(tools)
    assert stats.sources[0]["error"] is None
    assert len(tools.calls) == 3
    assert slept
    assert all(10.0 <= s <= 30.0 for s in slept)
    [(name, found)] = stored
    assert name == "linkedin:AI Engineer"
    assert [p.url for _, p in found] == [EXTERNAL, pages.view(made[1])]
    assert found[0][1].text.endswith(f"Found on LinkedIn: {pages.view(made[0])}")
    assert leases.acquired[0][2] == ["www.linkedin.com"]


def test_the_pace_is_shared_per_host_with_another_lease() -> None:
    now = [0.0]
    pacer = MemoryPacer(lambda: now[0])
    asyncio.run(pacer.reserve("www.linkedin.com", 0.0))  # another person's page, just now
    made = pages.offers(1)
    _, _, slept, _ = run(FakeTools(site(made)), pacer=pacer)
    assert len(slept) == 2
    assert all(s >= 10.0 for s in slept)


def test_at_most_5_result_pages() -> None:
    made = pages.offers(25 * 7)
    tools = FakeTools(site(made, per_page=25) | {pages.search_url(7): pages.results(made[150:])})
    # Every posting is known already: only result pages are opened.
    plan = Plan([src()], known=Known({pages.view(o) for o in made}))
    stats, _, _, _ = run(tools, plan=plan)
    opened = [c for c in tools.calls if "/jobs/search/" in c]
    assert len(opened) == 5
    assert stats.sources[0]["result_pages"] == 5


def test_the_sign_in_wall_skips_the_source_and_starts_no_hand_off() -> None:
    url = pages.search_url()
    tools = RedirectTools({url: pages.SIGNIN}, {url: "https://www.linkedin.com/login?x=1"})
    stats, stored, _, leases = run(tools)
    assert stats.sources[0]["error"] == "linkedin_signin"
    assert stats.sources[0]["result_pages"] == 1  # counted as the sweep of the day
    assert stored == []
    assert tools.calls == [url]  # no click, no other page: nothing asks for a sign in
    assert leases.released  # the lease is given back


def test_a_sign_in_page_at_the_asked_address_is_the_wall_too() -> None:
    url = pages.search_url()
    stats, _, _, _ = run(FakeTools({url: pages.SIGNIN}))
    assert stats.sources[0]["error"] == "linkedin_signin"


def test_the_checkpoint_stops_the_source() -> None:
    made = pages.offers(2)
    found = site(made)
    tools = RedirectTools(
        found, {pages.view(made[1]): "https://www.linkedin.com/checkpoint/challenge/x"}
    )
    tools.pages[pages.view(made[1])] = pages.CHECKPOINT
    stats, stored, _, _ = run(tools)
    assert stats.sources[0]["error"] == "linkedin_checkpoint"
    [(_, kept)] = stored  # the posting read before the checkpoint is kept
    assert len(kept) == 1


@pytest.mark.parametrize("code", ["linkedin_off", "linkedin_paused", "linkedin_daily"])
def test_a_refused_check_runs_nothing(code: str) -> None:
    async def refuse(plan: Any) -> str | None:
        return code

    tools = FakeTools(site(pages.offers(1)))
    stats, stored, _, leases = run(tools, check=refuse)
    assert stats.sources[0]["error"] == code
    assert (tools.calls, stored, leases.acquired) == ([], [], [])


def test_without_its_guard_linkedin_never_runs() -> None:
    tools = FakeTools(site(pages.offers(1)))
    stats, _, _, leases = run(tools, check=None)
    assert stats.sources[0]["error"] == "linkedin_off"
    assert (tools.calls, leases.acquired) == ([], [])


def test_a_failing_check_runs_nothing() -> None:
    async def broken(plan: Any) -> str | None:
        raise RuntimeError("database gone")

    tools = FakeTools(site(pages.offers(1)))
    stats, _, _, _ = run(tools, check=broken)
    assert stats.sources[0]["error"] == "browser_unavailable"
    assert tools.calls == []


# --- the schema ---------------------------------------------------------------------------------


def test_the_acceptance_is_validated() -> None:
    ok = src(accepted_at="2026-10-20T10:00:00+00:00", text_version=1)
    assert ok.text_version == 1
    for bad in (
        {"accepted_at": "2026-10-20T10:00:00+00:00"},  # without its version
        {"text_version": 1},
        {"accepted_at": "yesterday", "text_version": 1},
        {"accepted_at": "2026-10-20T10:00:00", "text_version": 1},  # no offset
        {"accepted_at": "2026-10-20T10:00:00+00:00", "text_version": 0},
        {"accepted_at": "2026-10-20T10:00:00+00:00", "text_version": "1"},
    ):
        with pytest.raises(ValidationError):
            src(**bad)
    with pytest.raises(ValidationError):
        BrowserSource.model_validate(
            {
                "kind": "vie",
                "query": "x",
                "accepted_at": "2026-10-20T10:00:00+00:00",
                "text_version": 1,
            }
        )


def test_one_linkedin_source_per_person() -> None:
    one = {"kind": "linkedin", "query": "a"}
    SearchSection.model_validate({"browser_sources": [one, {"kind": "vie", "query": "b"}]})
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"browser_sources": [one, one | {"query": "b"}]})
