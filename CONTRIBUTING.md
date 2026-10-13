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
4. CI (`.github/workflows/ci.yml`) runs on the pull request, on the self-hosted runners of
   Harald's Mac (`[self-hosted, mac-ci]`, three of them with 3 CPUs and 4 GB each; all of them
   wait while the Mac is asleep or Docker Desktop is closed; README, CI runners on the Mac), in
   three jobs, one per runner:
   `api` (ruff, the whole pytest suite against Postgres and Redis, the coverage floor of 95
   percent, the docker marked tests), `checks` (pre-commit, the secret scan of every commit
   the pull request brings, shellcheck, the `tests/*.sh`
   scripts and the guards, then the e2e groups apps and ingestion) and `journeys` (eslint, tsc,
   build, Vitest,
   then the e2e groups identity and sweep; every e2e group against its own scratch Compose
   stack). The run must be green: all three jobs, none of which can be skipped. A pull request
   that touches documentation only (`tasks/`, `docs/`, `CONTRIBUTING.md`, `CAPABILITY-MAP.md`,
   the `SPEC-*.md` files, the pull request template) runs
   pre-commit alone (the shell tests too when it touches `README.md`, which they check, and
   the api tests when it touches `tasks/ingestion/todo.md`, whose table of citation examples
   an api test checks);
   one that touches `web/` only skips the api tests; anything else runs
   everything, and so does every push to `main` (README, What a CI run does). Only a pull
   request against `main` is narrowed this way: one against another branch (stacked on an
   unmerged pull request) runs everything, whatever it touches. A new
   `web/e2e/*.spec.ts` joins one of the e2e groups in the workflow, or the exclusion list of
   `.github/scripts/e2e_groups.py` with its reason; CI fails otherwise. A spec of the ingestion
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
   the gate that judges it (a pull request runs its own copy of all three): the reviewer reads
   it as such, and reads the plan line of the run (`ci_plan: ...`, the first step of each job)
   against the files of the diff. The same goes for what the secret scan goes by, which a
   pull request also brings itself: `.gitleaks.toml`, `.gitleaksignore`, any added
   `gitleaks:allow` comment, `.github/scripts/secret_scan.sh` and the gitleaks block of
   `.pre-commit-config.yaml`. The scan's log names them when a pull request touches one
   (`secret_scan: NOTE: ...`).
6. The orchestrator merges with a **merge commit** (`gh pr merge --merge --delete-branch`): the
   branch's commits, one per step of the agent's work, stay readable in the history, and the
   merge commit names the pull request. Squash is not used. **A pull request is mergeable
   when** the latest `pull_request` run of CI for its current head commit, started against
   the base it will merge into, concluded success, and the latest review verdict on that head
   is APPROVE. "All checks green" alone is not the test: it is true while no check has
   registered yet, and checks belong to the head commit, so they stay green when the base of
   the pull request is changed, which starts no run. To read the three facts:

   ```bash
   gh pr view <n> --json headRefOid,baseRefName                                      # the head commit and the base
   gh run list --workflow ci.yml --event pull_request --commit <head> --limit 1      # the latest run for that head: completed, success
   gh run view <run> --log | grep 'ci_plan: '                                        # the base that run was started against, and its plan
   gh api repos/GoldLegendW80/jobapp/pulls/<n>/reviews --jq '.[-1] | .commit_id, (.body | split("\n")[0])'   # the latest verdict and the commit it is about
   ```

   A run that started before `main` last moved tested the old `main`:
   `ops/ci-rerun.sh <n>` gives the same head a fresh run (below).
7. Every push to `main` deploys: once CI is green on `main`, `.github/workflows/deploy.yml` runs
   on the self-hosted runner on the Jetson (README, Continuous delivery).

`main` is always deployable: nothing is pushed to it directly, and a red `main` is fixed first.

A job that never starts ("Waiting for a runner") means the Mac's runners are offline:
`ops/ci-runners.sh status` on the Mac, then `ops/ci-runners.sh start`. A run that stays queued
although `status` shows the runners listening and idle was queued while a runner was being
recreated and is never assigned: cancel it and start it again. A job that died with a lost runner
(the Mac went to sleep) is rerun with `gh run rerun <id> --failed`.

To test an open pull request again after `main` moved (the merge queue, a changed workflow):
`ops/ci-rerun.sh <number>`. It waits until GitHub's merge commit for the pull request is `main`
as it is now plus the branch, then closes and reopens the pull request, which starts a pull
request run on that commit; the branch gets no commit, so a review of its head stays valid.
`gh run rerun` would test the old merge commit with the old workflow, `gh workflow run` the
branch alone with its own copy of the workflow, and a close and reopen done by hand right after
`main` moved the stale merge commit (README, What a CI run does). When GitHub never reports
a fresh merge commit (seen once, for a draft against another branch than `main`), the script
gives up after two minutes with nothing changed, and a commit on the branch is what starts a
fresh run. A new workflow
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
kept by convention and by the orchestrator, which merges only what step 6 above calls mergeable. Moving to a paid plan or a public repository would let a ruleset require the CI checks (the three job names `api`, `checks`, `journeys`, kept stable for that) and one approval on `main`.

## Locally before pushing

```bash
pre-commit run --all-files
pre-commit run gitleaks-commits --hook-stage manual      # the secret scan of CI: the whole history of the branch (at a commit the hook reads the staged changes only)
(cd api && uv run --native-tls pytest -m "not docker")    # with TEST_DATABASE_URL, or Docker for a throwaway database
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test && pnpm --dir web build
tests/deploy_test.sh                                     # when deploy.sh, ops/ or tests/*.sh change
tests/migrate_test.sh                                    # by hand, not in CI: when ops/migrate.sh or api/alembic/env.py change (real Docker, four api builds, no other scratch stack up)
tests/ci_runners_test.sh                                 # when ops/ci-runners.sh, ops/ci-rerun.sh, ops/ci-runner/ or the workflows change
python3 .github/scripts/ci_plan_test.py                  # when ci.yml, .pre-commit-config.yaml or the path rules of ci_plan.py change
tests/secret_scan_test.sh                                # when .github/scripts/secret_scan.sh or the gitleaks hooks change
```
