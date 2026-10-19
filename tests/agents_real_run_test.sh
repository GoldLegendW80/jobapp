#!/usr/bin/env bash
# Local tests of tests/agents_real_run_jetson.sh against a fake ssh that plays the Jetson (review
# of PR #34). No Jetson, no model call, nothing remote: the fake ssh is first on the PATH and the
# host name does not resolve, so a call that escaped the fake would fail.
#
#   tests/agents_real_run_test.sh
#
# Covers: without OWNER, or with an OWNER that is not an e-mail address, nothing runs (here and in
# the four other Jetson scripts that put OWNER into SQL); in those five scripts the owner's address,
# refused or accepted, reaches no output and no file they write, even when the fake Jetson quotes
# it on stderr and in the refusals that used to name it (under bash -x: no file); a dry run leaves every file under
# tasks/evidence/agents-a5/ byte for byte as committed and starts nothing; a real run is refused
# before any ssh call when an evidence file it would write exists; an account that is not an
# admin with a subscription credential is refused before
# any start; the message text, a token shaped value and the text of a log message are counted and
# never printed; a short message is not a false failure; a start that gives no answer is reported
# as unknown, not as refused; the remote Python's stderr stays out of the log; the same kind
# twice keeps both sets of samples, and metrics.json holds both runs with their memory figures
# as JSON numbers under a decimal comma locale; metrics that do not read as JSON fail the call; a
# proposal decided meanwhile fails the call.
# Needs jq.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SCRIPT="tests/agents_real_run_jetson.sh"
COMMITTED="tasks/evidence/agents-a5"
JOB="280af3db-5fae-4219-9300-cd65035fdbd4"
RUN="11111111-1111-4111-8111-111111111111"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

passed=0
step() { printf '\n== %s\n' "$*"; }
ok() {
  passed=$((passed + 1))
  printf '   ok: %s\n' "$*"
}
fail() {
  printf '   FAIL: %s\n' "$*" >&2
  exit 1
}

# run <expected exit> <cmd...>: run, keep stdout+stderr in $WORK/out, check the exit code (pipefail)
run() {
  local want="$1" got=0
  shift
  # Through a pipe: the script's own tee has ended, and its log is whole, when cat sees the end.
  "$@" 2>&1 | cat >"$WORK/out" || got=$?
  [[ "$got" == "$want" ]] || fail "$* exited $got, expected $want: $(tail -c 600 "$WORK/out")"
}
out_has() { grep -q -F -- "$1" "$WORK/out" || fail "output lacks '$1': $(tail -c 600 "$WORK/out")"; }
out_lacks() { ! grep -q -i -F -- "$1" "$WORK/out" || fail "output holds '$1'"; }
calls() { wc -l <"$CALLS" | tr -d ' '; }
starts() { grep -c -F 'exec -T api python' "$CALLS" || true; }
sums() { # the checksum of every committed evidence file, with its path
  local tool=(shasum -a 256)
  if command -v sha256sum >/dev/null; then tool=(sha256sum); fi
  find "$COMMITTED" -type f | LC_ALL=C sort | while IFS= read -r path; do "${tool[@]}" "$path"; done
}
reset() { # a fresh fake Jetson: no call recorded, no run started
  rm -rf "$STATE"
  mkdir -p "$STATE"
  : >"$CALLS"
}

command -v jq >/dev/null || fail "jq is needed"
[[ -x "$SCRIPT" ]] || fail "$SCRIPT is not executable"

