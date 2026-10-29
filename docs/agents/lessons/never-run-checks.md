# Journeys and tests never run on the head (RUN)

Cue: the diff changes a page, a journey, a stack, a fake stream or a Docker image, and the
builder could not, or did not, run the group's journeys or the module's whole suite on the head.

Counts, corpus 2026-10-14 to 10-28: 8 rounds, 8 pull requests, 10 items, 3 in later rounds.
Rank 14. Backs review-checklist.md item 14 and when/journeys.md (formerly implementer.md, "Tests").

## Edit rows

component: implementer.md, "Tests", now when/journeys.md ("A journey that has never run hides most
of its bugs: run every new journey on its own stack before you push"; "Run ALL journeys of your
module's group") and orchestration.md, Lessons learned (the same lesson, now in when/journeys.md)
candidates: implementer.md and orchestration.md since 2026-10-14
change: run every journey of the group on an own stack before the push
observed: journeys that "cannot pass as written" reached review (#309, #316, #472), journeys that
failed on the head every time (#691, #692), a recorded stream and vitest rows red on the head
(#470 round 2, #598 round 3); several builders were stopped by the gate's lock and the reviewer
too ("Tests not run: `.local-check/lock` was present for the whole review", #505; #307)
verdict: no observed effect

component: the review-checklist.md of 2026-10-14
candidates: the checklist of 2026-10-14
change: none (no item named running journeys)
observed: reviewers found these by running the journeys themselves
verdict: no observed effect

## Entry RUN-1

- **cue**: a pull request that touches a page, a journey or what a journey's stack runs.
- **lesson**: run every journey of the group and the module's whole suite on the head, on an own
  stack, before review; when the gate's lock, the disk or the sandbox stops a run, write "not run"
  and the reason in the body, so that the reviewer or the orchestrator runs it before the approval
  (review-checklist.md item 14; when/journeys.md).
- **evidence**: #309 (the last journey step "cannot pass as written"); #316 ("The journey cannot
  pass with two or more left fields"); #472 ("the journey cannot pass as written"); #691 ("fails on
  this head, every time"); #692 (a journey fails on this head, another inherited); #598 round 3
  (vitest rows "fail on the head and on the merge, 3 of 3 runs"); #470 round 2
  (`test_every_recorded_stream_parses[apply_check_down]` fails on this head); #307 (Docker marked
  tests never ran against the narrowed profile); the journey notes of 2026-10-27 on the open
  branch (j15 expected one website's progress line that the new order of websites made late,
  found only on a stack).
- **confidence**: high: 8 pull requests.
- **supports**: review-checklist.md item 14 (new); when/journeys.md ("not run" declared).
