"""Migration 0004_jobs against a real Postgres: schema, enums, indexes, every constraint,
up/down/up with data, offline SQL, and `alembic check` against the models."""

import hashlib
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.config import get_settings
from app.jobs.models import (
    ContractType,
    FileOrigin,
    JobEventKind,
    JobStatus,
    SweepStatus,
    SweepTrigger,
)
from tests.test_migrations import HEAD, alembic_config, current_revision

TABLES = {"jobs", "job_events", "job_files", "sweeps"}
ENUMS = {
    "contract_type": ContractType,
    "job_status": JobStatus,
    "job_event_kind": JobEventKind,
    "file_origin": FileOrigin,
    "sweep_trigger": SweepTrigger,
    "sweep_status": SweepStatus,
}


@pytest.fixture
def at_base(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    yield engine
    command.downgrade(alembic_config(), "base")
    engine.dispose()


@pytest.fixture
def at_head(at_base: sa.Engine) -> sa.Engine:
    command.upgrade(alembic_config(), "head")
    return at_base


def url(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def add_user(conn: sa.Connection, email: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )


def add_job(
    conn: sa.Connection, user: str, slug: str, url: str | None = None, **extra: object
) -> str:
    columns = {"user_id": user, "slug": slug, "company": "Acme", "title": "AI Engineer", "url": url}
    columns.update(extra)
    names = ", ".join(columns)
    values = ", ".join(f":{name}" for name in columns)
    return str(
        conn.execute(
            sa.text(f"INSERT INTO jobs ({names}) VALUES ({values}) RETURNING id"), columns
        ).scalar_one()
    )


def enum_labels(engine: sa.Engine, name: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = :n ORDER BY e.enumsortorder"
                ),
                {"n": name},
            ).scalars()
        )


def fails(engine: sa.Engine, statement: str, params: dict[str, object] | None = None) -> None:
    with pytest.raises((IntegrityError, DataError)), engine.begin() as conn:
        conn.execute(sa.text(statement), params or {})


def test_upgrade_creates_the_jobs_schema(at_head: sa.Engine) -> None:
    inspector = sa.inspect(at_head)
    assert set(inspector.get_table_names()) >= TABLES

    columns = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert set(columns) == {
        "id",
        "user_id",
        "slug",
        "company",
        "title",
        "location",
        "contract_type",
        "url",
        "source",
        "status",
        "fit",
        "fit_overridden",
        "competencies",
        "gaps",
        "description",
        "posting_text",
        "published_on",
        "pay_min",
        "pay_max",
        "pay_currency",
        "pay_period",
        "work_mode",
        "found_on",
        "applied_on",
        "notes",
        "legacy",
        "search",
        "created_at",
        "updated_at",
    }
    for name in (
        "fit", "url", "source", "description", "posting_text", "applied_on",
        "pay_min", "pay_max", "pay_currency", "pay_period", "work_mode",
    ):  # fmt: skip
        assert columns[name]["nullable"], name
    for name in ("slug", "company", "title", "status", "competencies", "gaps", "found_on"):
        assert not columns[name]["nullable"], name
    assert type(columns["search"]["type"]).__name__ == "TSVECTOR"
    assert columns["search"]["computed"]["persisted"] is True
    assert type(columns["legacy"]["type"]).__name__ == "JSONB"
    assert columns["created_at"]["type"].timezone is True

    for table in TABLES:
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
    fks = {
        (table, fk["constrained_columns"][0]): fk
        for table in TABLES
        for fk in inspector.get_foreign_keys(table)
    }
    assert set(fks) == {
        ("jobs", "user_id"),
        ("job_events", "job_id"),
        ("job_events", "user_id"),
        ("job_files", "job_id"),
        ("sweeps", "user_id"),
    }
    assert all(fk["options"]["ondelete"] == "CASCADE" for fk in fks.values())

    indexes = {ix["name"]: ix for ix in inspector.get_indexes("jobs")}
    assert indexes["ix_jobs_user_id_url"]["unique"]
    assert indexes["ix_jobs_user_id_url"]["dialect_options"]["postgresql_where"] == (
        "(url IS NOT NULL)"
    )
    assert indexes["ix_jobs_user_id_status"]["column_names"] == ["user_id", "status"]
    assert indexes["ix_jobs_user_id_found_on"]["column_sorting"] == {"found_on": ("desc",)}
    assert indexes["ix_jobs_user_id_fit"]["column_names"] == ["user_id", "fit"]
    assert indexes["ix_jobs_user_id_company"]["column_names"] == ["user_id", "company"]
    assert indexes["ix_jobs_search"]["dialect_options"]["postgresql_using"] == "gin"
    uniques = {u["name"]: u for u in inspector.get_unique_constraints("jobs")}
    assert uniques["uq_jobs_user_id_slug"]["column_names"] == ["user_id", "slug"]
    sweeps = {ix["name"]: ix for ix in inspector.get_indexes("sweeps")}
    running = sweeps["ix_sweeps_one_running_per_user"]
    assert running["unique"]
    assert running["dialect_options"]["postgresql_where"] == "(status = 'running'::sweep_status)"


