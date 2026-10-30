"""Job alert e-mails in a sweep (SPEC-mail.md, "Job alert e-mails: a narrow exception").

The sweep's hook (`sweep.run_sweep(alerts=...)`, run before the job websites) reads, for each
provider the person turned on in Find jobs setup (`search.alert_emails`, frozen in the sweep's
scope), the alert e-mails received since that provider's cursor, and stores the postings they
list through the run's commits (`sweep.Commits`): the same never apply rules, duplicates and
scoring as every finding.

**What is read.** One Gmail search per provider, through the app's MCP client (`mcp_client.py`,
the mailbox of the sweep's own user and nobody else's):

    from:(<the provider's exact senders>) after:<cursor in seconds> -in:spam -in:trash

then each listed message in full, oldest first, at most MAX_MESSAGES a sweep (the rest waits for
the next one). Nothing else of the mailbox is listed or opened for this purpose. A message is
read only when `mail/parse.py` finds one `From` whose address is exactly one of the provider's
senders (`providers.py`), Gmail authenticated it (`mail/auth.py`: DMARC or an aligned DKIM pass
in Gmail's own header, the veto of an explicit `dmarc=fail`, no fetched mail), and it is inbound;
any other message is counted as `refused` and its body is not parsed.

**How it is parsed.** By the provider's deterministic parser (never a model): its text part
first; the HTML part only when the text part gave nothing and the provider has an HTML reading,
and then only through the parser service's text version (`parser_client.sanitise_mail_html`),
never parsed in this process, and every parser runs off the event loop. A mail that gives no
job is counted (`empty_messages`): a format that changed shows there, and so does a mail whose
HTML part the parser service refused as a file. A mail that one of the pure parsers
(`parse.parse`, the provider's text and HTML readings) fails on is counted (`failed_messages`,
the error logged without its text) and the cursor moves past it: one bad mail never stops its
provider. Anything else that fails while a mail is read (the parser service down, busy or too
slow, the filters, the store) stops the provider with the cursor on the previous mail, so the
next sweep reads that mail again.

**What is stored.** Each job as a finding: the title, the company and the location the mail
states, the snippet as the posting's text (an alert never holds the full description), the
canonical address built from the job's id (the tracked link is dropped and never fetched), the
provider as the source ("mail:indeed"), and in the sweep event's `discovery` the provider and the
Gmail id of its mail. The person's unwanted titles and places are dropped before the store, and
when she chose contract types, a job of another type (the engine's `kind`) is too.

**The cursor** is the date of the newest message handled, kept in the provider's entry of the
sweep row (`cursor`, with the connection it belongs to) and read back from the person's latest
sweeps: no table of its own. Every sweep where the provider runs writes it again, a sweep that
found no new alert included, so it never ages out of those sweeps. It moves only after the
message's postings are stored, so a crash reads a message again (its postings are then
duplicates) and never skips one. The connection is the account's `connected_at`: reconnecting
the same Google account keeps it, and the cursor with it; a disconnect deletes the account, so
the next connection starts without a cursor. Without one (the first sweep of a provider, or a
new connection) the search goes FIRST_DAYS back, never before the connection. A provider that
stops before it reads (a connection to renew, no OAuth client) still writes the cursor it had.
A listing cut at MAX_LIST_PAGES lacks the oldest messages (Gmail lists the newest first): its
messages are read, but the cursor stays where it was (`listing_truncated`), so none is skipped.

**Time.** The alert e-mails of every provider share ALERTS_TIME_S of the sweep, so they never
starve the job websites that run after them; a provider out of time ends `time_limit` with the
cursor on its last mail handled.

**The stats entry** of each provider: host "mail:<key>", kind "alerts", mode "mail", the
outcome of `sweep.finish_source`, `found` and `postings` as the commits count them, `messages`,
`refused`, `empty_messages`, `failed_messages`, `dropped`, `unwanted`, `other_contract`. A
provider that cannot read ends with a code: `mail_not_connected`, `mail_needs_reconnect`,
`mail_unavailable`, `time_limit`, `store_failed`, `mail_failed`.

Nothing of a mail (address, subject, text) is logged or put in a stats entry: ids and counts.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.config import Settings
from app.jobs import sweep
from app.jobs.importer import kind
from app.jobs.models import Sweep
from app.mail import auth, parse
from app.mail.alerts.providers import PROVIDERS, Provider
from app.mail.alerts.text import AlertJob, Parsed
from app.mail.google import SCOPE_READONLY
from app.mail.mcp_client import MailConnectionError, MailRequestUnknownError, MailServerDownError
from app.mail.models import AccountStatus, MailAccount
from app.profile import matching

logger = logging.getLogger("app.mail.alerts")

MAX_MESSAGES = 40  # alert e-mails of one provider read in one sweep, oldest first
LIST_PAGE = 100
MAX_LIST_PAGES = 20  # a listing longer than this is truncated: the cursor then stays
FIRST_DAYS = 14  # how far back the first sweep of a provider reads
ALERTS_TIME_S = 120.0  # the alert e-mails' share of a sweep, every provider together
CURSOR_SWEEPS = 30  # the person's latest sweeps where a provider's cursor is looked for
BATCH = 25  # findings handed to one commit
NOTE = "From a job alert e-mail: the full description is on the posting's page."


class Mailbox(Protocol):
    """What this reads of the mailbox: `mcp_client.GmailMcp`, or a fake in the tests."""

    async def list_messages(
        self, account: MailAccount, *, q: str = "", page_token: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]: ...  # fmt: skip

    async def get_message(
        self, account: MailAccount, id: str, *, format: str = "full",
        metadata_headers: list[str] | None = None,
    ) -> dict[str, Any]: ...  # fmt: skip


# The parser service's text version of a mail's HTML part, None when it failed.
HtmlText = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class AlertFinding:
    """One job of an alert, in the shape `sweep._store_finding` reads."""

    title: str
    location: str
    url: str
    text: str
    company: str | None
    published_on: str | None
    evidence: dict[str, str]
    detail_read_at: str


@dataclass(frozen=True)
class Batch:
    findings: tuple[AlertFinding, ...]


class _StopError(Exception):
    """The provider stops here, with this code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _UnreadableError(Exception):
    """A pure parser failed on one mail: counted and passed (module docstring)."""

    def __init__(self, error: Exception) -> None:
        super().__init__(type(error).__name__)
        self.error = error


