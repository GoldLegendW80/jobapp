# Native acceptance continuation handoff

Source checkpoint: `20df820a346503b3083c5fe306e88c19a28eab55`, branch `apply/native-proof-completion`, worktree `<checkout>/.claude/worktrees/codex-apply-native-proof`. Current tree: `f07a2c6b9d63bf102e485c09131690619b5cc73c`.

The frozen root `0070e6202cfe983ab2b6136f29e154c87c16a67c`, tree `602166af9b30a4cde220d9ac38ac4a0fcdbfffd8`, was merged before the fresh build. Only the native fixture application-list route changed afterward. Root has integrated that test correction as `c9569de9`; later root merge `c4725ede` was tree-equivalent to its first parent. No production source was edited by this continuation.

## Completed source and checks

- `1a465598`: framed/no-credential journeys require latest filled event counts exactly filled=6, unverified=0, failed=0, left=1, agent=0, skipped=0; exact identity/profile values; Resume document source; downloaded pinned CV bytes nonempty and PDF signature. Existing accepted-upload SHA and full-byte comparison retained. Root integrated this as `f711a07e`.
- `20df820a`: observed native fixture route fix, from unsupported GET `/api/postings/{id}/applications` to existing GET `/api/applications?posting_id={id}&limit=1`. Three inserted lines, one deleted line, no production handler change.
- Whole composed Apply/browser suite on the frozen app source: **10,460 passed in 361.17s**, `ops/agent-test.sh`, `tests/apply tests/browser -m 'not docker' -n 2 -q --tb=short`. This includes all B1/B2 fixes. Log `/tmp/fall604b-attempt1-module.log` (also `/tmp/fall604b-module.log`).
- Full web typecheck/lint, Ruff check and format of 783 Python files, CI plan 81, registry, and hooks passed at the verified-proof checkpoint. Route-only correction: full typecheck, focused ESLint and commit hooks passed.
- Pure status probe confirmed source-only assertions survive unverified/no_file and value_differs; exact counts reject both.

## Actual native results, including failures

Attempt 1 used one stack for both files. Results: **2 passed, 2 failed, 6 did not run**, 3.3m.

1. Unchanged A8 Lever failed waiting for the before-click screenshot. Browserd closed its first native navigation with the real memory guard at used_mib=1015, then browser_navigate returned 410 and the application was queued for retry. No cap or guard was changed.
2. Hosted known-fallback stopped on the unsupported GET list route returning 405. This was fixed in `20df820a`.

All first attempt logs are preserved as `/tmp/fall604b-attempt1-{build,module,migrate,up,playwright,stack}.log`; results/trace/screenshots are `/tmp/fall604b-attempt1-results/`. Browser memory event and navigation evidence are in `attempt1-stack.log` lines 520-522 and 620-626. First-failure Lever error context and trace are in `apply-a8-apply-on-the-fake-08437-cted-approve-submitted-once/` under that results directory.

Attempt 2 used fresh sequential, per-file stacks at unchanged browser memory limit 1024 MiB and guard 90%.

- **ALL FOUR UNCHANGED A8 JOURNEYS PASS, 4.1m.** Lever parse correction, person approval and submission passed in 2.5m. Custom generic native designation, outline pixels, person approval and submission passed in 1.5m. Preparation and no-page-error companions passed. Log `/tmp/fall604b-attempt2-a8-playwright.log`; stack `/tmp/fall604b-attempt2-a8-stack.log`; full result directory `/tmp/fall604b-attempt2-a8-results/`.
- **Hosted known-Ashby Review PASS, 2.1m.** Native known selection recovered to generic; zero preapproval submissions, two person approval clicks, exact one accepted upload, pinned PDF SHA and full bytes all passed. Setup also passed.
- **Fallback rehearsal journey FAILED, 2.2m**, in posting setup: PATCH of another posting to the already-used canonical hosted URL returned the normal duplicate-posting 409. This is not stale updated_at. Fallback totals: **2 passed, 1 failed, 3 did not run**, 4.3m. Log `/tmp/fall604b-attempt2-fallback-playwright.log`; stack `/tmp/fall604b-attempt2-fallback-stack.log`; results `/tmp/fall604b-attempt2-fallback-results/`.
- Held Full auto, framed mid-fill fallback and no-credential journeys **did not run** because the file is serial. Three completed rehearsals, exact held submission, and six verified/one Left native counts remain unproved. No complete native acceptance claim is made.

## Exact remaining fixture blocker

