"""Agents, issue #33: agent_runs.credential_kind, the kind of model credential a run used.

The SDK reports a cost for every run (`total_cost_usd`), on a Claude subscription too, where
nothing is billed per run: the app has to know which kind of credential a run used to say
whether the amount is a cost or an estimate at API prices. The worker records the kind when it
reads the credential at the start of the run (the kind only, never the secret, and not a
reference to the credential row: the user may replace or remove the credential later, and the
run keeps what it used).

Nullable, with no default and no backfill: a run that ended before this migration, or that
failed before its credential was read, has no recorded kind, and the app words its amount
neutrally. Guessing from the user's credential of today would be wrong whenever it was replaced
since. The column reuses the enum model_credential_kind of 0006.

Written by hand to match app/agents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("claude_subscription", "anthropic_api_key")


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "credential_kind",
            pg.ENUM(*KINDS, name="model_credential_kind", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "credential_kind")
