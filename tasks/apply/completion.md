# Apply specification completion

The owner's request of 2026-10-22 implements [SPEC-apply.md](../../SPEC-apply.md), tracked by
#563 under #70, in [PR #604](https://github.com/GoldLegendW80/jobapp/pull/604).
This ledger records implementation and separate test checkpoints. The final review and batch
gate result on that pull request identify the exact checked head and tree.

Implementation and scoped source review now cover A1 to A18. Runtime acceptance and the final
combined gate are still in progress. The current application source is frozen at
`0070e6202cfe983ab2b6136f29e154c87c16a67c`, tree
`602166af9b30a4cde220d9ac38ac4a0fcdbfffd8`, for the composed Apply/browser and native rerun.

The default-worker audit found missing generic wiring, known-ATS fallback, framed recovery,
credential-free fills and explicit Saved answers creation. These are implemented. Current
native file readback passes all four A8 journeys and three native file/iframe/chooser cases,
with exactly one accepted nonempty approved CV per Lever/custom submission and zero custom
submissions before approval. Saved answers passes its real fresh-user Add/reuse/local-edit/
profile-precedence journey, with zero submissions. Actual known-fallback Review/Full auto,
framed recovery and no-credential acceptance remain pending in the new combined rerun.

Fresh [field/progress closure](../evidence/apply604-field-progress-closure.md) closes the parser
and original-budget findings, including preservation of person edits and closed wizard steps.
Independent checks pass 3,360 genuine local choices, 495 clear wrapper controls, 48 ambiguous
named groups retained as unknown, and 108 actual-helper progress cases; all 11 targeted
mutations are detected. The [implementation checkpoint](../evidence/apply604-field-progress-implementation.md)
passes 710 focused cases including eight real PostgreSQL finish/persistence cases. Visible
unresolved fields remain separate from attempted outcomes so fresh and resumed credential-free
prefill both work. The earlier 10,049-case whole Apply/browser pass is a separate checkpoint;
the final composed source and native runtime are now running. These counts do not substitute
for the quiet exact-tree gate or final whole-PR review.

## Integration boundary

The isolated `apply/spec-completion` branch combines immutable snapshots of #465, #521, #517,
#516, #470, #598, #583, #588, #589, #590, #591, #592, #595, #600 and #601. Existing approved heads and their source branches are left untouched. Earlier fixes #600 and
#601 retain their independently reviewed heads. Those approvals do not approve this combined
tree. This task has not merged changes into main or deployed.

Tests use invented `e2e-*@example.org` accounts, local fake ATS sites and fake Gmail. No test
submits to a real company or sends real mail. Browser verification uses the repository's
Playwright tooling because Chrome DevTools MCP is unavailable in this session. At most two
heavy jobs run together; the final batch gate runs on a quiet machine.

## Capabilities and verification

| Capability | Implemented behavior | Runtime verification and final gate coverage |
|---|---|---|
| A1 to A8: forms, answers, fill, approval and reports | Source-bound answers, ATS adapters, exact person values, approved document/field binding, one-use submission and reports. Default generic worker, known fallback, framed recovery and explicit Saved answers are implemented. | The named-group checkpoint passes all 7,070 Apply tests and seven actual A5/A7 browser tests. Native file fix: 9,729 Apply/browser passes, three actual native file/iframe/chooser cases and all four actual A8 cases pass. Saved answers actual native acceptance passes. Known fallback actual acceptance and the final combined gate/review remain required. |
| A9: Apply myself | Native document picker ownership, immediate Submit-and-Close recording, ended-session queue/reopen/record, placed-document freezing, no app submit click. | Four new real browser recovery flows, original manual flows, six inherited native cases and distinct incidental-picker race pass. Whole Apply/Browser groups cover final integration. |
| A10a: retries and person input | Immutable 1,800-second person wait outside active time; same adapter, attempt, earlier fields and steps survive captcha/email-code input. | Whole Apply/browser checkpoint: 7,167 passes, plus three real Redis/private-HTTP cases. Actual captcha/email-code flows pass before a fresh approval and one fake submission. |
| A10b: sign-in | Watched and later owner-owned handoffs, registered typed callbacks and idempotent queueing; Full auto becomes Review first. | Both fake-site journeys and all 22 backend cases pass. Whole Apply/Browser groups cover final integration. |
| A11 to A13c: caps, queue and Full auto | Holds, rehearsals, fit/caps/pacing, pauses, model check and repeated final guards. | Injected-clock and counted fake-send/submit tests; whole Apply group includes actual holds and rehearsals. |
| A14: email | Immutable two-PDF mail, exact recipient and approval, code-owned send authority, coherent local stops/unknowns and shared confirmation. | Fake Gmail sends exactly one message with both PDFs; seven connection journeys pass. Lifecycle and final pacing checkpoints below. |
| A15: Workday, Google Careers, VIE | Ordered review-only wizards; early required edits trigger a fresh fill; VIE reuses exact person messages. | Five real browser tests pass in 6.6 minutes, with four approved fake submissions. Final Apply group also captures review screens. |
| A16: LinkedIn | Recorded opt-in, private pinned opener, modal replay, company-site fallback, production pacing of at least 600 seconds and at most 15 a day. | Eleven opener/refusal native cases pass. Consent, review and three rehearsals followed by held Full auto pass. Final Apply group runs all six flows, including corrected recovery/checkpoint cases. |
| A17: privacy and retention | Owner-bound routes/callbacks, private no-store errors, deletion cascades, temporary image and mail-audit retention, privileged boundary checks and private worker IO. | Six actual canary workflows, 42 AST cases, real firewall namespace checks and registered-route/deletion tests. Final Apply group includes the two-user journey. |
| A18: external profiles | Exact reviewed CV pin, section hash/one-use Save, explicit Replace, autosave consent and atomic empty-only writes, durable edits/Skip. | Four real Chromium flows pass in 4.6 minutes; 5,278 affected backend and 429 frontend cases pass. Eight real-Postgres route/lock cases and 20 inspected screenshots. |

