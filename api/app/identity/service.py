"""Identity operations on the database. Functions flush but never commit: the caller owns the
transaction (a request handler, or the CLI).
"""

import enum
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


def clean_ip(ip: str | None) -> str | None:
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
        ip=clean_ip(ip),
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


# --- the account page: password and name ------------------------------------------------------


class WrongPasswordError(ValueError):
    """The current password given is not the account's password (or it changed meanwhile)."""


class SessionEndedError(LookupError):
    """The caller's session ended, or its account was disabled, while the request ran."""


SAME_PASSWORD = "The new password must be different from the current one."  # noqa: S105, a message


def _lock_live_account(session: Session, user_id: UUID, session_id: UUID, now: datetime) -> User:
    """Lock the account row (SELECT ... FOR UPDATE) and check, under that lock and with a fresh
    read, that it is still active and that the caller's session still exists and is unexpired.
    Read committed takes a new snapshot per statement, so a change committed by another request
    while this one waited for the lock is seen here. Raises SessionEndedError."""
    user = session.scalars(
        sa.select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if user is None or not user.is_active:
        raise SessionEndedError
    alive = session.scalar(
        sa.select(UserSession.id).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.expires_at > now,
        )
    )
    if alive is None:
        raise SessionEndedError
    return user


def change_password(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    current: str,
    new: str,
    now: datetime | None = None,
) -> int:
    """Replace the password of a signed in account and end its other sessions; the session
    `session_id` (the caller's browser) stays. Returns how many other sessions ended. The caller
    commits.

    Order: the current password is verified (WrongPasswordError), the new one must differ from
    it after NFKC normalisation and pass the rules of `security.check_password_rules`
    (PasswordRuleError, code `same_as_current` or the rule's code). Both slow argon2 steps run
    before the account row is locked. Under the lock the account must still be active and the
    session alive (SessionEndedError), and the hash must still be the one just verified: when
    two sessions change the password at once, the first to take the lock wins and ends the other
    session, which then gets SessionEndedError. A hash replaced by a reset link meanwhile gives
    WrongPasswordError. A wrong current password never ends any session.
    """
    now = now or utcnow()
    user = session.scalars(
        sa.select(User).where(User.id == user_id).execution_options(populate_existing=True)
    ).one_or_none()
    if user is None or not user.is_active:
        security.hash_dummy(current)
        raise SessionEndedError
    verified_hash = user.password_hash
    if not security.verify_password(verified_hash, current):
        raise WrongPasswordError
    if security.normalize_password(new) == security.normalize_password(current):
        raise security.PasswordRuleError("same_as_current", SAME_PASSWORD)
    new_hash = security.hash_password(security.check_password_rules(new, user.email))

    locked = _lock_live_account(session, user_id, session_id, now)
    if locked.password_hash != verified_hash:
        raise WrongPasswordError
    locked.password_hash = new_hash
    ended = session.execute(
        sa.delete(UserSession).where(UserSession.user_id == user_id, UserSession.id != session_id)
    )
    session.flush()  # updated_at moves through its onupdate
    return int(ended.rowcount or 0)


def rename_user(
    session: Session,
    user_id: UUID,
    session_id: UUID,
    raw_name: str | None,
    now: datetime | None = None,
) -> User:
    """Set the display name of a signed in account, cleaned as on an invite (`clean_name`:
    NFC, whitespace collapsed, 1 to NAME_MAX_LENGTH characters, no control characters). The
    same name writes nothing, so updated_at stays. Raises AcceptError or SessionEndedError.
    The caller commits."""
    name = clean_name(raw_name)
    user = _lock_live_account(session, user_id, session_id, now or utcnow())
    if user.name != name:
        user.name = name
        session.flush()
    return user


# --- admin: listing and revoking links ---------------------------------------------------------


class InviteStatus(enum.StrEnum):
    active = "active"
    used = "used"
    revoked = "revoked"
    expired = "expired"


def invite_status(invite: Invite, now: datetime) -> InviteStatus:
    """What a link is now. Used wins over revoked (a used link was never revoked: revocation
    only touches unused ones), and both win over expired, which only time decides."""
    if invite.used_at is not None:
        return InviteStatus.used
    if invite.revoked_at is not None:
        return InviteStatus.revoked
    if invite.expires_at <= now:
        return InviteStatus.expired
    return InviteStatus.active


@dataclass(frozen=True)
class InviteListing:
    """One page of links, newest first, each with the name of the admin who made it (None for
    links from the command line or whose creator is gone)."""

    rows: list[tuple[Invite, str | None]]
    total: int


