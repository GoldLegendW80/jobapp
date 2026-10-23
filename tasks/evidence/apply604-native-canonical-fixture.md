# Native canonical-posting fixture acceptance

Issue #563, PR #604. Native fixture correction only.

The previous fresh fallback attempt returned the normal duplicate-posting 409 when its second
rehearsal created another job and patched it to the first job's canonical fake Ashby URL.
The retained response states `This posting is already in your list.` and names the existing job.
This was not an optimistic-version conflict. The verbatim invented-data request and response
are retained in `/tmp/fall604b-attempt2-409.json`.

The fixture now creates one hosted posting for the Full auto account. Three real Likes each
create a distinct application on that posting, reach `rehearsed`, receive the person's Yes,
become Review `ready`, and receive normal person cancellation. The fourth Like reuses the same
posting and must reach its held submission. `likes.on_like` calls `service.start` for each Like;
cancelled applications are in `ENDED`, so the existing duplicate guard permits this lifecycle.
No unlike endpoint is needed. No URL alias, job deletion, direct database write, rehearsal
counter change, production source change, guard relaxation, skip or assertion deletion is used.

The earlier failure is the red native regression. The corrected native file adds distinct
rehearsal-application assertions, retains the three Yes/cancellation checks and verifies the
fourth submission on the same posting. Its existing independent proof assertions still require
six verified fields and one Left field, exact values and events, a nonempty pinned PDF, exact
uploaded SHA/full bytes, zero credential-free agent calls/tokens, and no unapproved Submit.

Tested source checkpoint: `5e9cd38a53156ca6f22ea05d72d8690f83235aca`.
Tested tree: `91ac2c8787254faac5492f03f95884b26aae22e6`.

Full web typecheck, focused ESLint, pre-commit hooks, all 81 CI-plan checks and the e2e registry
pass. The whole composed Apply/browser 10,460-test and unchanged four-case A8 native checkpoints
remain prior evidence on the frozen application source, not reruns of this fixture correction.

Native execution **passes all 6 tests in 11.7 minutes**, with no failed or skipped tests, on
one fresh uninterrupted `fall604c-native` stack, one worker, zero retries. Its own current API/worker/browser/Caddy images were built from the tested source;
the unchanged `apply578-renderer:review` image is borrowed. Browser memory remains 1,024 MiB and
guard 90 percent. The existing fake-stack timers are unchanged.

| Native case | Result |
| --- | --- |
| Fresh person, explicit Saved answer and native preparation | PASS, 3.7 s |
| Hosted Review, zero before approval, exactly one accepted pinned-CV submission after approval | PASS, 2.1 min |
| Three distinct same-posting rehearsals, three real Yes answers, three normal person cancellations, zero added submissions | PASS, 5.2 min |
| Fourth same-posting Like, held countdown, exactly one added accepted pinned-CV submission after its deadline, one click | PASS, 2.6 min |
| Known mid-fill frame remains frame 0, Full auto becomes Review with origin reason, six verified plus one Left, zero agent tokens or Submit | PASS, 48.6 s |
| Credential-free generic native form, six verified plus one Left, pinned nonempty PDF, zero agent calls/tokens or Submit | PASS, 57.1 s |

Read-only proof snapshots retain the three `rehearsal_yes` and `cancelled` event pairs and the
fourth held/submit lifecycle on the same posting in `/tmp/fall604c-after-held-proof.json`.
The fourth hold ended at `2026-10-23T03:33:51.197689+00:00`; its sole click is recorded at
`2026-10-23T03:34:45.283502+00:00`.

| Browser cgroup, bytes | Before | After |
| --- | ---: | ---: |
| Current | 90681344 | 138428416 |
| Inactive file | 0 | 16850944 |
| Active file | 0 | 0 |
| Working set | 90681344 | 121577472 |
| Limit | 1073741824 | 1073741824 |
| Guard percent | 90 | 90 |

These are before/after figures, not a peak or physical-capacity measurement. The final stack
log contains no memory-guard closure. Runtime image IDs and all service caps are retained in
`/tmp/fall604c-caps.log`; memory records are `/tmp/fall604c-memory-before.log` and
`/tmp/fall604c-memory-after.log`.

Execution artifacts are `/tmp/fall604c-{build,migrate,up,playwright,stack}.log`,
`/tmp/fall604c-results/` (including nine Playwright screenshots), and the recipe
`/tmp/fall604c-native-run.py`. All 20 named desktop/mobile light/dark layout images are copied
under `/tmp/fall604c-screens/`; `/tmp/fall604c-screens/index.txt` lists every exact path:

```text
/tmp/fall604c-screens/framed-review-desktop-dark.png
/tmp/fall604c-screens/framed-review-desktop-light.png
/tmp/fall604c-screens/framed-review-mobile-dark.png
/tmp/fall604c-screens/framed-review-mobile-light.png
/tmp/fall604c-screens/hosted-applied-desktop-dark.png
/tmp/fall604c-screens/hosted-applied-desktop-light.png
/tmp/fall604c-screens/hosted-applied-mobile-dark.png
/tmp/fall604c-screens/hosted-applied-mobile-light.png
/tmp/fall604c-screens/hosted-held-desktop-dark.png
/tmp/fall604c-screens/hosted-held-desktop-light.png
/tmp/fall604c-screens/hosted-held-mobile-dark.png
/tmp/fall604c-screens/hosted-held-mobile-light.png
/tmp/fall604c-screens/hosted-review-desktop-dark.png
/tmp/fall604c-screens/hosted-review-desktop-light.png
/tmp/fall604c-screens/hosted-review-mobile-dark.png
/tmp/fall604c-screens/hosted-review-mobile-light.png
/tmp/fall604c-screens/no-credential-review-desktop-dark.png
/tmp/fall604c-screens/no-credential-review-desktop-light.png
/tmp/fall604c-screens/no-credential-review-mobile-dark.png
/tmp/fall604c-screens/no-credential-review-mobile-light.png
```

The root independently inspected all four held, framed and credential-free layouts, plus the
four hosted Review layouts. The implementer inspected desktop/mobile hosted Review, held and credential-free layouts.

Independent cleanup evidence `/tmp/fall604c-cleanup.json` confirms zero owned containers,
images, networks or volumes and absence of synthetic `/tmp/fall604c-fixture.settings`.
The borrowed `apply578-renderer:review` remains. Own temporary dependency symlink and worktree
screenshot copies are removed after preservation. No real environment files, accounts, sites,
credentials, mail, public review, push, merge or deployment were used.

Prior failures remain recorded: first native attempt hit the unchanged memory guard and the
wrong list-route fixture; the next fresh attempt passed all A8 cases and hosted Review, then
stopped on the canonical-posting duplicate. Those partial passes did not establish complete
known-fallback acceptance. Sustained memory/capacity issue #605, physical Jetson and real-site,
sign-in/credential acceptance remain unproved. The root's combined quiet gate and independent
review remain required.
