"""How an upload is read: counted, hashed and timed while it arrives, held once, cut off at the
limit, and never more than two at a time.

Three ways to reach the route, each for what the others cannot show:
- the app over ASGI with a body fed message by message (`stream_post`): what the app does with
  each chunk, how many it takes, how much memory it holds;
- a real uvicorn on a socket: what a real HTTP parser does with a wrong Content-Length, a
  chunked body, a silent or vanishing client;
- threads against a real Postgres: two uploads at once.
"""

import asyncio
import contextlib
import hashlib
import select
import socket
import threading
import time
import tracemalloc
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import COOKIE_NAME
from app.ingestion import documents, documents_router
from app.ingestion.models import FILE_MAX_BYTES, IngestionDocument
from tests.conftest import free_port
from tests.identity.api_helpers import CSRF, Api
from tests.ingestion.conftest import ALICE
from tests.ingestion.helpers import (
    MIB,
    PDF_MAGIC,
    URL,
    Answer,
    chunks,
    named,
    pdf,
    post_stream,
    rows,
    stream_post,
    upload,
    uploaded,
    user_id,
)

BLOCK = 64 * 1024


def slots_free(app: FastAPI) -> int:
    """Upload slots not held right now (2 when no upload is in progress)."""
    slots = getattr(app.state, "ingestion_upload_slots", None)
    return documents_router.UPLOAD_SLOTS if slots is None else slots._value


async def blocks(
    total: int, first: bytes = PDF_MAGIC, pause: float = 0.0
) -> AsyncIterator[dict[str, Any]]:
    """A body of `total` bytes in 64 KiB messages that all share one block of memory."""
    block = bytes(BLOCK)
    sent = 0
    while sent < total:
        size = min(BLOCK, total - sent)
        part = (first + block)[:size] if sent == 0 else block[:size]
        sent += size
        if pause:
            await asyncio.sleep(pause)
        yield {"type": "http.request", "body": part, "more_body": sent < total}


# --- chunk by chunk, over ASGI -------------------------------------------------------------


def test_a_body_in_many_chunks_is_stored_whole_with_its_hash(
    app: FastAPI, alice: str, factory: sessionmaker[Session]
) -> None:
    parts = [PDF_MAGIC, b"a" * 70_000, b"", b"b" * 3, b"c" * 200_000]
    answer = stream_post(app, alice, lambda: chunks(*parts), named("chunked.pdf"))
    assert answer.status == 201
    assert answer.taken == len(parts)
    (stored,) = rows(factory)
    assert stored.raw == b"".join(parts)
    assert stored.sha256 == hashlib.sha256(b"".join(parts)).digest()
    assert stored.size == len(b"".join(parts))
    assert slots_free(app) == 2


def test_the_read_stops_at_the_first_chunk_past_the_limit(
    app: FastAPI, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_router, "FILE_MAX_BYTES", MIB)
    answer = stream_post(app, alice, lambda: blocks(100 * MIB))
    assert answer.status == 413
    assert answer.json() == {"detail": documents.TOO_LARGE}
    assert answer.headers["connection"] == "close"
    assert answer.headers["cache-control"] == "no-store"
    # 16 messages are 1 MiB exactly; the 17th passes the limit and nothing after it is taken.
    assert answer.taken == 17
    assert rows(factory) == []
    assert slots_free(app) == 2


def test_exactly_the_limit_in_chunks_is_kept_and_one_byte_more_is_not(
    app: FastAPI, alice: str, factory: sessionmaker[Session]
) -> None:
    exact = stream_post(app, alice, lambda: blocks(FILE_MAX_BYTES), named("exact.pdf"))
    assert exact.status == 201
    assert exact.json()["size"] == FILE_MAX_BYTES
    over = stream_post(app, alice, lambda: blocks(FILE_MAX_BYTES + 1, first=b"%PDF-2.0\n"))
    assert over.status == 413
    assert len(rows(factory)) == 1


def test_a_declared_length_over_the_limit_is_refused_before_any_chunk_is_taken(
    app: FastAPI, alice: str
) -> None:
    headers = {"Content-Length": str(FILE_MAX_BYTES + 1)}
    answer = stream_post(app, alice, lambda: blocks(FILE_MAX_BYTES + 1), headers)
    assert (answer.status, answer.taken) == (413, 0)
    assert answer.headers["connection"] == "close"


