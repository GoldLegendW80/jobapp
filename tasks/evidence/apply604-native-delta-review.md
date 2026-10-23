REVIEW VERDICT: REQUEST CHANGES
Head: 964d25741e77f9f7d4c3295c361df2bd94b75c4b

LOCAL scoped source review only. This is not a public PR approval or an approval of the whole integration.

## Scope and provenance

Initial acceptance checkpoint 67fc1cf99681524d556d45dbdfceaa5e6909f306, integrated as 508fee62154c0c258cb3dd0ced1defac7ab781d0: web/e2e/apply-known-fallback.spec.ts, four tests/ats_forms/apply-fallback HTML fixtures, tests/ats_forms_stub.py dispatch, api/tests/apply/test_a1_ats_forms_stub.py companion, and the Summary fill request in api/app/agents/fake_streams/apply_form.json. Eight files, 577 inserted lines.

Also reviewed complete followup deltas 7fcc6a73ca2be33500cc70b49d0a7c11256c60e9 (equivalent builder checkpoint ff102ee2e1c4082e4ed06baf7b5a2cd6bd9d5ac4) and 964d25741e77f9f7d4c3295c361df2bd94b75c4b. The second followup adds the ninth scoped file, api/tests/apply/test_a8_worker_runtime.py.

Detached review worktree: .claude/worktrees/rv604-native-delta, final checkout at the Head above. Read SPEC-apply.md owner fallback decision, approval/replay flow, Full auto guards/rehearsals/hold, source order and no-credential fallback; repository reviewer, implementer and checklist instructions; code-review-and-quality and security-and-hardening skills. No old application logic was re-reviewed beyond tracing what these new assertions prove. The separate parser/local-group and original-deadline findings belong to their existing review and fix/recheck stream.

## Blocking

1. web/e2e/apply-known-fallback.spec.ts, the shared framed/no-credential test around its Email/Resume assertions and application-detail events: source identity/document does not prove successful native readback. run._source deliberately preserves these sources for both filled and unverified outcomes. A native Resume whose upload reports ok but whose current FileList is empty is Filled(unverified, no_file), and an identity readback mismatch is unverified/value_differs; both still satisfy the current source assertions. Strengthen both branches using the existing public filled event status counts, expected to be filled=6, unverified=0, failed=0, left=1, agent=0, skipped=0 for these seven-field fixtures, verifying the actual count rather than changing it to accommodate a failure. Retain exact identity/profile field assertions and the unknown Summary Left assertion. Download the application-pinned CV version using the existing audit metadata and assert nonempty PDF bytes. This requires no production telemetry or policy change and makes empty-file or mismatched native values fail. Root and builder acknowledged this required gap; a fresh continuation owns the fix and native run.

## Findings closed in reviewed source followups

- Shared default-stream companion mismatch: /tmp/fall604-module.log preserves the initial 9,905 passed, one failed result in test_default_fake_stream_names_real_native_fields_and_submit. Adding e25 Summary to the stream without adding its label to the exact companion list caused that failure. 7fcc/ff102 adds only Summary to that list, retaining all six original field checks, required-ref checks and exact Submit designation check. I directly executed this existing pure companion function at the final reviewed head: PASS. This does not establish that the whole module rerun passes.
- Rehearsal reuse: the fake catalogue supplies one canonical hosted form. A successful Yes moves a rehearsal back to ready, where it still holds live uniqueness. 7fcc/ff102 now asserts ready/review after Yes, calls the normal Cancel API, asserts cancelled, and still requires rehearsals_done=3 through the normal settings view. This closes the source setup defect without seeded counters or weakened guards. Native confirmation is pending.
- Prior submitted Review duplicate: the original same-account sequence reused that form after Review had already submitted it; distinct titles cannot bypass same-address refusal. 964d257 creates and prepares a separate invented Review account and saves its Summary through the normal API, leaving Full auto to its original account. Assertions and cleanup are retained. This closes that source setup defect without fake job aliases, uniqueness changes or prior-application deletion. Native confirmation is pending.

## Correctness, security and boundary assessment

