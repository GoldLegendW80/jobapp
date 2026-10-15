#!/usr/bin/env bash
# The operations of .github/workflows/ops.yml, run on the Jetson's runner (owner decision of
# 2026-10-15: operate production through GitHub, no ssh). Each action is one of a closed list,
# mapped to the script that already does it in the deploy directory; nothing here takes a command
# or a free argument. ops/remote.sh dispatches the workflow from any laptop and prints the lines.
#
#   ops/jetson-ops.sh <action>          in JOBAPP_DIR (default ~/jobapp); --list prints the actions
#
#   status             ops/jetson-verify.sh: services, health, alembic, memory, error lines, browser
#   backup             ops/db.sh backup into BACKUP_DIR (default ~/jobapp-backups), the nightly
#                      folder, with its retention; db.sh's own lock keeps it apart from another
#                      backup or restore (a deploy's predeploy backup included)
#   backups            the count of dumps under BACKUP_DIR (deploys/ included) and the newest name
#   apply-status       python -m app.apply.admin status in the api container (apply A6)
#   apply-pause        ... pause: every application submission stops at once
#   apply-resume       ... resume
#   egress-host-check  ops/browser-egress.sh host-check: the browser's host firewall as installed
#                      by the owner (sudo, by hand, once) is live and exact
#
# Output: the figures only, on one line `JOBAPP-OPS action=<action> ... result=ok|fail` (status
# prints its JOBAPP-VERIFY line first). What the scripts print themselves stays out of the log:
# the run log of this public repository is readable by anyone, so no log line, no path beyond a
# dump's name, no environment value. With GITHUB_STEP_SUMMARY set, the lines go to the job
# summary too.
#
# Exit status: 0 result=ok, 1 result=fail, 64 an action that is not in the list.
# Tests (tests/remote_ops_test.sh): COMPOSE, the compose command.
set -uo pipefail

PROG="ops/jetson-ops.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACTIONS="status backup backups apply-status apply-pause apply-resume egress-host-check"
COMPOSE="${COMPOSE:-docker compose -f compose.yml --profile tunnel}"
export COMPOSE

usage_error() {
  printf '%s: %s\nusage: %s <action>, one of: %s\n' "$PROG" "$1" "$PROG" "$ACTIONS" >&2
  exit 64
}
(($# == 1)) || usage_error "one action"
ACTION="$1"
if [[ "$ACTION" == --list ]]; then
  # shellcheck disable=SC2086 # one action per line: the list is split on purpose
  printf '%s\n' $ACTIONS
  exit 0
fi
[[ " $ACTIONS " == *" $ACTION "* ]] || usage_error "unknown action"

DIR="${JOBAPP_DIR:-$HOME/jobapp}"
BACKUPS="${BACKUP_DIR:-$HOME/jobapp-backups}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# finish <result> [key=value ...]: the one line, the summary, the exit status.
finish() {
  local result="$1" line
  shift
  line="JOBAPP-OPS action=$ACTION${*:+ $*} result=$result"
  printf '%s\n' "$line"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    # shellcheck disable=SC2016 # Markdown backticks, not a command
    printf '### ops: %s\n\n`%s`\n\n' "$ACTION" "$line" >>"$GITHUB_STEP_SUMMARY"
  fi
  [[ "$result" == ok ]]
  exit
}
# word <text>: one token of the line: letters, digits and . _ : - only, else `-`.
word() { if [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; then printf '%s' "$1"; else printf -- '-'; fi; }
# count <regex>: lines of what the script printed that match, counted, never shown.
count() { grep -cE "$1" "$TMP" || true; }

cd "$DIR" 2>/dev/null || finish fail reason=no-deploy-dir
[[ -f compose.yml ]] || finish fail reason=nothing-deployed

case "$ACTION" in
status)
  bash "$HERE/jetson-verify.sh" "$DIR"
  status=$?
  if ((status == 0)); then finish ok; else finish fail; fi
  ;;
backup)
  [[ -f ops/db.sh ]] || finish fail reason=no-db-script
  status=0
  JOBAPP_DIR="$DIR" BACKUP_DIR="$BACKUPS" bash ops/db.sh backup >"$TMP" 2>&1 || status=$?
  dump="$(sed -n 's/^backup=//p' "$TMP" | tail -n 1)"
  figures="warnings=$(count 'WARNING') errors=$(count 'ERROR|db.sh: ')"
  if ((status)) || [[ -z "$dump" || ! -f "$dump" ]]; then
    finish fail "exit=$status $figures"
  fi
  finish ok "dump=$(word "$(basename "$dump")") bytes=$(wc -c <"$dump" | tr -d ' ') $figures"
  ;;
backups)
  [[ -d "$BACKUPS" ]] || finish ok "dumps=0 newest=-"
  find "$BACKUPS" -maxdepth 2 -type f -name '*.dump' >"$TMP" 2>/dev/null
  n="$(grep -c . "$TMP" || true)"
  newest="$(sed 's|.*/||' "$TMP" | sort | tail -n 1)"
  finish ok "dumps=$n newest=$(word "${newest:--}")"
  ;;
apply-status | apply-pause | apply-resume)
  status=0
  # shellcheck disable=SC2086 # COMPOSE is a command line
  $COMPOSE exec -T api python -m app.apply.admin "${ACTION#apply-}" </dev/null >"$TMP" 2>/dev/null || status=$?
  state="$(sed -n 's/^apply submissions: \([A-Za-z]*\)$/\1/p' "$TMP" | tail -n 1)"
  if ((status == 0)) && [[ "$state" == paused || "$state" == running ]]; then
    finish ok "apply=$state"
  fi
  finish fail "apply=$(word "${state:--}") exit=$status"
  ;;
egress-host-check)
  [[ -f ops/browser-egress.sh ]] || finish fail reason=no-egress-script
  if bash ops/browser-egress.sh host-check >"$TMP" 2>&1; then
    finish ok egress=ok
  fi
  finish fail egress=fail
  ;;
esac
