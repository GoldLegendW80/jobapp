"""Request and response bodies of the identity API.

Input limits are generous enough for any real value and small enough that nothing oversized
reaches argon2 or the database: a longer field is a 422, never a 500. The real rules (email
syntax, password rules, name required) are checked in the handlers so their messages stay
user-facing.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.identity.models import InviteKind, Role

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
