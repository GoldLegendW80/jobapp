"""The check of a finished PDF's text layer (D32, issue #862): the rule of `pdf_text`, the layer
PDFium reads from PDFs the tests render (one with the e-mail as text, one with an image in its
place), and the records the render job stores on the version, which the api shows apart from
the corrections and which never refuse anything.

Invented data only: "Alex Example", e2e-alex@example.org, +33 1 23 45 67 89."""

from __future__ import annotations

import asyncio
import ctypes
import io
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.documents import corrections, pdf_text, render_client, render_job, router
from app.documents.models import DocumentVersion, FitResult
from app.documents.render_client import Rendered
from tests.documents.render_support import RecordingQueue, cv_draft, give_profile
from tests.documents.store_support import (
    AT,
    USER_A,
    add_posting,
    add_user,
    db,
    engine,
    factory,
    migrated_url,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

EMAIL = "e2e-alex@example.org"
PHONE = "+33 1 23 45 67 89"
GOOD = f"Alex Example\nPlatform Engineer\n{EMAIL}\n{PHONE}\nLyon, France\nExperience"


TABLE = [
    # (case, the text layer, what is found)
    ("a good layer", GOOD, []),
    ("an empty layer", "", [pdf_text.NO_TEXT]),
    ("white space and punctuation only", " \n\r\n . , \xa0", [pdf_text.NO_TEXT]),
    ("the e-mail missing", GOOD.replace(EMAIL, ""), [pdf_text.EMAIL]),
    ("the e-mail in capitals", GOOD.replace(EMAIL, EMAIL.upper()), []),
    ("the e-mail cut by a line break", GOOD.replace("@", "@\r\n"), []),
    ("the e-mail in full width letters", GOOD.replace("alex", "\uff41lex"), []),
    ("another e-mail", GOOD.replace(EMAIL, "e2e-other@example.org"), [pdf_text.EMAIL]),
    ("the phone missing", GOOD.replace(PHONE, ""), [pdf_text.PHONE]),
    ("the phone with other separators", GOOD.replace(PHONE, "+33 (1) 23.45.67-89"), []),
    ("the phone with no-break spaces", GOOD.replace(PHONE, PHONE.replace(" ", "\xa0")), []),
    ("the phone without its plus", GOOD.replace(PHONE, PHONE[1:]), [pdf_text.PHONE]),
    ("one digit of the phone wrong", GOOD.replace("89", "88"), [pdf_text.PHONE]),
    ("a replacement character", GOOD + "\nR\ufffdsum\ufffd", [pdf_text.REPLACEMENT]),
    ("a cid code", GOOD + "\nExp(cid:12)rience", [pdf_text.GLYPH_NAME]),
    ("a slash uni name", GOOD + "\nR/uni00E9sum/uni00E9", [pdf_text.GLYPH_NAME]),
    ("a bare uni name", GOOD + "\nR uni00E9 sum", [pdf_text.GLYPH_NAME]),
    ("a slash g name", GOOD + "\nExperi/g42nce", [pdf_text.GLYPH_NAME]),
    ("a glyph name", GOOD + "\nglyph42", [pdf_text.GLYPH_NAME]),
    ("an afii name", GOOD + "\n/afii10017", [pdf_text.GLYPH_NAME]),
    ("notdef", GOOD + "\n.notdef", [pdf_text.GLYPH_NAME]),
    ("unique is a word", GOOD + "\nA unique platform, uniform and united.", []),
    ("an instance name", GOOD + "\nRan g5.xlarge and glyphs of type.", []),
    ("a path", GOOD + "\n/usr/bin and /g/ and lucid ideas", []),
    ("a year and a version", GOOD + "\nPython 3.12 since 2021, u2023 build", []),
    (
        "everything at once",
        "Alex Example \ufffd (cid:3)",
        [pdf_text.EMAIL, pdf_text.PHONE, pdf_text.REPLACEMENT, pdf_text.GLYPH_NAME],
    ),
]


@pytest.mark.parametrize(
    ("text", "expected"), [case[1:] for case in TABLE], ids=[case[0] for case in TABLE]
)
def test_the_text_layer_rule(text: str, expected: list[str]) -> None:
    assert list(pdf_text.check(text, EMAIL, PHONE)) == expected


def test_a_document_that_prints_no_email_or_phone_is_not_asked_for_them() -> None:
    assert pdf_text.check("Alex Example", "", "") == ()
    assert pdf_text.check("Alex Example", " ", "n/a") == ()


def test_the_records_and_their_summary() -> None:
    assert pdf_text.summary([]) is None
    assert pdf_text.summary([{"code": "banned_phrase"}]) is None
    passed = [record.as_dict() for record in pdf_text.records(())]
    assert pdf_text.summary(passed) == []
    failed = [record.as_dict() for record in pdf_text.records((pdf_text.EMAIL,))]
    assert pdf_text.summary(failed) == [{"code": pdf_text.EMAIL, "message": corrections.PDF_EMAIL}]
    assert all(record["reason"] in corrections.REASONS for record in failed)
    assert all(message in corrections.REASONS for message in pdf_text.MESSAGES.values())


# --- the layer PDFium reads, on PDFs the tests render ----------------------------------------

HTML = "<html><body style=\"font-family: 'Helvetica', sans-serif\">Alex Example</body></html>"


def _text(document: Any, page: Any, words: str, y: int) -> None:
    text = raw.FPDFPageObj_NewTextObj(document.raw, b"Helvetica", ctypes.c_float(11))
    wide = (words + "\0").encode("utf-16-le")
    raw.FPDFText_SetText(text, ctypes.cast(ctypes.c_char_p(wide), raw.FPDF_WIDESTRING))
    raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 72, y)
    raw.FPDFPage_InsertObject(page.raw, text)


