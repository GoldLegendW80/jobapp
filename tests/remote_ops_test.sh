#!/usr/bin/env bash
# Tests of operating the Jetson without ssh (owner decision of 2026-10-15): ops/jetson-verify.sh
# (the figures of the stack) and ops/jetson-ops.sh (the actions of ops.yml) against a fake docker
# and fake deployed scripts, ops/remote.sh (the dispatch from a laptop) against a fake gh, and
# what .github/workflows/ops.yml and deploy.yml must keep saying. Nothing reaches GitHub, Docker
# or the Jetson. A planted token stands for every log line and secret: it must never come out.
#
#   tests/remote_ops_test.sh
# shellcheck disable=SC2016 # single quoted on purpose: workflow text, planted commands, fake scripts
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
cd "$ROOT"

passed=0
ok() {
  passed=$((passed + 1))
  printf 'ok   %s\n' "$*"
}
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}
OUT="$WORK/out"
run() { # run <expected exit> <command ...>: output in $OUT
  local want="$1" got=0
  shift
  "$@" >"$OUT" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "exit $got, expected $want: $* -- $(tail -c 1500 "$OUT")"
}
out_has() { grep -qF -- "$1" "$OUT" || fail "output lacks '$1': $(tail -c 1500 "$OUT")"; }
out_lacks() { ! grep -qF -- "$1" "$OUT" || fail "output holds '$1': $(tail -c 1500 "$OUT")"; }
line_is() { # line_is <n> <text>: line n of the output, exactly
  [[ "$(sed -n "${1}p" "$OUT")" == "$2" ]] || fail "line $1 is '$(sed -n "${1}p" "$OUT")', expected '$2'"
}
CANARY="canary$RANDOM$RANDOM$RANDOM"
export CANARY
mkdir -p "$WORK/bin"
export PATH="$WORK/bin:$PATH"
export STATE="$WORK/state"

# --- the fake docker of the Jetson ------------------------------------------------------------------
# Only the deploy's compose command (-f compose.yml --profile tunnel) is answered; anything else is
# recorded in $STATE/unexpected and fails. Files of $STATE: services, ps, alembic, logs (absent:
# the command fails), apply (the state the admin command prints).
cat >"$WORK/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STATE/docker.log"
C="compose -f compose.yml --profile tunnel"
answer() { if [[ -f "$STATE/$1" ]]; then cat "$STATE/$1"; else exit 1; fi; }
case "$*" in
"$C config --services") answer services ;;
"$C --profile browser ps -a --format {{.Service}}	{{.State}}	{{.Health}}") answer ps ;;
"$C exec -T api alembic current")
  printf 'INFO  [alembic.runtime.migration] Context impl PostgresqlImpl. %s\n' "$CANARY" >&2
  printf '/app/.venv/lib/python3.12/site-packages/x.py:1: SAWarning: a warning on stdout\n'
  answer alembic
  ;;
"$C logs --no-color --since 10m") answer logs ;;
"$C exec -T api python -m app.apply.admin "*)
  printf 'apply %s\n' "${@: -1}" >>"$STATE/apply.log"
  printf 'connecting with %s\n' "$CANARY" >&2
  printf 'apply submissions: %s\n' "$(cat "$STATE/apply")"
  ;;
*)
  printf '%s\n' "$*" >>"$STATE/unexpected"
  exit 1
  ;;
esac
EOF
chmod +x "$WORK/bin/docker"

