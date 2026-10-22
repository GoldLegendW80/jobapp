# Apply specification completion

Requested on 2026-10-22: implement the whole apply specification. The contract is
[SPEC-apply.md](../../SPEC-apply.md), the existing [task plan](todo.md), and the recorded owner
decisions. This audit supplements that plan; an item is complete only with implementation and
passing evidence. Whole-spec tracking issue: #563, under #70. This task makes no writes to
existing approved pull requests.

## Integration baseline

The separate `apply/spec-completion` branch combines immutable snapshots of existing pull
requests #465, #521, #517, #516, #470, #598, #583, #588, #589, #590, #591, #592, #595, #600 and
#601. Their source branches and GitHub states are not changed. This makes their dependencies
available for development without merging anything into main or deploying it.

The integration preserves both sides of the A9/A10a live-view and interrupted-run changes and
the A13c model-check services and worker. The combined tree requires fresh regression checks;
an approval of an original snapshot is not an approval of the combined tree.

## Capability and evidence checklist

| Capability | Existing implementation | Completion work | Evidence required |
|---|---|---|---|
| A1 to A8: forms, answers, fill, approval, reports, base adapters | Main plus existing apply snapshots | Audit interactions and refusal paths; fix specification gaps | Complete apply tests and fake ATS journeys |
| A9: Apply myself | #517 snapshot | Check manual/session recovery and file chooser follow ups | Manual journey, no app submit click, isolation |
| A10a: retries and needs you | #516 snapshot | Owner's HTTP rules and the full 30-minute captcha/email-code wait outside active lease time | Injected-clock failure, timer and recovery tests |
| A10b: sign-in recovery | Missing | #578: attended and unattended handoff, later callback, expired sessions and lease survival | Fake login walls, idempotent resume, Full auto becomes review |
| A11 to A13c: caps, Like queue and Full auto | Main plus #591, #598, #470 | Verify composed decision, hold, rehearsals, model check, pauses and final rechecks | Counted fake submissions, clock/cap tests and journeys |
| A14: email applications | Missing; required mail send components also missing | #251: draft, exact authorisation, provider boundary, send gate, uncertainty resolution and UI | Fake Gmail only, immutable attachments, no duplicate send |
| A15: Workday, Google Careers and VIE | Source integrated; acceptance pending | #252: bounded wizards, sign-in resume, labelled controls and file choosers | Fake multi-step applications; Full auto hands over to review |
| A16: LinkedIn Easy Apply | Source checkpoint integrated, acceptance pending | #253: opted-in modal flow, company-site fallback and LinkedIn pacing | Fake modal, consent revocation/checkpoint, cap and isolation tests |
| A18a to A18c: complete external profiles | Backend, UI and additive schema integrated; acceptance pending | #254, #255, #256: persisted sections, factual fill, protected-field policy, review/save gate and UI | Fake portal, one-use hash approvals, current values/Replace, autosave consent |
| A17: privacy and retention | Storage ownership/cascades exist; final proof missing | #257: prune temporary screenshots, route isolation, privacy text and click/send boundary proof | Two-user route suite, account/posting deletion, retention boundaries and canaries |

Current development streams: manual/session recovery and the bounded person-action wait;
Workday/Google Careers/VIE acceptance; LinkedIn modal acceptance. External-profile runtime
acceptance needs a fresh debugging pass from the preserved diagnostic handoff. The filed
answer, exclusion and fake-ATS follow-ups, final privacy proof and independent reviews follow.

The audit also covers filed follow-ups: #335 (shared exclusion and duplicate matching), #573
(manual recovery), #581 (editing a letter answer), #594 (document rendering wait), #587
(reporting pages addressed to an AI), #585 (unambiguous stored answer mappings), #593 (answer
filter limits), and #582 (fixture fidelity). Unknown qualifications and visas stay unanswered
unless the person's stored facts establish them. Human choices and real-site acceptance are
recorded separately from unfinished implementation.

## Dependency and ownership decisions

- Sign-in recovery supplies a user-owned, typed callback registry shared with external profile
  completion. A callback cannot name another user's run or an arbitrary queue job.
- Profile completion needs persisted addresses, section states, locators and audit metadata
  that migration 0017 did not create. An additive migration is required despite the original
  plan's "no migration" estimate. PR #591 supplies revision 0023; profile completion takes
  0024, and required mail send storage follows as 0025.
- The worker prepares email drafts; the process allowed to hold the mail send key owns the
  actual send gate. An uncertain provider result never causes an automatic resend.
- Testing uses invented `e2e-*@example.org` accounts and local fake sites/providers. At most
  two heavy test jobs run at once. Browser verification uses the repository's Playwright
  tooling because Chrome DevTools MCP is not configured in this session.
