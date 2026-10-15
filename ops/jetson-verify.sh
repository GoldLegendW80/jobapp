#!/usr/bin/env bash
# The state of the stack on the Jetson as figures, read on the Jetson itself: the last step of
# .github/workflows/deploy.yml and the `status` action of .github/workflows/ops.yml run it on the
# Jetson's runner, so that nobody needs ssh to know whether production is well (owner decision of
# 2026-10-15). ops/ship.sh and ops/deploy-checked.sh read its line from the run's log
# (gh run view <id> --log), ops/remote.sh status prints it.
#
#   ops/jetson-verify.sh [<deploy dir>]      default JOBAPP_DIR, else ~/jobapp
#
# One line on stdout, always the same keys in the same order (`-` when a figure cannot be read):
#   JOBAPP-VERIFY services=8 healthy=8 missing=0 unhealthy=0 alembic=0018 head=1 mem_used=2300
#     mem_total=7620 errors=0 browser=off commit=0123456789ab unwell=- absent=- result=ok
# services  the services of the compose file with the tunnel profile (the deploy's own files and
#           profile: docker compose -f compose.yml --profile tunnel), each looked up in ps -a so a
#           stopped container shows; healthy: running and healthy; missing: no container;
#           unhealthy: a container that is not running and healthy (names and states in unwell=,
#           names of the missing ones in absent=, comma separated)
# alembic   the revision `alembic current` prints in the api container; head=1 when it is (head)
# mem_*     MB used and total, from /proc/meminfo (used is total minus available)
# errors    lines of the last 10 minutes of the compose logs at JSON level error or critical (the
#           api and the worker, api/app/logging_config.py, and caddy), or holding " error " or
#           "traceback" (any case); counted, never printed
# browser   off when it has no container (its profile is not on), else its state: healthy, or the
#           state and health docker reports. A running browser that is not healthy is a failure;
#           an exited one is not (it stays down after a reboot until the next deploy, owner decision
#           of 2026-10-14)
# commit    the first 12 characters of .deployed-commit
# result    ok, or fail when any figure above is wrong or unread
# Only these figures: no log line, no environment value, no host name. With GITHUB_STEP_SUMMARY
# set, the same figures go to the job summary as a table.
#
# Exit status: 0 result=ok, 1 result=fail, 64 wrong arguments.
# Tests (tests/remote_ops_test.sh): COMPOSE (the compose command) and JETSON_MEMINFO.
set -uo pipefail # not -e: every figure is read, whatever the one before it found

PROG="ops/jetson-verify.sh"
COMPOSE="${COMPOSE:-docker compose -f compose.yml --profile tunnel}"
MEMINFO="${JETSON_MEMINFO:-/proc/meminfo}"

case "${1:-}" in
-h | --help)
  sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
  ;;
-*)
  printf '%s: unknown argument %s\nusage: %s [<deploy dir>]\n' "$PROG" "$1" "$PROG" >&2
  exit 64
  ;;
