"""Agents A1: model_credentials and its enum model_credential_kind.

One row per user at most (user_id is the primary key), cascading from users: a deleted account
leaves no credential behind. The secret is only ever stored encrypted (app/agents/crypto.py).

Written by hand to match app/agents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("claude_subscription", "anthropic_api_key")


def upgrade() -> None:
    labels = ", ".join(f"'{kind}'" for kind in KINDS)
    op.execute(f"CREATE TYPE model_credential_kind AS ENUM ({labels})")
    op.create_table(
        "model_credentials",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            pg.ENUM(*KINDS, name="model_credential_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("secret_encrypted", pg.BYTEA(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("last4", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_model_credentials"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_model_credentials_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "octet_length(secret_encrypted) > 28",
            name="ck_model_credentials_secret_encrypted_length",
        ),
        sa.CheckConstraint("char_length(last4) = 4", name="ck_model_credentials_last4_length"),
    )


def downgrade() -> None:
    op.drop_table("model_credentials")
    op.execute("DROP TYPE model_credential_kind")
