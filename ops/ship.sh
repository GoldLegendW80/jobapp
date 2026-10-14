#!/usr/bin/env bash
# Approved pull requests to production in one command (issue #278): what the orchestrator did by
# hand after each review round (CONTRIBUTING.md, Merging, steps 6 and 7).
#
#   ops/ship.sh --approved              every open pull request approved on its current head
#   ops/ship.sh 281 279                 these, in this order (each must be approved on its head)
#   ops/ship.sh --plan --approved       say what would be done, touch nothing
#   ops/ship.sh --redact <log >tail     the filter of a log tail before it goes into a comment
#
# The steps.
# 1. Selection. A pull request is approved when the latest of its reviews whose body begins with
#    "REVIEW VERDICT:" reads exactly "REVIEW VERDICT: APPROVE" on line 1 and "Head: <full sha>"
#    of its current head commit on line 2. --approved takes every open pull request so approved
#    and skips, with the reason, drafts and those GitHub does not call MERGEABLE; a pull request
#    named on the command line that fails one of these stops the run before anything is done.
# 2. Order. A pull request that changes ops/local-check.sh first, then the given order (ascending
#    numbers with --approved). The gate of a batch that begins with it is that pull request's own
#    ops/local-check.sh, run from a detached worktree of its head (the record still lands in
#    .local-check/ of the main checkout, see the header of local-check.sh). A second one that
#    changes the gate is deferred to a later run: one gate script per batch.
# 3. Gate. ops/local-check.sh --merge <n> ... once for the batch. On a merge conflict the pull
#    request it names is dropped. On a failed step the batch is bisected: the shortest failing
#    prefix names the pull request to drop (when it is the first one alone, main is checked alone,
#    and a main that fails stops the run). A dropped pull request gets a comment with the failing
#    steps and the end of their log, each line that may hold a secret withheld (redact below);
#    then the rest is checked again. A lock or disk refusal of the gate stops the run.
# 4. Merge. Only when origin/main is still the base the gate checked and each head is still the
#    commit the PASS record lists: gh pr merge --merge --delete-branch --match-head-commit <sha>,
#    in the checked order. A head that moved stops the run before the first merge.
# 5. The main checkout (checked clean and on main before the gate, and again here) gets
#    git pull --ff-only; its tree must equal the gate's tree, then ops/deploy-checked.sh <commit>.
# 6. The Jetson over ssh, in ~/jobapp: every compose service running and healthy, alembic current
#    at (head), the memory used, and the count of lines matching " error " or "traceback" (any
#    case) in the last 10 minutes of the compose logs, which must be 0. Only these figures come
#    back: no log line, no environment value.
# 7. The issues the merged pull requests close (GitHub's closingIssuesReferences, the "Closes #N"
#    of their bodies) move to Done on the project board (project 1 of GoldLegendW80).
#
# Output: one line per action, then a summary block whose last line is "SHIP: OK", "SHIP: PLAN",
# "SHIP: NOTHING TO SHIP" or "SHIP: FAIL <reason>"; on a failure the same reason goes to stderr.
# Gate and deploy logs are kept in .local-check/ship/<run>/ of the main checkout (the last 10 runs).
# Exit status: 0 shipped, planned or nothing to ship; 1 a step failed or refused; 64 wrong
# arguments.
#
# For tests/ship_test.sh: SHIP_SSH_HOST (default jetson) and SHIP_POLL_SECONDS (default 5, the
# wait between two reads of a pull request whose mergeable state GitHub has not computed yet).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/ship.sh"
GATE_SCRIPT="ops/local-check.sh"
SSH_HOST="${SHIP_SSH_HOST:-jetson}"
POLL_SECONDS="${SHIP_POLL_SECONDS:-5}"
KEEP_LOGS=10
# The project board: project 1 of GoldLegendW80, its Status field and the option Done.
BOARD_OWNER="GoldLegendW80"
BOARD_NUMBER=1
BOARD_ID="PVT_kwHOA9eVYc4BmeB8"
STATUS_FIELD="PVTSSF_lAHOA9eVYc4BmeB8zhlG8_A"
STATUS_DONE="98236657"

