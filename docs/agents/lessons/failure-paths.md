# Guards on the happy path only (FAIL)

Cue: a state machine writes a state (`running`, `filling`, owned, queued, a stored wall) or a
guard runs at one point of a flow, and only the path where everything succeeds was tested.

Counts, corpus 2026-10-14 to 10-28: 14 rounds, 13 pull requests, 19 items, 2 in later rounds.
Rank 4. Backs review-checklist.md item 4.

## Edit rows

component: review-checklist.md item 2 (its shutdown sentence: "assert no job starts after the
stop began and that engine disposal waits for threads")
candidates: the checklist of 2026-10-14
change: shutdown ordering as the one named failure path
observed: no item of this family was about shutdown; they were states left behind by a cancel,
a timeout, a raise in `finish` or `stop`, a full queue, a retry, or the person's step in between
verdict: no observed effect

component: reviewer.md rule 1 ("probe the failure paths (concurrency, limits, bad input, a killed
worker, ownership between users)")
candidates: reviewer.md since 2026-10-14
change: the reviewer probes failure paths in one pass
observed: reviewers found all 19 items in first reviews but two; the builders' tests covered the
success path
verdict: helped at review, no observed effect at build

## Entry FAIL-1

- **cue**: a change writes a state that something else must later move, or places a check before
  an act.
- **lesson**: in review-checklist.md item 4, list every state the change writes and its exit on a
  cancel, a timeout, a dead worker, an exception in `finish` or `stop`, a full queue and the
  person's step in between, with one test per exit; a check that guards an act runs before the act
  on every path that reaches it.
- **evidence**: #312 ("An application is left `filling` for good when the job is cancelled, times
  out, the worker dies, or `finish` raises"); #303 ("stays `running` for good"); #621 ("Any
  exception or interruption inside `Browser.stop()` or `kill()`" leaves the lease owned); #467
  ("removes the only exit of a run whose own `advance` fails ... at every tick"); #274 ("A save,
  restore or template switch is lost when the render queue is full"); #516 (a `finish` that raises
  after `resume`; "the payment check runs only after the adapter and the answer agent typed, and
  never on a filled form"); #517 ("A confirmation the watch saw is thrown away when the session
  ends"); #661 ("the stored wall outlives a sign in done through the form agent's own tool");
  #285 round 2 ("One failed or slow `GET /leases/current` ends any lease older than 5 minutes");
  #428 (a Like ignores an automatic write that waits for consent).
- **confidence**: high: 10 pull requests, one shape.
- **supports**: review-checklist.md item 4 (new).

## Entry FAIL-2

- **cue**: a run classifies an outcome as retry, needs you or failed.
- **lesson**: classify by what the page and the adapter saw, not by the last HTTP status, and
  retry only what the spec lists. Module knowledge of apply: belongs in `tasks/apply/todo.md` when
  the next apply task is planned, not in the always on files.
- **evidence**: #516 round 1 ("the HTTP status overrides what the adapter saw on the page";
  `internal_error` retried against SPEC-apply's list), round 2 ("the run's HTTP status sticks to
  the refill after the person's step"); #310 ("A captcha or sign in wall is reported as
  `form_changed`"); #465 ("A Cancel or a failed swap during the hand off ends the fill as a
  failure").
- **confidence**: medium: 3 pull requests, all in apply.
- **supports**: review-checklist.md item 4, last clause.

## Single cases (NO_ENTRY)

- #395: a 422 that names a path with no field on the form gives a false message and no way on.
  One case.
