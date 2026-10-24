# Apply PR 604 current main integration, 2026-10-23

Apply parent: `57c17cc030cb4421224f5cce0a03d282cab5bac1`, tree `61a4162bfaa5b1b1d1632771fafaeb46077b736d`.
Fetched main: `1244322c2c2d6a84c4678a3d97ea25e5fbf02531`.
Worktree: `.claude/worktrees/codex-apply604-main-sync`, branch `apply/604-main-sync`.
This is a local merge of main into the isolated Apply source, with both parents retained.

## Conflict resolutions

- `api/tests/ingestion/test_engine_worker.py`: retain Apply's explicit 900-second work budgets, 1800-second sign-in wait, 900-second takeover wait, 3600-second aggregate agent transport allowance, two slots, 3720-second job timeout and 3900-second stale grace. Retain main's explanation and generic assertions that the longest registered agent determines both queue margins. The separate longer-agent regression retains its assertions about recomputation and unchanged ingestion budgets.
- `web/src/test/server.ts`: retain Apply's browser-free fixture and add main's O8 feed catalogue handler, including its imported catalogue. Both fixture consumers remain represented.

The worker startup diagnostic auto-merge retains main's exception class without exception text. O8 source summaries, freelance cards, Search form persistence, source attribution and O7 per-address request counting are carried from main. No Apply source or Apply test file changes relative to the Apply parent, so its stricter guards, provenance checks and acceptance/refusal tables are retained byte for byte. Application edits remain local; reusable Saved answers remain separate.

## Verification at the source checkpoint

- Focused backend via `caffeinate -i ops/agent-test.sh --name apply604-main-sync`, `-n 2 -m 'not docker'`: **20 passed in 11.40 seconds**. Files: ingestion `test_engine_worker.py`; Jobs startup Redis diagnostic test with both exception classes; O8 `test_o8_summary.py`; the two O7 request-count tests changed by main.
- Ruff check: passed. Ruff format check: **785 files already formatted**.
- CI planner: **81 passed**. Journey registry: **29 CI specs, 4 reasoned exclusions, 53 local-group specs**.
- Staged whitespace check and unresolved-path check: passed.
- Web typecheck and focused fixture consumers are reported in the implementer's handback after this checkpoint. The final whole gate belongs to the orchestrator; the previously reported gate blockers are outside this merge's scope.

## Exact incoming paths relative to the Apply parent

```text
SPEC-opportunities.md
api/app/jobs/schemas.py
api/app/jobs/sweep_router.py
api/app/worker.py
api/tests/ingestion/test_engine_worker.py
api/tests/jobs/test_worker.py
api/tests/opportunities/test_o7_feeds.py
api/tests/opportunities/test_o8_summary.py
tasks/evidence/opportunities-o8/mission-in-discover-desktop-dark.png
tasks/evidence/opportunities-o8/mission-in-discover-desktop-light.png
tasks/evidence/opportunities-o8/mission-in-discover-mobile-dark.png
tasks/evidence/opportunities-o8/mission-in-discover-mobile-light.png
tasks/evidence/opportunities-o8/opt-in-platforms-desktop-dark.png
tasks/evidence/opportunities-o8/opt-in-platforms-desktop-light.png
tasks/evidence/opportunities-o8/opt-in-platforms-mobile-dark.png
tasks/evidence/opportunities-o8/opt-in-platforms-mobile-light.png
tasks/evidence/opportunities-o8/posting-via-feed-desktop-dark.png
tasks/evidence/opportunities-o8/posting-via-feed-desktop-light.png
tasks/evidence/opportunities-o8/posting-via-feed-mobile-dark.png
tasks/evidence/opportunities-o8/posting-via-feed-mobile-light.png
tasks/evidence/opportunities-o8/sources-card-desktop-dark.png
tasks/evidence/opportunities-o8/sources-card-desktop-light.png
tasks/evidence/opportunities-o8/sources-card-mobile-dark.png
tasks/evidence/opportunities-o8/sources-card-mobile-light.png
tasks/evidence/opportunities-o8/sweep-summary-desktop-dark.png
tasks/evidence/opportunities-o8/sweep-summary-desktop-light.png
tasks/evidence/opportunities-o8/sweep-summary-mobile-dark.png
tasks/evidence/opportunities-o8/sweep-summary-mobile-light.png
tasks/opportunities/todo.md
web/e2e/opportunities-o8.spec.ts
web/src/features/jobs/find-jobs.test.tsx
web/src/features/jobs/find-jobs.tsx
web/src/features/jobs/job-detail-page.test.tsx
web/src/features/jobs/job-detail-page.tsx
web/src/features/jobs/jobs-actions.tsx
web/src/features/jobs/labels.ts
web/src/features/jobs/sweep-sources.tsx
web/src/features/jobs/sweeps-api.ts
web/src/features/opportunities/sources/feeds-api.ts
web/src/features/opportunities/sources/freelance-sources-card.test.tsx
web/src/features/opportunities/sources/freelance-sources-card.tsx
web/src/features/opportunities/sources/source-words.test.ts
web/src/features/opportunities/sources/source-words.ts
web/src/features/opportunities/sources/sweep-feeds.tsx
web/src/features/profile/api.ts
web/src/features/profile/editor-forms.ts
web/src/features/profile/search-tab.tsx
web/src/lib/wording.ts
web/src/test/feeds.ts
web/src/test/server.ts
```

This evidence file is the only additional path. The prior reconciliation report is retained. No held A18 UI or Apply journey-fixture path is changed by this incoming delta. Test services were disposable wrapper-owned Postgres/Redis containers; no image build, external account, real job site, public review, push or deployment was used.