# The fake Jetson. Every call is recorded; an unknown command fails loudly.
BIN="$WORK/bin"
STATE="$WORK/state"
CALLS="$WORK/calls"
mkdir -p "$BIN"
cat >"$BIN/ssh" <<'FAKE'
#!/usr/bin/env bash
# ssh -o BatchMode=yes <host> <command...>
set -euo pipefail
printf '%s\n' "$*" >>"$STUB_CALLS"
cmd="${*:4}"
# STUB_ECHO: as hostile as a remote error, which may quote its command line and its SQL on stderr.
if [[ -n "${STUB_ECHO:-}" && "$cmd" == *@* ]]; then printf 'bash: line 1: %s\n' "$cmd" >&2; fi
labels() { grep -o "'[a-z_0-9]*='" <<<"$1" | tr -d "'" || true; }
count() { if [[ -f "$STUB_STATE/$1" ]]; then cat "$STUB_STATE/$1"; else echo 0; fi; }
bump() { echo $(($(count "$1") + 1)) >"$STUB_STATE/$1"; }
case "$cmd" in
  *"bash -s --"*) # the memory sampler: three samples, then the end
    cat >/dev/null
    for i in 1 2 3; do
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$((1760000000 + i))" 150994944 123731968 5200 0 0 0
    done
    ;;
  *"exec -T api python -c"*) # a program of tests/ingestion_transcribe_jetson.sh, told by its text
    program="$(cat)"
    case "$program" in
      *has_consented*)
        printf '{"agent": true, "user": %s, "role": "admin", "credential": "claude_subscription", "consented": %s, "ingestion_run": %s, "agent_runs": 0}\n' \
          "$([[ "${STUB_ACCOUNT:-}" == missing ]] && echo false || echo true)" \
          "$([[ "${STUB_CONSENT:-}" == no ]] && echo false || echo true)" \
          "$([[ "${STUB_BUSY:-}" == 1 ]] && echo true || echo false)"
        ;;
      *"def seed("*) printf '{"document_id": "%s", "agent_run_id": "%s", "enqueued": true, "run_status": "running"}\n' "$STUB_RUN" "$STUB_RUN" ;;
      *"def distance("*) echo '{"agent_status": "done", "document_status": "done", "images_left": 0, "items_stored": 0, "run_status": "done"}' ;;
      *"get_one(AgentRun"*) echo done ;;
      *)
        echo "fake ssh: unexpected program" >&2
        exit 97
        ;;
    esac
    ;;
  *"exec -T api python"*) # the start of a run
    cat >/dev/null
    case "${STUB_START:-ok}" in
      ok)
        bump starts
        printf '{"http": 202, "run_id": "%s", "enqueued": true}\n' "$STUB_RUN"
        ;;
      drop) exit 255 ;; # the connection dropped: no answer
      crash)
        printf 'Traceback (most recent call last):\n  input_value=%s\n' "$STUB_PRIVATE" >&2
        printf '{"http": 500, "error_class": "ValidationError"}\n'
        ;;
    esac
    ;;
  *"exec -T db sh -c"*)
    sql="$(cat)"
    if [[ -n "${STUB_ECHO:-}" ]]; then grep -F '@' <<<"$sql" | sed 's/^/LINE 1: /' >&2 || true; fi
    case "$sql" in
      *"'owner_role='"*)
        credential="${STUB_KIND:-claude_subscription},updated_at:2026-10-10"
        if [[ "${STUB_KIND:-}" == none ]]; then credential=none; fi
        printf 'users=2|owner_role=%s|jobs=3|per_status=found:3|fit_sum=240|max_updated_at=2026-10-10|rows_md5=aa|job_events=5,max_id:9|files=2|sweeps=1|profile_revision=6|profile_md5=bb|credential=%s\n' \
          "${STUB_ROLE:-admin}" "$credential"
        ;;
      *"'agent_runs='"*)
        bump snapshots
        decided=0
        if [[ "${STUB_DECIDE:-0}" == 1 && "$(count snapshots)" -ge 2 ]]; then decided=1; fi
        runs=$((2 + $(count starts)))
        printf 'agent_runs=%s|agent_runs_owner=%s|agent_runs_active=0|agent_events=34|proposals=1|proposals_owner=pending:1|proposals_decided=%s|agent_threads=1|agent_messages=2|credential_verified=true\n' \
          "$runs" "$runs" "$decided"
        ;;
      *"from jobs j where id"*) if [[ "${STUB_JOB:-}" != missing ]]; then echo 'shortlisted|100|3707|f|2|1|1'; fi ;;
      *"order by created_at desc limit 1"*) echo "$STUB_RUN" ;;
      *"select status, (select count"*) echo 'done|13' ;;
      *jsonb_build_object*)
        if [[ "${STUB_METRICS:-ok}" == bad ]]; then echo 'not JSON'; else printf '{"run_id": "%s", "status": "done", "events": 13}\n' "$STUB_RUN"; fi
        ;;
      # The SQL of tests/e2e_jetson.sh, e2e_jobs_jetson.sh and e2e_jobs_sweep_jetson.sh.
      *"'mac_ips='"*) echo 'email=0|mac_ips=0|all=0' ;;
      *"not success"*) echo 0 ;;
      *"e2e-%@example.org"*) labels "$sql" | sed 's/$/0/' | paste -s -d '|' - ;;
      *"'password_hash_md5='"* | *"'fits_manual='"*) # the owner's account or jobs: one of each, the address as stored
        if [[ "${STUB_ACCOUNT:-}" != missing ]]; then
          labels "$sql" | sed -e "s/^email=\$/email=${STUB_OWNER:-}/" -e 's/=$/=1/' | paste -s -d '|' -
        fi
        ;;
      *)
        echo "fake ssh: unexpected SQL" >&2
        exit 97
        ;;
    esac
    ;;
  *"ps --format"*) printf '%s:healthy\n' api caddy db redis tailscale worker ;;
  *".deployed-commit"*) echo e1883640f1ffe1883640f1ffe1883640f1ffe188 ;;
  *lock_first*) echo 4 ;;
  *"free -m"*) printf 'Mem: 7619 2055 100 10 500 5239\nSwap: 3809 263 3546\n' ;;
  *"docker stats"*) printf 'jobapp-worker-1 144MiB / 1.5GiB\njobapp-api-1 117MiB / 512MiB\n' ;;
  *memory.events*) printf 'oom 0\noom_kill 0\n' ;;
  *"date -u"*) echo 2026-10-10T12:00:00Z ;;
  *"ls -A /tmp"*) echo 0 ;;
  *"find / /tmp"*) echo 0 ;;
  *"logs --no-color"*)
    printf '{"ts": "t", "level": "info", "logger": "app.agents.runner", "msg": "agent run started", "run_id": "%s"}\n' "$STUB_RUN"
    printf '{"ts": "t", "level": "error", "logger": "app.agents.runner", "msg": "failed: %s", "run_id": "%s"}\n' "$STUB_PRIVATE" "$STUB_RUN"
    if [[ -n "${STUB_LOG_EXTRA:-}" ]]; then printf '%s\n' "$STUB_LOG_EXTRA"; fi
    ;;
  *)
    echo "fake ssh: unexpected command: $cmd" >&2
    exit 97
    ;;
