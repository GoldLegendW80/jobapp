# Rules for every implementer (read once, follow exactly)

An implementer is a subagent that builds one task (an issue) on its own branch and opens one pull
request. The orchestrator gives it a brief (template in [orchestration.md](orchestration.md));
this file holds everything the brief does not repeat. The reviewer's rules are in
[reviewer.md](reviewer.md) and the self review list in [review-checklist.md](review-checklist.md).

Repo: public `GoldLegendW80/jobapp`. FastAPI + Postgres 16 + Redis + arq worker in `api/` (Python
run with `uv run`; add `--native-tls` on a network that intercepts TLS, as the owner's does),
React + Tailwind + shadcn + TanStack Query in `web/` (pnpm), plus the `parser`, `renderer` and
`browser` services, Caddy and Tailscale, all in Docker Compose. There is NO CI per pull request:
the merge gate is `ops/local-check.sh`, run by the orchestrator on the merged batch through
`ops/ship.sh`. You never run the gate.

## Git

- The main checkout (the clone the orchestrator works from) is never edited and never switches
  branch. Make your own worktree from `origin/main` after `git -C <main> fetch -q origin`:
  `git -C <main> worktree add -b <module>/<task-id>-<slug> .claude/worktrees/<name> origin/main`.
  `.claude/worktrees/` is git ignored. Work only there, with absolute paths.
- One git command per call, `git -C <worktree> ...`. If a hook of your shell rewrites commands
  (an rtk style proxy), call git by its absolute path (`/usr/bin/git` on macOS).
- Plain pushes only, never force. Never bypass git hooks (no `core.hooksPath=/dev/null`, no
  `--no-verify`): if a hook fails, fix the cause.
- Never push to `main`, never merge, never deploy. The orchestrator merges with `ops/ship.sh`.

## Context costs money

- Never print a whole file or log: `grep -n`, `sed -n 'a,bp'`, `| tail -n 30`,
  `pytest -q ... | tail -n 15`.
- Never read `tasks/ingestion/todo.md` or `tasks/mail/todo.md` whole (one holds a table of
  hundreds of examples): grep the section heading, read by line range.
- No exploratory reruns. Wait for nothing after the push.
- Stop and hand back after about 90 tool calls even if unfinished: commit, push, open the pull
  request as it is with an exact "Left to do" list in its body. A fresh agent continues.
- Commit and push work in progress within the first hour and after each step: a session limit can
  cut every running agent at once, and unpushed work is lost.

## Safety

- Never read `.env` files (`.env`, `.env.jetson`, `.env.local`, `.env.*.local`, `.env.production`, `.env.prod`, `.env.mailsend`, `.env.redis`), the `.secrets/` folder or production credentials;
  never print, grep or log a secret. `.env.example` is the tracked template: read and edit it freely (add a new setting there).
- Tests use only `e2e-*@example.org` accounts and never touch real user data.
- Never contact a real Google host; never send mail.
- No cybersecurity specific feature work.
- The repository is PUBLIC (since 2026-10-15): never commit personal data, secrets, tokens,
  e-mail addresses, phone numbers or private URLs. Fixtures, evidence and examples use invented
  data (`Example Robotics`, `e2e-someone@example.org`).
- Anything that needs sudo, a password or a 2FA code on any machine is the owner's: write the
  exact command in the hand back and stop.

## Text shown to a user

No dashes as separators: no em dash, no en dash, no " - ". Use commas, colons or a new sentence.
Hyphenated words are fine. This holds for UI strings, e-mails, generated documents, error
messages and the pull request body.

## Code

- Write it like the surrounding code: comment density, naming, idiom.
- Stay inside the files the task owns. A change outside must be small, justified in the pull
  request body, and listed there.
- Shared lists: if you must add a line to a hand edited registry (journey groups in
  `ops/local-check.sh`, the e2e group list, imports in `api/app/worker.py`, `api/app/config.py`,
  `.env.example`), keep it a single line, append it in sorted position, and name it in the pull
  request body. These files cause most batch conflicts.
- Heuristic code (a parser, classifier, matcher, rule set, regex over user or web text): 79% of
  second and later review rounds came from these. Before writing the rule, write the accept and
  refuse table from the spec as a parametrized test: 30 to 40 neighbour cases around every rule
  (near misses, other languages, spacing, case, punctuation, empty, huge) plus a corpus of true
  inputs that must stay untouched. Make the default fail closed (when unsure, do the safe thing
  the spec names). Prefer an allow list of what is accepted to a deny list of what is refused.
  Model the structure (a tree, a period, a vocabulary) instead of adding strings. Keep it pure and
  linear in input size. List what it still misses under "Declared open points" in the pull
  request body, so it is not re-blocked.

## Tests

- No sleeps, no wall clock bounds, no real clock where an injected one exists. A privacy marker
  is a planted random token (the gate runs under a temporary folder whose path holds words like
  `private`, so a marker such as "private" fails).
- Run tests through `ops/agent-test.sh`, which caps you so the gate can run beside you:
  `ops/agent-test.sh --name <task> tests/<module> -m "not docker" -n 2` (throwaway Postgres and
  Redis, `nice -n 15`, at most `-n 2`), and `ops/agent-test.sh --name <task> --playwright <args>`
  for journeys (one worker, refused while the gate holds its lock).
- Before pushing, run:
  - api: the WHOLE test folder of the module you touched (every Docker marked parser file too if
    you touched the parser), then `uv run ruff check app tests` and
    `uv run ruff format --check app tests` in `api/`;
  - web: `pnpm --dir web run typecheck` (it covers `web/e2e`: Playwright runs a journey without
    type checking it), `pnpm --dir web exec vitest run src/features/<the folders you touched>`,
    `pnpm --dir web run lint`;
  - always: `python3 .github/scripts/ci_plan_test.py` and `python3 .github/scripts/e2e_groups.py`
    (fast, and the gate runs them; a test that globs `*.md` fails the plan test);
  - shell: every `tests/*_test.sh` of a script you changed, and shellcheck through the pinned
    image: `docker run --rm --name <task>-sc -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 <files>`.
- Then `grep -rn` in `api/tests` and `web/e2e` for assertions of any behaviour you changed, and
  fix those tests in the same pull request (earlier tasks broke main by running only their own
  files).
- Playwright journeys, when the task has one: grep `ops/local-check.sh` for `stack_up` and
  `s_journey` to see how the gate starts a Compose stack for a journey group, and do the same
  with your own project name (`-p <task>-$RANDOM`), `-f compose.yml -f compose.mac.yml` on an
  Apple silicon Mac as the gate does, and your OWN image tags through an override file (never
  retag or rebuild `jobapp-*:latest`). Run ALL journeys of your module's group, not only your new
  file. A journey that has never run hides most of its bugs: run every new journey on its own
  stack before you push. Take the stack down with its volumes and `docker rmi` your tags after.
- Shared machine: other agents may run beside you. If the folder `.local-check/lock` of the main
  checkout exists, the gate is running: start nothing heavy until it is gone. If `df` shows under
  12 GiB free, stop building images and say so in the hand back.

## Processes and Docker

- Never start a process that outlives your own command: no `&` background jobs, no `while :`
  loops, no `nohup`, no artificial CPU or memory load ("burners") to simulate a busy machine. A
  load test, if asked for, runs real tests side by side in the foreground of ONE command and ends
  with it. (A reviewer once left 24 CPU loops running for 2.5 hours and broke two merge gates.)
- Never run `docker system prune`, `docker network prune`, `docker volume prune` or
  `docker image prune` with filters wider than your own names: the machine hosts the owner's
  other projects. Remove only the containers, networks, volumes and images you created, by name.

## Speed rules (owner, 2026-10-14)

- Size: a pull request stays under about 1500 changed lines, fixtures and generated files
  excluded. Pull requests above 3000 lines needed twice as many review rounds. If the task is
  bigger, ship the first coherent slice (model and API with tests, or one screen) as its own pull
  request and list the rest under "Left to do"; the orchestrator opens the follow up.
- Self review before the push: run every item of [review-checklist.md](review-checklist.md) that
  applies to your diff. In the pull request body, add a "Self review" section with one line per
  item applied. Reviewers block on the same list, so each missed item costs a round of about 30
  minutes.
- Before the push: merge `origin/main` into your branch and rerun your tests; sweep docs, plan
  text, the pull request body and UI strings for values your change made stale
  (`grep -rn <old value>`).
- Dependencies: when your pull request needs another open one merged first, put
  `Depends on #<n>` as the FIRST line of the body. `ops/ship.sh` holds it back until that one
  merges.

## Shared files

Never delete or edit files you did not create outside your worktree: other agents' worktrees,
the orchestrator's notes, `.claude/reviews/` drafts of others, the main checkout. Work only in
your own worktree and your own files.

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