APP="$WORK/app"
fresh() { # a deployed tree in $APP with a well stack, and a fresh $STATE
  rm -rf "$STATE" "$APP" "$WORK/backups"
  mkdir -p "$STATE" "$APP/ops" "$WORK/backups"
  : >"$APP/compose.yml"
  printf '0123456789abcdef0123456789abcdef01234567\n' >"$APP/.deployed-commit"
  printf '%s\n' caddy tailscale api worker parser renderer db redis >"$STATE/services"
  printf '%s\trunning\thealthy\n' api worker caddy db redis parser renderer tailscale >"$STATE/ps"
  printf '0018 (head)\n' >"$STATE/alembic"
  {
    printf 'api-1  | INFO started %s\nworker-1  | errors handled: none\n' "$CANARY"
    printf 'api-1  | {"ts": "2026-10-14T10:00:00.000+00:00", "level": "info", "logger": "app", "msg": "3 errors retried"}\n'
    printf 'caddy-1  | {"level":"info","ts":1760436000.1,"logger":"http.log.access","msg":"handled request"}\n'
  } >"$STATE/logs"
  printf 'MemTotal:        7802880 kB\nMemFree:          512000 kB\nMemAvailable:    5447680 kB\n' >"$STATE/meminfo"
  echo paused >"$STATE/apply"
}
export JETSON_MEMINFO="$STATE/meminfo"
WELL="JOBAPP-VERIFY services=8 healthy=8 missing=0 unhealthy=0 alembic=0018 head=1 mem_used=2300 mem_total=7620 errors=0 browser=off commit=0123456789ab unwell=- absent=- result=ok"

# === ops/jetson-verify.sh ===========================================================================
fresh
export GITHUB_STEP_SUMMARY="$WORK/summary.md"
: >"$GITHUB_STEP_SUMMARY"
run 0 ops/jetson-verify.sh "$APP"
[[ "$(cat "$OUT")" == "$WELL" ]] || fail "a well stack: $(cat "$OUT")"
grep -qx '| services | 8 |' "$GITHUB_STEP_SUMMARY" || fail "summary: $(cat "$GITHUB_STEP_SUMMARY")"
grep -qx '### The Jetson: ok' "$GITHUB_STEP_SUMMARY" || fail "summary title: $(cat "$GITHUB_STEP_SUMMARY")"
! grep -qF "$CANARY" "$GITHUB_STEP_SUMMARY" || fail "the summary holds a log line"
out_lacks "$CANARY"
[[ ! -e "$STATE/unexpected" ]] || fail "docker called without the deploy's compose files: $(cat "$STATE/unexpected")"
unset GITHUB_STEP_SUMMARY
run 0 env JOBAPP_DIR="$APP" ops/jetson-verify.sh
[[ "$(cat "$OUT")" == "$WELL" ]] || fail "JOBAPP_DIR: $(cat "$OUT")"
ok "a well stack: one line of figures (8 services, alembic at head, memory from MemAvailable, 0 error lines, no browser), the job summary table, no log line"

fresh
printf 'api\trunning\thealthy\nworker\texited\t\ncaddy\trestarting\t\nparser\trunning\tunhealthy\n' >"$STATE/ps"
printf '%s\n' caddy tailscale api worker parser >"$STATE/services"
printf '0017_old\n' >"$STATE/alembic"
{
  printf 'api-1  | {"ts": "2026-10-14T10:00:00.000+00:00", "level": "error", "logger": "app", "msg": "%s"}\n' "$CANARY"
  printf 'worker-1  | {"ts": "2026-10-14T10:00:01.000+00:00", "level": "critical", "logger": "arq", "msg": "%s"}\n' "$CANARY"
  printf 'caddy-1  | {"level":"error","ts":1760436000.1,"logger":"http.log.error","msg":"%s"}\n' "$CANARY"
  printf 'api-1  | {"ts": "2026-10-14T10:00:02.000+00:00", "level": "info", "logger": "app", "msg": "errors: none, error_rate 0"}\n'
  printf 'api-1  | Traceback (most recent call last):\nworker-1  | a error here\n'
} >"$STATE/logs"
run 1 ops/jetson-verify.sh "$APP"
[[ "$(cat "$OUT")" == "JOBAPP-VERIFY services=5 healthy=1 missing=1 unhealthy=3 alembic=0017_old head=0 mem_used=2300 mem_total=7620 errors=5 browser=off commit=0123456789ab unwell=caddy:restarting,worker:exited,parser:unhealthy absent=tailscale result=fail" ]] ||
  fail "an unwell stack: $(cat "$OUT")"
