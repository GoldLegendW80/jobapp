"""The worker's reading of a design answer (documents D25a, parser_client.check_design_pdf and
check_design_docx, read_design): every byte of it is hostile, so each rule is broken here once
and refused, and a good answer goes through a server that answers it over a connection."""

import copy
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import design_fixtures as fx
from tests.ingestion.parser.helpers import Plenty, as_in_the_child, sandbox_handler, sandbox_module
from tests.ingestion.parser.test_client_calls import framed, lying, run

main = sandbox_module("main")


def answer_of(kind: str, data: bytes) -> dict:
    with as_in_the_child():
        return json.loads(main.encode(sandbox_handler(kind).handle(data, {}, Plenty())))


PDF = answer_of("design_pdf", fx.one_column())
DOCX = answer_of("design_docx", fx.styled_docx())


def broken(answer: dict, change: Callable[[dict], Any]) -> dict:
    copied = copy.deepcopy(answer)
    change(copied)
    return copied


def rule_of(check: Callable[[Any], Any], answer: Any) -> str:
    with pytest.raises(pc.ParserMalformedError) as caught:
        check(answer)
    return caught.value.code


def first_run(answer: dict) -> dict:
    return answer["read"][0]["runs"][0]


PDF_BREAKS = [
    ("a key more", lambda a: a.update(extra=1), "keys"),
    ("another kind", lambda a: a.update(kind="pdf_render"), "kind"),
    ("no page", lambda a: a.update(pages=0), "pages"),
    ("three pages read", lambda a: a.update(read=a["read"] * 3), "pages"),
    ("a page twice", lambda a: a.update(read=a["read"] * 2), "page"),
    ("a box past the page", lambda a: first_run(a).update(box=[0, 0, 5951, 10]), "box"),
    ("a box upside down", lambda a: first_run(a).update(box=[10, 10, 5, 20]), "box"),
    ("a negative box", lambda a: first_run(a).update(box=[-1, 0, 5, 20]), "box"),
    ("a box of three", lambda a: first_run(a).update(box=[0, 0, 5]), "box"),
    ("a bool for a number", lambda a: first_run(a).update(size=True), "runs"),
    ("a font that is not in the table", lambda a: first_run(a).update(font=99), "runs"),
    ("a colour by name", lambda a: first_run(a).update(colour="red"), "colour"),
    ("a colour in upper case", lambda a: first_run(a).update(colour="#FFFFFF"), "colour"),
    ("an empty run", lambda a: first_run(a).update(text=""), "run_text"),
    ("a run with a NUL", lambda a: first_run(a).update(text="a\x00b"), "run_text"),
    ("a run with a line feed", lambda a: first_run(a).update(text="a\nb"), "run_text"),
    ("a run with a surrogate", lambda a: first_run(a).update(text="a\ud800"), "run_text"),
    ("a run past its cap", lambda a: first_run(a).update(text="x" * 301), "run_text"),
    ("runs past the cap", lambda a: a["read"][0].update(runs=[first_run(a)] * 1_501), "runs"),
    (
        "a shape of another type",
        lambda a: a["read"][0]["shapes"][0].update(type="circle"),
        "shapes",
    ),
    ("an image with pixels", lambda a: a["read"][0]["images"][0].update(jpeg="AA=="), "images"),
    ("margins without text", lambda a: a["read"][0].update(runs=[]), "margins"),
    ("a font weight of 50", lambda a: a["fonts"][0].update(weight=50), "font"),
    ("a font name with a tab", lambda a: a["fonts"][0].update(name="a\tb"), "font"),
    ("a scan with runs", lambda a: a.update(scanned=True), "scanned"),
    ("a sentence as a note", lambda a: a.update(notes=[{"code": "the file is fine"}]), "note_code"),
    ("a note of another kind", lambda a: a.update(notes=[{"code": "scans_not_read"}]), "note_code"),
]


@pytest.mark.parametrize(("name", "change", "rule"), PDF_BREAKS, ids=[r[0] for r in PDF_BREAKS])
def test_a_pdf_answer_that_breaks_a_rule_is_refused(name: str, change: Any, rule: str) -> None:
    assert rule_of(pc.check_design_pdf, broken(PDF, change)) == rule, name


def test_the_text_of_all_runs_together_is_capped() -> None:
    page = PDF["read"][0]
    run_ = {**page["runs"][0], "text": "x" * 300}
    many = {**page, "runs": [run_] * 1_500}
    two = {**PDF, "read": [many, {**many, "index": 1}], "pages": 2}
    assert rule_of(pc.check_design_pdf, two) == "text_too_long"


