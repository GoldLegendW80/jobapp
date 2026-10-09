## What

<!-- One or two sentences: what this pull request changes. -->

## Why

<!-- The need behind it. -->

## Spec and task

<!-- SPEC-<module>.md section and the tasks/todo.md task id, e.g. SPEC-jobs.md, Task J4. -->

## Tests

<!-- What was added or changed, the edge cases covered, and the commands run locally. -->

- [ ] `pre-commit run --all-files`
- [ ] api: `uv run pytest` (with a database), web: `pnpm lint && pnpm typecheck && pnpm test && pnpm build`
- [ ] Playwright journey against a running stack, when the change has a UI or an HTTP surface

## Evidence

<!-- Screenshots under tasks/evidence/<task>/, command output, run links. -->

## Risks

<!-- Migrations, data on the Jetson, secrets, rollbacks, anything the reviewer should look at twice. -->
