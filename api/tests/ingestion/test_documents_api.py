"""/api/ingestion/documents against a real Postgres: adding a file of every kind and a pasted
text, the refusals, dedupe and the two replacement rules, the limits, listing, the text preview,
the secret warning, deletion, and what never reaches a log."""

import asyncio
import codecs
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import CSRF_REFUSED, UNAUTHORIZED
from app.identity.models import User
from app.ingestion import documents, documents_router
from app.ingestion.models import (
    FILE_MAX_BYTES,
    TEXT_MAX_CHARS,
    FailedKind,
    IngestionConsent,
    IngestionDocument,
    IngestionImage,
    TextSource,
    TextStatus,
)
from tests.identity.api_helpers import Api, Clock
from tests.ingestion.conftest import ALICE, BOB, RecordingQueue
from tests.ingestion.helpers import (
    DOSSIER,
    MIB,
    URL,
    add_image,
    archive,
    confirm,
    count,
    delete,
    jpeg,
    listing,
    paste,
    pdf,
    png,
    row,
    rows,
    secret_like,
    set_columns,
    text_of,
    upload,
    uploaded,
    user_id,
)

# --- a text file: complete at once ---------------------------------------------------------


def test_a_text_file_is_read_at_once_and_its_bytes_are_never_stored(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    response = upload(api, alice, DOSSIER.encode(), "dossier.md")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {
        "id": body["id"],
        "name": "dossier.md",
        "kind": "text",
        "mime": "text/plain",
        "size": len(DOSSIER.encode()),
        "status": "done",
        "source": "parsed",
        "readable": True,
        "note": None,
        "truncated": False,
        "pages": None,
        "pages_text": None,
        "pages_image": None,
        "failed_reason": None,
        "secret_warning": False,
        "secret_confirmed_at": None,
        "created_at": body["created_at"],
        "duplicate": False,
        "refused": None,
    }
    stored = row(factory, body["id"])
    assert stored.user_id == user_id(factory, ALICE)
    assert stored.text == DOSSIER
    assert stored.raw is None
    assert stored.created_at == clock.now
    assert (stored.parser_version, stored.attempts) == (documents.PARSER_VERSION, 0)
    assert stored.text_source is TextSource.parsed


@pytest.mark.parametrize(
    ("data", "text"),
    [
        (codecs.BOM_UTF8 + b"with a BOM\n", "with a BOM\n"),
        (codecs.BOM_UTF16_LE + "R\u00e9sum\u00e9".encode("utf-16-le"), "R\u00e9sum\u00e9"),
        (codecs.BOM_UTF16_BE + "R\u00e9sum\u00e9".encode("utf-16-be"), "R\u00e9sum\u00e9"),
        (b"line one\r\nline two\rline three\n", "line one\nline two\nline three\n"),
        (b"bell\x07 and form\x0cfeed", "bell and form\nfeed"),
        (
            "e\u0301 \u65e5\u672c \U0001f600 \u0645\u0631\u062d\u0628\u0627".encode(),
            "\u00e9 \u65e5\u672c \U0001f600 \u0645\u0631\u062d\u0628\u0627",
        ),
        (b'{"name": "Robin Example", "years": 4}', '{"name": "Robin Example", "years": 4}'),
        (b"first,last\nRobin,Example\n", "first,last\nRobin,Example\n"),
        (b"<script>alert(1)</script>", "<script>alert(1)</script>"),
    ],
)
def test_text_is_decoded_and_cleaned(
    api: Api, alice: str, factory: sessionmaker[Session], data: bytes, text: str
) -> None:
    body = uploaded(api, alice, data)
    assert (body["kind"], body["status"]) == ("text", "done")
    assert row(factory, body["id"]).text == text
    assert body["size"] == len(data)


def test_the_name_never_decides_the_type(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    picture = uploaded(api, alice, png(), "notes.txt")
    assert (picture["kind"], picture["mime"], picture["status"]) == (
        "image",
        "image/png",
        "pending",
    )
    words = uploaded(api, alice, b"only words, whatever the name says", "scan.pdf")
    assert (words["kind"], words["mime"], words["status"]) == ("text", "text/plain", "done")
    # Nor does a declared Content-Type.
    declared = upload(api, alice, pdf(), "x", headers={"Content-Type": "text/plain"})
    assert declared.json()["kind"] == "pdf"


# --- binary kinds: kept, waiting for their reader ------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind", "mime", "source", "readable"),
    [
        # Since G2b a PDF has a reader: it waits for its job, not for a later version.
        (pdf(), "pdf", "application/pdf", "parsed", True),
        # Since G7 an archive too: the parser tells a Word document from an export.
        (archive(), "archive", "application/zip", "parsed", True),
        (png(), "image", "image/png", "transcribed", False),
        (jpeg(), "image", "image/jpeg", "transcribed", False),
    ],
)
def test_a_binary_kind_is_kept_whole_and_waits_for_a_reader(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    jobs_queue: RecordingQueue,
    data: bytes,
    kind: str,
    mime: str,
    source: str,
    readable: bool,
) -> None:
    body = uploaded(api, alice, data, "file.bin")
    assert (body["kind"], body["mime"], body["source"]) == (kind, mime, source)
    assert (body["status"], body["readable"]) == ("pending", readable)
    # A reading is queued for the kind that has a reader, and for no other.
    assert [job["args"] for job in jobs_queue.jobs] == ([(body["id"],)] if readable else [])
    assert body["size"] == len(data)
    stored = row(factory, body["id"])
    assert stored.raw == data
    assert stored.text is None
    assert not stored.secret_warning
    # No text yet: the preview says so, without a 500.
    response = text_of(api, alice, body["id"])
    assert response.status_code == 409
    assert response.json() == {"detail": documents_router.NO_TEXT_YET}
    assert response.headers["cache-control"] == "no-store"


def test_nothing_is_parsed_here(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    """A PDF that is no PDF beyond its first five bytes is kept all the same: only a parser,
    in its own service, ever opens a file."""
    body = uploaded(api, alice, b"%PDF-" + b"\xff\x00garbage", "broken.pdf")
    assert body["status"] == "pending"
    assert row(factory, body["id"]).raw == b"%PDF-" + b"\xff\x00garbage"


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "rule"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "legacy-office"),
        (b"{\\rtf1\\ansi A letter}", "rtf"),
        (archive(b"mimetype", b"application/vnd.oasis.opendocument.text"), "opendocument"),
        (b"GIF89a" + b"\x00" * 32, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16, "heic"),
        (b"\x7fELF" + b"\x00" * 32, "program"),
        (b"\x00" * 4096, "unsupported"),
        (b"\xc3\x28 broken utf-8", "unsupported"),
        (b"\x1f\x8b\x08\x00" + b"\x00" * 16, "unsupported"),
        ("plain".encode("utf-16-le"), "unsupported"),
    ],
)
def test_a_type_that_is_not_read_is_refused_with_415_and_one_sentence(
    api: Api, alice: str, factory: sessionmaker[Session], data: bytes, rule: str
) -> None:
    response = upload(api, alice, data, "upload.bin")
    assert response.status_code == 415
    assert response.json() == {"detail": documents.REFUSED[rule]}
    assert response.headers["cache-control"] == "no-store"
    assert rows(factory) == []


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"", documents.EMPTY),
        (b" \n\t\r\n ", documents.NO_TEXT),
        (codecs.BOM_UTF8, documents.NO_TEXT),
        (b"\x07\x1b\x7f", documents.NO_TEXT),
    ],
)
def test_an_empty_file_and_a_file_without_text_are_422(
    api: Api, alice: str, factory: sessionmaker[Session], data: bytes, detail: str
) -> None:
    response = upload(api, alice, data)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert rows(factory) == []


