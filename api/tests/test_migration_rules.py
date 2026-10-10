"""Expand first, contract later: a migration must leave the previous release able to run.

A deploy applies the migrations of the new images while the previous release still serves, and
a failed start rolls back to the previous images on the migrated schema, without a downgrade
(issue #81; CONTRIBUTING.md, Migrations). So `upgrade()` may add, and may not take away or
tighten what the release before it reads and writes, unless that release already stopped using
it. This guard reads every revision file and flags, in `upgrade()` only:

- `drop_table`, `drop_column`, `rename_table`;
- `alter_column` that renames (`new_column_name`), retypes (`type_`) or forbids NULL
  (`nullable=False`);
- `add_column` of a NOT NULL column with no `server_default` (the previous release inserts rows
  without it);
- raw SQL that drops a table or a column, renames, or sets NOT NULL.

A flagged statement passes with an explicit marker, on the statement or in the comment lines
right above it, that says why the previous release is not hurt:

    # contract-ok: jobs.legacy_fit is no longer read or written since release 0011
    op.drop_column("jobs", "legacy_fit")

It is a cheap net, not a proof: it cannot see a constraint that the previous release's writes
would break, or a change of meaning. The rule in CONTRIBUTING.md is what holds.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MARKER = re.compile(r"#\s*contract-ok:\s*(?P<reason>.*)$")
MIN_REASON = 15

DESTRUCTIVE_CALLS = {
    "drop_table": "drops a table",
    "drop_column": "drops a column",
    "rename_table": "renames a table",
}
DESTRUCTIVE_SQL = re.compile(
    r"\b(drop\s+(table|column)|rename\s+(to|column|constraint)|set\s+not\s+null)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    line: int
    what: str


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _is_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _call_name(call: ast.Call) -> str | None:
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _breach(call: ast.Call) -> str | None:
    """What this call takes away from the previous release, or None."""
    name = _call_name(call)
    if name in DESTRUCTIVE_CALLS:
        return DESTRUCTIVE_CALLS[name]
    if name == "alter_column":
        if _keyword(call, "new_column_name") is not None:
            return "renames a column"
        if _keyword(call, "type_") is not None:
            return "changes a column type"
        if _is_false(_keyword(call, "nullable")):
            return "forbids NULL in an existing column"
    if name == "add_column":
        for arg in ast.walk(call):
            if (
                isinstance(arg, ast.Call)
                and _call_name(arg) == "Column"
                and _is_false(_keyword(arg, "nullable"))
                and _keyword(arg, "server_default") is None
            ):
                return "adds a NOT NULL column without a server_default"
    return None


def _marked(lines: list[str], first: int, last: int) -> bool:
    """A `# contract-ok: <reason>` on lines first..last (1-based) or in the comments just above."""
    start = first
    while start > 1 and lines[start - 2].lstrip().startswith("#"):
        start -= 1
    for line in lines[start - 1 : last]:
        match = MARKER.search(line)
        if match and len(match.group("reason").strip()) >= MIN_REASON:
            return True
    return False


def check_source(source: str) -> list[Finding]:
    """The statements of `upgrade()` that break the expand rule and carry no marker."""
    tree = ast.parse(source)
    lines = source.splitlines()
    findings: list[Finding] = []
    for func in tree.body:
        if not (isinstance(func, ast.FunctionDef) and func.name == "upgrade"):
            continue
        for stmt in ast.walk(func):
            if not isinstance(stmt, ast.stmt) or isinstance(stmt, ast.FunctionDef):
                continue
            # Only the innermost statement that holds the call or the SQL text is reported.
            if any(isinstance(child, ast.stmt) for child in ast.iter_child_nodes(stmt)):
                continue
            reasons = [
                why
                for node in ast.walk(stmt)
                if isinstance(node, ast.Call) and (why := _breach(node)) is not None
            ]
            reasons += [
                f"raw SQL: {match.group(0).lower()}"
                for node in ast.walk(stmt)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                for match in [DESTRUCTIVE_SQL.search(node.value)]
                if match
            ]
            last = stmt.end_lineno or stmt.lineno
            if reasons and not _marked(lines, stmt.lineno, last):
                findings.append(Finding(stmt.lineno, "; ".join(dict.fromkeys(reasons))))
    return sorted(findings, key=lambda finding: finding.line)


def migration(upgrade_body: str, downgrade_body: str = "    pass") -> str:
    return (
        "import sqlalchemy as sa\n"
        "from alembic import op\n\n\n"
        f"def upgrade() -> None:\n{upgrade_body}\n\n\n"
        f"def downgrade() -> None:\n{downgrade_body}\n"
    )


def test_every_revision_follows_the_expand_rule() -> None:
    files = sorted(VERSIONS.glob("*.py"))
    assert len(files) >= 10, "the revision files were not found"
    breaches = [
        f"{path.name}:{finding.line}: {finding.what}"
        for path in files
        for finding in check_source(path.read_text(encoding="utf-8"))
    ]
    assert not breaches, (
        "these migrations take something away from the release that is still running while "
        "they apply (CONTRIBUTING.md, Migrations). Split the change over two releases, or mark "
        "the statement with `# contract-ok: <why the previous release is not hurt>`:\n  "
        + "\n  ".join(breaches)
    )


@pytest.mark.parametrize(
    ("body", "what"),
    [
        ('    op.drop_column("jobs", "fit")', "drops a column"),
        ('    op.drop_table("jobs")', "drops a table"),
        ('    op.rename_table("jobs", "postings")', "renames a table"),
        ('    op.alter_column("jobs", "fit", new_column_name="score")', "renames a column"),
        ('    op.alter_column("jobs", "fit", type_=sa.Text())', "changes a column type"),
        (
            '    op.alter_column("jobs", "fit", nullable=False)',
            "forbids NULL in an existing column",
        ),
        (
            '    op.add_column("jobs", sa.Column("fit", sa.Integer(), nullable=False))',
            "adds a NOT NULL column without a server_default",
        ),
        ('    op.execute("ALTER TABLE jobs DROP COLUMN fit")', "raw SQL: drop column"),
        ('    op.execute("alter table jobs rename to postings")', "raw SQL: rename to"),
        ('    op.execute(f"ALTER TABLE {1} ALTER fit SET NOT NULL")', "raw SQL: set not null"),
        (
            '    with op.batch_alter_table("jobs") as batch:\n        batch.drop_column("fit")',
            "drops a column",
        ),
        ('    if True:\n        op.drop_table("jobs")', "drops a table"),
    ],
)
def test_a_contracting_statement_is_flagged(body: str, what: str) -> None:
    findings = check_source(migration(body))
    assert [finding.what for finding in findings] == [what]


@pytest.mark.parametrize(
    "body",
    [
        '    op.add_column("jobs", sa.Column("fit", sa.Integer(), nullable=True))',
        '    op.add_column("jobs", sa.Column("fit", sa.Integer()))',
        '    op.add_column("j", sa.Column("n", sa.Integer(), nullable=False, server_default="0"))',
        '    op.create_table("t", sa.Column("id", sa.Integer(), nullable=False))',
        '    op.create_index("ix_jobs_fit", "jobs", ["fit"])',
        '    op.alter_column("jobs", "fit", nullable=True)',
        '    op.alter_column("jobs", "fit", server_default="0")',
        "    op.execute(\"CREATE TYPE kind AS ENUM ('a', 'b')\")",
        '    op.drop_index("ix_jobs_fit", table_name="jobs")',
    ],
)
def test_an_expanding_statement_passes(body: str) -> None:
    assert check_source(migration(body)) == []


def test_downgrade_may_drop() -> None:
    source = migration(
        '    op.add_column("jobs", sa.Column("fit", sa.Integer()))',
        '    op.drop_column("jobs", "fit")\n    op.execute("DROP TYPE kind")',
    )
    assert check_source(source) == []


def test_a_marker_with_a_reason_lets_the_statement_through() -> None:
    above = (
        "    # Second half of the fit rework.\n"
        "    # contract-ok: jobs.legacy_fit is not read or written since release 0011\n"
        '    op.drop_column("jobs", "legacy_fit")'
    )
    inline = '    op.drop_table("old")  # contract-ok: the table was emptied and unused since 0012'
    inside = (
        "    op.alter_column(\n"
        '        "jobs",\n'
        '        "fit",\n'
        "        nullable=False,  # contract-ok: every release since 0011 always writes fit\n"
        "    )"
    )
    for body in (above, inline, inside):
        assert check_source(migration(body)) == []


@pytest.mark.parametrize(
    "comment",
    [
        "# contract-ok:",
        "# contract-ok: ok",
        "# contract ok: the previous release does not use it",
        "# expand first, contract later",
    ],
)
def test_a_marker_without_a_real_reason_does_not_count(comment: str) -> None:
    body = f'    {comment}\n    op.drop_column("jobs", "fit")'
    assert [finding.what for finding in check_source(migration(body))] == ["drops a column"]


def test_a_marker_covers_only_its_own_statement() -> None:
    body = (
        "    # contract-ok: jobs.legacy_fit is not read or written since release 0011\n"
        '    op.drop_column("jobs", "legacy_fit")\n'
        "\n"
        '    op.drop_column("jobs", "fit")'
    )
    findings = check_source(migration(body))
    assert [(finding.line, finding.what) for finding in findings] == [(9, "drops a column")]


def test_every_breach_of_one_file_is_reported() -> None:
    body = (
        '    op.drop_table("a")\n'
        '    op.add_column("b", sa.Column("c", sa.Integer()))\n'
        '    op.drop_table("d")'
    )
    assert [finding.line for finding in check_source(migration(body))] == [6, 8]
