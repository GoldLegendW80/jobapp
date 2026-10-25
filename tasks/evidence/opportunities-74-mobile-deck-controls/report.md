# Discover action overlap, issue 74

The action row now occupies normal layout space below the card. The production change removes
`sticky bottom-4` from that row. Card text, source links and the existing action handlers stay in place.

Depends on #611, transitively #610. Refs #74. Base `b8c0be4a353fb26dd82b42e50385337c2fb0aae2`,
tree `1d548119e23c27031cb0dea1cef2de2608a93766`.

## Measured regression

The committed old UI plus the new geometry case ran first on an owned fresh fake stack.
RED code head `6ef0ce1f2f45f229463bdc18335d6e7e1d4f8eb4`, tree `59112202d332c8d09abd1ca49de46a960147353f`.
Its single long-card dark case failed the intended rectangle assertion, native wrapper exit 1.
At 375px the card spans y=233 to 778 and its source spans y=713 to 757; Pass and Like start at
y=732, covering the source. The actual failure image and rectangles are retained here.

GREEN code head `85b2b78fcec5ebee2e7c2ab0e029701eb8059ec5`, tree `891aa291410aa5affb9b5ae0b6ffda92a9c6f746`.
The original 36 opportunities journeys plus four new geometry cases passed: **40 passed,
zero skips, failures, flakes or retries**, one worker. Playwright 83.910819 seconds;
actual native wrapper exit 0, 85.018 seconds.

The four cases cover short and long cards, light and dark, 375px and 1280px, filters closed
and open, and scrolling to the heading, source and actions. All 48 real-browser measurements
have zero field/action intersections and a 20px minimum card/action gap. Controls measure at
least 44px in both dimensions. Each case also passes source-link trial clicks and center hit
tests, keeps the server deck and drag offset unchanged while scrolling, then performs Pass,
Undo and Like through the normal controls. The new cases use source links to the fake ATS
fixture, with trial clicks and hit tests. The original O2 test bodies, hooks, assertions and timeouts
remain unchanged. GREEN adds read-only rectangle attachments to the RED oracle.

## Verification and artifacts

Focused deck units: 67 passed. Whole opportunities units: 84 passed with one worker.
Type checking, lint, CI-plan 81 tests, E2E group checks, scoped pre-commit and normal commit
hooks passed. Both stages collected the exact 40 names before execution. API, worker and
Caddy images have recorded source-head/tree labels, and the running container IDs matched.
Both fresh stacks were migrated to 0023 before testing; all 11 services were healthy with zero
restarts afterward. Owned containers, volumes, networks and image tags were removed. The
borrowed renderer stayed at `sha256:95a287fd6ab2e53c62fc06fa1c08d97346039557bb3951b08626fb6b0e475610`.
All 1,992 bound source/test/build files stayed unchanged across each run.

Actually viewed the RED image, all 16 new geometry captures, and all eight original O7 Hacker
News/Remote OK captures. Seven representative PNGs are copied byte-for-byte here; hashes and
capture states are in `manifest.json`. New full-page captures were taken after scrolling to
the controls. They retain the existing sticky app header at its scrolled position, sometimes
across the filter panel. No screenshot was edited.

Raw logs, traces, reports, all measured rectangle attachments, source bindings, dependency
receipts and duplicate captures are retained privately under `/tmp/jobapp-74-evidence`.
This evidence-only commit is later than the tested code head. The image labels above refer
to the recorded code head, not the later evidence head. No runtime rerun is claimed for the
evidence-only addition.
