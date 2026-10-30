# The checks that apply to every pull request

Read this at the self review, before the push (a reviewer: before the verdict). Items 3, 8 and 12
of [review-checklist.md](../review-checklist.md) apply to every pull request, so their checks are
here: one read at the end instead of lines in the text everyone reads first. Moved on 2026-10-29
from the checklist. The cases are in [lessons/merge-with-main.md](../lessons/merge-with-main.md),
[lessons/acceptance-delivered.md](../lessons/acceptance-delivered.md) and
[lessons/overclaiming-text.md](../lessons/overclaiming-text.md).

## Checklist item 3, the check

Merge `origin/main` last, then rerun the typecheck and the whole module on the merged tree.
(MERGE: 15 rounds, 14 PRs, 10 of them in later rounds.)

Check: `git merge-tree --write-tree origin/main HEAD` prints no CONFLICT, and a clean merge is not
a green one (#427, #551, #642). Line 1 `Depends on` names every open branch the head contains.

## Checklist item 8, the check

Deliver every acceptance line, named failure path and owner decision, with its evidence. (ACC: 11
rounds, 11 PRs, and 5 of the 13 items on the open branch of 2026-10-27.)

Check: each line, each failure path the plan or spec names and each decision posted on the issue or
the pull request is in the body with its test; a declared open point is for a heuristic's residue,
never an acceptance line; the spec's words and named functions verbatim; `Closes` only when all of
it is delivered, else `Refs`.

## Checklist item 12, the check

Every claim in text or in a recorded outcome has its test next to it. (CLAIM: 10 rounds, 10 PRs.)

Check: `git diff | grep -nEi "never|always|every|no .* can|within [0-9]+|cannot|guarantee"` in
comments, docstrings, docs, plan, the body and UI text: name the proving test or soften. A label or
an outcome ("Accept all", "searched", "read") counts only what happened. Then `grep -rn` the old
value of anything you changed.
