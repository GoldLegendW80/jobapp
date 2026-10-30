# The gate and the ops tools (OPS)

Cue: a change of `ops/ship.sh`, `ops/local-check.sh`, `ops/agent-test.sh`, `ops/board-sync.py`,
`ops/dev-setup.sh` or `.github/scripts/ci_plan.py`: tools that decide what runs, merges or ships.

Counts, corpus 2026-10-14 to 10-28: 13 rounds, 9 pull requests, 30 items, 5 in later rounds.
Rank 5. Backs review-checklist.md item 5.

## Edit rows

component: review-checklist.md item 8 ("test `set -e` in the way the function is called, and
avoid `grep -q` in a pipe under pipefail"; path variants, `sync` before delete)
candidates: the checklist of 2026-10-14
change: the shell traps found in the corpus of 2026-10-14
observed: none of the 30 items was about `set -e`, `pipefail`, symlinks or `fsync` (one fix
suggestion used a here string to avoid `grep -q` in a pipe, #334). They were unbounded external
calls (#501), a scope mapper that narrows on what it cannot read (#554, 10 items over 3 rounds;
#676), teardown and locks after a kill (#292, #434), ship logic (#284, 8 items) and claims of read
only (#556)
verdict: no observed effect (the item names traps that no longer occur; the bash 3.2 notes stay
in orchestration.md, Lessons learned)

component: reviewer.md rule 7 (a gate change is read as such)
candidates: reviewer.md since 2026-10-14
change: the reviewer reads a gate change against CONTRIBUTING.md step 5
observed: every item was found at review; #554 alone needed 3 REQUEST CHANGES rounds
verdict: helped at review

## Entry OPS-1

- **cue**: a tool decides the scope of the gate (which groups, which journeys) from what it can
  read of the tree.
- **lesson**: anything the mapper cannot read or resolve widens the run (dynamic or reflective
  imports, side effect imports, deletions, read or parse errors, routes called over HTTP,
  `api/tests` files mounted by stacks); test each with a synthetic tree (review-checklist.md
  item 5).
- **evidence**: #554 round 1 ("Python dependencies are silently missed", "Deletions lose
  consumers from the graph", "Filesystem and parse errors narrow scope", `api/tests/*` mapped to
  no group "fails open"), round 3 (loader uses, "TypeScript line comments swallow valid imports
  after Unicode line terminators"), round 4 ("reflective namespace access still bypasses the
  Python loader boundary"); #676 ("false narrowing ... the web pages of other groups call these
  modules' routes over HTTP").
- **confidence**: high for the gate: 2 pull requests, 4 rounds, one shape.
- **supports**: review-checklist.md item 5.

## Entry OPS-2

- **cue**: a script calls `gh`, `git`, `docker` or a network service while it holds a lock, or it
  can be killed midway.
- **lesson**: bound every external call with a timeout and test the hung and the killed run: the
  lock, the stack, the network slot and the cards end in a state the next run accepts; a watchdog
  test needs a real minimum, not a whole second counter (review-checklist.md item 5).
- **evidence**: #501 round 1 ("nothing bounds a sync, so a hung GitHub can stall the ship and hold
  the lock"), round 2 ("the three new hung-sync tests are flaky", bash `$SECONDS` is a whole
  second counter); #292 ("A gate that dies without its trap leaves every following gate broken on
  journeys"; "a journey stack is never taken down by its step, yet its network slot is freed");
  #434 (the run again reuses the first run's compose project); #284 (8 items of ship logic: order
  by `Depends on`, a flake guard before bisect, cards moved right after the merge).
- **confidence**: medium to high: 4 pull requests.
- **supports**: review-checklist.md item 5.

## Single cases (NO_ENTRY)

- #556: a read only setup probe can still install Node and change the user's pnpm home. One case.
- #574: a sync archives a source of facts its own plan still reads. One case.
- The old item 8 text, kept for the day it fires again: call each step as the lanes do
  (`step || true`, `if step`) with a stub whose first command fails; fake commands that print
  hundreds of lines after the match; path variants `dir`, `dir/`, `dir/.` and a symlinked dir;
  `sync` the new file and its folder before deleting anything; a partial run must not print the
  last line of a full pass.