- Real-site Checkpoint A remains a human acceptance step after a checked deployment. It is
  not simulated by a fixture and is not claimed as completed by implementation tests.

## Final verification

- [ ] All capabilities above have implementation, including their provider dependencies.
- [ ] The complete affected API modules pass, with fixture-only skips identified.
- [ ] Frontend typecheck, lint, component tests and build pass.
- [ ] All apply/browser journeys needed by the completed capabilities run on isolated stacks.
- [ ] Migration upgrade and downgrade checks pass with one Alembic head.
- [ ] Independent reviews cover the submission, profile-save and email-send gates.
- [ ] The task's write audit confirms that approved PRs and their source branches were untouched.
- [ ] Each remaining human step or external configuration requirement is named explicitly.

## Evidence collected during implementation

- A10b backend, source commit `4f6e0b99`: 6,604 application/browser tests passed against
  throwaway databases and local fakes. Its web slice `0f643afb` passed typecheck and 416 tests
  in 17 frontend files. Final follow-up `13adfb29` passed both attended and later sign-in
  recovery journeys against the local fake, with one explicitly approved fake submission.
  Its final 22-case backend subset and the composed frontend suite still need to run.
- A18 schema `41e94c86`: 69 application-start regression tests passed. Core `6a4f7244` passed
  218 focused cases, mostly policy cases. Follow-up `18cbb047` passed 250 cases, including real
  lease DTOs, API boundaries, known-site dialogs, concurrent approval, pause, recovery and the
  shared token/turn budgets. Atomic-write/cancellation follow-up `b808cd50` passed 157 focused
  cases. The whole apply/browser run passed 4,950 cases and found one outdated queue registry
  assertion; its exact composed assertion is corrected, with a rerun pending. CV pinning
  `354a9f5f` uses the exact reviewed version and PDF digest: a newer default cannot substitute
  its bytes, and a missing or changed version prevents upload and Save. Final profile tests
  passed 30 cases. Browser testing found and fixed a stale Replace checkbox and premature
  completion after the first autosave section (`2d20cf64`, `466c7a5f`). All four real browser
  flows still require a passing rerun by the fresh continuation agent. Fresh-request tests found
  that Edit and Skip returned success without a database commit. The thin fix `0d2ed479`
  (integrated as `7e4f8edc`) passed five durability cases, including the exact persisted values
  hash and section authorisation; browser and whole-module reruns continue. The dedicated
  [A18 evidence](../evidence/apply-a18/tests.txt) records the remaining proof and stack recipe.
  The continuation's final browser run passed the complete Account/sign-in/edit/Save/Skip
  journey but left three failures. Its broad backend run was interrupted after 1,463 passes
  and two real LinkedIn classification assertion failures; it is not whole-module proof.
  The committed [runtime handoff](../evidence/apply-a18/runtime-handoff.txt) preserves exact
  run IDs, timing discrepancies and pending fixture assertions for a fresh debugger.
- A14 provider `06d24153`: 82 existing Google/key cases and the focused MCP transport case
  passed using a local fake Google service. Gate follow-up `5d0b18db` passed 1,251 mail,
  application and migration tests. It checks the exact recipient, both immutable PDFs,
  current approval, pauses and uncertainty without an automatic resend. The final A14 fake
  browser journey passed with one message and both PDFs, and seven existing Gmail connection
  journeys passed. A broad application/mail/migration run passed 5,808 cases and exposed two
  compatibility failures, both fixed with a 310-case affected rerun. Final combined suites
  remain. The shared "It was submitted" action still needs atomic, idempotent resolution of
  the linked application, MailSend and draft; the mail-specific resolver already updates all
  three. Independent review and Docker firewall namespace checks also remain.
- Mail infrastructure `a03dbfd2` passed 175 deployment contract checks, 52 allocator checks,
  pinned shellcheck and 81 CI plan checks. The normal Apply journey topology now supplies
  both fake Gmail and fake ATS services. A 12 MiB synthetic provider send succeeded at its
  128 MiB cap with zero OOM events or restarts, but memory reached the cap with 292 pressure
  events. This demonstrates the bounded test workload, not spare headroom or Jetson acceptance.
- A16 source `558e5c01` is integrated as `874a1722`, with both email and LinkedIn retained in
  Full auto's source registry. The private Easy Apply opener is unavailable to models;
  ordinary submit detection is unchanged. Checkpoint and pinned-button follow-up `ff575c9c`
  is integrated as `4a07e410`; eleven native Chromium refusal/opener cases passed. Finite
  pacing and the external manual alternative `b848d029` are integrated as `1ea2a9dc`: production
  requires at least 600 seconds between LinkedIn submissions and at most 15 a day. Forty-two
  configuration and 55 frontend cases passed. Modal journey acceptance continues.
