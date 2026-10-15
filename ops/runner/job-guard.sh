#!/usr/bin/env bash
# Job-started hook of the Jetson runner (ACTIONS_RUNNER_HOOK_JOB_STARTED, set in the image, so no
# change to the repository can lift it). The runner has the host Docker socket, which is root on
# the Jetson: it runs two workflows only, deploy.yml and ops.yml (the operations without ssh, owner
# decision of 2026-10-15) as defined on main, and only for the event they use, a dispatch (issue
# #116: it no longer follows a CI run). Any other job (a pull request, a branch, another workflow,
# another repository) fails here, before its first step runs. Exit 1 refuses the job. A change of
# this list reaches the runner only when its image is built again: ./deploy.sh runner install.
set -euo pipefail

REPO="GoldLegendW80/jobapp"
ALLOWED=("$REPO/.github/workflows/deploy.yml@refs/heads/main" "$REPO/.github/workflows/ops.yml@refs/heads/main")

refuse() {
  printf 'runner-job-guard: refused: %s\n' "$*" >&2
  printf 'runner-job-guard: this runner only runs %s on workflow_dispatch\n' "${ALLOWED[*]}" >&2
  exit 1
}

[[ "${GITHUB_REPOSITORY:-}" == "$REPO" ]] || refuse "repository ${GITHUB_REPOSITORY:-unknown}"
allowed=0
for workflow in "${ALLOWED[@]}"; do
  [[ "${GITHUB_WORKFLOW_REF:-}" != "$workflow" ]] || allowed=1
done
((allowed)) || refuse "workflow ${GITHUB_WORKFLOW_REF:-unknown}"
case "${GITHUB_EVENT_NAME:-}" in
  workflow_dispatch) ;;
  *) refuse "event ${GITHUB_EVENT_NAME:-unknown}" ;;
esac
printf 'runner-job-guard: allowed %s (%s)\n' "$GITHUB_WORKFLOW_REF" "$GITHUB_EVENT_NAME"
