"""Mail M4: everything the reading side of the mail module stores.

Six tables and their six enums: `mail_accounts` (one connected Gmail per user, the refresh token
sealed as `model_credentials.secret_encrypted` is), `mail_threads`, `mail_messages`,
`mail_classifications`, `mail_confirmed_domains` and `mail_assist_batches`, as the DDL of
tasks/mail/todo.md ("Data model") writes them. And one unique constraint on `jobs`,
`(id, user_id)`, so a thread can point at its posting together with its user.

Isolation is in the schema: a message carries `(thread_id, user_id)` and a classification
`(message_id, user_id)` as composite foreign keys, and a thread points at
`jobs(id, user_id)` through `(posting_id, user_id)`. No row of these tables can belong to one
user and point at another's. Deleting a posting empties `posting_id` alone
(`ON DELETE SET NULL (posting_id)`, a column list, Postgres 15 or later; production runs 16):
`user_id` is not null and must stay. Every table goes with its user: `users` cascades to
accounts, threads, confirmed domains and batches, threads to messages, messages to
classifications. A classification's `proposal_id` is set to null when the proposal goes.

`uq_mail_accounts_google_sub` is the rule "one Google account belongs to one app user", and
`uq_mail_assist_batches_user_id_open` the rule "one open batch per user". The CHECK constraints
are backstops (app/mail/models.py says what each one is).

Expand only: new types, new tables and one new unique constraint on `jobs`, whose primary key
already makes `(id, user_id)` unique, so it can never fail on existing rows. The release before
this one reads and writes none of it (CONTRIBUTING.md, Migrations). The constraint on `jobs`
builds an index under a short exclusive lock: the table holds a few thousand rows.

Constraints are named with `op.f()` or by their full name: the name written here is the final
one, the one app/models_base.py gives the model's constraint.

Written by hand to match app/mail/models.py (the tests run `alembic check` against it). Every
statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-14
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "mail_account_status": ("ok", "needs_reconnect", "error"),
    "mail_link_source": ("rule", "model", "manual"),
    "mail_message_direction": ("inbound", "outbound"),
    "mail_label": (
        "application_received",
        "rejection",
        "interview_invitation",
        "assessment",
        "offer",
        "info_request",
        "other",
    ),
    "mail_classification_source": ("rule", "model"),
    "mail_assist_batch_status": ("running", "paused", "done", "cancelled"),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def _uuid(name: str, **more: Any) -> sa.Column[Any]:
    return sa.Column(name, pg.UUID(as_uuid=True), **more)


def _uuid_pk() -> sa.Column[Any]:
    return _uuid("id", server_default=sa.text("gen_random_uuid()"), nullable=False)


def _at(name: str, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def _flag(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("false"))


def _text(name: str, default: bool = True) -> sa.Column[Any]:
    return sa.Column(
        name, sa.Text(), nullable=False, server_default=sa.text("''") if default else None
    )


def _texts(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
    )


def upgrade() -> None:
    op.create_unique_constraint("uq_jobs_id_user_id", "jobs", ["id", "user_id"])
    for name, labels in ENUMS.items():
        quoted = ", ".join(f"'{label}'" for label in labels)
        op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")

    op.create_table(
        "mail_accounts",
        _uuid("user_id", nullable=False),
        _text("email", default=False),
        _text("google_sub", default=False),
        sa.Column("refresh_token_encrypted", pg.BYTEA(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        _texts("scopes"),
        sa.Column("history_id", sa.Text(), nullable=True),
        _at("connected_at", nullable=False),
        _at("initial_sync_done_at"),
        _at("last_sync_at"),
        sa.Column("sync_cursor", pg.JSONB(), nullable=True),
        sa.Column("sync_counts", pg.JSONB(), nullable=True),
        _at("backoff_until"),
        sa.Column("status", _enum("mail_account_status"), nullable=False, server_default="ok"),
        sa.Column("error_code", sa.Text(), nullable=True),
        _at("disconnecting_at"),
        _at("assistant_consent_at"),
        sa.Column("assistant_consent_version", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_mail_accounts")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("google_sub", name="uq_mail_accounts_google_sub"),
        sa.CheckConstraint(
            "email <> '' AND google_sub <> ''", name=op.f("ck_mail_accounts_identity_not_empty")
        ),
        sa.CheckConstraint(
            "(assistant_consent_at IS NULL) = (assistant_consent_version IS NULL)",
            name=op.f("ck_mail_accounts_consent_is_whole"),
        ),
    )

    op.create_table(
        "mail_threads",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        _text("gmail_thread_id", default=False),
        _uuid("posting_id", nullable=True),
        sa.Column("link_source", _enum("mail_link_source"), nullable=True),
        sa.Column("link_confidence", sa.REAL(), nullable=True),
        _flag("link_locked"),
        _text("subject"),
        _texts("participants"),
        _at("last_message_at"),
        _flag("needs_reply"),
        _at("deleted_at"),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_threads")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_threads_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "gmail_thread_id", name="uq_mail_threads_user_id_gmail_thread_id"
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_mail_threads_id_user_id"),
    )
    # Written as SQL: the column list of SET NULL is Postgres' own (15 or later).
    op.execute(
        "ALTER TABLE mail_threads ADD CONSTRAINT fk_mail_threads_posting_id_jobs "
        "FOREIGN KEY (posting_id, user_id) REFERENCES jobs (id, user_id) "
        "ON DELETE SET NULL (posting_id)"
    )
    op.create_index("ix_mail_threads_user_id_posting_id", "mail_threads", ["user_id", "posting_id"])

    op.create_table(
        "mail_messages",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        _uuid("thread_id", nullable=False),
        _text("gmail_message_id", default=False),
        sa.Column("message_id_header", sa.Text(), nullable=True),
        sa.Column("references_header", sa.Text(), nullable=True),
        _text("from_addr"),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("reply_to", sa.Text(), nullable=True),
        _texts("to_addrs"),
        _texts("cc_addrs"),
        _at("received_at", nullable=False),
        _text("subject"),
        _text("snippet"),
        _text("body_text"),
        sa.Column("body_html_sanitized", sa.Text(), nullable=True),
        _flag("truncated"),
        _flag("has_remote_images"),
        _flag("trusted"),
        sa.Column("from_domain", sa.Text(), nullable=True),
        _flag("suspicious"),
        sa.Column("direction", _enum("mail_message_direction"), nullable=False),
        _texts("labels"),
        sa.Column("attachments", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("before_connection", sa.Boolean(), nullable=False),
        _at("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_messages")),
        sa.ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["mail_threads.id", "mail_threads.user_id"],
            name="fk_mail_messages_thread_id_mail_threads",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "gmail_message_id", name="uq_mail_messages_user_id_gmail_message_id"
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_mail_messages_id_user_id"),
        sa.CheckConstraint(
            "jsonb_typeof(attachments) = 'array'",
            name=op.f("ck_mail_messages_attachments_is_array"),
        ),
    )
    op.create_index(
        "ix_mail_messages_thread_id_received_at", "mail_messages", ["thread_id", "received_at"]
    )

    op.create_table(
        "mail_classifications",
        _uuid("message_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("label", _enum("mail_label"), nullable=False),
        sa.Column("source", _enum("mail_classification_source"), nullable=False),
        sa.Column("rule", sa.Text(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=False),
        _uuid("proposal_id", nullable=True),
        _flag("applied"),
        _flag("undone"),
        _at("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_mail_classifications")),
        sa.ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["mail_messages.id", "mail_messages.user_id"],
            name="fk_mail_classifications_message_id_mail_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_mail_classifications_proposal_id_proposals"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_mail_classifications_confidence_range"),
        ),
    )

    op.create_table(
        "mail_confirmed_domains",
        _uuid("user_id", nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        _at("confirmed_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint(
            "user_id", "company_key", "domain", name=op.f("pk_mail_confirmed_domains")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_confirmed_domains_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "company_key <> '' AND domain <> ''",
            name=op.f("ck_mail_confirmed_domains_key_not_empty"),
        ),
    )

    op.create_table(
        "mail_assist_batches",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        sa.Column(
            "status", _enum("mail_assist_batch_status"), nullable=False, server_default="running"
        ),
        sa.Column("estimate_low", sa.Integer(), nullable=False),
        sa.Column("estimate_high", sa.Integer(), nullable=False),
        sa.Column("tokens_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threads_total", sa.Integer(), nullable=False),
        sa.Column("threads_done", sa.Integer(), nullable=False, server_default="0"),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_assist_batches")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_assist_batches_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "estimate_low >= 0 AND estimate_high >= estimate_low AND tokens_spent >= 0 "
            "AND threads_total >= 0 AND threads_done >= 0",
            name=op.f("ck_mail_assist_batches_counts_not_negative"),
        ),
    )
    op.create_index(
        "uq_mail_assist_batches_user_id_open",
        "mail_assist_batches",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'paused')"),
    )


def downgrade() -> None:
    op.drop_table("mail_assist_batches")
    op.drop_table("mail_confirmed_domains")
    op.drop_table("mail_classifications")
    op.drop_table("mail_messages")
    op.drop_table("mail_threads")
    op.drop_table("mail_accounts")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE {name}")
    op.drop_constraint("uq_jobs_id_user_id", "jobs", type_="unique")
