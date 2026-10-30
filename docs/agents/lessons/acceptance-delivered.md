# Acceptance lines, owner decisions and named paths not delivered (ACC)

Cue: the pull request closes an issue whose plan section lists acceptance lines, edge cases or
failure paths, or the owner posted a decision on the issue or the pull request.

Counts, corpus 2026-10-14 to 10-28: 11 rounds, 11 pull requests, 14 items, none in later rounds.
Open branch: 5 of its 13 items (#715, B17, B19, J11, J17), the largest family there. Rank 8.
Backs review-checklist.md item 8.

## Edit rows

component: review-checklist.md item 14 ("Deliver every line of the acceptance list, with
evidence")
candidates: the checklist of 2026-10-14
change: copy each acceptance line into the body with its test or screenshot
observed: still 11 pull requests, and the open branch's first reviews blocked on it five times;
the misses were lines whose test was absent (a failure path, hostile text, a verification the
issue requires), owner decisions posted after the plan, and `Closes` on a partial delivery
verdict: no observed effect

component: implementer.md, "Heuristic code", now when/heuristic-code.md ("List what it still
misses under 'Declared open points'")
candidates: implementer.md since 2026-10-14
change: named residue is not re-blocked
observed: builders also declared acceptance lines open, and reviewers blocked them: #453 (a plan
edge case "has no test, and the pull request body declares it open"), J11 (the issue's
verification "listed under Declared open points")
verdict: hurt, in 2 cases (read as covering acceptance lines too)

## Entry ACC-1

- **cue**: an issue with acceptance lines, edge cases or failure paths in its plan section.
- **lesson**: in review-checklist.md item 8, copy every acceptance line, every failure path the
  plan or spec names and every owner decision posted on the issue or the pull request into the
  body, each with the test that shows it; a declared open point covers a heuristic's residue,
  never an acceptance line; `Closes` only when all of it is delivered, else `Refs`.
- **evidence**: #169 ("the acceptance added for exactly that, with its test, is not delivered");
  #307 ("The acceptance this PR closes is not all delivered (`Closes #230`)"); #640 (acceptance
  (3) names "hostile text" and no test feeds it); #681 (the credential split, the send handler's
  failure paths and the deploy order the spec names have no test); #682 ("Merging closes the issue
  and loses those lines"); #453; #480 (the plan's journey is not the one written); B17 (no test
  drives the ended login path the plan names); B19 ("The three api changes that feed
  `interactive.outcome` are untested"); J11.
- **confidence**: high: 7 merged pull requests and three parts of the open branch.
- **supports**: review-checklist.md item 8; when/heuristic-code.md (open points are for
  residue only).

## Entry ACC-2

- **cue**: the spec states a word, a sentence, a cap or a matcher by name.
- **lesson**: use the spec's words and named functions verbatim, and a cap the spec sets changes
  only with a reviewed spec change.
- **evidence**: #451 ("the contract words differ from the spec"; the empty deck text); #455 ("the
  deck does not decide as `apply.guardrails.never_apply`, which the spec names"); #429 (the owner's
  decision of 2026-10-20 posted on the pull request); J17 ("The owner decided on 2026-10-27 that
  this sentence is required"); #715 (per source caps: "Any changed cap needs evidence and a
  reviewed spec change"); #438 (pacing per host "across people", the spec's line).
- **confidence**: medium to high: 4 merged pull requests and two open parts.
- **supports**: review-checklist.md item 8.
