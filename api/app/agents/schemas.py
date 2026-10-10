"""Bodies of /api/me/model-credential. The secret comes in as a SecretStr (its repr and any
validation error never show it) and never goes out: answers carry kind, last4 and dates only."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.agents.models import CredentialKind

# Generous bound for the body itself; the format check (credentials.clean_secret) is stricter.
SECRET_BODY_MAX = 4096


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CredentialKind
    secret: SecretStr = Field(max_length=SECRET_BODY_MAX)


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: CredentialKind
    last4: str
    verified_at: datetime | None
    updated_at: datetime


class VerifyOut(BaseModel):
    # verified: the API accepted the key just now. unverified: a subscription token, which the
    # server cannot check without running Claude Code; the first agent run checks it.
    result: Literal["verified", "unverified"]
    message: str
    credential: CredentialOut