esac
FAKE
cat >"$BIN/docker" <<'FAKE'
#!/usr/bin/env bash
printf 'local docker %s\n' "$*" >>"$STUB_CALLS"
exit 97
FAKE
# The local commands of the four other Jetson scripts: no journey, no address lookup, no Pillow.
cat >"$BIN/pnpm" <<'FAKE'
#!/usr/bin/env bash
printf 'local pnpm %s\n' "$*" >>"$STUB_CALLS"
echo "fake playwright: no test run"
FAKE
cat >"$BIN/curl" <<'FAKE'
#!/usr/bin/env bash
printf 'local curl %s\n' "$*" >>"$STUB_CALLS"
exit 6
FAKE
cat >"$BIN/uv" <<'FAKE'
#!/usr/bin/env bash
printf 'local uv %s\n' "$*" >>"$STUB_CALLS"
cat >/dev/null
echo '{"jpeg": "AAAA", "width": 1109, "height": 1568, "quality": 80, "text": "a made up line\nanother"}'
FAKE
chmod +x "$BIN/ssh" "$BIN/docker" "$BIN/pnpm" "$BIN/curl" "$BIN/uv"

PRIVATE="PRIVATE-TEXT-OF-AN-ERROR"
# script [VAR=value ...]: the script against the fake Jetson, the pauses cut to nothing
script() {
  env PATH="$BIN:$PATH" DEPLOY_HOST=jetson.invalid STUB_CALLS="$CALLS" STUB_STATE="$STATE" \
    STUB_RUN="$RUN" STUB_PRIVATE="$PRIVATE" IDLE_S=0 POLL_S=0 OWNER=e2e-owner@example.org "$@" \
    "$SCRIPT" "$JOB"
}
DEFAULT_FRAGMENT="e two strongest poin" # 20 characters of the default MESSAGE, from its 14th
grep -q -F -- "$DEFAULT_FRAGMENT" "$SCRIPT" || fail "the default MESSAGE of the script changed: update DEFAULT_FRAGMENT"

