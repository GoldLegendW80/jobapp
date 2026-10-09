# Contributing

How a change reaches the Jetson. The work is done by agents, coordinated by an orchestrator
session; Harald decides what gets built. Intent: [docs/intent/agent-features.md](../docs/intent/agent-features.md).

## One task, one branch, one pull request

1. A task of `tasks/todo.md` gets its own branch from `main`, named `<module>/<task-id>-<slug>`,
   for example `jobs/j4-rescore`, `platform/7-ci` or `profile/p5-import-preview`.
2. Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat(jobs): ...`,
   `fix(identity): ...`, `ci: ...`, `docs: ...`) and end with the agent's attribution line:

   ```
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   ```

3. The branch is pushed and a pull request is opened with `gh pr create`; the template
   (`.github/pull_request_template.md`) asks for what, why, the spec and task, tests, evidence and
   risks.
4. CI (`.github/workflows/ci.yml`) runs on the pull request: api (ruff, pytest against Postgres
   and Redis, coverage at least 95 percent), web (eslint, tsc, Vitest, build), shell (shellcheck,
   `tests/deploy_test.sh`), e2e (the Playwright journeys against a scratch Compose stack with a
   synthetic jobs import) and pre-commit. It must be green.
5. A separate Claude review subagent, run by the orchestrator with fresh context, reviews the diff
   and posts its verdict on the pull request with `gh pr review` (`--approve`, or
   `--request-changes` with the findings). Findings are fixed on the same branch.
6. The orchestrator merges when CI is green and the review approves, with a **merge commit**
   (`gh pr merge --merge --delete-branch`): the branch's commits, one per step of the agent's work,
   stay readable in the history, and the merge commit names the pull request. Squash is not used.
7. Every push to `main` deploys: once CI is green on `main`, `.github/workflows/deploy.yml` runs
   on the self-hosted runner on the Jetson (README, Continuous delivery).

`main` is always deployable: nothing is pushed to it directly, and a red `main` is fixed first.

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
```
