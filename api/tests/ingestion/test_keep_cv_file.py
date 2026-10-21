"""The file of an upload sent to become a base CV is kept (issue #519; SPEC-ingestion.md, "Text
extraction"). Through the app, two e2e people, real Postgres at head, the parse job played by the
test's parser:

- a PDF or a Word file uploaded with `for=base_cv` keeps its bytes once its text or its page
  images are stored, and the quota counts them; any other upload drops them as before;
- the base CV is converted after the text is stored (a pick while it is read is still 409), and
  finds the file whichever came first;
- the same file sent again for a base CV takes the mark and gets its file back;
- deleting the last base CV converted from it drops the file, the text stays; deleting the
  document in ingestion drops the row; another person neither reads nor drops it;
- a planted canary of the file reaches no log line.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import store
from app.ingestion import deletion, documents, documents_router, jobs
from app.ingestion.models import IngestionDocument, TextStatus
from tests.documents.render_support import give_profile
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.identity.api_helpers import Api, Clock
from tests.ingestion import archive_fixtures as fx
from tests.ingestion import scan_fixtures as scans
from tests.ingestion.conftest import signed_in
from tests.ingestion.helpers import (
    PDF_MAGIC,
    delete,
    listing,
    named,
    png,
    row,
    upload,
    uploaded,
    user_id,
)
from tests.ingestion.test_jobs import TEXT, Parser, parsed, run
from tests.ingestion.test_jobs_images import RealParser

ALEX = "e2e-keep-alex@example.org"
BLAKE = "e2e-keep-blake@example.org"
URL = "/api/ingestion/documents"
BASES = "/api/documents/base-cvs"


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Clock) -> str:
    token = signed_in(factory, api, ALEX)
    give_profile(factory, user_id(factory, ALEX), clock.now)
    return token


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Clock) -> str:
    token = signed_in(factory, api, BLAKE)
    give_profile(factory, user_id(factory, BLAKE), clock.now)
    return token


def for_cv(api: Api, token: str, data: bytes, name: str = "cv.pdf") -> Any:
    return api.post(f"{URL}?for=base_cv", token=token, content=data, headers=named(name))


def sent_for_cv(api: Api, token: str, data: bytes, name: str = "cv.pdf") -> dict[str, Any]:
    response = for_cv(api, token, data, name)
    assert response.status_code in (200, 201), response.text
    found: dict[str, Any] = response.json()
    return found


def convert(api: Api, token: str, document_id: str) -> Any:
    body = {"from": "upload", "ingestion_document_id": document_id}
    return api.request("POST", BASES, token=token, json=body)


def converted(api: Api, token: str, document_id: str) -> str:
    response = convert(api, token, document_id)
    assert response.status_code == 202, response.text
    made: str = response.json()["base_cv"]["id"]
    return made


def delete_base(api: Api, token: str, base_id: str) -> None:
    response = api.request("DELETE", f"{BASES}/{base_id}", token=token)
    assert response.status_code == 204, response.text


def cv_pdf(marker: str = "", size: int = 4096) -> bytes:
    """A made up PDF: the first bytes are what the upload reads, `marker` sits after them."""
    head = PDF_MAGIC + marker.encode()
    return head + b"\x00" * (size - len(head))


# --- kept for a base CV, dropped otherwise ------------------------------------------------------


def test_a_file_sent_for_a_base_cv_keeps_its_bytes_once_its_text_is_stored(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf(size=40_000)
    body = sent_for_cv(api, alex, data)
    assert row(factory, body["id"]).keep_bytes
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text, stored.raw) == (TextStatus.done, TEXT, data)
    assert stored.keep_bytes
    # The quota counts the file it keeps, beside the text.
    assert listing(api, alex)["usage"]["bytes"] == 40_000 + len(TEXT.encode())


def test_a_word_file_sent_for_a_base_cv_keeps_its_bytes_as_a_docx(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = fx.cv_docx()
    body = sent_for_cv(api, alex, data, "cv.docx")
    assert run(factory, clock, RealParser(), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.mime, stored.raw, stored.keep_bytes) == (documents.DOCX_MIME, data, True)


def test_the_scanned_pages_of_a_file_sent_for_a_base_cv_keep_its_bytes(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = scans.scan(2)
    body = sent_for_cv(api, alex, data, "scan.pdf")
    assert run(factory, clock, RealParser(), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.raw) == (TextStatus.awaiting_transcription, data)


def test_every_other_upload_drops_its_bytes_as_before(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    plain = uploaded(api, alex, cv_pdf("plain"), "cv.pdf")
    assert run(factory, clock, Parser(parsed()), plain["id"]) == jobs.DONE
    assert (row(factory, plain["id"]).raw, row(factory, plain["id"]).keep_bytes) == (None, False)
    scanned = uploaded(api, alex, scans.scan(1), "scan.pdf")
    assert run(factory, clock, RealParser(), scanned["id"]) == jobs.DONE
    assert row(factory, scanned["id"]).raw is None
    # Only a PDF or a ZIP takes the mark: an image or a text sent from the panel does not.
    image = sent_for_cv(api, alex, png(), "cv.png")
    text = sent_for_cv(api, alex, b"Robin Example, engineer.", "cv.txt")
    assert row(factory, image["id"]).keep_bytes is False
    assert (row(factory, text["id"]).keep_bytes, row(factory, text["id"]).raw) == (False, None)


@pytest.mark.parametrize("purpose", ["", "BASE_CV", "base-cv", "profile", "base_cv "])
def test_another_purpose_is_refused_and_stores_nothing(
    api: Api, alex: str, factory: sessionmaker[Session], purpose: str
) -> None:
    response = api.post(
        URL, token=alex, content=cv_pdf(), headers=named("cv.pdf"), params={"for": purpose}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == documents_router.BAD_PURPOSE
    assert listing(api, alex)["items"] == []


def test_a_file_that_fails_drops_its_bytes_and_its_mark(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    from app.ingestion import parser_client as pc

    body = sent_for_cv(api, alex, cv_pdf())
    refusal = pc.ParserRefusedError("damaged", failure="file")
    assert run(factory, clock, Parser(refusal), body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.raw, stored.keep_bytes) == (TextStatus.failed, None, False)


def test_a_text_and_a_kept_file_over_the_quota_fail_the_document(
    api: Api,
    alex: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file stays, so the text comes on top of it, not in its place."""
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 10_000)
    body = sent_for_cv(api, alex, cv_pdf(size=6_000))
    assert run(factory, clock, Parser(parsed("word " * 900)), body["id"]) == jobs.FAILED
    assert (row(factory, body["id"]).failed_reason, row(factory, body["id"]).raw) == (
        jobs.OVER_QUOTA,
        None,
    )


