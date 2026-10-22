"""A user's connected Gmail: connect, consent, disconnect (mail M4; tasks/mail/todo.md, M4,
"Locks" and "Privacy and isolation").

**Connect** is two halves. `begin` makes the state and the PKCE verifier (oauth.py) and gives
the consent address. `complete` is the callback: it opens the state against the session, takes
the verifier (once), exchanges the code, reads `userinfo`, and stores the account. Nothing of a
mailbox is read: the only Gmail call is `getProfile`, for the history id a later sync starts
from. No transaction is open during a call to Google; the account is decided and written in one
short transaction under `lock_sync`, where these rules hold:

- one Google account (`google_sub`) belongs to one app user: another user's is `taken`;
- a connected user must disconnect before connecting another address;
- a user whose account is not `ok` may reconnect the same Google account only;
- a second callback for an account that is already connected changes nothing (two tabs);
- nothing connects while `disconnecting_at` is set.

A grant that is refused is revoked at Google, except where that would end a grant a stored
account lives on: the account of another user (`taken`, note N11: the fresh tokens are dropped
without a call) and the user's own healthy connection (the second tab). Google ends every token
of one person for this client when one of them is revoked.

**Consent** (decision 13) is the pair `assistant_consent_at` and `assistant_consent_version`.
`assistant_allowed(db, user)` is its one check: every task that lets a model read a mail calls
it before any run.

**Disconnect** is the three steps of "Locks": (1) one transaction under `lock_sync` then
`lock_send` sets `disconnecting_at`; (2) the revocation at Google, with no transaction open;
(3) one transaction under both locks purges (purge.py). When Google cannot be reached the
local deletion still happens and the caller is told the access was not removed there.
`finish_disconnect` ends a disconnect whose process died between (1) and (3): the tick of M7
calls it for every user of `stale_disconnects`.

The refresh token is sealed with `crypto.Keyring` and the associated data of the user id and
`models.TOKEN_KIND`: another user's id does not open it. It is never logged, raised or answered.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents.crypto import DecryptError, Keyring
from app.config import Settings
from app.identity.models import User
from app.jobs.service import is_lock_timeout
from app.mail import locks, log, oauth, purge
from app.mail.errors import GoogleError, stored_code
from app.mail.google import SCOPE_READONLY, SCOPE_SEND, GoogleClient, Grant, Tokens
from app.mail.models import TOKEN_KIND, AccountStatus, MailAccount

# The version of the consent text the page shows (web/src/features/mail/gmail-card.tsx). A
# change of that text raises both, and a consent given to an older text no longer counts.
CONSENT_VERSION = 1
# A disconnect whose flag is older than this was interrupted: the tick finishes it.
STALE_DISCONNECT = timedelta(seconds=60)
CODE_MAX_CHARS = 2048


class Outcome(enum.StrEnum):
    """Where a callback ends: the whole list of `/account?gmail=` values."""

    connected = "connected"
    denied = "denied"
    taken = "taken"
    scope = "scope"
    error = "error"


class ConsentError(Exception):
    """The consent could not be recorded. `reason` is `not_connected` or `version`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Disconnected:
    found: bool
    # False when Google did not confirm: the user removes the access there himself.
    revoked: bool


@dataclass(frozen=True)
class Fresh:
    """What a callback holds once Google answered. Nothing of it is ever shown."""

    google_sub: str
    email: str = field(repr=False)
    tokens: Tokens = field(repr=False)
    history_id: str | None = None
    sealed: bytes | None = field(default=None, repr=False)
    key_version: int | None = None


class _Verdict(enum.Enum):
    new = "new"
    reconnect = "reconnect"
    same = "same"
    taken = "taken"
    refused = "refused"


def build_client(settings: Settings) -> GoogleClient | None:
    """The one Google client of this process, or None when no OAuth client is configured."""
    if settings.google_oauth_client_id is None:
        return None
    return GoogleClient(settings)


