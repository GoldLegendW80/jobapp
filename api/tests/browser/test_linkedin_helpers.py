"""LinkedIn's address and wall helpers (sources/linkedin.py), kept for the guard and the agent
of a LinkedIn website: the pace host, the company address behind LinkedIn's redirect, the public
host rule, the sign in wall and the checkpoint by address and by heading, and the schema of the
acceptance row. Invented addresses only."""

from typing import Any
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from app.browser.sources import linkedin as li
from app.browser.sources import parse_snapshot
from app.profile.schemas import BrowserSource, SearchSection


def src(**kw: Any) -> BrowserSource:
    return BrowserSource.model_validate(
        {"kind": "linkedin", "query": "AI Engineer", "enabled": True} | kw
    )


def test_the_pace_host_is_linkedins_or_the_fixture_servers() -> None:
    assert li.host(None) == li.HOST == "www.linkedin.com"
    assert li.host("http://pages:8080") == "pages"


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
    SearchSection.model_validate({"browser_sources": [one, {"kind": "freework", "query": "b"}]})
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"browser_sources": [one, one | {"query": "b"}]})