# bash 3.2 (macOS) runs the inherited EXIT trap when a background job ends, which removed the script's
# temporary directory under it whenever the sampler ended first (a failure under load only). The
# sampler is exec'd after the trap is cleared; a plain `ssh ... &` would bring the failure back.
grep -q -F 'trap - EXIT; exec ssh' "$SCRIPT" || fail "the background sampler of $SCRIPT keeps the EXIT trap: clear it (trap - EXIT) and exec ssh"
! grep -q -E '^[[:space:]]*ssh .*&$' "$SCRIPT" || fail "$SCRIPT starts ssh as a plain background job: it inherits the EXIT trap"

[[ -d "$COMMITTED" ]] || fail "$COMMITTED is missing"
sums >"$WORK/sums.before"
[[ -s "$WORK/sums.before" ]] || fail "$COMMITTED holds no file"
unchanged() {
  sums >"$WORK/sums.after"
  diff -u "$WORK/sums.before" "$WORK/sums.after" >&2 || fail "$1 changed a file under $COMMITTED"
}

step "B0: without OWNER the script stops before any call (it has no default address)"
reset
run 1 env -u OWNER PATH="$BIN:$PATH" DEPLOY_HOST=jetson.invalid STUB_CALLS="$CALLS" STUB_STATE="$STATE" \
  "$SCRIPT" "$JOB"
out_has "OWNER is not set: run it again with OWNER="
[[ "$(calls)" == 0 ]] || fail "a call was made without OWNER"
run 1 script OWNER=
out_has "OWNER is not set"
[[ "$(calls)" == 0 ]] || fail "a call was made with an empty OWNER"
ok "OWNER unset or empty: stopped with the reason, no call made"

step "B0b: an OWNER that is not an e-mail address stops every Jetson script that puts it into SQL"
OWNER_FORM='^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$'
OWNER_CHECK=" =~ $OWNER_FORM ]] || die \"OWNER is not an email address\""
OWNER_SCRIPTS=(tests/agents_real_run_jetson.sh tests/ingestion_transcribe_jetson.sh tests/e2e_jetson.sh
  tests/e2e_jobs_jetson.sh tests/e2e_jobs_sweep_jetson.sh)
for s in "${OWNER_SCRIPTS[@]}"; do
  grep -q -F -- "$OWNER_CHECK" "$s" || fail "$s does not check the form of OWNER with $OWNER_FORM"
done
for v in e2e-owner@example.org e2e-lucien.quillard@example.org e2e-owner+jetson@example.org \
  E2E-Owner@Example.ORG e2e_owner@mail.example.org 0@example.org; do
  [[ "$v" =~ $OWNER_FORM ]] || fail "the form refuses the address $v"
done
# Every command the scripts run is a fake that records the call: a refused value reaches none.
OBIN="$WORK/bin-owner"
mkdir -p "$OBIN"
cat >"$OBIN/fake" <<'FAKE'
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >>"$STUB_CALLS"
exit 97
FAKE
chmod +x "$OBIN/fake"
for c in ssh docker npx pnpm curl tailscale uv; do ln -s fake "$OBIN/$c"; done
# The planted token: every OWNER below carries it, and no output and no file of the scripts may.
TOKEN="Kq83zTn"
# Copies of the five scripts under a throwaway root, so whatever they write lands there and never
# under the committed tasks/evidence/; ./deploy.sh url answers an address that does not resolve.
TROOT="$WORK/root"
mkdir -p "$TROOT/tests"
for s in "${OWNER_SCRIPTS[@]}"; do cp -p "$s" "$TROOT/$s"; done
printf '#!/usr/bin/env bash\necho https://jetson.invalid\n' >"$TROOT/deploy.sh"
chmod +x "$TROOT/deploy.sh"
no_token_written() { # no file under the throwaway root holds the token
  local rc=0
  grep -r -i -l -F -- "$TOKEN" "$TROOT" >"$WORK/hits" || rc=$?
  [[ "$rc" == 1 ]] || fail "$1: the planted token is in a file the script wrote (grep exit $rc): $(cat "$WORK/hits")"
}
# shellcheck disable=SC2016,SC1003 # the values are literal, never expanded
REFUSED=(e2e-Kq83zTn @Kq83zTn.example.org e2e-Kq83zTn@ e2e-Kq83zTn@@example.org "e2e-Kq83zTn@example.org';--"
  "e2e-Kq83zTn'@example.org" "e2e Kq83zTn@example.org" " e2e-Kq83zTn@example.org" "e2e-Kq83zTn@example.org "
  $'e2e-Kq83zTn@example.org\nselect 1' $'e2e-Kq83zTn@example.org\t' "e2e-Kq83zTn@example.org;"
  'e2e-Kq83zTn$(id)@example.org' 'e2e-Kq83zTn`id`@example.org' 'e2e-Kq83zTn@example.org\' 'e2e-Kq83zTn@exa"mple.org'
  e2e-Kq83zTn@example.org% e2e-Kq83zTn@exa_mple.org "e2e-Kq83zTn@example,org")