Source: `web/e2e/apply-known-fallback.spec.ts` posting helper around lines 159-177, called by rehearsal loop around lines 363-373. The helper creates a new posting for each rehearsal, then PATCHes it to the same canonical URL. Normal job duplicate protection correctly refuses the subsequent job even after the earlier application has been cancelled.

Verbatim structured request and response: `/tmp/fall604b-attempt2-409.json`.

Request: PATCH `/api/jobs/1754af7d-80fd-49c8-b34e-c0e8bb6180f8` with `{ "url": "http://atsforms:8080/ashby/example-robotics/applied-ai-engineer--known-fallback/application", "fit": 90, "updated_at": "2026-10-23T03:20:11.512019Z" }`.

Response: `{ "detail": "This posting is already in your list.", "existing_id": "9d0b433f-afad-4aab-9263-a6a913486029", "existing_slug": "brindle-example-labs-senior-platform-engineer-fallback-2" }`.

Existing list and duplicate semantics are documented/implemented in `api/app/jobs/router.py` around 373-496 and `api/app/jobs/service.py` around 1180. The nearby e2e helpers use direct optimistic PATCH; no existing automatic version-retry helper was found in the inspected helpers. A version retry would not address this actual duplicate response. Fresh agent should determine the existing normal same-posting Like/rehearsal/Yes/Cancel reuse flow, keeping canonical URL and cap/rehearsal guards. No fake URL aliases, counter seeding, deletion, or production policy change has been made or requested here.

## Cgroup metadata

All figures are bytes; guard 90%, limit 1,073,741,824 throughout. Files are `/tmp/fall604b-attempt2-{a8,fallback}-memory-{before,after}.log`.

| Stack and time | current | inactive_file | active_file | working set |
| --- | ---: | ---: | ---: | ---: |
| A8 before | 102092800 | 5935104 | 4456448 | 96157696 |
| A8 after | 128643072 | 12644352 | 9064448 | 115998720 |
| Fallback before | 91582464 | 0 | 0 | 91582464 |
| Fallback after | 113623040 | 8441856 | 0 | 105181184 |

The first-attempt guard closure did not repeat in the successful A8 retry or hosted Review retry. These before/after figures are not peak measurements and do not establish physical Jetson capacity.

## Screenshots and artifact index

Root inspected the current four A8 proof screens and four hosted Review layouts during execution. I also inspected Lever Applied visually. Preserved exact paths:

- `/tmp/fall604b-attempt2-a8-results/apply-a8-apply-on-the-fake-08437-cted-approve-submitted-once/lever-review.png`
- `/tmp/fall604b-attempt2-a8-results/apply-a8-apply-on-the-fake-08437-cted-approve-submitted-once/lever-applied.png`
- `/tmp/fall604b-attempt2-a8-results/apply-a8-apply-on-the-fake-82d4d-and-outlined-submitted-once/custom-review.png`
- `/tmp/fall604b-attempt2-a8-results/apply-a8-apply-on-the-fake-82d4d-and-outlined-submitted-once/custom-applied.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-review-desktop-light.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-review-desktop-dark.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-review-mobile-light.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-review-mobile-dark.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-applied-desktop-light.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-applied-desktop-dark.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-applied-mobile-light.png`
- `/tmp/fall604b-attempt2-fallback-screens/hosted-applied-mobile-dark.png`

Rehearsal failure trace: `/tmp/fall604b-attempt2-fallback-results/apply-known-fallback-three-627a5-osted-fallback-send-nothing/trace.zip`, plus `error-context.md` and `test-failed-1.png` in the same directory.

Recipes preserved for provenance: `/tmp/fall604b-native-run.py` and `/tmp/fall604b-retry-run.py`. They require fresh owned prefixes/log locations before reuse so prior evidence is not overwritten. Existing image override/config are synthetic, not real account settings.

## Cleanup confirmed

Both wrapper sessions ended and their finally blocks removed every owned stack, volume, network and the four `fall604b-*:review` image tags. Independent Docker list queries for fall604b containers, images, networks and volumes returned empty. Synthetic `/tmp/fall604b-fixture.settings` is absent. Borrowed `apply578-renderer:review` was not built, retagged or removed. No watchers or outliving processes were created. Only own dependency symlinks and generated worktree screenshots were removed after screenshots were copied to `/tmp`; worktree is clean. No real environment files/accounts/sites/credentials, mail, deployment, public approval or push was used.

Fresh fixture/native continuation owns the remaining work. Root quiet final gate remains pending.