- A15 source `b6248171` and shared wizard continuation `24390c4a` are integrated as
  `0137366a` and `0ff76c1c`. An early required answer prevents Next, and an exact person edit
  queues a fresh full fill before final approval. Adapter-owned target and field-shape markers
  remain in the approval hash. The isolated builder passed 105 pure cases, nine database
  cases and 355 frontend cases. Its composed browser journeys remain unrun.
- Composed A15 backend run: 8,435 passed, 19 skipped, with one failure and seven setup
  errors caused by exact adapter/fixture expectations that predated the added adapters and
  LinkedIn test setting. Their corrections and the typed/inline summary fixes passed 149
  pure/Compose and 420 adapter/owned-gate cases. The evidence lists the existing skips.
  Source `a6ecddef` is integrated as `466ed0c4`; its actual wizard journeys are running.
- A16 finite source `78a9276a` is integrated as `e1d4904f`, with shared typed and inline
  summary helpers `5e259507` and `983fa488`. The final focused database suite passed 77
  cases and pure summary checks passed 19. Six-flow browser acceptance remains pending;
  the previous run passed consent then found the inline summary parser issue. The source
  fix is covered by regressions but still requires a rebuilt application journey.
- A9 recovery source `60103855` and final passive confirmation watch `b8a1a503` are
  integrated as `015794d6` and `25b80508`. The focused manual/recovery/queue database suite
  passed 90 cases, with 123 pure browser ownership/action/recovery cases also passing.
  File chooser ownership and the full 30-minute person-action wait remain unfinished.
- A9 chooser/input ownership source `34fe8b86` is integrated as `39baa442`. The app now owns
  the Attach input turn, binds a chooser to its observation boundary and rechecks ownership
  after pacing. Three additional database regressions and native Chromium race proof remain
  pending. A fresh continuation owns the still-unimplemented A10a wait.
- Fresh A18 diagnosis correlated the retained traces with host power logs: two Idle Sleep
  periods of 1,034 and 1,070 seconds explain the previous readiness/timeout discrepancy.
  New browser commands keep the host awake only for the foreground command and retain the
  same timeouts, durable-state checks and atomic race assertions. Their rerun is in progress.
- A15 browser evidence `4bb524cc` is integrated as `b4a558a3`: all five serial tests passed
  in 6.6 minutes, covering four flows with exactly one approved fake submission each.
  Eight inspected handoff/submission screenshots are retained; review screenshots remain
  for the final composed run. The 48 MiB Workday fixture peaked at 454,807,552 browser bytes
  under its 1 GiB cap, without OOM or restart. This is fixture evidence, not a real-site or
  Jetson measurement. Its owned stack and images were removed.
- A18 read-lock fix `85d0aca1` is integrated as `1d6cda5a`. Three real-Postgres NOWAIT
  regressions first reproduced retained locks in profile detail, image and refusal paths;
  the complete eight-case route file then passed. Completed reads now release their
  transactions before returning. All async profile writers were checked for a commit before
  queue/browser awaits. The four-flow runtime rerun remains required.
- A10a fixed person wait `8403f579` is integrated as `e0696d76`. Private lease-owned waits
  pause the active/idle clocks for at most 1,800 seconds with an immutable deadline, retain
  the same adapter and prior report across wizard continuation, and remain distinct from
  sign-in. Pure/browser/wizard checks passed 618 cases, with 19 banner cases also passing.
  Database, email-code detection and native/browser acceptance continue.
- A17 retention `6ff0ae6d`: seven tests passed after a red run, covering expiry boundaries,
  image kinds, posting deletion and the privileged-click import boundary. The final route,
  account-deletion and mode-canary suite must run after all features are integrated.
- Integration checks through `0ff76c1c`: whole API lint and formatting passed on 711 files,
  frontend typecheck passed, all 81 CI plan cases passed, and E2E group registration passed.
  Frontend lint last passed before the latest mail and wizard slices; the final composed checks
  remain required.
- Integration through `9ae55749`: API lint and formatting passed on 712 files, and frontend
  lint passed. These static checks do not replace the pending combined regression journeys.

The new draft is [PR #604](https://github.com/GoldLegendW80/jobapp/pull/604). Work also remains
on A9's recovery follow-ups (#573), A15, A16 and the final cross-capability verification. Existing
approved branches receive no writes from this task. A read-only comparison on 2026-10-22 found
concurrent updates to #579, #538, #521, #518 and #505 elsewhere; #521's update is a merge of main.
The original snapshots named above remain the isolated integration baseline until explicitly
reconciled and retested.

Read-only board refresh on 2026-10-22: 10 cards Building, 1 In review, 2 Fix & re-check and 25
Approved across the project. No board changes were applied. A fresh fetch confirms current
`origin/main` (`e369e41d`) is already an ancestor of this integration branch.
