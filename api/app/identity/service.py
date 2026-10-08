"""Identity operations on the database. Functions flush but never commit: the caller owns the
transaction (a request handler, or the CLI).
"""

import ipaddress
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from app.identity import security
from app.identity.models import Invite, InviteKind, Role, User
from app.identity.models import Session as UserSession

INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=1)
DEFAULT_TTL = {InviteKind.invite: INVITE_TTL, InviteKind.reset: RESET_TTL}
SESSION_TTL = timedelta(days=30)
# A session is extended (and its cookie reissued) at most once per this interval.
SESSION_REFRESH_AFTER = timedelta(days=1)
NAME_MAX_LENGTH = 100
USER_AGENT_MAX_LENGTH = 512

# Namespace of the transaction advisory lock that serialises link creation per email and kind.
_INVITE_LOCK_NAMESPACE = 20_002


class InvalidEmailError(ValueError):
    """The text is not a usable email address. The message is user-facing."""


def normalize_email(raw: str) -> str:
    """Validate an address and return the form we store and compare.

    Syntax only (no DNS lookup, the Jetson may be offline and nothing is sent anyway). The whole
    address is lowercased with `str.lower`, the same folding citext applies, so the stored value
    and the database comparison agree. `casefold` would merge distinct addresses such as
    `straße@` and `strasse@`, which a mail server treats as different mailboxes.
    """
    if not isinstance(raw, str):
        raise InvalidEmailError("Enter a valid email address.")
    candidate = raw.strip()
    if not candidate or len(candidate) > 320:
        raise InvalidEmailError("Enter a valid email address.")
    try:
        validated = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        raise InvalidEmailError("Enter a valid email address.") from None
    return validated.normalized.lower()


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IssuedInvite:
    """A new link. `token` exists only here, in memory: show it once, never log or store it."""

    invite: Invite
    token: str

    @property
    def path(self) -> str:
        prefix = "/invite/" if self.invite.kind is InviteKind.invite else "/reset/"
        return prefix + self.token