out_lacks "$CANARY"
out_lacks "Traceback"
ok "an unwell stack: stopped, restarting, unhealthy and missing services, alembic behind, JSON and plain error lines counted, exit 1, figures only"

fresh
printf 'browser\trunning\thealthy\n' >>"$STATE/ps"
run 0 ops/jetson-verify.sh "$APP"
out_has " browser=healthy "
fresh
printf 'browser\texited\t\n' >>"$STATE/ps"
run 0 ops/jetson-verify.sh "$APP"
out_has " browser=exited "
fresh
printf 'browser\trunning\tunhealthy\n' >>"$STATE/ps"
run 1 ops/jetson-verify.sh "$APP"
out_has " browser=running:unhealthy "
out_has " result=fail"
fresh
printf 'browser\trunning\tstarting\n' >>"$STATE/ps"
run 1 ops/jetson-verify.sh "$APP"
out_has " browser=running:starting "
out_has " services=8 healthy=8 "
ok "the browser: off without a container, healthy, exited (down after a reboot, by decision) passes, running and not healthy fails; never counted with the 8"

fresh
rm "$STATE/services" "$STATE/ps" "$STATE/alembic" "$STATE/logs" "$STATE/meminfo"
rm "$APP/.deployed-commit"
run 1 ops/jetson-verify.sh "$APP"
[[ "$(cat "$OUT")" == "JOBAPP-VERIFY services=0 healthy=0 missing=0 unhealthy=0 alembic=- head=0 mem_used=- mem_total=- errors=- browser=off commit=- unwell=- absent=- result=fail" ]] ||
  fail "nothing readable: $(cat "$OUT")"
run 1 ops/jetson-verify.sh "$WORK/nowhere"
out_has "services=0 "
out_has " result=fail"
fresh
printf 'x;touch %s/pwned\trunning\tbad state\n' "$WORK" >>"$STATE/ps"
printf 'x;touch %s/pwned\n' "$WORK" >>"$STATE/services"
run 1 ops/jetson-verify.sh "$APP"
out_has " unwell=-:- "
[[ ! -e "$WORK/pwned" ]] || fail "a service name ran as a command"
ok "figures that cannot be read are - and fail; a name or a state that is not one plain token is printed as -"

run 64 ops/jetson-verify.sh --frobnicate
run 64 ops/jetson-verify.sh "$APP" extra
run 0 ops/jetson-verify.sh --help
out_has "JOBAPP-VERIFY services=8"
ok "jetson-verify.sh: wrong arguments exit 64, --help prints the header"

# === ops/jetson-ops.sh ==============================================================================
# The deployed scripts it calls, as fakes that print a planted secret among their lines.
fake_scripts() {
  cat >"$APP/ops/db.sh" <<'EOF'
#!/usr/bin/env bash
printf 'db.sh %s JOBAPP_DIR=%s BACKUP_DIR=%s\n' "$*" "$JOBAPP_DIR" "$BACKUP_DIR" >>"$STATE/db.log"
printf '   dumping with %s\n' "$CANARY"
[[ -z "${FAKE_DB_FAIL:-}" ]] || { echo "db.sh: pg_dump: $CANARY" >&2; exit 1; }
f="$BACKUP_DIR/jobapp-2026-10-15T100000.dump"
printf 'dump bytes\n' >"$f"
echo "   WARNING: something the owner should read"
echo "backup=$f"
EOF
  cat >"$APP/ops/browser-egress.sh" <<'EOF'
#!/usr/bin/env bash
printf 'egress %s\n' "$*" >>"$STATE/egress.log"
echo "rules: $CANARY"
[[ -z "${FAKE_EGRESS_FAIL:-}" ]]
EOF
}
OPS=(env JOBAPP_DIR="$APP" BACKUP_DIR="$WORK/backups" ops/jetson-ops.sh)

