# Plan, spec and decisions pull requests (PLAN)

Cue: a pull request that writes a spec, a module plan (`tasks/<module>/todo.md`) or rows of
`docs/decisions.md`.

Counts, corpus 2026-10-14 to 10-28: 6 rounds, 5 pull requests, 18 items (3.6 per pull request),
1 in a later round. Rank 17, but the densest family per pull request. Backs review-checklist.md
item 17.

## Edit rows

component: review-checklist.md item 7 (dependencies, one owner per shared file, one run per task)
candidates: the checklist of 2026-10-14
change: depends on, owns and touches per task; split past about 5 deliverables
observed: 16 findings in 3 plan pull requests before, 18 items in 5 since; the same shapes
(#189, #191) plus a spec whose security story is not said (#509) and a decision without the
owner's words (#508)
verdict: no observed effect

## Entry PLAN-1

- **cue**: a plan or spec pull request.
- **lesson**: per task, "depends on", "owns" and "touches", compared across tasks that run in
  parallel; migration numbers chained on main's head; contracts cited with the right task id and
  the real signature; a task past about 5 deliverables split; every decision row cites the
  owner's words; what a secret reaches and does not reach said per service (review-checklist.md
  item 17).
- **evidence**: #189 ("missing dependency, cyclic hook, wrong group"; "D4 is too big for one
  run"; a shared file of parallel tasks); #191 ("the documents contract is cited wrongly"; "the
  stated slice order breaks" the migration chain; "A18: clearly too big for one run"); #509 ("How
  `MAIL_SEND_KEY` reaches the api and `gmailmcp` and not the worker is not said"; "The M1b
  Playwright journey cannot be written"); #410 (a rehearsal move "cannot work as written"); #508 (a
  decision row whose "only source is the orchestrator note").
- **confidence**: medium to high: 5 plan or spec pull requests were blocked, and at least 2 (#435, #627) merged on a first APPROVE.
- **supports**: review-checklist.md item 17.
