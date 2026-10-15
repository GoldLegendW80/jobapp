---
description: Start a fresh reviewer subagent on a pull request (first review on Opus, re-check on Sonnet)
argument-hint: <pr number> [recheck]
---

Review pull request $ARGUMENTS, as the orchestrator of `docs/agents/orchestration.md`.

1. Read its state: `gh pr view <pr> --json title,headRefName,headRefOid,body,files` and the latest
   verdict, `gh api repos/GoldLegendW80/jobapp/pulls/<pr>/reviews --jq '.[-1] | .commit_id, (.body | split("\n")[0:2])'`.
2. No verdict yet: a first review. Spawn the `reviewer` subagent on Opus with the "First review"
   brief of `docs/agents/orchestration.md` (the task section, the spec, files changed outside the
   owned ones, any gate change).
3. A REQUEST CHANGES verdict on an older head, or the argument `recheck`: a re-check. Spawn the
   `reviewer` subagent on Sonnet with the "Re-check" brief (previous blocking items and the diff
   since the reviewed head only).
4. A verdict already APPROVE on the current head: say so and do nothing.
5. When it hands back: on APPROVE the pull request waits for `/ship`; on REQUEST CHANGES run
   `/fix-pr <pr>`; file every "Follow ups" item as an issue with a card
   (`ops/card.sh <issue url> todo <module> later`).
