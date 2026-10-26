# Apply navigation failure source closure

Reviewed source: `3bd1aa505c34732aea13fa52dc275e7c5a448dce`, tree
`be37d5ac56c17adde78cecf653a9377ab96ac6d2`.
Integrated locally without conflicts at `e2fb05b13fd097e9cef15505307e4db2c30c568b`.
All seven changed source, test and evidence files are byte-identical to that reviewed head.
The integration leaves the recorded startup-memory failure evidence intact.

Ordinary `browser_navigate` Playwright failures during page acquisition, focus, title
and ordinary settle now return the existing `navigation_failed` result. Goto timeout
remains `navigation_timeout`. Existing retry delays remain 600 then 3600 seconds.
Memory formula, guard threshold, resource limits, fonts and launch flags are unchanged.

The first independent review requested changes on
`bc58313b8bbdc8ec1f47175236fbbf8480578495`: the initial catch also made failures
while recovering a forbidden host retryable. Nine real Playwright base/head probes
reproduced it, including a permitted goto that redirected outside the allow list.

A fresh fix agent added an explicit repair exception boundary. Recovery failures
retain the identical original exception and the existing route `page_error` handling.
Successful refusal remains `host_not_allowed`. Cancellation, other tools, submission
approval, unknown submission outcome and explicit profile-save uncertainty retain
their existing contracts. Production code uses no private Playwright exception import.

Fix verification: 18 failures and six passes before the correction; afterward,
3428 affected tests passed with pinned Playwright 1.55.0 and 2280 browser tests passed
in the normal API environment. Ruff, 81 CI-plan tests, group checks and commit hooks
passed. These are fix-author results.

The fresh narrow recheck found the prior blocker closed with no new blocker or fixup:

* 18 independent base/head comparisons passed, covering real Playwright errors,
  RuntimeError, cancellation, successful refusal, original identity and transport traces.
* 145 focused tests passed, including the actual HTTP handler and guard closure cases.
* Bypassing both repair wrappers caused 18 failures and nine passes. After exact source
  restoration, all 27 refusal cases passed. Scoped Ruff and format checks passed.

The recheck used its own source-bound environment, then removed its test services,
venv and caches. No Chromium, image build, native UX run or full gate was run for this
classification fix. The injected post-arm failed-snapshot test demonstrates generic
unknown-outcome safety; it does not represent a new post-click navigation path.

Private review receipts: `/tmp/jobapp-605-navigation-first-review.md`,
`/tmp/codex-605-navigation-fix1-report.md`, and
`/tmp/jobapp-605-navigation-recheck1.md`.

The 1 GiB cold-start file-charge failure and original all-five-case, 16-layout native UX
acceptance remain open. This source closure is not a capacity remedy, full-gate pass,
public approval or authorization to publish. No approved PR or source branch changed.