esac
(($# <= 1)) || {
  printf 'usage: %s [<deploy dir>]\n' "$PROG" >&2
  exit 64
}
DIR="${1:-${JOBAPP_DIR:-$HOME/jobapp}}"

# word <text>: the text as one token of the line: letters, digits and . _ : - only, else `-`.
word() { if [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; then printf '%s' "$1"; else printf -- '-'; fi; }
# compose <args...>: the deploy's compose command in the deploy directory, never reading stdin.
compose() {
  # shellcheck disable=SC2086 # COMPOSE is a command line
  $COMPOSE "$@" </dev/null
}

services=0 healthy=0 missing=0 unhealthy=0
alembic="-" head=0 mem_used="-" mem_total="-" errors="-" browser="off" commit="-"
unwell=() absent=()
problems=0

if ! cd "$DIR" 2>/dev/null; then
  problems=1
else
  declared="$(compose config --services 2>/dev/null)"
  # Both profiles in ps, so the browser's container shows when it exists; ps -a, so a stopped one does.
  ps="$(compose --profile browser ps -a --format '{{.Service}}	{{.State}}	{{.Health}}' 2>/dev/null)"
  while IFS= read -r svc; do
    [[ -n "$svc" ]] || continue
    services=$((services + 1))
    row="$(awk -F '\t' -v s="$svc" '$1 == s { print; exit }' <<<"$ps")"
    if [[ -z "$row" ]]; then
      missing=$((missing + 1))
      absent+=("$(word "$svc")")
      continue
    fi
    IFS=$'\t' read -r _ state health <<<"$row"
    if [[ "$state" == running && "$health" == healthy ]]; then
      healthy=$((healthy + 1))
    else
      unhealthy=$((unhealthy + 1))
      unwell+=("$(word "$svc"):$(word "${health:-$state}")")
    fi
  done <<<"$declared"
  ((services > 0)) || problems=1

  row="$(awk -F '\t' '$1 == "browser" { print; exit }' <<<"$ps")"
  if [[ -n "$row" ]]; then
    IFS=$'\t' read -r _ state health <<<"$row"
    if [[ "$state" == running && "$health" == healthy ]]; then
      browser=healthy
    elif [[ "$state" == running ]]; then
      browser="$(word "running:${health:-none}")"
      problems=1
    else
      browser="$(word "${state:-unknown}")"
    fi
  fi

  current="$(compose exec -T api alembic current 2>/dev/null)"
  rev="$(sed -n 's/^\([0-9A-Za-z_][0-9A-Za-z_]*\) (head).*/\1/p' <<<"$current" | head -n 1)"
  if [[ -n "$rev" ]]; then
    alembic="$rev"
    head=1
  else
    rev="$(sed -n 's/^\([0-9A-Za-z_][0-9A-Za-z_]*\)\( (.*\)\{0,1\}$/\1/p' <<<"$current" | tail -n 1)"
    [[ -z "$rev" ]] || alembic="$rev"
  fi

  logs="$(mktemp)"
  if compose logs --no-color --since 10m >"$logs" 2>&1; then
    errors="$(grep -ciE '"level": ?"(error|critical)"| error |traceback' "$logs")"
  fi
  rm -f "$logs"

  deployed="$(head -c 40 .deployed-commit 2>/dev/null)"
  [[ ! "$deployed" =~ ^[0-9a-f]{12} ]] || commit="${deployed:0:12}"
fi

total_kb="$(awk '$1 == "MemTotal:" { print $2; exit }' "$MEMINFO" 2>/dev/null)"
avail_kb="$(awk '$1 == "MemAvailable:" { print $2; exit }' "$MEMINFO" 2>/dev/null)"
if [[ "$total_kb" =~ ^[0-9]+$ && "$avail_kb" =~ ^[0-9]+$ ]]; then
  mem_total=$((total_kb / 1024))
  mem_used=$(((total_kb - avail_kb) / 1024))
fi

((unhealthy == 0 && missing == 0 && head == 1)) || problems=1
[[ "$mem_used" != - && "$errors" == 0 ]] || problems=1
result=ok
((problems == 0)) || result=fail

joined() { local IFS=,; if (($#)); then printf '%s' "$*"; else printf -- '-'; fi; }
line="JOBAPP-VERIFY services=$services healthy=$healthy missing=$missing unhealthy=$unhealthy"
line+=" alembic=$alembic head=$head mem_used=$mem_used mem_total=$mem_total errors=$errors"
line+=" browser=$browser commit=$commit unwell=$(joined ${unwell[@]+"${unwell[@]}"})"
line+=" absent=$(joined ${absent[@]+"${absent[@]}"}) result=$result"
printf '%s\n' "$line"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    printf '### The Jetson: %s\n\n| figure | value |\n|---|---|\n' "$result"
    for pair in ${line#JOBAPP-VERIFY }; do
      printf '| %s | %s |\n' "${pair%%=*}" "${pair#*=}"
    done
    printf '\n'
  } >>"$GITHUB_STEP_SUMMARY"
fi
[[ "$result" == ok ]]
