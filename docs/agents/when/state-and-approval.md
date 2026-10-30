# State, locks, approvals and the person's decisions

Read this when a trigger line sent you here: your diff adds or changes a state machine (a status
that is written, a job that runs, a draft), a guard on an act, a lock or a read then write path, a
retry, a click, consent or submission, or a value the person rejected, removed, replaced or
picked. A reviewer reads it for the same diffs. Moved on 2026-10-29 from checklist items 4, 11, 13
and 18 of [review-checklist.md](../review-checklist.md). The cases are in
[lessons/failure-paths.md](../lessons/failure-paths.md),
[lessons/races-and-locks.md](../lessons/races-and-locks.md),
[lessons/approval-gate.md](../lessons/approval-gate.md) and
[lessons/person-decisions.md](../lessons/person-decisions.md). Hard rule 4 of CLAUDE.md (nothing
is submitted without the person's approval) is the rule behind item 13.

## Checklist item 4, the check

Walk every unhappy path to the state it leaves. (FAIL: 14 rounds, 13 PRs.)

Check: for each state the change writes, its exit on a cancel, a timeout, a dead worker, a raise
in `finish` or `stop`, a full queue, a retry and the person's step in between, one test each;
nothing stays `running`, `filling` or owned for good; a check that guards an act runs before it on
every path; a failure is classed by what the page showed.

## Checklist item 11, the check

Shared state: lock, read again under the lock, compare with what the person saw, then write.
(RACE: 10 rounds, 10 PRs.)

Check: `git diff | grep -nE "FOR UPDATE|advisory|lock_|create_task|to_thread|refresh"`; a two
session test per read then write path (a click and the tick, two workers); one lock order; a fix
that moves a read reopens it.

## Checklist item 13, the check

Bind every click, consent and submission to what the person saw and approved. (APPR: 9 rounds, 8
PRs.)

Check: the approval carries what the page showed (answers, text version, figures) and the act
checks it again at the act and after its time limit, 409 on a mismatch; one Like gives one click;
a check before submit fails closed on what it did not see; a destructive button asks.

## Checklist item 18, the check

A person's decision survives every later run; time rules take an injected clock. (DEC: 3 rounds,
3 PRs.)

Check: a rejected, removed, replaced or picked value comes back flagged or not at all after a
rerun; pass `now` and `as_of` in; test at the limit, one second after, after a Resume, an edit or
a replace.
