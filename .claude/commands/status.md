---
description: Where the build stands: production, open pull requests and their verdicts, the board, what is next
---

Report where the jobapp build stands, briefly, as the orchestrator of `docs/agents/orchestration.md`.

1. Read `docs/STATUS.md` (the last written state and the next steps) and `docs/decisions.md` only
   if a decision matters for what is next.
2. Live state, each in one command with short output:
   - open pull requests with their latest verdict and whether it is on the current head:
     `gh pr list --state open --json number,title,headRefOid` and, per pull request,
     `gh api repos/GoldLegendW80/jobapp/pulls/<pr>/reviews --jq '.[-1] | .commit_id, (.body | split("\n")[0])'`;
   - production: `git fetch -q origin && git log --oneline -1 origin/main`, and the last
     `SHIP:` summary in `.local-check/ship/` when it exists on this machine;
   - the board: cards moved to Done since the last status, and open issues or pull requests
     missing from project 1 (`gh project item-list 1 --owner GoldLegendW80 --format json --limit 500`).
3. Answer in a few lines: what is live, what waits for review or for a ship, what the owner must
   do himself (from `docs/STATUS.md`, "The owner's pending action"), and the next two or three
   tasks. If `docs/STATUS.md` is out of date against the live state, say what changed.
