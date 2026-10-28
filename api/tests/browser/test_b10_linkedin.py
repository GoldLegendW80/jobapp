"""Browser B10: LinkedIn's reader (sources/linkedin.py) on snapshots of invented postings, the
Runner's guard check, the pacing of 10 to 30 s on the shared per host pacer, the 5 result pages,
the sign in wall and the checkpoint, and the schema of the acceptance. No database: the guard's
rules over Postgres are tests/jobs/test_b10_linkedin_sweep.py."""

import asyncio
import random
from typing import Any
from urllib.parse import quote

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
    log: list[Any] | None = None,
) -> tuple[Stats, list[tuple[str, list[Any]]], list[float], FakeLeases]:
    leases, slept, stored, now = FakeLeases(), [], [], [0.0]
    log = log if log is not None else []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    async def store(name: str, found: Any) -> None:
        log.append(("store", len(list(found))))
        stored.append((name, list(found)))

    async def progress() -> None:
        entry = stats.sources[-1] if stats.sources else {}
        log.append(("progress", entry.get("result_pages"), entry.get("error")))

    async def wall(plan: Any, code: str) -> None:
        log.append(("wall", code))

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
        walls={"linkedin": wall},
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
        assert cards[0].location == "Paris, France"  # folded (fold_location)
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
PAGE = "https://www.linkedin.com/jobs/view/4100000001/"


# Review round 1, B3: every internal address a redirect can name, refused; the posting then keeps
# its LinkedIn address.
REFUSED_TARGETS = [
    "http://127.0.0.1/",
    "http://localhost:8000/",
    "http://localhost./",
    "http://app.localhost/",
    "http://api:8000/",
    "http://db/",
    "http://10.0.0.1/",
    "http://172.16.5.4/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fd00::1]/",
    "http://[fe80::1%25en0]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:8.8.8.8]/",
    "http://100.100.100.100/",
    "http://100.64.0.1/",
    "http://0.0.0.0/",
    "http://255.255.255.255/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://0x7f.1/",
    "http://127.1/",
    "http://0177.0.0.1/",
    "http://010.8.8.8/",  # octal: the browser reads 8.8.8.8, a form never kept
    "http://8.8.8.8.9/",
    "http://a.123/",
    "http://\uff11\uff12\uff17.0.0.1/",  # full width digits
    "http://127\u30020\u30020\u30021/",  # ideographic dots
    "http://router.local/",
    "http://x.internal/",
    "http://host.docker.internal/",
    "http://nas.home.arpa/",
    "http://1.0.0.127.in-addr.arpa/",
    "http://host.ts.net/",
    "http://my-box.tail1234.ts.net/",
    "http://printer.lan/",
    "http://wiki.corp/",
    "http://a.test/",
    "http://a.invalid/",
    "http://abc.onion/",
    "http://127.0.0.1.nip.io/",
    "http://10.0.0.1.sslip.io/",
    "http://x.localtest.me/",
    "http://a..example.org/",
    "http://a.example.org/x y",
    "http://a.example.org/x\ty",
    "http://a.example.org/x\u0000",
    "http://a.example.org/\u200bx",
    "https://a.example.org:8443/x",
    "http://a.example.org:22/",
    "http://xn--/",
    "http://8.8.8.8/jobs/1",  # any IP literal, even a global one
    "http://[2001:4860:4860::8888]/",
    "http://[64:ff9b::7f00:1]/",  # NAT64 to 127.0.0.1
    "http://[::127.0.0.1]/",  # IPv4 compatible
    "http://10.0.0%2e1/",  # percent encoded dot: the browser reads 10.0.0.1
    "http://127.0.0.1%2e/",
    "http://192.168.1%2E1/",
    "http://169.254.169%2e254/",
    "http://0x7f.0.0%2e1/",
    "http://nas.home%2earpa/",
    "http://router.local%2e/",
    "http://x.ts%2enet/",
    "http://a.b%00/",
    "http://a.example%2eorg/",
    "http://-a.example.org/",
    "http://a-.example.org/",
    "http://a_b.example.org/",
    "http://a.example.org%25/",
    "http://1.2.3.4a/",
    "http://" + "a" * 64 + ".example.org/",
]
# Ten company addresses of the kinds LinkedIn links to: kept as they are.
COMPANY_TARGETS = [
    "https://boards.greenhouse.io/examplerobotics/jobs/4100000001",
    "https://job-boards.eu.greenhouse.io/examplerobotics/jobs/4100000002",
    "https://jobs.lever.co/example-robotics/0a1b2c3d-0000-4000-8000-000000000001/apply",
    "https://jobs.ashbyhq.com/example-robotics/0a1b2c3d-0000-4000-8000-000000000002",
    "https://examplerobotics.wd3.myworkdayjobs.com/en-US/Careers/job/Paris/AI-Engineer_R1",
    "https://careers.example-robotics.com/jobs/ai-engineer?src=linkedin",
    "https://www.example-robotics.fr/carrieres/ingenieur-ia",
    "https://jobs.smartrecruiters.com/ExampleRobotics/744000000000001",
    "https://bücher-beispiel.de/jobs/ki-ingenieur",
]


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Paris, Île-de-France, France", "Paris, France"),
        ("Paris, Île-de-France, France (Hybrid)", "Paris, France"),
        ("Lyon, Auvergne-Rhône-Alpes, France (On-site)", "Lyon, France"),
        ("Berlin, Berlin, Germany (Remote)", "Berlin, Germany"),
        ("Paris, Île-de-France, France (Hybride)", "Paris, France"),
        ("Paris, France", "Paris, France"),
        ("  Paris ,  France  ", "Paris, France"),
        ("France", "France"),
        ("European Union (Remote)", "European Union"),
        ("Greater Paris Metropolitan Region", "Greater Paris Metropolitan Region"),
        ("Paris (La Défense), Île-de-France, France", "Paris (La Défense), France"),
        ("", ""),
    ],
)
def test_fold_location(text: str, folded: str) -> None:
    assert li.fold_location(text) == folded