def test_a_declared_length_under_the_limit_does_not_excuse_a_longer_body(
    app: FastAPI, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content-Length is only ever a reason to refuse early: the count is what is trusted."""
    monkeypatch.setattr(documents_router, "FILE_MAX_BYTES", MIB)
    answer = stream_post(app, alice, lambda: blocks(3 * MIB), {"Content-Length": "10"})
    assert answer.status == 413
    assert rows(factory) == []


def test_an_upload_is_held_once_in_memory(
    app: FastAPI, alice: str, factory: sessionmaker[Session]
) -> None:
    """20 MiB arrive in 64 KiB chunks and go to Postgres: the process never holds a second copy
    (a list of chunks joined at the end, a statement parameter, a decoded string). What Python
    allocated at the highest point is the one buffer and its growth margin."""
    stream_post(app, alice, lambda: chunks(b"warm up: imports, pools, the first connection"))
    tracemalloc.start()
    try:
        answer = stream_post(app, alice, lambda: blocks(FILE_MAX_BYTES), named("big.pdf"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert answer.status == 201
    assert peak < FILE_MAX_BYTES * 1.25, f"peak {peak / MIB:.1f} MiB for a 20 MiB upload"
    with factory() as db:
        stored = db.execute(
            sa.select(sa.func.octet_length(IngestionDocument.raw)).where(
                IngestionDocument.size == FILE_MAX_BYTES
            )
        ).scalar_one()
    assert stored == FILE_MAX_BYTES


def test_a_twenty_mebibyte_text_is_never_decoded_whole(app: FastAPI, alice: str) -> None:
    """Text of 4 byte characters: decoded at once it would be a string of 20 MiB on top of the
    buffer, and more for each cleaning pass. Only its first characters are ever a string."""
    stream_post(app, alice, lambda: chunks(b"warm up"))
    emoji = "\U0001f600".encode() * (BLOCK // 4)
    count = FILE_MAX_BYTES // BLOCK

    async def body() -> AsyncIterator[dict[str, Any]]:
        for index in range(count):
            yield {"type": "http.request", "body": emoji, "more_body": index < count - 1}

    tracemalloc.start()
    try:
        answer = stream_post(app, alice, body, named("emoji.txt"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert answer.status == 201
    assert (answer.json()["truncated"], answer.json()["status"]) == (True, "done")
    assert peak < FILE_MAX_BYTES * 2, f"peak {peak / MIB:.1f} MiB for a 20 MiB text"


def test_a_body_that_goes_silent_ends_with_408_and_frees_its_slot(
    app: FastAPI,
    alice: str,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(documents_router, "IDLE_TIMEOUT_S", 0.2)

    async def stalls() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "http.request", "body": PDF_MAGIC + b"half", "more_body": True}

    start = time.monotonic()
    with caplog.at_level("INFO", logger="app.ingestion"):
        answer = stream_post(app, alice, stalls)
    assert answer.status == 408
    assert answer.json() == {"detail": documents_router.STALLED}
    assert answer.headers["connection"] == "close"
    assert 0.2 <= time.monotonic() - start < 5
    assert rows(factory) == []
    assert slots_free(app) == 2
    (refused,) = [vars(r) for r in caplog.records if r.getMessage() == "document refused"]
    assert (refused["rule"], refused["status"], refused["size"]) == ("upload-stalled", 408, 13)


def test_a_body_that_trickles_forever_ends_with_408_too(
    app: FastAPI, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every chunk arrives within the idle limit, so only the limit on the whole body ends it."""
    monkeypatch.setattr(documents_router, "IDLE_TIMEOUT_S", 10.0)
    monkeypatch.setattr(documents_router, "TOTAL_TIMEOUT_S", 0.5)
    start = time.monotonic()
    answer = stream_post(app, alice, lambda: blocks(100 * MIB, pause=0.05))
    assert answer.status == 408
    assert 0.5 <= time.monotonic() - start < 5
    assert answer.taken < 30
    assert rows(factory) == []
    assert slots_free(app) == 2


def test_a_slow_but_steady_body_is_kept(
    app: FastAPI, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_router, "IDLE_TIMEOUT_S", 5.0)
    answer = stream_post(app, alice, lambda: chunks(PDF_MAGIC, b"a", b"b", b"c", pause=0.1))
    assert answer.status == 201
    assert rows(factory)[0].raw == PDF_MAGIC + b"abc"


def test_a_client_that_goes_away_leaves_nothing(
    app: FastAPI,
    alice: str,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def leaves() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "http.request", "body": PDF_MAGIC + b"half", "more_body": True}
        yield {"type": "http.disconnect"}

    with caplog.at_level("INFO", logger="app.ingestion"):
        answer = stream_post(app, alice, leaves)
    assert answer.status == 400
    assert answer.json() == {"detail": documents_router.INTERRUPTED}
    assert rows(factory) == []
    assert slots_free(app) == 2
    (refused,) = [vars(r) for r in caplog.records if r.getMessage() == "document refused"]
    assert (refused["rule"], refused["size"]) == ("upload-interrupted", 13)
    assert "unhandled error" not in caplog.text


def test_a_refused_type_frees_its_slot(app: FastAPI, alice: str) -> None:
    answer = stream_post(app, alice, lambda: chunks(b"GIF89a", b"\x00" * 100))
    assert answer.status == 415
    assert slots_free(app) == 2


# --- two at a time -------------------------------------------------------------------------


async def both_slots_taken(app: FastAPI) -> None:
    """Wait until two uploads hold the two slots (they are then reading their bodies)."""
    while slots_free(app) > 0:  # noqa: ASYNC110, a semaphore offers no event to wait on
        await asyncio.sleep(0.01)


def run_together(*posts: Any) -> list[Answer]:
    async def together() -> list[Answer]:
        return list(await asyncio.gather(*posts))

    return asyncio.run(together())


def test_a_third_upload_waits_for_a_slot_and_its_body_is_not_read_meanwhile(
    app: FastAPI, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_router, "SLOT_WAIT_S", 5.0)
    released: dict[str, float] = {}
    third_started: dict[str, float] = {}

    def slow(tag: bytes) -> Any:
        async def body() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "http.request", "body": PDF_MAGIC + tag, "more_body": True}
            await asyncio.sleep(0.4)
            released[tag.decode()] = time.monotonic()
            yield {"type": "http.request", "body": b"end", "more_body": False}

        return body

    async def third() -> AsyncIterator[dict[str, Any]]:
        third_started["at"] = time.monotonic()
        yield {"type": "http.request", "body": b"the third upload, a text", "more_body": False}

    async def late() -> Answer:
        await both_slots_taken(app)
        return await post_stream(app, alice, third)

    first, second, last = run_together(
        post_stream(app, alice, slow(b"one")), post_stream(app, alice, slow(b"two")), late()
    )
    assert (first.status, second.status, last.status) == (201, 201, 201)
    # The third body was asked for only once a slot was free.
    assert third_started["at"] >= min(released.values())
    assert len(rows(factory)) == 3
    assert slots_free(app) == 2


