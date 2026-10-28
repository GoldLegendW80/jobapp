"""The finder's session (app/browser/finder.py), pure: the address rules it shares with browserd,
navigation on one site, and the validation of what the agent reports. Fake tools, snapshots in
the format browserd answers, invented sites only. The runtime over the fake backend is in
test_finder_runtime.py."""

import asyncio
import random
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from app.browser import finder
from app.browser.finder import FinderSession, canonical
from app.browser.sources import Pages
from tests.browser.fakes import Tools

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))
from browserd.tools.policy import parse_hosts


def browserd_leases(hosts: list[str]) -> bool:
    """Whether browserd's own rule (policy.parse_hosts) accepts every host of a lease."""
    try:
        parse_hosts(hosts)
    except ValueError:
        return False
    return True


SITE = "https://careers.example-robotics.test/jobs"


def link(name: str, url: str, ref: int) -> str:
    return f'  - link "{name}" [ref=e{ref}] [cursor=pointer]:\n    - /url: {url}'


def listing(titles: list[str], *, next_url: str = "", extra: str = "") -> str:
    lines = ["- main [ref=e1]:", '  - heading "Open roles" [level=1] [ref=e2]']
    for n, title in enumerate(titles):
        lines.append(link(title, f"/jobs/{slug(title)}", 10 + n))
    if next_url:
        lines.append(link("Next page", next_url, 90))
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def slug(title: str) -> str:
    return "-".join(title.lower().split())


def posting_page(title: str, text: str = "Build agents for robots.") -> str:
    return (
        f'- main [ref=e1]:\n  - heading "{title}" [level=1] [ref=e2]\n'
        f"  - paragraph [ref=e3]: Paris, France\n  - paragraph [ref=e4]: {text}"
    )


def site_pages(*titles: str) -> dict[str, str]:
    found = {SITE: listing(list(titles))}
    for title in titles:
        found[f"https://careers.example-robotics.test/jobs/{slug(title)}"] = posting_page(title)
    return found


def posting_of(name: str, **kw: str) -> dict[str, str]:
    return {
        "title": name,
        "location": "Paris, France",
        "url": f"/jobs/{slug(name)}",
        "text": f"{name}: build agents.",
    } | kw


