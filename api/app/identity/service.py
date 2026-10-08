"""Identity operations on the database. Functions flush but never commit: the caller owns the
transaction (a request handler, or the CLI).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from app.identity import security
from app.identity.models import Invite, InviteKind, Role, User

INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=1)
DEFAULT_TTL = {InviteKind.invite: INVITE_TTL, InviteKind.reset: RESET_TTL}

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