def _pdf(email_as_image: bool) -> bytes:
    document = pdfium.PdfDocument.new()
    page = document.new_page(595, 842)
    _text(document, page, "Alex Example", 780)
    _text(document, page, PHONE, 740)
    if email_as_image:
        image = pdfium.PdfImage.new(document)
        bitmap = pdfium.PdfBitmap.from_pil(Image.new("RGB", (180, 14), "white"))
        image.set_bitmap(bitmap)
        image.set_matrix(pdfium.PdfMatrix().scale(180, 14).translate(72, 760))
        page.insert_obj(image)
    else:
        _text(document, page, EMAIL, 760)
    page.gen_content()
    page.close()
    out = io.BytesIO()
    document.save(out)
    document.close()
    return out.getvalue()


def test_the_layer_of_a_good_pdf_passes() -> None:
    pages, text = render_client._check(_pdf(email_as_image=False), 1, HTML)
    assert pages == 1
    assert EMAIL in text
    assert "Alex Example" in text
    assert pdf_text.check(text, EMAIL, PHONE) == ()


def test_an_image_in_place_of_the_email_is_found() -> None:
    pages, text = render_client._check(_pdf(email_as_image=True), 1, HTML)
    assert pages == 1
    assert "Alex Example" in text
    assert pdf_text.check(text, EMAIL, PHONE) == (pdf_text.EMAIL,)


def test_a_pdf_of_no_text_is_found_empty() -> None:
    document = pdfium.PdfDocument.new()
    document.new_page(595, 842).close()
    out = io.BytesIO()
    document.save(out)
    document.close()
    pages, text = render_client._check(out.getvalue(), 1, HTML)
    assert (pages, pdf_text.check(text, EMAIL, PHONE)) == (1, (pdf_text.NO_TEXT,))


# --- recorded by the render job, shown apart, never a refusal ----------------------------------


@dataclass
class TextRenderer:
    """A renderer stand in whose PDFs carry `text` as their layer (None: not read)."""

    text: str | None
    pdf: bytes = field(default_factory=lambda: _pdf(email_as_image=False))

    async def __call__(self, html: str) -> Rendered:
        return Rendered(pdf=self.pdf, pages=1, text=self.text)


def _render(factory: sessionmaker[Session], renderer: TextRenderer) -> DocumentVersion:
    with factory() as db:
        user = add_user(db, USER_A)
        posting = add_posting(db, user)
        db.commit()
    give_profile(factory, user, AT)
    with factory() as db:
        document = cv_draft(db, user, posting, AT)
    with factory() as db:
        found, _ = render_job.mark_queued(db, user, document)
        revision = found.draft_revision
        db.commit()
    asyncio.run(render_job.enqueue(RecordingQueue(), document, revision))
    ctx: dict[str, Any] = {"sessions": factory, "clock": lambda: AT, "documents_render": renderer}
    asyncio.run(render_job.render_document(ctx, str(document), revision))
    with factory() as db:
        version = db.execute(
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .options(sa.orm.undefer(DocumentVersion.pdf), sa.orm.undefer(DocumentVersion.content))
        ).scalar_one()
        db.expunge(version)
    return version


def test_a_render_records_its_check_and_the_api_shows_it_apart(
    factory: sessionmaker[Session],
) -> None:
    version = _render(factory, TextRenderer(GOOD.replace(EMAIL, "")))
    assert version.fit == FitResult.one_page  # stored all the same
    assert version.pdf
    codes = [record["code"] for record in version.corrections]
    assert codes == [pdf_text.CHECKED, pdf_text.EMAIL]
    summary = router._summary(version)
    assert summary["pdf_text"] == [{"code": pdf_text.EMAIL, "message": corrections.PDF_EMAIL}]
    assert summary["corrections_count"] == 0
    assert router._version_out(version).corrections == []


def test_a_good_render_records_a_pass(factory: sessionmaker[Session]) -> None:
    version = _render(factory, TextRenderer(GOOD))
    assert [record["code"] for record in version.corrections] == [pdf_text.CHECKED]
    assert router._summary(version)["pdf_text"] == []


def test_a_layer_not_read_records_nothing(factory: sessionmaker[Session]) -> None:
    version = _render(factory, TextRenderer(None))
    assert version.corrections == []
    assert router._summary(version)["pdf_text"] is None