def state_key(settings: Settings) -> bytes | None:
    if settings.secret_key is None:
        return None
    return oauth.derive_key(settings.secret_key.get_secret_value().encode("utf-8"))


# --- reading ---------------------------------------------------------------------------------


def get(db: Session, user_id: uuid.UUID) -> MailAccount | None:
    """The user's own account row, or None. There is no way to ask for another user's."""
    return db.get(MailAccount, user_id)


def assistant_allowed(db: Session, user: User | uuid.UUID) -> bool:
    """May a model read this user's mail? Only with a recorded consent to the current text, on
    an account that is connected and not being disconnected. The one check of decision 13."""
    user_id = user.id if isinstance(user, User) else user
    account = get(db, user_id)
    return (
        account is not None
        and account.disconnecting_at is None
        and account.assistant_consent_at is not None
        and account.assistant_consent_version == CONSENT_VERSION
    )


def still_connected(db: Session, user_id: uuid.UUID, connected_at: datetime) -> bool:
    """The check a sync makes under `lock_sync` at every batch, and the send gate under
    `lock_send`: the account row exists, no disconnect has begun, and it is the connection the
    caller started with. False: store nothing, send nothing, end."""
    account = get(db, user_id)
    if account is not None:
        db.refresh(account)
    return (
        account is not None
        and account.disconnecting_at is None
        and account.connected_at == connected_at
    )


def grant_of(db: Session, keyring: Keyring, user_id: uuid.UUID) -> Grant | None:
    """The user's grant for the Google client, the token opened, or None without an account.
    Raises crypto.DecryptError when this server's keys do not open it."""
    row = db.scalars(
        sa.select(MailAccount)
        .where(MailAccount.user_id == user_id)
        .options(undefer(MailAccount.refresh_token_encrypted))
    ).one_or_none()
    if row is None:
        return None
    opened = keyring.decrypt(row.refresh_token_encrypted, row.key_version, user_id, TOKEN_KIND)
    return Grant(str(user_id), opened.plaintext)


def stale_disconnects(db: Session, now: datetime) -> list[uuid.UUID]:
    """The users whose disconnect was interrupted (the flag is older than 60 s)."""
    return list(
        db.scalars(
            sa.select(MailAccount.user_id)
            .where(MailAccount.disconnecting_at < now - STALE_DISCONNECT)
            .order_by(MailAccount.user_id)
        )
    )


def rotate_all(db: Session, keyring: Keyring) -> int:
    """Re-encrypt with the current key every refresh token written by another key; how many
    changed. A row this server cannot open raises crypto.DecryptError and nothing changes
    (`python -m app.agents.cli rotate` runs it with the model credentials, in one transaction)."""
    stale = db.scalars(
        sa.select(MailAccount)
        .where(MailAccount.key_version != keyring.version)
        .options(undefer(MailAccount.refresh_token_encrypted))
        .order_by(MailAccount.user_id)
        .with_for_update()
    ).all()
    for row in stale:
        opened = keyring.decrypt(
            row.refresh_token_encrypted, row.key_version, row.user_id, TOKEN_KIND
        )
        sealed = keyring.encrypt(opened.plaintext, row.user_id, TOKEN_KIND)
        row.refresh_token_encrypted = sealed.ciphertext
        row.key_version = sealed.key_version
    db.flush()
    return len(stale)


# --- consent ---------------------------------------------------------------------------------


def set_consent(
    db: Session, user_id: uuid.UUID, *, granted: bool, version: int, now: datetime
) -> MailAccount:
    """Record or withdraw the consent of decision 13, with the version of the text the user
    saw. The caller commits. Raises `ConsentError`."""
    locks.lock_sync(db, user_id)
    account = db.scalars(
        sa.select(MailAccount).where(MailAccount.user_id == user_id).with_for_update()
    ).one_or_none()
    if account is None or account.disconnecting_at is not None:
        raise ConsentError("not_connected")
    if granted:
        if version != CONSENT_VERSION:
            raise ConsentError("version")
        account.assistant_consent_at = now
        account.assistant_consent_version = version
    else:
        account.assistant_consent_at = None
        account.assistant_consent_version = None
    db.flush()
    log.event("mail.account.consent", user_id=str(user_id), status=int(granted))
    return account


