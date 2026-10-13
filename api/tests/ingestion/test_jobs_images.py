"""Images and scanned pages through the parse job (app/ingestion/jobs.py, task G8) against a
real Postgres: an uploaded PNG or JPEG becomes one normalised page image, the scanned pages of
a PDF become one image each at their page index, and the document then waits for its
transcription. The images of one document are written in one transaction with its state, under
the guard of every other reading: a document deleted or replaced while it was rendered gets
nothing.

The parser is played by the real handlers (sandbox/image.py, sandbox/pdf_text.py and
sandbox/pdf_render.py), called in the test process and read through the client's own checks:
everything but the socket and the lockdown, which tests/ingestion/parser/test_image_container.py
adds.
"""

import io
import logging
import re
import sys
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.ingestion import documents, jobs
from app.ingestion import parser_client as pc
from app.ingestion.models import FailedKind, IngestionImage, TextSource, TextStatus
from tests.identity.api_helpers import Api, Clock
from tests.ingestion import image_fixtures as pictures
from tests.ingestion import scan_fixtures as scans
from tests.ingestion.conftest import ALICE, RecordingQueue
from tests.ingestion.helpers import (
    count,
    delete,
    listing,
    row,
    set_columns,
    text_of,
    upload,
    uploaded,
    user_id,
)
from tests.ingestion.parser.helpers import Plenty, sandbox_handler, sandbox_module
from tests.ingestion.test_jobs import Parser, run, stored_before

main = sandbox_module("main")
# pdf_render.py imports pdf_text.py from beside it, as it does in the child.
sys.modules.setdefault("pdf_text", sandbox_handler("pdf_text"))


class RealParser(Parser):
    """The real readers behind the client's checks, remembering what they were asked. `instead`
    replaces the answer of the call of that rank (from 0) by a result or an error, and `during`
    runs while the call of rank `at` is out."""

    def __init__(self, instead: dict[int, Any] | None = None, at: int = 0) -> None:
        super().__init__(None)
        self.instead = instead or {}
        self.at = at
        self.options: list[dict[str, Any]] = []

    async def __call__(self, kind: str, data: bytes, options: Any = None) -> pc.Parsed:
        rank = len(self.calls)
        self.calls.append((kind, data))
        self.options.append(dict(options or {}))
        if self.during is not None and rank == self.at:
            self.during()
        if rank in self.instead:
            answer = self.instead[rank]
            if isinstance(answer, BaseException):
                raise answer
            return answer
        found = sandbox_handler(kind).handle(data, dict(options or {}), Plenty())
        return pc.check_parsed(pc.loads(main.encode(found)), kind)


def kinds(parser: Parser) -> list[str]:
    return [kind for kind, _ in parser.calls]


def images(factory: sessionmaker[Session], document_id: str) -> list[IngestionImage]:
    with factory() as db:
        return list(
            db.execute(
                sa.select(IngestionImage)
                .options(undefer(IngestionImage.jpeg), undefer(IngestionImage.text))
                .where(IngestionImage.document_id == document_id)
                .order_by(IngestionImage.position)
            ).scalars()
        )


def picture_of(image: IngestionImage) -> Image.Image:
    return Image.open(io.BytesIO(image.jpeg))


def image_of(api: Api, token: str | None, document_id: str, position: int) -> Any:
    return api.get(f"/api/ingestion/documents/{document_id}/images/{position}", token=token)


# --- an uploaded image --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name"),
    [(pictures.screenshot_png(), "shot.png"), (pictures.photo_jpeg(), "photo.jpg")],
    ids=["png", "jpeg"],
)
def test_an_uploaded_image_is_queued_and_becomes_one_page_image(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    jobs_queue: RecordingQueue,
    clock: Clock,
    data: bytes,
    name: str,
) -> None:
    body = uploaded(api, alice, data, name)
    assert (body["status"], body["readable"], body["kind"]) == ("pending", True, "image")
    assert [job["args"] for job in jobs_queue.jobs] == [(body["id"],)]
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["image"]
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text_source) == (
        TextStatus.awaiting_transcription,
        TextSource.transcribed,
    )
    assert (stored.text, stored.raw, stored.note, stored.truncated) == ("", None, None, False)
    assert (stored.pages, stored.pages_text, stored.pages_image) == (1, 0, 1)
    assert (stored.parser_version, stored.pending_since) == (documents.PARSER_VERSION, None)
    assert (stored.failed_kind, stored.failed_reason, stored.attempts) == (None, None, 1)
    (image,) = images(factory, body["id"])
    assert (image.position, image.user_id, image.text) == (0, stored.user_id, None)
    assert 0 < len(image.jpeg) <= 300_000
    picture = picture_of(image)
    assert (picture.format, picture.mode, picture.size) == ("JPEG", "RGB", (1200, 800))
    assert (image.width, image.height) == picture.size
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["source"], item["pages_text"], item["pages_image"]) == (
        "awaiting_transcription",
        "transcribed",
        0,
        1,
    )


