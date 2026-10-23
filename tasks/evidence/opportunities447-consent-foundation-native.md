# Platform consent foundation: native validation

Refs #447. This partial foundation depends on #612. It adds current-version opt-in records, consent/history guards and preservation of protected Free-Work settings through ordinary Search edits. A platform reader, Runner integration and activation controls remain separate work.

Tested source: `180638809942ec6a9b4b63d722cabfcc6c4b6b6f`, tree `af896088f08491e70cd314374e186c4b191acb9a`. The final publication adds only this evidence and four unchanged screenshots. All 1,457 frozen application, build and test input hashes match the tested source.

The complete opportunities group passed all 37 cases across nine files, one worker, zero retries and the unchanged 180,000 ms per-test timeout. Playwright reported 88.104 seconds; its agent wrapper completed in 90.289 seconds. Controller and teardown exited zero. There were no skipped, unexpected or flaky cases.

The new journey checks protected consent/query/location metadata, hidden-row indexing during move/remove, a real stale-save 409 followed by Keep editing, preservation of a newer Off decision, the underlying ten-source cap, replacement of a visible source and persistence after reload. All assertions completed. Desktop and 375 px screenshots in both themes were inspected. The tracked page errors and unexpected-network arrays were empty; only the local test origin was observed.

Before browser execution, actual image IDs, eight source paths per API/worker role, role-specific imports, fake settings and ten served asset hashes were checked. All eleven services were healthy with zero restarts before and after. These imports describe separate probe processes; host and image build equality is not claimed.

An earlier run passed the 36 existing cases and failed the new test because its locator asked for `dialog` while the captured UI used `alertdialog`. The one-word correction preserves every assertion. The failed run, screenshot, trace and logs remain preserved privately. It did not verify the later steps; the corrected complete run above does.

The unchanged application source previously passed 2,608 API tests with 17 existing private-engine golden skips, and 836 owning frontend tests. The corrected test passed typecheck, full lint, 37-case collection and normal hooks. Preparation used pnpm10.17.1 with unchanged exact dependency pins; native execution used the existing pinned pnpm9.15.9 and Node26.3.0. No dependency installation occurred during correction.

The isolated stack, its volumes/networks and the three exact owned image tags were removed. The borrowed renderer remained unchanged before and after cleanup. All data and services were synthetic and local. This proves the consent foundation; it does not establish real platform reading, daily Runner enforcement, sustained Apply memory capacity or whole-app completion.

The adjacent screenshot manifest records the tested source and exact image bytes. Private raw logs and large artifacts remain outside the repository.
