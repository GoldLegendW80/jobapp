# Migrations

Read this when a trigger line sent you here: your diff adds a revision under
`api/alembic/versions/`, changes `api/alembic/env.py` or `ops/migrate.sh`, or changes what the
code stores in JSONB. A reviewer reads it for the same diffs. Gathered on 2026-10-29 from
CONTRIBUTING.md, from the header of [review-checklist.md](../review-checklist.md) (never blocked in
either corpus, keep doing them) and from checklist item 17.

- The rule is CONTRIBUTING.md, "Migrations: expand first, contract in a later release"
  (`grep -n '^## Migrations' CONTRIBUTING.md`, read to the next `##`): the previous release runs
  on the new schema, so a migration expands only, `downgrade()` is still written and tested, and
  a contract step waits for a later release. The guard is `api/tests/test_migration_rules.py`;
  `# contract-ok: <why>` marks a statement the guard may let through.
- What the code stores in JSONB follows the same rule (ship the reader one release before the
  writer, or say in the pull request that the release is a one way door).
- When `ops/migrate.sh` or `api/alembic/env.py` change, run `tests/migrate_test.sh` by hand (real
  Docker, four api builds, no other scratch stack up).
- Migration numbers chain on main's head (checklist item 17,
  [contracts-and-plans.md](contracts-and-plans.md)).
