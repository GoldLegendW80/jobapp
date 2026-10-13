"""The CSV files of a LinkedIn data export (task G7 of the ingestion plan). Runs only inside the
locked down child of the parser service: main.py imports this file after the lockdown and calls
`handle`. The bytes are hostile, and so is every name found in them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

An export is a ZIP that holds, at its root, at least one of the files of FILES: the ones that
describe the member's own profile. Every other entry is ignored and never inflated: messages,
connections, contacts, invitations, advertising data and whatever else an export carries are
about other people, or about nothing a job application needs. The archive is opened under the
caps of zipcaps.py, and a name is only ever compared with FILES (case does not count); what is
answered is the name of FILES, never the name the archive wrote.

What comes back for an export that was read:

    {"ok": true, "kind": "linkedin_zip", "documents": [
        {"name": "Positions.csv", "text": ..., "sha256": ..., "size": N, "truncated": bool}]}

One document per known file that holds text, in the order of FILES. `sha256` and `size` are
those of the file as the archive holds it: the worker stores each one as a document of its
own, found again by this hash when the export, or the CSV alone, is added a second time. The
text is the CSV as it is, decoded (UTF-8, with or without a byte order mark, or UTF-16 with
one) and cleaned; no form feed, since a CSV has no pages. A text is cut at TEXT_MAX_CHARS
characters, and all of them together at EXPORT_MAX_CHARS, so that the answer fits the 4 MiB
the worker reads (four bytes a character at most); a cut one says `truncated`.

What comes back for a file that was not read, `{"ok": false, "failure": "file", "code": ...}`:

    not_export  a ZIP that holds none of FILES at its root (the worker asked docx.py first, so
                it is neither a Word document nor an export)
    not_utf8    a known file that is not text in UTF-8: nothing of the export is kept
    no_text     the known files are all empty
    bomb        more text than EXPORT_MAX_CHARS leaves room for (a known file would be left
                out whole), the memory of the child, and every `bomb` of zipcaps.py
    too_slow    the budget was spent before the files were read
    unreadable, nested_archive, protected   as zipcaps.py says

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import codecs
import hashlib
import re
import unicodedata

import zipcaps

TEXT_MAX_CHARS = 500_000  # ingestion_documents.text
EXPORT_MAX_CHARS = 900_000  # every text of one answer: under 4 MiB at four bytes a character

# The files of an export that describe the member's own profile, by the names LinkedIn gives
# them. parser_client.LINKEDIN_FILES is the same list (a test keeps the two in step).
FILES = (
    "Profile.csv",
    "Profile Summary.csv",
    "Positions.csv",
    "Education.csv",
    "Skills.csv",
    "Languages.csv",
    "Certifications.csv",
    "Courses.csv",
    "Projects.csv",
    "Honors.csv",
    "Publications.csv",
    "Patents.csv",
    "Organizations.csv",
    "Volunteering.csv",
    "Test Scores.csv",
)

_LINE_BREAKS = re.compile("\r\n?|[\x0b\x0c\x85\u2028\u2029]")
# Control characters but the tab and the line feed, and the two things Postgres does not store:
# U+0000 (in the first range) and half a surrogate pair.
_DROPPED = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f\ud800-\udfff]")


class _NotText(Exception):  # noqa: N818, an outcome, not an error of this program
    """A known file that is not text."""


def failure(code: str) -> dict:
    return {"ok": False, "failure": "file", "code": code}


def decode(content: bytes) -> str:
    """`content` as text: UTF-8 (a byte order mark accepted), or UTF-16 with its mark. Raises
    _NotText for anything else, and for a NUL, which no text holds."""
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as error:
        raise _NotText from error
    if "\x00" in text:
        raise _NotText
    return text


def clean(text: str) -> str:
    """Text as it is stored: NFC, every line break a line feed (a form feed too), no control
    character, no blank at the end of a line. The lines stay as they are: they are rows."""
    text = unicodedata.normalize("NFC", _DROPPED.sub("", _LINE_BREAKS.sub("\n", text)))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def known(archive: zipcaps.Archive) -> list[tuple[str, str]]:
    """(name of FILES, name in the archive) for each known file at the root, in the order of
    FILES. A name written twice with another case counts once, the first one."""
    at_root = {}
    for name in archive.names:
        if "/" not in name:
            at_root.setdefault(name.lower(), name)
    return [(file, at_root[file.lower()]) for file in FILES if file.lower() in at_root]


def handle(data: bytes, options: dict, budget) -> dict:
    """Read `data` as a LinkedIn export. `options` is not used: this reader takes none."""
    try:
        archive = zipcaps.Archive(data)
        found = known(archive)
        if not found:
            return failure("not_export")
        room = EXPORT_MAX_CHARS
        documents = []
        seen: set[str] = set()
        for file, name in found:
            if budget.spent():
                return failure("too_slow")
            content = archive.read(name)
            text = clean(decode(content))
            digest = hashlib.sha256(content).hexdigest()
            # The same bytes under two names are one document.
            if not text or digest in seen:
                continue
            seen.add(digest)
            if room <= 0:
                return failure("bomb")
            kept = text[: min(TEXT_MAX_CHARS, room)]
            room -= len(kept)
            documents.append(
                {
                    "name": file,
                    "text": kept,
                    "sha256": digest,
                    "size": len(content),
                    "truncated": len(kept) < len(text),
                }
            )
    except zipcaps.Refused as refused:
        return failure(refused.code)
    except _NotText:
        return failure("not_utf8")
    except MemoryError:
        return failure("bomb")
    if not documents:
        return failure("no_text")
    return {"ok": True, "kind": "linkedin_zip", "documents": documents}
