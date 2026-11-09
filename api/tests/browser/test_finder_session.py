"""The search agent's address rules (app/browser/finder.py), pure: the ones it shares with
browserd, and its definition. Invented sites only."""

import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.browser import finder
from app.browser.finder import canonical

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))
from browserd.tools.policy import parse_hosts


def browserd_leases(hosts: list[str]) -> bool:
    """Whether browserd's own rule (policy.parse_hosts) accepts every host of a lease."""
    try:
        parse_hosts(hosts)
    except ValueError:
        return False
    return True


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


def test_the_search_agent_is_internal_private_and_runs_only_for_a_pending_website() -> None:
    from app.agents import definitions

    agent = definitions.AGENTS[finder.INTERACTIVE_NAME]
    assert agent.internal
    assert agent.private_io
    assert not agent.live
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable(finder.INTERACTIVE_NAME, "development")
    with pytest.raises(LookupError):
        agent.prompt({"request": "0" * 32}, None)  # a run nobody waits for: no model call