def test_a_third_upload_gives_up_with_503_when_both_slots_stay_busy(
    app: FastAPI,
    alice: str,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(documents_router, "SLOT_WAIT_S", 0.2)
    # Long enough that the two stalled uploads still hold their slots when the third gives up,
    # however slow the machine.
    monkeypatch.setattr(documents_router, "IDLE_TIMEOUT_S", 3.0)

    async def stalls() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "http.request", "body": PDF_MAGIC, "more_body": True}

    async def late() -> Answer:
        await both_slots_taken(app)
        return await post_stream(app, alice, lambda: chunks(b"a text that never gets a slot"))

    with caplog.at_level("INFO", logger="app.ingestion"):
        first, second, third = run_together(
            post_stream(app, alice, stalls), post_stream(app, alice, stalls), late()
        )
    assert (first.status, second.status) == (408, 408)
    assert third.status == 503
    assert third.json() == {"detail": documents_router.BUSY}
    assert third.headers["retry-after"] == "5"
    assert third.taken == 0  # its body was never read
    assert rows(factory) == []
    assert slots_free(app) == 2
    rules = Counter(vars(r)["rule"] for r in caplog.records if r.getMessage() == "document refused")
    assert rules == {"upload-stalled": 2, "upload-busy": 1}


# --- a real server on a socket -------------------------------------------------------------