DOCX_BREAKS = [
    ("a key more", lambda a: a.update(extra=1), "keys"),
    ("a style of another type", lambda a: a["styles"][0].update(type="script"), "styles"),
    ("a style without its id", lambda a: a["styles"][0].update(id=""), "name"),
    ("a style key more", lambda a: a["styles"][0].update(onload="x"), "styles"),
    ("a size that is text", lambda a: a["defaults"].update(size="11pt"), "props"),
    ("a size past the bound", lambda a: a["defaults"].update(size=100_001), "props"),
    ("an alignment of its own", lambda a: a["defaults"].update(align="sideways"), "props"),
    ("a colour by name", lambda a: a["styles"][1].update(colour="blue"), "props"),
    ("a bold that is a number", lambda a: a["styles"][1].update(bold=1), "props"),
    ("an outline of 10", lambda a: a["styles"][1].update(outline=10), "styles"),
    ("a theme colour of its own", lambda a: a["theme"]["colours"].update(evil="#000000"), "theme"),
    ("a font name past its cap", lambda a: a["theme"].update(major="F" * 65), "name"),
    ("a section without columns", lambda a: a["sections"][0].update(columns=0), "sections"),
    ("a count below zero", lambda a: a.update(tables=-1), "counts"),
    ("formats past the cap", lambda a: a.update(formats=a["formats"] * 41), "formats"),
    ("a format with text", lambda a: a["formats"][0].update(text="Alex"), "formats"),
    ("a sentence as a note", lambda a: a.update(notes=[{"code": "fine"}]), "note_code"),
]


@pytest.mark.parametrize(("name", "change", "rule"), DOCX_BREAKS, ids=[r[0] for r in DOCX_BREAKS])
def test_a_docx_answer_that_breaks_a_rule_is_refused(name: str, change: Any, rule: str) -> None:
    assert rule_of(pc.check_design_docx, broken(DOCX, change)) == rule, name


def test_a_refusal_of_the_child_is_its_error() -> None:
    with pytest.raises(pc.ParserRefusedError) as caught:
        pc.check_design_docx({"ok": False, "failure": "file", "code": "macros"})
    assert (caught.value.failure, caught.value.code) == ("file", "macros")


# --- over a connection --------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "answer"), [("design_pdf", PDF), ("design_docx", DOCX)])
def test_a_good_answer_arrives_and_nothing_of_the_file_is_logged(
    kind: str, answer: dict, caplog: pytest.LogCaptureFixture
) -> None:
    async def test() -> None:
        async with lying(framed(answer)) as port:
            found = await pc.read_design(kind, fx.CANARY.encode(), host="127.0.0.1", port=port)
        assert isinstance(found, pc.DesignPdf if kind == "design_pdf" else pc.DesignDocx)

    with caplog.at_level(logging.DEBUG):
        run(test)
    logged = caplog.text + " ".join(str(vars(record)) for record in caplog.records)
    assert kind in logged
    assert fx.CANARY not in logged


def test_an_answer_announced_over_the_design_cap_is_refused_before_it_is_read() -> None:
    async def test() -> None:
        line = {"ok": True, "failure": None, "code": "ok", "length": pc.DESIGN_MAX_ANSWER + 1}
        async with lying(json.dumps(line).encode() + b"\n") as port:
            with pytest.raises(pc.ParserMalformedError) as caught:
                await pc.read_design("design_pdf", b"%PDF-", host="127.0.0.1", port=port)
        assert caught.value.code == "length"

    run(test)


def test_the_death_of_a_design_pdf_child_is_the_files_and_of_a_design_docx_the_servers() -> None:
    async def test() -> None:
        dead = {"ok": False, "failure": "server", "code": "crashed", "length": 0}
        reply = json.dumps(dead).encode() + b"\n"
        for kind, outcome in (
            ("design_pdf", ("file", "bomb")),
            ("design_docx", ("server", "crashed")),
        ):
            async with lying(reply) as port:
                with pytest.raises(pc.ParserRefusedError) as caught:
                    await pc.read_design(kind, b"x", host="127.0.0.1", port=port)
            assert (caught.value.failure, caught.value.code) == outcome

    run(test)


def test_a_design_kind_is_not_a_parse_and_a_parse_kind_is_not_a_design() -> None:
    async def test() -> None:
        for kind in pc.DESIGN_KINDS:
            with pytest.raises(ValueError, match="read_design"):
                await pc.parse(kind, b"x")
        with pytest.raises(ValueError, match="not a design kind"):
            await pc.read_design("pdf_text", b"x")

    run(test)