## Shared contracts and checkpoint evidence

- Exclusion/duplicates: Unicode folding, hidden controls, IDNA and canonical fixed points;
  all seven new real-database cases and 1,085 pure/property cases pass. The Jobs/Apply
  checkpoint passed 6,280 with 15 existing personal-source skips; corrected manual fixtures
  then passed all 113 affected cases.
- Person answers: generic and VIE execution use the exact saved value before typing or a
  model call, including a 10,000-character document letter. Changed field shape remains empty
  with `person_answer_changed`. Related checkpoints pass 1,045 and 268 cases. An independent
  pure complete-run probe preserves the full letter, performs zero gate clicks and does not
  expose its text through model tool responses.
- Answer edits: the owner confirmed on 2026-10-23 that application edits stay local. Reusable
  answers are explicitly saved separately in Saved answers. The specification's source and
  success wording now agrees with its existing application-edit API contract.
- Reports/history: generic page parsing retains node boundaries for direct AI-reader
  warnings. Confirmed prior submissions are queried across roles and time for the owner and
  company; another person's history and rehearsed/uncertain attempts cannot establish Yes.
- Free text: each personal factual or preference sentence needs complete permitted source
  provenance. Finite technical action equivalences apply only to clear predicates; object
  names and noun lists remain intact. An achievement does not establish personal intent.
  Unrecognised paraphrases stay with the person. Original unsourced intent controls are
  refusal tests with genuine sourced positive companions; shared recorded streams are
  unchanged. Final grounding checkpoint: 1,915 focused passes and 66 original factual/
  coordinated review controls with supported companions. See
  [source provenance evidence](../../docs/evidence/apply604-source-provenance.md). The quote/
  metadata follow-up adds 332 passing boundary cases and 440 clean independent probes; typed
  metadata remains available to independent fact checking. Its whole Apply run passed 6,969
  with one inherited canary fixture failure, documented in
  [boundary evidence](../evidence/apply-provenance-boundaries.txt); the final gate covers the
  corrected fixture and combined source. The subsequent quote-ambiguity fix passes all
  7,092 Apply tests and 1,152 focused cases; ambiguous personal prose supplies only a complete
  context-preserving copy. Unique quote spans retain genuine statements outside the name. See
  [quote ambiguity evidence](../../docs/evidence/apply604-quote-ambiguity.md).
- Letter/rendering: adapter-owned classification, ordinary 5,000 and document-letter 10,000
  codepoint limits, exact shape binding and no truncation; an immutable hour per rendering
  attempt. All 201 real database/API cases pass. Migration tests pass through one `0026` head.
  The final Apply group runs the five visible editor/render-wait tests.
- Request boundaries: six request schemas have explicit bounds, with no registry exceptions
  or larger body classes. All 45 identity/body/schema cases pass, including escaped maxima
  and one-byte-over refusal. Email subjects additionally use 998 codepoints and reject CR/LF
  before mutation; the server-derived `max_chars` and client/editor agree. Legacy invalid
  content returns typed 422 at approval and remains editable.
