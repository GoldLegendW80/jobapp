"""Archives through the parse job (app/ingestion/jobs.py, task G7) against a real Postgres: a
Word document becomes its text, a LinkedIn export one document per CSV in one transaction that
deletes the row of the archive, and a ZIP that is neither is failed with its reason and keeps
its row.

The parser is played by the real handlers, sandbox/docx.py and sandbox/linkedin_zip.py, called
in the test process and read through the client's own checks: everything but the socket and
the lockdown, which tests/ingestion/parser/test_archive_container.py adds.
"""

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import deletion, documents, jobs
from app.ingestion import parser_client as pc
from app.ingestion.models import FailedKind, IngestionDocument, TextSource, TextStatus
from tests.identity.api_helpers import Api, Clock
from tests.ingestion import archive_fixtures as fx
from tests.ingestion import image_fixtures as pictures
from tests.ingestion.conftest import ALICE, BOB, RecordingQueue
from tests.ingestion.helpers import delete, row, rows, set_columns, upload, uploaded, user_id
from tests.ingestion.parser.helpers import (
    Plenty,
    as_in_the_child,
    sandbox_handler,
    sandbox_module,
)
from tests.ingestion.test_jobs import Parser, run, stored_before

main = sandbox_module("main")


class RealParser(Parser):
    """The two real readers behind the client's checks, remembering what they were asked."""

    def __init__(self) -> None:
        super().__init__(None)

    async def __call__(self, kind: str, data: bytes, options: Any = None) -> pc.Parsed:
        self.calls.append((kind, data))
        if self.during is not None:
            self.during()
        with as_in_the_child():
            answer = sandbox_handler(kind).handle(data, dict(options or {}), Plenty())
        return pc.check_parsed(pc.loads(main.encode(answer)), kind)


def kinds(parser: Parser) -> list[str]:
    return [kind for kind, _ in parser.calls]


def of_user(factory: sessionmaker[Session], email: str) -> list[IngestionDocument]:
    owner = user_id(factory, email)
    return [document for document in rows(factory) if document.user_id == owner]


EXPORT_NAMES = ["export.zip: Positions.csv", "export.zip: Profile.csv", "export.zip: Skills.csv"]


# --- a Word document ----------------------------------------------------------------------------


def test_an_uploaded_docx_is_queued_and_becomes_its_text(
    api: Api, alice: str, factory: sessionmaker[Session], jobs_queue: RecordingQueue, clock: Clock
) -> None:
    body = uploaded(api, alice, fx.cv_docx(), "cv.docx")
    assert (body["status"], body["readable"], body["kind"]) == ("pending", True, "archive")
    assert [job["args"] for job in jobs_queue.jobs] == [(body["id"],)]
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["docx"]
    stored = row(factory, body["id"])
    assert stored.text == (
        "Curriculum vitae\n\nCamille Durand\n"
        f"Find me on LinkedIn ({fx.LINK})\nLanguage\tLevel\nFrench\tNative\n\n"
        "camille.durand@example.org"
    )
    assert (stored.text_status, stored.mime) == (TextStatus.done, documents.DOCX_MIME)
    assert (stored.pages, stored.pages_text, stored.pages_image) == (None, None, None)
    assert (stored.raw, stored.note, stored.truncated) == (None, None, False)
    assert documents.kind_of(stored.mime) is documents.Kind.archive


# --- a LinkedIn export --------------------------------------------------------------------------


def test_an_export_becomes_one_row_per_csv_in_one_transaction_and_its_own_row_is_gone(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, fx.export(), "export.zip")
    commits: list[int] = []
    engine = factory.kw["bind"]

    def count_rows(connection: sa.Connection) -> None:
        commits.append(
            connection.execute(
                sa.select(sa.func.count()).select_from(IngestionDocument)
            ).scalar_one()
        )

    sa.event.listen(engine, "commit", count_rows)
    try:
        parser = RealParser()
        assert run(factory, clock, parser, body["id"]) == jobs.DONE
    finally:
        sa.event.remove(engine, "commit", count_rows)
    # Two commits: the claim, with the archive alone, then the three files and no archive.
    assert commits == [1, 3]
    assert kinds(parser) == ["docx", "linkedin_zip"]
    found = of_user(factory, ALICE)
    assert sorted(document.name for document in found) == EXPORT_NAMES
    assert body["id"] not in {str(document.id) for document in found}
    by_name = {document.name: document for document in found}
    positions = by_name["export.zip: Positions.csv"]
    assert positions.text == fx.POSITIONS.replace("\r\n", "\n").strip()
    assert positions.sha256 == hashlib.sha256(fx.POSITIONS.encode()).digest()
    assert positions.size == len(fx.POSITIONS.encode())
    for document in found:
        assert (document.mime, document.text_status) == (documents.CSV_MIME, TextStatus.done)
        assert (document.text_source, document.raw) == (TextSource.parsed, None)
        assert (document.pending_since, document.failed_reason) == (None, None)
    # What nobody reads is nowhere.
    assert "never read" not in " ".join(document.text or "" for document in found)