# --- connect ---------------------------------------------------------------------------------


async def begin(
    *,
    sessions: sessionmaker[Session],
    google: GoogleClient,
    verifiers: oauth.Verifiers,
    key: bytes,
    redirect_uri: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime,
) -> str | None:
    """The consent address for this session, or None when this user cannot start: he is
    connected (disconnect first) or a disconnect is running. Raises
    `oauth.StoreUnavailableError` when the verifier could not be kept."""

    def allowed() -> tuple[bool, str | None]:
        with sessions() as db:
            account = get(db, user_id)
            if account is None:
                return True, None
            if account.disconnecting_at is not None or account.status is AccountStatus.ok:
                return False, None
            return True, account.email

    may, hint = await run_in_threadpool(allowed)
    if not may:
        return None
    nonce, verifier = oauth.new_nonce(), oauth.new_verifier()
    with log.hide(verifier, nonce):
        await verifiers.put(nonce, verifier)
    state = oauth.sign_state(key, user_id=user_id, session_id=session_id, nonce=nonce, now=now)
    return google.consent_url(
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=oauth.challenge(verifier),
        login_hint=hint,
    )


def _verdict(
    db: Session, user_id: uuid.UUID, google_sub: str
) -> tuple[_Verdict, MailAccount | None]:
    """Under `lock_sync`: what this user may do with this Google account right now."""
    own = db.scalars(
        sa.select(MailAccount).where(MailAccount.user_id == user_id).with_for_update()
    ).one_or_none()
    other = db.scalar(
        sa.select(MailAccount.user_id).where(
            MailAccount.google_sub == google_sub, MailAccount.user_id != user_id
        )
    )
    if other is not None:
        return _Verdict.taken, own
    if own is None:
        return _Verdict.new, None
    if own.disconnecting_at is not None or own.google_sub != google_sub:
        return _Verdict.refused, own
    if own.status is AccountStatus.ok:
        return _Verdict.same, own
    return _Verdict.reconnect, own


def _settle(
    sessions: sessionmaker[Session], user_id: uuid.UUID, fresh: Fresh, now: datetime
) -> _Verdict:
    """One short transaction under `lock_sync`: decide, and store when `fresh` is sealed."""
    try:
        with sessions() as db, db.begin():
            locks.lock_sync(db, user_id)
            verdict, own = _verdict(db, user_id, fresh.google_sub)
            if fresh.sealed is None or fresh.key_version is None:
                return verdict
            scopes = sorted(fresh.tokens.scopes)
            if verdict is _Verdict.new:
                db.add(
                    MailAccount(
                        user_id=user_id,
                        email=fresh.email,
                        google_sub=fresh.google_sub,
                        refresh_token_encrypted=fresh.sealed,
                        key_version=fresh.key_version,
                        scopes=scopes,
                        history_id=fresh.history_id,
                        connected_at=now,
                        status=AccountStatus.ok,
                    )
                )
                db.flush()
            elif verdict is _Verdict.reconnect and own is not None:
                own.email = fresh.email
                own.refresh_token_encrypted = fresh.sealed
                own.key_version = fresh.key_version
                own.scopes = scopes
                own.history_id = own.history_id or fresh.history_id
                own.status = AccountStatus.ok
                own.error_code = None
                own.backoff_until = None
                db.flush()
            return verdict
    except IntegrityError:
        # Two users, one Google account, at the same moment: the unique key decided.
        return _Verdict.taken


