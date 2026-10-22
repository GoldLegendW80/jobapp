"""Application mail drafts and content-free send audit."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mail_drafts (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        user_id UUID NOT NULL,
        thread_id UUID,
        application_id UUID,
        kind TEXT DEFAULT 'reply' NOT NULL,
        to_addrs TEXT[] DEFAULT '{}'::text[] NOT NULL,
        cc_addrs TEXT[] DEFAULT '{}'::text[] NOT NULL,
        subject TEXT DEFAULT '' NOT NULL,
        body TEXT DEFAULT '' NOT NULL,
        written_by TEXT DEFAULT 'user' NOT NULL,
        version INTEGER DEFAULT '1' NOT NULL,
        status TEXT DEFAULT 'draft' NOT NULL,
        document_versions JSONB DEFAULT '{}' NOT NULL,
        fact_check JSONB,
        gmail_message_id TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        sent_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_mail_drafts PRIMARY KEY (id),
        CONSTRAINT uq_mail_drafts_id_user_id UNIQUE (id, user_id),
        CONSTRAINT uq_mail_drafts_application_id UNIQUE (application_id),
        CONSTRAINT fk_mail_drafts_thread_id_mail_threads FOREIGN KEY(thread_id, user_id)
        REFERENCES mail_threads (id, user_id) ON DELETE CASCADE,
        CONSTRAINT fk_mail_drafts_application_id_applications FOREIGN KEY(application_id,
        user_id) REFERENCES applications (id, user_id) ON DELETE CASCADE,
        CONSTRAINT ck_mail_drafts_kind_allowed CHECK (kind IN ('reply', 'application')),
        CONSTRAINT ck_mail_drafts_kind_subject CHECK ((kind = 'application') = (application_id
        IS NOT NULL)),
        CONSTRAINT ck_mail_drafts_status_allowed CHECK (status IN ('draft', 'sending',
        'send_unknown', 'sent')),
        CONSTRAINT ck_mail_drafts_writer_allowed CHECK (written_by IN ('user', 'agent')),
        CONSTRAINT ck_mail_drafts_version_positive CHECK (version >= 1),
        CONSTRAINT fk_mail_drafts_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON
        DELETE CASCADE
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_mail_drafts_open_thread ON mail_drafts (thread_id) WHERE status
        <> 'sent'
        """)
    op.execute("""
        CREATE TABLE mail_sends (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        user_id UUID NOT NULL,
        draft_id UUID,
        posting_id UUID,
        company_key TEXT,
        authorisation_kind TEXT DEFAULT 'user_click' NOT NULL,
        authorised_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        content_hash TEXT DEFAULT '' NOT NULL,
        recipients_count INTEGER DEFAULT '0' NOT NULL,
        new_recipients BOOLEAN DEFAULT 'false' NOT NULL,
        result TEXT DEFAULT 'refused' NOT NULL,
        refusal_code TEXT,
        message_id_header TEXT,
        gmail_message_id TEXT,
        gmail_thread_id TEXT,
        resolved_by TEXT,
        at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_mail_sends PRIMARY KEY (id),
        CONSTRAINT ck_mail_sends_result_allowed CHECK (result IN ('refused', 'pending', 'sent',
        'failed', 'unknown')),
        CONSTRAINT ck_mail_sends_authorisation_allowed CHECK (authorisation_kind IN
        ('user_click', 'person_approval', 'full_auto_like')),
        CONSTRAINT fk_mail_sends_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON
        DELETE CASCADE
        )
        """)
    op.execute("""
        CREATE INDEX ix_mail_sends_user_id_at ON mail_sends (user_id, at)
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_mail_sends_pending_draft ON mail_sends (draft_id) WHERE result =
        'pending'
        """)
    op.add_column("applications", sa.Column("mail_send_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "mail_send_id")
    op.drop_table("mail_sends")
    op.drop_table("mail_drafts")
