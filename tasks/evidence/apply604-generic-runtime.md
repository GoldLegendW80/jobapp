# Apply A8 default worker runtime checkpoint

Recorded 2026-10-22. Refs #563, #343, #332. This is a partial checkpoint, not completed A8 acceptance.

The normal `worker.apply_services` now binds `RuntimeFormAgent` on the same lazy worker agent context as the answer agent. No model starts when services are constructed. The generic unknown-site path stays Review first, with the existing token, turn, active, wait and lease limits unchanged. The recorded default stream contains element refs from the actual pinned Chromium initial snapshot in `tests/ats_forms/a8/snapshots/workable-initial.txt`. Modelled A8 streams and the shared A5 recorded claims are unchanged.

The formerly skipped custom actual-browser journey is enabled. Its second posting has its own title to preserve posting deduplication. It asserts zero custom submissions before approval, an outlined submit, then one accepted custom submission with the person's email and one nonempty resume. The final assertions currently fail safely because native file readback is incomplete. They remain required.

## Actual browser evidence

Command: `ops/agent-test.sh --name gen604 --playwright -c /tmp/gen604.config.cjs apply-a8.spec.ts --workers=1`, wrapped by foreground `caffeinate`. Private project `gen604-runtime`, fake ATS forms only, own API/worker/Caddy/browser image tags and unchanged service memory limits.

Result: 2 passed, 1 failed, 1 did not run, 6.6 minutes. The base CV setup and full Lever journey passed; Lever submitted once. The custom default-worker form runtime completed in 21.112 seconds with 26,000 input and 580 output tokens, no denied tools. Its filled form and highlighted submit were visible, and the zero-preapproval-submissions assertion passed. Approval entered the actual submit lease. Replay returned `ready`, `clicked=false`; the required `Applied.` assertion timed out after the existing 180 seconds. No retry, timeout, budget or readback condition was weakened.

Screenshots retained under `apply-a8/`: `runtime-lever-review.png`, `runtime-lever-applied.png`, `runtime-custom-review.png`. These are desktop actual UI captures. The custom before image shows the red submit outline, the profile identity, the prepared CV and optional Left answers. It also exposes a conservative optional aggregate unsupported-button row, separately reported for parser review. No custom after image exists because nothing was submitted.

## Confirmed remaining replay blocker

A separate actual pinned-browser probe uploaded an invented `cv.pdf` to the unchanged custom native file input. The browser tool returned `ok: true`, `uploaded: ["cv.pdf"]`. Its subsequent snapshot still contains only `button "Resume *" [ref=e19]`, with no file name. Parsing yields `file:resume#1` with an empty value. The captured snapshot is `tests/ats_forms/a8/snapshots/workable-postupload-no-echo.txt`.

`base._fill_file` therefore returns `unverified/no_file`; submit `refill` correctly refuses any result whose status is not `filled`. The modelled CustomForm exposes a filename that this real native control does not expose through the pinned snapshot. A fresh fix must add trustworthy code-owned file readback, keeping the real native shape, strict approval replay and private model tool responses. Adding an echo to the fake or accepting unverified upload is not a fix.

The full local diagnostics are retained at `/tmp/gen604-replay-failure/` (Playwright trace, screenshots, stack and journey logs), with `/tmp/gen604-postupload.log` and `/tmp/gen604-postupload.py` for the confirming probe. These local paths are reviewer handoff, not portable CI artifacts. All owned containers, volumes, networks and image tags were removed; borrowed `apply578-renderer:review` was not modified. The native browser reported its original 1,073,741,824-byte limit, no OOM kill and zero restarts.

## Other verification

The default binding regression failed before the worker change, then all 3 focused pure worker/native-ref tests passed. Replaying the original stale stream refs against the captured native snapshot fails the new regression. Whole API Ruff check passed, 752 files passed formatting, web typecheck passed, journey registry passed, CI-plan 81 checks passed. Web lint on the old `08f79db3` base found 5 pre-existing site-profile errors; root already fixed those separately in `7942296c`.

Whole Apply backend regression is intentionally deferred to the fresh replay-fix slice by the orchestrator, so it runs against the completed native readback fix. The whole Apply journey group and final composed gate remain the orchestrator's required work.

## Separate remaining scope

Known-adapter `form_changed` fallback is owned by a fresh agent. It must work in both modes for already supported ATS, bind the chosen strategy into approval replay, preserve the Full auto allowlist and every model check/cap, and keep submit replay free of model calls. The custom unknown-site path remains Review first. #343 UI reason sentences and designated-row editability are a separate root-owned slice. Name-only React `type=button` submit designation remains the issue's declared fail-closed support limitation.