@pytest.mark.parametrize("target", REFUSED_TARGETS)
def test_external_url_refuses_an_internal_target(target: str) -> None:
    assert li.external_url(GO + quote(target, safe=""), PAGE) is None


@pytest.mark.parametrize("target", COMPANY_TARGETS)
def test_external_url_keeps_a_company_address(target: str) -> None:
    assert li.external_url(GO + quote(target, safe="") + "&urlhash=x", PAGE) == target


@pytest.mark.parametrize(
    ("host", "kept"),
    [
        ("Jobs.Example.ORG", "jobs.example.org"),
        ("\uff4a\uff4f\uff42\uff53.example.org", "jobs.example.org"),  # full width letters
        ("bücher-beispiel.de", "bücher-beispiel.de"),
        ("example.org.", "example.org"),
        ("8.8.8.8", None),
        ("2001:4860:4860::8888", None),
        ("[::1]", None),
        ("a.b%2ec", None),
        ("a.b" + "c" * 250 + ".org", None),
        ("xn--p1ai.xn--p1ai", "xn--p1ai.xn--p1ai"),
        ("", None),
        ("org", None),
        ("127.0.0.1", None),
        ("0x7f.1", None),
        ("2130706433", None),
        ("a.local", None),
        ("local", None),
        ("notlocal.org", "notlocal.org"),  # a suffix is a whole label, not a word ending
        ("my-internal.com", "my-internal.com"),
        ("internal.example.com", "internal.example.com"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
    ],
)
def test_public_host(host: str, kept: str | None) -> None:
    assert li.public_host(host) == kept


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


def test_no_result_page_cap_the_reader_reads_to_the_last_page() -> None:
    """Owner, 2026-10-28: no page cap for LinkedIn either. The reader goes on until a page
    brings no new card or fewer than a full page; pacing and the sweep's time bound it."""
    made = pages.offers(25 * 7 + 10)
    tools = FakeTools(
        site(made, per_page=25)
        | {
            pages.search_url(7): pages.results(made[150:175]),
            pages.search_url(8): pages.results(made[175:]),
        }
    )
    # Every posting is known already: only result pages are opened.
    plan = Plan([src()], known=Known({pages.view(o) for o in made}))
    stats, _, _, _ = run(tools, plan=plan)
    opened = [c for c in tools.calls if "/jobs/search/" in c]
    assert len(opened) == 8
    assert stats.sources[0]["result_pages"] == 8


def test_the_sign_in_wall_skips_the_source_and_starts_no_hand_off() -> None:
    url = pages.search_url()
    tools = RedirectTools({url: pages.SIGNIN}, {url: "https://www.linkedin.com/login?x=1"})
    stats, stored, _, leases = run(tools)
    assert stats.sources[0]["error"] == "linkedin_signin"
    assert stats.sources[0]["result_pages"] == 1  # the page it met
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


def test_the_checkpoint_and_the_days_count_are_saved_before_the_read_returns() -> None:
    # A kill after the checkpoint page (before the postings are stored) keeps the pause and the
    # sweep of the day: the wall hook (record_checkpoint) and a progress save holding the page
    # and the code both come before the store.
    made = pages.offers(2)
    tools = RedirectTools(
        site(made), {pages.view(made[1]): "https://www.linkedin.com/checkpoint/challenge/x"}
    )
    tools.pages[pages.view(made[1])] = pages.CHECKPOINT
    log: list[Any] = []
    run(tools, log=log)
    first_page = log.index(("progress", 1, None))
    walled = log.index(("wall", "linkedin_checkpoint"))
    saved = log.index(("progress", 1, "linkedin_checkpoint"))
    stored = log.index(("store", 1))
    assert first_page < walled < saved < stored


def test_the_sign_in_wall_is_saved_but_starts_no_pause() -> None:
    log: list[Any] = []
    run(FakeTools({pages.search_url(): pages.SIGNIN}), log=log)
    assert ("progress", 1, "linkedin_signin") in log
    assert ("wall", "linkedin_signin") in log  # the hook sees it; only a checkpoint pauses


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
