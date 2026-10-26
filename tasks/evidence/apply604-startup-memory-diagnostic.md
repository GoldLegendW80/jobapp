# Startup memory diagnostic

The declared native Review UX attempt on `9358e4eb7f1f55d8e716a8ed81746193cd9d2ffe`,
tree `2b5e8ed0995a025c3ab20e669aea11e5146d88a6`, failed. One case passed, one failed,
three did not run, and there were zero Playwright retries or flaky results. Playwright took
187.238393 seconds; the wrapper exited 1 after 189.434 seconds. No required review layout
was produced. Gate 4 has not started.

The unchanged 180000 ms pre-click image assertion failed before the second case reached its
repeated edits. The first pass prepared documents through the API. The later cookie, console
and zero-submit assertions did not complete. The retained application response reports
`failed`, attempt 1, `error_code=page_error`, and `submitted_at=null`; this is not a substitute
for the unfinished zero-submit assertion.

## Actual guard decision

The original navigation target closed under the specified memory guard. The hard memory and
swap ceilings remained 1073741824 bytes, with a 90 percent guard. The formula stayed current
less inactive file cache. The deciding read, sample `31470831388`, lasted 236.542 microseconds.
Its accounting and parser statuses were valid, with no read, observer or truncation failure.

| Deciding counter | Bytes | MiB |
| --- | ---: | ---: |
| Current | 1073573888 | 1023.839844 |
| Inactive file | 13447168 | 12.824219 |
| Working set | 1060126720 | 1011.015625 |
| Limit | 1073741824 | 1024 |
| Anonymous | 253755392 | 242 |
| File, including shared memory | 794480640 | 757.675781 |
| Active file | 756948992 | 721.882812 |
| Shared memory, included in file | 24084480 | 22.968750 |
| Kernel | 23629824 | 22.535156 |

The triggering working set exceeds the 921.6 MiB threshold. The independent collector's
nearby cgroup read matches the deciding counters exactly. Its cgroup read ended 5.368958 ms
before the guard read started. These sequential reads are not atomic, but no incompatible
deciding snapshot was observed. The cgroup reached its cap and recorded `max=110`, `oom=0`
and `oom_kill=0`; the browser had no container restart or OOM kill.

From startup to the guard, file charge rose about 483.305 MiB and anonymous memory about
175.859 MiB. After the close, the working set fell chiefly because active file counters moved
to inactive, rather than a comparable fall in anonymous memory. This supports investigation
of file faults, charges and reclaim. It does not prove the guard was a false positive or that
delaying it would be safe. All 53 Chromium PSS reads and 17 mapping reads were denied with
errno 13, so the responsible files and unique resident shares remain unattributed.

The collector emitted 1214 samples, used 1.618087 CPU seconds and at most 12836 KiB RSS,
and wrote 2692400 bytes. These fit its declared 5 CPU-second, 32768 KiB RSS and 33554432-byte
output budgets. No PID scan or read was marked truncated. No elevated access, cache eviction,
prewarming, changed containment, or increased memory limit was used. There was no unsampled
same-state control, so the footprint figures do not establish zero observer perturbation.

## Failure classification

The exact blank-page fix kept Chromium alive after the guard closed the original target.
The pending `browser_navigate` then raised Playwright `TargetClosedError`, which reached the
generic route handler and became `page_error`. Apply deliberately excludes that generic code
from its retry allowlist, so this attempt ended failed. It did not enter the older run's
queued 600-second retry path.

Navigation's existing helper already maps Playwright failures from `goto` to
`navigation_failed`, but acquisition, focus, title and settling have additional boundaries.
A separately reviewed navigation-only classification correction is the next source task.
It must preserve generic errors in other operations, cancellation, superseded fills, the
existing 600/3600-second retries and uncertainty after a submission or profile save. It must
not change the guard or introduce an immediate refill. Classification alone is not a memory
remedy and cannot be presented as passing this original five-case acceptance.