for v in "${REFUSED[@]}"; do
  [[ "$v" == *"$TOKEN"* ]] || fail "the refused value '$v' does not carry the planted token"
done
for s in "${OWNER_SCRIPTS[@]}"; do
  for v in "${REFUSED[@]}"; do
    reset
    run 1 env PATH="$OBIN:$PATH" DEPLOY_HOST=jetson.invalid STUB_CALLS="$CALLS" OWNER="$v" "$TROOT/$s" "$JOB"
    out_has "OWNER is not an email address"
    out_lacks "$TOKEN"
    [[ "$(calls)" == 0 ]] || fail "$s made a call with the OWNER '$v': $(head -n 3 "$CALLS")"
    [[ ! -e "$TROOT/tasks" ]] || fail "$s wrote a file with the OWNER '$v': $(find "$TROOT/tasks" -type f | head -n 3)"
  done
done
ok "${#REFUSED[@]} malformed OWNER values, each with the planted token, stop each of the ${#OWNER_SCRIPTS[@]} scripts before any call: not printed, no file written"

step "B0c: an OWNER that passes the checks is never printed nor written, even where the Jetson quotes it"
# The mask below would hide a message that names the address: no output command may name it at all.
PRINTS_OWNER='(^[[:space:]]*|(\|\||&&|;)[[:space:]]*)(die|info|step|printf|echo) [^#]*[$][{]?(OWNER|HARALD)([^A-Za-z0-9_]|$)'
for s in "${OWNER_SCRIPTS[@]}"; do
  ! grep -n -E -- "$PRINTS_OWNER" "$s" >"$WORK/prints" || fail "$s prints the owner's address: $(cat "$WORK/prints")"
  # shellcheck disable=SC2016 # the line of the script, literal
  grep -q -F 'exec > >(masked | tee "$LOG") 2>&1' "$s" || fail "$s does not mask the address in its log"
done
GOOD="e2e-$TOKEN@example.org"
[[ "$GOOD" =~ $OWNER_FORM ]] || fail "the form refuses $GOOD"
# owner_run <plain|traced> <script> <exit> <a line the run reaches> <the log it writes, or -> [VAR=value ...]
# One run of a copy against the fake Jetson, which quotes the address on stderr as a remote error
# would. Traced runs go under bash -x: the trace before the log starts reaches only the terminal.
owner_run() {
  local mode="$1" s="$2" want="$3" reached="$4" log="$5" cmd=("$TROOT/$2")
  shift 5
  if [[ "$mode" == traced ]]; then cmd=(bash -x "$TROOT/$s"); fi
  reset
  rm -rf "$TROOT/tasks"
  run "$want" env PATH="$BIN:$PATH" DEPLOY_HOST=jetson.invalid STUB_CALLS="$CALLS" STUB_STATE="$STATE" \
    STUB_RUN="$RUN" STUB_PRIVATE="$PRIVATE" STUB_ECHO=1 STUB_OWNER="$GOOD" IDLE_S=0 POLL_S=0 OWNER="$GOOD" "$@" \
    "${cmd[@]}" "$JOB"
  if [[ "$mode" == plain ]]; then out_lacks "$TOKEN"; fi
  out_has "$reached"
  out_has "(the owner's address)"
  if [[ "$log" != - ]]; then [[ -s "$log" ]] || fail "$s $* wrote no ${log#"$TROOT"/}"; fi
  no_token_written "$s $*"
}
for mode in plain traced; do
  owner_run "$mode" tests/agents_real_run_jetson.sh 0 "runs added: 1" "$TROOT/tasks/evidence/agents-a5/run-owner.log" \
    REAL_RUN=1 RUNS=chat LABEL=owner
  owner_run "$mode" tests/ingestion_transcribe_jetson.sh 0 "The line for Measurements" \
    "$TROOT/tasks/evidence/ingestion-g14/run-owner.log" REAL_RUN=1 LABEL=owner POLL_S=1
  owner_run "$mode" tests/e2e_jetson.sh 1 "screenshots, index in" "$TROOT/tasks/evidence/identity/run.log"
  owner_run "$mode" tests/e2e_jobs_jetson.sh 0 PASS "$TROOT/tasks/evidence/jobs-j4-jetson/run.log"
  owner_run "$mode" tests/e2e_jobs_sweep_jetson.sh 0 PASS "$TROOT/tasks/evidence/jobs-j5-jetson/run.log"
