"""The typed address of "Sign in to a site" (browser B7): one accept and refuse table over the
api's check (app/browser/address.py) and browserd's (browser/browserd/signin.py), and both
always agree, on the table and on generated neighbours. The default is to refuse: a value is
accepted only when it reads as an http or https address with a host.
"""

import random
import string
import sys
from pathlib import Path
from typing import Any

import pytest

from app.browser.address import ADDRESS_MAX, check_address

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import signin  # noqa: E402

CHECKS = [check_address, signin.check_address]

ACCEPTED = [
    "https://example.org",
    "https://example.org/",
    "http://example.org",
    "https://example.org/login?next=/a",
    "https://www.example.org/fr/connexion?redirect=%2Fprofil&lang=fr#form",
    "HTTPS://EXAMPLE.ORG/Login",
    "Https://Example.Org",
    "https://example.org:8443/sign-in",
    "http://pages:8080/b7/site.html",
    "https://jobs.example-robotics.example/apply/123",
    "https://xn--socit-esab.fr/compte",
    "https://example.org/a/b/c.html",
    "https://example.org/?q=a+b&x=1",
    "https://example.org/path;param",
    "https://example.org/~person",
    "https://example.org/emploi/d%C3%A9veloppeur",
    "https://example.org#@not-a-user",
    "https://a.b.c.d.example.org",
    "https://1.example.org",
    "https://example.org/" + "a" * 2000,
    "http://203.0.113.7/login",
    "https://example.org:1",
    "https://example.org:65535",
    "https://example.org/login?next=https://example.org/a",
    "https://example.org/a--b",
    "https://example.org/-a",
]

# Spaces and line ends around an address are dropped; the rest is kept as typed.
TRIMMED = [
    (" https://example.org/login ", "https://example.org/login"),
    ("\thttps://example.org\n", "https://example.org"),
    ("https://example.org\r\n", "https://example.org"),
]

REFUSED: list[Any] = [
    "--remote-debugging-port=9222",
    "-a",
    "--",
    "-",
    "--user-data-dir=/profiles",
    " --remote-debugging-port=9222",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "file:///etc/passwd",
    "FILE:///etc/passwd",
    "chrome://settings",
    "chrome-extension://abc/page.html",
    "devtools://devtools/bundled/inspector.html",
    "view-source:https://example.org",
    "data:text/html,x",
    "about:blank",
    "blob:https://example.org/1",
    "ftp://example.org",
    "ws://example.org",
    "wss://example.org",
    "https://",
    "https:///",
    "https:///example.org",
    "http://",
    "https:example.org",
    "https:/example.org",
    "https//example.org",
    "example.org",
    "example.org/login",
    "www.example.org",
    "//example.org",
    "https://" + "a" * 5000 + ".org",
    "https://example.org/" + "a" * ADDRESS_MAX,
    "",
    " ",
    "\t\n",
    None,
    42,
    ["https://example.org"],
    {"address": "https://example.org"},
    b"https://example.org",
    "https://user@example.org",
    "https://user:password@example.org",
    "https://example.org@evil.example",
    "https://@example.org",
    "https://exa mple.org",
    "https://example.org/a b",
    "https://example.org/\x00",
    "https://example.org/\x7f",
    "https://example.org\nhttps://evil.example",
    "https://example.org\\@evil.example",
    "https:\\\\example.org",
    "https://evil.example\\.example.org/",
    "https://société.fr",
    "https://example.org/développeur",
    "ｈｔｔｐｓ://example.org",
    "https://ｅxample.org",
    "https://example.org\u200b",
    "https://example.org/\u202e",
    "https://[::1]/",
    "https://[2001:db8::1]:443/",
    "https://example.org.",
    "https://.example.org",
    "https://example..org",
    "https://-example.org",
    "https://example-.org",
    "https://exa_mple.org",
    "https://exa%6dple.org",
    "https://" + "a" * 64 + ".org",
    "https://" + ".".join(["a" * 60] * 5),
    "https://example.org:0",
    "https://example.org:65536",
    "https://example.org:99999",
    "https://example.org:-1",
    "https://example.org:+80",
    "https://example.org:8o",
    "https://example.org:",
    "https://example.org:80:90",
    "https://example.org: 80",
    "http s://example.org",
    "httpss://example.org",
    "shttp://example.org",
    " https ://example.org",
]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("address", ACCEPTED)
def test_an_http_or_https_address_with_a_host_is_accepted(check: Any, address: str) -> None:
    assert check(address) == address


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize(("typed", "kept"), TRIMMED)
def test_spaces_around_are_dropped(check: Any, typed: str, kept: str) -> None:
    assert check(typed) == kept


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("value", REFUSED)
def test_anything_else_is_refused(check: Any, value: Any) -> None:
    assert check(value) is None


def test_an_accepted_address_can_never_be_read_as_a_switch() -> None:
    for address in ACCEPTED + [kept for _, kept in TRIMMED]:
        assert not check_address(address).startswith("-")  # type: ignore[union-attr]


def test_the_two_copies_agree_on_generated_neighbours() -> None:
    """Every accepted address cut, doubled, or with one character changed, inserted or removed:
    both checks give the same answer, and what they accept still starts with http."""
    alphabet = string.printable + "éß\u200b\uff0f\u00a0-:/@\\[]%.?#"
    rng = random.Random(7)  # noqa: S311 - test data
    seen = 0
    for address in ACCEPTED:
        variants = {address[:i] for i in range(len(address) + 1)}
        for _ in range(40):
            at = rng.randrange(len(address) + 1)
            ch = rng.choice(alphabet)
            variants.add(address[:at] + ch + address[at:])
            variants.add(address[:at] + ch + address[at + 1 :])
            variants.add(address[:at] + address[at + 1 :])
        for variant in variants:
            got = check_address(variant)
            assert got == signin.check_address(variant), variant
            if got is not None:
                assert got.lower().startswith(("http://", "https://")), variant
            seen += 1
    assert seen > 2000


def test_a_huge_value_is_refused_at_once() -> None:
    assert check_address("https://example.org/" + "a" * 1_000_000) is None
    assert signin.check_address(" " * 1_000_000) is None
