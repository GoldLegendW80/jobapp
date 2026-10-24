# Apply gate, durable browser fixture identities

This is a scoped verification checkpoint for #563 / PR604, not whole-PR approval.

Gate3 failed before browser behavior assertions in two Docker tests because each requested a fresh profile UUID using an earlier profile's permanent UID. The module fixture shares its profiles volume across sequential tests. Ending a lease correctly preserves the UID ownership record; the product guard must continue refusing a different profile. The [gate record](apply604-gate3-result.md) retains the original failures.

Fresh implementer commit `8b63abd0d787ed1ba4020f1882338b7d9efbd76c`, based on composed main checkpoint `0b668519`, changes only the later B7 fixture UID from 20026 to unused 20027 and the later native-navigation fixture UID from 20000 to unused 20001. It adds two ownership comments. All cases, assertions, embedded native checks, budgets and production code are unchanged.

The exact four ordered existing cases reproduce RED: two pass and both later lease creations fail with 409 uid_in_use in 19.97 seconds. With the fixture correction, the identical four cases pass in 23.13 seconds, reaching SavePage policy assertions and all 14 native navigation/tab checks. Both complete affected files plus existing permanent-UID unit ownership regressions pass 52 of 52 in 174.23 seconds, with no skips.

API Ruff check/format, CI planner (81), e2e registry and normal commit hooks pass. One owned browser image was built from the released source and reused by both module stacks; the original memory guard and resource caps remained. All owned containers, fixture volumes, networks and image tags were removed. No real account or external company was used, no push/public review/deployment was performed by the implementer.

Retained commands and exact logs: `/tmp/jobapp-apply604-uid/handback.md`, `old-red.log`, `fixed-green.log`, `whole-files-and-unit-guards.log` under that same directory. The read-only causal audit is `/tmp/jobapp-apply604-gate3-uid-diagnosis.md`. Independent scoped review and the final complete quiet Docker suite remain required.