done
ok "a real run of each of the 5 scripts to its end: the address in no output and no file; under bash -x, in no file"
owner_run plain tests/agents_real_run_jetson.sh 0 PASS -
owner_run plain tests/ingestion_transcribe_jetson.sh 0 "every check passed" - POLL_S=1
ok "the dry runs of tests/agents_real_run_jetson.sh and tests/ingestion_transcribe_jetson.sh: not printed"
A5_LOG="$TROOT/tasks/evidence/agents-a5/run-owner.log"
G14_LOG="$TROOT/tasks/evidence/ingestion-g14/run-owner.log"
owner_run plain tests/agents_real_run_jetson.sh 1 "the owner's account has no model credential" "$A5_LOG" \
  REAL_RUN=1 RUNS=chat LABEL=owner STUB_KIND=none
owner_run plain tests/agents_real_run_jetson.sh 1 "the owner's account has the role member" "$A5_LOG" \
  REAL_RUN=1 RUNS=chat LABEL=owner STUB_ROLE=member
owner_run plain tests/agents_real_run_jetson.sh 1 "no job $JOB in the owner's account" "$A5_LOG" \
  REAL_RUN=1 RUNS=chat LABEL=owner STUB_JOB=missing
owner_run plain tests/ingestion_transcribe_jetson.sh 1 "the owner's account is not on jetson.invalid" "$G14_LOG" \
  REAL_RUN=1 LABEL=owner POLL_S=1 STUB_ACCOUNT=missing
owner_run plain tests/ingestion_transcribe_jetson.sh 1 "the owner's account has not accepted" "$G14_LOG" \
  REAL_RUN=1 LABEL=owner POLL_S=1 STUB_CONSENT=no
owner_run plain tests/ingestion_transcribe_jetson.sh 1 "a run of the owner's account is under way" "$G14_LOG" \
  REAL_RUN=1 LABEL=owner POLL_S=1 STUB_BUSY=1
owner_run plain tests/e2e_jetson.sh 1 "the owner's account is not on jetson.invalid" "$TROOT/tasks/evidence/identity/run.log" \
  STUB_ACCOUNT=missing
owner_run plain tests/e2e_jobs_jetson.sh 1 "no jobs in the owner's account on jetson.invalid" \
  "$TROOT/tasks/evidence/jobs-j4-jetson/run.log" STUB_ACCOUNT=missing
owner_run plain tests/e2e_jobs_sweep_jetson.sh 1 "no jobs in the owner's account on jetson.invalid" \
  "$TROOT/tasks/evidence/jobs-j5-jetson/run.log" STUB_ACCOUNT=missing
ok "the 9 refusals that named the account after the log started now say \"the owner's account\""

step "B1: a dry run leaves the committed evidence as it is"
reset
run 0 script
out_has "dry run"
out_has "PASS"
unchanged "the default dry run"
[[ "$(starts)" == 0 ]] || fail "a dry run started a run"
! grep -q '^local docker' "$CALLS" || fail "a dry run called the local docker"
ok "default dry run: PASS, $(wc -l <"$WORK/sums.before" | tr -d ' ') evidence files unchanged, no run started ($(calls) read only calls)"
reset
run 0 script LABEL=followup RUNS=chat
unchanged "the dry run with LABEL=followup"
[[ "$(starts)" == 0 ]] || fail "a dry run started a run"
ok "dry run with LABEL=followup RUNS=chat: evidence unchanged"

