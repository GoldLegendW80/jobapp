# Contributing

How a change reaches the Jetson. The work is done by agents, coordinated by an orchestrator
session; Harald decides what gets built. The intent behind this flow is `docs/intent/agent-features.md`
in the job folder that holds this repository (it is not part of the repository).

## One task, one branch, one pull request

1. A task of `tasks/todo.md` or of a module's `tasks/<module>/todo.md` gets its own branch from `main`, named `<module>/<task-id>-<slug>`,
   for example `jobs/j4-rescore`, `platform/7-ci` or `profile/p5-import-preview`.
2. Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat(jobs): ...`,
   `fix(identity): ...`, `ci: ...`, `docs: ...`) and end with the agent's attribution line:

   ```
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   ```

3. The branch is pushed and a pull request is opened with `gh pr create`; the template
   (`.github/pull_request_template.md`) asks for what, why, the spec and task, tests, evidence and
   risks.
4. No CI run per pull request (the owner's decision of 2026-10-13, issue #116: a run took about
   ten minutes on the Mac's runners and runs queued behind each other, so five approved pull
   requests meant an hour of waiting). The checks are the ones CI ran, and they run once per
   batch, natively on the Mac, by `ops/local-check.sh` (step 6). The author runs what the change
   touches before pushing (Locally before pushing, below) and says in the pull request what was
   run. The batch check always runs the full set, whatever the batch touches, which is the steps
   of the three jobs of `.github/workflows/ci.yml`:
   `api` (ruff, the whole pytest suite against Postgres and Redis, the coverage floor of 95
   percent, the docker marked tests, the scoring timing test), `checks` (pre-commit, the secret
   scan of every commit the batch brings, shellcheck, the `tests/*_test.sh` scripts and the
   guards, the e2e groups apps, ingestion and mail) and `journeys` (eslint, tsc, build, Vitest, the
   e2e groups identity and sweep; every e2e group against its own scratch Compose stack).
   `ci.yml` stays the definition of those steps and starts by hand only
   (`gh workflow run ci.yml --ref <branch>`, with a runner started: README, CI runners on the
   Mac). The batch check reads two lists from the `ci.yml` of the tree it checks: the files of
   the Shellcheck step and the `tests/*_test.sh` steps. So a new `tests/*_test.sh` gets a step
   in `ci.yml`, and a new shell script joins the Shellcheck step. A new
   `web/e2e/*.spec.ts` joins one of the e2e groups in the workflow, or the exclusion list of
   `.github/scripts/e2e_groups.py` with its reason; the check fails otherwise. A spec added to
   the groups apps or sweep is also added to `journey_specs` in `ops/local-check.sh`
   (`tests/local_check_test.sh` fails when the two differ). A spec of the ingestion
   module is named `ingestion-<task>.spec.ts` and joins its group by that name alone
   (`web/e2e/ingestion-all.config.ts` runs them all). Every step of `ci.yml`
   has an `id` and is listed in `.github/scripts/ci_plan.py`: a new check under its unit in
   `LANES`, with a condition on that unit's plan output, anything else in `SUPPORT`.
   `ci_plan_test.py` fails on a step that is in no list, on a check that does not follow its
   unit and on a test command filed under `SUPPORT`.
5. A separate Claude review subagent, run by the orchestrator with fresh context, reviews the diff
   and posts its verdict as a review comment, `gh pr review <n> --comment --body-file <review>`,
   whose first line is `REVIEW VERDICT: APPROVE` or `REVIEW VERDICT: REQUEST CHANGES`, followed
   by the findings. (`--approve` and `--request-changes` are not used: every agent acts as the
   repository owner, and GitHub does not let the author of a pull request approve it.) Findings
   are fixed on the same branch and the subagent reviews the new head. A diff that touches
   `.github/scripts/ci_plan.py`, `ci_plan_test.py` or a step condition of `ci.yml` changes
   the definition of the checks. So does one that touches `ops/local-check.sh` or
   `ops/deploy-checked.sh`, the gate itself since issue #116, or the two lists of `ci.yml` the
   batch check reads (the Shellcheck files, the `tests/*_test.sh` steps): the check is run with
   the script of the main checkout, on the lists of the tree it checks, so a pull request can
   take a step out of its own check. The reviewer reads such a diff as a change of the gate. The same goes for what the secret scan goes by, which a
   pull request also brings itself: `.gitleaks.toml`, `.gitleaksignore`, any added
   `gitleaks:allow` comment, `.github/scripts/secret_scan.sh` and the gitleaks block of
   `.pre-commit-config.yaml`. The scan's log names them when a pull request touches one
   (`secret_scan: NOTE: ...`).
6. The orchestrator merges with a **merge commit** (`gh pr merge --merge --delete-branch`): the
   branch's commits, one per step of the agent's work, stay readable in the history, and the
   merge commit names the pull request. Squash is not used. **A pull request is mergeable
   when** the latest review verdict on its CURRENT head commit is APPROVE. After any push, a
   reviewer confirms the new head: a verdict about an older commit does not count. To read the
   two facts:

   ```bash
   gh pr view <n> --json headRefOid                                                  # the head commit
   gh api repos/GoldLegendW80/jobapp/pulls/<n>/reviews --jq '.[-1] | .commit_id, (.body | split("\n")[0])'   # the latest verdict and the commit it is about
   ```

   **Approved pull requests merge as a batch, in a stated order, after the batch check passed
   on that order.** No run in between:

   ```bash
   ops/local-check.sh --merge <n1> --merge <n2> --merge <n3>   # main, then each head merged in that order. The proof is the last line, exactly: LOCAL CHECK: PASS <tree sha> <seconds>s
   gh pr merge <n1> --merge --delete-branch                    # the same order, back to back
   gh pr merge <n2> --merge --delete-branch
   gh pr merge <n3> --merge --delete-branch
   git fetch origin && git rev-parse 'origin/main^{tree}'      # must print the tree sha of the PASS line
   ops/deploy-checked.sh                                       # step 7
   ```

   The tree sha is the key. The check writes a record `.local-check/<tree sha>.json` in the main
   checkout (git ignored) only when the full set passed, and once the pull requests are merged
   in the same order, `git rev-parse 'origin/main^{tree}'` equals that sha exactly when what
   was checked is what is on `main`. When it differs (another order, a head pushed after the
   check, something merged in between), `main` is checked again before any deploy:
   `ops/local-check.sh` with no argument checks `origin/main` as it is. Exit status of the
   check: 0 every step that ran passed (a proof only with the `LOCAL CHECK: PASS` line,
   below), 1 a check failed (the failed steps are named on the last line, the end of
   their logs is shown, the full logs are kept in the folder it prints), 2 a merge conflict (it
   names the ref: that branch is updated first, which is a new head and so a new review
   confirmation), 3 another check holds the lock or the disk is under 12 GiB free.

   What counts as proof. Only a last line that begins with exactly `LOCAL CHECK: PASS `,
   followed by the tree sha, and the record `.local-check/<tree sha>.json` written next to it.
   That line is printed by a run of the full set with the real steps and by nothing else. A
   run with `--only` ends with `LOCAL CHECK: PARTIAL PASS <tree sha> ...` and writes no
   record: it is a tool for the author, never a proof for a merge or a deploy. A run of the
   script's own tests ends with `LOCAL CHECK: STUB PASS ...`. Exit status 0 alone proves
   nothing, a partial run has it too. Before the record is written the check compares the
   steps that ran with the list of the full set, and a step that did not run fails the check.
   A new `tests/*_test.sh` gets a step `run: tests/<name>_test.sh` in `ci.yml`, or a line with
   its reason in `shell_tests_left_out` of `ops/local-check.sh`: one that has neither fails
   the check. The secret scan reads every commit since the last tree that has a PASS record
   (50 commits before the base when there is none), and prints that range.

   What the batch check does not give. There is no check per pull request: a failure belongs
   to the batch, and is bisected by running the check again with fewer `--merge` refs. It runs
   only while the Mac is awake and Docker Desktop is open, one check at a time. Nothing on
   GitHub shows its result: the record on the Mac and the `LOCAL CHECK: PASS` line are the
   proof, so the orchestrator quotes that whole line when it merges.

   **One command for steps 6 and 7: `ops/ship.sh`** (issue #278). It does the sequence above
   and stops at the first thing that is not as it should be:

   ```bash
   ops/ship.sh --plan --approved   # which pull requests, in which order, with which gate: reads GitHub only, changes nothing
   ops/ship.sh --approved          # every open pull request approved on its current head, not a draft, MERGEABLE
   ops/ship.sh 281 279             # these, in this order; one that is not approved on its head stops the run
   ```

   A pull request whose body has a line beginning `Depends on #N` or `Stacked on #N` ships only
   after #N: once #N is merged, or after it in the same batch; otherwise it waits (and so do the
   pull requests that depend on it). A pull request that changes `ops/local-check.sh` goes first
   and the batch is checked with its own version of the script, from a detached worktree of its
   head (a second one, or one that depends on another pull request of the batch, waits for the
   next run). A failed batch is checked again once: a PASS ships (the first failure is named
   flaky), other failing steps stop the run (`SHIP: FAIL flaky gate`), the same failure is
   bisected: the pull request that makes it fail is dropped with its dependents and gets a
   comment with the step blamed and the last 30 lines of its log (each line that may hold a
   secret withheld), and the rest is checked again; a main that fails alone stops the run.
   It merges only when main and every head are still what the PASS record lists (`gh pr merge
   --repo <owner/repo> --match-head-commit`), and moves the issues the merged pull requests
   close to Done on the board right after the merges. Then it pulls the main checkout (which
   must be clean and on `main`), deploys only when its tree is the gate's tree, and checks the
   Jetson over ssh with the deploy's compose profile (every service running and healthy, a
   stopped or missing one counts, `alembic current` at head, memory, no error line in the last
   10 minutes, JSON `"level": "error"` lines included). The last line is `SHIP: OK`,
   `SHIP: PLAN, nothing was done`, `SHIP: NOTHING TO SHIP` or `SHIP: FAIL <reason>` (exit 1);
   the gate and deploy logs of the last 10 runs stay in `.local-check/ship/`.
7. Only a checked tree is deployed, through `ops/deploy-checked.sh`. It refuses a commit whose
   tree has no PASS record, and a commit that is not on `origin/main`. Then it starts
   `.github/workflows/deploy.yml` on the Jetson's own runner for the full sha
   (`gh workflow run deploy.yml -f ref=<sha>`), waits for that run, prints its conclusion and
   asks the public health URL (README, Continuous delivery). Nothing deploys by itself any
   more: the deploy workflow has no trigger but the dispatch. `--dry-run` says what it would
   start.

`main` is always deployable: nothing is pushed to it directly, and a `main` whose check fails is fixed first.

The Mac's CI runners (`ops/ci-runners.sh`, README, CI runners on the Mac) are optional and
stopped by default since issue #116. They serve a run of `ci.yml` started by hand, and nothing
else: `ops/ci-runners.sh status` on the Mac, then `ops/ci-runners.sh start`, then
`gh workflow run ci.yml --ref <branch>`. A job that never starts ("Waiting for a runner") means
they are stopped or the Mac is asleep. `ops/ci-rerun.sh` gave a pull request a fresh run by
closing and reopening it; `ci.yml` has no pull request trigger any more, so it starts nothing
today. A new workflow
file cannot use the Mac's runners until `ops/ci-runner/job-started.sh` allows it, and no job may
target `self-hosted` alone: that label is also the Jetson's. Test jobs name their worker count
(`PYTEST_WORKERS`, `VITEST_WORKERS`) and never ask for every core: the Mac is shared.

## Migrations: expand first, contract in a later release

A deploy applies the migrations of the new release while the previous release is still serving,
then starts the new code (`ops/migrate.sh`, issue #81). If the new code does not come up, the
previous images are started again on the migrated schema, and nothing ever downgrades it. So for
a few seconds on every deploy, and for as long as it takes after a rollback, **the previous
release runs on the new schema**. A migration must leave it able to.

- **Expand** in the release that needs the change: new tables, new nullable columns or columns
  with a `server_default`, new indexes, new enum values. The previous release ignores them.
- **Contract** in a later release, once no deployed code reads or writes the thing: drop a column
  or a table, rename, change a type, add `NOT NULL`. A rename is three steps over releases: add
  the new column and write both, move the readers, drop the old one.
- Never in one release: drop or rename something the previous release uses, add a `NOT NULL`
  column without a `server_default` (the previous release inserts rows without it), tighten a
  constraint its writes would break.
- A migration is one transaction and must be quick: it waits for the locks of the running release
  and is stopped after `MIGRATE_TIMEOUT_S` (300 seconds), rolled back.
- `downgrade()` is still written and tested, for a human who decides to go back; no deploy runs it.

What the code stores in JSONB follows the same rule. The sections of a profile are validated on
every read, so a release that starts storing a form the previous release refuses cannot be taken
back once a user has saved one: the previous release answers 500 on that user's profile. Ship the
reader one release before the writer, or say in the pull request that the release is a one way
door, with the read only query that names who would break before a rollback by hand (P7, a year
alone as a date, was merged that way: its query is in the description of pull request #105).

`api/tests/test_migration_rules.py` reads every revision and fails on a drop, a rename, a type
change, a new `NOT NULL` in `upgrade()`. When the previous release really no longer uses the
thing, say so on the statement and the guard lets it through:

```python
# contract-ok: jobs.legacy_fit is not read or written since release 0011
op.drop_column("jobs", "legacy_fit")
```

The guard is a net for the obvious cases, not a proof. The rule is what holds.

## Enforcement

The repository is private on GitHub's free plan, where protected branches and rulesets are a paid
feature for private repositories. Checked on 2026-10-09: `gh api repos/GoldLegendW80/jobapp/rulesets`
answers an empty list and `.../branches/main/protection` answers 404 "Branch not protected"; no
ruleset or protection was created (that write was not attempted). The rules above are therefore
kept by convention and by the orchestrator, which merges only what step 6 above calls mergeable, after the batch check, and deploys only through `ops/deploy-checked.sh`. Moving to a paid plan or a public repository would let a ruleset require one approval on `main`; the batch check runs on the Mac and has no status on GitHub that a ruleset could require.

## Locally before pushing

```bash
pre-commit run --all-files
pre-commit run gitleaks-commits --hook-stage manual      # the secret scan of CI: the whole history of the branch (at a commit the hook reads the staged changes only)
(cd api && uv run --native-tls pytest -m "not docker")    # with TEST_DATABASE_URL, or Docker for a throwaway database
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test && pnpm --dir web build
# a new or changed journey: run the web type check above, it covers web/e2e (Playwright runs a journey without type checking it)
tests/deploy_test.sh                                     # when deploy.sh, ops/ or tests/*.sh change
tests/migrate_test.sh                                    # by hand, not in CI: when ops/migrate.sh or api/alembic/env.py change (real Docker, four api builds, no other scratch stack up)
tests/ci_runners_test.sh                                 # when ops/ci-runners.sh, ops/ci-rerun.sh, ops/ci-runner/ or the workflows change
python3 .github/scripts/ci_plan_test.py                  # when ci.yml, .pre-commit-config.yaml or the path rules of ci_plan.py change
tests/secret_scan_test.sh                                # when .github/scripts/secret_scan.sh or the gitleaks hooks change
tests/local_check_test.sh                                # when ops/local-check.sh, ops/deploy-checked.sh or the triggers of the workflows change
tests/ship_test.sh                                       # when ops/ship.sh changes
ops/local-check.sh --base HEAD --only api                # one job of the batch check on a commit of one's own (api, checks or journeys; a partial run writes no record)
```