def session_on(pages_: dict[str, str], start: str = SITE) -> tuple[FinderSession, Tools]:
    tools = Tools(dict(pages_))
    pages = Pages(tools, 1e9, sleep=_no_sleep, rng=random.Random(1), monotonic=lambda: 0.0)  # noqa: S311
    asyncio.run(pages.open(start))
    return FinderSession(pages, canonical(start), pages.last_text, user_id="u"), tools


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("HTTPS://Careers.Example.TEST/Jobs#top", "https://careers.example.test/Jobs"),
        ("https://careers.example.test:443/jobs", "https://careers.example.test/jobs"),
        (
            "http://careers.example.test:8080/jobs?page=2",
            "http://careers.example.test:8080/jobs?page=2",
        ),
        ("https://careers.example.test", "https://careers.example.test/"),
        ("https://user:pw@careers.example.test/", ""),
        ("file:///etc/passwd", ""),
        ("data:text/html,hi", ""),
        ("javascript:alert(1)", ""),
        ("https://careers.example.test:99999/", ""),
        ("/jobs/1", ""),
        ("", ""),
        # Hosts as browserd accepts them (policy.py parse_hosts), or not an address.
        ("https://karriere.müller.de/jobs", "https://karriere.xn--mller-kva.de/jobs"),
        ("https://karriere.xn--mller-kva.de/jobs", "https://karriere.xn--mller-kva.de/jobs"),
        ("https://KARRIERE.MÜLLER.DE/", "https://karriere.xn--mller-kva.de/"),
        ("https://careers.ｅxample.test/", "https://careers.example.test/"),  # full width
        ("https://careers。example.test/", "https://careers.example.test/"),  # ideographic dot
        ("https://careers.example.test./", "https://careers.example.test/"),  # one final dot
        ("https://careers.example.test../", ""),
        ("https://my_site.example/", ""),
        ("https://[::1]/", ""),
        ("https://[::1]:8443/jobs", ""),
        ("https://a..b.test/", ""),
        ("https://.example.test/", ""),
        ("https://-bad.example.test/", ""),
        ("https://bad-.example.test/", ""),
        ("https://" + "a" * 64 + ".test/", ""),
        ("https://" + "a" * 63 + ".test/", "https://" + "a" * 63 + ".test/"),
        ("https://ex%41mple.test/", ""),
        ("https://careers example.test/", ""),
        ("https://evil.example\\@careers.example.test/", ""),
        ("https://careers.example.test\\jobs", ""),
        ("https://careers.example.test/jobs\\1", ""),
        ("https://pages/b9/careers.html", "https://pages/b9/careers.html"),
        # An IP v4 literal, or any last label all digits: browserd's parse_hosts refuses it.
        ("http://127.0.0.1:8080/", ""),
        ("https://10.0.0.1/careers", ""),
        ("https://jobs.example.123/", ""),
        ("https://jobs.example.123./", ""),
        ("https://jobs.123.example/", "https://jobs.123.example/"),
        ("https://jobs.example.a123/", "https://jobs.example.a123/"),
        # Paths and queries percent encoded outside printable ASCII, never twice.
        (
            "https://careers.example.test/jobs/ingénieur",
            "https://careers.example.test/jobs/ing%C3%A9nieur",
        ),
        (
            "https://careers.example.test/jobs/ing%C3%A9nieur",
            "https://careers.example.test/jobs/ing%C3%A9nieur",
        ),
        ("https://careers.example.test/a b", "https://careers.example.test/a%20b"),
        (
            "https://careers.example.test/jobs?q=ingé&p=2",
            "https://careers.example.test/jobs?q=ing%C3%A9&p=2",
        ),
        ("https://careers.example.test/jobs?q=a/b?c", "https://careers.example.test/jobs?q=a/b?c"),
        ('https://careers.example.test/"x"<y>', "https://careers.example.test/%22x%22%3Cy%3E"),
        ("https://careers.example.test/日本", "https://careers.example.test/%E6%97%A5%E6%9C%AC"),
    ],
)
def test_canonical_addresses(raw: str, kept: str) -> None:
    assert canonical(raw) == kept
    if kept:
        host = urlsplit(kept).hostname or ""
        assert browserd_leases([host]), host  # browserd leases it
        assert all(0x21 <= ord(c) <= 0x7E for c in kept)  # browserd's bad_url rule
        assert canonical(kept) == kept


@pytest.mark.parametrize(
    "host",
    ["pages", "a.b", "x" * 63, "a-b.c-d.e", "1.2.3.4", "*.example.test", "a_b", "-a", "a-",
     "A.example", "a..b", "", ".a", "a.", "x" * 64, "xn--mller-kva.de", "a b", "10.0.0.1",
     "127.0.0.1", "a.example.123", "a.example.123.", "123", "a.123.b", "a.b1", "1a.2b"],
)  # fmt: skip
def test_the_host_rule_is_browserds(host: str) -> None:
    wild = host.startswith("*.")
    assert bool(finder.ascii_host(host)) == (browserd_leases([host]) and not wild)


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("https://linkedin.com/jobs", True),
        ("https://www.linkedin.com/jobs/view/1", True),
        ("https://fr.LinkedIn.com/company/example-robotics/jobs", True),
        ("https://linkedin.com./jobs", True),
        ("https://LINKEDIN.COM/", True),
        ("http://touch.www.linkedin.com/", True),
        ("https://linkedin.com.example.test/jobs", False),
        ("https://notlinkedin.com/jobs", False),
        ("https://linkedin.co/jobs", False),
        ("https://careers.example-robotics.test/linkedin.com", False),
    ],
)
def test_linkedin_is_never_a_site(url: str, refused: bool) -> None:
    assert finder.not_a_site(canonical(url)) is refused