step "B1: a real run is refused when its evidence exists, before any ssh call"
reset
run 1 script REAL_RUN=1
out_has "refused, nothing was started"
out_has "$COMMITTED/run.log"
out_has "$COMMITTED/metrics.json"
out_has "$COMMITTED/memory-triage.tsv"
[[ "$(calls)" == 0 ]] || fail "the refusal came after $(calls) ssh calls"
unchanged "the refused real run"
ok "REAL_RUN=1 with the committed run.log, metrics.json and samples: refused, 0 ssh calls, evidence unchanged"
reset
run 1 script REAL_RUN=1 LABEL=followup RUNS=chat
out_has "$COMMITTED/run-followup.log"
[[ "$(calls)" == 0 ]] || fail "the refusal came after $(calls) ssh calls"
unchanged "the refused real run with LABEL=followup"
ok "REAL_RUN=1 LABEL=followup: refused, 0 ssh calls"
reset
mkdir -p "$WORK/only-samples"
: >"$WORK/only-samples/memory-new-chat.tsv"
run 1 script REAL_RUN=1 LABEL=new RUNS=chat EVIDENCE="$WORK/only-samples"
out_has "memory-new-chat.tsv"
[[ "$(calls)" == 0 ]] || fail "the refusal came after $(calls) ssh calls"
[[ ! -e "$WORK/only-samples/run-new.log" ]] || fail "a refused run wrote a log"
ok "a samples file alone is enough to refuse, and no log is written"

step "B2: only an admin account with a subscription credential gets a run"
reset
run 1 script OWNER=friend@example.org STUB_ROLE=member STUB_KIND=anthropic_api_key
out_has "refused, nothing was started"
out_has "role member"
[[ "$(starts)" == 0 ]] || fail "a run was started for a member"
ok "dry run for a member with an API key: refused"
reset
run 1 script REAL_RUN=1 EVIDENCE="$WORK/b2-member" OWNER=friend@example.org STUB_ROLE=member STUB_KIND=claude_subscription
out_has "refused, nothing was started"
[[ "$(starts)" == 0 ]] || fail "a run was started for a member"
ok "real run for a member, even with a subscription credential: refused before any start"
reset
run 1 script REAL_RUN=1 EVIDENCE="$WORK/b2-key" OWNER=other-admin@example.org STUB_ROLE=admin STUB_KIND=anthropic_api_key
out_has "kind anthropic_api_key"
[[ "$(starts)" == 0 ]] || fail "a run was started with an API key"
ok "real run for an admin with an API key: refused before any start"
unchanged "the refused accounts"

step "N2, N3, N4: texts are counted, never printed"
reset
run 0 script
out_has "log lines holding a fragment of the message (20 characters, not printed): 0"
out_has "log lines holding a token shaped value (prefix sk-ant-): 0"
out_lacks "$DEFAULT_FRAGMENT"
ok "the fragment of the message is not printed"
reset
run 1 script STUB_LOG_EXTRA="prompt: What are th$DEFAULT_FRAGMENT""ts of my profile"
out_has "log lines holding a fragment of the message (20 characters, not printed): 1"
out_has "FAIL"
out_lacks "$DEFAULT_FRAGMENT"
ok "a message found in the logs fails the call, its text still not printed"
reset
token="sk-""ant-""notarealvalue0000"
run 1 script STUB_LOG_EXTRA="authorization $token"
out_has "log lines holding a token shaped value (prefix sk-ant-): 1"
out_has "FAIL"
out_lacks "$token"
ok "a bare token shaped value in the logs fails the call and is not printed"
reset
run 0 script MESSAGE="Hi" RUNS=chat
out_has "not checked, the message is shorter than"
out_has "PASS"
ok "a message too short to look for: the check is skipped with a line, no false failure"
reset
run 0 script MESSAGE="A question of 25 letters." RUNS=chat
out_has "log lines holding a fragment of the message (25 characters, not printed): 0"
ok "a short message is looked for whole"
reset
run 1 script MESSAGE="   " RUNS=chat
out_has "MESSAGE is empty"
[[ "$(calls)" == 0 ]] || fail "an empty MESSAGE reached the Jetson"
ok "an empty MESSAGE is refused before any ssh call"