fresh
fake_scripts
export GITHUB_STEP_SUMMARY="$WORK/summary.md"
: >"$GITHUB_STEP_SUMMARY"
run 0 "${OPS[@]}" status
line_is 1 "$WELL"
line_is 2 "JOBAPP-OPS action=status result=ok"
grep -qF 'JOBAPP-OPS action=status result=ok' "$GITHUB_STEP_SUMMARY" || fail "the ops line is not in the summary"
unset GITHUB_STEP_SUMMARY
printf '0017_old\n' >"$STATE/alembic"
run 1 "${OPS[@]}" status
line_is 2 "JOBAPP-OPS action=status result=fail"
ok "status: the JOBAPP-VERIFY line, then result=ok; an unwell stack is result=fail, exit 1"

fresh
fake_scripts
run 0 "${OPS[@]}" backup
[[ "$(cat "$OUT")" == "JOBAPP-OPS action=backup dump=jobapp-2026-10-15T100000.dump bytes=11 warnings=1 errors=0 result=ok" ]] || fail "backup: $(cat "$OUT")"
grep -qx "db.sh backup JOBAPP_DIR=$APP BACKUP_DIR=$WORK/backups" "$STATE/db.log" || fail "db.sh call: $(cat "$STATE/db.log")"
out_lacks "$CANARY"
out_lacks "$WORK"
run 1 env FAKE_DB_FAIL=1 "${OPS[@]}" backup
[[ "$(cat "$OUT")" == "JOBAPP-OPS action=backup exit=1 warnings=0 errors=1 result=fail" ]] || fail "a failed backup: $(cat "$OUT")"
out_lacks "$CANARY"
run 0 "${OPS[@]}" backups
[[ "$(cat "$OUT")" == "JOBAPP-OPS action=backups dumps=1 newest=jobapp-2026-10-15T100000.dump result=ok" ]] || fail "backups: $(cat "$OUT")"
mkdir -p "$WORK/backups/deploys"
: >"$WORK/backups/deploys/jobapp-2026-10-16T010000-predeploy-abc.dump"
: >"$WORK/backups/notes.txt"
run 0 "${OPS[@]}" backups
out_has "dumps=2 newest=jobapp-2026-10-16T010000-predeploy-abc.dump"
run 0 env JOBAPP_DIR="$APP" BACKUP_DIR="$WORK/none" ops/jetson-ops.sh backups
out_has "dumps=0 newest=- result=ok"
ok "backup: db.sh backup in the deploy directory, the dump's name, size and the count of warnings only; a failure is exit 1 with no line of db.sh; backups counts the dumps (deploys/ too)"

fresh
fake_scripts
for action in status pause resume; do
  run 0 "${OPS[@]}" "apply-$action"
  [[ "$(cat "$OUT")" == "JOBAPP-OPS action=apply-$action apply=paused result=ok" ]] || fail "apply-$action: $(cat "$OUT")"
done
[[ "$(tr '\n' ' ' <"$STATE/apply.log")" == "apply status apply pause apply resume " ]] || fail "admin calls: $(cat "$STATE/apply.log")"
echo running >"$STATE/apply"
run 0 "${OPS[@]}" apply-status
out_has "apply=running result=ok"
echo UNREADABLE >"$STATE/apply"
run 1 "${OPS[@]}" apply-status
[[ "$(cat "$OUT")" == "JOBAPP-OPS action=apply-status apply=UNREADABLE exit=0 result=fail" ]] || fail "unreadable: $(cat "$OUT")"
out_lacks "$CANARY"
ok "apply-status, apply-pause, apply-resume: app.apply.admin in the api container, the state alone; UNREADABLE fails"

fresh
fake_scripts
run 0 "${OPS[@]}" egress-host-check
[[ "$(cat "$OUT")" == "JOBAPP-OPS action=egress-host-check egress=ok result=ok" ]] || fail "egress: $(cat "$OUT")"
grep -qx 'egress host-check' "$STATE/egress.log" || fail "egress call: $(cat "$STATE/egress.log")"
run 1 env FAKE_EGRESS_FAIL=1 "${OPS[@]}" egress-host-check
out_has "egress=fail result=fail"
out_lacks "$CANARY"
ok "egress-host-check: ops/browser-egress.sh host-check of the deployed tree, ok or fail, never its rules"

