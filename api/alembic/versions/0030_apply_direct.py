"""One click Apply (issue #738, owner 2026-11-01): the mode `direct`, the authorisation
`apply_click` and the person's setting `apply_settings.review_before_send` (off by default).

Revision ID: 0030
Revises: 0029

One-way door: Postgres cannot drop a value from an enum type. The downgrade removes the column
and every row that uses the new values, then recreates both types without them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE application_mode ADD VALUE IF NOT EXISTS 'direct'")
    op.execute("ALTER TYPE apply_authorisation_kind ADD VALUE IF NOT EXISTS 'apply_click'")
    op.add_column(
        "apply_settings",
        sa.Column("review_before_send", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("apply_settings", "review_before_send")
    op.execute("DELETE FROM apply_authorisations WHERE kind::text = 'apply_click'")
    op.execute("UPDATE applications SET mode = 'review' WHERE mode::text = 'direct'")
    # The checks that compare a column with a label of the old type go first, then come back.
    op.execute(
        "ALTER TABLE apply_authorisations "
        "DROP CONSTRAINT ck_apply_authorisations_kind_matches_subject"
    )
    op.execute("ALTER TABLE applications DROP CONSTRAINT ck_applications_manual_is_never_dry")
    op.execute("ALTER TYPE apply_authorisation_kind RENAME TO apply_authorisation_kind_0030")
    op.execute(
        "CREATE TYPE apply_authorisation_kind AS ENUM "
        "('person_approval', 'full_auto_like', 'profile_section')"
    )
    op.execute(
        "ALTER TABLE apply_authorisations ALTER COLUMN kind TYPE apply_authorisation_kind "
        "USING kind::text::apply_authorisation_kind"
    )
    op.execute("DROP TYPE apply_authorisation_kind_0030")
    op.execute("ALTER TYPE application_mode RENAME TO application_mode_0030")
    op.execute("CREATE TYPE application_mode AS ENUM ('manual', 'review', 'full_auto')")
    op.execute(
        "ALTER TABLE applications ALTER COLUMN mode TYPE application_mode "
        "USING mode::text::application_mode"
    )
    op.execute("DROP TYPE application_mode_0030")
    op.execute(
        "ALTER TABLE applications ADD CONSTRAINT ck_applications_manual_is_never_dry "
        "CHECK (NOT (dry_run AND mode = 'manual'))"
    )
    op.execute(
        "ALTER TABLE apply_authorisations ADD CONSTRAINT "
        "ck_apply_authorisations_kind_matches_subject "
        "CHECK ((kind = 'profile_section') = (site_run_id IS NOT NULL))"
    )
