#!/usr/bin/env bash
# Job-started hook of the Mac CI runners (ACTIONS_RUNNER_HOOK_JOB_STARTED, set in the image, so
# no change to the repository can lift it). Two things before the first step of every job:
#
# 1. Guard. These runners take this repository's CI workflow only. A job of another repository
#    or of another workflow file that targets [self-hosted, mac-ci] fails here. Exit 1 refuses.
# 2. A clean Docker daemon: whatever an earlier job left (a cancelled run, a killed container)
#    is removed, so every job starts as on a fresh machine. Images and build cache stay.
set -euo pipefail

REPO="GoldLegendW80/jobapp"
WORKFLOW_PREFIX="$REPO/.github/workflows/ci.yml@"

refuse() {
  printf 'ci-job-started: refused: %s\n' "$*" >&2
  printf 'ci-job-started: this runner only runs .github/workflows/ci.yml of %s\n' "$REPO" >&2
  exit 1
}

[[ "${GITHUB_REPOSITORY:-}" == "$REPO" ]] || refuse "repository ${GITHUB_REPOSITORY:-unknown}"
[[ "${GITHUB_WORKFLOW_REF:-}" == "$WORKFLOW_PREFIX"* ]] || refuse "workflow ${GITHUB_WORKFLOW_REF:-unknown}"
printf 'ci-job-started: allowed %s (%s) on %s\n' "$GITHUB_WORKFLOW_REF" "${GITHUB_EVENT_NAME:-}" "${RUNNER_NAME:-this runner}"

# ci-clean.sh sits next to this script, in the image and in the repository.
"$(dirname "${BASH_SOURCE[0]}")/ci-clean.sh" all
printf 'ci-job-started: %s CPUs, %s MB of memory for this runner\n' "$(nproc 2>/dev/null || echo unknown)" \
  "$(awk '{ if ($1 == "max") print "unlimited"; else printf "%d", $1 / 1048576 }' /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown)"
