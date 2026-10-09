#!/usr/bin/env bash
# Job-started hook of the Jetson runner (ACTIONS_RUNNER_HOOK_JOB_STARTED, set in the image, so no
# change to the repository can lift it). The runner has the host Docker socket, which is root on
# the Jetson: it runs one workflow only, deploy.yml as defined on main, and only for the events
# that workflow uses. Any other job (a pull request, a branch, another workflow, another
# repository) fails here, before its first step runs. Exit 1 refuses the job.
set -euo pipefail

REPO="GoldLegendW80/jobapp"
WORKFLOW="$REPO/.github/workflows/deploy.yml@refs/heads/main"

refuse() {
  printf 'runner-job-guard: refused: %s\n' "$*" >&2
  printf 'runner-job-guard: this runner only runs %s on workflow_run or workflow_dispatch\n' "$WORKFLOW" >&2
  exit 1
}

[[ "${GITHUB_REPOSITORY:-}" == "$REPO" ]] || refuse "repository ${GITHUB_REPOSITORY:-unknown}"
[[ "${GITHUB_WORKFLOW_REF:-}" == "$WORKFLOW" ]] || refuse "workflow ${GITHUB_WORKFLOW_REF:-unknown}"
case "${GITHUB_EVENT_NAME:-}" in
  workflow_run | workflow_dispatch) ;;
  *) refuse "event ${GITHUB_EVENT_NAME:-unknown}" ;;
esac
printf 'runner-job-guard: allowed %s (%s)\n' "$GITHUB_WORKFLOW_REF" "$GITHUB_EVENT_NAME"
