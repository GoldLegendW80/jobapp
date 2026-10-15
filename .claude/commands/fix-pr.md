---
description: Start a fresh fix agent on the blocking items of a pull request's latest review
argument-hint: <pr number>
---

Fix pull request $ARGUMENTS, as the orchestrator of `docs/agents/orchestration.md`.

1. Read the latest verdict: `gh api repos/GoldLegendW80/jobapp/pulls/<pr>/reviews --jq '.[-1].body'`.
   It must be `REVIEW VERDICT: REQUEST CHANGES`; otherwise say what it is and stop.
2. Extract the blocking items (and any "Fixups for the fix agent") as an exact list, one line each.
3. Spawn a FRESH `implementer` subagent (never resume the builder or an earlier fixer): Opus when
   an item is a heuristic, a race, security or privacy; Sonnet when every item is small. Use the
   "Fix agent" brief of `docs/agents/orchestration.md` with the list.
4. When it hands back with a new head, run `/review-pr <pr> recheck`.
