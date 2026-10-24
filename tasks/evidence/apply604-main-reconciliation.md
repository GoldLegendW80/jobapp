# Apply PR 604 main reconciliation

Base: `85fcd3871c4b453fc7831732d0b5ea068aeb7d09`. Pinned main: `c5443b93ad2f78d421b5bb61a78ffe0e9d1a7cb0`.
Worktree: `.claude/worktrees/codex-apply-main-reconcile`, branch `apply/563-main-reconcile`.
Merge commit: `a772447be10a3bc9c4b3733b9519fa8aa699a985`. Its first parent is the root base and second parent is pinned main; the integration branch fast-forwarded to this commit before its final gate.

The merge keeps both parents. The main checkout, original source branches and approved PR heads were not written. No push, public review or deployment was performed.

## Resolution rationale

- `agent.py`, both module-documentation conflicts: retain the concise current source-provenance contract, including metadata exclusion, opaque quotations, complete personal assertions and finite predicate equivalences. Add the upstream short-pronoun routing rule. Upstream's old prose claiming declared model provenance is unverified would contradict the implemented source checks. The working routing additions from main are retained.
- `answers.py`, fluent vocabulary conflict: keep `business fluent` and `proficient user`. These explicitly establish fluency. Keep Professional working as a distinct nonfluent band and Professional as its own source band. The new option-only lookup treats Professional as fluent when offered by the site. A person storing plain Professional still cannot choose Fluent, Advanced, Full professional or a CEFR grade; exact Professional remains allowed.
- `answers.py`, degree-option conflict: retain the discipline guard excluding options with a field not established by the stored degree, while applying main's local-degree restriction to Baccalauréat/Bac and Licence. They select their own named degree only, without guessing a country-dependent level.
- `test_a3_answers.py`: retain Professional working's distinct nonfluent parser result and test the new source/option namespace difference for Professional. Public resolver tables, rather than the parser's internal representation, prove refusal of unsupported fluency.
- `test_a5_agent_filters.py`: both sides already refuse the US team as practical; retain the explicit main comment. Supply a real personal source to accepted short-pronoun labels and compound-label judge controls. Separately test the same short labels with empty profile as unsupported claims. The no-company control now has a valid personal source, proving company routing rather than incidental grounding.
- The auto-merged `test_a3_answer_table.py` kept seven obsolete option expectations from the older branch. Restore the current-main expectations for these exact cases, retaining every row: a stored C1 may select the single Professional option; both Professional and Fluent offered together are ambiguous and left. Plain stored Professional refusal controls remain. Vocabulary uniqueness is tested independently for the source and option namespaces.

## Verification

- Focused A3/A5: **898 passed**, including explicit source and empty-profile controls.
- Full Apply: **7,243 passed in 298.88 seconds**, using the two-worker local Postgres/Redis wrapper.
- Ruff: all checks passed; **750 files** formatted.
- Pre-commit: passed, including secret scan, merge-marker checks and both Ruff hooks.
- Restored routing/source controls after the child mutation: **38 passed in 3.97 seconds**.
- Mutation: reintroducing the old unrestricted us employer anchor only in a child pytest process made all **11 compound-label cases fail** as expected. The staged files were not modified by the experiment.
- CI planner: **81 passed**.
- Journey registry: passed, 29 CI specs, 4 reasoned exclusions, 46 local-group-only specs.
- AST audit against the root base: no definitions removed or added. Four pronoun-routing functions and five language/degree functions changed; source-provenance, NFA, quotation, bindings and remaining guard definitions are unchanged.
- All source changes remain confined to the two Apply files; test changes to the three corresponding tables. No Jobs source auto-merge and no Jobs consumer of the changed resolver functions, so the whole Apply suite is the affected module check.

## Cleanup and remaining work

Test wrappers removed their own temporary Postgres/Redis containers and volumes. Final `docker ps -a` filtered to the agent-owned reconciliation prefix returned no containers. Worktree is clean and the pinned main ancestor check passed. No images built or retagged. The worktree is retained as audit evidence after integration. The final exact-head composed gate and whole-PR review are recorded on PR #604.