- Email lifecycle: a whole Apply/mail checkpoint passes 7,191. Subsequent whole mail/A14
  passes 1,324 after atomic local-stop settlement, and 132 final A14 recovery cases pass after
  missing-draft recovery. The frontend passes 379 Apply cases and 13 typed-error queue cases.
  Recovery checks exact owner, posting, active send id and email ATS under locks. An uncertain
  attempt retains its spent authority and cannot automatically mint or send again.
- Final email pacing: 14 real-Postgres overlap/clock cases and the whole 1,346 mail/A14 suite
  pass. Final host reservation and click share a transaction. A paced non-send atomically
  resets its audit/draft/binding while retaining valid approval, mode and hold for later retry.
  Provider calls retain the mail lock; final time checks follow blocking owner/apply locks.
  The host-lock follow-up samples pacing time and rechecks authorization after acquisition;
  all 1,358 mail/A14 tests and 164 final lock/cap cases pass. See
  [host-lock expiry evidence](../../docs/evidence/apply604-host-expiry.md).
- Document/takeover gates: current document versions are rechecked for both modes before
  lease acquisition and click. All 21 focused document/outcome cases pass. Live ownership
  and its generation guard stop old paced input; Give back supplies a fresh page snapshot.
  A definite refused click returns to fresh approval without freezing files or claiming an
  uncertain submission. Profile Save cannot replay its spent approval. The final whole
  browser/affected gate checkpoint passes 2,221 cases.
- Navigation/clocks: navigation checks after dwell, awaited focus and immediately before
  `goto`; tab select/close check before mutation. Passive lists and manual navigation keep
  their allowed paths. Aggregate transport, queue and generic-form allowances include the
  specified 900-second Take over wait; active browser, token and turn limits stay unchanged.
  Whole browser/affected generic checkpoint: 3,273 passes. Exact-source Chromium: two tests
  containing all 21 native ownership checks pass. A continuous retake shares its original
  native/public/API pause deadline rather than renewing it. Ten focused deadline cases and
  the whole browser/affected Apply control suite pass 2,162 cases.
- Integration with upstream: the latest main changes to short Why-us pronouns and language/
  country-specific degree options are composed with the stricter provenance and existing
  field guards. Both acceptance and refusal tables remain, with valid sources in topic tests
  and separate unsourced-refusal controls. All 7,243 merged Apply tests pass, alongside 898
  focused A3/A5 checks. The
  [resolution record](../evidence/apply604-main-reconciliation.md) explains each conflict;
  the final PR records the merged head and gate.
- Privacy: all private endpoint families and linked documents have signed-out/cross-person,
  missing equivalence, CSRF, no-store success/error and callback-ownership coverage. The
  Documents/route/cascade checkpoint passes 3,515 with one stale memory assertion subsequently
  corrected. The canary/boundary suite passes all 48 cases: six complete worker workflows and
  42 AST alias cases. The actual Docker firewall namespace harness passes 16 coexistence,
  repair and uninstall checks; pinned shellcheck passes.
- Mail setup: exact file/directory modes independent of umask, refusal of unsafe existing
  directories before credentials, deterministic descriptor cleanup. All 21 setup cases and
  1,267 mail/A14 cases pass; 175 deployment contracts pass. Both relevant permission-neighbor
  tests detect a deliberately inverted condition, and committed source was restored.

These counts are separate checkpoints, not a summed final result. Detailed commands and
limits are under [tasks/evidence](../evidence), including
[A17 boundaries](../evidence/apply-a17/boundaries.txt),
[A17 routes](../evidence/apply-a17/routes.txt),
[mail lifecycle](../evidence/apply-mail-lifecycle.txt) and
[gate ownership](../evidence/apply-gate-ownership.txt).

## Final acceptance record

The first combined gate checked head `73ee000f99468a48f31cac7fcca03e306e9841d4`,
tree `058ea99230024b0491068aa7fa13738a2b58f81a`, and was deliberately interrupted
after confirmed failures (exit 130). It is not a passing acceptance record. The API phase
reported 26,521 passes, 57 existing skips and three outdated ingestion/Compose assertions.
Those assertions now reflect the existing generic transport allowance and optional mail
network/service configuration. The mail journey's obsolete client contract and A1's removed
form assertion are also corrected. Docker/coverage/component phases had not completed.