async def _pure(function: Callable[..., Any], *args: Any) -> Any:
    """A pure parser, off the event loop: whatever it raises is `_UnreadableError`."""
    try:
        return await asyncio.to_thread(function, *args)
    except Exception as error:
        raise _UnreadableError(error) from None


@dataclass
class _Filters:
    titles: Any = None
    places: Any = None
    contracts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, search: Any) -> _Filters:
        def compiled(terms: Any) -> Any:
            words = getattr(terms, "terms", None)
            if not isinstance(words, list):
                return None
            return matching.compile_terms(words, getattr(terms, "advanced_regex", False) is True)

        return cls(
            compiled(getattr(search, "title_exclude", None)),
            compiled(getattr(search, "location_exclude", None)),
            frozenset(getattr(search, "contract_types", None) or ()),
        )

    def place(self, job: AlertJob) -> str:
        """Where a job goes: "unwanted", "other_contract" or "kept"."""
        if (self.titles is not None and self.titles.search(job.title)) or (
            self.places is not None and job.location and self.places.search(job.location)
        ):
            return "unwanted"
        if self.contracts and kind(job.title, job.location, job.url).value not in self.contracts:
            return "other_contract"
        return "kept"


def query(provider: Provider, after_ms: int) -> str:
    """The one Gmail search of a provider: its exact senders, after the cursor, never spam or
    trash. Gmail's `from:` is a pre-filter only; `refused` decides on the parsed message."""
    return f"from:({' OR '.join(provider.senders)}) after:{after_ms // 1000} -in:spam -in:trash"


def dmarc_vetoed(raw: Any) -> bool:
    """Whether the one `Authentication-Results` header `mail/auth.py` reads (the topmost) holds
    an explicit `dmarc=fail`: the owner's veto of 2026-10-21 (#190), which auth.py gets with
    task M3b and the alert e-mails apply already. Anything unreadable is a veto."""
    payload = raw.get("payload") if isinstance(raw, dict) else None
    headers = payload.get("headers") if isinstance(payload, dict) else None
    if not isinstance(headers, list):
        return True
    for header in headers:
        if not isinstance(header, dict) or not isinstance(header.get("name"), str):
            return True
        if header["name"].strip().lower() == "authentication-results":
            read = auth.read_results(header.get("value"))
            return read is None or any(
                result.method == "dmarc" and result.result == "fail" for result in read[1]
            )
    return True


def refused(message: parse.ParsedMessage, provider: Provider, raw: Any | None = None) -> str | None:
    """Why a message is not read for `provider`, None when it is (module docstring). `raw` is
    Gmail's answer the message was parsed from, for the DMARC veto."""
    if message.from_addr is None or message.from_addr not in provider.senders:
        return "sender_not_allowed"
    if message.suspicious or not message.trusted:
        return "not_authenticated"
    if raw is not None and dmarc_vetoed(raw):
        return "not_authenticated"
    if message.direction != "inbound":
        return "not_inbound"
    return None