def test_an_export_that_fails_half_way_keeps_nothing_but_its_row(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = uploaded(api, alice, fx.export(), "export.zip")
    real, calls = documents._store, []

    def breaks_on_the_second(*args: Any, **kwargs: Any) -> documents.Stored:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the database refused the second file")
        return real(*args, **kwargs)

    monkeypatch.setattr(documents, "_store", breaks_on_the_second)
    assert run(factory, clock, RealParser(), body["id"]) == jobs.ERROR
    assert len(calls) == 2
    (alone,) = of_user(factory, ALICE)
    assert (str(alone.id), alone.text_status, alone.raw) == (
        body["id"],
        TextStatus.pending,
        fx.export(),
    )


def test_the_archive_deleted_while_it_is_parsed_leaves_nothing(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, fx.export(), "export.zip")
    parser = RealParser()

    def delete_it_once() -> None:
        if len(parser.calls) == 2:  # while the export reader works
            assert delete(api, alice, body["id"]).status_code == 204

    parser.during = delete_it_once
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    assert kinds(parser) == ["docx", "linkedin_zip"]
    assert of_user(factory, ALICE) == []


def test_the_same_export_uploaded_twice_makes_no_duplicate(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    first = uploaded(api, alice, fx.export(), "export.zip")
    assert run(factory, clock, RealParser(), first["id"]) == jobs.DONE
    before = {document.name: (document.id, document.text) for document in of_user(factory, ALICE)}
    clock.advance(minutes=5)
    # The archive's row is gone, so the same archive is a new upload; its files are not.
    second = uploaded(api, alice, fx.export(), "another name.zip")
    assert second["id"] != first["id"]
    assert run(factory, clock, RealParser(), second["id"]) == jobs.DONE
    after = {document.name: (document.id, document.text) for document in of_user(factory, ALICE)}
    assert after == before
    assert sorted(after) == EXPORT_NAMES
    # A CSV of the export added alone is the document that is already there.
    again = upload(api, alice, fx.POSITIONS.encode(), "Positions.csv")
    assert again.json()["id"] == str(before["export.zip: Positions.csv"][0])
    assert len(of_user(factory, ALICE)) == 3


def test_an_export_that_would_replace_a_file_a_run_has_read_says_so_and_keeps_the_rest(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    first = uploaded(api, alice, fx.export(), "export.zip")
    assert run(factory, clock, RealParser(), first["id"]) == jobs.DONE
    by_name = {document.name: document for document in of_user(factory, ALICE)}
    read = by_name["export.zip: Positions.csv"]
    replaced = by_name["export.zip: Profile.csv"]
    # Both replaceable; a run has read only the first.
    for document in (read, replaced):
        set_columns(factory, str(document.id), text_status=TextStatus.failed, failed_reason="why")
    assert delete(api, alice, str(by_name["export.zip: Skills.csv"].id)).status_code == 204

    def check(db: Session, document: IngestionDocument) -> str | None:
        return deletion.REPLACEMENT_REFUSED if document.id == read.id else None

    documents.register_replacement_check(check)
    clock.advance(minutes=5)
    second = uploaded(api, alice, fx.export(), "again.zip")
    assert run(factory, clock, RealParser(), second["id"]) == jobs.FAILED
    archive = row(factory, second["id"])
    assert (archive.text_status, archive.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert archive.failed_reason == documents.export_refused(["export.zip: Positions.csv"])
    assert archive.raw is None
    kept = row(factory, str(read.id))
    assert (kept.text_status, kept.failed_reason) == (TextStatus.failed, "why")
    assert row(factory, str(replaced.id)).text_status is TextStatus.done
    names = sorted(document.name for document in of_user(factory, ALICE))
    # A replaced row takes the name of the file that replaced it.
    assert names == [
        "again.zip",
        "again.zip: Profile.csv",
        "again.zip: Skills.csv",
        "export.zip: Positions.csv",
    ]
    # Deleting what was read and adding the export again replaces its failed row.
    assert delete(api, alice, str(read.id)).status_code == 204
    clock.advance(minutes=1)
    third = uploaded(api, alice, fx.export(), "again.zip")
    assert third["id"] == second["id"]
    assert run(factory, clock, RealParser(), third["id"]) == jobs.DONE
    assert len(of_user(factory, ALICE)) == 3


@pytest.mark.parametrize(
    ("names", "sentence"),
    [
        (
            ["a.zip: Positions.csv"],
            "The other files of this export were added. A run has read a.zip: Positions.csv, "
            "already among your documents, so the export did not replace it. Delete it first, "
            "then add the export again.",
        ),
        (
            ["a.zip: Positions.csv", "a.zip: Skills.csv"],
            "The other files of this export were added. A run has read a.zip: Positions.csv, "
            "a.zip: Skills.csv, already among your documents, so the export did not replace "
            "them. Delete them first, then add the export again.",
        ),
    ],
)
def test_the_sentence_of_an_export_that_did_not_replace_a_file(
    names: list[str], sentence: str
) -> None:
    assert documents.export_refused(names) == sentence
    assert " - " not in sentence
    assert "\u2013" not in sentence
    assert "\u2014" not in sentence


def test_the_same_export_of_two_users_gives_each_their_own_rows(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    hers = uploaded(api, alice, fx.export(), "export.zip")
    his = uploaded(api, bob, fx.export(), "export.zip")
    assert run(factory, clock, RealParser(), hers["id"]) == jobs.DONE
    # Hers are stored: his export still makes his own three.
    assert run(factory, clock, RealParser(), his["id"]) == jobs.DONE
    alices, bobs = of_user(factory, ALICE), of_user(factory, BOB)
    assert sorted(document.name for document in alices) == EXPORT_NAMES
    assert sorted(document.name for document in bobs) == EXPORT_NAMES
    assert {document.id for document in alices}.isdisjoint(document.id for document in bobs)
    # One user's rows are the other's business in no way: deleting hers leaves his.
    for document in alices:
        assert delete(api, alice, str(document.id)).status_code == 204
        assert delete(api, bob, str(document.id)).status_code == 404
    assert len(of_user(factory, BOB)) == 3


def test_an_export_that_takes_the_user_past_200_documents(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    owner = user_id(factory, ALICE)
    with factory() as db:
        for number in range(documents.USER_MAX_DOCUMENTS - 2):
            documents.store_paste(
                db, owner, f"Note {number}", f"A note, number {number}.", clock.now
            )
        db.commit()
    body = uploaded(api, alice, fx.export(), "export.zip")  # the 199th document
    # Three files in the place of one archive would make 201.
    assert run(factory, clock, RealParser(), body["id"]) == jobs.FAILED
    found = of_user(factory, ALICE)
    assert len(found) == documents.USER_MAX_DOCUMENTS - 1
    assert not [document for document in found if document.mime == documents.CSV_MIME]
    failed = row(factory, body["id"])
    assert (failed.text_status, failed.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert failed.failed_reason == documents.EXPORT_TOO_MANY
    assert "200 documents" in documents.EXPORT_TOO_MANY
    assert failed.raw is None
    # One document less and the same export fits exactly: 197 notes and three files.
    note = next(document for document in found if document.mime == documents.TEXT_MIME)
    assert delete(api, alice, str(note.id)).status_code == 204
    clock.advance(minutes=1)
    again = uploaded(api, alice, fx.export(), "export.zip")
    assert again["id"] == body["id"], "the failed row is taken over"
    assert run(factory, clock, RealParser(), again["id"]) == jobs.DONE
    assert len(of_user(factory, ALICE)) == documents.USER_MAX_DOCUMENTS


# --- what cannot be read ------------------------------------------------------------------------

REFUSED = [
    ("a ZIP that is neither", lambda: fx.zip_of([("notes.txt", "some notes")]), "not_export", 2),
    ("unknown CSV names only", lambda: fx.export({"Connections.csv": "a,b\r\n"}), "not_export", 2),
    ("a CSV that is not UTF-8", lambda: fx.export({"Skills.csv": b"Fran\xe7ais"}), "not_utf8", 2),
    ("a docm", lambda: fx.docx(content_types=fx.MACRO_CONTENT_TYPES), "macros", 1),
    ("a docx without its body", lambda: fx.zip_of(
        [("[Content_Types].xml", fx.CONTENT_TYPES), ("word/styles.xml", "<a/>")]), "unreadable", 1),
    ("an entity bomb", fx.entity_bomb_docx, "bomb", 1),
    *[(name, make, code, 1) for name, make, code in fx.HOSTILE],
]  # fmt: skip


@pytest.mark.parametrize(
    ("name", "make", "code", "asked"), REFUSED, ids=[case[0] for case in REFUSED]
)
def test_an_archive_that_cannot_be_read_is_failed_with_its_reason_and_keeps_its_row(
    name: str,
    make: Any,
    code: str,
    asked: int,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
) -> None:
    body = uploaded(api, alice, make(), "file.zip")
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.FAILED
    assert kinds(parser) == ["docx", "linkedin_zip"][:asked]
    (kept,) = of_user(factory, ALICE)
    assert str(kept.id) == body["id"]
    assert (kept.text_status, kept.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert kept.failed_reason == jobs.ARCHIVE_SENTENCES[code]
    assert (kept.raw, kept.text) == (None, None)
    # A file failure is final: nothing queues it again.
    assert jobs.reap(factory, clock.now + timedelta(hours=1)).queue == []


def test_every_reason_of_an_archive_is_a_sentence_without_a_dash_as_a_separator() -> None:
    sentences = [*jobs.ARCHIVE_SENTENCES.values(), documents.EXPORT_TOO_MANY]
    assert set(jobs.ARCHIVE_SENTENCES) >= pc.FILE_CODES - {"encryption", "too_many_pixels"}
    for sentence in sentences:
        assert sentence.endswith(".")
        assert not any(mark in sentence for mark in (" - ", "—", "–", "PDF"))
    time_note = pc.Note("time_limit", {})
    assert jobs.note_sentence(time_note, documents.Kind.archive) == (
        "This document took too long to read to its end."
    )


# --- the rows that waited -----------------------------------------------------------------------


def test_the_zip_rows_waiting_since_g1_are_queued_by_the_reaper_on_its_first_tick(
    alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    owner = user_id(factory, ALICE)
    weeks_ago = clock.now - timedelta(days=21)
    export = stored_before(factory, owner, fx.export(), "application/zip", weeks_ago)
    word = stored_before(factory, owner, fx.cv_docx(), "application/zip", weeks_ago)
    neither = stored_before(
        factory, owner, fx.zip_of([("a.txt", "a")]), "application/zip", weeks_ago
    )
    # Since G8 an image is read too: the one that waited since G1 is queued with the zip rows.
    image = stored_before(factory, owner, pictures.screenshot_png(), "image/png", weeks_ago)
    assert {"application/zip", "image/png"} <= set(jobs.readable_mimes())
    found = jobs.reap(factory, clock.now)
    assert sorted(job[0] for job in found.queue) == sorted([export, word, neither, image])
    outcomes = {
        document_id: run(factory, clock, RealParser(), str(document_id))
        for document_id, _ in found.queue
    }
    assert outcomes == {export: jobs.DONE, word: jobs.DONE, neither: jobs.FAILED, image: jobs.DONE}
    left = {document.id: document for document in of_user(factory, ALICE)}
    assert export not in left
    assert left[word].mime == documents.DOCX_MIME
    assert left[neither].failed_reason == jobs.ARCHIVE_SENTENCES["not_export"]
    # The image is read as a picture, waiting for its transcription, and nothing of it is kept raw.
    assert (left[image].text_status, left[image].raw) == (TextStatus.awaiting_transcription, None)
    assert len(left) == 3 + 3  # the three files of the export, the document, the two others
    assert jobs.reap(factory, clock.now + timedelta(hours=1)).queue == []


# --- privacy ------------------------------------------------------------------------------------


def test_nothing_of_an_export_or_a_document_reaches_a_log_or_the_result(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    # The name of the files is a token drawn for this run, never a word of the language: a log
    # record carries the path of the module that wrote it (`pathname`), and a checkout under
    # macOS's `/private/var/folders` holds "private", as another one may hold any common word.
    name = f"name-{uuid.uuid4().hex}"
    export = uploaded(api, alice, fx.export(), f"Camille {name} export.zip")
    word = uploaded(api, alice, fx.cv_docx(), f"Camille {name} cv.docx")
    bomb = uploaded(api, alice, fx.ratio_bomb(), f"Camille {name} bomb.zip")
    results = [run(factory, clock, RealParser(), body["id"]) for body in (export, word, bomb)]
    assert results == [jobs.DONE, jobs.DONE, jobs.FAILED]
    said = caplog.text + " ".join(repr(vars(record)) for record in caplog.records)
    for secret in (name, "Camille", "Durand", "Positions.csv", "Example Rail", "example.org"):
        assert secret not in said, secret
    unpacked = [record for record in caplog.records if record.getMessage() == "export unpacked"]
    assert [(record.files, record.new_files) for record in unpacked] == [(3, 3)]  # type: ignore[attr-defined]
