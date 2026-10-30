# Shell scripts, bash 3.2 and ops tools

Read this when a trigger line sent you here: your diff adds or changes a `*.sh` file, a script
under `ops/`, a `tests/*_test.sh`, or a workflow step that runs one. A reviewer reads it for the
same diffs. Moved on 2026-10-29 from [implementer.md](../implementer.md) (Tests, "shell"),
orchestration.md (Lessons learned, bash 3.2) and the shell part of checklist item 5 of
[review-checklist.md](../review-checklist.md). The cases are in
[lessons/gate-and-ops-tools.md](../lessons/gate-and-ops-tools.md). A script that is part of the
gate, ship or deploy also needs [gate-and-ship.md](gate-and-ship.md).

## Before you push a shell change

Every `tests/*_test.sh` of a script you changed, and shellcheck through the pinned image:
`docker run --rm --name <task>-sc -v "$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 <files>`.

## bash 3.2 (macOS `/bin/bash`)

No associative arrays or `mapfile`; `set -e` is ignored inside a function called from `if`, `||`
or `&&`; `grep -q` in a pipe under `pipefail` can fail a healthy check (SIGPIPE of the writer); an
EXIT trap must remove what background jobs of the script created, and a script must never leave a
background job behind it.

## Checklist item 5

Its check (a `timeout` on every `gh`, `git` and `docker` call, a watchdog test, a kill that leaves
no stack, slot or lock) is in [gate-and-ship.md](gate-and-ship.md): it applies to every script
that calls them, not only to the gate.
