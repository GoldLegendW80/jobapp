# A cache or a race in the UI (UI)

Cue: a page polls, caches a query, keeps a draft, shows a toast with an action, or acts on the
first card of a list.

Counts, corpus 2026-10-14 to 10-28: 8 rounds, 8 pull requests, 9 items, none in later rounds.
Open branch: 1 (B18). Rank 15. Backs review-checklist.md item 15.

## Edit rows

component: review-checklist.md item 2, last sentence ("For UI state, reset flags wherever the
draft is replaced")
candidates: the checklist of 2026-10-14
change: reset UI flags where the draft is replaced
observed: the items were about which read or which item the page acts on, more than flags
verdict: no observed effect

## Entry UI-1

- **cue**: a page shows or acts on server state that changes while it is open.
- **lesson**: what the page shows is the last successful read of this mount (a failed read never
  counts as fresh), and each poll, toast or swipe acts on its own item, not on the latest one
  (review-checklist.md item 15).
- **evidence**: #308 ("the view stays on the old state when the generation finishes between two
  polls"); #472 ("a Like can be sent for a passed card"; "the Undo of an older toast undoes a newer
  swipe"); #598 (paced applications "still poll every 3 seconds"); #661 ("no banner in the queue
  while a watched hand off waits"); B18 ("`query.isFetchedAfterMount` is true after a read that
  *failed*").
- **confidence**: medium to high: 4 merged pull requests and one open part.
- **supports**: review-checklist.md item 15 (new).

## Entry UI-2

- **cue**: a form keeps a draft beside the saved value.
- **lesson**: a summary reads what was saved, not the draft; unsaved typing survives a refetch and
  a list change; one guard per navigation.
- **evidence**: #480 ("the summary is built from the in-memory `draft`, not from what was saved");
  #553 ("unsaved typing is thrown away"); #309 ("switching from a tab with unsaved changes to the
  CV tab asks twice"); #178 ("Picking another value of a conflict while the edit form is open
  writes the value that was not picked").
- **confidence**: medium: 4 pull requests.
- **supports**: review-checklist.md item 15.
