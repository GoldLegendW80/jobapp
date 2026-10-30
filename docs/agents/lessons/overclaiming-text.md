# Text that claims more than the code does (CLAIM)

Cue: a docstring, a doc, a plan line, the pull request body, a button label or a recorded outcome
states a guarantee ("every", "never", "forbidden", "reported", "searched", "read").

Counts, corpus 2026-10-14 to 10-28: 10 rounds, 10 pull requests, 10 items, 2 in later rounds.
Rank 12. Backs review-checklist.md item 12.

## Edit rows

component: review-checklist.md item 5 ("Every sentence that says 'always', 'never', 'every' ...
needs the test or measurement next to it")
candidates: the checklist of 2026-10-14
change: grep the claim words in the diff, name the proving test or soften
observed: 13 findings in 6 of 61 pull requests before, 10 items in 10 of 242 since; the item
names text only, and two of the ten were recorded outcomes (#541, #720)
verdict: confounded (helped is plausible)

## Entry CLAIM-1

- **cue**: a diff adds or keeps a sentence that states what the code always or never does.
- **lesson**: name the proving test next to the claim in the body, or soften the sentence; grep
  the repository for the old value of anything changed (review-checklist.md item 12).
- **evidence**: #681 (the setup doc "says 'The signing key is forbidden in worker settings'. No
  such check exists"); #679 ("the claim is false and the test cannot see it"); #310 ("The module
  docstring says 'Anything else ... is reported `unsupported_widget`', but three shapes vanish");
  #298 (the plan "says '`tests/documents/test_estimate.py` pins every figure below' ... but no test
  pins the worked figures"); #677 (the coverage claim "holds for `APIRoute` only"); #427 round 2
  (the acceptance line "still says 'nothing that hides a field'"); #460 round 2 ("The PR body is
  B10's"); #178 ("decide only the proposals shown, and the page does not say so at the button").
- **confidence**: high: 8 pull requests.
- **supports**: review-checklist.md item 12.

## Entry CLAIM-2

- **cue**: code records an outcome or a status that the person reads (searched, read, accepted).
- **lesson**: record the outcome from what the session observed, never from the attempt: a search
  counts when a search ran, a text counts as read when every part was read.
- **evidence**: #541 ("reused steps can leave part of a text unread, and the run still says it
  was read"); #720 ("any `browser_click` after a typed title records a search, so
  `report_no_postings("no_match")` becomes a success after a search that never ran"); the open
  branch's J16 and B19 parts, whose tasks were truthful outcomes (B19: "acceptance 6, the truthful
  outcome").
- **confidence**: medium: 2 merged pull requests and two open parts.
- **supports**: review-checklist.md item 12 ("An outcome counts only what happened").