## Provenance and private preflight corrections

All 3823 tracked input identities match before and after. The original whole-file SHA-256 is
`39296ce3052764636a28c5759ab07f17e6dd0a2b6bed4117b78d4b2a1d5ab8ca`.
The browser's five lower RootFS layers match the older actual images. Layer six changes with
the intended browser source copy, and layer seven remains the same empty layer. Package and
binary attribution was collected after the workflow and sampling, never before the first
lease. The existing tini entrypoint and protections were preserved; the reviewed diagnostic
command changed only the browserd logger level before executing the same module.

The four-image build ran once and passed in 4.555 seconds. Its metadata tail was recovered
after a narrow formatter correction, without rebuilding. Preparation failures are preserved
and are not native test attempts:

| Preparation correction | Proof |
| --- | --- |
| Verify symlink target text like the existing source manifest | 12 author cases and 14 independent cases; original directory-link failure reproduced; 132 earlier artifacts unchanged |
| Locate pinned transitive Playwright metadata under pnpm's installed layout | Matching package, lock, CLI and wrapper hashes after the completed own dependency copy; no second copy, install or scripts |
| Read four optional image Config keys without absent-key errors | Actual five-image original failure and corrected success, 12 author fixture cases, fresh independent actual check, unchanged output allowlist and renderer guard |

Final private controller SHA-256:
`f6f22588b8b318e89ace656e88eca90363020cc642e96574c18816d412f2cd50`.
Measurement plan:
`7b596bb386b0b0a45978b58a2329c196246ce8d2d53d8b48812e41bf51ee5b14`.
Unchanged embedded and external sampler:
`ddde8aebbe15b9418fa8ae75211af0a60eb47b6f4d1856e535a4a0c336746d7f`.
The root-reviewed metadata resume helper has SHA-256
`4753b72cfedfc9e7a9284a302190352b6be2572991e724b6c2d0c61b1895d508`.
It checked the successful build, source, inputs and five image IDs, recorded a distinct
`built-recovered` result, then completed the original build marker. It performed no build.

Independent private reviews are retained at `/tmp/codex-605-freeze-symlink-recheck.md` and
`/tmp/codex-605-image-metadata-recheck.md`. The latter report SHA-256 is
`96078992e8431048062a58ce9bdc450f59ac42a5bcbaf7ce5593bc47a2641427`.
The raw experiment, source manifests, numeric series, guard-window records, original traces,
command exits, image identities, cleanup records and artifact hashes are under
`/tmp/codex-605-diagnostic-native-evidence`. The validator report is `native-diagnosis.md`;
the independent source and numeric assessment is `/tmp/jobapp-605-numeric-memory-diagnosis.md`.

## Images, cleanup and acceptance limit

Root inspected both automatic PNGs. The [failure image](apply604-composed-native/ux-diagnostic-attempt/failure.png)
shows the failed attempt and no pre-click image. The
[preparation image](apply604-composed-native/ux-diagnostic-attempt/preparation.png) is blank
because that case prepares documents through the API. Their original hashes and separate
Playwright/wrapper durations are in
[images.json](apply604-composed-native/ux-diagnostic-attempt/images.json). Neither is one of
the required letter-overflow, render-failed, original-long or original-control quartets.

Stack down and final cleanup exited 0. Owned container, network and volume inventories are
empty; owned settings, shim, dependencies and store paths are absent. The sampler exited 0;
the events child was terminated and waited. All 12 observed containers had zero restarts
and OOM kills. Four owned image tags and original artifacts remain for review; the borrowed
renderer stays exactly `95a287fd6ab2e53c62fc06fa1c08d97346039557bb3951b08626fb6b0e475610`.

Issue #605 and the original native acceptance remain open. No physical Jetson capacity,
cold-start remedy, headroom, passing gate or whole-PR approval is claimed by this diagnostic.
