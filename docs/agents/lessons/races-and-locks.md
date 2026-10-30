# Races and locks on the server (RACE)

Cue: two writers can meet on one row or one resource (a click and the tick, two workers, a
render and an edit, a stop and a start), or a decision is read before the write it governs.

Counts, corpus 2026-10-14 to 10-28: 10 rounds, 10 pull requests, 10 items, 1 in a later round.
Rank 11. Backs review-checklist.md item 11.

## Edit rows

component: review-checklist.md item 2 ("lock, re-read under the lock, compare with what the user
saw, then write")
candidates: the checklist of 2026-10-14
change: grep for locks and threads, a two session test per read then write path
observed: 11 of 61 pull requests (18%) blocked on it before; 10 of 242 (4%) since
verdict: confounded (helped is plausible; the tasks also moved from documents and leases to
browser and apply flows)

## Entry RACE-1

- **cue**: a read decides a write and another actor can write in between.
- **lesson**: lock, read again under the lock, compare with what the person saw, then write; one
  lock order across paths; a two session test per path (review-checklist.md item 11). A fix
  round that moves a read can reopen it.
- **evidence**: #303 ("A click and the tick can both start the same document: two runs, two
  spends"); #266 ("A lock cycle between `delete_base` and `create_generation`, reproduced"); #276
  ("A hand edit made while the writer's overflowing version renders is overwritten by the
  shortening pass"); #274 (a template switch during a render "is never rendered"); #297 ("The cap
  of 3 documents per lease is checked outside the lease's lock"); #172 ("a job is started after the
  stop began"); #438 (preemption race while browserd ends the holder); #428 round 2 ("a stale read,
  opened by this round's change").
- **confidence**: high: 8 pull requests.
- **supports**: review-checklist.md item 11.

## Single cases (NO_ENTRY)

- #618: `db.refresh(generation)` "discards unflushed changes (the sessionmaker has
  `autoflush=False`)". One case; kept as a grep hint in review-checklist.md item 11.
- #687: blocking database work and advisory lock waits on the api's event loop while another
  coroutine holds the send lock. One case here (CPU work on the loop is HOST, #505).