say() { printf '%s\n' "$*"; }
usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }
usage_error() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  printf 'usage: %s [--plan] (--approved | <pull request number> ...)\n' "$PROG" >&2
  exit 64
}

SUMMARY=()
note() { SUMMARY+=("$(printf '%-9s %s' "$1:" "$2")"); }
summary() {
  ((${#SUMMARY[@]})) || return 0
  say ""
  say "SHIP SUMMARY"
  local line
  for line in "${SUMMARY[@]}"; do say "$line"; done
}
die() {
  summary
  say "SHIP: FAIL $*"
  printf '%s: %s\n' "$PROG" "$*" >&2
  exit 1
}
short() { printf '%s' "${1:0:7}"; }
joined() { # joined <word ...>: the words separated by ", ", or "none"
  (($#)) || {
    printf 'none'
    return 0
  }
  local out="$1" word
  shift
  for word in "$@"; do out="$out, $word"; done
  printf '%s' "$out"
}

# --- redact: a log tail that goes into a pull request comment --------------------------------------
# A line goes through only when nothing in it looks like a secret; otherwise it is replaced by a
# marker. Fail closed, by shape rather than by a list of known secrets: a word of the family of
# secrets (token, password, auth but not author, cookie, key names), a known key prefix, a password in a URL, a
# run of 32 or more hex digits, or a run of 32 or more token characters that mixes upper case,
# lower case and digits. Stated cost: a line that merely mentions a token, or holds a commit sha,
# is withheld too. Lines are cut at 400 characters.
SECRET_WORDS='secret|token|passw|pwd|api[_-]?key|apikey|(^|[^[:alnum:]])auth([^o]|$)|authorization:|oauth|bearer|credential|cookie|private[ _-]?key|signature|dsn|-----begin|ghp_|gho_|ghs_|ghu_|ghr_|github_pat_|(^|[^[:alnum:]])sk-[[:alnum:]]|xox[abprs]-|akia[[:alnum:]]{8}|aiza[[:alnum:]_-]{8}|ya29\.|gocspx-|eyj[[:alnum:]_-]{8,}\.'
URL_PASSWORD='://[^/@[:space:]]*:[^/@[:space:]]*@'
HEX_RUN='[[:xdigit:]]{32,}'
TOKEN_RUN='[[:alnum:]+/=_-]{32,}'
WITHHELD='[line withheld: it may hold a secret]'
redact() {
  local line low rest tok risky
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line:0:400}"
    low="$(tr '[:upper:]' '[:lower:]' <<<"$line")"
    risky=0
    if [[ "$low" =~ $SECRET_WORDS || "$line" =~ $URL_PASSWORD || "$line" =~ $HEX_RUN ]]; then risky=1; fi
    rest="$line"
    while ((risky == 0)) && [[ "$rest" =~ $TOKEN_RUN ]]; do
      tok="${BASH_REMATCH[0]}"
      if [[ "$tok" =~ [[:upper:]] && "$tok" =~ [[:lower:]] && "$tok" =~ [[:digit:]] ]]; then risky=1; fi
      rest="${rest#*"$tok"}"
    done
    if ((risky)); then say "$WITHHELD"; else say "$line"; fi
  done
}

PLAN=0
APPROVED=0
GIVEN=()
while (($#)); do
  case "$1" in
  --plan) PLAN=1 ;;
  --approved) APPROVED=1 ;;
  --redact)
    [[ $# -eq 1 ]] || usage_error "--redact takes no other argument"
    redact
    exit 0
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  -*) usage_error "unknown argument: $1" ;;
  *)
    [[ "$1" =~ ^[1-9][0-9]{0,6}$ ]] || usage_error "not a pull request number: $1"
    for given in ${GIVEN[@]+"${GIVEN[@]}"}; do
      [[ "$given" != "$1" ]] || usage_error "pull request $1 is given twice"
    done
    GIVEN+=("$1")
    ;;
  esac
  shift
done
if ((APPROVED)) && ((${#GIVEN[@]})); then usage_error "--approved or pull request numbers, not both"; fi
if ((APPROVED == 0)) && ((${#GIVEN[@]} == 0)); then usage_error "name pull requests, or give --approved"; fi

for tool in git gh python3 ssh; do
  command -v "$tool" >/dev/null || die "$tool is not installed"
done
COMMON="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ||
  die "$ROOT is not in a git repository"
MAIN="$(dirname "$COMMON")"
RECORDS="$MAIN/.local-check"
cd "$MAIN" # gh finds the repository from here

main_refusal() { # prints why the main checkout cannot be pulled, nothing when it can
  local branch status
  branch="$(git -C "$MAIN" symbolic-ref -q --short HEAD 2>/dev/null)" || branch="a detached HEAD"
  if [[ "$branch" != main ]]; then
    say "the main checkout $MAIN is on $branch, not main"
  elif ! status="$(git -C "$MAIN" status --porcelain 2>/dev/null)"; then
    say "git cannot read the status of the main checkout $MAIN"
  elif [[ -n "$status" ]]; then
    say "the main checkout $MAIN has uncommitted or untracked files"
  fi
}

# --- what GitHub says of a pull request -------------------------------------------------------------
# One line: state, draft, head, mergeable, the verdict of the latest "REVIEW VERDICT:" review and
# the sha its line 2 names ("-" for an empty field).
# shellcheck disable=SC2016 # a jq program: $r and $l are jq variables
FACTS_JQ='([.reviews[] | select(.body | startswith("REVIEW VERDICT:"))] | last) as $r
| (($r.body // "") | gsub("\r"; "") | split("\n")) as $l
| [.state, (.isDraft | tostring), .headRefOid, .mergeable,
   (($l[0] // "") | ltrimstr("REVIEW VERDICT:") | gsub("^ +| +$"; "")),
   (($l[1] // "") | ltrimstr("Head:") | gsub("^ +| +$"; ""))]
| map(if . == "" or . == null then "-" else . end) | @tsv'
WHY=""
F_STATE="" F_DRAFT="" F_HEAD="" F_MERGEABLE="" F_VERDICT="" F_VHEAD=""
check_pr() { # check_pr <n>: WHY is empty when it may ship, else the reason; F_* hold the facts
  local tries=0 out
  WHY=""
  while :; do
    if ! out="$(gh pr view "$1" --json state,isDraft,headRefOid,mergeable,reviews --jq "$FACTS_JQ" 2>/dev/null)"; then
      WHY="gh cannot read it"
      return 0
    fi
    IFS=$'\t' read -r F_STATE F_DRAFT F_HEAD F_MERGEABLE F_VERDICT F_VHEAD <<<"$out"
    [[ "$F_MERGEABLE" == UNKNOWN && $tries -lt 5 ]] || break
    tries=$((tries + 1))
    sleep "$POLL_SECONDS"
  done
  if [[ "$F_STATE" != OPEN ]]; then
    WHY="it is $F_STATE"
  elif [[ "$F_DRAFT" != false ]]; then
    WHY="a draft"
  elif ! [[ "$F_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
    WHY="no head commit"
  elif [[ "$F_VERDICT" == - ]]; then
    WHY="no review verdict"
  elif [[ "$F_VERDICT" != APPROVE ]]; then
    WHY="the latest verdict is $F_VERDICT"
  elif [[ "$F_VHEAD" != "$F_HEAD" ]]; then
    WHY="the approval names $F_VHEAD, the head is $(short "$F_HEAD")"
  elif [[ "$F_MERGEABLE" != MERGEABLE ]]; then
    WHY="GitHub says $F_MERGEABLE"
  fi
}
changes_gate() { # changes_gate <n>: true when the pull request changes ops/local-check.sh
  local files
  files="$(gh api "repos/{owner}/{repo}/pulls/$1/files" --paginate --jq '.[].filename')" ||
    die "gh cannot list the files of #$1"
  grep -qxF "$GATE_SCRIPT" <<<"$files"
}
issues_of() { # issues_of <n>: "<number> <url>" of each issue the pull request closes
  gh pr view "$1" --json closingIssuesReferences --jq '.closingIssuesReferences[] | "\(.number) \(.url)"'
}

# --- 1. selection -----------------------------------------------------------------------------------
SEL=()
SEL_HEAD=()
SKIPPED=()
if ((APPROVED)); then
  open="$(gh pr list --state open --limit 200 --json number --jq '.[].number')" ||
    die "gh cannot list the open pull requests"
  numbers=()
  while read -r n; do
    if [[ -n "$n" ]]; then numbers+=("$n"); fi
  done < <(sort -n <<<"$open")
  for n in ${numbers[@]+"${numbers[@]}"}; do
    check_pr "$n"
    if [[ -n "$WHY" ]]; then
      say "skip   #$n: $WHY"
      SKIPPED+=("#$n ($WHY)")
    else
      SEL+=("$n")
      SEL_HEAD+=("$F_HEAD")
    fi
  done
else
  for n in "${GIVEN[@]}"; do
    check_pr "$n"
    [[ -z "$WHY" ]] || die "#$n cannot ship: $WHY"
    SEL+=("$n")
    SEL_HEAD+=("$F_HEAD")
  done
fi
note skipped "$(joined ${SKIPPED[@]+"${SKIPPED[@]}"})"
if ((${#SEL[@]} == 0)); then
  summary
  say "SHIP: NOTHING TO SHIP"
  exit 0
fi

# --- 2. order -----------------------------------------------------------------------------------------
GATE_PR=""
ORDER=()
REST=()
DEFERRED=()
for n in "${SEL[@]}"; do
  if changes_gate "$n"; then
    if [[ -z "$GATE_PR" ]]; then
      GATE_PR="$n"
    else
      say "defer  #$n: it changes $GATE_SCRIPT as #$GATE_PR does, one gate script per batch"
      DEFERRED+=("#$n (changes $GATE_SCRIPT as #$GATE_PR does)")
    fi
  else
    REST+=("$n")
  fi
done
[[ -z "$GATE_PR" ]] || ORDER+=("$GATE_PR")
ORDER+=(${REST[@]+"${REST[@]}"})
head_of() {
  local i
  for i in "${!SEL[@]}"; do
    if [[ "${SEL[i]}" == "$1" ]]; then
      say "${SEL_HEAD[i]}"
      return 0
    fi
  done
  return 1
}
labels() { # labels <n ...>: "#n abc1234, ..."
  local n out=()
  for n in "$@"; do out+=("#$n $(short "$(head_of "$n")")"); done
  joined ${out[@]+"${out[@]}"}
}
note deferred "$(joined ${DEFERRED[@]+"${DEFERRED[@]}"})"
note order "$(labels "${ORDER[@]}")"
merge_args() { # merge_args <n ...>: the --merge arguments, printed
  local n
  for n in "$@"; do printf -- ' --merge %s' "$n"; done
}

if ((PLAN)); then
  say "plan   ship in this order: $(labels "${ORDER[@]}")"
  if [[ -n "$GATE_PR" ]]; then
    say "plan   gate: the $GATE_SCRIPT of #$GATE_PR at $(short "$(head_of "$GATE_PR")") (it changes the gate), from a detached worktree of that head"
  else
    say "plan   gate: the $GATE_SCRIPT of the main checkout"
  fi
  say "plan   $GATE_SCRIPT$(merge_args "${ORDER[@]}"); on a failure, bisect, drop and comment the failing pull request, check the rest again"
  say "plan   then gh pr merge --merge --delete-branch --match-head-commit <checked head>, one by one in this order"
  why="$(main_refusal)"
  if [[ -n "$why" ]]; then
    say "plan   git pull --ff-only in $MAIN: a real run would refuse now, $why"
  else
    say "plan   git pull --ff-only in $MAIN (clean, on main), its tree compared with the gate's tree"
  fi
  [[ ! -d "$RECORDS/lock" ]] || say "plan   a batch check holds $RECORDS/lock now: the gate of a real run would refuse to start"
  say "plan   ops/deploy-checked.sh <that commit>, then ssh $SSH_HOST: compose health, alembic head, memory, error lines of 10 minutes"
  cards=()
  for n in "${ORDER[@]}"; do
    issues="$(issues_of "$n")" || die "gh cannot read the issues #$n closes"
    while read -r number _; do
      [[ -n "$number" ]] && cards+=("#$number")
    done <<<"$issues"
  done
  say "plan   move to Done on the board: $(joined ${cards[@]+"${cards[@]}"})"
  summary
  say "SHIP: PLAN, nothing was done"
  exit 0
fi

# --- the run: lock, logs, cleanup --------------------------------------------------------------------
why="$(main_refusal)"
[[ -z "$why" ]] || die "$why"
mkdir -p "$RECORDS"
LOCK="$RECORDS/ship.lock"
HAVE_LOCK=0
WORK=""
GATE_TREE=""
cleanup() {
  if [[ -n "$GATE_TREE" ]]; then git -C "$MAIN" worktree remove --force "$GATE_TREE" >/dev/null 2>&1 || true; fi
  if [[ -n "$WORK" ]]; then rm -rf "$WORK"; fi
  if ((HAVE_LOCK)); then rm -rf "$LOCK"; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
mkdir "$LOCK" 2>/dev/null || die "another ops/ship.sh holds $LOCK (remove it if no ship is running)"
HAVE_LOCK=1
printf '%s\n' "$$" >"$LOCK/pid"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/jobapp-ship.XXXXXX")"
RUN="ship$(date +%m%d%H%M%S)-$$"
LOGS="$RECORDS/ship/$RUN"
mkdir -p "$LOGS"
# shellcheck disable=SC2012 # names made by this script, no odd characters
ls -1t "$RECORDS/ship" | tail -n "+$((KEEP_LOGS + 1))" | while IFS= read -r old; do
  rm -rf "${RECORDS:?}/ship/$old"
done
say "ship   $(labels "${ORDER[@]}") (logs $LOGS)"

if [[ -n "$GATE_PR" ]]; then
  git -C "$MAIN" fetch -q origin "pull/$GATE_PR/head" 2>/dev/null || die "cannot fetch the head of #$GATE_PR"
  sha="$(git -C "$MAIN" rev-parse FETCH_HEAD)"
  [[ "$sha" == "$(head_of "$GATE_PR")" ]] ||
    die "the head of #$GATE_PR moved while shipping ($(short "$(head_of "$GATE_PR")") approved, $(short "$sha") now)"
  GATE_TREE="$WORK/gate"
  git -C "$MAIN" worktree add -q --detach "$GATE_TREE" "$sha" >"$LOGS/worktree.log" 2>&1 ||
    die "cannot make a worktree of the head of #$GATE_PR ($LOGS/worktree.log)"
fi

# --- 3. gate and bisect ---------------------------------------------------------------------------
GATE_RUNS=0
G_STATUS=0
G_LOG=""
G_LAST=""
G_KIND=""
G_TREE=""
G_STEPS=""
G_CULPRIT=""
run_gate() { # run_gate [<n> ...]: the gate on main with these merged (none: main alone)
  local script="$MAIN/$GATE_SCRIPT" from="main" args=() n
  if [[ -n "$GATE_PR" && $# -gt 0 && "$1" == "$GATE_PR" ]]; then
    script="$GATE_TREE/$GATE_SCRIPT"
    from="#$GATE_PR"
  fi
  for n in "$@"; do args+=(--merge "$n"); done
  GATE_RUNS=$((GATE_RUNS + 1))
  G_LOG="$LOGS/gate-$GATE_RUNS.log"
  say "gate   run $GATE_RUNS, the $GATE_SCRIPT of $from:${args[*]+ ${args[*]}}"
  G_STATUS=0
  (cd "$MAIN" && "$script" ${args[@]+"${args[@]}"}) >"$G_LOG" 2>&1 </dev/null || G_STATUS=$?
  G_LAST="$(tail -n 1 "$G_LOG")"
  say "gate   run $GATE_RUNS: $G_LAST"
  G_KIND="" G_TREE="" G_STEPS="" G_CULPRIT=""
  if [[ "$G_STATUS" == 0 && "$G_LAST" =~ ^LOCAL\ CHECK:\ PASS\ ([0-9a-f]{40})\ [0-9]+s$ ]]; then
    G_KIND=pass
    G_TREE="${BASH_REMATCH[1]}"
  elif [[ "$G_STATUS" == 1 && "$G_LAST" =~ ^LOCAL\ CHECK:\ FAIL\ [0-9a-f]{40}\ (.+)$ ]]; then
    G_KIND=fail
    G_STEPS="${BASH_REMATCH[1]}"
  elif [[ "$G_STATUS" == 2 ]] &&
    G_CULPRIT="$(awk '/^MERGE CONFLICT: pull request [0-9]+ / { print $5; exit }' "$G_LOG")" &&
    in_batch "$G_CULPRIT"; then
    G_KIND=conflict
    G_STEPS="merge-conflict"
  else
    G_KIND=broken
  fi
}
tail_of() { # tail_of <gate log>: the end of the failed steps' logs, or the conflict, redacted
  awk '/^MERGE CONFLICT: /{on=1} /^--- /{on=1} /^logs: /{on=0} on' "$1" | tail -n 60 | redact
}
COMMENT_FAILED=()
DROPPED=()
BATCH=("${ORDER[@]}")
in_batch() {
  local m
  for m in "${BATCH[@]}"; do [[ "$m" != "$1" ]] || return 0; done
  return 1
}
drop() { # drop <n> <failing steps> <gate log>: out of the batch, with a comment on the pull request
  local n="$1" keep=() m before=() body="$WORK/comment-$1.md"
  for m in "${BATCH[@]}"; do
    if [[ "$m" == "$n" ]]; then
      before=(${keep[@]+"${keep[@]}"})
    else
      keep+=("$m")
    fi
  done
  BATCH=(${keep[@]+"${keep[@]}"})
  DROPPED+=("#$n ($2)")
  say "drop   #$n: $2"
  {
    say "**ops/ship.sh took this pull request out of the batch:** the batch check (\`$GATE_SCRIPT\`) failed at \`$2\`."
    say ""
    say "Checked: head $(head_of "$n") merged on main$( ((${#before[@]})) && printf ' after %s' "$(joined "${before[@]/#/#}")"), $(date -u '+%Y-%m-%d %H:%M UTC')."
    say "The end of the log follows; each line that may hold a secret is withheld. The full logs stay on the Mac."
    say ""
    say '```text'
    tail_of "$3"
    say '```'
  } >"$body"
  if ! gh pr comment "$n" --body-file "$body" >/dev/null 2>&1; then
    say "drop   #$n: the comment could not be posted"
    COMMENT_FAILED+=("#$n")
  fi
}
gate_broken() {
  case "$G_STATUS" in
  3) die "the gate refused to start, a check holds the lock or the disk is short: $G_LAST" ;;
  *) die "the gate did not give a result (exit $G_STATUS, log $G_LOG): $G_LAST" ;;
  esac
}

while :; do
  ((${#BATCH[@]})) || {
    note dropped "$(joined ${DROPPED[@]+"${DROPPED[@]}"})"
    die "every pull request of the batch failed the gate"
  }
  run_gate "${BATCH[@]}"
  case "$G_KIND" in
  pass) break ;;
  conflict)
    drop "$G_CULPRIT" "$G_STEPS" "$G_LOG"
    continue
    ;;
  fail) ;;
  *) gate_broken ;;
  esac
  # The shortest failing prefix: the batch of length hi fails; prefixes shorter than lo pass.
  lo=1
  hi=${#BATCH[@]}
  fail_log="$G_LOG"
  fail_steps="$G_STEPS"
  while ((lo < hi)); do
    mid=$(((lo + hi) / 2))
    run_gate "${BATCH[@]:0:mid}"
    case "$G_KIND" in
    pass) lo=$((mid + 1)) ;;
    fail)
      hi=$mid
      fail_log="$G_LOG"
      fail_steps="$G_STEPS"
      ;;
    *) gate_broken ;;
    esac
  done
  culprit="${BATCH[lo - 1]}"
  if ((lo == 1)); then
    # The first pull request alone fails: unless main fails without it.
    run_gate
    case "$G_KIND" in
    pass) ;;
    fail) die "main itself fails the gate at $G_STEPS (log $G_LOG): fix main first, nothing merged" ;;
    *) gate_broken ;;
    esac
  fi
  drop "$culprit" "$fail_steps" "$fail_log"
done
TREE="$G_TREE"
note dropped "$(joined ${DROPPED[@]+"${DROPPED[@]}"})"
((${#COMMENT_FAILED[@]} == 0)) || note comments "not posted on $(joined "${COMMENT_FAILED[@]}")"
note gate "PASS $TREE after $GATE_RUNS run(s), batch $(labels "${BATCH[@]}")"

# --- 4. merge -----------------------------------------------------------------------------------------
RECORD="$RECORDS/$TREE.json"
[[ -f "$RECORD" ]] || die "the gate printed PASS for $TREE but wrote no record $RECORD"
facts="$(python3 - "$RECORD" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1]))
print(record.get("result"), str(record.get("stub")).lower(), record["base"]["commit"])
for merged in record.get("merged", []):
    print(merged["ref"].replace("pull request ", ""), merged["commit"])
PY
)" || die "cannot read the record $RECORD"
read -r result stub base <<<"$(head -n 1 <<<"$facts")"
[[ "$result" == PASS && "$stub" == false ]] || die "the record $RECORD is not a full PASS (result $result, stub $stub)"
checked="$(tail -n +2 <<<"$facts" | cut -d' ' -f1 | tr '\n' ' ')"
[[ "$checked" == "${BATCH[*]} " ]] || die "the record $RECORD lists the merges ${checked:-none}, the batch is ${BATCH[*]}"
gated_head() { awk -v n="$1" '$1 == n { print $2 }' <<<"$(tail -n +2 <<<"$facts")"; }

now="$(git -C "$MAIN" ls-remote origin refs/heads/main | cut -f1)" || die "cannot read main on origin"
[[ "$now" == "$base" ]] || die "main moved since the gate (checked on $(short "$base"), now $(short "$now")): nothing merged, run ops/ship.sh again"
for n in "${BATCH[@]}"; do
  check_pr "$n"
  [[ -z "$WHY" ]] || die "#$n cannot be merged any more: $WHY; nothing merged"
  gated="$(gated_head "$n")"
  [[ "$F_HEAD" == "$gated" ]] ||
    die "the head of #$n moved since the gate ($(short "$gated") checked, $(short "$F_HEAD") now): nothing merged"
done
MERGED=()
for n in "${BATCH[@]}"; do
  gated="$(gated_head "$n")"
  if ((${#MERGED[@]})); then
    # GitHub recomputes the mergeable state after the merge before it; check_pr waits for it.
    check_pr "$n"
    [[ -z "$WHY" && "$F_HEAD" == "$gated" ]] ||
      die "#$n changed during the merges (${WHY:-head $(short "$F_HEAD")}): merged $(joined "${MERGED[@]}"), main is not the checked tree, nothing deployed"
  fi
  if ! (cd "$MAIN" && gh pr merge "$n" --merge --delete-branch --match-head-commit "$gated") >"$LOGS/merge-$n.log" 2>&1; then
    note merged "$(joined ${MERGED[@]+"${MERGED[@]}"})"
    die "gh pr merge #$n failed ($LOGS/merge-$n.log): main is not the checked tree, nothing deployed"
  fi
  state="$(gh pr view "$n" --json state --jq .state)" || state="unknown"
  if [[ "$state" != MERGED ]]; then
    note merged "$(joined ${MERGED[@]+"${MERGED[@]}"})"
    die "#$n is $state after gh pr merge: main is not the checked tree, nothing deployed"
  fi
  MERGED+=("#$n")
  say "merge  #$n $(short "$gated")"
done
note merged "$(joined "${MERGED[@]}")"

# --- 5. pull, compare, deploy -----------------------------------------------------------------------
why="$(main_refusal)"
[[ -z "$why" ]] || die "$why: not pulled, nothing deployed"
git -C "$MAIN" pull --ff-only -q >"$LOGS/pull.log" 2>&1 || die "git pull --ff-only failed in $MAIN ($LOGS/pull.log): nothing deployed"
commit="$(git -C "$MAIN" rev-parse HEAD)"
tree="$(git -C "$MAIN" rev-parse 'HEAD^{tree}')"
note main "$(short "$commit") tree $tree"
[[ "$tree" == "$TREE" ]] ||
  die "the tree of main $(short "$tree") differs from the gate's tree $(short "$TREE"): nothing deployed, run $GATE_SCRIPT on main"
say "deploy ops/deploy-checked.sh $commit"
deploy_status=0
"$MAIN/ops/deploy-checked.sh" "$commit" >"$LOGS/deploy.log" 2>&1 </dev/null || deploy_status=$?
if ((deploy_status)); then
  note deploy "FAIL, exit $deploy_status ($LOGS/deploy.log)"
  die "ops/deploy-checked.sh failed (exit $deploy_status): $(tail -n 1 "$LOGS/deploy.log")"
fi
note deploy "ok ($(tail -n 1 "$LOGS/deploy.log"))"

# --- 6. the Jetson ----------------------------------------------------------------------------------
# Runs there; prints only tab separated figures, never a log line or an environment value.
# shellcheck disable=SC2016 # expanded on the Jetson
REMOTE='set -u
cd ~/jobapp || exit 3
docker compose ps --format "{{.Service}}\t{{.State}}\t{{.Health}}" 2>/dev/null |
  while IFS= read -r line; do printf "ps\t%s\n" "$line"; done
rev="$(docker compose exec -T api alembic current 2>/dev/null |
  sed -n "s/^\([0-9A-Za-z_][0-9A-Za-z_]*\) (head).*/\1/p" | head -n 1)"
printf "alembic\t%s\n" "${rev:--}"
free -m | awk "/^Mem:/ { printf \"memory\t%s\t%s\n\", \$3, \$2 }"
logs="$(mktemp)"
if docker compose logs --no-color --since 10m >"$logs" 2>&1; then
  printf "errors\t%s\n" "$(grep -ciE " error |traceback" "$logs")"
else
  printf "errors\t-\n"
fi
rm -f "$logs"'
say "check  ssh $SSH_HOST, ~/jobapp"
jetson="$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$SSH_HOST" bash -s <<<"$REMOTE" 2>"$LOGS/jetson.err")" ||
  die "ssh $SSH_HOST failed ($LOGS/jetson.err): deployed, the Jetson is not checked"
services=0
unwell=()
rev="-"
mem_used="-"
mem_total="-"
errors="-"
while IFS=$'\t' read -r kind a b c; do
  case "$kind" in
  ps)
    services=$((services + 1))
    [[ "$b" == running && "$c" == healthy ]] || unwell+=("$a ${c:-$b}")
    ;;
  alembic) rev="$a" ;;
  memory)
    mem_used="$a"
    mem_total="$b"
    ;;
  errors) errors="$a" ;;
  esac
done <<<"$jetson"
problems=()
((services > 0)) || problems+=("no compose service")
((${#unwell[@]} == 0)) || problems+=("not healthy: $(joined "${unwell[@]}")")
[[ "$rev" != - ]] || problems+=("alembic is not at head")
[[ "$errors" == 0 ]] || problems+=("$errors error lines in 10 minutes")
note jetson "$services services, $((services - ${#unwell[@]})) healthy; alembic $rev; memory $mem_used of $mem_total MB used; $errors error lines in 10 minutes"
((${#problems[@]} == 0)) || die "the Jetson after the deploy: $(joined "${problems[@]}"); cards not moved"

# --- 7. the board -------------------------------------------------------------------------------------
moved=()
stuck=()
seen=" "
for n in "${BATCH[@]}"; do
  issues="$(issues_of "$n")" || {
    stuck+=("the issues of #$n")
    continue
  }
  while read -r number url; do
    [[ -n "$number" && "$seen" != *" $number "* ]] || continue
    seen="$seen$number "
    if item="$(gh project item-add "$BOARD_NUMBER" --owner "$BOARD_OWNER" --url "$url" --format json --jq .id 2>/dev/null)" &&
      [[ -n "$item" ]] &&
      gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$STATUS_FIELD" \
        --single-select-option-id "$STATUS_DONE" >/dev/null 2>&1; then
      moved+=("#$number")
    else
      stuck+=("#$number")
    fi
  done <<<"$issues"
done
note cards "Done: $(joined ${moved[@]+"${moved[@]}"})"
((${#stuck[@]} == 0)) || die "deployed and checked, but these cards did not move to Done: $(joined "${stuck[@]}")"
summary
say "SHIP: OK"