@pytest.mark.parametrize(
    ("host", "pair"),
    [
        ("example.test", ["example.test", "www.example.test"]),
        ("www.example.test", ["www.example.test", "example.test"]),
        ("careers.example.test", ["careers.example.test", "www.careers.example.test"]),
        ("pages", ["pages"]),
        ("www.pages", ["www.pages"]),
    ],
)
def test_a_lease_opens_the_host_and_its_www_twin_only(host: str, pair: list[str]) -> None:
    assert finder.host_pair(host) == pair


# --- the session's rules ----------------------------------------------------------------------


def test_navigation_to_another_site_is_refused_and_counted() -> None:
    session, tools = session_on(site_pages("AI Engineer"))
    for other in (
        "https://evil.example/steal",
        "https://careers.example-robotics.test.evil.example/",
        "http://careers.example-robotics.test/jobs",  # another scheme is another site
        "https://careers.example-robotics.test:8443/jobs",
        "//evil.example/x",
    ):
        text, error = asyncio.run(session.navigate(other))
        assert error, other
        assert "Refused" in text, other
    assert session.refused["other_host"] == 5
    assert tools.calls == [SITE]  # nothing was opened


@pytest.mark.parametrize("raw", ["file:///etc/passwd", "chrome://settings", "javascript:x()"])
def test_navigation_to_a_non_web_address_is_refused(raw: str) -> None:
    session, tools = session_on(site_pages())
    _text, error = asyncio.run(session.navigate(raw))
    assert error
    assert tools.calls == [SITE]


def test_a_relative_link_is_opened_on_the_site() -> None:
    session, tools = session_on(site_pages("AI Engineer"))
    text, error = asyncio.run(session.navigate("/jobs/ai-engineer"))
    assert not error
    assert "untrusted_page_snapshot" in text
    assert tools.calls[-1] == "https://careers.example-robotics.test/jobs/ai-engineer"


def test_at_most_twenty_pages_the_careers_page_included() -> None:
    many = {f"{SITE}?page={n}": listing([f"Role {n}"]) for n in range(1, 30)}
    session, tools = session_on({SITE: listing([])} | many)
    answers = [asyncio.run(session.navigate(f"{SITE}?page={n}")) for n in range(1, 25)]
    assert len(tools.calls) == finder.PAGES_MAX
    assert session.stopped == "page_budget"
    assert answers[-1][1]


def test_a_redirect_off_the_site_is_not_read() -> None:
    from tests.browser.fakes import RedirectTools

    target = "https://careers.example-robotics.test/jobs/moved"
    tools = RedirectTools(
        {SITE: listing([]), target: listing(["X"])}, {target: "https://evil.example/"}
    )
    pages = Pages(tools, 1e9, sleep=_no_sleep, rng=random.Random(1), monotonic=lambda: 0.0)  # noqa: S311
    asyncio.run(pages.open(SITE))
    session = FinderSession(pages, SITE, pages.last_text, user_id="u")
    _text, error = asyncio.run(session.navigate(target))
    assert error
    assert session.refused["other_host"] == 1
    assert target not in session.opened


REPORTS = [
    (posting_of("AI Engineer"), ""),
    (
        posting_of(
            "AI Engineer", url="https://CAREERS.example-robotics.test/jobs/ai-engineer#apply"
        ),
        "",
    ),
    (posting_of("AI Engineer", title="  "), "missing_field"),
    (posting_of("AI Engineer", text=""), "missing_field"),
    ({"title": "AI Engineer", "url": "/jobs/ai-engineer"}, "missing_field"),
    ({"title": 3, "url": "/jobs/ai-engineer", "text": "x"}, "missing_field"),
    ("AI Engineer", "not_a_posting"),
    (posting_of("AI Engineer", url="https://evil.example/jobs/ai-engineer"), "other_host"),
    (
        posting_of("AI Engineer", url="http://careers.example-robotics.test/jobs/ai-engineer"),
        "other_host",
    ),
    (posting_of("AI Engineer", url="mailto:jobs@example.test"), "not_an_address"),
    (posting_of("AI Engineer", url="/jobs/invented"), "not_seen"),
    (posting_of("AI Engineer", url=SITE), "not_a_posting_page"),
    (posting_of("A" * 301), "title_too_long"),
]