def test_the_stored_page_images_are_shown_to_their_owner_and_to_nobody_else(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, scans.scan(2), "scan.pdf")
    assert run(factory, clock, RealParser(), body["id"]) == jobs.DONE
    preview = text_of(api, alice, body["id"]).json()
    assert preview["text"] == "\f"
    stored = images(factory, body["id"])
    assert preview["images"] == [
        {"position": image.position, "width": image.width, "height": image.height}
        for image in stored
    ]
    assert [image.position for image in stored] == [0, 1]
    answer = image_of(api, alice, body["id"], 1)
    assert (answer.status_code, answer.content) == (200, stored[1].jpeg)
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["content-security-policy"] == "default-src 'none'; sandbox"
    # Another user, nobody, a page without an image, an id that is none: the same 404.
    for refused in (
        image_of(api, bob, body["id"], 0),
        image_of(api, alice, body["id"], 2),
        image_of(api, alice, "not-an-id", 0),
    ):
        assert (refused.status_code, refused.headers["cache-control"]) == (404, "no-store")
    assert image_of(api, None, body["id"], 0).status_code == 401
    assert image_of(api, alice, body["id"], 200).status_code == 422
    assert text_of(api, bob, body["id"]).status_code == 404


def test_an_image_over_the_pixel_limit_is_failed_with_its_reason(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    # 5,001 by 5,000: one line of pixels over 25 megapixels, read from the header alone.
    body = uploaded(api, alice, pictures.blank_png(5001, 5000), "poster.png")
    assert run(factory, clock, RealParser(), body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert stored.failed_reason == jobs.IMAGE_SENTENCES["too_many_pixels"]
    assert (stored.raw, stored.text) == (None, None)
    assert count(factory, IngestionImage) == 0
    assert listing(api, alice)["items"][0]["failed_reason"].startswith("This image is too large")


def test_every_sentence_about_an_image_is_plain_words_without_a_dash() -> None:
    sentences = [
        *jobs.IMAGE_SENTENCES.values(),
        jobs.FILE_SENTENCES["no_text"],
        *(
            jobs.note_sentence(pc.Note(code, {"count": n}))
            for code in ("scans_over_cap", "pages_not_rendered", "render_time_limit")
            for n in (1, 3)
        ),
    ]
    for sentence in sentences:
        assert not re.search("[‒-―]| - ", sentence), sentence
        assert sentence.endswith(".")
    # Every code the image reader answers has its own sentence.
    assert set(jobs.IMAGE_SENTENCES) == {"unreadable", "too_many_pixels", "bomb", "too_slow"}
    assert jobs.file_sentence("too_many_pixels", documents.Kind.image).startswith("This image")


# --- the scanned pages of a PDF -----------------------------------------------------------------


def test_a_scanned_pdf_becomes_one_image_per_page_in_one_document(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, scans.scan(2), "scan.pdf")
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["pdf_text", "pdf_render"]
    assert parser.options[1] == {"pages": [0, 1], "check": [], "limit": 8}
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text_source) == (
        TextStatus.awaiting_transcription,
        TextSource.transcribed,
    )
    assert (stored.text, stored.raw, stored.note, stored.truncated) == ("\f", None, None, False)
    assert (stored.pages, stored.pages_text, stored.pages_image) == (2, 0, 2)
    found = images(factory, body["id"])
    assert [image.position for image in found] == [0, 1]
    for image in found:
        picture = picture_of(image)
        assert (picture.format, picture.size) == ("JPEG", (image.width, image.height))
        assert max(picture.size) == 1568
        assert len(image.jpeg) <= 300_000


def test_the_images_of_a_mixed_pdf_sit_at_their_page_indexes(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """A text, a scan, a text, a scan under a short footer, a short text beside a small logo:
    pages 1 and 3 become images, by the first trigger and by the second."""
    body = uploaded(api, alice, scans.mixed(), "mixed.pdf")
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["pdf_text", "pdf_render"]
    assert parser.options[1] == {"pages": [1, 3, 4], "check": [3, 4], "limit": 8}
    stored = row(factory, body["id"])
    # The parser read three pages: the text is its own, G14 lists the pages a model wrote.
    assert (stored.text_status, stored.text_source) == (
        TextStatus.awaiting_transcription,
        TextSource.parsed,
    )
    assert (stored.pages, stored.pages_text, stored.pages_image) == (5, 3, 2)
    slots = stored.text.split("\f")
    assert [bool(slot) for slot in slots] == [True, False, True, False, True]
    assert scans.FOOTER not in stored.text, "the image holds the page, its footer included"
    assert [image.position for image in images(factory, body["id"])] == [1, 3]
    assert (stored.note, stored.truncated, stored.raw) == (None, False, None)


def test_a_pdf_of_text_never_stores_an_image(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, scans.scan_pdf([scans.Sheet(text=scans.LONG)]), "text.pdf")
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["pdf_text"], "every page has a long text: the renderer is not asked"
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text_source) == (TextStatus.done, TextSource.parsed)
    assert (stored.pages_text, stored.pages_image) == (1, 0)
    assert count(factory, IngestionImage) == 0


def test_only_thirty_scanned_pages_are_kept_and_the_note_names_the_rest(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, scans.scan(31), "long scan.pdf")
    parser = RealParser()
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    assert kinds(parser) == ["pdf_text", *["pdf_render"] * 4]
    assert [options["limit"] for options in parser.options[1:]] == [8, 8, 8, 6]
    assert [options["pages"][0] for options in parser.options[1:]] == [0, 8, 16, 24]
    stored = row(factory, body["id"])
    assert (stored.pages, stored.pages_text, stored.pages_image) == (31, 0, 30)
    assert stored.text == "\f" * 30
    assert stored.truncated is True
    assert stored.note == (
        "Only the first 30 scanned pages of a PDF are kept as images. 1 scanned page was left out."
    )
    found = images(factory, body["id"])
    assert [image.position for image in found] == list(range(30))
    # What a scan of 30 pages costs its owner, against the quota (assumption A18).
    with factory() as db:
        used = documents.usage(db, user_id(factory, ALICE))
    assert used.bytes == sum(len(image.jpeg) for image in found) + len(stored.text)
    assert used.bytes <= 30 * 300_000 + 30


# --- one transaction, under the guard -----------------------------------------------------------


@pytest.mark.parametrize("at", [0, 2], ids=["while its text is read", "between two batches"])
def test_a_document_deleted_while_it_is_rendered_stores_nothing(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, at: int
) -> None:
    body = uploaded(api, alice, scans.scan(9), "scan.pdf")
    parser = RealParser(at=at)
    parser.during = lambda: delete(api, alice, body["id"])
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    assert kinds(parser) == ["pdf_text", "pdf_render", "pdf_render"]
    assert listing(api, alice)["items"] == []
    assert count(factory, IngestionImage) == 0


def test_a_document_replaced_while_it_is_rendered_keeps_the_new_upload(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    data = scans.scan(2)
    body = uploaded(api, alice, data, "scan.pdf")
    parser = RealParser(at=1)

    def replace() -> None:
        # The first try failed in the meantime, and the user added the file again.
        set_columns(factory, body["id"], text_status=TextStatus.failed, raw=None)
        clock.advance(seconds=5)
        assert upload(api, alice, data, "scan.pdf").status_code == 201

    parser.during = replace
    assert run(factory, clock, parser, body["id"]) == jobs.SUPERSEDED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text, stored.raw) == (TextStatus.pending, None, data)
    assert count(factory, IngestionImage) == 0
    # The job of the new upload reads it.
    assert run(factory, clock, RealParser(), body["id"]) == jobs.DONE
    assert [image.position for image in images(factory, body["id"])] == [0, 1]


@pytest.mark.parametrize(
    ("error", "outcome", "kind"),
    [
        (pc.ParserRefusedError("memory_limit"), jobs.RETRY, None),
        (pc.ParserTimeoutError("timeout"), jobs.RETRY, None),
        (pc.ParserRefusedError("bomb", failure="file"), jobs.FAILED, FailedKind.file),
    ],
    ids=["the child killed", "the wall limit", "the file's fault"],
)
def test_a_batch_that_fails_drops_the_batches_before_it(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    error: pc.ParserError,
    outcome: str,
    kind: FailedKind | None,
) -> None:
    """One rule: the images of a document are all written or none is, whatever batch failed."""
    body = uploaded(api, alice, scans.scan(9), "scan.pdf")
    parser = RealParser(instead={2: error})
    assert run(factory, clock, parser, body["id"]) == outcome
    assert kinds(parser) == ["pdf_text", "pdf_render", "pdf_render"]
    stored = row(factory, body["id"])
    assert count(factory, IngestionImage) == 0
    assert stored.text is None
    if kind is None:
        # A failure of the server: the bytes are kept and the reaper queues one more try.
        assert (stored.text_status, stored.raw is not None) == (TextStatus.pending, True)
        assert stored.failed_kind is FailedKind.server
        clock.advance(seconds=jobs.RETRY_AFTER.total_seconds() + 1)
        assert [job[0] for job in jobs.reap(factory, clock.now).queue] == [stored.id]
        assert run(factory, clock, RealParser(), body["id"]) == jobs.DONE
        assert len(images(factory, body["id"])) == 9
    else:
        assert (stored.text_status, stored.raw) == (TextStatus.failed, None)
        assert (stored.failed_kind, stored.failed_reason) == (kind, jobs.FILE_SENTENCES["bomb"])


def test_the_time_budget_spent_in_a_batch_keeps_what_was_rendered_and_says_so(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = uploaded(api, alice, scans.scan(9), "scan.pdf")
    parser = RealParser(instead={2: pc.ParserRefusedError("too_slow", failure="file")})
    assert run(factory, clock, parser, body["id"]) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.pages, stored.pages_text, stored.pages_image) == (9, 0, 8)
    assert stored.note == "This PDF took too long to turn into images. 1 scanned page was left out."
    assert stored.truncated is True
    assert [image.position for image in images(factory, body["id"])] == list(range(8))


def one_image(position: int = 0, **more: Any) -> pc.Parsed:
    jpeg = pictures.saved(pictures.page(40, 30), "JPEG")
    values = {"text": "", "pages": 1, "pages_text": 0, "pages_image": 1, **more}
    return pc.Parsed(images=(pc.PageImage(position, 40, 30, jpeg),), **values)


@pytest.mark.parametrize(
    "answer",
    [
        one_image(position=1),
        one_image(text="words the reader of images never writes"),
        one_image(pages=2),
        one_image(text=None),
        pc.Parsed(
            text="",
            pages=1,
            pages_text=0,
            pages_image=1,
            images=(*one_image().images, *one_image(position=1).images),
        ),
        pc.Parsed(text="", pages=1, pages_text=0, pages_image=1),
    ],
    ids=["position", "text", "pages", "no text", "two images", "no image"],
)
def test_an_answer_that_is_not_one_image_is_the_parsers_fault(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, answer: pc.Parsed
) -> None:
    body = uploaded(api, alice, pictures.screenshot_png(), "shot.png")
    assert run(factory, clock, RealParser(instead={0: answer}), body["id"]) == jobs.RETRY
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.text) == (TextStatus.pending, None)
    assert stored.raw is not None
    assert count(factory, IngestionImage) == 0


