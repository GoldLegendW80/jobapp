"""The sanitiser of a mail's HTML for real (mail M3): the worker's image, the `parser` service as
compose.yml defines it, its locked down child with nh3 inside, and the hand made pages of
fixtures/m3/html sent with the application's own client, `parser_client.sanitise_mail_html`.
Nothing is mounted over the image and nothing is simulated.

What it proves, each on Linux under the limits of the child (16 descriptors, no file, no
process, no network, the address space of its kind, 45 s of CPU, the seccomp deny list):

- nh3 is in the image, and imports and runs under the lockdown;
- every page comes back as the very answer the handler gives in the test process
  (test_mail_html.py states what each holds and does not hold), through the worker's checks;
- the pages written to cost the most (nesting, one unclosed tag, output that outgrows its
  input) end as a truncated answer, never as a death of the child, and the service answers the
  next job with its sandbox in force;
- 1 kB, 100 kB and 500 kB of HTML stay far inside the address space of the kind.

Marked `docker`. The image is built by the fixture from api/Dockerfile (target `worker`) under
a tag of its own unless PARSER_TEST_IMAGE names one. `MAIL_MEASURE_OUT=<file>` writes what each
page cost, for the plan's Measurements.
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion.parser.helpers import API, ROOT, Plenty, sandbox_handler, sandbox_module
from tests.ingestion.parser.test_pdf_container import Stack
from tests.mail import m3_fixtures as fx
from tests.mail.test_mail_html import ALLOWED, Shape
from tests.pg import docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]

main = sandbox_module("main")
mail_html = sandbox_handler("mail_html")
MEASURED: dict[str, dict[str, Any]] = {}
PAGES = (
    "ordinary",
    "scripts",
    "links",
    "styles",
    "document_tricks",
    "foreign",
    "images",
    "entities",
    "instructions",
)
PARAGRAPH = (
    "<p>Bonjour Zoé, <b>merci</b> pour votre <a href='https://example.org/a'>message</a>.</p>"
)


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("PARSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-worker:mail-m3-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-mail-m3-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "120")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)
        out = os.environ.get("MAIL_MEASURE_OUT")
        if out and MEASURED:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(MEASURED, handle, indent=2, sort_keys=True)
                handle.write("\n")


def sanitise(stack: Stack, markup: str, name: str = "") -> pc.MailHtml:
    """One page through the real service, with the application's own client."""
    started = time.monotonic()
    got = asyncio.run(pc.sanitise_mail_html(markup, host="127.0.0.1", port=stack.port("parser")))
    usage = got.usage
    assert usage is not None
    if name:
        MEASURED[name] = {
            "bytes": len(markup.encode()),
            "wall_ms": int((time.monotonic() - started) * 1000),
            "html_bytes": len(got.html.encode()),
            "characters": len(got.text),
            **dict(usage),
        }
    # Under 80 % of what the child may use.
    assert 0 < usage["max_vm_kb"] * 1024 < main.LIMITS["mail_html"]["as"] * 8 // 10
    return got


def here(markup: str) -> pc.MailHtml:
    """The same page through the handler in this process, and the same checks."""
    answer = mail_html.handle(markup.encode(), {}, Plenty())
    return pc.check_mail_html(pc.loads(main.encode(answer)))


def same(real: pc.MailHtml, local: pc.MailHtml) -> None:
    assert (real.html, real.text, real.remote_images, real.truncated) == (
        local.html,
        local.text,
        local.remote_images,
        local.truncated,
    )


def test_an_ordinary_mail_is_sanitised_by_the_real_child(stack: Stack) -> None:
    got = sanitise(stack, fx.page("ordinary"), "ordinary")
    same(got, here(fx.page("ordinary")))
    assert "<h1>Bonjour Zoé,</h1>" in got.html
    assert "Suivre : votre espace (https://jobs.example.com/track?id=42&lang=fr)" in got.text
    stack.alive()


@pytest.mark.parametrize("name", PAGES[1:])
def test_a_hostile_page_comes_back_as_the_handler_s_own_answer(stack: Stack, name: str) -> None:
    got = sanitise(stack, fx.page(name), name)
    same(got, here(fx.page(name)))
    for tag, attributes in Shape(got.html).tags:
        assert tag in mail_html.TAGS
        assert set(attributes) <= ALLOWED.get(tag, set())


@pytest.mark.parametrize(
    ("name", "page", "truncated"),
    [
        ("tables_1000_deep", "<table><tr><td>" * 1000 + "deep words", True),
        ("div_100000_deep", "<p>before</p>" + "<div>" * 100_000 + "never read", True),
        ("unclosed_tag_500_kb", "<div " + "a" * 499_995, False),
        ("unclosed_comment_500_kb", "<p>kept</p><!--" + "x" * 499_985, False),
        ("ampersands_500_kb", "&" * 500_000, True),
        ("quotes_500_kb", '"' * 500_000, True),
        ("links_480_kb", "<a href=http://a.example>x</a>" * 16_000, True),
        ("emoji_2_mb", "<p>first</p>" + "🎉" * 520_000, True),
    ],
)
def test_a_page_written_to_cost_the_most_is_cut_and_the_service_lives(
    stack: Stack, name: str, page: str, truncated: bool
) -> None:
    got = sanitise(stack, page, name)
    same(got, here(page))
    assert got.truncated is truncated
    assert len(got.html.encode()) <= pc.MAX_MAIL_HTML_BYTES
    stack.alive()
    # And the next job is served as if nothing had happened.
    assert sanitise(stack, "<p>next</p>").html == "<p>next</p>"


@pytest.mark.parametrize(
    ("name", "size"), [("1_kb", 1_000), ("100_kb", 100_000), ("500_kb", 500_000)]
)
def test_the_cost_of_a_message_by_size(stack: Stack, name: str, size: int) -> None:
    page = PARAGRAPH * (size // len(PARAGRAPH.encode()))
    got = sanitise(stack, page, f"size_{name}")
    same(got, here(page))
    # Each link gains its `rel`: 500 kB of them outgrow the cap of the stored HTML and are cut.
    assert got.truncated is (size == 500_000)
    assert got.text.startswith("Bonjour Zoé, merci pour votre message (https://example.org/a).")


def test_a_document_kind_still_answers_beside_it(stack: Stack) -> None:
    """The kind lists are shared with ingestion: adding one took nothing from the others."""
    with pytest.raises(pc.ParserRefusedError) as caught:
        asyncio.run(stack.parse()("docx", b"not a document"))
    assert caught.value.failure == "file"
    stack.alive()