All six A16 journeys and the two-user A17 journey passed in that run; A17 made exactly one
approved fake submission and refused cross-person/signed-out private reads. Required A16
screenshots included unsettled/loading views, so they still need recapture and inspection.
The new screenshot helper passes two actual Chromium cases and its old-helper mutation fails
both. Profile approval's unsaved/pending edit fix passes 406 component and independent cases.

Independent source review also found final expiry/pause checks preceding blocking locks or
the last commit. Fixes and a second recheck exposed paced-approval and last-commit edge cases;
the latest fix passes 252 real-Postgres/Redis regression cases. Its broader Apply/mail run
reported 8,739 passes and five lifecycle-fixture failures caused by repeated injected state
mutations. The fixture-only repair preserves repeated guard reads and all original assertions;
179 lifecycle/contracts/boundary cases and the full 8,744-case repeat pass. Generic-runtime and
fallback changes require subsequent regression runs and the final composed gate. A fresh narrow
[closure review](../evidence/apply604-final-guard-closure.md) at `97c650d8` now closes both final
guard blockers: 168 independent pure cases pass, six regressions are caught by mutations, and
90 real-database boundary/host-expiry cases pass. This is scoped closure, not whole-PR approval.

Two composed A18 cases hit the unchanged browser memory guard at 923/941 MiB. Idle cgroup
measurements retained substantial active file cache even after Chromium exited. Functional
acceptance will use a fixed partition with a fresh stack per Apply file, keeping all four A18
cases together and preserving browser restart/cookie checks. The exact Playwright partition
must prove every test is collected once with no new skips. This isolation does not establish
long-lived production capacity or resolve the observed memory pressure; the guard formula,
service caps and host target remain unchanged. The sustained-service experiment and physical
capacity acceptance are tracked by
[browser follow-up #605](https://github.com/GoldLegendW80/jobapp/issues/605).

The new known-fallback native fixture checkpoint first reported 9,905 passes and one stale
six-versus-seven-field recording assertion. That assertion now includes Summary while retaining
all existing field and Submit checks. A second module attempt was intentionally interrupted
(exit 130) to wait for the latest source fixes. These are not native acceptance. The
[native delta review](../evidence/apply604-native-delta-review.md) also requires verified fill
counts and a nonempty pinned PDF in the frame/no-credential journeys; source labels alone
permit unverified outcomes. A fresh continuation owns those proof assertions and the actual
composed rerun. The fresh [proof recheck](../evidence/apply604-native-proof-recheck.md)
closes that assertion defect at source: independent tests reject unverified fills, wrong
values, missing events and empty/non-PDF/wrong-version files. Actual native counts and
execution still need confirmation. Rehearsals use normal person cancellation between attempts and a separate
Review account so the real duplicate-form guards remain in force.

Before merging, require a passing exact-tree batch gate and fresh independent review on #604.
The gate covers complete API suites and coverage, Docker/native tests, frontend typecheck,
lint, components and production build, migration upgrade/downgrade with one head, script and
secret checks, and the scoped Apply/Browser/Opportunities/mail journeys. Review includes the
required desktop/mobile light/dark evidence and a write audit of protected source branches.
The pull request records the final result, fixture-only skips, any retry/failure and the exact
head/tree. No source-snapshot approval substitutes for that combined review.

## Owner acceptance and deployment prerequisites

Real-site Checkpoint A remains the owner's step after a checked deployment: choose a real
Ashby/Greenhouse role, observe and approve it, and inspect screenshots and frozen documents.
Real sign-ins, real profile-save acceptance and Full auto's live acceptance also remain human
steps. Local fakes do not satisfy these physical/site acceptance criteria.

The existing core limits total 5,248 MiB; optional mail adds 128 MiB, for 5,376 MiB, 256 MiB above
the unchanged 5 GiB target. All 24 corrected renderer/Compose accounting cases pass. No memory
limit or target changed. The maximum 12 MiB synthetic mail send completed at the provider cap
with no OOM/restart but 292 pressure events. Fake Workday peaked at 454,807,552 browser bytes
under its 1 GiB cap. These measurements establish those workloads only, with no claim of
Jetson headroom or the 7.4 GiB host acceptance.

The owner provisions production credentials and installs host egress rules with sudo as in
[mail setup](../../docs/mail-application-setup.md). This task has not read real secret files,
run production setup, changed host firewall rules, merged changes into main or deployed.