@pytest.mark.parametrize(
    "positions",
    [[7], [1, 0], [0, 0], list(range(9))],
    ids=["a page not asked for", "out of order", "twice", "more than the limit"],
)
def test_a_batch_whose_pages_are_not_those_asked_for_is_refused_whole(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, positions: list[int]
) -> None:
    body = uploaded(api, alice, scans.scan(2), "scan.pdf")
    batch = pc.Parsed(
        pages=2,
        pages_text=0,
        pages_image=len(positions),
        images=tuple(one_image(position).images[0] for position in positions),
    )
    assert run(factory, clock, RealParser(instead={1: batch}), body["id"]) == jobs.RETRY
    assert row(factory, body["id"]).text_status is TextStatus.pending
    assert count(factory, IngestionImage) == 0


def test_images_larger_than_what_is_left_of_the_quota_fail_the_document(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = scans.scan(2)
    body = uploaded(api, alice, data, "scan.pdf")
    # Room for the upload, not for what its two pages weigh once rendered: asked again inside
    # the transaction that would write them.
    monkeypatch.setattr(documents, "USER_MAX_BYTES", len(data) + 1000)
    assert run(factory, clock, RealParser(), body["id"]) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert stored.failed_reason == jobs.OVER_QUOTA
    assert (stored.raw, stored.text) == (None, None)
    assert count(factory, IngestionImage) == 0


# --- the reaper ---------------------------------------------------------------------------------


def test_the_images_waiting_since_g1_are_queued_in_bounded_batches_and_read(
    alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """The deploy of G8 finds every image stored since G1 `pending` with its bytes: the reaper
    takes them oldest first, REAP_BATCH rows a tick at most, and each becomes its page image."""
    owner = user_id(factory, ALICE)
    weeks_ago = clock.now - timedelta(days=20)
    waiting = [
        stored_before(
            factory,
            owner,
            pictures.screenshot_png(300 + n, 200),
            "image/png" if n % 2 else "image/jpeg",  # the stored type is not what is trusted
            weeks_ago + timedelta(minutes=n),
        )
        for n in range(5)
    ]
    first = jobs.reap(factory, clock.now, batch=3)
    assert [job[0] for job in first.queue] == waiting[:3]
    parser = RealParser()
    for document_id, _ in first.queue:
        assert run(factory, clock, parser, str(document_id)) == jobs.DONE
    second = jobs.reap(factory, clock.now, batch=3)
    assert [job[0] for job in second.queue] == waiting[3:]
    for document_id, _ in second.queue:
        assert run(factory, clock, parser, str(document_id)) == jobs.DONE
    assert jobs.reap(factory, clock.now, batch=3).queue == []
    assert kinds(parser) == ["image"] * 5
    for n, document_id in enumerate(waiting):
        stored = row(factory, str(document_id))
        assert (stored.text_status, stored.text_source, stored.raw) == (
            TextStatus.awaiting_transcription,
            TextSource.transcribed,
            None,
        )
        (image,) = images(factory, str(document_id))
        assert (image.position, image.width, image.height) == (0, 300 + n, 200)
    assert jobs.REAP_BATCH == 50


# --- privacy ------------------------------------------------------------------------------------


def test_nothing_of_an_image_reaches_a_log_or_a_return_value(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    shot = uploaded(api, alice, pictures.screenshot_png(), "private payslip.png")
    scan = uploaded(api, alice, scans.mixed(), "private contract.pdf")
    broken = uploaded(api, alice, pictures.truncated(pictures.photo_jpeg()), "private id.jpg")
    outcomes = [run(factory, clock, RealParser(), body["id"]) for body in (shot, scan, broken)]
    assert outcomes == [jobs.DONE, jobs.DONE, jobs.FAILED]
    stored = images(factory, shot["id"]) + images(factory, scan["id"])
    assert len(stored) == 3
    said = "\n".join(f"{record.getMessage()} {vars(record)}" for record in caplog.records)
    assert "document rendered" in said
    for secret in ("private", "payslip", "contract", scans.LONG[:20], scans.FOOTER[:20]):
        assert secret not in said
    for image in stored:
        assert image.jpeg[:16].hex() not in said
        assert repr(image.jpeg[:16]) not in said
    assert "JFIF" not in said
    assert "\\xff\\xd8" not in said
    rendered = [record for record in caplog.records if record.getMessage() == "document rendered"]
    assert [(record.pages_image, record.pages_text) for record in rendered] == [(1, 0), (2, 3)]
    assert all(isinstance(record.image_bytes, int) for record in rendered)
