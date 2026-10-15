#!/usr/bin/env bash
# Operate the Jetson from any laptop with gh only, no ssh (owner decision of 2026-10-15): this
# dispatches .github/workflows/ops.yml on the Jetson's runner, waits for that run and prints the
# figures it printed.
#
#   ops/remote.sh <action>       one of: status backup backups apply-status apply-pause
#                                apply-resume egress-host-check (ops/jetson-ops.sh says what each does)
#   ops/remote.sh --list         the actions, one per line
#
# Needs gh signed in with the scope `workflow` and an account with write access to the
# repository (ops/dev-setup.sh checks the scope). The run is told apart from anyone else's by its
# id: the newest dispatch of ops.yml by this gh account that was not there before the dispatch.
# Output: `run <id>: <url>`, then the run's JOBAPP-VERIFY and JOBAPP-OPS lines (figures only,
# read from gh run view <id> --log, nothing else of the log), then `REMOTE: OK <action>` or
# `REMOTE: FAIL <action> <why>`.
#
# Exit status: 0 the run succeeded and said result=ok, 1 it did not (or it could not be started or
# found), 64 wrong arguments.
# Tests (tests/remote_ops_test.sh): REMOTE_POLL_SECONDS (default 2), the wait between two looks
# for the run.
set -euo pipefail

PROG="ops/remote.sh"
WORKFLOW="ops.yml"
# The same list as the choice of ops.yml and the case of ops/jetson-ops.sh (the test checks all three).
ACTIONS="status backup backups apply-status apply-pause apply-resume egress-host-check"
POLL_SECONDS="${REMOTE_POLL_SECONDS:-2}"
# The lines of ops/jetson-ops.sh and ops/jetson-verify.sh, matched exactly.
LINE_RE='JOBAPP-(VERIFY|OPS) [a-z_]+=[A-Za-z0-9_.:,-]+( [a-z_]+=[A-Za-z0-9_.:,-]+)*'

usage_error() {
  printf '%s: %s\nusage: %s <action> | --list\nactions: %s\n' "$PROG" "$1" "$PROG" "$ACTIONS" >&2
  exit 64
}
ACTION=""
case "${1:-}" in
-h | --help)
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
  ;;
--list)
  # shellcheck disable=SC2086 # one action per line: the list is split on purpose
  printf '%s\n' $ACTIONS
  exit 0
  ;;
"") usage_error "name an action" ;;
esac
(($# == 1)) || usage_error "one action"
ACTION="$1"
[[ " $ACTIONS " == *" $ACTION "* ]] || usage_error "unknown action: $ACTION"

fail() {
  printf 'REMOTE: FAIL %s %s\n' "$ACTION" "$*"
  exit 1
}
command -v gh >/dev/null || fail "gh is not installed"
cd "$(dirname "$0")/.." # gh finds the repository from here

login="$(gh api user --jq .login 2>/dev/null)" || fail "gh is not signed in (gh auth login -h github.com -s workflow)"
ids() { # the ids of the latest dispatches of ops.yml by this account, newest first
  gh run list --workflow "$WORKFLOW" --event workflow_dispatch --user "$login" --limit 20 \
    --json databaseId --jq '.[].databaseId'
}
before="$(ids 2>/dev/null)" || fail "gh cannot list the runs of $WORKFLOW, nothing was dispatched"
before=" $(tr '\n' ' ' <<<"$before") "
gh workflow run "$WORKFLOW" --ref main -f "action=$ACTION" >/dev/null 2>&1 ||
  fail "the dispatch of $WORKFLOW failed (write access to the repository and the gh scope workflow are needed)"

run=""
for _ in $(seq 1 30); do
  for id in $(ids 2>/dev/null || true); do
    if [[ "$before" != *" $id "* ]]; then
      run="$id"
      break
    fi
  done
  [[ -z "$run" ]] || break
  sleep "$POLL_SECONDS"
done
[[ -n "$run" ]] || fail "the run of the dispatch did not appear (gh run list --workflow $WORKFLOW): look before dispatching again"
printf 'run %s: %s\n' "$run" "$(gh run view "$run" --json url --jq .url 2>/dev/null || echo "gh run view $run")"

gh run watch "$run" --exit-status >/dev/null 2>&1 || true
conclusion="$(gh run view "$run" --json conclusion --jq .conclusion 2>/dev/null || echo unknown)"
# The log of a run that just ended can take a few seconds to be published: a few tries.
lines=""
for _ in 1 2 3 4 5; do
  if log="$(gh run view "$run" --log 2>/dev/null)"; then
    lines="$(grep -oE "$LINE_RE" <<<"$log" || true)"
    break
  fi
  sleep "$POLL_SECONDS"
done
log=""
[[ -z "$lines" ]] || printf '%s\n' "$lines"

if [[ "$conclusion" != success ]]; then
  [[ -n "$lines" ]] ||
    fail "the run ended $conclusion with no figures: gh run view $run --log-failed (a 'runner-job-guard: refused' there means the Jetson's runner predates ops.yml: README, Deploy from any machine)"
  fail "the run ended $conclusion"
fi
grep -qE "^JOBAPP-OPS action=$ACTION( .*)? result=ok$" <<<"$lines" || fail "the run printed no result=ok for $ACTION"
printf 'REMOTE: OK %s\n' "$ACTION"
