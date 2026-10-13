"""Migration 0012 on real Postgres: ingestion_runs, ingestion_run_steps and ingestion_items as the
DDL of SPEC-ingestion.md writes them, both ways, with their keys, cascades and backstops."""

from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.ingestion import plan, runs_models
from tests.ingestion.test_migration import (
    add_document,
    add_user,
    at_base,  # noqa: F401, a fixture
    at_head,  # noqa: F401, a fixture
    columns,
    enum_labels,
    url,
)
from tests.test_migrations import HEAD, alembic_config, current_revision

TABLES = {"ingestion_runs", "ingestion_run_steps", "ingestion_items"}
ENUMS = {
    "ingestion_run_status": ["running", "paused", "done", "failed", "cancelled"],
    "ingestion_step_kind": ["transcribe", "extract"],
    "ingestion_step_status": ["planned", "running", "done", "failed", "cancelled", "reused"],
}
# SPEC-ingestion.md, the DDL, column by column: name -> (type, nullable).
RUN_COLUMNS = {
    "id": ("UUID", False),
    "user_id": ("UUID", False),
    "document_ids": ("ARRAY", False),
    "base_revision": ("INTEGER", False),
    "status": ("ENUM", False),
    "partial": ("BOOLEAN", False),
    "paused_reason": ("TEXT", True),
    "paused_at": ("TIMESTAMP", True),
    "advance_failures": ("INTEGER", False),
    "advance_failed_at": ("TIMESTAMP", True),
    "closed_reason": ("TEXT", True),
    "waiting": ("TEXT", True),
    "deadline_at": ("TIMESTAMP", False),
    "cancel_requested_at": ("TIMESTAMP", True),
    "estimate_low": ("BIGINT", False),
    "estimate_high": ("BIGINT", False),
    "token_ceiling": ("BIGINT", False),
    "tokens_spent": ("BIGINT", False),
    "ceiling_history": ("JSONB", False),
    "created_at": ("TIMESTAMP", False),
    "finished_at": ("TIMESTAMP", True),
}
STEP_COLUMNS = {
    "id": ("UUID", False),
    "ingestion_run_id": ("UUID", False),
    "position": ("INTEGER", False),
    "kind": ("ENUM", False),
    "document_id": ("UUID", False),
    "sha256": ("BYTEA", False),
    "part": ("INTEGER", True),
    "range_start": ("INTEGER", True),
    "range_end": ("INTEGER", True),
    "image_positions": ("ARRAY", True),
    "identity_hash": ("TEXT", False),
    "text_sha256": ("BYTEA", True),
    "status": ("ENUM", False),
    "agent_run_id": ("UUID", True),
    "attempt": ("SMALLINT", False),
    "token_budget": ("BIGINT", True),
    "cut_budget": ("BIGINT", True),
    "refused_count": ("INTEGER", False),
    "two_digit_year_count": ("INTEGER", False),
    "items_count": ("INTEGER", False),
    "purge_events": ("BOOLEAN", False),
}
ITEM_COLUMNS = {
    "id": ("BIGINT", False),
    "user_id": ("UUID", False),
    "document_id": ("UUID", False),
    "step_id": ("UUID", False),
    "section": ("TEXT", False),
    "key": ("TEXT", False),
    "action": ("TEXT", False),
    "value": ("JSONB", True),
    "value_hash": ("TEXT", False),
    "citations": ("JSONB", False),
    "flags": ("ARRAY", False),
}

RUN = (
    "INSERT INTO ingestion_runs (user_id, document_ids, base_revision, deadline_at, estimate_low, "
    "estimate_high, token_ceiling) VALUES (:u, ARRAY[CAST(:d AS uuid)], 1, now(), 10, 20, 40) "
    "RETURNING id"
)
STEP = (
    "INSERT INTO ingestion_run_steps (ingestion_run_id, position, kind, document_id, sha256, "
    "part, identity_hash) VALUES (:r, :p, 'extract', :d, :h, 0, :i) RETURNING id"
)
ITEM = (
    "INSERT INTO ingestion_items (user_id, document_id, step_id, section, key, action, value, "
    "value_hash, citations) VALUES (:u, :d, :s, 'identity', :k, 'add', CAST(:v AS jsonb), :vh, "
    "CAST('[]' AS jsonb)) RETURNING id"
)
HASH = "a" * 64


def add_run(conn: sa.Connection, user: str, document: str) -> str:
    return str(conn.execute(sa.text(RUN), {"u": user, "d": document}).scalar_one())


