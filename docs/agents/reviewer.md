# Rules for every reviewer (read once, follow exactly)

A reviewer is a subagent with a fresh context that reviews one pull request and posts one verdict.
The orchestrator gives it a brief (template in [orchestration.md](orchestration.md)).

First read [shared-rules.md](shared-rules.md): the safety, git, context, process and Docker
rules, the same as the builder's. What differs for a reviewer: your own worktree under
`.claude/worktrees/rv<pr>` (`git -C <main> fetch -q origin`, then `git -C <main> worktree add
--detach .claude/worktrees/rv<pr> origin/<branch>`). A fixup is pushed with
`git -C <worktree> push origin HEAD:<branch>` (plain, never forced). Tests through
`ops/agent-test.sh` (at most `-n 2`, under `nice -n 15`); never Playwright while `.local-check/lock`
of the main checkout exists; throwaway containers and stacks named `rv<pr>-*` and removed
afterwards.

Goal: one review round wherever possible. Measured on the 242 reviewed pull requests merged from
2026-10-14 to 2026-10-28 ([lessons/INDEX.md](lessons/INDEX.md)): 44% needed at least one REQUEST
CHANGES round, heuristic rules held 49% of the blocking items of later rounds (42 of 86), and
each round costs a fix agent, a re-check and a wait for the gate.

## Where the verdict lives

The verdict is a review comment on the pull request, and that comment is the only record: the
fix agent, the re-check and `ops/ship.sh` all read it from GitHub. Draft the body in a file of
your own worktree, `.claude/reviews/review-<pr>[-roundN].md` (git ignored, never committed), and
post it with:

```bash
gh pr review <pr> --comment --body-file .claude/reviews/review-<pr>.md
```

`--approve` and `--request-changes` are not used: every agent acts as the repository owner, and
GitHub does not let the author of a pull request approve it.

Format, exactly (`ops/ship.sh` parses lines 1 and 2):

```
REVIEW VERDICT: APPROVE            <- or: REVIEW VERDICT: REQUEST CHANGES
Head: <full 40 character sha>      <- the head AFTER your own fixup commits

## Blocking
1. <file:line> <the failure scenario> <the fix expected>, precise enough that a fix agent needs
   no further reading. "None." when approving.

## Pushed fixups
## Follow ups
## Tests run
```

## The rules

1. **One complete pass.** Find everything in the first round: read the spec lines the pull
   request claims, the diff and the tests; probe the failure paths (concurrency, limits, bad
   input, a killed worker, ownership between users). Never hold findings back for a later round.
2. **Block only on:** wrong behaviour against the spec, data loss, a race, security or privacy, a
   secret or personal data leak, a test that does not test what it claims, a missing test for a
   failure path the spec names, dashes in user facing text, or a change that breaks other
   modules. Everything else is non blocking: list it under "Follow ups" and the orchestrator files
   an issue.
3. **Mechanical fixes: push them yourself.** A one line or obviously local fix (lint, typo, a
   missing import or registration, a wrong constant, a dash in a string, a missing `Closes #`, an
   assertion message) goes in your own commit `review: <what>` on the pull request branch, with a
   plain push; then rerun the affected tests. If the session refuses your commit or push, do not
   retry: list the exact edit under "Fixups for the fix agent" and leave it uncommitted. Logic
   changes are never yours to make: those are REQUEST CHANGES.
4. **Verdict:** as above. The `Head:` line names the head after your fixups, so the approval
   counts for what will merge.
5. **Later rounds:** check only the previous blocking items and the diff since the last reviewed
   head. A new blocker in old code is allowed only for data loss or security; anything else is a
   follow up.
6. **Checklist:** [review-checklist.md](review-checklist.md) is the minimum, not the maximum. For
   each item that applies to the diff, read the file its pointer names before you judge it. To
   judge tests, read the Tests section of [implementer.md](implementer.md), what the author had to
   run. Also read [when/shell-scripts.md](when/shell-scripts.md) for a changed `*.sh` file or
   `ops/` script,
   [when/gate-and-ship.md](when/gate-and-ship.md) for a gate change (rule 7),
   [when/migrations.md](when/migrations.md) for an Alembic revision, and
   [when/journeys.md](when/journeys.md) to run or judge a journey or a Docker stack.
7. **Gate changes:** a diff that touches `ops/local-check.sh`, `ops/deploy-checked.sh`,
   `.github/scripts/ci_plan.py`, `.github/scripts/ci_plan_test.py`, a step condition or the Shellcheck and
   `tests/*_test.sh` lists of `.github/workflows/ci.yml`, or what the secret scan goes by
   (a `.gitleaks.toml` or `.gitleaksignore` when one is added, a new `gitleaks:allow`,
   `.github/scripts/secret_scan.sh`, the gitleaks block of `.pre-commit-config.yaml`)
   changes the gate itself: read it as such (CONTRIBUTING.md, step 5).

## Hand back

Your final message, nothing else: the verdict, the head sha, the blocking items in one line each,
the fixups pushed, the follow ups.
