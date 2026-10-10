#!/usr/bin/env bash
# Job-started hook of the Mac CI runners (ACTIONS_RUNNER_HOOK_JOB_STARTED, set in the image, so
# no change to the repository can lift it). Three things before the first step of every job:
#
# 1. Guard. These runners take this repository's CI workflow only. A job of another repository
#    or of another workflow file that targets [self-hosted, mac-ci] fails here. Exit 1 refuses.
# 2. A clean Docker daemon: whatever an earlier job left (a cancelled run, a killed container)
#    is removed, so every job starts as on a fresh machine. Images and build cache stay.
# 3. Disk guard. The job fails here, with the figure, when the Mac has less than CI_MIN_FREE_GB
#    free (default 10). A full disk does not fail one job: it corrupts Docker Desktop's store
#    for every container of the Mac (2026-10-10). /host-disk is an empty folder of the Mac
#    mounted read only by ops/ci-runners.sh, there so that df can see the Mac's own disk; the
#    container's own file systems only show Docker Desktop's virtual disk.
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

min_gb="${CI_MIN_FREE_GB:-10}"
probe="${CI_DISK_PROBE:-/host-disk}"
[[ -d "$probe" ]] || probe=/var/lib/docker
[[ -d "$probe" ]] || probe=/
free_gb="$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {print int($4 / 1048576)}' || true)"
if [[ ! "$free_gb" =~ ^[0-9]+$ ]]; then
  printf 'ci-job-started: refused: could not read the free disk space at %s\n' "$probe" >&2
  exit 1
fi
if ((free_gb < min_gb)); then
  printf 'ci-job-started: refused: only %s GB free on the disk (%s), CI needs %s GB before it starts a job.\n' "$free_gb" "$probe" "$min_gb" >&2
  printf 'ci-job-started: free space on the Mac (ops/ci-runners.sh prune empties the CI caches), then rerun the job.\n' >&2
  exit 1
fi
printf 'ci-job-started: %s GB free on the disk (%s), at least %s needed\n' "$free_gb" "$probe" "$min_gb"
printf 'ci-job-started: %s CPUs, %s MB of memory for this runner\n' "$(nproc 2>/dev/null || echo unknown)" \
  "$(awk '{ if ($1 == "max") print "unlimited"; else printf "%d", $1 / 1048576 }' /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown)"