# --- the order of the pick and the text -----------------------------------------------------------


def test_the_base_cv_finds_the_file_whether_it_was_asked_before_or_after_the_text(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf()
    body = sent_for_cv(api, alex, data)
    # Asked while the file is read: refused as before, the file stays.
    assert convert(api, alex, body["id"]).status_code == 409
    assert row(factory, body["id"]).raw == data
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    first = converted(api, alex, body["id"])
    # Asked again later, from the same file: still there.
    second = converted(api, alex, body["id"])
    assert first != second
    assert row(factory, body["id"]).raw == data


def test_a_file_already_read_gets_its_bytes_back_when_sent_for_a_base_cv(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf("again")
    plain = uploaded(api, alex, data, "cv.pdf")
    assert run(factory, clock, Parser(parsed()), plain["id"]) == jobs.DONE
    base_id = converted(api, alex, plain["id"])
    assert row(factory, plain["id"]).raw is None
    response = for_cv(api, alex, data)
    assert response.status_code == 200, response.text
    assert (response.json()["id"], response.json()["duplicate"]) == (plain["id"], True)
    stored = row(factory, plain["id"])
    assert (stored.raw, stored.keep_bytes, stored.text) == (data, True, TEXT)
    assert listing(api, alex)["usage"]["bytes"] == len(data) + len(TEXT.encode())
    # The base CV made before the file came back reads it now; its delete drops it again.
    delete_base(api, alex, base_id)
    assert row(factory, plain["id"]).raw is None


def test_a_file_waiting_for_its_reader_takes_the_mark_of_a_second_upload(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf("waiting")
    plain = uploaded(api, alex, data, "cv.pdf")
    assert sent_for_cv(api, alex, data)["id"] == plain["id"]
    # A plain upload of it after that leaves the mark alone.
    assert upload(api, alex, data, "cv.pdf").status_code == 200
    assert row(factory, plain["id"]).keep_bytes
    assert run(factory, clock, Parser(parsed()), plain["id"]) == jobs.DONE
    assert row(factory, plain["id"]).raw == data


def test_a_file_sent_again_without_room_is_refused_and_left_as_it_was(
    api: Api,
    alex: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = cv_pdf("room", size=6_000)
    plain = uploaded(api, alex, data, "cv.pdf")
    assert run(factory, clock, Parser(parsed()), plain["id"]) == jobs.DONE
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 5_000)
    assert for_cv(api, alex, data).status_code == 413
    stored = row(factory, plain["id"])
    assert (stored.raw, stored.keep_bytes) == (None, False)


# --- hard delete stays hard ---------------------------------------------------------------------


def test_deleting_the_last_base_cv_of_the_file_drops_it_and_keeps_the_text(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf()
    body = sent_for_cv(api, alex, data)
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    first = converted(api, alex, body["id"])
    second = converted(api, alex, body["id"])
    delete_base(api, alex, first)
    assert row(factory, body["id"]).raw == data  # the other base CV still reads it
    delete_base(api, alex, second)
    stored = row(factory, body["id"])
    assert (stored.raw, stored.keep_bytes, stored.text) == (None, False, TEXT)
    assert listing(api, alex)["usage"]["bytes"] == len(TEXT.encode())


def test_deleting_the_document_in_ingestion_drops_the_file_and_unlinks_the_base_cv(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf("gone")
    body = sent_for_cv(api, alex, data)
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    base_id = converted(api, alex, body["id"])
    assert delete(api, alex, body["id"]).status_code == 204
    with factory() as db:
        assert db.get(IngestionDocument, uuid.UUID(body["id"])) is None
        found = db.execute(
            sa.text("SELECT count(*) FROM ingestion_documents WHERE bytes = :b"), {"b": data}
        ).scalar_one()
        assert found == 0
        source = db.execute(
            sa.text("SELECT source_document_id FROM documents WHERE id = :d"), {"d": base_id}
        ).scalar_one()
        assert source is None
    # The base CV stays and goes later without trouble.
    delete_base(api, alex, base_id)


def test_a_base_cv_from_a_file_that_was_not_kept_changes_nothing_when_deleted(
    api: Api, alex: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    plain = uploaded(api, alex, cv_pdf("plain"), "cv.pdf")
    assert run(factory, clock, Parser(parsed()), plain["id"]) == jobs.DONE
    delete_base(api, alex, converted(api, alex, plain["id"]))
    stored = row(factory, plain["id"])
    assert (stored.raw, stored.keep_bytes, stored.text) == (None, False, TEXT)


def test_a_file_replaced_and_waiting_for_its_reader_keeps_it_when_its_base_cv_goes(
    api: Api, alex: str, factory: sessionmaker[Session]
) -> None:
    """The file of a row read again is what the job reads: a delete never takes it away."""
    data = cv_pdf("pending")
    body = sent_for_cv(api, alex, data)
    with factory() as db:
        assert not documents.drop_kept_file(db, user_id(factory, ALEX), uuid.UUID(body["id"]))
        db.commit()
    assert row(factory, body["id"]).raw == data


def test_a_base_cv_delete_waits_for_an_ingestion_delete_holding_the_file(
    api: Api,
    alex: str,
    engine: sa.Engine,
    factory: sessionmaker[Session],
    clock: Clock,
) -> None:
    """Ingestion's delete holds the document row, then rewrites the base CV (its SET NULL); the
    base CV's delete takes that row before its own (lock 1b), so it waits and then deletes, no
    deadlock and no busy answer."""
    alex_id = user_id(factory, ALEX)
    body = sent_for_cv(api, alex, cv_pdf("race"))
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    base_id = uuid.UUID(converted(api, alex, body["id"]))
    holder = factory()
    try:
        assert documents.lock_document(holder, alex_id, uuid.UUID(body["id"])) is not None
        outcome: dict[str, Any] = {}

        def remove_base() -> None:
            with factory() as db:
                store.delete_base(db, alex_id, base_id, clock.now)
                store.commit(db)

        thread = in_thread(remove_base, outcome)
        wait_until_blocked(engine, [thread], 1)
        assert thread.is_alive()
        assert deletion.delete(holder, alex_id, uuid.UUID(body["id"]), clock.now)
        holder.commit()
        thread.join()
    finally:
        holder.close()
    assert "error" not in outcome, outcome.get("error")
    with factory() as db:
        assert (
            db.execute(
                sa.text("SELECT count(*) FROM documents WHERE id = :d"), {"d": base_id}
            ).scalar_one()
            == 0
        )


# --- another person -----------------------------------------------------------------------------


def test_another_person_never_reads_nor_drops_the_file(
    api: Api, alex: str, blake: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = cv_pdf("shared")
    mine = sent_for_cv(api, alex, data)
    assert run(factory, clock, Parser(parsed()), mine["id"]) == jobs.DONE
    base_id = converted(api, alex, mine["id"])
    # Blake's own upload of the same bytes is his own row, never Alex's.
    theirs = sent_for_cv(api, blake, data)
    assert theirs["id"] != mine["id"]
    assert convert(api, blake, mine["id"]).status_code == 404
    assert api.request("DELETE", f"{BASES}/{base_id}", token=blake).status_code == 404
    assert delete(api, blake, mine["id"]).status_code == 404
    with factory() as db:
        blake_id = user_id(factory, BLAKE)
        assert not documents.drop_kept_file(db, blake_id, uuid.UUID(mine["id"]))
        db.commit()
    assert row(factory, mine["id"]).raw == data
    assert listing(api, blake)["usage"]["bytes"] == len(data)


# --- logs -----------------------------------------------------------------------------------------


def test_the_canary_of_a_kept_file_reaches_no_log_line(
    api: Api,
    alex: str,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = f"canary{uuid.uuid4().hex}"
    data = cv_pdf(canary)
    with caplog.at_level("DEBUG"):
        plain = uploaded(api, alex, data, "cv.pdf")
        assert run(factory, clock, Parser(parsed(f"{TEXT} {canary}")), plain["id"]) == jobs.DONE
        assert sent_for_cv(api, alex, data)["id"] == plain["id"]
        base_id = converted(api, alex, plain["id"])
        delete_base(api, alex, base_id)
        assert delete(api, alex, plain["id"]).status_code == 204
    logged = caplog.text + "".join(
        json.dumps({k: str(v) for k, v in vars(record).items()}) for record in caplog.records
    )
    assert any(r.getMessage() == "document file kept" for r in caplog.records)
    assert canary not in logged
    assert data.hex() not in logged