async def complete(
    *,
    sessions: sessionmaker[Session],
    google: GoogleClient,
    keyring: Keyring,
    verifiers: oauth.Verifiers,
    key: bytes,
    redirect_uri: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    query: Mapping[str, str],
    now: datetime,
) -> Outcome:
    """The callback. `query` is what Google (or anyone) sent: only `state`, `code` and `error`
    are read, and none of them is echoed, logged or stored."""
    uid = str(user_id)
    try:
        state = oauth.read_state(
            key, query.get("state", ""), user_id=user_id, session_id=session_id, now=now
        )
    except oauth.StateError as refused:
        log.event("mail.oauth.refused", user_id=uid, reason=refused.reason)
        return Outcome.error
    try:
        verifier = await verifiers.take(state.nonce)
    except oauth.StoreUnavailableError:
        log.event("mail.oauth.refused", user_id=uid, reason="store")
        return Outcome.error
    if verifier is None:
        log.event("mail.oauth.refused", user_id=uid, reason="replayed")
        return Outcome.error
    if "error" in query:
        denied = query.get("error") == "access_denied"
        log.event("mail.oauth.refused", user_id=uid, reason="denied" if denied else "google")
        return Outcome.denied if denied else Outcome.error
    code = query.get("code", "")
    if not code or len(code) > CODE_MAX_CHARS:
        log.event("mail.oauth.refused", user_id=uid, reason="no_code")
        return Outcome.error

    with log.hide(code, verifier):
        try:
            tokens = await google.exchange_code(
                code=code, code_verifier=verifier, redirect_uri=redirect_uri
            )
        except GoogleError as failed:
            log.event("mail.oauth.failed", user_id=uid, call="token", code=stored_code(failed))
            return Outcome.error
    with log.hide(tokens.access_token, tokens.refresh_token):
        return await _connect(sessions, google, keyring, user_id, tokens, now)


async def _connect(
    sessions: sessionmaker[Session],
    google: GoogleClient,
    keyring: Keyring,
    user_id: uuid.UUID,
    tokens: Tokens,
    now: datetime,
) -> Outcome:
    uid = str(user_id)
    spare = tokens.refresh_token or tokens.access_token

    async def refuse(reason: str, outcome: Outcome = Outcome.error, revoke: bool = True) -> Outcome:
        if revoke:
            await _revoke(google, user_id, spare)
        google.forget(uid)
        log.event("mail.oauth.refused", user_id=uid, reason=reason)
        return outcome

    try:
        info = await google.userinfo(tokens.access_token)
    except GoogleError as failed:
        # Whose grant this is stays unknown, so it is not revoked: it could be another user's.
        log.event("mail.oauth.failed", user_id=uid, call="userinfo", code=stored_code(failed))
        return await refuse("userinfo", revoke=False)
    fresh = Fresh(google_sub=info.sub, email=info.email, tokens=tokens)
    try:
        verdict = await run_in_threadpool(_settle, sessions, user_id, fresh, now)
        if verdict is _Verdict.taken:
            return await refuse("taken", Outcome.taken, revoke=False)
        if verdict is _Verdict.same:
            return await refuse("already_connected", Outcome.connected, revoke=False)
        if verdict is _Verdict.refused:
            return await refuse("not_allowed")
        if not info.email_verified:
            return await refuse("unverified")
        if SCOPE_READONLY not in tokens.scopes:
            return await refuse("scope", Outcome.scope)
        if tokens.refresh_token is None:
            return await refuse("no_refresh_token")
        try:
            sealed = keyring.encrypt(tokens.refresh_token, user_id, TOKEN_KIND)
            profile = await google.connection_profile(
                MailAccount(
                    user_id=user_id,
                    email=info.email,
                    refresh_token_encrypted=sealed.ciphertext,
                    key_version=sealed.key_version,
                ),
                keyring,
            )
        except GoogleError as failed:
            log.event("mail.oauth.failed", user_id=uid, call="profile", code=stored_code(failed))
            return await refuse("profile")
        sealed = keyring.encrypt(tokens.refresh_token, user_id, TOKEN_KIND)
        fresh = Fresh(
            google_sub=info.sub,
            email=info.email,
            tokens=tokens,
            history_id=profile.history_id or None,
            sealed=sealed.ciphertext,
            key_version=sealed.key_version,
        )
        verdict = await run_in_threadpool(_settle, sessions, user_id, fresh, now)
    except DBAPIError as busy:
        if not is_lock_timeout(busy):
            raise
        return await refuse("busy", revoke=False)
    if verdict is _Verdict.taken:
        return await refuse("taken", Outcome.taken, revoke=False)
    if verdict is _Verdict.same:
        return await refuse("already_connected", Outcome.connected, revoke=False)
    if verdict is _Verdict.refused:
        return await refuse("not_allowed")
    log.event(
        "mail.account.connected",
        user_id=uid,
        status=int(SCOPE_SEND in tokens.scopes),
        code=verdict.value,
    )
    return Outcome.connected


