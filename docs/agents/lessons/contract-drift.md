# A new field or a changed contract misses a reader (DRIFT)

Cue: a schema gains a field, a function changes its return shape or a meaning, a mode is added, a
shared layer (browserd's tools, a gate module, a setting) changes, or a slice is carved out of a
larger branch.

Counts, corpus 2026-10-14 to 10-28: 11 rounds, 11 pull requests, 13 items, 1 in a later round.
Open branch: 1 (J16, data loss). Rank 9. Backs review-checklist.md item 9 and implementer.md,
"Tests".

## Edit rows

component: implementer.md, "Tests" ("`grep -rn` in `api/tests` and `web/e2e` for assertions of
any behaviour you changed")
candidates: implementer.md since 2026-10-14
change: fix the older tests that state the changed behaviour
observed: the misses were readers, not assertions: old editors and imports that omit the new key
(J16), registries (`FIELD_TYPES`, #699; `ENV_NAMES`, #259, derived since #385), another
module's consumer (#477, #660, #680) and tests of other modules (#459 round 3, "breaks 8 tests that the PR does not
update")
verdict: no observed effect

component: the registry tests of ingestion (`test_every_path_of_every_section_has_a_type`) and
of the agents' fake streams (`test_every_recorded_stream_parses`)
candidates: the test suite
change: a field or a stream without its entry fails a test
observed: they caught #699 and #470 round 2, once the reviewer ran the other module's folder
verdict: helped (when run)

## Entry DRIFT-1

- **cue**: a stored section gains a field.
- **lesson**: a save, an import or a history restore that omits the key keeps the stored value,
  tested with an old payload; the field joins every registry that lists fields (`FIELD_TYPES`,
  the schemas' kept when omitted set) and every journey that asserts
  the stored shape (review-checklist.md item 9).
- **evidence**: J16 ("Any editor or import that does not know `location_exclude` silently empties
  the person's Unwanted places"); #699 ("`search.browser_sources[].interactive_only` has no entry
  in FIELD_TYPES, so tests/ingestion/test_items.py fails twice"; an older journey "expects browser
  sources without `interactive_only`"), and #700, which contains it; #459 round 3 ("The schema
  change breaks 8 tests that the PR does not update"); #259 ("The `DOCUMENTS_*` settings leak into
  every other test"); #189 (a plan that adds a section "breaks ingestion's field table").
- **confidence**: medium to high: 5 pull requests and one open part, one of them data loss.
- **supports**: review-checklist.md item 9; implementer.md, "Tests" (readers and registries).

## Entry DRIFT-2

- **cue**: a change in a layer that other modules call, or a slice carved from a larger branch.
- **lesson**: list the callers of what changed (`grep -rn` the name across `api/app`,
  `browser/`, `web/src`), run their test folders, and make a slice stand alone: what it removes or
  narrows must not break a sibling that is already on main.
- **evidence**: #660 ("any button ref the loop cannot read refuses the whole `browser_snapshot`,
  on every lease and for every adapter, not only the generic one"); #680 ("the slice does not
  stand alone, it breaks Apply myself's 'Attach my CV'"); #316 (rows that A6 refuses to edit "are
  given an input that can never save"; a kinds mismatch with the gate); #301 ("D8 breaks D9's
  pinned worked figures once main is merged"); #477 (`override_candidates` "compares the stored fit
  with the engine score without the bonus"); #415 (a chat on an uploaded base CV is checked
  without the uploaded text); #467 (the reference column is not what the code assumes).
- **confidence**: high: 7 pull requests.
- **supports**: review-checklist.md item 9.
