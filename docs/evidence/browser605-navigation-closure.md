# Browser navigation failure classification, issue 605

Implementation base: `ca8ded900a09bf9d38ed60319d612d5fec7febc1`.

`actions.run` classifies a raised public Playwright `Error` during `browser_navigate`, including
page acquisition, focus, title and the final settle, as the existing `navigation_failed` result.
The existing `goto` timeout result remains `navigation_timeout`. Other operations, Python
exceptions, returned failure results, cancellation, URL policy and person input keep their
existing handling. Production code imports no private Playwright exception or memory cause
tracker.

The operation boundary is necessary because `pages.navigate` previously caught only failures
from `goto`. The retained native evidence cannot identify which awaited boundary threw the
original `TargetClosedError`.

Tests use the existing fake page/process boundaries and the real memory guard. Closing the
original target at focus, goto or title retains the exact internal blank, the unchanged memory
event and the live browser while returning `navigation_failed`. The targeted run loads the
actual `TargetClosedError` from isolated Playwright 1.55.0, the browser image's pinned version.
API-only environments use distinct fake Playwright exception types, without adding a project
dependency or skipping these tests.

RED on the base with the actual pinned package: 19 failed, 68 passed. The failures cover the
previously unhandled typed boundaries and guard closures at focus/title; goto already handled
its error and timeout cases. After the change, the complete non-Docker browser suite and
affected Apply retry, cancellation, approval and profile safety files passed: 3401 tests.
Apply regressions preserve the existing waits of 600 then 3600 seconds and the final failure,
leave arbitrary `page_error` permanent, prevent requeue after cancellation, require approval
again after a pre-arm navigation failure, preserve post-click uncertainty without a second
click, and retain `save_unknown` after an explicit profile save.

Commands use `ops/agent-test.sh`, `-n 2`, throwaway Postgres/Redis and an isolated copy of the
matching API environment. The pinned Playwright package is supplied only through a temporary
test path. Source imports were checked against this worktree, including `app.apply.run` hash
`6b04d1d304913c9b1f558bc9efb38f386d528140d7e0aeff048f3d0dddf96157`.
Ruff check and format passed for API app/tests and the changed browser source. CI plan tests
passed, 81 tests; e2e group validation passed.
The normal API environment, without the temporary Playwright path, also passed the complete
non-Docker browser suite: 2253 tests.
Required pre-commit hooks passed on the changed files, including Ruff and the secret scan.

Self review covered the typed error boundary, unchanged refusal scope, cancellation, delayed
retry limits, approval and one-click handling, profile-save uncertainty, and invented fixtures.
The change does not adjust the memory formula, threshold, cap, blank lifecycle, budgets or
retry settings. No image build or native UX run was performed for this change. Issue 605's
capacity failure and the original five-case, 16-layout native acceptance remain unresolved.
