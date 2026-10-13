"""The Google client against the in process fake (mail M1). One test per edge case of the
plan's block, named after it. No test sleeps or measures time: the clock is `Clock`, and the
waits the client asks for are the list `sleeps`."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import inspect
import logging
import pickle
import traceback
import tracemalloc
from urllib.parse import unquote

import httpx
import pytest

from app.config import Settings
from app.mail import errors, log
from app.mail import google as google_module
from app.mail.google import (
    ATTACHMENT_MAX_BYTES,
    MB,
    MESSAGE_MAX_BYTES,
    UNITS,
    USER_UNITS_PER_MINUTE,
    GoogleClient,
    Grant,
    next_backoff,
)
from tests.mail import canary
from tests.mail.conftest import (
    ALICE,
    BOB,
    Clock,
    Connected,
    connect,
    make_client,
    run,
    settings_for,
)
from tests.mail.fake_gmail import FakeGoogle, generated_bytes, raw_size_for_answer

# --- the retry rule: 429, 403 with a rate limit reason, 5xx ---------------------------------


def test_429_with_retry_after_waits_that_long_and_then_succeeds(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    fake.fail("getProfile", "429", retry_after=3)
    profile = run(google.get_profile(account_a.grant))
    assert profile.email == ALICE
    assert sleeps == [3.0]
    assert [call["status"] for call in fake.calls_of("getProfile")] == [429, 200]


def test_429_with_a_long_retry_after_raises_at_once_and_carries_the_figure(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    fake.fail("getProfile", "429", retry_after=900)
    with pytest.raises(errors.QuotaError) as caught:
        run(google.get_profile(account_a.grant))
    assert (caught.value.code, caught.value.status, caught.value.retry_after) == ("quota", 429, 900)
    assert sleeps == []
    assert len(fake.calls_of("getProfile")) == 1


def test_429_without_retry_after_backs_off_1_2_4_seconds_then_raises_quota(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    fake.fail("getProfile", "429", times=10)
    with pytest.raises(errors.QuotaError) as caught:
        run(google.get_profile(account_a.grant))
    assert caught.value.reason == "rate_limit"
    assert caught.value.retry_after is None
    assert sleeps == [1.0, 2.0, 4.0]
    assert len(fake.calls_of("getProfile")) == 4


def test_the_backoff_carries_up_to_a_quarter_of_jitter(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    client = make_client(fake, mail_settings, clock, sleeps, jitter=lambda: 1.0)
    fake.fail("getProfile", "503", times=2)
    run(client.get_profile(account_a.grant))
    assert sleeps == [1.25, 2.5]


@pytest.mark.parametrize(
    ("kind", "reason"),
    [("403_rate", "rate_limit"), ("403_user_rate", "user_rate_limit")],
    ids=["403_rateLimitExceeded", "403_userRateLimitExceeded"],
)
def test_403_rate_limit_exceeded_and_user_rate_limit_exceeded_are_quota_and_retried(
    fake: FakeGoogle,
    google: GoogleClient,
    account_a: Connected,
    sleeps: list[float],
    kind: str,
    reason: str,
) -> None:
    fake.fail("messages.list", kind, times=1)
    page = run(google.list_messages(account_a.grant, q="mistral"))
    assert page.refs
    assert sleeps == [1.0]
    fake.fail("messages.list", kind, times=10)
    with pytest.raises(errors.QuotaError) as caught:
        run(google.list_messages(account_a.grant, q="mistral"))
    assert (caught.value.code, caught.value.status, caught.value.reason) == ("quota", 403, reason)


def test_403_daily_limit_is_quota_and_is_not_retried(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    fake.fail("getProfile", "403_daily")
    with pytest.raises(errors.QuotaError) as caught:
        run(google.get_profile(account_a.grant))
    assert caught.value.reason == "daily_limit"
    assert sleeps == []


def test_403_insufficient_scope_is_scope_missing(
    fake: FakeGoogle, google: GoogleClient, sleeps: list[float]
) -> None:
    account = connect(fake, ALICE, scopes=("openid",))
    with pytest.raises(errors.ScopeMissingError) as caught:
        run(google.get_profile(account.grant))
    assert caught.value.code == "scope_missing"
    assert sleeps == []


@pytest.mark.parametrize("kind", ["500", "503"])
def test_500_and_503_are_retried_then_google_down(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float], kind: str
) -> None:
    fake.fail("messages.get", kind, times=2)
    message = run(google.get_message(account_a.grant, "m-mistral-1"))
    assert message["id"] == "m-mistral-1"
    assert sleeps == [1.0, 2.0]
    del sleeps[:]
    fake.fail("messages.get", kind, times=10)
    with pytest.raises(errors.GoogleDownError) as caught:
        run(google.get_message(account_a.grant, "m-mistral-1"))
    assert (caught.value.code, caught.value.status) == ("google_down", int(kind))
    assert sleeps == [1.0, 2.0, 4.0]


def test_a_timeout_is_a_typed_error_and_is_not_retried(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    fake.fail("getProfile", "timeout")
    with pytest.raises(errors.GoogleTimeoutError) as caught:
        run(google.get_profile(account_a.grant))
    assert (caught.value.code, caught.value.reason) == ("google_down", "timeout")
    assert sleeps == []
    assert google_module.TIMEOUT.connect == 10.0
    assert google_module.TIMEOUT.read == 10.0


def test_a_truncated_json_answer_is_a_malformed_answer(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    fake.fail("messages.list", "truncated")
    with pytest.raises(errors.MalformedAnswerError) as caught:
        run(google.list_messages(account_a.grant))
    assert (caught.value.code, caught.value.reason) == ("google_down", "truncated")


def test_an_answer_that_is_not_json_is_a_malformed_answer(
    mail_settings: Settings,
    fake: FakeGoogle,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    inner = fake.transport()

    def page(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, text="<html><body>Sign in to your network</body></html>")
        return inner.handler(request)

    client = make_client(fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(page))
    with pytest.raises(errors.MalformedAnswerError) as caught:
        run(client.get_profile(account_a.grant))
    assert caught.value.reason == "not_json"
    fake.fail("getProfile", "html", times=10)  # a proxy's 502 page: a 5xx, retried, never parsed
    with pytest.raises(errors.GoogleDownError) as caught_down:
        run(google_on(fake, mail_settings, clock, sleeps).get_profile(account_a.grant))
    assert caught_down.value.status == 502


def google_on(
    fake: FakeGoogle, settings: Settings, clock: Clock, sleeps: list[float]
) -> GoogleClient:
    return make_client(fake, settings, clock, sleeps)


# --- sizes ----------------------------------------------------------------------------------


def test_a_messages_get_answer_of_9_mb_is_refused_at_8_mb_with_too_large(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    fake.mailboxes[ALICE].add(
        {
            "id": "m-nine-mb",
            "headers": [
                ["From", "Big Sender <big@lumen-labs.example>"],
                ["Subject", "A very long mail"],
            ],
            "text": "start",
            "pad_bytes": 9 * MB * 3 // 4,
        }
    )
    full = fake.transport().handler(
        httpx.Request(
            "GET",
            "http://fake-google.test/gmail/v1/users/me/messages/m-nine-mb",
            headers={"Authorization": f"Bearer {run(google.access_token(account_a.grant))}"},
        )
    )
    assert 8.9 * MB < len(full.content) < 9.1 * MB
    with pytest.raises(errors.TooLargeError) as caught:
        run(google.get_message(account_a.grant, "m-nine-mb"))
    assert (caught.value.code, caught.value.limit) == ("too_large", MESSAGE_MAX_BYTES)
    # Never dropped: the same message comes back from format=metadata, named as truncated.
    fetched = run(google.get_messages(account_a.grant, ["m-mistral-1", "m-nine-mb"]))
    assert [message["id"] for message in fetched.messages] == ["m-mistral-1", "m-nine-mb"]
    assert fetched.truncated == frozenset({"m-nine-mb"})
    assert "parts" not in fetched.messages[1]["payload"]
    assert "body" not in fetched.messages[1]["payload"]
    assert fetched.messages[1]["snippet"] == "start"


def test_any_other_json_answer_is_refused_over_2_mb(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    inner = fake.transport()

    def big(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, content=b'{"pad": "' + b"x" * (2 * MB) + b'"}')
        return inner.handler(request)

    client = make_client(fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(big))
    with pytest.raises(errors.TooLargeError) as caught:
        run(client.list_messages(account_a.grant))
    assert caught.value.limit == 2 * MB


def _big_attachment(fake: FakeGoogle, answer_bytes: int) -> tuple[str, int]:
    size = raw_size_for_answer(answer_bytes)
    message = fake.mailboxes[ALICE].add(
        {
            "id": f"m-big-{answer_bytes}",
            "headers": [["From", "Files <files@lumen-labs.example>"], ["Subject", "A large file"]],
            "text": "See the file.",
            "attachments": [
                {"filename": "large.bin", "mimeType": "application/octet-stream", "size": size}
            ],
        }
    )
    return message["attachments"][0]["attachmentId"], size


def test_an_attachments_get_answer_of_34_mb_is_read_in_chunks_with_the_peak_memory_measured(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    attachment_id, size = _big_attachment(fake, 34 * MB)
    expected = hashlib.sha256()
    for piece in generated_bytes(attachment_id, size):
        expected.update(piece)

    async def download() -> tuple[int, str, int, int]:
        digest = hashlib.sha256()
        total = 0
        largest = 0
        tracemalloc.start()
        try:
            stream = google.download_attachment(account_a.grant, f"m-big-{34 * MB}", attachment_id)
            async with contextlib.aclosing(stream) as pieces:
                async for piece in pieces:
                    digest.update(piece)
                    total += len(piece)
                    largest = max(largest, len(piece))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return total, digest.hexdigest(), largest, peak

    total, digest, largest, peak = run(download())
    assert total == size
    assert total * 4 // 3 > 33.9 * MB  # the answer itself weighed 34 MB
    assert digest == expected.hexdigest()
    assert largest <= 64 * 1024
    # The whole answer is 34 MB and the file 25.5 MB: the process never held a tenth of it.
    assert peak < 3 * MB, f"peak of {peak} bytes while downloading"
    # Shown with `pytest -s`: the figure of "Measurements", M1, in tasks/mail/todo.md.
    print(f"attachment download: {total} bytes decoded, peak of {peak} bytes traced")
    assert google.slots.active == 0


def test_an_attachments_get_answer_of_37_mb_is_cut_at_the_cap(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    attachment_id, _ = _big_attachment(fake, 37 * MB)

    async def download() -> int:
        total = 0
        stream = google.download_attachment(account_a.grant, f"m-big-{37 * MB}", attachment_id)
        async with contextlib.aclosing(stream) as pieces:
            async for piece in pieces:
                total += len(piece)
        return total

    received: list[int] = []

    async def scenario() -> None:
        received.append(await download())

    with pytest.raises(errors.TooLargeError) as caught:
        run(scenario())
    assert caught.value.limit == ATTACHMENT_MAX_BYTES
    assert received == []
    assert google.slots.active == 0  # the slot was given back


def test_an_attachment_answer_cut_short_is_truncated(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    inner = fake.transport()

    def cut(request: httpx.Request) -> httpx.Response:
        if "/attachments/" in request.url.path:
            return httpx.Response(200, content=b'{"size": 9000, "data": "QUJDRA')
        return inner.handler(request)

    client = make_client(fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(cut))

    async def download() -> bytes:
        return b"".join(
            [piece async for piece in client.download_attachment(account_a.grant, "m", "a")]
        )

    with pytest.raises(errors.MalformedAnswerError) as caught:
        run(download())
    assert caught.value.reason == "truncated"


def test_the_pdf_of_the_seed_downloads_whole(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    async def download() -> bytes:
        message = await google.get_message(account_a.grant, "m-pdf-1")
        part = message["payload"]["parts"][1]
        assert part["filename"] == "interview-details.pdf"
        stream = google.download_attachment(
            account_a.grant, "m-pdf-1", part["body"]["attachmentId"]
        )
        return b"".join([piece async for piece in stream])

    content = run(download())
    assert content.startswith(b"%PDF-1.4")
    assert content.rstrip().endswith(b"%%EOF")


def test_three_downloads_at_once_the_third_waits(
    fake: FakeGoogle, mail_settings: Settings, clock: Clock, sleeps: list[float]
) -> None:
    fake.seed("bulk")
    accounts = [connect(fake, ALICE), connect(fake, BOB), connect(fake, "e2e-bulk@example.org")]
    for account in accounts:
        fake.mailboxes[account.email].add(
            {
                "id": "m-file",
                "headers": [["Subject", "A file"]],
                "text": "file",
                "attachments": [{"filename": "f.bin", "size": 1000}],
            }
        )
    inner = fake.transport()

    async def scenario() -> None:
        arrived = asyncio.Semaphore(0)
        release = asyncio.Event()

        async def held(request: httpx.Request) -> httpx.Response:
            if "/attachments/" in request.url.path:
                arrived.release()
                await release.wait()
            return inner.handler(request)

        client = make_client(
            fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(held)
        )

        async def download(account: Connected) -> int:
            attachment = fake.mailboxes[account.email].messages["m-file"]["attachments"][0]
            stream = client.download_attachment(account.grant, "m-file", attachment["attachmentId"])
            return sum([len(piece) async for piece in stream])

        tasks = [asyncio.create_task(download(account)) for account in accounts]
        await arrived.acquire()
        await arrived.acquire()
        # Two are at Google. The third is queued in the client: it never left the process.
        for _ in range(50):  # lets the third task reach the queue: turns of the loop, no time
            if client.slots.waiting:
                break
            await asyncio.sleep(0)
        assert (client.slots.active, client.slots.waiting) == (2, 1)
        assert len(fake.calls_of("attachments.get")) == 0
        assert not any(task.done() for task in tasks)
        release.set()
        assert await asyncio.gather(*tasks) == [1000, 1000, 1000]
        assert (client.slots.active, client.slots.waiting) == (0, 0)
        assert len(fake.calls_of("attachments.get")) == 3

    run(scenario())


def test_one_download_per_user_the_second_of_the_same_user_waits(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    fake.mailboxes[ALICE].add(
        {
            "id": "m-file",
            "headers": [],
            "text": "f",
            "attachments": [{"filename": "f.bin", "size": 10}],
        }
    )
    attachment_id = fake.mailboxes[ALICE].messages["m-file"]["attachments"][0]["attachmentId"]
    inner = fake.transport()

    async def scenario() -> None:
        arrived = asyncio.Semaphore(0)
        release = asyncio.Event()

        async def held(request: httpx.Request) -> httpx.Response:
            if "/attachments/" in request.url.path:
                arrived.release()
                await release.wait()
            return inner.handler(request)

        client = make_client(
            fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(held)
        )

        async def download() -> int:
            stream = client.download_attachment(account_a.grant, "m-file", attachment_id)
            return sum([len(piece) async for piece in stream])

        tasks = [asyncio.create_task(download()) for _ in range(2)]
        await arrived.acquire()
        for _ in range(50):
            if client.slots.waiting:
                break
            await asyncio.sleep(0)
        assert (client.slots.active, client.slots.waiting) == (1, 1)
        release.set()
        assert await asyncio.gather(*tasks) == [10, 10]

    run(scenario())


# --- tokens ---------------------------------------------------------------------------------


def test_invalid_grant_on_refresh_is_revoked(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    run(google.get_profile(account_a.grant))
    fake.revoke(ALICE)  # the user took the access away at Google
    with pytest.raises(errors.RevokedError) as caught:
        run(google.get_profile(account_a.grant))
    assert (caught.value.code, caught.value.reason) == ("revoked", "invalid_grant")
    assert errors.stored_code(caught.value) == "revoked"
    assert sleeps == []
    fresh = connect(fake, ALICE)
    fake.fail("token", "invalid_grant")
    with pytest.raises(errors.RevokedError):
        run(google.get_profile(fresh.grant))


def test_a_token_that_expires_between_two_pages_is_refreshed_once(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, clock: Clock
) -> None:
    fake.seed("bulk")
    account = connect(fake, "e2e-bulk@example.org")

    def grants() -> list[str]:
        return [call["query"]["grant_type"] for call in fake.calls_of("token")]

    first = run(google.list_messages(account.grant, max_results=50))
    assert first.next_page_token
    assert grants() == ["refresh_token"]
    # Google ends the token early: the next page answers 401, the client refreshes once.
    fake.expire_access("e2e-bulk@example.org")
    second = run(
        google.list_messages(account.grant, max_results=50, page_token=first.next_page_token)
    )
    assert grants() == ["refresh_token", "refresh_token"]
    assert [call["status"] for call in fake.calls_of("messages.list")] == [200, 401, 200]
    assert not {ref.id for ref in first.refs} & {ref.id for ref in second.refs}
    # The token's own lifetime passes: the client refreshes before it asks, no 401.
    clock.advance(3600)
    third = run(
        google.list_messages(account.grant, max_results=50, page_token=second.next_page_token)
    )
    assert grants() == ["refresh_token"] * 3
    assert [call["status"] for call in fake.calls_of("messages.list")] == [200, 401, 200, 200]
    assert len({ref.id for page in (first, second, third) for ref in page.refs}) == 150


def test_a_401_right_after_a_refresh_is_revoked_not_a_loop(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    fake.fail("getProfile", "401", times=5)
    with pytest.raises(errors.RevokedError):
        run(google.get_profile(account_a.grant))
    assert len(fake.calls_of("getProfile")) == 2
    assert len(fake.calls_of("token")) == 2


def test_the_fake_asked_with_a_wrong_bearer_answers_401(fake: FakeGoogle) -> None:
    connect(fake, ALICE)
    wrong = canary.marker("ya29.wrong")
    for path in ("/gmail/v1/users/me/profile", "/gmail/v1/users/me/messages", "/v1/userinfo"):
        reply = fake.handle("GET", path, {"authorization": f"Bearer {wrong}"})
        assert reply.status == 401
        assert b"UNAUTHENTICATED" in reply.body  # type: ignore[operator]
        assert reply.headers["WWW-Authenticate"].startswith("Bearer")
    assert fake.handle("GET", "/gmail/v1/users/me/profile", {}).status == 401


# --- pagination, history, a message that is gone --------------------------------------------


def test_a_page_token_that_repeats_stops_the_loop_after_the_cap(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    fake.switch("repeat_page_token", "on")
    listing = run(google.list_all_messages(account_a.grant, q="application", limit=10_000))
    # The same token came back: cut at the second page, far before the cap of pages.
    assert listing.stopped == "page_token_repeated"
    assert listing.pages == 2 == len(fake.calls_of("messages.list"))


def test_a_listing_that_never_ends_is_cut_at_the_cap_of_pages(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    inner = fake.transport()
    served = 0

    def endless(request: httpx.Request) -> httpx.Response:
        nonlocal served
        if request.url.path.endswith("/messages"):
            served += 1
            ids = [{"id": f"x{served}", "threadId": f"x{served}"}]
            return httpx.Response(200, json={"messages": ids, "nextPageToken": f"t{served}"})
        return inner.handler(request)

    client = make_client(fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(endless))
    listing = run(client.list_all_messages(account_a.grant, page_cap=7))
    assert (listing.stopped, listing.pages, served) == ("page_cap", 7, 7)
    assert google_module.PAGE_CAP == 60


def test_a_listing_reads_every_page_and_stops_at_its_limit(
    fake: FakeGoogle, google: GoogleClient
) -> None:
    fake.seed("bulk")
    account = connect(fake, "e2e-bulk@example.org")
    listing = run(google.list_all_messages(account.grant))
    assert (listing.stopped, listing.pages, len(listing.refs)) == ("end", 3, 300)
    assert len({ref.id for ref in listing.refs}) == 300
    assert run(google.list_all_messages(account.grant, limit=120)).stopped == "limit"


def test_history_list_404_is_history_expired(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, sleeps: list[float]
) -> None:
    box = fake.mailboxes[ALICE]
    start = str(box.history_id)
    box.add({"id": "m-new", "headers": [["Subject", "New"]], "text": "new"})
    box.relabel("m-new", add=["STARRED"], remove=["INBOX"])
    box.delete("m-new")
    page = run(google.list_history(account_a.grant, start))
    assert [sorted(set(record) - {"id", "messages"}) for record in page.records] == [
        ["messagesAdded"],
        ["labelsAdded"],
        ["labelsRemoved"],
        ["messagesDeleted"],
    ]
    assert page.history_id == str(box.history_id)
    added = run(google.list_history(account_a.grant, start, history_types=["messageAdded"]))
    assert len(added.records) == 1
    box.history_floor = int(start) + 1
    with pytest.raises(errors.HistoryExpiredError) as caught:
        run(google.list_history(account_a.grant, start))
    assert (caught.value.code, caught.value.status) == ("history_expired", 404)
    assert sleeps == []


def test_a_message_id_listed_then_gone_is_skipped_and_counted(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    page = run(google.list_messages(account_a.grant, q='"Mistral AI"'))
    ids = [ref.id for ref in page.refs]
    assert "m-mistral-1" in ids
    assert "m-mistral-2" in ids
    fake.mailboxes[ALICE].delete("m-mistral-2")  # deleted in Gmail between the list and the get
    fetched = run(google.get_messages(account_a.grant, ids))
    assert fetched.gone == 1
    assert "m-mistral-2" not in [message["id"] for message in fetched.messages]
    assert len(fetched.messages) == len(ids) - 1
    with pytest.raises(errors.NotFoundError) as caught:
        run(google.get_message(account_a.grant, "m-mistral-2"))
    assert (caught.value.code, errors.stored_code(caught.value)) == ("not_found", "google_down")


def test_two_mailboxes_on_the_fake_never_mix(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, account_b: Connected
) -> None:
    async def scenario() -> None:
        a = await google.list_all_messages(account_a.grant, q='"Mistral AI"')
        b = await google.list_all_messages(account_b.grant, q='"Mistral AI"')
        assert a.refs
        assert b.refs
        assert not {ref.id for ref in a.refs} & {ref.id for ref in b.refs}
        assert {ref.id for ref in b.refs} == {"b-mistral-1", "b-mistral-2"}
        # B's ids do not exist for A, and the other way round.
        with pytest.raises(errors.NotFoundError):
            await google.get_message(account_a.grant, "b-mistral-1")
        with pytest.raises(errors.NotFoundError):
            await google.get_thread(account_a.grant, "b-t-mistral")
        with pytest.raises(errors.NotFoundError):
            await google.get_message(account_b.grant, "m-mistral-1")
        # One id that exists in both mailboxes answers each user with their own mail.
        mine = await google.get_message(account_a.grant, "m-shared-id")
        theirs = await google.get_message(account_b.grant, "m-shared-id")
        assert "first mailbox only" in mine["snippet"]
        assert "second mailbox only" in theirs["snippet"]
        assert (await google.get_profile(account_a.grant)).email == ALICE
        assert (await google.get_profile(account_b.grant)).email == BOB
        # A's token cache never serves B, even for a grant that claims A's user id.
        stolen = Grant(account_a.user_id, account_b.grant.refresh_token)
        assert (await google.get_profile(stolen)).email == BOB
        assert (await google.get_profile(account_a.grant)).email == ALICE

    run(scenario())
    assert {call["email"] for call in fake.calls_of("messages.get")} == {ALICE, BOB}


# --- the quota counter ----------------------------------------------------------------------


def test_the_client_counts_quota_units_per_user_and_minute_and_refuses_over_half_the_rate(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, account_b: Connected, clock: Clock
) -> None:
    assert USER_UNITS_PER_MINUTE == google_module.GOOGLE_USER_UNITS_PER_MINUTE // 2 == 3000
    clock.now = 60_000.0  # the start of a minute
    run(google.get_profile(account_a.grant))
    run(google.list_messages(account_a.grant))
    assert (
        google.budget.used(account_a.user_id) == UNITS["getProfile"] + UNITS["messages.list"] == 6
    )
    google.budget.spend(account_a.user_id, USER_UNITS_PER_MINUTE - 6 - 4)
    clock.advance(20)
    before = len(fake.calls)
    with pytest.raises(errors.QuotaError) as caught:
        run(google.get_message(account_a.grant, "m-mistral-1"))
    assert (caught.value.reason, caught.value.retry_after, caught.value.status) == (
        "local_budget",
        40,
        None,
    )
    assert len(fake.calls) == before  # refused before any request left
    run(google.get_message(account_b.grant, "b-mistral-1"))  # another user has a budget of his own
    clock.advance(40)
    run(google.get_message(account_a.grant, "m-mistral-1"))  # a new minute
    assert google.budget.used(account_a.user_id) == UNITS["messages.get"]


def test_the_account_backoff_doubles_from_5_minutes_to_6_hours() -> None:
    delays = []
    current: int | None = None
    for _ in range(9):
        current = next_backoff(current)
        delays.append(current)
    assert delays == [300, 600, 1200, 2400, 4800, 9600, 19200, 21600, 21600]


# --- OAuth ----------------------------------------------------------------------------------


def test_the_code_exchange_userinfo_and_revoke_round_trip(
    fake: FakeGoogle, google: GoogleClient
) -> None:
    verifier = canary.marker("verifier") * 2
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    redirect = "http://localhost/api/mail/oauth/callback"
    url = google.consent_url(
        redirect_uri=redirect, state="state-1", code_challenge=challenge, login_hint=ALICE
    )
    assert url.startswith("http://fake-google.test/o/oauth2/v2/auth?")
    page = fake.handle("GET", url.removeprefix("http://fake-google.test"), {})
    assert page.status == 200  # type: ignore[operator]
    assert b'name="decision" value="allow"' in page.body
    form = httpx.QueryParams(
        {
            "client_id": fake.client_id,
            "redirect_uri": redirect,
            "state": "state-1",
            "code_challenge": challenge,
            "scope": " ".join(google_module.SCOPES),
            "account": ALICE,
            "granted": [google_module.SCOPE_EMAIL, google_module.SCOPE_READONLY],
            "decision": "allow",
        }
    )
    decision = fake.handle("POST", "/o/oauth2/v2/auth/decision", {}, str(form).encode())
    back = httpx.URL(decision.headers["Location"])
    assert decision.status == 302
    assert back.params["state"] == "state-1"
    code = back.params["code"]

    async def scenario() -> None:
        with pytest.raises(errors.OAuthError) as wrong:
            await google.exchange_code(
                code="4/0not-a-code", code_verifier=verifier, redirect_uri=redirect
            )
        assert (wrong.value.code, wrong.value.reason) == ("oauth_refused", "invalid_grant")
        tokens = await google.exchange_code(
            code=code, code_verifier=verifier, redirect_uri=redirect
        )
        # The user unticked gmail.send at Google: the grant says so (assumption A7).
        assert google_module.SCOPE_SEND not in tokens.scopes
        assert google_module.SCOPE_READONLY in tokens.scopes
        assert tokens.refresh_token
        assert tokens.expires_in == 3599
        info = await google.userinfo(tokens.access_token)
        assert (info.email, info.email_verified) == (ALICE, True)
        with pytest.raises(errors.OAuthError):  # a code works once
            await google.exchange_code(code=code, code_verifier=verifier, redirect_uri=redirect)
        grant = Grant("user-1", tokens.refresh_token)
        assert (await google.get_profile(grant)).email == ALICE
        await google.revoke(tokens.refresh_token)
        await google.revoke(tokens.refresh_token)  # already dead at Google: still a success
        google.forget("user-1")
        with pytest.raises(errors.RevokedError):
            await google.get_profile(grant)

    run(scenario())
    hidden = canary.marker("1//refresh")
    assert hidden not in repr(Grant("u", hidden))


def test_a_wrong_pkce_verifier_is_refused_by_the_fake(
    fake: FakeGoogle, google: GoogleClient
) -> None:
    redirect = "http://localhost/api/mail/oauth/callback"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(b"right" * 10).digest()).decode().rstrip("=")
    )
    form = httpx.QueryParams(
        {
            "client_id": fake.client_id,
            "redirect_uri": redirect,
            "state": "s",
            "code_challenge": challenge,
            "scope": " ".join(google_module.SCOPES),
            "account": ALICE,
            "granted": list(google_module.SCOPES),
            "decision": "allow",
        }
    )
    location = fake.handle("POST", "/o/oauth2/v2/auth/decision", {}, str(form).encode()).headers[
        "Location"
    ]
    code = httpx.URL(location).params["code"]
    with pytest.raises(errors.OAuthError) as caught:
        run(google.exchange_code(code=code, code_verifier="wrong" * 10, redirect_uri=redirect))
    assert caught.value.reason == "invalid_grant"


def test_without_an_oauth_client_a_token_call_is_refused_before_any_request(
    fake: FakeGoogle,
) -> None:
    client = GoogleClient(settings_for(), transport=fake.transport())
    with pytest.raises(errors.OAuthError) as caught:
        run(client.get_profile(Grant("u", "1//" + "r" * 40)))
    assert caught.value.reason == "invalid_client"
    assert fake.calls == []


# --- where the calls go ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"google_api_base_url": None},
        {"google_token_base_url": None},
        {"google_auth_url": None},
        {"google_api_base_url": "https://gmail.googleapis.com"},
        {"google_token_base_url": "https://oauth2.googleapis.com"},
        {"google_auth_url": "https://accounts.google.com/o/oauth2/v2/auth"},
        {"google_api_base_url": "https://www.googleapis.com"},
    ],
    ids=[
        "api unset",
        "token unset",
        "auth unset",
        "gmail.googleapis.com",
        "oauth2.googleapis.com",
        "accounts.google.com",
        "www.googleapis.com",
    ],
)
def test_a_real_google_address_used_under_app_env_test_raises(
    overrides: dict[str, str | None],
) -> None:
    with pytest.raises(errors.RealGoogleInTestError, match="real Google address"):
        GoogleClient(settings_for(**overrides))


def test_unset_the_addresses_are_googles_and_production_takes_nothing_else() -> None:
    development = settings_for(
        app_env="development",
        google_auth_url=None,
        google_token_base_url=None,
        google_api_base_url=None,
    )
    found = google_module.endpoints(development)
    assert found.auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert found.token_url == "https://oauth2.googleapis.com/token"
    assert found.revoke_url == "https://oauth2.googleapis.com/revoke"
    assert found.userinfo_url == "https://openidconnect.googleapis.com/v1/userinfo"
    assert found.gmail_url == "https://gmail.googleapis.com/gmail/v1/users/me"
    # The settings refuse the three variables in production; the client checks again, so a
    # Settings object built some other way cannot point production at a fake either.
    forged = development.model_copy(
        update={"app_env": "production", "google_api_base_url": "http://gmailstub:8098"}
    )
    with pytest.raises(errors.RealGoogleInTestError, match="not a Google address"):
        google_module.endpoints(forged)
    assert google_module.is_google_host("gmail.googleapis.com")
    assert not google_module.is_google_host("gmail.googleapis.com.evil.example")
    assert not google_module.is_google_host("notgoogle.com")


def test_the_client_has_no_function_that_sends_a_mail() -> None:
    source = inspect.getsource(google_module)
    for word in ("messages/send", "messages.send", "/send", '"raw"', "drafts"):
        assert word not in source, (
            f"app/mail/google.py names {word}: sending belongs to gate.py (M12)"
        )
    public = [name for name in vars(GoogleClient) if not name.startswith("_")]
    assert not [name for name in public if "send" in name.lower() or "draft" in name.lower()]
    assert "messages.send" not in UNITS


def test_an_id_that_is_not_an_id_never_reaches_an_address(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    for bad in ("../profile", "a/b", "a?format=raw", "", "a b"):
        with pytest.raises(errors.GoogleDownError) as caught:
            run(google.get_message(account_a.grant, bad))
        assert caught.value.reason == "bad_id"
    assert fake.calls_of("messages.get") == []


# --- privacy: tokens and mail content never reach a log, an error or an arq result -----------


def test_tokens_and_googles_text_never_reach_a_log_an_exception_or_a_pickled_result(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    planted = {
        "refresh": canary.marker("1//refresh"),
        "access": canary.marker("ya29.access"),
        "google_text": canary.marker("googletext"),
        "subject": canary.marker("subject"),
        "search": canary.marker("search"),
        "secret": canary.marker("clientsecret"),
        "header": canary.marker("header"),
    }
    settings = settings_for(
        google_oauth_client_id="planted-client", google_oauth_client_secret=planted["secret"]
    )
    state = {"mode": "ok"}

    def respond(request: httpx.Request) -> httpx.Response:
        body = unquote(request.content.decode())
        if request.url.path == "/token":
            assert planted["refresh"] in body
            assert planted["secret"] in body
            if state["mode"] == "invalid_grant":
                return httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": planted["google_text"]},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": planted["access"],
                    "expires_in": 3599,
                    "scope": "s",
                    "token_type": "Bearer",
                },
            )
        assert request.headers["authorization"] == f"Bearer {planted['access']}"
        if state["mode"] == "ok":
            return httpx.Response(
                200, json={"messages": [{"id": "m1", "threadId": "t1"}], "resultSizeEstimate": 1}
            )
        status = int(state["mode"])
        error = {
            "code": status,
            "message": f"{planted['google_text']} for {planted['subject']}",
            "errors": [
                {
                    "message": planted["google_text"],
                    "domain": "usageLimits",
                    "reason": "rateLimitExceeded"
                    if status in (403, 429)
                    else planted["google_text"],
                }
            ],
            "status": planted["google_text"],
        }
        return httpx.Response(status, json={"error": error}, headers={"X-Echo": planted["header"]})

    client = make_client(fake, settings, clock, sleeps, transport=httpx.MockTransport(respond))
    grant = Grant("3f0c1f0e-user", planted["refresh"])
    caught: list[BaseException] = []
    with caplog.at_level(logging.DEBUG):
        run(client.list_messages(grant, q=planted["search"]))
        for mode in ("429", "403", "500", "404", "400", "401", "invalid_grant"):
            state["mode"] = mode
            if mode == "invalid_grant":
                client.forget(grant.user_id)
            try:
                run(client.list_messages(grant, q=planted["search"]))
            except errors.GoogleError as error:
                caught.append(error)
            else:
                pytest.fail(f"mode {mode} should have raised")
    assert [type(error).__name__ for error in caught] == [
        "QuotaError",
        "QuotaError",
        "GoogleDownError",
        "NotFoundError",
        "GoogleDownError",
        "RevokedError",
        "RevokedError",
    ]
    assert caplog.records, "the client logged its failures: an empty log would prove nothing"
    shown = []
    for error in caught:
        shown += [str(error), repr(error), repr(error.args), repr(vars(error))]
        shown.append("".join(traceback.format_exception(error)))
        shown.append(pickle.dumps(error))  # what arq would store as the result of a job
        again = pickle.loads(pickle.dumps(error))  # noqa: S301, our own object
        assert (type(again), again.code, again.status, again.reason) == (
            type(error),
            error.code,
            error.status,
            error.reason,
        )
    assert canary.find(planted.values(), logs=caplog.records, values=shown) == []
    assert canary.find(planted.values(), logs=[caplog.text]) == []
    # The check sees what it looks for: the same search over a line that does hold a marker.
    assert canary.find(planted.values(), logs=[f"Authorization: Bearer {planted['access']}"])


def test_httpx_does_not_log_the_address_of_a_mail_call_and_logs_others_as_before(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected, caplog: pytest.LogCaptureFixture
) -> None:
    search = canary.marker("searchterm")
    with caplog.at_level(logging.DEBUG):
        run(google.list_messages(account_a.grant, q=search))

        async def other() -> None:
            async with httpx.AsyncClient(transport=fake.transport()) as plain:
                await plain.get("http://fake-google.test/healthz")

        run(other())
    assert canary.find([search], logs=caplog.records) == []
    assert [
        record
        for record in caplog.records
        if record.name == "httpx" and "healthz" in record.getMessage()
    ]


def test_tokens_are_registered_with_the_scrubber_only_while_a_call_uses_them(
    fake: FakeGoogle,
    mail_settings: Settings,
    clock: Clock,
    sleeps: list[float],
    account_a: Connected,
) -> None:
    from app import logging_config

    inner = fake.transport()
    during: list[tuple[str, ...]] = []

    def watch(request: httpx.Request) -> httpx.Response:
        during.append(logging_config._hidden)
        return inner.handler(request)

    before = logging_config._hidden
    client = make_client(fake, mail_settings, clock, sleeps, transport=httpx.MockTransport(watch))
    run(client.get_profile(account_a.grant))
    assert account_a.grant.refresh_token in during[0]  # the refresh
    access = next(iter(fake.access_tokens))
    assert access in during[1]
    assert account_a.grant.refresh_token in during[1]
    assert logging_config.clean_text(f"Bearer {access}") == f"Bearer {access}"  # no longer hidden
    assert logging_config._hidden == before


def test_a_mail_log_line_takes_ids_and_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    subject = canary.marker("subject line")
    with caplog.at_level(logging.INFO, logger="app.mail"):
        log.event(
            "mail.test.line",
            user_id="3f0c1f0e-9d2a",
            message_id="18c2a4f9e1",
            count=3,
            code=subject,
            reason="someone@example.org",
        )
    record = caplog.records[-1]
    assert (record.user_id, record.message_id, record.count) == ("3f0c1f0e-9d2a", "18c2a4f9e1", 3)  # type: ignore[attr-defined]
    assert (record.code, record.reason) == (log.REFUSED, log.REFUSED)  # type: ignore[attr-defined]
    assert canary.find([subject], logs=caplog.records) == []
    with pytest.raises(ValueError, match="not a mail log field"):
        log.event("mail.test.line", subject="anything")
    with pytest.raises(ValueError, match="fixed dotted word"):
        log.event(f"mail {subject}")