run 1 env JOBAPP_DIR="$WORK/nowhere" ops/jetson-ops.sh status
out_has "JOBAPP-OPS action=status reason=no-deploy-dir result=fail"
rm "$APP/compose.yml"
run 1 "${OPS[@]}" apply-status
out_has "reason=nothing-deployed result=fail"
fresh
run 1 "${OPS[@]}" backup
out_has "reason=no-db-script result=fail"
for bad in "" frobnicate "status;touch $WORK/pwned" '$(touch pwned)' "status extra" "STATUS"; do
  # shellcheck disable=SC2086 # "status extra" is two arguments on purpose
  run 64 env JOBAPP_DIR="$APP" ops/jetson-ops.sh $bad
done
run 64 env JOBAPP_DIR="$APP" ops/jetson-ops.sh "status;touch $WORK/pwned"
[[ ! -e "$WORK/pwned" && ! -e pwned ]] || fail "an action ran as a command"
ok "jetson-ops.sh: no deploy directory or nothing deployed fails with a reason; any action outside the list (empty, unknown, injected, two words, other case) exits 64"

# === ops/remote.sh ==================================================================================
# The fake gh: runs are ids in $STATE/runs (newest first); a dispatch adds one after
# $STATE/appear_after more listings. $STATE/conclusion, $STATE/log (the run's log as gh prints it),
# $STATE/dispatch_fail. Every call in $STATE/gh.log.
cat >"$WORK/bin/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STATE/gh.log"
case "$1 $2" in
"api user") echo e2e-tester ;;
"run list")
  [[ ! -e "$STATE/list_fail" ]] || exit 1
  if [[ -e "$STATE/pending" ]]; then
    left="$(cat "$STATE/appear_after" 2>/dev/null || echo 0)"
    if ((left > 0)); then
      echo $((left - 1)) >"$STATE/appear_after"
    else
      { cat "$STATE/pending"; cat "$STATE/runs"; } >"$STATE/runs.new"
      mv "$STATE/runs.new" "$STATE/runs"
      rm "$STATE/pending"
    fi
  fi
  cat "$STATE/runs"
  ;;
"workflow run")
  [[ ! -e "$STATE/dispatch_fail" ]] || exit 1
  [[ -e "$STATE/never_appears" ]] || echo 12 >"$STATE/pending"
  ;;
"run watch") exit 0 ;;
"run view")
  case "$*" in
  *"--json url"*) echo "https://github.com/example/jobapp/actions/runs/$3" ;;
  *"--json conclusion"*) cat "$STATE/conclusion" ;;
  *--log*) cat "$STATE/log" ;;
  *) exit 2 ;;
  esac
  ;;
*)
  printf 'unexpected %s\n' "$*" >>"$STATE/gh.log"
  exit 2
  ;;
esac
EOF
chmod +x "$WORK/bin/gh"
export REMOTE_POLL_SECONDS=0
gh_fresh() { # <conclusion> [<JOBAPP lines> ...]: the run 12 to come, its log with the step's own text
  rm -rf "$STATE"
  mkdir -p "$STATE"
  printf '11\n10\n' >"$STATE/runs"
  echo "$1" >"$STATE/conclusion"
  shift
  {
    printf 'ops on the Jetson\tRun the action\t2026-10-15T10:00:00.0Z ##[group]Run bash ops/jetson-ops.sh "$ACTION"\n'
    printf 'ops on the Jetson\tRun the action\t2026-10-15T10:00:00.1Z   ACTION: status\n'
    printf 'ops on the Jetson\tRun the action\t2026-10-15T10:00:00.2Z said %s\n' "$CANARY"
    for line in "$@"; do printf 'ops on the Jetson\tRun the action\t2026-10-15T10:00:01.0Z %s\r\n' "$line"; done
  } >"$STATE/log"
}