- Hosted form selection genuinely starts with the known Ashby adapter through an existing catalogue path. Its first/last-name DOM lacks Ashby's Full name anchor, triggering normal form_changed and generic recovery. The new dispatch remains after catalogue validation; a missing job still returns 404 and ordinary Ashby pages are unchanged.
- The framed fixture initially has the Greenhouse anchors in one frame. Its synchronous first-input handler replaces the later Last name control with Family name, removing the known field key after discovery while retaining a resolvable identity field. Pure recognition/key probes confirm this is a known framed Greenhouse path, not merely an initially generic form. The persisted strategy vendor=greenhouse, frame=0 and Family name assertions are meaningful. Same-lease identity is covered by the existing pure recovery tests, not directly counted by this new native journey.
- Hosted Review checks zero recorded submissions before two explicit approval clicks, then exactly one accepted submission. cvMatches compares the sole resume size, SHA-256 and fetched stored bytes to the application-pinned PDF bytes. Full auto uses real settings, fit, per-person cap, three real Like/rehearsal/Yes flows, then a visible held timer and a click timestamp at or after the held deadline. It does not write authorisations, rehearsal counters or submission state in SQL.
- The native frame/no-credential branches delete credentials through the normal API, compare the exact agent-run ID list, assert zero token usage and no credential stop, require no designation/click/submission, preserve the unknown Summary and require frame handover to Review with frame_origin_unverified. Their source-only native value assertion is the sole remaining scoped source blocker above.
- The Summary stream addition contains only an existing pinned native ref, e25, and calls the existing code-resolved fill_field tool. It supplies no answer text and no click tool. The saved Summary is explicitly posted and matches the invented profile's existing motivation. Existing A8 assertions must remain intact in the native rerun because the default stream is shared.
- Fixtures only load local fake resources. The new route adds no dependency, production handler, authentication, capability, cap, timeout, model-check or upload-policy change. SQL audit is read-only and application-ID scoped; test IDs come from newly created synthetic accounts. No secret or real personal data was found in the delta. No production data or credentials were accessed.
- Readability/architecture/performance: helpers follow existing journey conventions; fixture reuse is small and bounded. No additional blocking structure or performance issue identified in this test-only scope.

## Verification performed by this reviewer

At 508fee62, then repeated at 964d2574 with the followups:

- Direct invocation of the new existing pure loopback fake-route companion: PASS.
- Inverted the new known-fallback route condition only in the review worktree: the new companion failed as required. Original source was restored in finally; final worktree is clean. PASS mutation killed.
- Checked Summary e25 against the committed pinned native Workable snapshot; every recorded tool input remains ref-only and no click tool is present: PASS.
- Pure adapter selection and field-key probe over the pinned shape wrapped in one iframe: Greenhouse framed recognition succeeds; replacing Last name with Family name removes the old known key: PASS. This is a source/pure probe, not a newly captured native frame snapshot.
- Pure report-status probe: source-only assertion survives unverified/no_file, while existing filled-event counters distinguish it from verified fill: PASS, reproducing the blocker and validating the proposed assertion boundary.
- Existing worker stream exact-label/designation companion at 964d2574: PASS.
- git diff --check and final git status: PASS, clean.

These were lightweight direct single-process probes under nice 15, using the existing integration virtual environment read-only. ops/agent-test.sh would allocate Postgres/Redis; root explicitly assigned SOURCE/PURE ONLY while both heavy slots were occupied, so no Docker, database, image build, Playwright or full gate was started. A local loopback server lived only for each foreground probe and its context cleaned it up.

One combined repeat initially picked up mutation-generated Python bytecode after source restoration. I removed only that review-worktree cache, disabled bytecode for subsequent mutation probes, and reran the complete pure probe set successfully. This was probe isolation, not a product failure; the original route test also passed before mutation.

## Verification still required

The fresh continuation must close blocker 1, review that exact delta, and execute all six new native journeys plus all four existing A8 journeys with unmodified assertions on current composed source, then run the whole affected Apply/browser module and required quality checks. No native acceptance pass is claimed in this report. The retained initial 9,905+1 result must not be presented as a pass; its expectation fix has only the focused pure confirmation listed above. Final combined gate, current-head source reviews, screenshot inspection and public whole-PR review remain root's separate work.

## Pushed fixups

None. No commit, push, public review, policy change, production code edit or approved-PR branch change. The detached review worktree and two /tmp reviewer-owned probe scripts are left for provenance. No containers, images, networks, volumes, watchers or outliving processes created by this reviewer.

## Follow ups

None beyond the explicit remaining native/composed validation and existing separate review streams.