def finding(job: AlertJob, provider: Provider, message: parse.ParsedMessage) -> AlertFinding:
    head = "\n".join(part for part in (job.title, job.company, job.location) if part)
    text = "\n\n".join(part for part in (head, job.snippet, NOTE) if part)
    return AlertFinding(
        title=job.title,
        location=job.location,
        url=job.url,
        text=text,
        company=job.company,
        published_on=None,
        evidence={
            "alert": provider.key,
            "gmail_message_id": message.gmail_message_id,
            "description": "snippet",
        },
        detail_read_at=message.received_at.isoformat(),
    )


def read_jobs(message: parse.ParsedMessage, provider: Provider) -> Parsed:
    return provider.parse_text(message.body_text) if message.body_text else Parsed((), 0)


# --- the database side ---------------------------------------------------------------------


def load_account(sessions: sessionmaker[Session], user_id: uuid.UUID) -> MailAccount | None:
    with sessions() as db:
        account = db.scalar(
            sa.select(MailAccount)
            .where(MailAccount.user_id == user_id)
            .options(undefer(MailAccount.refresh_token_encrypted))
        )
        if account is not None:
            db.expunge(account)
        db.rollback()
    return account


def load_cursor(
    sessions: sessionmaker[Session], user_id: uuid.UUID, source_id: str, connection: str
) -> int | None:
    """The newest cursor of a provider among the person's latest sweeps, for this connection."""
    with sessions() as db:
        rows = db.scalars(
            sa.select(Sweep.stats)
            .where(Sweep.user_id == user_id)
            .order_by(Sweep.started_at.desc())
            .limit(CURSOR_SWEEPS)
        ).all()
        db.rollback()
    best: int | None = None
    for stats in rows:
        for entry in (stats or {}).get("sources", []) if isinstance(stats, dict) else []:
            cursor = entry.get("cursor") if isinstance(entry, dict) else None
            if (
                isinstance(cursor, dict)
                and entry.get("source_id") == source_id
                and cursor.get("account") == connection
                and type(cursor.get("after_ms")) is int
            ):
                best = max(best or 0, cursor["after_ms"])
    return best


def _log_failure(plan: Any, provider: Provider, error: Exception, what: str = "") -> None:
    """A mail that could not be read, or a provider that failed: the error's type only, as its
    message or traceback may quote the mail's text (a decoding error quotes its bytes, a
    database error the posting it was given)."""
    logger.warning(
        what or "an alert e-mail could not be read",
        extra={
            "sweep_id": str(plan.sweep_id),
            "provider": provider.key,
            "error": type(error).__name__,
        },
    )


def _before(stamp: int, cursor: int | None, after: int) -> bool:
    """Whether a message is not this sweep's: handled by an earlier sweep (at or before the
    cursor), or older than the search's start (Gmail's `after:` is a pre-filter only)."""
    return (cursor is not None and stamp <= cursor) or stamp < after


def _internal_ms(raw: Any) -> int | None:
    """Gmail's date of a message (`internalDate`, milliseconds) when it reads as one."""
    stamp = raw.get("internalDate") if isinstance(raw, dict) else None
    if isinstance(stamp, str) and stamp.isascii() and stamp.isdigit() and len(stamp) <= 14:
        return int(stamp)
    return None


def _account_error(account: MailAccount | None) -> str | None:
    if account is None or account.disconnecting_at is not None:
        return "mail_not_connected"
    if SCOPE_READONLY not in (account.scopes or []):
        return "mail_not_connected"
    if account.status is AccountStatus.needs_reconnect:
        return "mail_needs_reconnect"
    return None


def _answer(found: dict[str, Any]) -> dict[str, Any]:
    error = found.get("error") if isinstance(found, dict) else "invalid_answer"
    if error in ("needs_reconnect", "token_expired", "not_connected"):
        raise _StopError("mail_needs_reconnect")
    if error is not None:
        raise _StopError("mail_unavailable")
    return found


# --- the run ---------------------------------------------------------------------------------


