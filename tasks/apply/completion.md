# Apply specification completion

Requested on 2026-10-22: implement the whole [SPEC-apply.md](../../SPEC-apply.md).
Tracking issue: #563, under #70. Implementation is assembled in draft
[PR #604](https://github.com/GoldLegendW80/jobapp/pull/604), branch `apply/spec-completion`.
The draft is not ready to merge or deploy: the combined gate and independent reviews remain.

## Integration boundary

The isolated branch combines immutable snapshots of #465, #521, #517, #516, #470, #598,
#583, #588, #589, #590, #591, #592, #595, #600 and #601. This task makes no writes to those
source branches or existing approved pull requests. Their approval does not approve this
combined tree. Main has not been merged or deployed by this task.

Tests use invented `e2e-*@example.org` accounts, local fake ATS sites and fake Gmail.
No test submits to a real company or sends real mail. Browser verification uses the
repository's Playwright tooling because Chrome DevTools MCP is unavailable in this session.
At most two heavy test jobs run together; the final gate needs a quiet machine.

## Capability and evidence checklist

| Capability | Implemented behavior and collected evidence | Remaining verification |
|---|---|---|
| A1 to A8: forms, answers, approval and reports | Ashby button/JSON groups, Greenhouse JSON/numeric IDs, chooser uploads, server-owned submission evidence. All 5,478 Apply tests passed at the fidelity checkpoint. | Resolve fresh independent answer audit findings; final API and Apply/Opportunities journeys. |
| A9: Apply myself | Four new real browser recovery flows pass, including immediate Submit-and-Close, document freezes, ended-session queue/reopen/record and no app click. Six inherited native cases and the distinct incidental-picker race pass. | Final composed regression and gate review. |
| A10a: retries and person input | Immutable 1,800-second person wait outside the active clock; captcha and email-code browser flows retain the attempt, earlier fields and wizard steps, then require fresh approval. Whole Apply/browser checkpoint: 7,167 passed, plus three real Redis/private-HTTP cases. | Final composed regression. |
| A10b: sign-in | Attended and later fake-site journeys pass; all 22 backend cases pass, including typed owner-bound callbacks and Full auto returning to review. | Final composed regression and ownership review. |
| A11 to A13c: caps, queue and Full auto | Gate, holds, rehearsals, model check, pauses and decision/submission rechecks are integrated. | Independent gate audit and counted submissions on final images. |
| A14: email | One fake Gmail message with both PDFs and seven connection journeys pass. Shared application confirmation now atomically resolves the application, MailSend and draft; 25 real database race/idempotence cases pass. Whole mail plus A14: 1,267 passed. | Final combined run and independent send-gate review. |
| A15: Workday, Google Careers, VIE | Five real browser tests pass in 6.6 minutes; four flows each submit once after approval. Early required answers cause a fresh complete fill. Eight handoff/submission screenshots inspected. | Final composed regression and review screenshots; VIE saved-answer audit fix. |
| A16: LinkedIn | Opt-in, private opener, modal, company fallback and pacing source integrated. Eleven native cases pass. Consent, review and three rehearsals followed by held Full auto pass. | Corrected fourth flow plus checkpoint/console flows and all six on final images. |
| A17: privacy and retention | Seven retention cases; owner/cascade route coverage; six complete private canary workflows; 42 privileged-boundary AST cases; 16 actual Docker firewall namespace checks. | New two-user browser journey and final independent privacy review. |
| A18: external profiles | Four real Chromium flows pass in 4.6 minutes; 5,278 affected backend cases and 429 frontend cases pass. One-use Save approvals, exact CV pin, explicit Replace, autosave consent and durable edits/Skip; eight real-Postgres route/lock cases. Twenty screenshots inspected. | Final composed regression and independent Save-gate review. |

## Shared completion work

- Exclusion and duplicate matching: fold case before removing accents, hidden controls,
  Unicode letters, IDNA and canonical fixed points. All seven new real-database cases and
  1,085 pure/property cases pass. The Jobs/Apply checkpoint passed 6,280 with 15 existing
  personal-source skips; corrected manual fixtures then passed all 113 affected cases.
- Document-letter editing: adapter-owned classification, ordinary 5,000 and letter 10,000
  limits, no truncation/control normalization, exact field-shape binding and displayed limits.
  Approved rendering waits retain one immutable hour per attempt. All 201 real database/API
  cases pass; all migration cases pass through the single `0026` head. The five new visible
  editor journeys still need to run.
- Request limits: six new request schemas have explicit bounds, without registry exceptions
  or larger body classes. All 45 related identity/body-limit/schema boundary cases pass,
  including worst escaped bodies and the one-byte-over limit.
- Answer integration checkpoint `080d04c6`: 10,310 Apply/agents/browser/mail cases passed,
  19 existing opt-in cases skipped, and one mail setup umask case failed. There were no Apply
  failures. The subsequent setup fix passes all 21 setup cases and all 1,267 mail/A14 cases.
- Grounded locative and coordinated claims follow-up `74d31e09`: 2,182 affected cases pass.
  Independent review subsequently found nominal locative claims could still borrow company
  words as personal facts. It also found VIE/generic execution bypassed saved person values,
  generic snapshot joining lost AI-reader warnings, and previous-application history was not
  populated. These concrete findings are being fixed before final combined verification.
- A17 routes: all registered private endpoint families and linked document representations
  are exercised for owner access, signed-out refusal, cross-person/missing equivalence, CSRF,
  no-store success/error behavior and typed callback ownership. The broad checkpoint passed
  13,132 and exposed 21 compatible Documents cache-header expectations plus one old memory
  accounting assertion. Preserving valid existing no-store directives fixes the compatibility
  issue; the Documents/route/cascade rerun passed 3,515 with only that old accounting assertion.
- The Compose accounting test now explicitly preserves the existing 5,248 MiB core total and
  adds the optional 128 MiB mail provider. All 24 renderer/Compose cases pass. No memory limit
  or global target changed: with mail the declared total is 5,376 MiB, 256 MiB above the
  unchanged 5 GiB target. Physical Jetson acceptance is outstanding.
- Mail setup now assigns exact modes independently of the operator's umask, rejects unsafe
  existing directories before credential creation and closes descriptors on failure. All 21
  setup tests pass, plus 175 deployment contracts. Two permission-neighbor tests detect a
  deliberately inverted traversal condition; committed source was restored afterwards.
- The A17 canary suite passes all 48 focused cases: six actual worker-backed workflows and
  42 AST boundary cases. It captures real queue result/event metadata and logs without leaking
  the canaries. The actual firewall harness passes 16 checks for independent mail/browser
  coexistence, drift repair and uninstall; pinned shellcheck passes.

Detailed slice evidence is retained under [tasks/evidence](../evidence), including
[recovery](../evidence/apply-recovery-runtime), [external profiles](../evidence/apply-a18),
[A17 boundaries](../evidence/apply-a17/boundaries.txt) and
[A17 routes](../evidence/apply-a17/routes.txt). Checkpoint counts are separate runs; they are
not a claim that the final combined tree has passed.

## Final verification

- [ ] Resolve all independent audit blockers and complete every implementation capability.
- [ ] Complete affected API suites pass, with fixture-only skips identified.
- [ ] Frontend typecheck, lint, component tests and production build pass.
- [ ] Required Apply, Browser, Opportunities and mail journeys run on final composed images.
- [ ] Inspect the required final review, handoff and submission screenshots.
- [ ] Migration upgrade/downgrade checks pass with one Alembic head.
- [ ] Independent reviews cover application Submit, external-profile Save and email Send.
- [ ] Final write audit confirms this task did not modify approved PRs or their branches.
- [ ] Each owner acceptance or external configuration step is named explicitly.

## Owner acceptance and deployment prerequisites

Real-site Checkpoint A remains the owner's step after a checked deployment: choose a real
Ashby/Greenhouse role, observe and approve it, and inspect its screenshots and frozen documents.
Real sign-ins, real profile-save acceptance and Full auto's live acceptance also remain human
steps. Local fakes do not satisfy these physical/site acceptance criteria.

The maximum 12 MiB synthetic mail send completed at the 128 MiB provider cap with no OOM or
restart, but reached the cap and recorded 292 pressure events. The bounded fake Workday run
peaked at 454,807,552 browser bytes under its 1 GiB cap. These measurements establish those
fixture workloads only; neither establishes Jetson headroom or the 7.4 GiB host acceptance.

The owner must provision production credentials and install host egress rules with sudo as
specified in [mail setup](../../docs/mail-application-setup.md). This task has not read real
secret files, run production setup, changed host firewall rules, merged main or deployed.