# --- disconnect ------------------------------------------------------------------------------


async def _revoke(google: GoogleClient | None, user_id: uuid.UUID, token: str | None) -> bool:
    """Revoke at Google; False when it could not be confirmed. Never raises for Google."""
    if google is None or not token:
        return False
    try:
        with log.hide(token):
            await google.revoke(token)
    except GoogleError as failed:
        log.event("mail.account.revoke_failed", user_id=str(user_id), code=stored_code(failed))
        return False
    finally:
        google.forget(str(user_id))
    return True


def _flag(
    sessions: sessionmaker[Session],
    keyring: Keyring | None,
    user_id: uuid.UUID,
    now: datetime,
    only_flagged: bool = False,
) -> tuple[bool, str | None]:
    """Step 1: under both locks, set `disconnecting_at` and open the token for the revocation.
    (found, token); the token is None when this server's keys do not open it."""
    with sessions() as db, db.begin():
        locks.lock_both(db, user_id)
        row = db.scalars(
            sa.select(MailAccount)
            .where(MailAccount.user_id == user_id)
            .options(undefer(MailAccount.refresh_token_encrypted))
            .with_for_update()
        ).one_or_none()
        if row is None or (only_flagged and row.disconnecting_at is None):
            return False, None
        if row.disconnecting_at is None:
            row.disconnecting_at = now
        if keyring is None:
            return True, None
        try:
            opened = keyring.decrypt(
                row.refresh_token_encrypted, row.key_version, user_id, TOKEN_KIND
            )
        except DecryptError:
            return True, None
        return True, opened.plaintext


def _purge(sessions: sessionmaker[Session], user_id: uuid.UUID) -> None:
    """Step 3: under both locks, delete. An account that is not flagged is left alone: it was
    connected again after another disconnect finished."""
    with sessions() as db, db.begin():
        locks.lock_both(db, user_id)
        row = db.scalars(
            sa.select(MailAccount).where(MailAccount.user_id == user_id).with_for_update()
        ).one_or_none()
        if row is not None and row.disconnecting_at is None:
            return
        gone = purge.purge_user(db, user_id)
    log.event("mail.account.disconnected", user_id=str(user_id), count=gone.rows)


async def disconnect(
    *,
    sessions: sessionmaker[Session],
    google: GoogleClient | None,
    keyring: Keyring | None,
    user_id: uuid.UUID,
    now: datetime,
    only_flagged: bool = False,
) -> Disconnected:
    """The three steps. Waits for one batch of a sync at most, never for a whole sync."""
    found, token = await run_in_threadpool(_flag, sessions, keyring, user_id, now, only_flagged)
    if not found:
        return Disconnected(found=False, revoked=True)
    revoked = await _revoke(google, user_id, token)
    await run_in_threadpool(_purge, sessions, user_id)
    return Disconnected(found=True, revoked=revoked)


async def finish_disconnect(
    *,
    sessions: sessionmaker[Session],
    google: GoogleClient | None,
    keyring: Keyring | None,
    user_id: uuid.UUID,
    now: datetime,
) -> Disconnected:
    """End a disconnect that was interrupted between the flag and the purge. Does nothing to an
    account that is not flagged."""
    return await disconnect(
        sessions=sessions,
        google=google,
        keyring=keyring,
        user_id=user_id,
        now=now,
        only_flagged=True,
    )