def list_invites(session: Session, limit: int, offset: int) -> InviteListing:
    total = int(session.scalar(sa.select(sa.func.count()).select_from(Invite)) or 0)
    found = session.execute(
        sa.select(Invite, User.name)
        .outerjoin(User, User.id == Invite.created_by)
        # id breaks ties between links made in the same instant, so pages never overlap.
        .order_by(Invite.created_at.desc(), Invite.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return InviteListing(rows=[(invite, name) for invite, name in found], total=total)


def revoke_invite(session: Session, invite_id: UUID, now: datetime | None = None) -> Invite | None:
    """Revoke a link that is still active (unused, unrevoked, unexpired) in one statement, so a
    concurrent accept or revoke cannot slip between a check and the write. Returns the revoked
    row, or None when there was nothing active to revoke."""
    now = now or utcnow()
    return session.scalars(
        sa.update(Invite)
        .where(
            Invite.id == invite_id,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
        .values(revoked_at=now)
        .returning(Invite)
        .execution_options(populate_existing=True)
    ).one_or_none()


# --- admin: listing and changing accounts ------------------------------------------------------

# Namespace of the transaction advisory lock that serialises every admin change to an account.
_ACCOUNTS_LOCK_NAMESPACE = 20_005


@dataclass(frozen=True)
class UserListing:
    """One page of accounts sorted by name, each with its count of unexpired sessions, plus the
    number of active admins across all pages."""

    rows: list[tuple[User, int]]
    total: int
    active_admins: int


def _live_sessions(now: datetime) -> sa.Subquery:
    return (
        sa.select(UserSession.user_id, sa.func.count().label("n"))
        .where(UserSession.expires_at > now)
        .group_by(UserSession.user_id)
        .subquery()
    )


def count_active_admins(session: Session) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(User)
            .where(User.role == Role.admin, User.is_active.is_(True))
        )
        or 0
    )


def list_users(session: Session, limit: int, offset: int, now: datetime) -> UserListing:
    total = int(session.scalar(sa.select(sa.func.count()).select_from(User)) or 0)
    live = _live_sessions(now)
    found = session.execute(
        sa.select(User, sa.func.coalesce(live.c.n, 0))
        .outerjoin(live, live.c.user_id == User.id)
        # Case-insensitive by name, then email (unique) so equal names never swap between pages.
        .order_by(sa.func.lower(User.name), User.name, User.email)
        .limit(limit)
        .offset(offset)
    ).all()
    return UserListing(
        rows=[(user, int(n)) for user, n in found],
        total=total,
        active_admins=count_active_admins(session),
    )


def count_live_sessions(session: Session, user_id: UUID, now: datetime) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(UserSession)
            .where(UserSession.user_id == user_id, UserSession.expires_at > now)
        )
        or 0
    )


class LastAdminError(Exception):
    """The change would leave no active admin. The message is user-facing."""


LAST_ADMIN = "This is the last active admin. Make someone else an admin first, then try again."


class UnknownUserError(LookupError):
    """No account has this id."""


class NotAnAdminError(PermissionError):
    """The acting admin was disabled or demoted after their request was let in."""


def update_user(
    session: Session,
    actor_id: UUID,
    user_id: UUID,
    is_active: bool | None = None,
    role: Role | None = None,
) -> User:
    """Disable, enable, promote or demote an account, as the admin `actor_id`. The caller commits.

    Every such change takes one transaction advisory lock first, so changes run one at a time
    and each reads the accounts as the previous one left them (read committed takes a new
    snapshot per statement). Under that lock:
    - the actor must still be an active admin (another admin may have just disabled or demoted
      them), else UnknownUserError, the same 404 a member gets;
    - a change that takes the target out of the active admins is refused with LastAdminError
      when no other active admin remains. Two admins disabling each other at once therefore
      leave exactly one of them active.

    Disabling ends every session of the account at once. Enabling also ends any session left
    over from before (an account disabled outside this function keeps its rows until they are
    next used), so re-enabling never brings an old browser back. A change to the current value
    writes nothing (updated_at stays).
    """
    session.execute(sa.select(sa.func.pg_advisory_xact_lock(_ACCOUNTS_LOCK_NAMESPACE)))
    actor = session.scalars(
        sa.select(User).where(User.id == actor_id).execution_options(populate_existing=True)
    ).one_or_none()
    if actor is None or not actor.is_active or actor.role is not Role.admin:
        raise NotAnAdminError
    user = session.scalars(
        sa.select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if user is None:
        raise UnknownUserError

    new_active = user.is_active if is_active is None else is_active
    new_role = user.role if role is None else role
    was_admin = user.is_active and user.role is Role.admin
    stays_admin = new_active and new_role is Role.admin
    if was_admin and not stays_admin:
        others = session.scalar(
            sa.select(sa.func.count())
            .select_from(User)
            .where(User.role == Role.admin, User.is_active.is_(True), User.id != user.id)
        )
        if not others:
            raise LastAdminError(LAST_ADMIN)

    if new_active != user.is_active:
        # Enabling or disabling: no session survives either way.
        end_user_sessions(session, user.id)
        user.is_active = new_active
    elif not new_active:
        end_user_sessions(session, user.id)  # already disabled: make sure nothing lingers
    if new_role != user.role:
        user.role = new_role
    session.flush()
    return user
