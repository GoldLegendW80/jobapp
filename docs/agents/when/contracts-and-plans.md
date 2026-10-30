# Changed contracts, and plan and spec pull requests

Read this when a trigger line sent you here: your diff adds a field or changes a return shape, a
schema, an import or restore format or any contract another module, `browser/` or `web/src` reads;
or the pull request is a plan, a spec or a decisions change. A reviewer reads it for the same
diffs. Moved on 2026-10-29 from checklist items 9 and 17 of
[review-checklist.md](../review-checklist.md). The cases are in
[lessons/contract-drift.md](../lessons/contract-drift.md) and
[lessons/plan-and-spec.md](../lessons/plan-and-spec.md). The `grep -rn` of the assertions of a
changed behaviour stays in implementer.md, Tests.

## Checklist item 9, the check

A new field or a changed contract reaches every reader. (DRIFT: 11 rounds, 11 PRs.)

Check: a save, an import or a restore that omits the new key keeps the stored value (a test with
an old payload); the field is in `FIELD_TYPES` and the schemas where it belongs; grep the callers
in `api/app`, `browser/` and `web/src` and run their modules' tests; a slice stands alone.

## Checklist item 17, the check

Plan and spec PRs: dependencies, one owner per shared file, one run per task, sources. (PLAN: 6
rounds, 5 PRs, 3.6 items each.)

Check: per task "depends on", "owns" and "touches", compared across parallel tasks; migration
numbers chain on main's head; contracts cited with the right task id and the real signature; split
past about 5 deliverables; each decision row cites the owner's words.
