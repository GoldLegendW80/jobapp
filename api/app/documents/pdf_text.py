"""The check of a finished PDF's text layer (SPEC-documents.md, "Being careful", the finished
PDF): pure.

After `verify_pdf.py` of ai-job-search (MIT, NOTICE): a PDF is read by an application system as
its text layer, not as its picture. The worker reads that layer with PDFium where it already
counts the pages of a PDF the app's renderer made (`render_client._check`, never a file from
elsewhere), and `check` names what is wrong with it:

- `pdf_no_text`: no letter or digit at all (a page drawn as an image); nothing else is checked;
- `pdf_email_not_text`, `pdf_phone_not_text`: the e-mail or the phone the document prints (the
  contact lines of the profile, as the template prints them) is not in the layer, compared with
  white space, case and compatibility forms aside, and for the phone its separators aside
  (spaces, dots, hyphens, brackets, slashes);
- `pdf_replacement_character`: U+FFFD, a letter the font could not map;
- `pdf_glyph_name`: a glyph name left in place of a letter: `(cid:12)`, `/uni00E9`, `uni00E9`,
  `/g42`, `glyph42`, `/afii10017`, `.notdef`.

Each finding is recorded on the version as a record of its `corrections` (`records`), with the
sentence of what to do as its reason, beside `pdf_text_checked`, which says the check ran. They
are read apart from the corrections (`summary`): a finding is shown on the document's page and
never refuses a download or a send (Decision 4).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from app.documents import corrections
from app.documents.corrections import Correction

FIELD = "pdf"
CHECKED = "pdf_text_checked"
NO_TEXT = "pdf_no_text"
EMAIL = "pdf_email_not_text"
PHONE = "pdf_phone_not_text"
REPLACEMENT = "pdf_replacement_character"
GLYPH_NAME = "pdf_glyph_name"
PROBLEMS = (NO_TEXT, EMAIL, PHONE, REPLACEMENT, GLYPH_NAME)
CODES = frozenset((CHECKED, *PROBLEMS))
# What the person reads for each finding, with what to do (`corrections.REASONS`).
MESSAGES = {
    CHECKED: corrections.PDF_CHECKED,
    NO_TEXT: corrections.PDF_NO_TEXT,
    EMAIL: corrections.PDF_EMAIL,
    PHONE: corrections.PDF_PHONE,
    REPLACEMENT: corrections.PDF_REPLACEMENT,
    GLYPH_NAME: corrections.PDF_GLYPH_NAME,
}
TEXT_MAX = 200_000  # characters of the layer read: a page of text is a few thousand

_SEPARATORS = re.compile(r"[\s.\-()/\xa0\u2010\u2011\u2012\u2013\u202f]+")
_GLYPH = re.compile(
    r"\(cid:\d{1,5}\)|\.notdef"
    r"|/(?:uni[0-9A-Fa-f]{4,6}|u[0-9A-F]{4,6}|g\d{1,5}|glyph\d{1,5}|afii\d{5}|cid\d{1,5})"
    r"|(?<![\w/])(?:uni[0-9A-F]{4,6}|glyph\d{1,5})(?![\w])"
)


def _flat(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def check(text: str, email: str, phone: str) -> tuple[str, ...]:
    """The findings on a PDF's text layer, in the order of PROBLEMS; empty when it passes.
    `email` and `phone` are what the document prints, empty when it prints none."""
    text = text[:TEXT_MAX]
    if not any(char.isalnum() for char in text):
        return (NO_TEXT,)
    found: list[str] = []
    flat = _flat(text)
    if email.strip() and _flat(email) not in flat:
        found.append(EMAIL)
    digits = _SEPARATORS.sub("", unicodedata.normalize("NFKC", phone))
    printed = _SEPARATORS.sub("", unicodedata.normalize("NFKC", text))
    if any(char.isdigit() for char in digits) and digits not in printed:
        found.append(PHONE)
    if "\ufffd" in text:
        found.append(REPLACEMENT)
    if _GLYPH.search(text):
        found.append(GLYPH_NAME)
    return tuple(found)


def records(found: Iterable[str]) -> list[Correction]:
    """The records of one check: `pdf_text_checked`, then one per finding."""
    return [Correction(FIELD, "", "", code, "code", MESSAGES[code]) for code in (CHECKED, *found)]


def is_record(record: Mapping[str, Any]) -> bool:
    return record.get("code") in CODES


def summary(stored: Iterable[Mapping[str, Any]]) -> list[dict[str, str]] | None:
    """The findings of a version's stored records, `{code, message}` each; None when its PDF
    was never checked (a version rendered before the check, or not rendered yet)."""
    mine = [record for record in stored if is_record(record)]
    if not any(record.get("code") == CHECKED for record in mine):
        return None
    return [
        {"code": str(record["code"]), "message": MESSAGES[str(record["code"])]}
        for record in mine
        if record.get("code") in PROBLEMS
    ]