def add_step(conn: sa.Connection, run: str, document: str, position: int = 0) -> str:
    return str(
        conn.execute(
            sa.text(STEP), {"r": run, "p": position, "d": document, "h": b"a" * 32, "i": HASH}
        ).scalar_one()
    )


def add_item(
    conn: sa.Connection, user: str, document: str, step: str, key: str = "city", vh: str = HASH
) -> int:
    row = {"u": user, "d": document, "s": step, "k": key, "v": '"Lyon"', "vh": vh}
    return int(conn.execute(sa.text(ITEM), row).scalar_one())


def seeded(conn: sa.Connection, email: str = "a@example.org") -> tuple[str, str, str, str]:
    user = add_user(conn, email)
    document = add_document(conn, user)
    run = add_run(conn, user, document)
    return user, document, run, add_step(conn, run, document)


def count(conn: sa.Connection, table: str) -> int:
    return int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_upgrade_creates_the_three_tables_with_every_column_of_the_ddl(
    at_head: sa.Engine,  # noqa: F811
) -> None:
    inspector = sa.inspect(at_head)
    assert set(inspector.get_table_names()) >= TABLES
    assert columns(at_head, "ingestion_runs") == RUN_COLUMNS
    assert columns(at_head, "ingestion_run_steps") == STEP_COLUMNS
    assert columns(at_head, "ingestion_items") == ITEM_COLUMNS
    assert {name: enum_labels(at_head)[name] for name in ENUMS} == ENUMS
    runs = {c["name"]: c for c in inspector.get_columns("ingestion_runs")}
    for name in ("deadline_at", "cancel_requested_at", "created_at", "finished_at"):
        assert runs[name]["type"].timezone is True
    assert "now()" in runs["created_at"]["default"]
    items = {c["name"]: c for c in inspector.get_columns("ingestion_items")}
    assert "nextval" in items["id"]["default"]  # bigserial
    command.check(alembic_config())  # the models and the migration say the same


def test_a_new_run_and_step_start_with_the_defaults(at_head: sa.Engine) -> None:  # noqa: F811
    with at_head.begin() as conn:
        _, _, run, step = seeded(conn)
        got = conn.execute(
            sa.text(
                "SELECT status::text, partial, tokens_spent, ceiling_history::text, waiting, "
                "paused_reason, finished_at FROM ingestion_runs WHERE id = :r"
            ),
            {"r": run},
        ).one()
        assert tuple(got) == ("running", False, 0, "[]", None, None, None)
        got = conn.execute(
            sa.text(
                "SELECT status::text, attempt, token_budget, refused_count, two_digit_year_count, "
                "items_count, purge_events, agent_run_id FROM ingestion_run_steps WHERE id = :s"
            ),
            {"s": step},
        ).one()
        assert tuple(got) == ("planned", 0, None, 0, 0, 0, False, None)


