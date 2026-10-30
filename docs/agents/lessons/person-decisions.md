# A person's decision does not survive the next run (DEC), and other logic (BIZ)

Cue: a rerun, a sweep or a merge meets a value the person rejected, removed, replaced or picked,
or a rule depends on time.

Counts, corpus 2026-10-14 to 10-28: DEC 3 rounds, 3 pull requests, 3 items; BIZ 2 single cases.
Rank 18. Backs review-checklist.md item 18.

## Edit rows

component: review-checklist.md item 3 ("State the invariant of the business rule as a property,
and test it with an injected clock")
candidates: the checklist of 2026-10-14
change: the invariant as a property; `now` and `as_of` passed in; tests at the boundary
observed: 10 findings in 8 of 61 pull requests before; since, 3 items on decisions that came back,
and the injected clock reappeared as the fix reviewers asked for in four other families (#315 at
the approval's time limit, #467 a run stuck at every tick, #683 pacing, #259 `as_of`)
verdict: confounded

## Entry DEC-1

- **cue**: a run proposes values again after the person decided on earlier ones.
- **lesson**: store the decision keyed by the value in every form it can come back in (the
  replaced value, the losing side of a conflict, a removed entry), and test the rerun: it comes
  back flagged or not at all (review-checklist.md item 18).
- **evidence**: #170 ("A conflict the user rejected comes back on the next run as a plain item");
  #178 ("A value the user replaced at accept, or the losing value of a conflict, comes back
  unflagged after a rerun"); #460 round 2 ("A board the person removed comes back at the next
  sweep").
- **confidence**: medium: 3 pull requests, one shape.
- **supports**: review-checklist.md item 18.

## Entry DEC-2

- **cue**: a rule has a deadline, a time limit, a pace or an open period.
- **lesson**: pass `now` and `as_of` in and test at the boundary: at the limit, one second after,
  after a Resume, an edit or a replace (review-checklist.md item 18).
- **evidence**: the fixes asked in #315 (one second before the time limit the click happens; at
  the limit and one second after, the application returns to `ready`), #467 ("with the injected
  clock"), #683 ("a fake clock asserting every page after the first waits at least 10 s") and #259
  round 3 ("Give `DocumentSources` the generation
  date as data, `as_of`").
- **confidence**: medium: four fixes, items classed in other families.
- **supports**: review-checklist.md item 18.

## Single cases (NO_ENTRY)

- BIZ, #285: `INSERT ... ON CONFLICT DO NOTHING` advances the sequence on every call, so "Every
  lease start uses up a uid" until the range runs out. One case.
- BIZ, #422: the tick changes a waiting generation's template without updating the recorded one,
  which a later check reads as a switch by hand. One case.
