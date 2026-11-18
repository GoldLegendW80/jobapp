"""browserd's web_fetch on the real browser container (browser B28, round 1 of #805): the image's
own httpx, through the image's own egress proxy on 127.0.0.1:3128, against the pages server of
tests/browser.compose.yml. Marked `docker`, with the stack and the image of
test_browser_container.py.

What runs: a page fetched through the proxy (the pages server under `pages` and `elsewhere`,
which the stack's proxy lets through although their addresses are private); the same pages
server by its private address, which the proxy refuses and which therefore never sees the
request (a direct connection would reach it); a body without length that goes on to 64 MB, and
64 MB of zeros in gzip, both answered `too_large` while the process grows by far less than they
weigh. The stack's names are single label (`pages`), which `fetch.allowed` refuses before any
request, so the script below lets every address through to test the proxy, the client and the
bounds themselves; the address table is test_b28_web_fetch.py's.
"""

import json
from typing import Any

import pytest

from tests.browser.test_browser_container import Stack, no_lease_left, stack

__all__ = ["no_lease_left", "stack"]

pytestmark = [pytest.mark.docker]

FETCH = """
import asyncio, json, resource, sys
from types import SimpleNamespace
from browserd.tools import fetch
fetch.allowed = lambda url: None  # the stack's single label names: the proxy is the test
tab = SimpleNamespace(state=SimpleNamespace(discovery=True, native_failed=False), lease_tag="b28")
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
answer = asyncio.run(fetch.run(tab, {"url": sys.argv[1]}))
grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
answer.pop("snapshot", None)
print(json.dumps({**answer, "grown_kb": grown}))
"""
COUNTS = (
    "import urllib.request;"
    " print(urllib.request.urlopen('http://127.0.0.1:8000/counts').read().decode())"
)
ADDRESS = "import socket; print(socket.gethostbyname(socket.gethostname()))"


def fetched(stack: Stack, url: str) -> dict[str, Any]:
    return json.loads(stack.python(FETCH, url))


def counts(stack: Stack) -> dict[str, int]:
    return json.loads(stack.execute("pages", "python", "-I", "-c", COUNTS))


def test_a_page_is_fetched_through_the_egress_proxy(stack: Stack) -> None:
    page = fetched(stack, "http://elsewhere:8080/count/b28-proxied")
    assert page["ok"] is True, page
    assert page["title"] == "Elsewhere"
    assert counts(stack).get("b28-proxied") == 1


def test_a_private_address_is_refused_by_the_proxy_and_never_reached(stack: Stack) -> None:
    """The pages server by its own private address: not on the stack's allow list, so the proxy
    refuses it and the server counts nothing; a fetch that went direct would be counted."""
    private = stack.execute("pages", "python", "-I", "-c", ADDRESS).strip()
    answer = fetched(stack, f"http://{private}:8080/count/b28-direct")
    assert answer["ok"] is False, answer
    assert "b28-direct" not in counts(stack)


@pytest.mark.parametrize("path", ["/b28/endless", "/b28/bomb"])
def test_a_body_past_10_mb_is_refused_without_holding_it(stack: Stack, path: str) -> None:
    """64 MB without length, and 64 MB of zeros in about 64 KB of gzip: each answers too_large,
    and the fetching process grows by less than 32 MB (the bound is 10 MB, read once)."""
    answer = fetched(stack, f"http://pages:8080{path}")
    assert answer["error"] == "too_large", answer
    assert answer["grown_kb"] < 32 * 1024, answer
