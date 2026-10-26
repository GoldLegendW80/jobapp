# Apply native file readback acceptance

Recorded 2026-10-23 for #563 and draft #604. Runtime source: `eb85f9b7`; native fixture source: `dba09470`, including the composed latest main at `cdb3487a`. This completes the native replay blocker recorded in [the earlier runtime checkpoint](apply604-generic-runtime.md).

The actual default worker A8 journey passes all four required tests. The custom form has zero submissions before approval, an outlined submit, and exactly one accepted submission after approval with the person's invented email and one nonempty resume. Lever also submits exactly once. The existing approval, document-version, replay and no-model-submit guards remain in their owning code.

## What changed

The pinned AI snapshot omits the selected file name from a native file input. Browser upload acknowledgement therefore could not prove the selection during replay. The existing snapshot response now includes `native_files`, keyed by the current native input's exact snapshot ref. It is read from the current `HTMLInputElement.files`, independently of the upload response and page text. The adapter uses that readback for native file controls. Empty, malformed, multiple or wrong selections cannot inherit a stale filename echoed by the page. Rejected uploads retain the existing alert refusal. Chooser controls retain their existing path.

The native read uses Playwright's single-locator evaluation path. The initial bulk evaluation attempt returned no element because that path did not see the pinned snapshot ref cache. The actual browser regression caught this as a safe `page_error`; using the existing isolated selector path resolves the same current ref correctly. A disappeared ref refuses the snapshot. No timeout or capability was increased: locator evaluation uses the existing five-second action bound. File lists are capped at three names, names at the lease's 83-character filename bound, and serialized metadata at the existing 40,000-character snapshot budget. No filename logging was added.

The underlying APIs are [Playwright Locator evaluation](https://playwright.dev/python/docs/api/class-locator#locator-evaluate) and the current [native FileList](https://developer.mozilla.org/en-US/docs/Web/API/HTMLInputElement/files). The selector-cache behavior above is an observation from the pinned 1.55 browser, not a claim about every Playwright version.

## Verification

- Focused boundary tests: 40 passed. Cases cover all four adapter classes, no filename echo, stale echoes, clearing, replacement with the wrong file, multiple files, rejection, malformed metadata, changed refs and the metadata budget. The separate top-level malformed-metadata regression failed all three cases before its guard, then passed. The initial focused run was 33 failed and 2 passed; it also exposed generic fixture setup that needed its lease-given file list corrected.
- Whole folders: `ops/agent-test.sh --name file604-final-module tests/apply tests/browser -m 'not docker' -n 2 -q --tb=short`: 9,729 passed in 384.63 seconds. An earlier checkpoint run passed 9,726 cases before the last malformed-metadata cases were added.
- Actual pinned browser: `BROWSER_TEST_IMAGE=file604-browser:review ops/agent-test.sh --name file604-native tests/browser/test_native_file_container.py tests/browser/test_b5_container.py::test_a_form_is_filled_and_read_back_and_submit_is_refused -n 0 -q --tb=short`: 3 passed in 109.34 seconds. The native boundary runs at top level and inside a same-origin iframe. Both verify current selection, replacement, multiple selection, explicit clearing with a changed ref, rejected uploads and foreign lease-token denial. The existing native/chooser flow also verifies model submit refusal.
- The reset fixture explicitly clears the new input. Chromium's `cloneNode` preserves a file input's selection, which the metadata correctly observed during the first reset probe.
- Actual A8: `ops/agent-test.sh --name file604-a8 --playwright -c /tmp/file604.config.cjs apply-a8.spec.ts --workers=1`: 4 passed in 4.2 minutes. Lever took 2.7 minutes, custom 1.4 minutes, and the no-page-error assertion passed. The baseline custom replay failure remains recorded under `/tmp/gen604-replay-failure/`.
- Whole API and browser Ruff check passed; 800 files passed formatting. Web typecheck and lint passed. CI plan: 81 passed. Journey registry passed. Commit hooks, including secret scanning, passed.

The two inspected actual UI captures are [before approval](apply-a8/native-custom-review.png) and [after the accepted submission](apply-a8/native-custom-applied.png). The before image shows the native resume readback and outlined submit. The after image shows Applied, both browser screenshots and the single submitted event.

## Resources and limits

All runs used synthetic `e2e-*@example.org` accounts and fake ATS forms. The final browser retained its 1,073,741,824-byte memory limit, no OOM kill and zero restarts. Disk remained about 44 GiB free. Own containers, networks, volumes, ephemeral fixture settings and `file604-*` image tags were removed. The borrowed regular-PDF renderer remained `apply578-renderer:review`, image `sha256:95a287fd6ab2e53c62fc06fa1c08d97346039557bb3951b08626fb6b0e475610`, without building, retagging or deleting it. The final composed gate must build its own current renderer.

Local diagnostics remain at `/tmp/file604-{native,playwright,final-module,focused,ci-plan,web-lint,stack}.log` and `/tmp/file604-results/`. The compose recipe and generated Playwright config are `/tmp/file604-native-run.py` and `/tmp/file604.config.cjs`; these are local handoff paths, not CI dependencies.

The iframe evidence covers a same-origin frame. Frame-routing and cross-origin approval policy are separate work. The optional aggregate unsupported-button row remains visible in these historical captures; this slice does not suppress it. Saved-answer functionality, broader generic recovery and the final composed gate remain with their respective owners.
