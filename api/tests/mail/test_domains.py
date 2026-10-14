"""mail/domains.py: the registrable domain, from the bundled public suffix list (mail M3)."""

import socket
from importlib import metadata

import pytest

from app.mail import domains


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.org", "example.org"),
        ("mail.hr.example.org", "example.org"),
        ("MAIL.Example.ORG.", "example.org"),
        ("careers.example.co.uk", "example.co.uk"),
        # A private suffix: each customer is an organisation of its own.
        ("alice.github.io", "alice.github.io"),
        ("www.alice.github.io", "alice.github.io"),
        ("acme.notion.site", "acme.notion.site"),
        # A top level domain the list does not know: its default rule, the last label.
        ("x.evil.example", "evil.example"),
        # Another script, written either way, is one name.
        ("bücher.example.org", "example.org"),
        ("www.bücher.example", "xn--bcher-kva.example"),
        ("www.xn--bcher-kva.example", "xn--bcher-kva.example"),
    ],
)
def test_the_registrable_domain(host: str, expected: str) -> None:
    assert domains.registrable_domain(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        "",
        "org",
        "co.uk",
        "github.io",  # a suffix is nobody's domain
        "localhost",
        "127.0.0.1",
        "[127.0.0.1]",
        "2001:db8::1",
        "example..org",
        ".example.org",
        "exa mple.org",
        "example.org\r\nx",
        "example.org\x00",
        "under_score.example.org",
        "-dash.example.org",
        "a" * 64 + ".example.org",
        ".".join(["a" * 60] * 5) + ".org",
        "é" * 2000 + ".org",
        None,
        b"example.org",
        42,
    ],
)
def test_what_is_not_a_host_name_has_no_domain(host: object) -> None:
    assert domains.registrable_domain(host) is None
    assert not domains.same_organisation(host, host)


def test_two_customers_of_a_shared_suffix_are_two_organisations() -> None:
    assert domains.same_organisation("mail.example.org", "example.org")
    assert not domains.same_organisation("alice.github.io", "mallory.github.io")
    assert not domains.same_organisation("example.org", "example.com")


def test_the_snapshot_date_is_the_one_of_the_pinned_package() -> None:
    stamp = metadata.version("publicsuffixlist").rsplit(".", 1)[-1]
    assert stamp == domains.SNAPSHOT_DATE.strftime("%Y%m%d")


def test_no_network_is_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert domains.registrable_domain("jobs.example.org") == "example.org"