def test_keys_cascades_and_indexes(at_head: sa.Engine) -> None:  # noqa: F811
    inspector = sa.inspect(at_head)

    def foreign(table: str) -> dict[tuple[str, ...], tuple[str, tuple[str, ...], str]]:
        return {
            tuple(fk["constrained_columns"]): (
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"]["ondelete"],
            )
            for fk in inspector.get_foreign_keys(table)
        }

    assert foreign("ingestion_runs") == {("user_id",): ("users", ("id",), "CASCADE")}
    assert foreign("ingestion_run_steps") == {
        ("ingestion_run_id",): ("ingestion_runs", ("id",), "CASCADE"),
        ("agent_run_id",): ("agent_runs", ("id",), "SET NULL"),
    }
    assert foreign("ingestion_items") == {
        ("user_id",): ("users", ("id",), "CASCADE"),
        ("document_id", "user_id"): ("ingestion_documents", ("id", "user_id"), "CASCADE"),
        ("step_id",): ("ingestion_run_steps", ("id",), "CASCADE"),
    }
    uniques = {
        table: {tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
        for table in TABLES
    }
    assert uniques["ingestion_run_steps"] == {("ingestion_run_id", "position")}
    assert uniques["ingestion_items"] == {("step_id", "section", "key", "value_hash")}
    indexes = {
        index["name"]: index
        for table in TABLES
        for index in inspector.get_indexes(table)
        if not index.get("duplicates_constraint")
    }
    assert set(indexes) == {
        "uq_ingestion_runs_user_id_active",
        "ix_ingestion_runs_user_id_created_at",
        "ix_ingestion_run_steps_agent_run_id",
        "ix_ingestion_run_steps_document_id",
        "ix_ingestion_items_document_id",
        "ix_ingestion_items_user_id",
    }
    active = indexes["uq_ingestion_runs_user_id_active"]
    assert active["unique"] is True
    assert active["column_names"] == ["user_id"]
    assert "running" in active["dialect_options"]["postgresql_where"]
    assert "paused" in active["dialect_options"]["postgresql_where"]


def test_one_active_run_per_user(at_head: sa.Engine) -> None:  # noqa: F811
    with at_head.begin() as conn:
        user, document, run, _ = seeded(conn)
        other = add_user(conn, "b@example.org")
        add_run(conn, other, add_document(conn, other))  # another user's run is no obstacle
    for status in ("running", "paused"):
        with at_head.begin() as conn:
            conn.execute(
                sa.text("UPDATE ingestion_runs SET status = :s WHERE id = :r"),
                {"s": status, "r": run},
            )
        with (
            pytest.raises(IntegrityError, match="uq_ingestion_runs_user_id_active"),
            at_head.begin() as conn,
        ):
            add_run(conn, user, document)
    for status in ("done", "failed", "cancelled"):
        with at_head.begin() as conn:
            conn.execute(
                sa.text("UPDATE ingestion_runs SET status = :s WHERE user_id = :u"),
                {"s": status, "u": user},
            )
            add_run(conn, user, document)  # a final run never blocks the next one
    with at_head.connect() as conn:
        assert count(conn, "ingestion_runs") == 5


def test_the_same_item_is_stored_once_per_step(at_head: sa.Engine) -> None:  # noqa: F811
    with at_head.begin() as conn:
        user, document, run, step = seeded(conn)
        add_item(conn, user, document, step)
        add_item(conn, user, document, step, key="country")  # another key
        add_item(conn, user, document, step, vh="b" * 64)  # another value at the same key
        add_item(conn, user, document, add_step(conn, run, document, 1))  # another step
    with (
        pytest.raises(IntegrityError, match="uq_ingestion_items_step_id_section_key_value_hash"),
        at_head.begin() as conn,
    ):
        add_item(conn, user, document, step)
    with (
        pytest.raises(IntegrityError, match="uq_ingestion_run_steps_ingestion_run_id_position"),
        at_head.begin() as conn,
    ):
        add_step(conn, run, document, 0)


def test_an_item_cannot_be_filed_under_another_user_than_its_documents(
    at_head: sa.Engine,  # noqa: F811
) -> None:
    with at_head.begin() as conn:
        _, document, _, step = seeded(conn)
        other = add_user(conn, "b@example.org")
    with (
        pytest.raises(IntegrityError, match="fk_ingestion_items_document_id_ingestion_documents"),
        at_head.begin() as conn,
    ):
        add_item(conn, other, document, step)


STEP_HEAD = (
    "INSERT INTO ingestion_run_steps (ingestion_run_id, position, kind, document_id, sha256, "
    "identity_hash"
)
STEP_VALUES = f") VALUES (:r, 5, '{{kind}}', :d, :h, '{HASH}'"
RUN_SET = "UPDATE ingestion_runs SET {change} WHERE id = :r"
ITEM_SET = "UPDATE ingestion_items SET {change} WHERE step_id = :s"


@pytest.mark.parametrize(
    "statement",
    [
        RUN_SET.format(change="document_ids = '{}'"),
        RUN_SET.format(change="base_revision = -1"),
        RUN_SET.format(change="estimate_low = 30"),  # above the high estimate
        RUN_SET.format(change="tokens_spent = -1"),
        RUN_SET.format(change="token_ceiling = -1"),
        RUN_SET.format(change="ceiling_history = '{}'::jsonb"),
        STEP_HEAD + STEP_VALUES.format(kind="extract") + ")",  # an extraction without a part
        STEP_HEAD + ", part, range_start" + STEP_VALUES.format(kind="extract") + ", 0, 5)",
        STEP_HEAD
        + ", part, range_start, range_end"
        + STEP_VALUES.format(kind="extract")
        + ", 0, 9, 5)",
        STEP_HEAD + STEP_VALUES.format(kind="transcribe") + ")",  # a transcription of nothing
        STEP_HEAD + ", image_positions" + STEP_VALUES.format(kind="transcribe") + ", '{}')",
        "UPDATE ingestion_run_steps SET position = -1 WHERE id = :s",
        "UPDATE ingestion_run_steps SET sha256 = '\\x00'::bytea WHERE id = :s",
        "UPDATE ingestion_run_steps SET identity_hash = 'short' WHERE id = :s",
        "UPDATE ingestion_run_steps SET text_sha256 = '\\x00'::bytea WHERE id = :s",
        "UPDATE ingestion_run_steps SET refused_count = -1 WHERE id = :s",
        "UPDATE ingestion_run_steps SET two_digit_year_count = -1 WHERE id = :s",
        "UPDATE ingestion_run_steps SET items_count = -1 WHERE id = :s",
        "UPDATE ingestion_run_steps SET token_budget = -1 WHERE id = :s",
        "UPDATE ingestion_run_steps SET agent_run_id = gen_random_uuid() WHERE id = :s",
        ITEM_SET.format(change="section = ''"),
        ITEM_SET.format(change="key = repeat('k', 401)"),
        ITEM_SET.format(change="action = 'merge'"),
        ITEM_SET.format(change="value = NULL"),  # only a removal goes without a value
        ITEM_SET.format(change="action = 'remove'"),  # and a removal carries none
        ITEM_SET.format(change="value_hash = 'short'"),
        ITEM_SET.format(change="citations = '{}'::jsonb"),
        ITEM_SET.format(change="flags = NULL"),
    ],
)
def test_the_constraints_hold(at_head: sa.Engine, statement: str) -> None:  # noqa: F811
    with at_head.begin() as conn:
        _, document, run, step = seeded(conn)
        add_item(conn, conn.execute(sa.text("SELECT id FROM users")).scalar_one(), document, step)
    params: dict[str, Any] = {"r": run, "s": step, "d": document, "h": b"a" * 32}
    used = {name: value for name, value in params.items() if f":{name}" in statement}
    with pytest.raises(IntegrityError), at_head.begin() as conn:
        conn.execute(sa.text(statement), used)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ingestion_runs SET status = 'merging'",
        "UPDATE ingestion_run_steps SET status = 'queued'",
        "UPDATE ingestion_run_steps SET kind = 'merge'",
    ],
)
def test_an_unknown_enum_label_is_refused(at_head: sa.Engine, statement: str) -> None:  # noqa: F811
    with at_head.begin() as conn:
        seeded(conn)
    with pytest.raises(DataError), at_head.begin() as conn:
        conn.execute(sa.text(statement))