def create_invite(
    session: Session,
    email: str,
    kind: InviteKind,
    created_by: UUID | None = None,
    ttl: timedelta | None = None,
    role: Role = Role.member,
    now: datetime | None = None,
) -> IssuedInvite:
    """Issue a one-time link for `email`, revoking any previous unused link of the same kind.

    Concurrent calls for the same email and kind are serialised by an advisory lock held until
    the transaction ends; the partial unique index on active links is the backstop.
    """
    email = normalize_email(email)
    ttl = DEFAULT_TTL[kind] if ttl is None else ttl
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    now = now or utcnow()
    session.execute(
        sa.select(sa.func.pg_advisory_xact_lock(_INVITE_LOCK_NAMESPACE, sa.func.hashtext(email)))
    )
    session.execute(
        sa.update(Invite)
        .where(
            Invite.email == email,
            Invite.kind == kind,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = security.new_token()
    invite = Invite(
        email=email,
        kind=kind,
        role=role,
        token_hash=security.hash_token(token),
        created_by=created_by,
        created_at=now,
        expires_at=now + ttl,
    )
    session.add(invite)
    session.flush()
    return IssuedInvite(invite=invite, token=token)


def lookup_invite(session: Session, token: str, now: datetime | None = None) -> Invite | None:
    """The active invite for a token, or None when it is malformed, unknown, used, revoked or
    expired. A link is valid strictly before `expires_at`. All failures look the same.
    """
    if not security.is_well_formed_token(token):
        return None
    now = now or utcnow()
    token_hash = security.hash_token(token)
    invite = session.scalars(
        sa.select(Invite).where(
            Invite.token_hash == token_hash,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
    ).one_or_none()
    if invite is None or not security.tokens_equal(invite.token_hash, token_hash):
        return None
    return invite


def get_user_by_email(session: Session, email: str) -> User | None:
    """The user with this address (case-insensitive through citext), or None."""
    return session.scalars(sa.select(User).where(User.email == email)).one_or_none()


# --- sign in -----------------------------------------------------------------------------------


def authenticate(session: Session, email: str, password: str) -> User | None:
    """The active user these credentials belong to, or None. Every failure costs one argon2
    verification (a dummy one for an address that has no account), so neither the answer nor
    its timing tells an unknown email from a wrong password or a disabled account.

    A hash made with older parameters is replaced on success; the caller commits.
    """
    try:
        email = normalize_email(email)
    except InvalidEmailError:
        security.hash_dummy(password)
        return None
    user = get_user_by_email(session, email)
    if user is None:
        security.hash_dummy(password)
        return None
    if not security.verify_password(user.password_hash, password):
        return None
    if not user.is_active:
        return None
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    return user


# --- sessions ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedSession:
    """A new session. `token` goes into the cookie once and exists nowhere else."""

    row: UserSession
    token: str


@dataclass(frozen=True)
class ActiveSession:
    """A session that passed every check. `refreshed` means its lifetime was just extended, so
    the cookie must be sent again with a fresh Max-Age."""

    row: UserSession
    user: User
    refreshed: bool


def _clean_ip(ip: str | None) -> str | None:
    """The address as Postgres inet accepts it, or None (test clients, unix sockets)."""
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None


def _clean_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    # Postgres text cannot hold NUL; the header is informational only.
    return user_agent.replace("\x00", "")[:USER_AGENT_MAX_LENGTH] or None


def create_session(
    session: Session,
    user: User,
    now: datetime | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedSession:
    now = now or utcnow()
    token = security.new_token()
    row = UserSession(
        user_id=user.id,
        token_hash=security.hash_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
        user_agent=_clean_user_agent(user_agent),
        ip=_clean_ip(ip),
    )
    session.add(row)
    session.flush()
    return IssuedSession(row=row, token=token)


def resolve_session(
    session: Session, token: str, now: datetime | None = None
) -> ActiveSession | None:
    """The live session behind a cookie value, or None.

    An expired session is deleted on sight. A session of a disabled user is refused and every
    session of that user is deleted. A session last seen at least SESSION_REFRESH_AFTER ago is
    slid forward: last_seen_at and expires_at move, and `refreshed` tells the caller to reissue
    the cookie. The caller commits.
    """
    if not security.is_well_formed_token(token):
        return None
    now = now or utcnow()
    token_hash = security.hash_token(token)
    found = session.execute(
        sa.select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == token_hash)
    ).one_or_none()
    if found is None:
        return None
    row, user = found
    if not security.tokens_equal(row.token_hash, token_hash):
        return None  # pragma: no cover, the lookup matched on equality already
    if row.expires_at <= now:
        session.delete(row)
        session.flush()
        return None
    if not user.is_active:
        end_user_sessions(session, user.id)
        return None
    refreshed = now - row.last_seen_at >= SESSION_REFRESH_AFTER
    if refreshed:
        row.last_seen_at = now
        row.expires_at = now + SESSION_TTL
        session.flush()
    return ActiveSession(row=row, user=user, refreshed=refreshed)


def delete_session_by_token(session: Session, token: str) -> bool:
    """Sign out one browser. True when a session was deleted."""
    if not security.is_well_formed_token(token):
        return False
    result = session.execute(
        sa.delete(UserSession).where(UserSession.token_hash == security.hash_token(token))
    )
    return bool(result.rowcount)


def end_user_sessions(session: Session, user_id: UUID) -> int:
    """Sign a user out everywhere. Returns how many sessions ended."""
    result = session.execute(sa.delete(UserSession).where(UserSession.user_id == user_id))
    return int(result.rowcount or 0)


# --- invite and reset links --------------------------------------------------------------------


class InvalidLinkError(LookupError):
    """The link is malformed, unknown, used, revoked or expired. Always the same answer."""


class AcceptError(ValueError):
    """The form is not acceptable. `code` is stable for the API; the message is user-facing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AccountExistsError(AcceptError):
    """An invite for an email that already has an account."""


def clean_name(raw: str | None) -> str:
    """The display name as stored: NFC, whitespace runs collapsed to one space, trimmed."""
    if raw is None:
        raise AcceptError("name_required", "Enter your name.")
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise AcceptError("name_required", "Enter your name.")
    if any(unicodedata.category(char) in ("Cc", "Cs") for char in name):
        raise AcceptError("name_invalid", "The name contains characters that are not valid.")
    if len(name) > NAME_MAX_LENGTH:
        raise AcceptError(
            "name_too_long", f"Use at most {NAME_MAX_LENGTH} characters for the name."
        )
    return name


def accept_invite(
    session: Session,
    token: str,
    password: str,
    name: str | None = None,
    now: datetime | None = None,
) -> User:
    """Use a link once: an invite creates the account with the link's role, a reset sets a new
    password and ends every session of that user. Returns the user, ready to be signed in.

    Raises InvalidLinkError, AcceptError (bad name), AccountExistsError, or
    security.PasswordRuleError. The slow argon2 hash runs before the invite row is locked; the
    lock (SELECT ... FOR UPDATE, rechecking that the link is still unused) makes concurrent
    accepts of one link serialise, and only the first one finds it unused. The caller commits.
    """
    now = now or utcnow()
    invite = lookup_invite(session, token, now)
    if invite is None:
        raise InvalidLinkError
    clean = clean_name(name) if invite.kind is InviteKind.invite else None
    password_hash = security.hash_password(security.check_password_rules(password, invite.email))

    locked = session.scalars(
        sa.select(Invite)
        .where(
            Invite.id == invite.id,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if locked is None:
        raise InvalidLinkError

    user = get_user_by_email(session, locked.email)
    if locked.kind is InviteKind.invite:
        if user is not None:
            raise AccountExistsError(
                "account_exists", "An account with this email already exists. Sign in instead."
            )
        user = User(email=locked.email, name=clean, password_hash=password_hash, role=locked.role)
        session.add(user)
    else:
        if user is None or not user.is_active:
            raise InvalidLinkError
        user.password_hash = password_hash
        end_user_sessions(session, user.id)
    locked.used_at = now
    user.last_login_at = now
    session.flush()
    return user
