"""Tables of the agents module. A1: model_credentials, one row per user at most.

The secret is stored only encrypted (`crypto.py`: AES-GCM, bound to the user id and the kind),
with the version of the key that wrote it and its last four characters for display. It is
deferred, so loading a row for GET never pulls the ciphertext along.
Migration: alembic/versions/0006_model_credentials.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.agents.crypto import NONCE_BYTES, TAG_BYTES
from app.models_base import Base


class CredentialKind(enum.StrEnum):
    claude_subscription = "claude_subscription"
    anthropic_api_key = "anthropic_api_key"


CREDENTIAL_KIND_ENUM = sa.Enum(
    CredentialKind,
    name="model_credential_kind",
    values_callable=lambda members: [m.value for m in members],
)


class ModelCredential(Base):
    __tablename__ = "model_credentials"
    __table_args__ = (
        sa.CheckConstraint(
            f"octet_length(secret_encrypted) > {NONCE_BYTES + TAG_BYTES}",
            name="secret_encrypted_length",
        ),
        sa.CheckConstraint("char_length(last4) = 4", name="last4_length"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[CredentialKind] = mapped_column(CREDENTIAL_KIND_ENUM, nullable=False)
    secret_encrypted: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    key_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    last4: Mapped[str] = mapped_column(sa.Text, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