def test_what_later_tasks_store_fits(at_head: sa.Engine) -> None:  # noqa: F811
    """G10 (the chain), G14 (transcription steps) and G15 (the purge) write these columns."""
    with at_head.begin() as conn:
        user, document, run, step = seeded(conn)
        agent_run = conn.execute(
            sa.text(
                "INSERT INTO agent_runs (user_id, agent) VALUES (:u, 'profile_extract') "
                "RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        conn.execute(
            sa.text(
                "UPDATE ingestion_runs SET status = 'paused', paused_reason = 'token_ceiling', "
                "waiting = 'slots', cancel_requested_at = now(), partial = true, "
                "tokens_spent = 9000000000, token_ceiling = 9000000000, "
                "ceiling_history = CAST(:h AS jsonb), finished_at = now() WHERE id = :r"
            ),
            {"r": run, "h": '[{"at": "2026-10-13T10:00:00Z", "spent": 40, "from": 40, "to": 60}]'},
        )
        conn.execute(
            sa.text(
                "UPDATE ingestion_run_steps SET status = 'running', agent_run_id = :a, "
                "attempt = 2, token_budget = 9000000000, range_start = 0, range_end = 100000, "
                "refused_count = 20, two_digit_year_count = 3, items_count = 2000, "
                "purge_events = true WHERE id = :s"
            ),
            {"a": agent_run, "s": step},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ingestion_run_steps (ingestion_run_id, position, kind, document_id, "
                "sha256, image_positions, identity_hash, status) VALUES (:r, 1, 'transcribe', "
                ":d, :h, ARRAY[0, 1, 2, 3, 4, 5, 6, 7], :i, 'reused')"
            ),
            {"r": run, "d": document, "h": b"a" * 32, "i": HASH},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ingestion_items (user_id, document_id, step_id, section, key, "
                "action, value, value_hash, citations, flags) VALUES (:u, :d, :s, 'experience', "
                ":k, 'remove', NULL, :vh, CAST(:c AS jsonb), ARRAY['looks_like_instruction'])"
            ),
            {
                "u": user,
                "d": document,
                "s": step,
                "k": "employers:" + "k" * 390,
                "vh": HASH,
                "c": '[{"start": 0, "end": 24, "excerpt": "Example Works, 2021"}]',
            },
        )
        # A step outlives its agent run: the reference is cleared, the row stays.
        conn.execute(sa.text("DELETE FROM agent_runs WHERE id = :a"), {"a": agent_run})
        assert (
            conn.execute(
                sa.text("SELECT agent_run_id FROM ingestion_run_steps WHERE id = :s"), {"s": step}
            ).scalar_one()
            is None
        )


def test_deleting_a_document_takes_its_items_and_leaves_the_run_and_its_steps(
    at_head: sa.Engine,  # noqa: F811
) -> None:
    with at_head.begin() as conn:
        user, document, run, step = seeded(conn)
        kept = add_document(conn, user, b"b" * 32)
        add_item(conn, user, document, step)
        add_item(conn, user, kept, add_step(conn, run, kept, 1))
        conn.execute(sa.text("DELETE FROM ingestion_documents WHERE id = :d"), {"d": document})
        assert count(conn, "ingestion_items") == 1
        assert count(conn, "ingestion_run_steps") == 2  # ids, status and tokens stay
        assert count(conn, "ingestion_runs") == 1


def test_deleting_a_step_a_run_or_a_user_cascades(at_head: sa.Engine) -> None:  # noqa: F811
    with at_head.begin() as conn:
        user, document, run, step = seeded(conn)
        add_item(conn, user, document, step)
        second = add_step(conn, run, document, 1)
        add_item(conn, user, document, second)
        conn.execute(sa.text("DELETE FROM ingestion_run_steps WHERE id = :s"), {"s": second})
        assert count(conn, "ingestion_items") == 1
        conn.execute(sa.text("DELETE FROM ingestion_runs WHERE id = :r"), {"r": run})
        assert count(conn, "ingestion_run_steps") == 0
        assert count(conn, "ingestion_items") == 0
    with at_head.begin() as conn:
        user, document, run, step = seeded(conn, "c@example.org")
        add_item(conn, user, document, step)
        conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user})
        for table in TABLES:
            assert count(conn, table) == 0