step "N7, N4, N8: a real run against the fake Jetson, the same kind twice"
# The run goes under a locale whose awk writes 1.5 as 1,5 (macOS awk follows it), when this machine has one.
COMMA=""
for locale in fr_FR.UTF-8 de_DE.UTF-8 fr_FR.utf8 de_DE.utf8; do
  if [[ "$(LC_ALL="$locale" awk 'BEGIN { printf "%.1f", 1.5 }' 2>/dev/null)" == "1,5" ]]; then
    COMMA="$locale"
    break
  fi
done
reset
E="$WORK/twice"
run 0 script REAL_RUN=1 RUNS="chat chat" EVIDENCE="$E" ${COMMA:+"LC_ALL=$COMMA"}
out_has "PASS"
out_has "runs added: 2 (started here: 2)"
out_has "count only"
out_has "proposals decided (accepted, rejected or other) during this call: 0"
out_has "app.agents.runner: agent run started"
out_has "app.agents.runner: (message not printed)"
out_lacks "$PRIVATE"
out_lacks "$DEFAULT_FRAGMENT"
[[ "$(starts)" == 2 ]] || fail "expected 2 starts, got $(starts)"
for f in run.log metrics.json memory-chat.tsv memory-chat-2.tsv; do
  [[ -s "$E/$f" ]] || fail "$f is missing or empty"
done
[[ "$(jq '.runs | length' "$E/metrics.json")" == 2 ]] || fail "metrics.json does not hold both runs: $(grep -m 1 '^jq: ' "$WORK/out" || true)"
[[ "$(jq '[.runs[].memory.worker_peak_mib] == [144, 144]' "$E/metrics.json")" == true ]] ||
  fail "the memory of the runs in metrics.json is not the 144 MiB the samples hold"
! grep -q -F -- "$PRIVATE" "$E/run.log" || fail "run.log holds the text of a log message"
ok "RUNS=\"chat chat\": both sets of samples kept (memory-chat.tsv, memory-chat-2.tsv), 2 runs in metrics.json"
ok "the memory figures are JSON numbers under ${COMMA:-the inherited locale (no locale here writes a decimal comma)}"
ok "the text of a log message is replaced by (message not printed)"
reset
run 1 script REAL_RUN=1 RUNS=chat EVIDENCE="$E"
[[ "$(calls)" == 0 ]] || fail "a rerun into the same folder made $(calls) ssh calls"
ok "the same call again is refused: its evidence exists"
reset
E="$WORK/decided"
run 1 script REAL_RUN=1 RUNS=triage EVIDENCE="$E" STUB_DECIDE=1
out_has "FAIL: a proposal was decided while the script ran"
ok "a proposal decided during the call fails it"
reset
E="$WORK/unreadable"
run 1 script REAL_RUN=1 RUNS=chat EVIDENCE="$E" STUB_METRICS=bad
out_has "FAIL: the metrics of this run do not read as JSON"
[[ ! -e "$E/metrics.json" ]] || fail "metrics.json was written without the run"
ok "metrics of a run that do not read as JSON fail the call, never a PASS with no run in metrics.json"

step "N4: the stderr of the remote start stays out of the log"
reset
E="$WORK/crash"
run 1 script REAL_RUN=1 RUNS=chat EVIDENCE="$E" STUB_START=crash
out_has "2 lines on its stderr (not printed)"
out_has "FAIL: refused: ValidationError"
out_lacks "$PRIVATE"
out_lacks "Traceback"
! grep -q -F -e "$PRIVATE" -e Traceback "$E/run.log" || fail "run.log holds the remote stderr"
ok "a crash of the start: the class name and a line count, never the traceback"

step "N6: a start that gives no answer is unknown, not refused"
reset
E="$WORK/drop"
run 1 script REAL_RUN=1 RUNS="chat triage" EVIDENCE="$E" STUB_START=drop
out_has "FAIL: unknown, check the run row"
out_has "Run id: $RUN"
out_lacks "FAIL: refused"
[[ "$(starts)" == 1 ]] || fail "a second run was started after an unknown one"
ok "dropped connection: reported as unknown with the run id read from the run rows, no second run"

unchanged "the whole test"
printf '\nall %s checks passed\n' "$passed"