@pytest.fixture
def served(app: FastAPI) -> Iterator[int]:
    """The app behind a real uvicorn (httptools, as in the image) on a loopback port."""
    port = free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, lifespan="off", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    yield port
    server.should_exit = True
    thread.join(timeout=10)


def sent_by(token: str, name: str) -> dict[str, str]:
    """The headers of an upload by the session `token`, for a plain HTTP client."""
    return {**CSRF, **named(name), "Cookie": f"{COOKIE_NAME}={token}"}


def head(token: str, *lines: str) -> bytes:
    request = [
        f"POST {URL} HTTP/1.1",
        "Host: 127.0.0.1",
        f"Cookie: {COOKIE_NAME}={token}",
        "X-Requested-With: jobapp",
        "X-Filename: UTF-8''socket.pdf",
        *lines,
        "",
        "",
    ]
    return "\r\n".join(request).encode()


def read_answer(sock: socket.socket, timeout: float = 10) -> tuple[int, dict[str, str], bytes]:
    """The response on `sock`: status, headers, and the body up to Content-Length."""
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        more = sock.recv(65536)
        assert more, f"connection closed before an answer: {data!r}"
        data += more
    raw_head, _, body = data.partition(b"\r\n\r\n")
    status_line, *header_lines = raw_head.decode("latin-1").split("\r\n")
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in header_lines)
    }
    length = int(headers.get("content-length", "0"))
    while len(body) < length:
        more = sock.recv(65536)
        assert more, "connection closed inside the body"
        body += more
    return int(status_line.split()[1]), headers, body


def closed_by_server(sock: socket.socket, timeout: float = 5) -> bool:
    sock.settimeout(timeout)
    try:
        while True:
            if not sock.recv(65536):
                return True
    except (ConnectionResetError, BrokenPipeError):
        return True
    except TimeoutError:
        return False


def test_a_real_upload_over_a_socket(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    body = pdf(300_000)
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, f"Content-Length: {len(body)}") + body)
        status, headers, answer = read_answer(sock)
    assert status == 201
    assert headers["cache-control"] == "no-store"
    assert b'"name":"socket.pdf"' in answer
    assert rows(factory)[0].raw == body


def test_too_large_a_declared_length_is_answered_before_the_body_is_sent(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, f"Content-Length: {FILE_MAX_BYTES + 1}"))
        status, headers, answer = read_answer(sock)  # not one byte of body was sent
        assert status == 413
        assert headers["connection"] == "close"
        assert b"larger than 20 MB" in answer
        assert closed_by_server(sock)
    assert rows(factory) == []


def test_a_chunked_body_over_the_limit_is_cut_off(
    served: int, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Content-Length to refuse on: the server answers 413 once the count passes the limit
    and closes, long before the client is done sending."""
    monkeypatch.setattr(documents_router, "FILE_MAX_BYTES", 256 * 1024)
    piece = f"{16384:x}\r\n".encode() + b"\x00" * 16384 + b"\r\n"
    budget = 64 * MIB
    sent = 0
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, "Transfer-Encoding: chunked"))
        answered = False
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            while sent < budget and not answered:
                sock.sendall(piece)
                sent += 16384
                answered = bool(select.select([sock], [], [], 0)[0])
        status, headers, answer = read_answer(sock)
        assert status == 413
        assert headers["connection"] == "close"
        assert b"larger than 20 MB" in answer
        assert closed_by_server(sock)
    assert sent < 16 * MIB, f"the client could send {sent} bytes before the answer"
    assert rows(factory) == []


def test_a_chunked_body_under_the_limit_is_kept(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    parts = [PDF_MAGIC, b"x" * 5000, b"y" * 70_000]
    wire = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in parts) + b"0\r\n\r\n"
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, "Transfer-Encoding: chunked") + wire)
        status, _, _ = read_answer(sock)
    assert status == 201
    assert rows(factory)[0].raw == b"".join(parts)


def test_a_content_length_larger_than_what_is_sent_ends_with_408(
    served: int, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client promises 1,000 bytes, sends 13 and waits: the server does not wait for ever."""
    monkeypatch.setattr(documents_router, "IDLE_TIMEOUT_S", 0.3)
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, "Content-Length: 1000") + PDF_MAGIC + b"half")
        start = time.monotonic()
        status, headers, answer = read_answer(sock)
        assert status == 408
        assert 0.25 <= time.monotonic() - start < 5
        assert headers["connection"] == "close"
        assert b"stopped arriving" in answer
        assert closed_by_server(sock)
    assert rows(factory) == []