gh_fresh success "$WELL" "JOBAPP-OPS action=status result=ok"
echo 2 >"$STATE/appear_after"
run 0 ops/remote.sh status
line_is 1 "run 12: https://github.com/example/jobapp/actions/runs/12"
line_is 2 "$WELL"
line_is 3 "JOBAPP-OPS action=status result=ok"
line_is 4 "REMOTE: OK status"
out_lacks "$CANARY"
out_lacks "##[group]"
grep -qx "workflow run ops.yml --ref main -f action=status" "$STATE/gh.log" || fail "dispatch: $(cat "$STATE/gh.log")"
grep -q "^run list --workflow ops.yml --event workflow_dispatch --user e2e-tester " "$STATE/gh.log" || fail "the runs were not those of this account: $(cat "$STATE/gh.log")"
grep -qx "run watch 12 --exit-status" "$STATE/gh.log" || fail "the run was not waited for: $(cat "$STATE/gh.log")"
! grep -q "^unexpected\|run view 1[01]\|run watch 1[01]" "$STATE/gh.log" || fail "an older run was read: $(cat "$STATE/gh.log")"
ok "remote.sh status: dispatched on main with the action, the new run of this account found (after two listings), waited for, its figures printed alone, REMOTE: OK"

gh_fresh success "JOBAPP-OPS action=apply-pause apply=paused result=ok"
run 0 ops/remote.sh apply-pause
out_has "JOBAPP-OPS action=apply-pause apply=paused result=ok"
line_is 3 "REMOTE: OK apply-pause"
gh_fresh success "JOBAPP-OPS action=apply-status apply=paused result=ok"
run 1 ops/remote.sh apply-pause
out_has "REMOTE: FAIL apply-pause the run printed no result=ok for apply-pause"
gh_fresh failure "JOBAPP-OPS action=backup exit=1 warnings=0 errors=1 result=fail"
run 1 ops/remote.sh backup
out_has "JOBAPP-OPS action=backup exit=1 warnings=0 errors=1 result=fail"
out_has "REMOTE: FAIL backup the run ended failure"
gh_fresh failure
printf 'ops on the Jetson\tSet up job\t2026-10-15T10:00:00.0Z runner-job-guard: refused: workflow x\n' >"$STATE/log"
run 1 ops/remote.sh status
out_has "REMOTE: FAIL status the run ended failure with no figures: gh run view 12 --log-failed"
out_has "runner-job-guard: refused"
ok "a failed run prints its figures and FAIL; one with no figures points at the log and the runner guard; a result for another action is not a success"

gh_fresh success
touch "$STATE/dispatch_fail"
run 1 ops/remote.sh status
out_has "REMOTE: FAIL status the dispatch of ops.yml failed"
gh_fresh success
touch "$STATE/list_fail"
run 1 ops/remote.sh status
out_has "gh cannot list the runs of ops.yml, nothing was dispatched"
! grep -q '^workflow run' "$STATE/gh.log" || fail "dispatched without knowing the runs before"
gh_fresh success
touch "$STATE/never_appears"
run 1 ops/remote.sh status
out_has "the run of the dispatch did not appear"
! grep -q "run watch" "$STATE/gh.log" || fail "an older run was watched"
ok "a refused dispatch, runs that cannot be listed, a run that never appears: each FAIL, exit 1, no older run taken for ours"

for bad in frobnicate "status;id" STATUS "status extra"; do
  gh_fresh success
  # shellcheck disable=SC2086 # "status extra" is two arguments on purpose
  run 64 ops/remote.sh $bad
  [[ ! -s "$STATE/gh.log" ]] || fail "gh called for a refused action: $(cat "$STATE/gh.log")"
done
run 64 ops/remote.sh
run 0 ops/remote.sh --help
out_has "ops/remote.sh <action>"
ok "remote.sh: an action outside the list, none, or two exit 64 before any gh call; --help prints the header"

