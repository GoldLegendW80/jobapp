# Combined gate 2: inherited self-test configuration

Input head `2d9df92c8f920974355cdcdb3ba4fa7af0e03a91`, merged tree
`af3a367c831573d62e55f9f906f9ea94bf5fcf10`, base main `8d9c8c68d145`.
This attempt is **not acceptance**. It was stopped with exit 130 after a confirmed failure.

The declared outer `LOCAL_CHECK_JOURNEY_SHARDS=apply:82` is a valid fixed functional partition.
Actual pinned Playwright collection proves 82 tests exactly once, zero skips, 21 whole files
and all four A18 cases together on shard 44. Its 61 empty shards are not exercised tests.
Proof: `/tmp/jobapp-apply604-partition-2d9df92c/summary.json` and all 82 shard manifests.

The mock harness `tests/local_check_test.sh` did not isolate inherited `LOCAL_CHECK_*`
configuration. Its retry-stack scenario invokes the gate without a shard override, expecting
the production default `apply:2`. The production gate correctly honored the caller's outer
`apply:82`, so the mock recorded `--shard=1/82` twice, including its fresh-stack retry, and
failed its existing share assertion. Explicit custom-shard scenarios had already passed.
This is a test-environment isolation defect; the shard assertion must remain intact.

Evidence: `/Users/h.hautin/Documents/Discoveries/AI/jobapp/.local-check/logs/lc1023054207-3764/local_check_test.log`.
Its first twenty scenarios pass, then the share scenario fails at 163 seconds. Production
`ops/local-check.sh` reads the setting at line 849; the harness scenario is near lines 777/782.
A fresh fix task owns only the mock boundary and preserves per-scenario environment overrides.

Before interruption, frontend typecheck/lint/build, Ruff, hooks, secret scan, shell checks,
CI plan, journey registry, environment/egress/setup and runner checks passed. Native A10a,
A10b, A11, A12 and A13 files also passed. API tests were around 15 percent; complete API,
coverage, Docker, components and the remaining journeys had not finished. These partial
checkpoints do not replace a complete gate on the corrected exact head.

Root signalled only its owned supervisor 3762, which stopped gate 3764 and retained artifacts.
The session exited 130; Docker listed no remaining containers and the shared gate lock was
removed. The sampler terminated through the supervisor finally block. The later shard 25
failure appeared after the interruption and is not a newly diagnosed application failure.
Logs, traces, screenshots and cgroup samples remain under `/tmp/jobapp-apply604-gate-2/`.
Application code and the previously passing 10,460-case module, four A8 cases and six native
known-fallback cases were unchanged. A replacement full quiet exact-head gate is required.

## Harness closure

Fresh fix `64beee4b121b968e8041eafdff52f4b557108e58`, integrated as `4992e291`, adds
only five lines to the test harness: comments and an explicit unset of the nine production
`LOCAL_CHECK_*` settings before scenario setup. All original assertions/scenarios are byte
preserved; their explicit per-command overrides still work. Production `ops/local-check.sh`
is unchanged, including the real outer partition, limits, timeout and retry behavior.

The agent reproduced the baseline failure after twenty passes under
`LOCAL_CHECK_JOURNEY_SHARDS=apply:82 LOCAL_CHECK_STACKS=3 LOCAL_CHECK_STEP_TIMEOUT=3600`.
After the fix, the full mock harness passes all 51 scenarios with those same inherited
settings, and all 51 with clean defaults under macOS `/bin/bash` 3.2. CI plan passes 81 tests;
registry, ShellCheck 0.11.0, Bash syntax, diff checks and pinned hooks also pass. Mock Docker,
gh and curl tools prevent real service/network actions. All sessions ended and owned fake
tools were removed.

Retained logs: `/private/tmp/jobapp-563-gate-env-y8tnsdkx/baseline-inherited.log`,
`fixed-inherited.log` and `fixed-default.log`. Root read the actual red/green tails and counts.
This closes the harness defect; it does not substitute for the new combined gate.