def test_enums_hold_exactly_the_python_values(at_head: sa.Engine) -> None:
    for name, cls in ENUMS.items():
        assert enum_labels(at_head, name) == [member.value for member in cls], name
    assert enum_labels(at_head, "job_event_kind")[-2] == "import"  # Python name import_


def test_job_constraints(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        alice, bob = add_user(conn, "a@b.co"), add_user(conn, "b@b.co")
        add_job(conn, alice, "acme-ai-engineer", "https://acme.example/jobs/1")
        add_job(conn, alice, "acme-ai-engineer-2")  # no url: many allowed
        add_job(conn, alice, "acme-ai-engineer-3")
        # The same slug and the same url for another user.
        add_job(conn, bob, "acme-ai-engineer", "https://acme.example/jobs/1")
        row = conn.execute(
            sa.text(
                "SELECT status, contract_type, fit, fit_overridden, competencies, gaps, notes, "
                "legacy, location, found_on = CURRENT_DATE FROM jobs WHERE slug = 'acme-ai-"
                "engineer-2'"
            )
        ).one()
        assert tuple(row) == ("found", "CDI", None, False, [], [], "", {}, "", True)
    params = {"u": alice}
    fails(
        at_head,
        "INSERT INTO jobs (user_id, slug, company, title) "
        "VALUES (:u, 'acme-ai-engineer', 'X', 'Y')",
        params,
    )
    fails(
        at_head,
        "INSERT INTO jobs (user_id, slug, company, title, url) "
        "VALUES (:u, 'other', 'X', 'Y', 'https://acme.example/jobs/1')",
        params,
    )
    for bad_slug in ("", "Acme", "acme--ai", "-acme", "acme-", "acmé", "acme ai"):
        fails(
            at_head,
            "INSERT INTO jobs (user_id, slug, company, title) VALUES (:u, :s, 'X', 'Y')",
            {"u": alice, "s": bad_slug},
        )
    bad_updates = [
        "UPDATE jobs SET fit = 101",
        "UPDATE jobs SET fit = -1",
        "UPDATE jobs SET url = ''",
        "UPDATE jobs SET legacy = '[]'::jsonb",
        "UPDATE jobs SET status = 'archived'",
        "UPDATE jobs SET contract_type = 'cdi'",  # labels are case-sensitive
        "UPDATE jobs SET company = NULL",
        "UPDATE jobs SET search = NULL",  # generated
        "INSERT INTO jobs (user_id, slug, company, title) "
        "VALUES (gen_random_uuid(), 'x', 'X', 'Y')",
    ]
    for statement in bad_updates:
        with pytest.raises(sa.exc.DBAPIError), at_head.begin() as conn:
            conn.execute(sa.text(statement))
    with at_head.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET fit = 0 WHERE slug = 'acme-ai-engineer-2'"))
        conn.execute(sa.text("UPDATE jobs SET fit = 100 WHERE slug = 'acme-ai-engineer-3'"))


def test_full_text_column_follows_the_row(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        add_job(
            conn,
            user,
            "a",
            company="Société Générale",
            title="Ingénieur IA",
            location="Paris, France",
            notes="Relancer lundi",
        )
        add_job(conn, user, "b", company="Mistral AI", title="Applied AI Engineer")

        def hits(query: str) -> list[str]:
            return list(
                conn.execute(
                    sa.text(
                        "SELECT slug FROM jobs WHERE search @@ "
                        "websearch_to_tsquery('simple', :q) ORDER BY slug"
                    ),
                    {"q": query},
                ).scalars()
            )

        assert hits("ingénieur") == ["a"]
        assert hits("INGÉNIEUR paris") == ["a"]  # case-insensitive, all words
        assert hits("ingenieur") == []  # 'simple' keeps accents
        assert hits("relancer") == ["a"]  # notes are searched
        assert hits("engineer") == ["b"]
        assert hits("engineers") == []  # no stemming
        assert hits("ai") == ["b"]  # "IA" is another word
        conn.execute(sa.text("UPDATE jobs SET notes = 'shortlist demain' WHERE slug = 'b'"))
        assert hits("demain") == ["b"]


def test_job_files_constraints(at_head: sa.Engine) -> None:
    content = b"%PDF-1.7 \x00\xff"
    digest = hashlib.sha256(content).digest()
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        job = add_job(conn, user, "a")
    insert = (
        "INSERT INTO job_files (job_id, name, mime, bytes, size, sha256, origin) "
        "VALUES (:j, :n, :m, :b, :s, :h, :o)"
    )
    good = {
        "j": job,
        "n": "cv.pdf",
        "m": "application/pdf",
        "b": content,
        "s": len(content),
        "h": digest,
        "o": "legacy",
    }
    with at_head.begin() as conn:
        conn.execute(sa.text(insert), good)
        conn.execute(sa.text(insert), {**good, "b": b"", "s": 0, "n": "empty.txt"})
    for change in (
        {"s": len(content) + 1},  # size must be the byte count
        {"s": len(content) - 1},
        {"h": digest[:31]},  # sha256 is 32 bytes
        {"h": b""},
        {"n": ""},
        {"m": ""},
        {"o": "scraped"},
        {"j": "00000000-0000-0000-0000-000000000000"},
    ):
        fails(at_head, insert, {**good, **change})
    fails(at_head, "UPDATE job_files SET bytes = '\\x00'::bytea WHERE name = 'cv.pdf'")


def test_one_running_sweep_per_user(at_head: sa.Engine) -> None:
    insert = "INSERT INTO sweeps (user_id, trigger) VALUES (:u, :t)"
    with at_head.begin() as conn:
        alice, bob = add_user(conn, "a@b.co"), add_user(conn, "b@b.co")
        conn.execute(sa.text(insert), {"u": alice, "t": "manual"})
        conn.execute(sa.text(insert), {"u": bob, "t": "schedule"})  # another user: fine
        row = conn.execute(sa.text("SELECT status, stats, finished_at FROM sweeps")).first()
        assert tuple(row) == ("running", {}, None)
    fails(at_head, insert, {"u": alice, "t": "schedule"})
    # Finished sweeps do not count, and finished_at goes with the status.
    fails(at_head, "UPDATE sweeps SET status = 'done' WHERE user_id = :u", {"u": alice})
    fails(at_head, "UPDATE sweeps SET finished_at = now() WHERE user_id = :u", {"u": alice})
    fails(at_head, "UPDATE sweeps SET stats = 'null'::jsonb")
    fails(at_head, "INSERT INTO sweeps (user_id, trigger) VALUES (:u, 'cron')", {"u": alice})
    with at_head.begin() as conn:
        conn.execute(
            sa.text("UPDATE sweeps SET status = 'done', finished_at = now() WHERE user_id = :u"),
            {"u": alice},
        )
        conn.execute(sa.text(insert), {"u": alice, "t": "manual"})
        conn.execute(
            sa.text(
                "UPDATE sweeps SET status = 'failed', finished_at = now(), error = 'boom' "
                "WHERE user_id = :u AND status = 'running'"
            ),
            {"u": alice},
        )
        conn.execute(sa.text(insert), {"u": alice, "t": "manual"})
        counts = conn.execute(
            sa.text("SELECT status, count(*) FROM sweeps WHERE user_id = :u GROUP BY 1 ORDER BY 1"),
            {"u": alice},
        ).all()
        assert [tuple(r) for r in counts] == [("running", 1), ("done", 1), ("failed", 1)]


def test_events_and_cascades(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        alice, bob = add_user(conn, "a@b.co"), add_user(conn, "b@b.co")
        job = add_job(conn, alice, "a")
        add_job(conn, bob, "a")
        conn.execute(
            sa.text("INSERT INTO job_events (job_id, user_id, kind) VALUES (:j, :u, 'created')"),
            {"j": job, "u": alice},
        )
        conn.execute(
            sa.text(
                "INSERT INTO job_files (job_id, name, mime, bytes, size, sha256, origin) VALUES "
                "(:j, 'a', 'text/plain', '', 0, sha256(''::bytea), 'upload')"
            ),
            {"j": job},
        )
        conn.execute(
            sa.text("INSERT INTO sweeps (user_id, trigger) VALUES (:u, 'manual')"), {"u": alice}
        )
    fails(
        at_head,
        "INSERT INTO job_events (job_id, user_id, kind) VALUES (:j, :u, 'deleted')",
        {"j": job, "u": alice},
    )
    fails(
        at_head,
        "INSERT INTO job_events (job_id, user_id, kind, data) VALUES (:j, :u, 'note', '[1]')",
        {"j": job, "u": alice},
    )
    with at_head.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE id = :j"), {"j": job})
        assert conn.execute(sa.text("SELECT count(*) FROM job_events")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM job_files")).scalar_one() == 0
        conn.execute(sa.text("DELETE FROM users"))
        for table in TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_up_down_up_with_data(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        job = add_job(conn, user, "a", "https://x.example/1")
        conn.execute(
            sa.text("INSERT INTO job_events (job_id, user_id, kind) VALUES (:j, :u, 'import')"),
            {"j": job, "u": user},
        )
        conn.execute(
            sa.text("INSERT INTO sweeps (user_id, trigger) VALUES (:u, 'manual')"), {"u": user}
        )
        conn.execute(
            sa.text("INSERT INTO profiles (user_id, sections) VALUES (:u, '{}')"), {"u": user}
        )
    command.downgrade(alembic_config(), "0003")
    assert current_revision(url(at_head)) == "0003"
    names = set(sa.inspect(at_head).get_table_names())
    assert not TABLES & names
    assert {"users", "profiles"} <= names  # earlier modules untouched
    for name in ENUMS:
        assert enum_labels(at_head, name) == []
    with at_head.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM profiles")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert current_revision(url(at_head)) == HEAD
    with at_head.begin() as conn:
        add_job(conn, user, "a", "https://x.example/1")  # a fresh, empty schema
    command.downgrade(alembic_config(), "0003")
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_offline_sql_has_the_jobs_tables(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0003:0004", sql=True)
    out = capsys.readouterr().out
    for table in TABLES:
        assert f"CREATE TABLE {table}" in out
    for name in ENUMS:
        assert f"CREATE TYPE {name} AS ENUM" in out
    assert "GENERATED ALWAYS AS" in out
    assert "USING gin" in out
    assert "WHERE status = 'running'" in out
    command.downgrade(alembic_config(), "0004:0003", sql=True)
    out = capsys.readouterr().out
    for table in TABLES:
        assert f"DROP TABLE {table}" in out
    for name in ENUMS:
        assert f"DROP TYPE {name}" in out


def test_0005_indexes_the_slot_of_scheduled_sweeps(at_head: sa.Engine) -> None:
    with at_head.connect() as conn:
        definition = conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": "ix_sweeps_user_id_schedule_slot"},
        ).scalar_one()
    assert "(stats ->> 'schedule_slot'::text)" in definition
    assert "WHERE (trigger = 'schedule'::sweep_trigger)" in definition
    command.downgrade(alembic_config(), "0004")
    with at_head.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"),
                {"n": "ix_sweeps_user_id_schedule_slot"},
            ).scalar_one()
            == 0
        )
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())
