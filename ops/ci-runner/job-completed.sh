#!/usr/bin/env bash
# Job-completed hook of the Mac CI runners (ACTIONS_RUNNER_HOOK_JOB_COMPLETED, set in the image).
# Runs after the last step of every job, whatever its result: removes the containers, networks
# and volumes the job created in this runner's Docker daemon, keeps images and build cache within
# their bounds (ci-clean.sh), and empties the job's work folder: the checkout, node_modules, the
# virtualenv and what Playwright wrote (reports, traces, screenshots: uploaded as artifacts by
# then). The next job's checkout starts from an empty folder. Never fails the job.
set -uo pipefail

"$(dirname "${BASH_SOURCE[0]}")/ci-clean.sh" job || true

ws="${GITHUB_WORKSPACE:-}"
# Only a folder under a runner's _work directory is ever emptied.
if [[ "$ws" == */_work/*/* && -d "$ws" ]]; then
  find "$ws" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  printf 'ci-job-completed: work folder emptied (%s)\n' "$ws"
fi
# Peak memory of this runner's container (the job, its stack and its builds) since it started.
if [[ -r /sys/fs/cgroup/memory.peak ]]; then
  printf 'ci-job-completed: memory peak of this runner since its start: %s MB\n' \
    "$(awk '{printf "%d", $1 / 1048576}' /sys/fs/cgroup/memory.peak)"
fi
exit 0