def test_up_down_up_with_data(at_head: sa.Engine) -> None:  # noqa: F811
    with at_head.begin() as conn:
        user, document, _, step = seeded(conn)
        add_item(conn, user, document, step)
    command.downgrade(alembic_config(), "0011")
    assert current_revision(url(at_head)) == "0011"
    assert not TABLES & set(sa.inspect(at_head).get_table_names())
    assert not set(ENUMS) & set(enum_labels(at_head))
    with at_head.connect() as conn:  # what 0011 holds is untouched
        assert count(conn, "users") == 1
        assert count(conn, "ingestion_documents") == 1
    command.upgrade(alembic_config(), "head")
    assert current_revision(url(at_head)) == HEAD
    assert set(sa.inspect(at_head).get_table_names()) >= TABLES
    command.check(alembic_config())
    with at_head.begin() as conn:  # and the tables work again
        document = conn.execute(sa.text("SELECT id FROM ingestion_documents")).scalar_one()
        user = conn.execute(sa.text("SELECT id FROM users")).scalar_one()
        add_item(conn, user, document, add_step(conn, add_run(conn, user, document), document))


def test_offline_sql_has_the_run_tables(
    at_base: sa.Engine,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(alembic_config(), "0011:0012", sql=True)
    up = capsys.readouterr().out
    for name in ENUMS:
        assert f"CREATE TYPE {name}" in up
    for table in TABLES:
        assert f"CREATE TABLE {table}" in up
    assert "CREATE UNIQUE INDEX uq_ingestion_runs_user_id_active" in up
    command.downgrade(alembic_config(), "0012:0011", sql=True)
    down = capsys.readouterr().out
    for table in TABLES:
        assert f"DROP TABLE {table}" in down
    for name in ENUMS:
        assert f"DROP TYPE {name}" in down


def test_identity_hash_is_stable_and_tells_steps_apart() -> None:
    base = plan.Identity("ab" * 32, "extract", (0,), 1, "claude-opus-5-5")
    same = plan.Identity("ab" * 32, plan.StepKind.extract, (0,), 1, "claude-opus-5-5")
    assert runs_models.identity_hash(base) == runs_models.identity_hash(same)
    assert len(runs_models.identity_hash(base)) == runs_models.HASH_HEX_CHARS
    others = [
        plan.Identity("cd" * 32, "extract", (0,), 1, "claude-opus-5-5"),
        plan.Identity("ab" * 32, "transcribe", (0,), 1, "claude-opus-5-5"),
        plan.Identity("ab" * 32, "extract", (1,), 1, "claude-opus-5-5"),
        plan.Identity("ab" * 32, "extract", (0,), 2, "claude-opus-5-5"),
        plan.Identity("ab" * 32, "extract", (0,), 1, "claude-sonnet-5-5"),
    ]
    hashes = {runs_models.identity_hash(identity) for identity in [base, *others]}
    assert len(hashes) == 6