def test_a_content_length_smaller_than_what_is_sent_stores_the_declared_bytes_only(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    """Whatever follows the declared length is not part of this request: the HTTP parser reads
    it as the next one (here, garbage, answered 400), never as more of the document."""
    body = PDF_MAGIC + b"declared"
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, f"Content-Length: {len(body)}") + body)
        status, _, _ = read_answer(sock)
        assert status == 201
        sock.sendall(b"\x00smuggled" * 100)
        status, _, _ = read_answer(sock)
        assert status == 400
    (stored,) = rows(factory)
    assert stored.raw == body


def test_the_same_in_one_segment_never_stores_more_than_was_declared(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    """Sent in one write, the parser sees the garbage at once and may refuse the connection as
    a whole. Either way no document holds a byte past the declared length."""
    body = PDF_MAGIC + b"declared"
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, f"Content-Length: {len(body)}") + body + b"\x00smuggled" * 100)
        status, _, _ = read_answer(sock)
        assert status in (201, 400)
        closed_by_server(sock, timeout=2)
    assert [doc.raw for doc in rows(factory)] in ([], [body])


def test_a_client_that_closes_mid_body_leaves_nothing_and_frees_its_slot(
    served: int, app: FastAPI, alice: str, api: Api, factory: sessionmaker[Session]
) -> None:
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head(alice, "Content-Length: 100000") + PDF_MAGIC + b"half")
        time.sleep(0.2)  # the server has started reading
    deadline = time.monotonic() + 5
    while slots_free(app) < 2:
        assert time.monotonic() < deadline, "the slot of the vanished client was never freed"
        time.sleep(0.02)
    assert rows(factory) == []
    with httpx.Client(base_url=f"http://127.0.0.1:{served}") as client:
        response = client.post(URL, content=pdf(), headers=sent_by(alice, "after.pdf"))
    assert response.status_code == 201


def test_an_anonymous_upload_is_answered_401_without_its_body(served: int) -> None:
    with socket.create_connection(("127.0.0.1", served), timeout=10) as sock:
        sock.sendall(head("no-such-session", f"Content-Length: {FILE_MAX_BYTES}"))
        status, _, answer = read_answer(sock)
    assert status == 401
    assert b"Sign in to continue." in answer


# --- two uploads at once, real Postgres ----------------------------------------------------

WRITERS = 8


def test_the_same_file_from_many_requests_at_once_is_one_row_and_no_error(
    served: int, alice: str, factory: sessionmaker[Session]
) -> None:
    data = pdf(200_000, fill=b"\x07")
    barrier = threading.Barrier(WRITERS)

    def send(_: int) -> tuple[int, str, bool]:
        with httpx.Client(base_url=f"http://127.0.0.1:{served}", timeout=30) as client:
            barrier.wait(timeout=30)
            response = client.post(URL, content=data, headers=sent_by(alice, "cv.pdf"))
        body = response.json()
        return response.status_code, body.get("id", ""), body.get("duplicate", False)

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        answers = list(pool.map(send, range(WRITERS)))
    assert Counter(status for status, _, _ in answers) == {201: 1, 200: WRITERS - 1}
    assert len({document_id for _, document_id, _ in answers}) == 1
    assert Counter(duplicate for _, _, duplicate in answers) == {False: 1, True: WRITERS - 1}
    (stored,) = rows(factory)
    assert stored.raw == data


