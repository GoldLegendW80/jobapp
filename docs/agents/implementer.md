# Rules for every implementer (read once, follow exactly)

An implementer is a subagent that builds one task (an issue) on its own branch and opens one pull
request. The orchestrator gives it a brief (template in [orchestration.md](orchestration.md));
this file holds what the brief does not repeat. First read [shared-rules.md](shared-rules.md). Each
item of [review-checklist.md](review-checklist.md) names the `when/` file to read when it applies
to your diff; a line below that says "read when/<file>.md first" binds you the same way.

There is NO CI per pull request: the merge gate is `ops/local-check.sh`, run by the orchestrator
on the merged batch through `ops/ship.sh`. You never run the gate. Python runs with `uv run`; the
stack and the folders are in the Map of CLAUDE.md.

## Git

Make your own worktree from `origin/main` after `git -C <main> fetch -q origin`:
`git -C <main> worktree add -b <module>/<task-id>-<slug> .claude/worktrees/<name> origin/main`.

## Pace

- Stop and hand back after about 90 tool calls even if unfinished: commit, push, open the pull
  request as it is with an exact "Left to do" list in its body. A fresh agent continues.
- Commit and push work in progress within the first hour and after each step: a session limit can
  cut every running agent at once, and unpushed work is lost.

## Code

- Write it like the surrounding code: comment density, naming, idiom.
- Stay inside the files the task owns. A change outside must be small, justified in the pull
  request body, and listed there.
- Shared lists: if you must add a line to a hand edited registry (journey groups in
  `ops/local-check.sh`, the e2e group list, imports in `api/app/worker.py`, `api/app/config.py`,
  `.env.example`), keep it a single line, append it in sorted position, and name it in the pull
  request body. These files cause most batch conflicts.
- Heuristic code (a parser, classifier, matcher, rule set, regex over user or web text; half the
  items of later review rounds): read [when/heuristic-code.md](when/heuristic-code.md) first.
- An Alembic revision, or a change to `api/alembic/env.py` or `ops/migrate.sh`: read
  [when/migrations.md](when/migrations.md) first.

## Tests

- No sleeps, no wall clock bounds, no real clock where an injected one exists. A privacy marker
  is a planted random token (the gate runs under a temporary folder whose path holds words like
  `private`, so a marker such as "private" fails).
- Run tests through `ops/agent-test.sh` (CLAUDE.md, Daily commands), which caps you so the gate can
  run beside you: throwaway Postgres and Redis, `nice -n 15`, at most `-n 2`; journeys on one
  worker, refused while the gate holds its lock.
- Before pushing, run the commands of CLAUDE.md, Daily commands:
  - api: the WHOLE test folder of the module you touched (every Docker marked parser file too if
    you touched the parser), then ruff check and ruff format --check in `api/`;
  - web: typecheck (it covers `web/e2e`: Playwright runs a journey without type checking it),
    vitest on the folders you touched, lint;
  - always: `ci_plan_test.py` and `e2e_groups.py` (fast, and the gate runs them; a test that
    globs `*.md` fails the plan test);
  - shell: when you changed a `*.sh` file, `ops/`, a `tests/*_test.sh` or a workflow step, read
    [when/shell-scripts.md](when/shell-scripts.md) first.
- Then `grep -rn` in `api/tests` and `web/e2e` for assertions of any behaviour you changed, and
  for the readers of any field or contract you changed (registries such as `FIELD_TYPES`, old
  editors and imports that omit a new key, other modules), and fix them in the same pull request
  (earlier tasks broke main by running only their own files).
- When your diff touches a journey, a page a journey drives, a Compose stack or a Docker image:
  read [when/journeys.md](when/journeys.md) first.

## Speed rules (owner, 2026-10-14)

- Size: a pull request stays under about 1500 changed lines, fixtures and generated files
  excluded. Pull requests above 3000 lines needed twice as many review rounds. If the task is
  bigger, ship the first coherent slice (model and API with tests, or one screen) as its own pull
  request and list the rest under "Left to do"; the orchestrator opens the follow up.
- Self review before the push: run every item of [review-checklist.md](review-checklist.md) that
  applies to your diff. In the pull request body, add a "Self review" section with one line per
  item applied. Reviewers block on the same list, so each missed item costs a round of about 30
  minutes.
- Before the push: merge `origin/main` into your branch and rerun your tests and the typecheck on
  the merged tree (merges without a conflict still broke both); sweep docs, plan
  text, the pull request body and UI strings for values your change made stale
  (`grep -rn <old value>`).
- Dependencies: when your pull request needs another open one merged first, put
  `Depends on #<n>` as the FIRST line of the body. `ops/ship.sh` holds it back until that one
  merges.

## Pull request

`gh pr create` from the worktree, with the template of `.github/pull_request_template.md`. The
body holds:
- `Depends on #<n>` as line 1 when there is a dependency;
- what was built against each acceptance line, with the test or evidence that shows it;
- edge cases covered, and those not covered with why;
- what you ran, with results;
- files changed outside the owned ones;
- "Self review" (one line per checklist item applied) and "Declared open points" for heuristics;
- open questions for the owner;
- `Closes #<issue>` (or `Refs #<issue>` for a slice that does not finish it);
- the last line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Commit messages in normal prose ending with
`Co-Authored-By: Claude <model name> <noreply@anthropic.com>` (your model's name).

## Hand back

Your final message, nothing else: pull request number and head sha; one line per acceptance item
(done or not); the result of each test command in one line; changes outside the owned files;
what is left open. Leave the worktree in place (the orchestrator removes it after the merge).
