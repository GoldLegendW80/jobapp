# Web pages: state, polls, toasts and drafts

Read this when a trigger line sent you here: your diff touches `web/src` where a page reads data
(a query, a poll, a refetch), shows a toast or a summary, keeps a draft or a form, or acts on one
item of a list. A reviewer reads it for the same diffs. Moved on 2026-10-29 from checklist item 15
of [review-checklist.md](../review-checklist.md). The cases are in
[lessons/ui-state.md](../lessons/ui-state.md). The web test commands (typecheck, which covers
`web/e2e`, vitest, lint) stay in implementer.md, Tests; a journey needs
[journeys.md](journeys.md).

## Checklist item 15, the check

UI state: show the last successful read and the saved value, and act on the item shown. (UI: 8
rounds, 8 PRs.)

Check: a failed read never counts as fresh (`isFetchedAfterMount` is true after an error); a poll,
a toast or a swipe acts on its own item; a summary reads what was saved; unsaved typing survives a
refetch; reset flags wherever the draft is replaced.