def test_the_same_file_through_the_service_at_once_is_one_row(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    owner = user_id(factory, ALICE)
    data = pdf(50_000)
    digest = hashlib.sha256(data).digest()
    barrier = threading.Barrier(WRITERS)

    def attempt(_: int) -> bool:
        with factory() as db:
            barrier.wait(timeout=30)
            stored = documents.store_file(db, owner, "cv.pdf", data, digest, datetime.now(UTC))
            db.commit()
            return stored.created

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        outcomes = Counter(pool.map(attempt, range(WRITERS)))
    assert outcomes == {True: 1, False: WRITERS - 1}
    assert len(rows(factory)) == 1


def test_two_uploads_that_together_pass_the_quota_one_wins(
    factory: sessionmaker[Session], api: Api, alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "USER_MAX_BYTES", 3000)
    owner = user_id(factory, ALICE)
    barrier = threading.Barrier(WRITERS)

    def attempt(n: int) -> int:
        data = pdf(2000, fill=bytes([n + 1]))
        with factory() as db:
            barrier.wait(timeout=30)
            try:
                documents.store_file(
                    db, owner, f"{n}.pdf", data, hashlib.sha256(data).digest(), datetime.now(UTC)
                )
            except documents.DocumentError as exc:
                db.rollback()
                return exc.status
            db.commit()
            return 201

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        outcomes = Counter(pool.map(attempt, range(WRITERS)))
    assert outcomes == {201: 1, 413: WRITERS - 1}
    with factory() as db:
        assert documents.usage(db, owner) == documents.Usage(bytes=2000, documents=1)


def test_the_last_free_place_goes_to_one_of_many_uploads(
    factory: sessionmaker[Session], api: Api, alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents, "USER_MAX_DOCUMENTS", 1)
    owner = user_id(factory, ALICE)
    barrier = threading.Barrier(WRITERS)

    def attempt(n: int) -> int:
        with factory() as db:
            barrier.wait(timeout=30)
            try:
                documents.store_paste(db, owner, None, f"text number {n}", datetime.now(UTC))
            except documents.DocumentError as exc:
                db.rollback()
                return exc.status
            db.commit()
            return 201

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        outcomes = Counter(pool.map(attempt, range(WRITERS)))
    assert outcomes == {201: 1, 422: WRITERS - 1}


def test_a_delete_and_an_upload_of_the_same_file_at_once_never_fail(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    owner = user_id(factory, ALICE)
    data = pdf(50_000, fill=b"\x09")
    digest = hashlib.sha256(data).digest()
    first = uploaded(api, alice, data, "cv.pdf")
    barrier = threading.Barrier(2)

    def remove() -> str:
        with factory() as db:
            barrier.wait(timeout=30)
            found = documents.delete_document(db, owner, uuid.UUID(first["id"]))
            db.commit()
            return "deleted" if found else "missing"

    def add() -> str:
        with factory() as db:
            barrier.wait(timeout=30)
            stored = documents.store_file(db, owner, "cv.pdf", data, digest, datetime.now(UTC))
            db.commit()
            return "created" if stored.created else "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        removal, addition = pool.submit(remove), pool.submit(add)
        assert removal.result(timeout=30) == "deleted"
        assert addition.result(timeout=30) in ("created", "duplicate")
    # Whatever the order: no error, and at most the one row of the new upload.
    left = rows(factory)
    assert len(left) == (1 if addition.result() == "created" else 0)


def test_the_service_refuses_a_body_over_the_limit_whoever_calls_it(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    data = pdf(FILE_MAX_BYTES + 1)
    with factory() as db, pytest.raises(documents.DocumentError) as refused:
        documents.store_file(
            db,
            user_id(factory, ALICE),
            "x.pdf",
            data,
            hashlib.sha256(data).digest(),
            datetime.now(UTC),
        )
    assert (refused.value.status, refused.value.rule) == (413, "file-too-large")
    assert upload(api, alice, b"still works").status_code == 201


def test_two_files_stored_in_one_transaction_each_keep_their_own_bytes(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    """The staging table is emptied before each file: a caller that stores several files in one
    transaction (a later task that unpacks an archive) never mixes their bytes."""
    owner = user_id(factory, ALICE)
    one, two = pdf(3000, fill=b"\x01"), pdf(4000, fill=b"\x02")
    with factory() as db:
        for name, data in (("one.pdf", one), ("two.pdf", two)):
            digest = hashlib.sha256(data).digest()
            documents.store_file(db, owner, name, data, digest, datetime.now(UTC))
        db.commit()
    assert sorted(doc.raw or b"" for doc in rows(factory)) == sorted([one, two])
