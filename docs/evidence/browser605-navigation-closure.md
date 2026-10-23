# Browser navigation failure classification, issue 605

Implementation base: `ca8ded900a09bf9d38ed60319d612d5fec7febc1`.

`actions.run` classifies a raised public Playwright `Error` during ordinary
`browser_navigate`, including page acquisition, focus, title and a successful settle, as the
existing `navigation_failed` result. The existing `goto` timeout result remains
`navigation_timeout`. Allow-list repair failures retain their original exception object and
reach the route's existing `page_error` handling; successful repair retains `host_not_allowed`.
An internal exception boundary distinguishes repair from ordinary navigation, including a
redirect detected by `Tab.settle`. Other operations, Python exceptions, returned failure
results, cancellation and person input keep their existing handling. Production code imports
no private Playwright exception or memory cause tracker.

The operation boundary is necessary because `pages.navigate` previously caught only failures
from `goto`. The retained native evidence cannot identify which awaited boundary threw the
original `TargetClosedError`.

Tests use the existing fake page/process boundaries and the real memory guard. Closing the
original target at focus, goto or title retains the exact internal blank, the unchanged memory
event and the live browser while returning `navigation_failed`. The targeted run loads the
actual `TargetClosedError` from isolated Playwright 1.55.0, the browser image's pinned version.
API-only environments use distinct fake Playwright exception types, without adding a project
dependency or skipping these tests.

Initial RED on the base with the actual pinned package: 19 failed, 68 passed. The failures cover the
previously unhandled typed boundaries and guard closures at focus/title; goto already handled
its error and timeout cases. After the change, the complete non-Docker browser suite and
affected Apply retry, cancellation, approval and profile safety files passed: 3401 tests.
Apply regressions preserve the existing waits of 600 then 3600 seconds and the final failure,
leave arbitrary `page_error` permanent, prevent requeue after cancellation, require approval
again after a pre-arm navigation failure, preserve post-click uncertainty without a second
click, and retain `save_unknown` after an explicit profile save. The post-arm test injects a
failed snapshot result. It is a generic state-safety test, not evidence of a new post-click
navigation path: this change classifies only `browser_navigate`, and a real snapshot closure
still returns `page_error`. Apply performs no ordinary navigation after the submit click.

The first review found that the initial catch also normalized errors raised during allow-list
repair. Fix-round RED on reviewed head `bc58313b8bbdc8ec1f47175236fbbf8480578495` reproduced
18 failures, with 6 cases passing. The regressions use actual `Tab.page`, `navigate`, `settle`
and the HTTP tool route for an existing off-list page, closing an off-list secondary tab, and
a successful goto that redirects off-list before settle. They cover public `Error`, public
`TimeoutError`, concrete pinned `TargetClosedError`, successful refusal, and original exception
identity. Three additional cancellation identity cases pass on the fix.

Fix-round GREEN with pinned Playwright 1.55.0 passed 3428 tests in the complete non-Docker
browser suite and the same affected Apply files. The adapted source-only reviewer probe also
passes all nine repair comparisons, with the original exception types, `page_error`, and
identical transport traces. That probe extracts the original `Tab` and `run` together from the
implementation base; the route regression tests separately exercise the actual HTTP handler.

Commands use `ops/agent-test.sh`, `-n 2`, throwaway Postgres/Redis and an isolated copy of the
matching API environment. The pinned Playwright package is supplied only through a temporary
test path. Source imports were checked against this worktree, including `app.apply.run` hash
`6b04d1d304913c9b1f558bc9efb38f386d528140d7e0aeff048f3d0dddf96157`.
Ruff check and format passed for API app/tests and the changed browser source. CI plan tests
passed, 81 tests; e2e group validation passed.
The initial normal API environment run, without the temporary Playwright path, passed the
complete non-Docker browser suite: 2253 tests. The fix-round run passed 2280 tests in the same
API-only environment, including the new repair regression cases.
Required pre-commit hooks passed on the changed files, including Ruff and the secret scan.

Self review covered the typed error boundary, refusal repair exception identity, cancellation, delayed
retry limits, approval and one-click handling, profile-save uncertainty, and invented fixtures.
The change does not adjust the memory formula, threshold, cap, blank lifecycle, budgets or
retry settings. No image build or native UX run was performed for this change. Issue 605's
capacity failure and the original five-case, 16-layout native acceptance remain unresolved.
