"""The parse job and its reaper (app/ingestion/jobs.py) against a real Postgres, with a parser
that the test plays: what a result becomes, every kind of failure, the races with a deletion
and a replacement, the reaper's rules, the upload that queues the job, the queue that is down,
the rate of readings, and what never reaches a log or a return value.

The job is called as arq calls it, `parse_document(ctx, document_id)`, without a queue: the
tests with the real worker on a real Redis are test_jobs_worker.py, and the real parser is in
tests/ingestion/parser/test_pdf_container.py.
"""

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents.ratelimit import RateLimiter
from app.ingestion import documents, documents_router, jobs, secrets
from app.ingestion import parser_client as pc
from app.ingestion.models import (
    TEXT_MAX_CHARS,
    FailedKind,
    IngestionDocument,
    TextStatus,
)
from tests.identity.api_helpers import Api, Clock
from tests.ingestion.conftest import ALICE, RecordingQueue
from tests.ingestion.helpers import (
    MIB,
    archive,
    delete,
    listing,
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

TEXT = "Robin Example, applied AI engineer in Nantes.\fSecond page, since March 2024."


def parsed(text: str = TEXT, **more: Any) -> pc.Parsed:
    slots = text.count("\f") + 1
    values = {"pages": slots, "pages_text": slots, "pages_image": 0, **more}
    return pc.Parsed(text=text, **values)


class Parser:
    """The parser as a test plays it: answers in order, each a Parsed or an error to raise, and
    remembers what it was sent. `gate`, when set, is waited for before it answers."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, bytes]] = []
        self.started = threading.Event()
        self.gate: asyncio.Event | None = None
        self.during: Callable[[], None] | None = None

    async def __call__(self, kind: str, data: bytes) -> pc.Parsed:
        self.calls.append((kind, data))
        self.started.set()
        if self.during is not None:
            await asyncio.to_thread(self.during)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def context(factory: sessionmaker[Session], clock: Clock, parser: Parser) -> dict[str, Any]:
    return {"sessions": factory, "clock": clock, "ingestion_parse": parser}


def run(factory: sessionmaker[Session], clock: Clock, parser: Parser, document_id: str) -> str:
    return asyncio.run(jobs.parse_document(context(factory, clock, parser), document_id))


def file_refusal(code: str, **values: int) -> pc.ParserRefusedError:
    return pc.ParserRefusedError(code, failure="file", values=values)


# --- the upload queues the reading --------------------------------------------------------------


def test_an_uploaded_pdf_queues_its_reading_after_it_is_stored(
    api: Api, alice: str, factory: sessionmaker[Session], jobs_queue: RecordingQueue, clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    assert (body["status"], body["readable"], body["failed_reason"]) == ("pending", True, None)
    stored = row(factory, body["id"])
    assert jobs_queue.jobs == [
        {
            "function": "parse_document",
            "args": (body["id"],),
            "queue": queues.INGESTION,
            "job_id": queues.document_job_id(stored.id, 0, clock.now),
        }
    ]
    assert jobs_queue.jobs[0]["queue"] == "jobapp:ingestion"
    # The job finds the row: it was committed before the job was queued.
    assert stored.raw == pdf()
    assert (stored.attempts, stored.pending_since) == (0, clock.now)


def test_only_a_stored_file_that_waits_for_a_reader_is_queued(
    api: Api, alice: str, jobs_queue: RecordingQueue, factory: sessionmaker[Session], clock: Clock
) -> None:
    uploaded(api, alice, b"plain text", "notes.txt")
    uploaded(api, alice, png(), "shot.png")
    uploaded(api, alice, archive(), "export.zip")
    assert upload(api, alice, b"GIF89a....", "a.gif").status_code == 415
    assert jobs_queue.jobs == []
    first = uploaded(api, alice, pdf(), "cv.pdf")
    assert len(jobs_queue.jobs) == 1
    # The same file again: nothing is stored, nothing is queued.
    again = upload(api, alice, pdf(), "cv again.pdf")
    assert (again.status_code, again.json()["duplicate"]) == (200, True)
    assert len(jobs_queue.jobs) == 1
    # A replacement is a new try of the same row: queued, under another id.
    set_columns(factory, first["id"], text_status=TextStatus.failed, failed_reason="x", raw=None)
    clock.advance(minutes=5)
    replaced = upload(api, alice, pdf(), "cv.pdf")
    assert (replaced.status_code, replaced.json()["id"]) == (201, first["id"])
    assert [job["args"] for job in jobs_queue.jobs] == [(first["id"],), (first["id"],)]
    assert jobs_queue.jobs[0]["job_id"] != jobs_queue.jobs[1]["job_id"]


def test_the_job_is_queued_outside_the_admission_of_the_body(
    app: FastAPI, api: Api, alice: str, jobs_queue: RecordingQueue
) -> None:
    """When the queue is asked, the user's turn and the upload slot are already given back: a
    slow Redis never holds them."""
    seen: list[tuple[int, int]] = []
    real = jobs_queue.enqueue

    async def watching(*args: Any, **kwargs: Any) -> bool:
        gate = documents_router._gate(app)
        seen.append((gate.bodies, gate.slots._value))
        return await real(*args, **kwargs)

    jobs_queue.enqueue = watching  # type: ignore[method-assign]
    uploaded(api, alice, pdf(), "cv.pdf")
    assert seen == [(0, documents_router.UPLOAD_SLOTS)]


def test_when_the_queue_is_down_the_document_is_failed_at_once_and_a_new_upload_replaces_it(
    api: Api, alice: str, jobs_queue: RecordingQueue, factory: sessionmaker[Session], clock: Clock
) -> None:
    jobs_queue.down = True
    response = upload(api, alice, pdf(), "cv.pdf")
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["failed_reason"]) == ("failed", jobs.SERVER_FAILED)
    stored = row(factory, body["id"])
    assert (stored.failed_kind, stored.raw, stored.pending_since) == (FailedKind.server, None, None)
    assert listing(api, alice)["usage"]["bytes"] == 0, "the bytes are gone, the quota is free"
    jobs_queue.down = False
    clock.advance(seconds=20)
    again = upload(api, alice, pdf(), "cv.pdf")
    assert (again.status_code, again.json()["id"]) == (201, body["id"])
    assert (again.json()["status"], again.json()["failed_reason"]) == ("pending", None)
    assert len(jobs_queue.jobs) == 1
    assert row(factory, body["id"]).raw == pdf()


def test_fail_unqueued_leaves_a_document_that_moved_on(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    document_id, owner = uuid.UUID(body["id"]), user_id(factory, ALICE)
    # Claimed by a job meanwhile (the reaper queued it): not this function's to fail.
    set_columns(factory, body["id"], attempts=1)
    found = jobs.fail_unqueued(factory, document_id, owner, clock.now)
    assert found is not None
    assert found.text_status is TextStatus.pending
    # Deleted meanwhile: nothing to return.
    assert delete(api, alice, body["id"]).status_code == 204
    assert jobs.fail_unqueued(factory, document_id, owner, clock.now) is None


# --- a result -----------------------------------------------------------------------------------


def test_a_text_is_stored_with_its_counts_and_the_bytes_are_dropped(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(40_000), "cv.pdf")
    assert listing(api, alice)["usage"]["bytes"] == 40_000
    parser = Parser(parsed(pages=5, pages_text=2, pages_image=0))
    clock.advance(seconds=3)
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert parser.calls == [("pdf_text", pdf(40_000))]
    stored = row(factory, body["id"])
    assert stored.text == TEXT
    assert stored.raw is None
    assert stored.text_status is TextStatus.done
    assert (stored.pages, stored.pages_text, stored.pages_image) == (5, 2, 0)
    assert (stored.truncated, stored.note, stored.secret_warning) == (False, None, False)
    assert (stored.failed_kind, stored.failed_reason, stored.pending_since) == (None, None, None)
    assert (stored.attempts, stored.parser_version) == (1, documents.PARSER_VERSION)
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["pages"], item["pages_text"]) == ("done", 5, 2)
    # The quota now counts the text, not the file.
    assert listing(api, alice)["usage"]["bytes"] == len(TEXT.encode())
    # The preview reads it, form feed included: offsets are those of the text as stored.
    preview = text_of(api, alice, body["id"]).json()
    assert preview["text"] == TEXT
    assert preview["total"] == len(TEXT)


def test_form_feeds_are_kept_and_everything_else_is_cleaned(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """Not documents.clean_text, which would turn the page separators into line feeds."""
    dirty = "one\r\ntwo\x1b[0m\x07\fpage 2\x0bend\u2028last\x85.\f\fpage 4\ttab"
    body = uploaded(api, alice, pdf(), "cv.pdf")
    answer = parsed(dirty, pages=9, pages_text=3, pages_image=1)
    assert run(factory, clock, Parser(answer), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert stored.text == "one\ntwo[0m\fpage 2\nend\nlast\n.\f\fpage 4\ttab"
    assert stored.text.count("\f") == dirty.count("\f") == 3
    assert "\f" not in documents.clean_text(dirty), "what the text route would have done"
    assert (stored.pages, stored.pages_text, stored.pages_image) == (9, 3, 1)


@pytest.mark.parametrize(
    ("notes", "sentence"),
    [
        (
            [("pages_not_read", {"from": 201, "to": 250})],
            "Only the first 200 pages of a PDF are read. Pages 201 to 250 were not read.",
        ),
        (
            [("pages_not_read", {"from": 201, "to": 201})],
            "Only the first 200 pages of a PDF are read. Page 201 was not read.",
        ),
        (
            [("text_limit", {"from": 31, "to": 44})],
            "Only the first 500,000 characters were read. Pages 31 to 44 were not read.",
        ),
        ([("text_limit", {})], "Only the first 500,000 characters were read."),
        (
            [("time_limit", {"from": 5, "to": 12})],
            "This PDF took too long to read to its end. Pages 5 to 12 were not read.",
        ),
        (
            [("scans_not_read", {"count": 3})],
            "3 pages hold no text and wait for the image reader.",
        ),
        (
            [("scans_not_read", {"count": 1})],
            "1 page holds no text and waits for the image reader.",
        ),
        (
            [("text_limit", {"from": 9, "to": 9}), ("scans_not_read", {"count": 2})],
            "Only the first 500,000 characters were read. Page 9 was not read. "
            "2 pages hold no text and wait for the image reader.",
        ),
    ],
)
def test_a_note_is_written_in_plain_words(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    notes: list[tuple[str, dict[str, int]]],
    sentence: str,
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    answer = parsed(truncated=True, notes=tuple(pc.Note(code, values) for code, values in notes))
    assert run(factory, clock, Parser(answer), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.note, stored.truncated) == (sentence, True)
    assert listing(api, alice)["items"][0]["note"] == sentence
    for dash in ("\u2014", "\u2013", " - "):
        assert dash not in sentence


def test_every_code_of_the_parser_has_a_sentence_without_a_dash() -> None:
    written = {**jobs.FILE_SENTENCES, "server": jobs.SERVER_FAILED, "quota": jobs.OVER_QUOTA}
    # too_many_pixels is the image reader's (G8): until its sentence exists, the fallback.
    assert set(jobs.FILE_SENTENCES) == pc.FILE_CODES - {"too_many_pixels"}
    assert jobs.file_sentence("too_many_pixels") == jobs.FILE_FALLBACK
    for code in pc.NOTE_CODES:
        written[code] = jobs.note_sentence(pc.Note(code, {"from": 2, "to": 3, "count": 4}))
    assert jobs.note_sentence(pc.Note("a_later_code", {})) == "A part of this file was not read."
    for code, sentence in written.items():
        assert sentence.endswith("."), code
        for dash in ("\u2014", "\u2013", " - "):
            assert dash not in sentence, code
    assert "protected in a way this version cannot open" in jobs.FILE_SENTENCES["encryption"]


def test_a_text_that_looks_like_it_holds_a_key_sets_the_warning(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    text = f"Deploy notes.\faws_access_key_id = {secret_like()}"
    assert secrets.scan(text)
    assert run(factory, clock, Parser(parsed(text)), body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.secret_warning, stored.secret_confirmed_at) == (True, None)
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["secret_warning"]) == ("done", True)


def test_a_text_larger_than_what_is_left_of_the_quota_fails_the_document(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small PDF can hold a long text: the quota is asked again when the text arrives."""
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 10_000)
    uploaded(api, alice, b"x" * 6_000, "notes.txt")
    body = uploaded(api, alice, pdf(3_000), "cv.pdf")
    long_text = "word " * 1_000  # 5,000 bytes: 6,000 + 5,000 is over, though 6,000 + 3,000 was not
    assert run(factory, clock, Parser(parsed(long_text)), body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert (stored.failed_reason, stored.text, stored.raw) == (jobs.OVER_QUOTA, None, None)
    assert listing(api, alice)["usage"]["bytes"] == 6_000
    # A text that fits is kept: what counts is the user's other documents plus the text.
    other = uploaded(api, alice, pdf(3_001), "cv2.pdf")
    assert run(factory, clock, Parser(parsed("word " * 700)), other["id"]) == jobs.DONE
    assert listing(api, alice)["usage"]["bytes"] == 6_000 + 3_500


def test_a_result_without_text_is_not_a_text(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    blank = uploaded(api, alice, pdf(100), "blank.pdf")
    assert run(factory, clock, Parser(parsed(" \n\f\t ")), blank["id"]) == jobs.FAILED
    stored = row(factory, blank["id"])
    assert (stored.failed_kind, stored.failed_reason) == (
        FailedKind.file,
        jobs.FILE_SENTENCES["no_text"],
    )
    # No text at all in an answer that says ok: the parser's fault, tried again later.
    odd = uploaded(api, alice, pdf(101), "odd.pdf")
    assert run(factory, clock, Parser(pc.Parsed(text=None)), odd["id"]) == jobs.RETRY
    assert row(factory, odd["id"]).text_status is TextStatus.pending


# --- failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(jobs.FILE_SENTENCES))
def test_a_failure_of_the_file_is_final_and_says_why(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, code: str
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(file_refusal(code, count=4))
    assert run(factory, clock, parser, body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert stored.failed_reason == jobs.FILE_SENTENCES[code]
    assert (stored.raw, stored.text, stored.pending_since, stored.attempts) == (None, None, None, 1)
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["failed_reason"]) == ("failed", jobs.FILE_SENTENCES[code])
    assert listing(api, alice)["usage"]["bytes"] == 0
    # Final: nothing queues it again, and a job that comes anyway does nothing.
    clock.advance(hours=1)
    assert jobs.reap(factory, clock.now).queue == []
    assert run(factory, clock, parser, body["id"]) == jobs.SKIPPED
    assert len(parser.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        pc.ParserDownError("unreachable"),
        pc.ParserBusyError("busy"),
        pc.ParserTimeoutError("deadline"),
        pc.ParserTimeoutError("cpu_limit"),
        pc.ParserMalformedError("form_feeds"),
        pc.ParserRefusedError("memory_limit"),
        pc.ParserRefusedError("crashed"),
    ],
)
def test_a_failure_of_the_server_is_tried_once_more_then_failed(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, error: pc.ParserError
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(error)
    clock.advance(seconds=1)
    assert run(factory, clock, parser, body["id"]) == jobs.RETRY
    stored = row(factory, body["id"])
    # Still pending, with its bytes, marked: the page goes on showing "Being read".
    assert (stored.text_status, stored.failed_kind) == (TextStatus.pending, FailedKind.server)
    assert (stored.raw, stored.attempts, stored.failed_reason) == (pdf(), 1, None)
    assert stored.pending_since == clock.now
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["readable"], item["failed_reason"]) == ("pending", True, None)
    # Not before RETRY_AFTER: the parser gets the time to come back.
    clock.advance(seconds=jobs.RETRY_AFTER.total_seconds() - 1)
    assert jobs.reap(factory, clock.now).queue == []
    clock.advance(seconds=1)
    due = jobs.reap(factory, clock.now).queue
    assert due == [(stored.id, queues.document_job_id(stored.id, 1, stored.pending_since))]
    assert run(factory, clock, parser, body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.server)
    assert (stored.failed_reason, stored.raw, stored.attempts) == (jobs.SERVER_FAILED, None, 2)
    assert len(parser.calls) == 2
    clock.advance(hours=1)
    assert jobs.reap(factory, clock.now) == jobs.Reaped(queue=[], failed=0)


def test_the_second_try_can_succeed(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(pc.ParserDownError("unreachable"), parsed())
    assert run(factory, clock, parser, body["id"]) == jobs.RETRY
    clock.advance(seconds=31)
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.text, stored.failed_kind, stored.attempts) == (TEXT, None, 2)


def test_a_failed_document_is_replaced_by_a_new_upload_and_read_again(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, jobs_queue: RecordingQueue
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    assert run(factory, clock, Parser(file_refusal("protected")), body["id"]) == jobs.FAILED
    clock.advance(minutes=1)
    again = upload(api, alice, pdf(), "cv.pdf")
    assert (again.status_code, again.json()["id"], again.json()["status"]) == (
        201,
        body["id"],
        "pending",
    )
    assert len(jobs_queue.jobs) == 2
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.DONE
    assert row(factory, body["id"]).text == TEXT


def test_a_job_for_a_document_that_is_gone_or_not_to_read_does_nothing(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    parser = Parser(parsed())
    assert run(factory, clock, parser, str(uuid.uuid4())) == jobs.GONE
    text = uploaded(api, alice, b"plain text", "notes.txt")
    image = uploaded(api, alice, png(), "shot.png")
    done = uploaded(api, alice, pdf(), "cv.pdf")
    assert run(factory, clock, parser, done["id"]) == jobs.DONE
    for document in (text, image, done):
        assert run(factory, clock, parser, document["id"]) == jobs.SKIPPED
    assert len(parser.calls) == 1
    assert row(factory, image["id"]).raw == png()
    assert run(factory, clock, parser, "not-a-uuid") == jobs.ERROR


def test_a_pending_document_without_its_bytes_is_failed(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    set_columns(factory, body["id"], raw=None)
    parser = Parser(parsed())
    assert run(factory, clock, parser, body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.server)
    assert parser.calls == []


# --- the races: a deletion or a replacement during the reading wins -----------------------------


def test_a_document_deleted_while_it_is_read_gets_no_text(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(parsed())
    parser.during = lambda: delete(api, alice, body["id"]).raise_for_status()
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    assert rows(factory) == []
    assert listing(api, alice)["usage"] == {"bytes": 0, "documents": 0}


def test_a_document_deleted_and_added_again_while_it_is_read_is_another_document(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(parsed("the text of the first upload"))
    added: list[dict] = []

    def delete_and_add() -> None:
        delete(api, alice, body["id"]).raise_for_status()
        clock.advance(seconds=1)
        added.append(uploaded(api, alice, pdf(), "cv.pdf"))

    parser.during = delete_and_add
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    (stored,) = rows(factory)
    assert str(stored.id) == added[0]["id"] != body["id"]
    assert (stored.text, stored.text_status, stored.raw) == (None, TextStatus.pending, pdf())
    assert stored.attempts == 0


def test_a_document_replaced_while_it_is_read_keeps_the_new_upload(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """The row fails meanwhile (another job, the reaper) and the user adds the file again: the
    same row, a new upload. The job that was reading the old one writes nothing."""
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(parsed("the text of the first upload"))

    def fail_and_replace() -> None:
        set_columns(factory, body["id"], text_status=TextStatus.failed, failed_reason="lost")
        clock.advance(seconds=5)
        again = upload(api, alice, pdf(), "cv.pdf")
        assert (again.status_code, again.json()["id"]) == (201, body["id"])

    parser.during = fail_and_replace
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    stored = row(factory, body["id"])
    assert (stored.text, stored.text_status, stored.attempts) == (None, TextStatus.pending, 0)
    assert stored.raw == pdf()
    assert stored.pending_since == clock.now
    # And the same for a failure the old job would have recorded.
    parser = Parser(file_refusal("protected"))
    parser.during = fail_and_replace
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    assert row(factory, body["id"]).text_status is TextStatus.pending


def test_the_write_waits_for_a_deletion_in_flight_and_then_finds_no_row(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """Two connections for real: the deletion holds the row when the job comes to write. The
    job waits for it, then finds nothing to write to."""
    body = uploaded(api, alice, pdf(), "cv.pdf")
    owner = user_id(factory, ALICE)
    deleting = factory()
    assert documents.delete_document(deleting, owner, uuid.UUID(body["id"])) is True
    parser = Parser(parsed())
    # The claim happened before the deletion started: play the write alone.
    claim = jobs._Claim(
        document_id=uuid.UUID(body["id"]),
        user_id=owner,
        kind=documents.Kind.pdf,
        size=len(pdf()),
        attempts=0,
        pending_since=clock.now,
        data=b"",
    )
    outcome: list[str] = []
    writer = threading.Thread(
        target=lambda: outcome.append(jobs._store_text(factory, claim, parser.answers[0], 0.0))
    )
    writer.start()
    writer.join(0.5)
    assert writer.is_alive(), "the write waits for the row the deletion holds"
    deleting.commit()
    deleting.close()
    writer.join(10)
    assert outcome == [jobs.SUPERSEDED]
    assert rows(factory) == []


def test_two_jobs_for_one_document_read_it_once(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """A second job that comes while the first holds the row (the reaper queued it twice, a
    worker restarted) answers busy and sends nothing to the parser."""
    body = uploaded(api, alice, pdf(), "cv.pdf")
    second = Parser(parsed("from the second job"))
    answers: list[str] = []
    first = Parser(parsed("from the first job"))
    first.during = lambda: answers.append(run(factory, clock, second, body["id"]))
    assert run(factory, clock, first, body["id"]) == jobs.DONE
    assert answers == [jobs.BUSY]
    assert second.calls == []
    assert row(factory, body["id"]).text == "from the first job"


def test_a_claim_older_than_the_longest_job_is_taken_over(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """The worker died while it read: nothing recorded, the claim stays. After STALE_AFTER the
    reaper queues the document again; the dead job's write, should it ever come, is refused."""
    body = uploaded(api, alice, pdf(), "cv.pdf")
    dead = jobs._claim(factory, uuid.UUID(body["id"]), clock.now)
    assert isinstance(dead, jobs._Claim)
    assert (dead.attempts, dead.data) == (1, pdf())
    clock.advance(seconds=jobs.STALE_AFTER.total_seconds() - 1)
    assert jobs.reap(factory, clock.now).queue == []
    assert run(factory, clock, Parser(parsed()), body["id"]) == jobs.BUSY
    clock.advance(seconds=1)
    assert [job[0] for job in jobs.reap(factory, clock.now).queue] == [dead.document_id]
    assert run(factory, clock, Parser(parsed("second try")), body["id"]) == jobs.DONE
    assert jobs._store_text(factory, dead, parsed("from the dead job"), 0.0) == jobs.SUPERSEDED
    assert row(factory, body["id"]).text == "second try"


def test_a_document_whose_last_try_never_came_back_is_failed_by_the_reaper(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(), "cv.pdf")
    set_columns(factory, body["id"], attempts=jobs.MAX_ATTEMPTS)
    assert jobs.reap(factory, clock.now) == jobs.Reaped(queue=[], failed=0), "not stale yet"
    clock.advance(seconds=jobs.STALE_AFTER.total_seconds())
    assert jobs.reap(factory, clock.now) == jobs.Reaped(queue=[], failed=1)
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.server)
    assert (stored.failed_reason, stored.raw) == (jobs.SERVER_FAILED, None)
    assert jobs.reap(factory, clock.now) == jobs.Reaped(queue=[], failed=0)


# --- the reaper ---------------------------------------------------------------------------------


def stored_before(
    factory: sessionmaker[Session], owner: uuid.UUID, data: bytes, mime: str, since: datetime
) -> uuid.UUID:
    """A row as G1 stored it before its kind had a reader: pending, with its bytes, never
    queued."""
    import hashlib

    with factory() as db, db.begin():
        document = IngestionDocument(
            user_id=owner,
            name="kept for later",
            mime=mime,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            text_source="parsed",
            text_status=TextStatus.pending,
            parser_version=1,
            pending_since=since,
            created_at=since,
        )
        db.add(document)
        db.flush()
        db.execute(
            sa.update(IngestionDocument).where(IngestionDocument.id == document.id).values(raw=data)
        )
        return document.id


def test_the_reaper_takes_the_kinds_that_have_a_reader_and_no_other(
    alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    owner = user_id(factory, ALICE)
    long_ago = clock.now - timedelta(days=30)
    old_pdf = stored_before(factory, owner, pdf(900), "application/pdf", long_ago)
    kept = [
        stored_before(factory, owner, png(), "image/png", long_ago),
        stored_before(factory, owner, png(300), "image/jpeg", long_ago),
        stored_before(factory, owner, archive(), "application/zip", long_ago),
        stored_before(factory, owner, archive(b"word/x"), documents.DOCX_MIME, long_ago),
        stored_before(factory, owner, b"other", "application/x-later", long_ago),
    ]
    assert jobs.readable_mimes() == ["application/pdf"]
    parser = Parser(parsed())
    for _ in range(10):
        found = jobs.reap(factory, clock.now)
        assert found.failed == 0
        assert [job[0] for job in found.queue] in ([old_pdf], [])
        for document_id, _ in found.queue:
            assert run(factory, clock, parser, str(document_id)) == jobs.DONE
        clock.advance(minutes=10)
    assert len(parser.calls) == 1, "the PDF, once"
    assert row(factory, str(old_pdf)).text == TEXT
    for document_id in kept:
        waiting = row(factory, str(document_id))
        assert waiting.text_status is TextStatus.pending
        assert waiting.raw is not None
        assert (waiting.attempts, waiting.failed_kind, waiting.failed_reason) == (0, None, None)
        assert waiting.pending_since == long_ago
        # And a job that named one of them by mistake leaves it alone too.
        assert run(factory, clock, parser, str(document_id)) == jobs.SKIPPED
    assert len(parser.calls) == 1


def test_the_reaper_queues_the_oldest_first_a_batch_at_a_time(
    alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    owner = user_id(factory, ALICE)
    ids = [
        stored_before(
            factory, owner, pdf(100 + n), "application/pdf", clock.now - timedelta(hours=n)
        )
        for n in range(7)
    ]
    assert jobs.REAP_BATCH == 50
    first = jobs.reap(factory, clock.now, batch=3)
    assert [job[0] for job in first.queue] == ids[:3:-1][:3] == [ids[6], ids[5], ids[4]]
    # Until a job claims them they are found again: queueing twice is one job (the same id).
    assert jobs.reap(factory, clock.now, batch=3) == first
    for document_id, _ in first.queue:
        assert run(factory, clock, Parser(parsed()), str(document_id)) == jobs.DONE
    assert [job[0] for job in jobs.reap(factory, clock.now, batch=3).queue] == [
        ids[3],
        ids[2],
        ids[1],
    ]
    assert len(jobs.reap(factory, clock.now).queue) == 4


def test_the_reaper_queues_a_pending_row_without_a_date_and_ids_tell_tries_apart(
    alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    owner = user_id(factory, ALICE)
    document_id = stored_before(factory, owner, pdf(), "application/pdf", clock.now)
    set_columns(factory, str(document_id), pending_since=None, attempts=1)
    assert jobs.reap(factory, clock.now).queue == [(document_id, f"document:{document_id}:1:0")]
    assert run(factory, clock, Parser(parsed()), str(document_id)) == jobs.DONE
    later = clock.now + timedelta(seconds=1)
    assert queues.document_job_id(document_id, 0, clock.now) != queues.document_job_id(
        document_id, 0, later
    )
    assert queues.document_job_id(document_id, 0, clock.now) != queues.document_job_id(
        document_id, 1, clock.now
    )


def test_the_reaper_cron_queues_through_redis_and_counts_what_was_accepted(
    alice: str, factory: sessionmaker[Session], clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    owner = user_id(factory, ALICE)
    ids = [
        stored_before(factory, owner, pdf(200 + n), "application/pdf", clock.now) for n in range(3)
    ]

    class Pool:
        def __init__(self) -> None:
            self.seen: list[tuple] = []

        async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object | None:
            self.seen.append((function, args, kwargs["_queue_name"], kwargs["_job_id"]))
            # Redis already holds the job of the second document: refused, not counted.
            return None if args == (str(sorted(ids)[1]),) else object()

    pool = Pool()
    ctx = {"sessions": factory, "clock": clock, "redis": pool}
    with caplog.at_level("INFO", logger="app.ingestion"):
        assert asyncio.run(jobs.reap_documents(ctx)) == 2
    assert sorted(call[1][0] for call in pool.seen) == sorted(str(i) for i in ids)
    assert {call[0] for call in pool.seen} == {"parse_document"}
    assert {call[2] for call in pool.seen} == {queues.INGESTION}
    reaped = [r for r in caplog.records if r.getMessage() == "documents reaped"]
    assert [(r.queued, r.failed) for r in reaped] == [(2, 0)]
    # Nothing to do: nothing logged.
    for document_id in ids:
        set_columns(factory, str(document_id), text_status=TextStatus.done)
    caplog.clear()
    with caplog.at_level("INFO", logger="app.ingestion"):
        assert asyncio.run(jobs.reap_documents(ctx)) == 0
    assert caplog.records == []


# --- the rate of readings -----------------------------------------------------------------------


class Ticks:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_eleventh_pdf_of_a_minute_is_429_with_the_wait(
    app: FastAPI, api: Api, alice: str, bob: str, jobs_queue: RecordingQueue
) -> None:
    ticks = Ticks()
    limit = documents_router.READS_PER_MINUTE
    assert limit == 10
    app.state.ingestion_read_rate = RateLimiter(limit, documents_router.READS_WINDOW_S, ticks)
    for n in range(limit):
        ticks.now += 2
        assert upload(api, alice, pdf(500 + n), f"cv{n}.pdf").status_code == 201
    refused = upload(api, alice, pdf(999), "one too many.pdf")
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "42"  # 60 s after the first, taken 18 s ago
    assert refused.headers["cache-control"] == "no-store"
    assert "connection" not in refused.headers, "the body was read whole: the answer arrives"
    assert refused.json() == {
        "detail": "You added 10 files to read in less than a minute, the most allowed. "
        "Wait 42 seconds, then add this one again."
    }
    assert len(listing(api, alice)["items"]) == limit, "nothing stored"
    assert len(jobs_queue.jobs) == limit, "nothing queued"
    # What costs no reading is not counted and not refused: a text, an image kept for later, a
    # type that is refused for itself.
    assert upload(api, alice, b"plain text", "notes.txt").status_code == 201
    assert upload(api, alice, png(), "shot.png").status_code == 201
    assert upload(api, alice, b"GIF89a....", "a.gif").status_code == 415
    # Per user: another account is not slowed down.
    assert upload(api, bob, pdf(999), "cv.pdf").status_code == 201
    # A refused upload is not counted: once the window has moved on, the next one passes.
    ticks.now += 41
    assert upload(api, alice, pdf(999), "one too many.pdf").status_code == 429
    ticks.now += 1.5
    assert upload(api, alice, pdf(999), "one too many.pdf").status_code == 201


def test_a_refusal_for_the_rate_is_logged_as_a_rule_and_the_gate_is_given_back(
    app: FastAPI, api: Api, alice: str, caplog: pytest.LogCaptureFixture
) -> None:
    app.state.ingestion_read_rate = RateLimiter(1, 60.0)
    assert upload(api, alice, pdf(), "a.pdf").status_code == 201
    with caplog.at_level("INFO", logger="app.ingestion"):
        assert upload(api, alice, pdf(700), "b.pdf").status_code == 429
    refused = [r for r in caplog.records if r.getMessage() == "document refused"]
    assert [(r.rule, r.status, r.size) for r in refused] == [("upload-rate", 429, 700)]
    gate = documents_router._gate(app)
    assert (gate.bodies, gate.slots._value) == (0, documents_router.UPLOAD_SLOTS)
    # The cap on waiting bodies of G1 is still there, next to the rate.
    assert documents_router.WAITING_MAX == 2


# --- privacy ------------------------------------------------------------------------------------


def test_nothing_of_a_document_reaches_a_log_a_return_value_or_an_error(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = f"name-{uuid.uuid4().hex}"
    words = f"words-{uuid.uuid4().hex}"
    secret = secret_like()
    text = f"{words}\fkey = {secret} {words}"
    returned: list[str] = []
    with caplog.at_level("DEBUG"):
        done = uploaded(api, alice, pdf(300) + words.encode(), f"{name}.pdf")
        returned.append(run(factory, clock, Parser(parsed(text)), done["id"]))
        failed = uploaded(api, alice, pdf(301) + words.encode(), f"{name}-2.pdf")
        returned.append(run(factory, clock, Parser(file_refusal("protected")), failed["id"]))
        retried = uploaded(api, alice, pdf(302) + words.encode(), f"{name}-3.pdf")
        down = Parser(pc.ParserDownError("unreachable"))
        returned.append(run(factory, clock, down, retried["id"]))
        clock.advance(minutes=1)
        returned.append(run(factory, clock, down, retried["id"]))
        # An unexpected error whose message quotes the document: only its type is logged.
        broken = uploaded(api, alice, pdf(303) + words.encode(), f"{name}-4.pdf")

        def explode(*_: object, **__: object) -> None:
            raise RuntimeError(f"could not store {text!r} of {name}")

        with monkeypatch.context() as patch:
            patch.setattr(jobs, "_store_text", explode)
            returned.append(run(factory, clock, Parser(parsed(text)), broken["id"]))
        jobs.reap(factory, clock.now)
    assert returned == [jobs.DONE, jobs.FAILED, jobs.RETRY, jobs.FAILED, jobs.ERROR]
    logged = caplog.text + "".join(
        json.dumps({k: str(v) for k, v in vars(record).items()}) for record in caplog.records
    )
    for private in (name, words, secret, "key ="):
        assert private not in logged
        assert all(private not in word for word in returned)
    events = [
        (record.getMessage(), getattr(record, "code", None))
        for record in caplog.records
        if record.name == "app.ingestion" and "parse" in record.getMessage()
    ]
    assert events == [
        ("document parsed", None),
        ("document parse failed", "protected"),
        ("document parse failed", "unreachable"),
        ("document parse failed", "unreachable"),
        ("document parse job failed", None),
    ]
    stored = next(r for r in caplog.records if r.getMessage() == "document parsed")
    assert (stored.characters, stored.pages, stored.pages_text) == (len(text), 2, 2)
    assert stored.secret_rules == secrets.scan(text) != []
    assert stored.document_id == done["id"]
    error = next(r for r in caplog.records if r.getMessage() == "document parse job failed")
    assert (error.error, error.document_id) == ("RuntimeError", broken["id"])
    assert error.where.startswith("test_jobs.py:")
    assert error.exc_info is None, "no traceback: it would print the message"
    # The broken one is still the reaper's to take up again.
    assert row(factory, broken["id"]).text_status is TextStatus.pending


def test_a_database_error_while_storing_names_no_value(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A text the column refuses (longer than its CHECK allows: the client's own cap makes this
    impossible, the test forces it): the error that comes back from Postgres is logged as its
    type, and the engine hides the parameters of the statement."""
    words = f"words-{uuid.uuid4().hex}"
    body = uploaded(api, alice, pdf(), "cv.pdf")
    too_long = parsed((words + " ") * (TEXT_MAX_CHARS // len(words)))
    assert len(too_long.text) > TEXT_MAX_CHARS
    with caplog.at_level("DEBUG"):
        assert run(factory, clock, Parser(too_long), body["id"]) == jobs.ERROR
    logged = caplog.text + "".join(
        json.dumps({k: str(v) for k, v in vars(record).items()}) for record in caplog.records
    )
    assert words not in logged
    error = next(r for r in caplog.records if r.getMessage() == "document parse job failed")
    assert error.error == "IntegrityError"
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text, stored.raw) == (TextStatus.pending, None, pdf())


def test_the_job_holds_one_document_at_most_and_frees_it_before_it_writes(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, pdf(2 * MIB), "cv.pdf")
    claim = jobs._claim(factory, uuid.UUID(body["id"]), clock.now)
    assert isinstance(claim, jobs._Claim)
    assert len(claim.data) == 2 * MIB
    assert (claim.kind, claim.size, claim.attempts) == (documents.Kind.pdf, 2 * MIB, 1)
    assert {documents.Kind.pdf: "pdf_text"} == jobs.PARSER_KIND
    assert jobs.JOB_TIMEOUT_S > pc.TIMEOUT_S + 30
    assert timedelta(seconds=jobs.JOB_TIMEOUT_S) < jobs.STALE_AFTER
