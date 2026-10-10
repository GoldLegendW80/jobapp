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
   Harald's Mac (`[self-hosted, mac-ci]`, two of them with 3 CPUs and 4 GB each: a run's jobs
   go through them two at a time, and all of them wait while the Mac is asleep or Docker Desktop
   is closed; README, CI runners on the Mac),
   every job on a runner of its own:
   `api (jobs)` and `api (rest)` (ruff, then pytest against Postgres and Redis in two shards),
   `api coverage` (the coverage of both shards combined, at least 95 percent: this job is where
   the floor is checked, and it fails when a shard failed), `web checks` (eslint, tsc, build),
   `web vitest` (two shards), `shell` (shellcheck, `tests/deploy_test.sh`, the e2e groups guard),
   `e2e (identity)`, `e2e (sweep)` and `e2e (apps)` (the Playwright journeys in three groups, each
   against its own scratch Compose stack, with a synthetic jobs import) and `pre-commit`. It must
   be green. A new `web/e2e/*.spec.ts` joins one of the e2e groups in the workflow, or the
   exclusion list of `.github/scripts/e2e_groups.py` with its reason; CI fails otherwise.
5. A separate Claude review subagent, run by the orchestrator with fresh context, reviews the diff
   and posts its verdict as a review comment, `gh pr review <n> --comment --body-file <review>`,
   whose first line is `REVIEW VERDICT: APPROVE` or `REVIEW VERDICT: REQUEST CHANGES`, followed
   by the findings. (`--approve` and `--request-changes` are not used: every agent acts as the
   repository owner, and GitHub does not let the author of a pull request approve it.) Findings
   are fixed on the same branch and the subagent reviews the new head.
6. The orchestrator merges when CI is green and the latest verdict is APPROVE, with a **merge commit**
   (`gh pr merge --merge --delete-branch`): the branch's commits, one per step of the agent's work,
   stay readable in the history, and the merge commit names the pull request. Squash is not used.
7. Every push to `main` deploys: once CI is green on `main`, `.github/workflows/deploy.yml` runs
   on the self-hosted runner on the Jetson (README, Continuous delivery).

`main` is always deployable: nothing is pushed to it directly, and a red `main` is fixed first.

A job that never starts ("Waiting for a runner") means the Mac's runners are offline:
`ops/ci-runners.sh status` on the Mac, then `ops/ci-runners.sh start`. A job that died with a
lost runner (the Mac went to sleep) is rerun with `gh run rerun <id> --failed`. A new workflow
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
kept by convention and by the orchestrator, which merges only green and approved pull requests. Moving to a paid plan or a public repository would let a ruleset require the CI checks and one approval on `main`.

## Locally before pushing

```bash
pre-commit run --all-files
(cd api && uv run --native-tls pytest -m "not docker")    # with TEST_DATABASE_URL, or Docker for a throwaway database
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test && pnpm --dir web build
tests/deploy_test.sh                                     # when deploy.sh, ops/ or tests/*.sh change
tests/ci_runners_test.sh                                 # when ops/ci-runners.sh, ops/ci-runner/ or the workflows change
```
