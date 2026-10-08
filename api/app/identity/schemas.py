"""Request and response bodies of the identity API.

Input limits are generous enough for any real value and small enough that nothing oversized
reaches argon2 or the database: a longer field is a 422, never a 500. The real rules (email
syntax, password rules, name required) are checked in the handlers so their messages stay
user-facing.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.identity.models import InviteKind, Role
from app.identity.service import InviteStatus

# Wider than any valid value (emails are at most 320 characters, passwords 128 after
# normalisation, which NFKC can shrink or grow), so the handler gives the precise message.
EMAIL_MAX = 512
PASSWORD_MAX = 1024
NAME_MAX = 100
NAME_INPUT_MAX = 1024


class _Strict(BaseModel):
    # Unknown fields are refused: a typo in a client is a clear 422, not a silent no-op.
    model_config = ConfigDict(extra="forbid")


class LoginIn(_Strict):
    email: str = Field(min_length=1, max_length=EMAIL_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class AcceptInviteIn(_Strict):
    # Required for an invite, ignored for a reset (the account already has a name).
    name: str | None = Field(default=None, max_length=NAME_INPUT_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None


class InviteOut(BaseModel):
    """What the invite page needs: whose link it is and whether to ask for a name."""

    email: str
    kind: InviteKind
    expires_at: datetime


class ErrorOut(BaseModel):
    detail: str


# --- admin -------------------------------------------------------------------------------------

INVITES_PAGE_MAX = 100
INVITES_PAGE_DEFAULT = 50


class AdminInviteIn(_Strict):
    """A new link. `role` is the role an invite gives (member when omitted); a reset link
    changes a password only, so a role there is refused rather than silently dropped."""

    email: str = Field(min_length=1, max_length=EMAIL_MAX)
    kind: InviteKind = InviteKind.invite
    role: Role | None = None


class AdminInviteOut(BaseModel):
    """A link as the admin area lists it. Never the token: it exists only in the create
    answer. `role` is null for reset links."""

    id: UUID
    email: str
    kind: InviteKind
    role: Role | None
    status: InviteStatus
    created_by_name: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class AdminInvitePage(BaseModel):
    items: list[AdminInviteOut]
    total: int
    limit: int
    offset: int


class AdminInviteCreated(BaseModel):
    """The only answer that carries the link: `/invite/<token>` or `/reset/<token>`, relative,
    so the admin's browser prefixes it with the origin it is on."""

    invite: AdminInviteOut
    path: str


USERS_PAGE_MAX = 100
USERS_PAGE_DEFAULT = 50


class AdminUserOut(BaseModel):
    """An account as the admin area lists it. `sessions` counts its unexpired sessions (signed
    in browsers). Never a password hash or a token."""

    id: UUID
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    sessions: int


class AdminUserPage(BaseModel):
    """One page of accounts sorted by name. `active_admins` counts every active admin, not only
    those on this page, so the client knows which row is the last one."""

    items: list[AdminUserOut]
    total: int
    limit: int
    offset: int
    active_admins: int


class AdminUserPatch(_Strict):
    """What an admin may change on an account: at least one of the two, never null."""

    is_active: StrictBool | None = None
    role: Role | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "AdminUserPatch":
        if not self.model_fields_set:
            raise ValueError("Send is_active, role or both.")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self