# --- the size limit ------------------------------------------------------------------------


def test_twenty_mebibytes_exactly_is_kept(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    data = pdf(FILE_MAX_BYTES, fill=b"\x5a")
    body = uploaded(api, alice, data, "big.pdf")
    assert body["size"] == FILE_MAX_BYTES == 20 * MIB
    with factory() as db:
        stored, digest = db.execute(
            sa.select(
                sa.func.octet_length(IngestionDocument.raw), sa.func.sha256(IngestionDocument.raw)
            )
        ).one()
        mine = db.execute(sa.select(IngestionDocument.sha256)).scalar_one()
    assert stored == FILE_MAX_BYTES
    assert bytes(digest) == bytes(mine)  # what arrived in Postgres is what was hashed


def test_one_byte_more_is_refused_before_it_is_read(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    response = upload(api, alice, pdf(FILE_MAX_BYTES + 1), "too-big.pdf")
    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than 20 MB."}
    assert response.headers["connection"] == "close"
    assert rows(factory) == []


def test_the_limit_holds_without_a_content_length(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    """A body sent as a stream declares no length: the count while reading is the limit."""
    exactly = upload(api, alice, iter([pdf(FILE_MAX_BYTES)]), "stream.pdf")
    assert "content-length" not in exactly.request.headers
    assert exactly.status_code == 201
    over = upload(api, alice, iter([pdf(FILE_MAX_BYTES), b"!"]), "stream-over.pdf")
    assert "content-length" not in over.request.headers
    assert over.status_code == 413
    assert over.json() == {"detail": documents.TOO_LARGE}
    assert len(rows(factory)) == 1


def test_a_content_length_that_is_not_a_number_is_ignored(api: Api, alice: str) -> None:
    response = upload(api, alice, b"short text", headers={"Content-Length": "many"})
    assert response.status_code == 201


def test_a_long_text_file_is_cut_at_the_limit_and_says_so(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, ("\u00e9" * (TEXT_MAX_CHARS + 1)).encode(), "long.txt")
    assert (body["status"], body["truncated"]) == ("done", True)
    assert body["note"] == "Only the first 500,000 characters were read."
    assert body["size"] == 2 * (TEXT_MAX_CHARS + 1)
    with factory() as db:
        assert db.execute(sa.select(sa.func.char_length(IngestionDocument.text))).scalar_one() == (
            TEXT_MAX_CHARS
        )
    exact = uploaded(api, alice, b"a" * TEXT_MAX_CHARS, "exact.txt")
    assert (exact["truncated"], exact["note"]) == (False, None)


def test_a_twenty_mebibyte_text_file_keeps_its_first_characters_only(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, b"word " * (FILE_MAX_BYTES // 5), "huge.txt")
    assert (body["status"], body["truncated"], body["size"]) == ("done", True, FILE_MAX_BYTES)
    stored = row(factory, body["id"])
    assert stored.raw is None
    assert len(stored.text or "") == TEXT_MAX_CHARS


# --- pasted text ---------------------------------------------------------------------------


def test_a_pasted_text_is_a_document_at_once(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    response = paste(api, alice, "Robin Example\r\nApplied AI engineer\n")
    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["kind"], body["source"]) == ("Pasted text", "text", "pasted")
    assert (body["status"], body["readable"], body["duplicate"]) == ("done", True, False)
    assert body["size"] == len(b"Robin Example\r\nApplied AI engineer\n")
    stored = row(factory, body["id"])
    assert stored.text == "Robin Example\nApplied AI engineer\n"
    assert stored.raw is None
    assert stored.text_source is TextSource.pasted


def test_a_paste_may_be_named_and_the_name_is_cleaned(api: Api, alice: str) -> None:
    assert paste(api, alice, "a", name="../LinkedIn\x00 about ").json()["name"] == "LinkedIn about"
    assert paste(api, alice, "b", name="   ").json()["name"] == "Pasted text"
    assert paste(api, alice, "c", name="x" * 800).json()["name"] == "x" * 200
    too_long = paste(api, alice, "d", name="x" * 801)
    assert too_long.status_code == 422
    assert too_long.json()["detail"].startswith("name: ")


def test_a_paste_of_exactly_the_limit_is_kept_and_one_more_character_is_413(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    at_limit = paste(api, alice, "\U0001f600" * documents.PASTE_MAX_CHARS)
    assert at_limit.status_code == 201  # characters, not bytes or UTF-16 units
    over = paste(api, alice, "a" * (documents.PASTE_MAX_CHARS + 1))
    assert over.status_code == 413
    assert over.json() == {
        "detail": "The pasted text is longer than 100,000 characters. "
        "Save it as a text file and add the file instead."
    }
    assert len(rows(factory)) == 1


def test_a_paste_body_over_two_mebibytes_is_refused_while_it_is_read(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    huge = json.dumps({"text": "a" * (documents_router.PASTE_BODY_MAX_BYTES + 1)}).encode()
    declared = api.post(f"{URL}/text", token=alice, content=huge)
    assert declared.status_code == 413
    streamed = api.post(f"{URL}/text", token=alice, content=iter([huge]))
    assert "content-length" not in streamed.request.headers
    assert streamed.status_code == 413
    assert streamed.json() == {"detail": documents.PASTE_TOO_LONG}
    # Caddy lets 10 MiB through on this route and may still be sending it: the connection is
    # kept, or Caddy would answer 502 in place of this sentence (the journey checks it).
    assert "connection" not in declared.headers
    assert "connection" not in streamed.headers
    assert rows(factory) == []


@pytest.mark.parametrize("text", ["", "   ", "\n\t\r\n", "\x00\x07", "\ufeff"])
def test_a_paste_with_nothing_in_it_is_422(
    api: Api, alice: str, factory: sessionmaker[Session], text: str
) -> None:
    response = paste(api, alice, text)
    assert response.status_code == 422
    assert response.json() == {"detail": documents.NO_TEXT_PASTED}
    assert rows(factory) == []


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        (b"not json", documents_router.BAD_JSON),
        (b"", documents_router.BAD_JSON),
        (b'{"text": "a"', documents_router.BAD_JSON),
        (b"[]", "Invalid request body: Input should be an object"),
        (b"{}", "text: Field required"),
        (b'{"text": 5}', "text: Input should be a valid string"),
        (b'{"text": null}', "text: Input should be a valid string"),
        (b'{"text": "a", "user_id": "x"}', "user_id: Extra inputs are not permitted"),
        (b'{"text": "a", "name": 3}', "name: Input should be a valid string"),
    ],
)
def test_a_malformed_paste_is_422_with_one_sentence(
    api: Api, alice: str, factory: sessionmaker[Session], content: bytes, detail: str
) -> None:
    response = api.post(f"{URL}/text", token=alice, content=content)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert rows(factory) == []


def test_half_a_surrogate_pair_never_reaches_the_database(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    response = api.post(f"{URL}/text", token=alice, content=b'{"text": "broken \\ud83d pair"}')
    assert response.status_code == 422
    assert rows(factory) == []
    # The service refuses it too, whoever calls it.
    with factory() as db, pytest.raises(documents.DocumentError) as refused:
        documents.store_paste(
            db, user_id(factory, ALICE), None, "broken \ud83d pair", datetime.now(UTC)
        )
    assert (refused.value.status, refused.value.message) == (422, documents.BAD_TEXT)


# --- names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("CV Robin Example 2026.pdf", "CV Robin Example 2026.pdf"),
        ("r\u00e9sum\u00e9 \u65e5\u672c\u8a9e.md", "r\u00e9sum\u00e9 \u65e5\u672c\u8a9e.md"),
        (
            "\u0633\u064a\u0631\u0629 \u0630\u0627\u062a\u064a\u0629.txt",
            "\u0633\u064a\u0631\u0629 \u0630\u0627\u062a\u064a\u0629.txt",
        ),
        ("notes \U0001f600\U0001f680.md", "notes \U0001f600\U0001f680.md"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("a\x00b\nc\td.txt", "abcd.txt"),
        ("x" * 1000 + ".txt", "x" * 200),
        ("/", "Untitled"),
        ("  ", "Untitled"),
    ],
)
def test_the_name_is_a_display_name_and_nothing_else(
    api: Api, alice: str, factory: sessionmaker[Session], name: str, stored: str
) -> None:
    body = uploaded(api, alice, b"some text", name)
    assert body["name"] == stored
    assert row(factory, body["id"]).name == stored


@pytest.mark.parametrize(
    "header",
    [None, "", "UTF-8''", "UTF-8''%FF%FE", "UTF-8''%2F%2E%2E"],
)
def test_a_missing_or_malformed_name_is_untitled_never_an_error(
    api: Api, alice: str, header: str | None
) -> None:
    headers = {} if header is None else {"X-Filename": header}
    response = upload(api, alice, b"some text " + repr(header).encode(), None, headers=headers)
    assert response.status_code == 201
    assert response.json()["name"] == "Untitled"


def test_a_plain_header_without_the_rfc_5987_prefix_is_read_as_it_is(api: Api, alice: str) -> None:
    response = upload(api, alice, b"some text", None, headers={"X-Filename": "plain notes.md"})
    assert response.json()["name"] == "plain notes.md"
    # A client that puts the UTF-8 bytes of the name in the header without encoding them.
    raw: Any = {"X-Filename": "caf\u00e9.txt".encode()}
    assert upload(api, alice, b"more text", None, headers=raw).json()["name"] == "caf\u00e9.txt"


# --- dedupe and replacement ----------------------------------------------------------------


def test_the_same_file_twice_is_one_document(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    first = uploaded(api, alice, DOSSIER.encode(), "dossier.md")
    again = upload(api, alice, DOSSIER.encode(), "another name.md")
    assert again.status_code == 200
    assert again.headers["cache-control"] == "no-store"
    assert again.json() == {**first, "duplicate": True}  # the first name stays
    assert len(rows(factory)) == 1


def test_the_same_binary_file_twice_is_one_document(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    first = uploaded(api, alice, pdf(), "cv.pdf")
    again = upload(api, alice, pdf(), "cv (1).pdf")
    assert (again.status_code, again.json()["id"], again.json()["duplicate"]) == (
        200,
        first["id"],
        True,
    )
    assert [doc.raw for doc in rows(factory)] == [pdf()]


def test_a_paste_of_the_bytes_of_a_file_is_that_file(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    first = uploaded(api, alice, b"the very same words", "words.txt")
    pasted = paste(api, alice, "the very same words")
    assert (pasted.status_code, pasted.json()["id"]) == (200, first["id"])
    assert pasted.json()["source"] == "parsed"
    twice = paste(api, alice, "another text")
    assert paste(api, alice, "another text").json() == {**twice.json(), "duplicate": True}
    assert len(rows(factory)) == 2


def test_two_users_may_hold_the_same_file_and_neither_learns_it(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    hers = uploaded(api, alice, DOSSIER.encode(), "hers.md")
    his = upload(api, bob, DOSSIER.encode(), "his.md")
    assert his.status_code == 201  # not 200: nothing says the file already exists somewhere
    assert his.json()["duplicate"] is False
    assert his.json()["id"] != hers["id"]
    assert his.json()["name"] == "his.md"
    stored = rows(factory)
    assert {doc.user_id for doc in stored} == {user_id(factory, ALICE), user_id(factory, BOB)}
    assert stored[0].sha256 == stored[1].sha256


def test_an_upload_that_matches_a_failed_document_takes_its_row_over(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    first = uploaded(api, alice, pdf(), "cv.pdf")
    assert row(factory, first["id"]).pending_since == clock.now
    clock.advance(hours=2)
    set_columns(
        factory,
        first["id"],
        text_status=TextStatus.failed,
        failed_reason="This PDF is protected. Remove the protection and export it again.",
        failed_kind=FailedKind.file,
        attempts=2,
        raw=None,
        note="left over",
        pages=3,
        pages_text=1,
        pages_image=2,
        transcribed_pages=[1, 2],
        secret_warning=True,
    )
    add_image(factory, first["id"])
    again = upload(api, alice, pdf(), "cv fixed.pdf")
    assert again.status_code == 201
    body = again.json()
    assert (body["id"], body["duplicate"], body["name"]) == (first["id"], False, "cv fixed.pdf")
    assert (body["status"], body["failed_reason"], body["note"]) == ("pending", None, None)
    assert (body["pages"], body["pages_text"], body["pages_image"]) == (None, None, None)
    assert body["created_at"] == first["created_at"]
    stored = row(factory, first["id"])
    # The row keeps the day it was first added; since when it waits this time is told apart.
    assert stored.pending_since == clock.now
    assert stored.pending_since > stored.created_at
    assert stored.transcribed_pages is None
    assert stored.raw == pdf()
    assert (stored.attempts, stored.failed_kind, stored.secret_warning) == (0, None, False)
    assert count(factory, IngestionImage) == 0
    assert len(rows(factory)) == 1


def test_a_done_document_with_a_note_is_read_again_only_by_a_newer_parser(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = uploaded(api, alice, pdf(), "scan.pdf")
    set_columns(
        factory,
        first["id"],
        text_status=TextStatus.done,
        text="page one\x0c",
        raw=None,
        note="1 page is a scan and will be read in a later version.",
        pages=2,
        pages_text=1,
        pages_image=1,
    )
    # The parser that left the note is the current one: the document is returned as it is.
    same = upload(api, alice, pdf(), "scan.pdf")
    assert (same.status_code, same.json()["status"], same.json()["duplicate"]) == (
        200,
        "done",
        True,
    )
    assert row(factory, first["id"]).text == "page one\x0c"
    # A newer parser: the same upload starts the row over.
    monkeypatch.setattr(documents, "PARSER_VERSION", documents.PARSER_VERSION + 1)
    newer = upload(api, alice, pdf(), "scan.pdf")
    assert (newer.status_code, newer.json()["id"]) == (201, first["id"])
    assert (newer.json()["status"], newer.json()["note"]) == ("pending", None)
    stored = row(factory, first["id"])
    assert (stored.raw, stored.text, stored.parser_version) == (pdf(), None, 2)


def test_a_done_document_without_a_note_is_never_replaced(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = uploaded(api, alice, b"a text read in full")
    monkeypatch.setattr(documents, "PARSER_VERSION", documents.PARSER_VERSION + 1)
    assert upload(api, alice, b"a text read in full").status_code == 200
    assert row(factory, first["id"]).parser_version == 1


def test_a_failed_text_document_is_replaced_by_its_text(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    first = uploaded(api, alice, b"a text that a later task marked failed")
    set_columns(factory, first["id"], text_status=TextStatus.failed, failed_reason="x", text=None)
    again = upload(api, alice, b"a text that a later task marked failed")
    assert (again.status_code, again.json()["status"]) == (201, "done")
    assert row(factory, first["id"]).text == "a text that a later task marked failed"


def test_the_unique_constraint_has_the_last_word(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever wrote the row meanwhile (a path that does not take the user's lock): the insert
    hits the constraint, and the upload answers with the row that is there."""
    first = uploaded(api, alice, pdf(), "cv.pdf")
    real = documents._by_hash
    calls = {"n": 0}

    def sometimes_blind(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] <= 2 else real(*args, **kwargs)

    monkeypatch.setattr(documents, "_by_hash", sometimes_blind)
    again = upload(api, alice, pdf(), "cv.pdf")
    assert (again.status_code, again.json()["id"], again.json()["duplicate"]) == (
        200,
        first["id"],
        True,
    )
    assert calls["n"] == 3
    assert len(rows(factory)) == 1


def test_another_integrity_error_is_not_swallowed(
    factory: sessionmaker[Session], api: Api, alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(db: Session, user: uuid.UUID, **kwargs: Any) -> Any:
        db.add(IngestionDocument(user_id=uuid.uuid4(), sha256=b"x" * 32))  # no such user
        db.flush()

    monkeypatch.setattr(documents, "_store", broken)
    with factory() as db, pytest.raises(IntegrityError):
        documents.store_paste(db, user_id(factory, ALICE), None, "text", datetime.now(UTC))


# --- the user's limits ---------------------------------------------------------------------


def test_the_quota_counts_bytes_text_and_page_images(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    assert listing(api, alice)["usage"] == {"bytes": 0, "documents": 0}
    binary = uploaded(api, alice, pdf(5000), "a.pdf")
    uploaded(api, alice, "h\u00e9llo".encode(), "b.txt")  # 6 bytes of text
    assert listing(api, alice)["usage"] == {"bytes": 5006, "documents": 2}
    add_image(factory, binary["id"])  # a 1,000 byte page image
    with factory() as db, db.begin():
        db.execute(sa.update(IngestionImage).values(text="abc"))
    assert listing(api, alice)["usage"] == {"bytes": 6009, "documents": 2}
    assert listing(api, bob)["usage"] == {"bytes": 0, "documents": 0}
    assert listing(api, alice)["limits"] == {
        "file_bytes": 20 * MIB,
        "paste_characters": 100_000,
        "text_characters": 500_000,
        "user_bytes": 100 * MIB,
        "documents": 200,
    }


def test_an_upload_that_would_pass_the_quota_is_413(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    uploaded(api, alice, pdf(2000), "a.pdf")
    over = upload(api, alice, pdf(1001, fill=b"\x01"), "b.pdf")
    assert over.status_code == 413
    assert over.json() == {
        "detail": "Your documents would take more than the 100 MB you can keep here. "
        "Delete one to make room."
    }
    assert upload(api, alice, b"t" * 1001, "c.txt").status_code == 413
    assert paste(api, alice, "p" * 1001).status_code == 413
    assert len(rows(factory)) == 1
    # Exactly the quota is allowed, the same file again is still answered, and another user
    # has a quota of their own.
    assert upload(api, alice, pdf(1000, fill=b"\x02"), "fits.pdf").status_code == 201
    assert upload(api, alice, pdf(2000), "a again.pdf").status_code == 200
    assert upload(api, bob, pdf(3000), "his.pdf").status_code == 201


def test_deleting_makes_room(api: Api, alice: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    first = uploaded(api, alice, pdf(3000), "a.pdf")
    assert upload(api, alice, b"more", "b.txt").status_code == 413
    assert delete(api, alice, first["id"]).status_code == 204
    assert upload(api, alice, b"more", "b.txt").status_code == 201


def test_a_replacement_is_counted_without_what_it_replaces(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    first = uploaded(api, alice, pdf(2500), "a.pdf")
    set_columns(factory, first["id"], text_status=TextStatus.failed, failed_reason="x")
    # The failed row still holds its 2,500 bytes; the new upload replaces them, it does not add.
    assert upload(api, alice, pdf(2500), "a.pdf").status_code == 201
    assert listing(api, alice)["usage"] == {"bytes": 2500, "documents": 1}


def test_a_replacement_that_does_not_fit_leaves_the_failed_row_as_it_was(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = uploaded(api, alice, pdf(2500), "a.pdf")
    set_columns(factory, first["id"], text_status=TextStatus.failed, failed_reason="why", raw=None)
    add_image(factory, first["id"])
    uploaded(api, alice, b"t" * 2000, "other.txt")
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    assert upload(api, alice, pdf(2500), "a.pdf").status_code == 413
    stored = row(factory, first["id"])
    assert (stored.text_status, stored.failed_reason) == (TextStatus.failed, "why")
    assert count(factory, IngestionImage) == 1  # the refusal rolled the whole attempt back


def test_the_201st_document_is_422(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "USER_MAX_DOCUMENTS", 3)
    for n in range(3):
        uploaded(api, alice, f"document {n}".encode(), f"{n}.txt")
    over = upload(api, alice, b"one too many", "4.txt")
    assert over.status_code == 422
    assert over.json() == {
        "detail": "You already keep 200 documents, the most allowed. Delete one to add another."
    }
    assert paste(api, alice, "a paste too many").status_code == 422
    assert upload(api, alice, pdf(), "4.pdf").status_code == 422
    assert upload(api, alice, b"document 0", "0 again.txt").status_code == 200  # still answered
    assert upload(api, bob, b"his first", "1.txt").status_code == 201
    assert len(rows(factory)) == 4


# --- listing -------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_carries_no_content(
    api: Api, alice: str, clock: Clock
) -> None:
    first = uploaded(api, alice, b"first text", "1.txt")
    clock.advance(seconds=5)
    second = uploaded(api, alice, pdf(), "2.pdf")
    clock.advance(seconds=5)
    third = paste(api, alice, "third text").json()
    response = api.get(URL, token=alice)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [item["id"] for item in body["items"]] == [third["id"], second["id"], first["id"]]
    for item, stored in zip(body["items"], (third, second, first), strict=True):
        assert item == {
            key: value for key, value in stored.items() if key not in ("duplicate", "refused")
        }
        assert "text" not in item
        assert "bytes" not in item
        assert "sha256" not in item


def test_the_list_never_loads_bytes_or_text(
    api: Api, alice: str, factory: sessionmaker[Session], engine: sa.Engine
) -> None:
    uploaded(api, alice, pdf(4000), "a.pdf")
    uploaded(api, alice, b"some text", "b.txt")
    statements: list[str] = []
    owner = user_id(factory, ALICE)
    with factory() as db:
        sa.event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
        found = documents.list_documents(db, owner)
        assert len(found) == 2
    (select,) = statements
    assert "ingestion_documents.bytes" not in select
    assert "ingestion_documents.text " not in select
    assert "ingestion_documents.text," not in select


def test_what_a_later_parser_writes_is_shown(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, pdf(), "scan.pdf")
    set_columns(
        factory,
        body["id"],
        text_status=TextStatus.awaiting_transcription,
        text="page one\x0c\x0c",
        raw=None,
        note="Pages 31 to 44 were not read.",
        truncated=True,
        pages=44,
        pages_text=12,
        pages_image=3,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    (item,) = listing(api, alice)["items"]
    assert (item["status"], item["note"]) == (
        "awaiting_transcription",
        "Pages 31 to 44 were not read.",
    )
    assert (item["pages"], item["pages_text"], item["pages_image"]) == (44, 12, 3)
    assert (item["truncated"], item["kind"]) == (True, "archive")
    set_columns(
        factory,
        body["id"],
        text_status=TextStatus.failed,
        failed_reason="This PDF is protected. Remove the protection and export it again.",
        failed_kind=FailedKind.file,
    )
    (item,) = listing(api, alice)["items"]
    assert (
        item["failed_reason"] == "This PDF is protected. Remove the protection and export it again."
    )


# --- the text preview ----------------------------------------------------------------------


def test_the_text_is_read_back_a_page_at_a_time(api: Api, alice: str) -> None:
    text = "".join(f"line {n:05d} \u00e9\U0001f600\n" for n in range(1000))
    body = uploaded(api, alice, text.encode(), "long.txt")
    first = text_of(api, alice, body["id"], limit=1000)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "text": text[:1000],
        "offset": 0,
        "next_offset": 1000,
        "total": len(text),
    }
    # Offsets count characters, astral ones included, so the pages join exactly.
    pieces, offset = [], 0
    while offset is not None:
        page = text_of(api, alice, body["id"], offset=offset, limit=3333).json()
        pieces.append(page["text"])
        offset = page["next_offset"]
    assert "".join(pieces) == text
    last = text_of(api, alice, body["id"], offset=len(text) - 5, limit=1000).json()
    assert (last["text"], last["next_offset"]) == (text[-5:], None)
    beyond = text_of(api, alice, body["id"], offset=len(text) + 10).json()
    assert beyond == {"text": "", "offset": len(text) + 10, "next_offset": None, "total": len(text)}
    whole = text_of(api, alice, body["id"]).json()  # the default page holds this text whole
    assert (whole["text"], whole["next_offset"]) == (text, None)


def test_the_form_feeds_between_the_pages_of_a_pdf_are_part_of_the_text(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, pdf(), "two pages.pdf")
    set_columns(factory, body["id"], text_status=TextStatus.done, text="one\x0c\x0cthree", raw=None)
    assert text_of(api, alice, body["id"]).json()["text"] == "one\x0c\x0cthree"
    assert text_of(api, alice, body["id"], offset=4, limit=1).json() == {
        "text": "\x0c",
        "offset": 4,
        "next_offset": 5,
        "total": 10,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"offset": -1},
        {"offset": TEXT_MAX_CHARS + 1},
        {"offset": "x"},
        {"limit": 0},
        {"limit": documents_router.PREVIEW_MAX + 1},
        {"limit": "all"},
    ],
)
def test_a_page_outside_the_bounds_is_422(api: Api, alice: str, params: dict[str, Any]) -> None:
    body = uploaded(api, alice, b"some text")
    response = text_of(api, alice, body["id"], **params)
    assert response.status_code == 422
    assert "some text" not in response.text


# --- the secret warning --------------------------------------------------------------------


def test_a_text_that_looks_like_it_holds_a_key_carries_a_warning_until_confirmed(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    text = f"export AWS_ACCESS_KEY_ID={secret_like()}\nnotes about the project\n"
    body = uploaded(api, alice, text.encode(), "notes.env.txt")
    assert (body["status"], body["secret_warning"], body["secret_confirmed_at"]) == (
        "done",
        True,
        None,
    )
    clock.advance(minutes=3)
    confirmed = confirm(api, alice, body["id"])
    assert confirmed.status_code == 200
    assert confirmed.headers["cache-control"] == "no-store"
    assert confirmed.json() == {
        **{key: value for key, value in body.items() if key not in ("duplicate", "refused")},
        "secret_confirmed_at": clock.now.isoformat().replace("+00:00", "Z"),
    }
    # Idempotent: the first date stays.
    clock.advance(minutes=3)
    assert confirm(api, alice, body["id"]).json() == confirmed.json()
    stored = row(factory, body["id"])
    assert stored.secret_warning
    assert stored.secret_confirmed_at == clock.now - timedelta(minutes=3)
    assert stored.text == text  # the text itself is kept as it was written


def test_a_pasted_secret_is_warned_about_too(api: Api, alice: str) -> None:
    body = paste(api, alice, f"my token is ghp_{'aB3dE5gH7jK9' * 3}").json()
    assert body["secret_warning"] is True
    assert paste(api, alice, "a text with nothing to hide").json()["secret_warning"] is False


def test_confirming_a_document_without_a_warning_changes_nothing(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, b"nothing to hide")
    response = confirm(api, alice, body["id"])
    assert response.status_code == 200
    assert response.json()["secret_confirmed_at"] is None
    assert row(factory, body["id"]).secret_confirmed_at is None


def test_a_replacement_starts_without_the_old_confirmation(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    text = f"key = {secret_like()}".encode()
    body = uploaded(api, alice, text)
    confirm(api, alice, body["id"])
    set_columns(factory, body["id"], text_status=TextStatus.failed, failed_reason="x")
    again = upload(api, alice, text).json()
    assert (again["secret_warning"], again["secret_confirmed_at"]) == (True, None)


# --- deletion ------------------------------------------------------------------------------


def test_delete_is_a_hard_delete_of_the_row_its_content_and_its_images(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    kept = uploaded(api, alice, b"this one stays", "keep.txt")
    gone = uploaded(api, alice, pdf(9000), "gone.pdf")
    add_image(factory, gone["id"], 0)
    add_image(factory, gone["id"], 1)
    response = delete(api, alice, gone["id"])
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert [str(doc.id) for doc in rows(factory)] == [kept["id"]]
    assert count(factory, IngestionImage) == 0
    assert [item["id"] for item in listing(api, alice)["items"]] == [kept["id"]]
    assert listing(api, alice)["usage"] == {"bytes": len(b"this one stays"), "documents": 1}
    assert text_of(api, alice, gone["id"]).status_code == 404
    with factory() as db:  # no tombstone, no copy: the bytes are in no row any more
        left = db.execute(sa.select(sa.func.count()).where(IngestionDocument.raw.is_not(None)))
        assert left.scalar_one() == 0


def test_deleting_twice_is_204_then_404(api: Api, alice: str) -> None:
    body = uploaded(api, alice, b"some text")
    assert delete(api, alice, body["id"]).status_code == 204
    again = delete(api, alice, body["id"])
    assert again.status_code == 404
    assert again.json() == {"detail": documents_router.NOT_FOUND}
    assert again.headers["cache-control"] == "no-store"


def test_a_deleted_file_can_be_added_again(api: Api, alice: str) -> None:
    first = uploaded(api, alice, pdf(), "cv.pdf")
    delete(api, alice, first["id"])
    again = upload(api, alice, pdf(), "cv.pdf")
    assert again.status_code == 201
    assert again.json()["id"] != first["id"]


def test_a_registered_guard_refuses_the_deletion_with_its_sentence(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, b"read by a run")
    other = uploaded(api, alice, b"not read by any run")
    seen: list[uuid.UUID] = []

    def guard(db: Session, document: IngestionDocument) -> str | None:
        seen.append(document.id)
        if str(document.id) == body["id"]:
            return "This document was read by a run and cannot be deleted yet."
        return None

    documents.register_delete_guard(guard)
    documents.register_delete_guard(guard)  # registered once, whatever is imported twice
    response = delete(api, alice, body["id"])
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This document was read by a run and cannot be deleted yet."
    }
    assert response.headers["cache-control"] == "no-store"
    assert len(rows(factory)) == 2
    assert delete(api, alice, other["id"]).status_code == 204
    assert [str(seen_id) for seen_id in seen] == [body["id"], other["id"]]


def test_deleting_the_account_leaves_nothing_behind(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    uploaded(api, alice, b"some text")
    add_image(factory, body["id"])
    his = uploaded(api, bob, b"his text")
    with factory() as db, db.begin():
        db.add(IngestionConsent(user_id=user_id(factory, ALICE), text_version=1))
    with factory() as db, db.begin():
        db.execute(sa.delete(User).where(User.email == ALICE))
    assert [str(doc.id) for doc in rows(factory)] == [his["id"]]
    assert count(factory, IngestionImage) == 0
    assert count(factory, IngestionConsent) == 0


# --- isolation: another user's document does not exist -------------------------------------


def test_one_user_never_sees_reads_confirms_or_deletes_anothers_document(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    text = f"token = {secret_like()} and private words"
    hers = uploaded(api, alice, text.encode(), "private.txt")
    her_pdf = uploaded(api, alice, pdf(), "private.pdf")
    assert listing(api, bob) == {
        "items": [],
        "usage": {"bytes": 0, "documents": 0},
        "limits": listing(api, alice)["limits"],
    }
    missing = str(uuid.uuid4())
    for document_id in (hers["id"], her_pdf["id"]):
        answers = [
            text_of(api, bob, document_id),
            confirm(api, bob, document_id),
            delete(api, bob, document_id),
        ]
        never_existed = [
            text_of(api, bob, missing),
            confirm(api, bob, missing),
            delete(api, bob, missing),
        ]
        for answer, reference in zip(answers, never_existed, strict=True):
            # The same status, body and headers as for an id that never existed: nothing
            # confirms that the document is there.
            assert answer.status_code == reference.status_code == 404
            assert answer.json() == reference.json() == {"detail": documents_router.NOT_FOUND}
            assert answer.content == reference.content
            # Every header, the request's own id aside: no length, no cache rule, no extra
            # header tells the two apart.
            assert header_set(answer) == header_set(reference)
            assert answer.headers["cache-control"] == "no-store"
            assert "private" not in answer.text
    stored = row(factory, hers["id"])
    assert stored.secret_confirmed_at is None  # his confirm did nothing
    assert len(rows(factory)) == 2  # his delete did nothing
    assert text_of(api, alice, hers["id"]).json()["text"] == text


def header_set(response: Any) -> dict[str, str]:
    """Every header of an answer but the id of its own request."""
    return {
        name: value for name, value in response.headers.items() if name.lower() != "x-request-id"
    }


def test_a_malformed_id_answers_exactly_as_an_unknown_one(api: Api, alice: str) -> None:
    uploaded(api, alice, b"some text")
    unknown = str(uuid.uuid4())
    for call in (text_of, confirm, delete):
        malformed, reference = call(api, alice, "not-a-uuid"), call(api, alice, unknown)
        assert malformed.status_code == reference.status_code == 404
        assert malformed.content == reference.content
        assert header_set(malformed) == header_set(reference)


@pytest.mark.parametrize(
    "document_id",
    ["1", "not-a-uuid", "00000000-0000-0000-0000-00000000000g", "..%2Fdocuments", "%00", "null"],
)
def test_an_id_that_is_not_a_uuid_is_the_same_404(api: Api, alice: str, document_id: str) -> None:
    uploaded(api, alice, b"some text")
    for response in (
        text_of(api, alice, document_id),
        confirm(api, alice, document_id),
        delete(api, alice, document_id),
    ):
        assert response.status_code == 404


def test_the_user_always_comes_from_the_session(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    """Nothing in a request can name another user: not a query parameter, not a header, not a
    field of the body."""
    alice_id = str(user_id(factory, ALICE))
    upload(api, bob, b"his text", headers={"X-User-Id": alice_id}, params={"user_id": alice_id})
    spoofed = api.post(f"{URL}/text", token=bob, json={"text": "x", "user_id": alice_id})
    assert spoofed.status_code == 422
    assert (
        api.get(URL, token=alice, params={"user_id": str(user_id(factory, BOB))}).json()["items"]
        == []
    )
    assert {doc.user_id for doc in rows(factory)} == {user_id(factory, BOB)}


# --- session, CSRF, methods ----------------------------------------------------------------


def test_every_route_needs_a_session(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    body = uploaded(api, alice, b"some text")
    for response in (
        upload(api, None, b"anonymous text"),
        upload(api, "not-a-session", b"anonymous text"),
        paste(api, None, "anonymous text"),
        api.get(URL),
        text_of(api, None, body["id"]),
        confirm(api, None, body["id"]),
        delete(api, None, body["id"]),
    ):
        assert response.status_code == 401
        assert response.json() == {"detail": UNAUTHORIZED}
    assert len(rows(factory)) == 1


def test_an_anonymous_upload_is_refused_before_its_body_counts(api: Api) -> None:
    response = upload(api, None, pdf(FILE_MAX_BYTES + 1))
    assert response.status_code == 401


def test_every_write_needs_the_csrf_header(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, b"some text")
    for response in (
        api.post(URL, token=alice, content=b"forged", csrf=False),
        api.post(f"{URL}/text", token=alice, json={"text": "forged"}, csrf=False),
        confirm(api, alice, body["id"], csrf=False),
        delete(api, alice, body["id"], csrf=False),
    ):
        assert response.status_code == 403
        assert response.json() == {"detail": CSRF_REFUSED}
    assert len(rows(factory)) == 1
    assert api.get(URL, token=alice, csrf=False).status_code == 200
    assert text_of(api, alice, body["id"], csrf=False).status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", ""),
        ("PATCH", ""),
        ("DELETE", ""),
        ("GET", "/text"),
        ("PUT", "/{id}"),
        ("GET", "/{id}"),
        ("POST", "/{id}"),
        ("GET", "/{id}/confirm"),
        ("POST", "/{id}/text"),
    ],
)
def test_other_methods_and_paths_are_not_routes(
    api: Api, alice: str, method: str, path: str
) -> None:
    body = uploaded(api, alice, b"some text")
    response = api.request(method, URL + path.replace("{id}", body["id"]), token=alice)
    assert response.status_code in (404, 405)


def test_both_upload_slots_busy_is_503_with_retry_after(
    app: FastAPI,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents_router, "SLOT_WAIT_S", 0.05)
    gate = documents_router._Gate()
    gate.slots = asyncio.Semaphore(0)
    app.state.ingestion_gate = gate
    response = upload(api, alice, b"some text")
    assert response.status_code == 503
    assert response.json() == {"detail": documents_router.BUSY}
    assert response.headers["retry-after"] == "5"
    assert "connection" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert rows(factory) == []


# --- logs ----------------------------------------------------------------------------------


def test_nothing_of_a_document_reaches_a_log(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    name = f"name-{uuid.uuid4().hex}"
    words = f"words-{uuid.uuid4().hex}"
    secret = secret_like()
    text = f"{words} key={secret}"
    with caplog.at_level("DEBUG"):
        first = uploaded(api, alice, text.encode(), f"{name}.txt")
        upload(api, alice, text.encode(), f"{name}.txt")  # duplicate
        binary = uploaded(api, alice, pdf() + words.encode(), f"{name}.pdf")
        paste(api, alice, f"pasted {text}", name=name)
        upload(api, alice, b"GIF89a" + words.encode(), f"{name}.gif")  # 415
        upload(api, alice, b"", f"{name}.empty")  # 422
        upload(api, alice, pdf(FILE_MAX_BYTES + 1), f"{name}.big")  # 413
        api.post(f"{URL}/text", token=alice, content=f'{{"text": "{words}", "x": 1}}'.encode())
        paste(api, alice, words * 5000)  # 413
        listing(api, alice)
        text_of(api, alice, first["id"])
        text_of(api, alice, binary["id"])  # 409
        text_of(api, bob, first["id"])  # 404
        confirm(api, alice, first["id"])
        set_columns(factory, binary["id"], text_status=TextStatus.failed, failed_reason="x")
        upload(api, alice, pdf() + words.encode(), f"{name}.pdf")  # replaced
        delete(api, alice, first["id"])
        delete(api, alice, first["id"])  # 404
    logged = caplog.text + "".join(
        json.dumps({k: str(v) for k, v in vars(record).items()}) for record in caplog.records
    )
    for private in (name, words, secret, "pasted "):
        assert private not in logged
    events = [record.getMessage() for record in caplog.records if record.name == "app.ingestion"]
    assert events == [
        "document stored",
        "document duplicate",
        "document stored",
        "document stored",
        "document refused",
        "document refused",
        "document refused",
        "document refused",
        "document secret warning confirmed",
        "document replaced",
        "document deleted",
    ]
    stored = next(r for r in caplog.records if r.getMessage() == "document stored")
    assert vars(stored)["document_id"] == first["id"]
    assert vars(stored)["secret_rules"] == ["aws-access-key", "generic-password-or-key"]
    assert (vars(stored)["size"], vars(stored)["mime"], vars(stored)["status"]) == (
        len(text.encode()),
        "text/plain",
        "done",
    )
    refused = [vars(r) for r in caplog.records if r.getMessage() == "document refused"]
    assert [(r["rule"], r["status"]) for r in refused] == [
        ("gif", 415),
        ("empty", 422),
        ("file-too-large", 413),
        ("paste-too-long", 413),
    ]


# --- after the review of pull request #98 --------------------------------------------------


def test_a_text_document_never_waited(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    body = uploaded(api, alice, b"complete at once")
    assert row(factory, body["id"]).pending_since is None
    pasted = paste(api, alice, "pasted, complete at once").json()
    assert row(factory, pasted["id"]).pending_since is None


def test_a_user_at_the_limit_costs_the_database_nothing(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quota and the count are checked before the bytes travel to Postgres: a refused
    upload stages nothing."""
    staged: list[int] = []
    real = documents._stage

    def counting(db: Session, body: bytes | bytearray) -> None:
        staged.append(len(body))
        real(db, body)

    monkeypatch.setattr(documents, "_stage", counting)
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    uploaded(api, alice, pdf(2500), "a.pdf")
    assert staged == [2500]
    assert upload(api, alice, pdf(1000, fill=b"\x01"), "b.pdf").status_code == 413
    assert staged == [2500]
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 10**9)
    monkeypatch.setattr(documents, "USER_MAX_DOCUMENTS", 1)
    assert upload(api, alice, pdf(1000, fill=b"\x02"), "c.pdf").status_code == 422
    assert staged == [2500]
    assert len(rows(factory)) == 1


def test_the_check_under_the_lock_still_decides(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first check is a courtesy to the database. A file that passes it and no longer fits
    when its turn comes (another upload of the user landed in between) is refused all the same."""
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    owner = user_id(factory, ALICE)
    real = documents._stage

    def another_lands_meanwhile(db: Session, body: bytes | bytearray) -> None:
        monkeypatch.setattr(documents, "_stage", real)
        real(db, body)
        with factory() as other:
            data = pdf(2000, fill=b"\x05")
            documents.store_file(
                other, owner, "other.pdf", data, hashlib.sha256(data).digest(), datetime.now(UTC)
            )
            other.commit()

    monkeypatch.setattr(documents, "_stage", another_lands_meanwhile)
    assert upload(api, alice, pdf(2000, fill=b"\x06"), "mine.pdf").status_code == 413
    assert [doc.name for doc in rows(factory)] == ["other.pdf"]


def test_a_replacement_asks_the_delete_guards(
    api: Api, alice: str, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """A replacement empties the old text and page images as a deletion does: what refuses the
    one refuses the other, and the row is returned as it is."""
    first = uploaded(api, alice, pdf(), "scan.pdf")
    set_columns(
        factory,
        first["id"],
        text_status=TextStatus.failed,
        failed_reason="why",
        text="what a run has read",
        raw=None,
    )
    add_image(factory, first["id"])
    asked: list[uuid.UUID] = []

    def guard(db: Session, document: IngestionDocument) -> str | None:
        asked.append(document.id)
        return "This document was read by a run and cannot be deleted yet."

    documents.register_delete_guard(guard)
    with caplog.at_level("INFO", logger="app.ingestion"):
        again = upload(api, alice, pdf(), "scan again.pdf")
    assert (again.status_code, again.json()["duplicate"]) == (200, True)
    assert (again.json()["id"], again.json()["name"]) == (first["id"], "scan.pdf")
    assert [str(document_id) for document_id in asked] == [first["id"]]
    # The refusal speaks: the page has a sentence to show, not a silent duplicate.
    assert again.json()["refused"] == documents.REPLACEMENT_REFUSED
    assert first["refused"] is None
    assert " - " not in documents.REPLACEMENT_REFUSED
    stored = row(factory, first["id"])
    assert (stored.text_status, stored.text, stored.raw) == (
        TextStatus.failed,
        "what a run has read",
        None,
    )
    assert count(factory, IngestionImage) == 1
    assert "document replacement refused" in [record.getMessage() for record in caplog.records]
    # Without the guard's objection the same upload takes the row over.
    documents._delete_guards.clear()
    assert upload(api, alice, pdf(), "scan again.pdf").status_code == 201


def test_a_type_this_module_does_not_know_is_never_taken_for_an_archive(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = uploaded(api, alice, pdf(), "sheet.xlsx")
    set_columns(factory, body["id"], mime="application/x-something-new")
    (item,) = listing(api, alice)["items"]
    assert (item["kind"], item["readable"]) == ("other", False)
    set_columns(factory, body["id"], mime="text/csv", text_status=TextStatus.done, text="a,b")
    (item,) = listing(api, alice)["items"]
    assert (item["kind"], item["readable"]) == ("text", True)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "", 401),
        ("POST", "", 401),
        ("POST", "/text", 401),
        ("GET", "/{id}/text", 401),
        ("DELETE", "/{id}", 401),
        ("POST", "/{id}/confirm", 401),
    ],
)
def test_an_answer_without_a_session_is_never_cached(
    api: Api, method: str, path: str, status: int
) -> None:
    response = api.request(method, URL + path.replace("{id}", str(uuid.uuid4())))
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


def test_every_answer_built_outside_the_router_is_no_store_too(api: Api, alice: str) -> None:
    body = uploaded(api, alice, b"some text")
    answers = {
        "csrf refused": api.post(URL, token=alice, content=b"x", csrf=False),
        "method not allowed": api.request("PUT", URL, token=alice),
        "bad offset": text_of(api, alice, body["id"], offset=-1),
        "unknown key in a paste": api.post(f"{URL}/text", token=alice, json={"text": "a", "x": 1}),
        "not json": api.post(f"{URL}/text", token=alice, content=b"not json"),
        "unknown route": api.get("/api/ingestion/nothing", token=alice),
    }
    assert {name: answer.status_code for name, answer in answers.items()} == {
        "csrf refused": 403,
        "method not allowed": 405,
        "bad offset": 422,
        "unknown key in a paste": 422,
        "not json": 422,
        "unknown route": 404,
    }
    for name, answer in answers.items():
        assert answer.headers["cache-control"] == "no-store", name


@pytest.mark.parametrize(
    ("path", "set_by_the_rule"),
    [
        ("/api/ingestion", True),
        ("/api/ingestion/documents", True),
        ("/api/ingestion/runs/1", True),
        ("/api/ingestionx", False),
        ("/api/jobs", False),
        ("/", False),
    ],
)
def test_the_no_store_rule_covers_the_ingestion_prefix_and_nothing_else(
    path: str, set_by_the_rule: bool
) -> None:
    from starlette.requests import Request
    from starlette.responses import Response

    from app.ingestion.routers import no_store

    async def call_next(_: Request) -> Response:
        return Response("x", headers={"Cache-Control": "public, max-age=60"})

    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    response = asyncio.run(no_store(Request(scope), call_next))
    expected = "no-store" if set_by_the_rule else "public, max-age=60"
    assert response.headers["cache-control"] == expected
