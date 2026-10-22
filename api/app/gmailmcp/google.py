"""M1 Gmail read routines moved intact to the provider boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Literal

import httpx

from app.mail import log
from app.mail.errors import (
    GoogleDownError,
    GoogleError,
    GoogleTimeoutError,
    NotFoundError,
    TooLargeError,
)
from app.mail.google import (
    CHUNK_BYTES,
    JSON_MAX_BYTES,
    MESSAGE_MAX_BYTES,
    PAGE_CAP,
    PAGE_SIZE,
    RETRY_AFTER_INLINE_S,
    RETRY_DELAYS_S,
    RETRY_JITTER,
    UNITS,
    Answer,
    Fetched,
    GoogleClient,
    Grant,
    HistoryPage,
    Listing,
    MessagePage,
    MessageRef,
    Profile,
    _count,
    _items,
    _loads,
    _safe_id,
    _text,
    decode_attachment,
    raise_for_answer,
)


class GmailGoogle(GoogleClient):
    async def _gmail(
        self,
        grant: Grant,
        call: str,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        *,
        cap: int = JSON_MAX_BYTES,
    ) -> dict[str, Any]:
        url = f"{self.endpoints.gmail_url}{path}"

        async def exchange() -> Answer:
            self.budget.spend(grant.user_id, UNITS[call])
            token = await self._token(grant)
            with log.hide(token):
                return await self._send("GET", url, params=params, bearer=token, cap=cap)

        async def once() -> dict[str, Any]:
            answer = await exchange()
            if answer.status == 401:
                # The token died early (revoked, or Google's clock is ahead): one refresh.
                self.forget(grant.user_id)
                answer = await exchange()
            raise_for_answer(answer, call)
            return _loads(answer.body)

        with log.hide(grant.refresh_token, self._client_secret):
            try:
                return await self._retrying(call, once)
            except GoogleError as error:
                log.event(
                    "mail.google.failed",
                    user_id=grant.user_id,
                    call=call,
                    code=error.code,
                    status=error.status,
                    reason=error.reason,
                )
                raise

    async def get_profile(self, grant: Grant) -> Profile:
        found = await self._gmail(grant, "getProfile", "/profile")
        return Profile(
            email=_text(found, "emailAddress"),
            messages_total=_count(found, "messagesTotal"),
            threads_total=_count(found, "threadsTotal"),
            history_id=_text(found, "historyId"),
        )

    async def list_messages(
        self,
        grant: Grant,
        *,
        q: str | None = None,
        page_token: str | None = None,
        max_results: int = PAGE_SIZE,
        include_spam_trash: bool = False,
    ) -> MessagePage:
        """One page of `messages.list`: ids only."""
        params = [("maxResults", str(max(1, min(500, max_results))))]
        if q:
            params.append(("q", q))
        if page_token:
            params.append(("pageToken", page_token))
        if include_spam_trash:
            params.append(("includeSpamTrash", "true"))
        found = await self._gmail(grant, "messages.list", "/messages", params)
        token = found.get("nextPageToken")
        return MessagePage(
            refs=tuple(
                MessageRef(_text(item, "id"), _text(item, "threadId"))
                for item in _items(found, "messages")
            ),
            next_page_token=token if isinstance(token, str) and token else None,
            estimate=_count(found, "resultSizeEstimate"),
        )

    async def list_all_messages(
        self, grant: Grant, *, q: str | None = None, limit: int = 5000, page_cap: int = PAGE_CAP
    ) -> Listing:
        """Every page of a search, cut at `limit` ids, at `page_cap` pages, and as soon as a
        page token comes back a second time."""
        refs: list[MessageRef] = []
        seen: set[str] = set()
        token: str | None = None
        pages = 0
        stopped: Literal["end", "limit", "page_cap", "page_token_repeated"] = "end"
        while True:
            page = await self.list_messages(grant, q=q, page_token=token)
            pages += 1
            refs.extend(page.refs)
            token = page.next_page_token
            if len(refs) >= limit:
                stopped = "limit"
            elif token is None:
                stopped = "end"
            elif token in seen:
                stopped = "page_token_repeated"
            elif pages >= page_cap:
                stopped = "page_cap"
            else:
                seen.add(token)
                continue
            break
        if stopped in ("page_cap", "page_token_repeated"):
            log.event("mail.google.listing_cut", user_id=grant.user_id, reason=stopped, pages=pages)
        return Listing(tuple(refs[:limit]), pages, stopped)

    async def get_message(
        self,
        grant: Grant,
        message_id: str,
        *,
        format: Literal["full", "metadata"] = "full",
        metadata_headers: Sequence[str] = (),
    ) -> dict[str, Any]:
        """One message as Google splits it (never `format=raw`). Over 8 MB: `TooLargeError`."""
        params = [("format", format)]
        if format == "metadata":
            params += [("metadataHeaders", name) for name in metadata_headers]
        found = await self._gmail(
            grant,
            "messages.get",
            f"/messages/{_safe_id(message_id)}",
            params,
            cap=MESSAGE_MAX_BYTES,
        )
        _text(found, "id")
        return found

    async def get_messages(self, grant: Grant, message_ids: Sequence[str]) -> Fetched:
        """Each message in full, one after the other. An id that is gone (404) is skipped and
        counted; a message over the cap comes back as metadata and is named in `truncated`."""
        messages: list[Mapping[str, Any]] = []
        truncated: set[str] = set()
        gone = 0
        for message_id in message_ids:
            try:
                try:
                    messages.append(await self.get_message(grant, message_id))
                except TooLargeError:
                    messages.append(await self.get_message(grant, message_id, format="metadata"))
                    truncated.add(message_id)
            except NotFoundError:
                gone += 1
        if gone or truncated:
            log.event(
                "mail.google.fetched",
                user_id=grant.user_id,
                count=len(messages),
                gone=gone,
                truncated=len(truncated),
            )
        return Fetched(tuple(messages), gone, frozenset(truncated))

    async def get_thread(
        self,
        grant: Grant,
        thread_id: str,
        *,
        format: Literal["full", "metadata", "minimal"] = "metadata",
    ) -> dict[str, Any]:
        """One conversation with its messages. Not in the plan's list of calls: kept to
        `metadata` by default, so its answer stays under the 2 MB cap of a JSON answer."""
        found = await self._gmail(
            grant, "threads.get", f"/threads/{_safe_id(thread_id)}", [("format", format)]
        )
        _text(found, "id")
        _items(found, "messages")
        return found

    async def list_history(
        self,
        grant: Grant,
        start_history_id: str,
        *,
        history_types: Sequence[str] = (
            "messageAdded",
            "messageDeleted",
            "labelAdded",
            "labelRemoved",
        ),
        page_token: str | None = None,
        max_results: int = PAGE_SIZE,
    ) -> HistoryPage:
        """One page of what changed since `start_history_id`. Too old: `HistoryExpiredError`."""
        params = [
            ("startHistoryId", start_history_id),
            ("maxResults", str(max(1, min(500, max_results)))),
        ]
        params += [("historyTypes", name) for name in history_types]
        if page_token:
            params.append(("pageToken", page_token))
        found = await self._gmail(grant, "history.list", "/history", params)
        token = found.get("nextPageToken")
        return HistoryPage(
            records=tuple(_items(found, "history")),
            next_page_token=token if isinstance(token, str) and token else None,
            history_id=_text(found, "historyId"),
        )

    async def download_attachment(
        self, grant: Grant, message_id: str, attachment_id: str
    ) -> AsyncIterator[bytes]:
        """The bytes of one attachment, in pieces of at most 64 KB, read as they are sent. Two
        downloads run at once in the process and one per user: the next one waits here. Close
        the iterator (`contextlib.aclosing`) when stopping early, so the slot is freed."""
        url = (
            f"{self.endpoints.gmail_url}/messages/{_safe_id(message_id)}"
            f"/attachments/{_safe_id(attachment_id)}"
        )
        await self.slots.acquire(grant.user_id)
        try:
            with log.hide(grant.refresh_token, self._client_secret), log.quiet():
                async for piece in self._download(grant, url):
                    yield piece
        finally:
            self.slots.release(grant.user_id)

    async def _download(self, grant: Grant, url: str) -> AsyncIterator[bytes]:
        refreshed = False
        retries = iter(RETRY_DELAYS_S)
        while True:
            self.budget.spend(grant.user_id, UNITS["attachments.get"])
            token = await self._token(grant)
            try:
                with log.hide(token):
                    async with (
                        self._http() as http,
                        http.stream(
                            "GET",
                            url,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/json",
                            },
                        ) as response,
                    ):
                        if response.status_code == 200:
                            chunks = response.aiter_bytes(CHUNK_BYTES)
                            async for piece in decode_attachment(chunks):
                                yield piece
                            return
                        body = bytearray()
                        async for chunk in response.aiter_bytes(CHUNK_BYTES):
                            body += chunk[: JSON_MAX_BYTES - len(body)]
                        answer = Answer(
                            response.status_code,
                            {name.lower(): value for name, value in response.headers.items()},
                            bytes(body),
                        )
            except httpx.TimeoutException:
                raise GoogleTimeoutError(reason="timeout") from None
            except (httpx.HTTPError, OSError):
                raise GoogleDownError(reason="unreachable") from None
            if answer.status == 401 and not refreshed:
                refreshed = True
                self.forget(grant.user_id)
                continue
            try:
                raise_for_answer(answer, "attachments.get")
            except GoogleError as error:
                delay = next(retries, None)
                short = error.retry_after is None or error.retry_after <= RETRY_AFTER_INLINE_S
                if delay is None or not error.retryable or not short:
                    log.event(
                        "mail.google.failed",
                        user_id=grant.user_id,
                        call="attachments.get",
                        code=error.code,
                        status=error.status,
                    )
                    raise
                await self._sleep(
                    float(error.retry_after)
                    if error.retry_after is not None
                    else delay * (1 + RETRY_JITTER * min(1.0, max(0.0, self._jitter())))
                )
