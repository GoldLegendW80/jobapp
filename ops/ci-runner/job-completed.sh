#!/usr/bin/env bash
# Job-completed hook of the Mac CI runners (ACTIONS_RUNNER_HOOK_JOB_COMPLETED, set in the image).
# Runs after the last step of every job, whatever its result: removes the containers, networks
# and volumes the job created in this runner's Docker daemon, keeps images and build cache within
# their bounds (ci-clean.sh), and deletes what Playwright wrote into the checkout (reports,
# traces, screenshots: uploaded as artifacts by then). Never fails the job.
set -uo pipefail

"$(dirname "${BASH_SOURCE[0]}")/ci-clean.sh" job || true

ws="${GITHUB_WORKSPACE:-}"
if [[ -n "$ws" && -d "$ws/web" ]]; then
  rm -rf "$ws/web/test-results" "$ws/web/playwright-report" "$ws/tasks/evidence/ci" 2>/dev/null || true
fi
# Peak memory of this runner's container (the job, its stack and its builds) since it started.
if [[ -r /sys/fs/cgroup/memory.peak ]]; then
  printf 'ci-job-completed: memory peak of this runner since its start: %s MB\n' \
    "$(awk '{printf "%d", $1 / 1048576}' /sys/fs/cgroup/memory.peak)"
fi
exit 0
