# Apply 604 canonical posting fixture source closure

Source review result: CLOSED, with no blocking source findings in the scoped fixture round.
This is a local source closure, not a public PR verdict or native acceptance.

## Reviewed source

- Base: `593d2b919376f0472c37ba79a854707f7e40edfc`, tree `0aded10fc88777c25f16b91a3fd955183f940859`.
- Author fix: `6489ec162a694325f39879d96da42b6c6cecb2c0`, tree `83e763cf9342c3a9fbe887ab74d166f6033195aa`.
- Exact composed candidate: `f61962cb14728da8076e5b0634e70679e70ee87e`, tree `1c8ec507a0f78f56f705f059da24e7924d772d86`.
- Review branch: `review/apply604-ux-posting` in `.claude/worktrees/codex-apply604-ux-posting-review`.
- Source scope: only `web/e2e/apply-review-ux.spec.ts` and `web/e2e/apply-document-helpers.ts`, 44 insertions and 18 deletions from the base. The other three changed files are existing completion/coverage evidence; that closed work was not re-reviewed or rerun.

The posting helper creates and patches the canonical fake Lever posting once, then regenerates
documents through the normal write endpoint before each later application. Its baselines use
document kind rather than response position. The retained API proof has stable document IDs per
kind, fresh version IDs and version numbers 1 through 5 for each kind, and five distinct
applications. The fixture asserts distinct application IDs and refuses to seed a version already
pinned by an earlier application.

The shared wait requires both versions to exceed the supplied kind baselines, be idle and error
free, and have PDFs. Old terminal render errors do not release regeneration; a new terminal
render error fails immediately with its message. Omitted baselines retain the original behavior
for API version numbers, which are non-negative integers. All 17 old two-argument callers remain
unchanged. There is one original 150000 ms polling window and no interval or budget override.

## Preservation audit

All hooks and all five case bodies, including the parameterized long/control cases, are byte
identical to the base. Their shared suffix SHA256 is
`cc207623412fc1751ee0f3382f2b0b125959b83764fececc1638f981ece14872`.
The original 600000 ms case timeout, model-registry import, cancelled-state and unfrozen-version
guards, exact invalid letter seeds, original assertions, zero-submission assertion and strict
console assertion remain. No URL nonce, deletion/reset, production guard change or production
source edit was introduced.

Reviewed file SHA256 values match the author's retained source audit:

- `apply-review-ux.spec.ts`: `39296ce3052764636a28c5759ab07f17e6dd0a2b6bed4117b78d4b2a1d5ab8ca`.
- `apply-document-helpers.ts`: `3d3e9888cbb8920628ccce4867c3d3017f5069bfd2fde84ad07d42c52dc891ff`.

## Independent execution

Reviewer evidence is retained in `/tmp/codex-apply604-ux-posting-review-evidence`.
The independent `independent-probe.cjs` transpiles the actual helper and executes its unchanged
predicate and postconditions with real Playwright assertions and real `expect.poll`. It wraps
poll only to observe its options and results; it does not replace polling, change its timeout,
or bypass a source condition. Requests return controlled snapshots without a native stack.

- PASS: 36 actual-helper scenarios, including 12 comparisons against the base helper's omitted-baseline behavior.
- PASS: stale ready pair, only CV advanced, only letter advanced, reordered results, partial/empty results, missing latest versions, missing PDFs, queued/rendering versions, and old CV/letter errors wait for the subsequent valid pair.
- PASS: both new terminal-error kinds fail after the first response with the original error message. HTTP 503, JSON failure and request failure remain strict immediate failures.
- PASS: reordered advanced pair and a partial supplied baseline accept the appropriate current pair.
- PASS: each invocation makes exactly one poll with only `{ timeout: 150000 }`.
- PASS: an in-memory mutation replacing the added version-advance condition with the original `>= 1` released stale PDFs on response one. The independent old-ready guard expected response two and killed the mutation. The tracked source stayed byte identical.
- PASS: own-file ESLint and Prettier checks, source-range `git diff --check`, and Gitleaks over the three candidate commits, with no leaks found.

The dependency lock hash matched the borrowed installed dependencies exactly:
`caa71054d0fafbbef08b1a1aa628e3b70835f74d01df6b300c59d9112c5a5916`.
No dependency installation was needed. The temporary dependency symlink was removed after lint
and format checks.

The first independent harness run incorrectly expected polling to retry a request failure.
The retained diagnostic proves the actual existing helper fails immediately. The harness
expectation was corrected, with no product or fixture source change. Both initial logs remain
alongside `independent-probe-green.log` and `independent-probe-result.json`. The initial `nice`
attempt was denied by the sandbox; these short isolated Node probes started no services.
Gitleaks was absent from PATH, so the passing source scan used its existing pre-commit cache
binary. Both command logs are retained.

## Author proof inspected

Retained target evidence in `/tmp/codex-apply604-ux-posting-evidence` shows:

- Original-source RED: second canonical PATCH returns 409 with the existing posting identity. The archived original spec matches the exact base source.
- Corrected-source GREEN: one source-backed API/Postgres test passed in 11.80 seconds. It executes extracted posting/wait/SEED code, the actual invented profile, fake-stream writers and the render pipeline for normal, long, control, long, control.
- The five cycles have one posting, five distinct applications, ten distinct current version pins, stable CV/letter document IDs, version numbers 1 through 5, and two deliberately stale document reads before each completed generation. The harness asserts exact unusual letter payloads and preserved prior content/hash/frozen state.
- Author typecheck, clean CI-plan 81-test run, E2E registry, formatting/lint and normal commit hooks passed. The earlier temporary-probe CI inventory race remains separate from the clean run.

Evidence provenance limits remain explicit. Earlier harness setup logs were overwritten before
preservation and are represented only by summaries and the author's tool transcript. They are
not raw evidence. The genuine collision RED traceback includes an unreached temporary harness
assertion using `plan.context.letter_text`; the author corrected it to `plan.letter_text` before
the first corrected-source GREEN. That correction is in the tool transcript, and the retained
Python harness is the GREEN version. It does not affect the second-PATCH collision RED.
The reviewer inspected this retained API proof and did not rerun it.

## Remaining gates and cleanup

No Docker, native browser, full backend/module, coverage diagnostic, build or Gate 4 was run by
this reviewer. The separate Apply module/coverage closure remains separate. The earlier failed
native run grants no acceptance. A fresh validator must still run all five native cases and
produce all 16 actual captures, followed by final quiet Gate 4 and the final whole-PR verdict.

Only this report is added by the review. No app or test source was edited, no public verdict was
posted, and nothing was pushed, merged or deployed. The reviewer created no services,
containers, volumes, images, accounts or persistent processes. Local probe evidence and the
review worktree remain for the orchestrator; no dependency symlink remains.
