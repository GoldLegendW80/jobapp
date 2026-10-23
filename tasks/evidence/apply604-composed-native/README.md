# Apply 604 composed native captures

Status: **checkpoint evidence; whole-PR acceptance remains pending**.

These are actual unchanged Playwright outputs on source `67813bac632794cc6c1df3df8dced6d51b750bf0`, tree `e8db21382650abccb7abe10310be5865dd0e7e28`, before the later HTTP403 recovery fix. Every copied PNG matches its source SHA-256; [checkpoint-images.json](checkpoint-images.json) records names, sizes and hashes. Root directly inspected every distinct emitted image. No image was redrawn or edited.

| Whole-file journey | Actual result | Playwright duration | Captures |
|---|---|---:|---|
| A18 external profiles | 4 passed; zero retries/skips/flaky | 278.554s | [a18](a18): all20 required desktop/mobile light/dark layouts, plus5 originals |
| A16 LinkedIn | 6 passed; zero retries/skips/flaky | 949.762s | [a16](a16): all12 required review, held and checkpoint-review layouts |
| Review UX | 1 passed,1 failed,3 not run; zero retries | 68.942s | [ux-failed-attempt](ux-failed-attempt): four overflow layouts only; **not acceptance** |
| Ingestion G10 | 8 passed; zero retries/skips/flaky | 12.578s | No required Apply layouts |

Each file ran on a fresh whole-file stack with one worker and zero retries. Browser cap1GiB, guard90percent, strict console errors and original individual budgets were unchanged. A18 peak386,379,776bytes, A16 peak353,398,784bytes; no OOM. Host sampling targeted only the owned browser container, without in-run exec probes. G10's worker pause/unpause barrier targeted one owned worker and finished unpaused. All owned runtime containers, networks, volumes and samplers were cleaned after each file.

The UX failure occurs after its editor/overflow assertions: SEED's partial model imports omit jobs metadata, so committing a fixture Application state raises NoReferencedTableError. The complete failure and three unrun cases remain recorded; four screenshots do not establish five-case acceptance. A fresh author adds only the canonical model-registry import. Fresh independent source closure and a new all-five-case whole-file native run, including the403 production correction, are required. The other12 UX layouts did not exist in this failed attempt.

Root inspection finds no visual blocker in the20 A18 and12 A16 required layouts. A18 preserves Replace/person edits, source labels, kept site values, autosave refusal/review distinctions and LinkedIn consent refusal. The mobile comparison table scrolls horizontally inside its card; its later columns are not initially visible. The three additional saved/autosave originals are byte-identical to inspected desktop-light variants; the distinct mobile-review and LinkedIn originals were directly inspected. A16 distinguishes review, three zero-send rehearsals, held submission, sign-in and security-pause review with their pinned document links and appropriate actions.

The four failed-attempt UX images display the long editor's error state, disabled Save/approval and remaining person answers. Their very long full-page images are scaled by image viewers; exact character counts are established by native assertions rather than a claim of reading every character visually.

Raw evidence, actual failure trace, input/image IDs, Playwright JSON, cgroups, commands and cleanup checks are retained under `/tmp/codex-apply604-native-evidence/`. The final complete quiet gate must rerun all nine journey groups on the eventual exact source; these pre403 checkpoints do not replace it. Sustained browser memory issue#605 and real-site/credential/physical owner acceptance remain open.