# === the lists and the workflows ====================================================================
choices="$(awk '/^        options:$/ { on = 1; next } on && /^          - / { print $2; next } on { exit }' .github/workflows/ops.yml | tr '\n' ' ')"
run 0 ops/remote.sh --list
remote_list="$(tr '\n' ' ' <"$OUT")"
run 0 ops/jetson-ops.sh --list
ops_list="$(tr '\n' ' ' <"$OUT")"
[[ "$choices" == "status backup backups apply-status apply-pause apply-resume egress-host-check " ]] || fail "ops.yml choices: $choices"
[[ "$remote_list" == "$choices" && "$ops_list" == "$choices" ]] ||
  fail "the actions differ: ops.yml '$choices', remote.sh '$remote_list', jetson-ops.sh '$ops_list'"
ok "ops.yml's choice, ops/remote.sh and ops/jetson-ops.sh name the same actions"

code() { grep -v '^ *#' "$@"; }
triggers() { awk '/^on:/ { on = 1; next } on && /^[^ #]/ { exit } on && /^  [a-z_]+:/ { print $1 }' "$1" | tr '\n' ' '; }
[[ "$(triggers .github/workflows/ops.yml)" == "workflow_dispatch: " ]] || fail "ops.yml is started by: $(triggers .github/workflows/ops.yml)"
[[ "$(grep -c '^    runs-on:' .github/workflows/ops.yml)" == 1 ]] || fail "ops.yml has more than one job"
grep -qx '    runs-on: \[self-hosted, jetson\]' .github/workflows/ops.yml || fail "ops.yml does not run on [self-hosted, jetson]"
grep -qx '        type: choice' .github/workflows/ops.yml || fail "the action of ops.yml is not a choice"
ops_code="$(code .github/workflows/ops.yml)"
if [[ "$(grep -c '\${{' <<<"$ops_code")" != 1 ]] || ! grep -qx '      ACTION: \${{ inputs.action }}' <<<"$ops_code"; then
  fail "ops.yml uses an expression outside the env of its job: $(code .github/workflows/ops.yml | grep '\${{')"
fi
grep -qx '          bash ops/jetson-ops.sh "\$ACTION"' .github/workflows/ops.yml || fail "ops.yml does not run ops/jetson-ops.sh with the action from the environment"
grep -qx '  contents: read' .github/workflows/ops.yml || fail "ops.yml asks for more than contents: read"
ok "ops.yml: dispatch only, one job on the Jetson, a closed choice that reaches the script through the environment only, contents: read"

grep -qx '        id: deploy' .github/workflows/deploy.yml || fail "the deploy step of deploy.yml has no id"
verify_step="$(awk '/^      - name: Verify the Jetson$/ { on = 1; print; next } on && /^      (- name:|# )/ { exit } on { print }' .github/workflows/deploy.yml)"
grep -qxF "        if: \${{ !cancelled() && steps.deploy.outcome == 'success' }}" <<<"$verify_step" || fail "the verify step's condition: $verify_step"
grep -qxF '        run: bash ops/jetson-verify.sh "$APP"' <<<"$verify_step" || fail "the verify step's command: $verify_step"
order="$(grep '^      - name: \(Deploy under the Jetson deploy lock\|Public URL\|Verify the Jetson\|State on failure\)$' .github/workflows/deploy.yml | sed 's/.*- name: //' | tr '\n' ',')"
[[ "$order" == "Deploy under the Jetson deploy lock,Public URL,Verify the Jetson,State on failure," ]] ||
  fail "deploy.yml step order: $order"
! grep -q 'JOBAPP-VERIFY' <<<"$verify_step" || fail "the verify step's own text holds the line a reader of the log looks for"
ok "deploy.yml: the verify step runs ops/jetson-verify.sh after the Public URL step, whenever the deploy step succeeded, and its text holds no JOBAPP-VERIFY"

printf '\nAll %d checks passed.\n' "$passed"
