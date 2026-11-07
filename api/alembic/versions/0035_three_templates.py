"""The owner's three templates replace the twenty (documents D27): a document whose template was
one of the twenty removed folders moves to the nearest of the three, so that its next render
prints with a look close to the one it had rather than with the default and a note.

Revision ID: 0035
Revises: 0034

Data only, no schema change. `documents.template_slug` alone moves: a version keeps its
`template_slug` and its PDF (a frozen version is what was sent, and the freeze trigger of 0015
guards `document_versions` only, so no row of `documents` is refused). A person's own template
(`upload-` and 8 characters) and the three are not touched.

The previous release, running on the migrated rows for the seconds of a deploy, has none of the
three folders: it prints them with its own default, which is what it does for any slug it does
not serve. The downgrade does nothing: the twenty folders are gone, and the new slugs are as
good as any for a release that falls back on what it does not know.
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

# The removed slugs, each to the one of the three nearest its look (D27, SPEC-documents.md).
MOVES = {
    **dict.fromkeys(
        ("sidebar", "split-right", "portrait", "banner", "columns-header", "cards", "grid"),
        "photo-columns",
    ),
    **dict.fromkeys(("serif", "elegant", "executive", "nordic"), "ledger"),
    **dict.fromkeys(
        (
            "academic", "accent", "bold", "classic", "compact", "minimal", "mono", "plain",
            "timeline",
        ),
        "band",
    ),
}  # fmt: skip
DOCUMENTS = sa.table("documents", sa.column("template_slug", sa.Text))


def upgrade() -> None:
    for target in sorted(set(MOVES.values())):
        removed = sorted(slug for slug, to in MOVES.items() if to == target)
        op.execute(
            DOCUMENTS.update()
            .where(DOCUMENTS.c.template_slug.in_(removed))
            .values(template_slug=target)
        )


def downgrade() -> None:
    pass  # the twenty folders are gone: nothing to move back to
