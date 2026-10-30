# Merges with main (MERGE)

Cue: main moved since the branch was cut, or since it was approved; the branch stacks on another
open one.

Counts, corpus 2026-10-14 to 10-28: 15 rounds, 14 pull requests, 15 items, 10 of them in later
rounds (main moved while the pull request waited). Rank 3. Backs review-checklist.md item 3 and
implementer.md, "Speed rules".

## Edit rows

component: review-checklist.md item 11 ("Rebase on `main` and check the merge before you ask",
2 findings in the corpus of 2026-10-14)
candidates: the checklist of 2026-10-14
change: `git merge-tree` shows no CONFLICT before review
observed: 15 rounds in 14 pull requests; 10 came after a first review, when other pull requests
had merged: #459 round 4, #460 round 2, #465 round 2, #691 round 2, #692 round 2, #654 rounds 1
and 3
verdict: no observed effect

component: implementer.md, "Speed rules" ("merge `origin/main` into your branch and rerun your
tests")
candidates: implementer.md since 2026-10-14
change: merge before the push and rerun the tests
observed: merges without a text conflict were red: #427 round 2 ("`git merge-tree` is clean, so
the gate would hit this at ship"), #642 round 2 ("the merge broke the typecheck, which the text
conflict could not show"), #551 round 2 (a merged pull request "made `_variants` return three
values"), #302 round 2 (`live.py` fails the gate after another pull request's lint config merges)
verdict: no observed effect (the rerun was the author's own files, not the typecheck and module)

component: orchestration.md, Task lifecycle step 8 (orchestrator note after a mechanical merge)
candidates: orchestration.md since 2026-10-14
change: rerun the affected tests after a merge of main into an approved head
observed: #642's merge re-check found the broken typecheck before the gate did
verdict: helped (one case)

## Entry MERGE-1

- **cue**: a branch is about to be reviewed or shipped and main has new commits.
- **lesson**: merge `origin/main` last, then run `pnpm --dir web run typecheck` and the whole test
  folder of each module the diff touches on the merged tree; a clean `git merge-tree` is not a
  green merge. The same rerun follows any merge of main after an approval. Line 1 `Depends on`
  names every open branch the head contains.
- **evidence**: #427 round 2, #642 round 2, #551 round 2, #302 round 2 (green text merge, red
  tree); #429, #459, #460, #465, #515, #554, #676, #691, #692 (conflict or behind main; #676
  changed `ops/local-check.sh` and "is 4 commits behind"); #654 round 3 ("Line 1 of the PR body
  reads 'Depends on ...' but the branch now contains #657").
- **confidence**: high: 14 pull requests.
- **supports**: review-checklist.md item 3; implementer.md, "Speed rules" (typecheck and whole
  module on the merged tree).
