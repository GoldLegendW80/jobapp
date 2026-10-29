# Tests that cannot fail (BLIND)

Cue: a new test is the evidence for a claim of the pull request, or a journey asserts that
something did not happen.

Counts, corpus 2026-10-14 to 10-28: 8 rounds, 8 pull requests, 8 items, 1 in a later round.
Open branch: 2 (B17, J17). Rank 16. Backs review-checklist.md item 16.

## Edit rows

component: review-checklist.md item 9 ("A test must be able to fail": revert the fix and watch
the new test go red)
candidates: the checklist of 2026-10-14
change: revert the fix, see red
observed: 5 findings in 5 of 61 pull requests before, 8 items in 8 of 242 since; the misses were
tests green with one layer removed, tests that read a stand in of what the system writes, and
"nothing happened" assertions the fixture could never violate
verdict: confounded

## Entry BLIND-1

- **cue**: a test or a journey proves a claim, especially an absence (no POST, no log line, no
  duplicate request).
- **lesson**: remove each layer of the fix alone and see the test go red; assert on what the
  system really writes (the JSON log with its `extra` fields, every route class); an absence is
  proved only when the fixture tries the forbidden act (review-checklist.md item 16).
- **evidence**: #427 round 2 ("The tables stay green with some layers removed"); #426 ("I removed
  `tolower` from `masked` ... and all 27 checks stay green"); #477 (reverting the sweep's bonus
  path stays green); #677 ("`caplog.text` holds only the formatted message, never the `extra`
  fields"); #682 ("the journey cannot fail if a click on a POST form button went through"); #608
  (the test "no longer proves 'one request per feed'"); #515 (the credential is swapped before the
  start, so a wrong order passes); #298 (a test that "does not test what it claims"); B17 ("the
  journey's 'GET only' assertion cannot fail"); J17 ("the test of that case does not test what its
  name says").
- **confidence**: high: 8 pull requests and two open parts.
- **supports**: review-checklist.md item 16.