@dataclass
class Reader:
    """The alert e-mails' run of one sweep (module docstring)."""

    sessions: sessionmaker[Session]
    mailbox: Mailbox | None
    html_text: HtmlText
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    async def __call__(
        self, plan: Any, stats: sweep.Stats, progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:  # fmt: skip
        keys = [key for key in sweep.alert_keys(getattr(plan, "search", None)) if key in PROVIDERS]
        if not keys:
            return
        # Every provider together: the job websites run after them (ALERTS_TIME_S).
        deadline = min(deadline, self.monotonic() + ALERTS_TIME_S)
        account = await asyncio.to_thread(load_account, self.sessions, plan.user_id)
        filters = _Filters.of(plan.search)
        for key in keys:
            provider = PROVIDERS[key]
            entry = sweep.bind_source(
                stats,
                "mail",
                (key,),
                {
                    **sweep.alert_entry(key),
                    "found": 0,
                    "postings": 0,
                    "messages": 0,
                    "refused": 0,
                    "empty_messages": 0,
                    "failed_messages": 0,
                    "dropped": 0,
                    "error": None,
                },
            )
            stats.current_board = entry["source_id"]
            await progress()
            commits = getattr(plan, "commits", None)
            if commits is not None:
                commits.admit(entry["source_id"])
            try:
                cursor: int | None = None
                if account is not None:
                    # Carried forward by every sweep where the provider runs, one that stops
                    # before reading included: the cursor is looked for in the latest
                    # CURSOR_SWEEPS sweeps only.
                    connection = account.connected_at.isoformat()
                    cursor = await asyncio.to_thread(
                        load_cursor, self.sessions, plan.user_id, entry["source_id"], connection
                    )
                    if cursor is not None:
                        entry["cursor"] = {"after_ms": cursor, "account": connection}
                error = _account_error(account)
                if error is None and self.mailbox is None:
                    error = "mail_unavailable"
                if error is not None or account is None or self.mailbox is None:
                    raise _StopError(error or "mail_unavailable")
                await self._provider(
                    plan, provider, self.mailbox, account, cursor, entry, filters, deadline,
                    commits,
                )  # fmt: skip
            except _StopError as stop:
                entry["error"] = stop.code
            except MailConnectionError:
                entry["error"] = "mail_needs_reconnect"
            except (MailServerDownError, MailRequestUnknownError):
                entry["error"] = "mail_unavailable"
            except sweep.StoreRefusedError:
                entry["error"] = "store_failed"
            except Exception as error:
                _log_failure(plan, provider, error, "alert e-mails failed")
                entry["error"] = "mail_failed"
            if commits is not None and not await commits.settle():
                entry["error"] = entry["error"] or "store_failed"
            sweep.finish_source(entry)
            stats.current_board = None
            await progress()

    async def _provider(
        self, plan: Any, provider: Provider, mailbox: Mailbox, account: MailAccount,
        cursor: int | None, entry: dict[str, Any], filters: _Filters, deadline: float,
        commits: Any,
    ) -> None:  # fmt: skip
        connection = account.connected_at.isoformat()
        after = cursor
        if after is None:
            # Nothing older than the connection, and at most FIRST_DAYS back.
            first = max(self.now() - timedelta(days=FIRST_DAYS), account.connected_at)
            after = int(first.timestamp() * 1000)
        ids: list[str] = []
        page: str | None = None
        for _ in range(MAX_LIST_PAGES):
            if self.monotonic() >= deadline:
                raise _StopError("time_limit")
            listed = _answer(
                await mailbox.list_messages(
                    account, q=query(provider, after), page_token=page, max_results=LIST_PAGE
                )
            )
            for item in listed.get("messages") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"])
            page = listed.get("nextPageToken")
            if not isinstance(page, str) or not page:
                page = None
                break
        # Gmail lists the newest first: a listing cut short lacks the oldest messages, so the
        # cursor must not move past them (module docstring).
        truncated = page is not None
        if truncated:
            entry["listing_truncated"] = True
        ids = list(dict.fromkeys(ids))[::-1]
        newest = cursor

        def advance(stamp: int) -> None:
            nonlocal newest
            newest = max(newest or 0, stamp)
            if not truncated:
                entry["cursor"] = {"after_ms": newest, "account": connection}

        handled = 0
        for index, message_id in enumerate(ids):
            # A message an earlier sweep read (Gmail's `after:` is in seconds) costs a read but
            # no place; reads stay under twice the cap.
            if handled >= MAX_MESSAGES or index >= 2 * MAX_MESSAGES:
                entry["messages_left"] = len(ids) - index
                break
            if self.monotonic() >= deadline:
                raise _StopError("time_limit")
            raw = _answer(await mailbox.get_message(account, message_id, format="full"))
            try:
                message = await _pure(parse.parse, raw, account.email)
            except _UnreadableError as failed:
                if isinstance(failed.error, parse.NotAMessageError):
                    handled += 1
                    entry["refused"] += 1
                    continue
                # A mail the parser cannot read: counted, and passed by the cursor when Gmail
                # gave its date, so it never stops the provider for good.
                _log_failure(plan, provider, failed.error)
                stamp = _internal_ms(raw)
                if stamp is not None and _before(stamp, cursor, after):
                    continue
                handled += 1
                entry["failed_messages"] += 1
                if stamp is not None:
                    advance(stamp)
                continue
            stamp = int(message.received_at.timestamp() * 1000)
            if _before(stamp, cursor, after):
                continue  # read by an earlier sweep, or older than the connection
            handled += 1
            if refused(message, provider, raw) is None:
                kept = await self._kept(plan, provider, message, entry, filters)
                if kept is not None:
                    await self._store(entry, kept, commits)
            else:
                entry["refused"] += 1
            # Only after its postings are stored: a crash before reads it again.
            advance(stamp)

    async def _kept(
        self, plan: Any, provider: Provider, message: parse.ParsedMessage,
        entry: dict[str, Any], filters: _Filters,
    ) -> list[AlertFinding] | None:  # fmt: skip
        """The findings of one read message, None when a pure parser failed on it: the failure
        is counted (`failed_messages`) and the cursor moves past the message, so one bad mail
        never stops its provider for good. Anything else raises and stops the provider with the
        cursor on the previous mail: the parser service down, busy or too slow (`_StopError`
        from `parser_text`), the filters, the findings. Storing is not in here: a store that
        fails stops the provider and keeps the cursor too (`_store`)."""
        entry["messages"] += 1
        try:
            result = await self._parsed(provider, message)
        except _UnreadableError as failed:
            _log_failure(plan, provider, failed.error)
            entry["failed_messages"] += 1
            return None
        return self._sorted(provider, message, result, entry, filters)

    async def _parsed(self, provider: Provider, message: parse.ParsedMessage) -> Parsed:
        # Off the event loop: a hostile part costs parse time, never the sweep's heartbeat.
        result: Parsed = await _pure(read_jobs, message, provider)
        if not result.jobs and message.raw_html and provider.parse_html_text is not None:
            version = await self.html_text(message.raw_html)
            if version:
                result = await _pure(provider.parse_html_text, version)
        return result

    def _sorted(
        self, provider: Provider, message: parse.ParsedMessage, result: Parsed,
        entry: dict[str, Any], filters: _Filters,
    ) -> list[AlertFinding]:  # fmt: skip
        entry["dropped"] += result.dropped
        if not result.jobs:
            entry["empty_messages"] += 1
            return []
        kept: list[AlertFinding] = []
        for job in result.jobs:
            place = filters.place(job)
            if place == "kept":
                kept.append(finding(job, provider, message))
            else:
                entry[place] = entry.get(place, 0) + 1
        return kept

    async def _store(self, entry: dict[str, Any], kept: list[AlertFinding], commits: Any) -> None:
        if not kept:
            return
        if commits is None:
            entry["found"] += len(kept)  # no store (a caller that is not a sweep): counted only
            return
        for start in range(0, len(kept), BATCH):
            chunk = tuple(kept[start : start + BATCH])
            try:
                await commits.save(entry["source_id"], entry["host"], Batch(chunk))
            except sweep.StoreRefusedError:
                raise
            except Exception as error:
                # A commit that failed: its error may quote the postings, so its type only.
                logger.warning(
                    "storing alert postings failed",
                    extra={"source_id": entry["source_id"], "error": type(error).__name__},
                )
                raise _StopError("store_failed") from None


async def parser_text(html: str) -> str | None:
    """The parser service's text version of an HTML part. None when the parser refused it as
    a file (permanent: the mail is counted empty and passed); the parser down, busy, too slow
    or failing on its side stops the provider with the cursor on the previous mail, so the next
    sweep reads that mail again (`_StopError("mail_unavailable")`)."""
    from app.ingestion import parser_client

    try:
        return (await parser_client.sanitise_mail_html(html)).text
    except parser_client.ParserError as error:
        if error.failure == "file":
            return None
        raise _StopError("mail_unavailable") from None


def hook_for(
    settings: Settings, sessions: sessionmaker[Session], keyring: Any
) -> sweep.BrowserHook:
    """The worker's hook. Without an OAuth client or a keyring, a provider that is on ends
    `mail_unavailable` (a visible reason), never silently."""
    from app.mail import accounts
    from app.mail.mcp_client import GmailMcp

    oauth = accounts.build_client(settings)
    mailbox = GmailMcp(settings, oauth, keyring) if oauth is not None and keyring else None
    return Reader(sessions, mailbox, parser_text)