@pytest.mark.parametrize(("item", "reason"), REPORTS)
def test_report_postings_validates_each_posting(item: Any, reason: str) -> None:
    session, _ = session_on(site_pages("AI Engineer"))
    _answer, error = session.report([item])
    assert not error
    assert session.refused[reason] == (1 if reason else 0)
    assert len(session.found) == (0 if reason else 1)
    if not reason:
        assert session.found[0].url == "https://careers.example-robotics.test/jobs/ai-engineer"


def test_report_postings_refuses_a_duplicate_and_what_is_not_a_list() -> None:
    session, _ = session_on(site_pages("AI Engineer"))
    session.report(
        [posting_of("AI Engineer"), posting_of("AI Engineer", url="/jobs/ai-engineer#x")]
    )
    assert session.refused["duplicate"] == 1
    assert len(session.found) == 1
    assert session.report({"title": "x"})[1] is True


def test_at_most_a_hundred_postings_per_run() -> None:
    titles = [f"Role {n}" for n in range(120)]
    session, _ = session_on(site_pages(*titles))
    session.report([posting_of(t) for t in titles[:60]])
    session.report([posting_of(t) for t in titles[60:]])
    assert len(session.found) == finder.POSTINGS_MAX
    assert session.refused["postings_max"] == 20


def test_an_opened_posting_keeps_the_text_code_read_not_the_models() -> None:
    session, _ = session_on(site_pages("AI Engineer"))
    asyncio.run(session.navigate("/jobs/ai-engineer"))
    session.report([posting_of("AI Engineer", text="Invented: a salary of a million.")])
    [kept] = session.found
    assert "Build agents for robots." in kept.text
    assert "million" not in kept.text


def test_an_opened_posting_whose_page_does_not_show_the_title_is_refused() -> None:
    session, _ = session_on(site_pages("AI Engineer"))
    asyncio.run(session.navigate("/jobs/ai-engineer"))
    session.report([posting_of("Chief Invented Officer", url="/jobs/ai-engineer")])
    assert session.refused["title_not_on_page"] == 1
    assert session.found == []


def test_the_finder_is_internal_read_only_and_bounded() -> None:
    from app.agents import definitions

    agent = definitions.AGENTS[finder.NAME]
    assert agent.internal
    assert agent.private_io
    assert not agent.live
    assert set(agent.tools) == {
        "browser_navigate",
        "browser_snapshot",
        "browser_wait_for",
        "report_postings",
    }
    assert agent.model_for("anthropic_api_key") == finder.MODEL_API_KEY
    assert agent.model_for("claude_subscription") == finder.MODEL
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable(finder.NAME, "development")
    with pytest.raises(LookupError):
        agent.prompt({"request": "0" * 32}, None)  # a run nobody waits for: no model call


def test_the_agent_may_open_the_www_twin_of_the_site() -> None:
    session, tools = session_on(site_pages("AI Engineer") | {
        "https://www.careers.example-robotics.test/jobs/x": listing([]),
    })  # fmt: skip
    _text, error = asyncio.run(session.navigate("https://www.careers.example-robotics.test/jobs/x"))
    assert not error
    assert tools.calls[-1] == "https://www.careers.example-robotics.test/jobs/x"


class NoUrlTools(Tools):
    async def snapshot(self) -> dict[str, Any]:
        done = await super().snapshot()
        return {k: v for k, v in done.items() if k != "url"}


def test_a_snapshot_without_an_address_fails_closed() -> None:
    session, _tools = session_on(site_pages("AI Engineer"))
    session.pages.tools = NoUrlTools(site_pages("AI Engineer"), current=SITE)
    seen = set(session.seen)
    text, error = asyncio.run(session.snapshot())
    assert error
    assert "untrusted_page_snapshot" not in text
    assert session.seen == seen
    text, error = asyncio.run(session.navigate("/jobs/ai-engineer"))  # nor a page opened
    assert error
    assert "untrusted_page_snapshot" not in text
    assert "https://careers.example-robotics.test/jobs/ai-engineer" not in session.opened
