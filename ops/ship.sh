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
#    Dependencies: every #N on a line of the body that begins with "Depends on" or "Stacked on"
#    (any case). A pull request ships only after each of them: N is MERGED already, or N ships
#    before it in the same batch. Otherwise --approved skips it "waits for #N" (and so, in turn,
#    every pull request that depends on it), and a named one stops the run.
# 2. Order. A pull request that changes ops/local-check.sh first, then the given order (ascending
#    numbers with --approved, each dependency before its dependents). The gate of a batch that
#    begins with it is that pull request's own ops/local-check.sh, run from a detached worktree of
#    its head (the record still lands in .local-check/ of the main checkout, see the header of
#    local-check.sh). A second one that changes the gate, or one that depends on another pull
#    request of the batch, is deferred to a later run: one gate script per batch.
# 3. Gate. ops/local-check.sh --merge <n>@<approved head> ... once for the batch (issue #468): each
#    pull request is checked at the head its approval names, never at a later push, and so is
#    every run of the bisect. Before each run the heads are read again: a head that moved since the
#    selection stops the run before anything is merged ("the head of #n moved while shipping"), so
#    the batch is selected again, and approved again, by the next run. On a merge conflict the pull
#    request it names is dropped. A failed step counts once it repeats: the batch is checked again
#    as it is, a PASS then ships (the first failure is named "flaky" in the summary) and another
#    list of failed steps stops the run ("flaky gate"), nothing dropped. The same failure twice is
#    bisected: the shortest prefix that fails at the same steps names the pull request to drop (a
#    prefix that fails at other steps counts as passing and is named flaky; when the culprit is the
#    first one alone, main is checked alone, and a main that fails stops the run). A dropped pull
#    request gets a comment with the step the bisect blamed and the last 30 lines of its log, each
#    line that may hold a secret withheld (redact below); every pull request that depends on it
#    leaves the batch too, without a comment; then the rest is checked again, unless it is exactly
#    a prefix the bisect already passed (that pass counts, no run). A lock or disk
#    refusal of the gate stops the run.
# 4. Merge. Only when origin/main is still the base the gate checked and each head is still the
#    commit the PASS record lists: gh pr merge --repo <owner/repo> --merge --delete-branch
#    --match-head-commit <sha>, in the checked order (with --repo gh leaves local branches alone,
#    so a branch checked out in a worktree cannot fail it). The state is read after each call:
#    MERGED counts as merged whatever gh exited with (a state that cannot be read is read once
#    more). A head that moved stops the run before the first merge; a stop between two merges, or
#    INT or TERM during them, names what merged and what did not.
# 5. The issues the merged pull requests close (GitHub's closingIssuesReferences, the "Closes #N"
#    of their bodies) move to Done on the project board (project 1 of GoldLegendW80) right after
#    the merges, since GitHub closes them then; also after a stop between two merges. A card that
#    does not move is named and makes the exit status 1, but stops nothing.
# 6. The main checkout (checked clean and on main before the gate, and again here) gets
#    git pull --ff-only; its tree must equal the gate's tree, then ops/deploy-checked.sh <commit>.
# 7. The Jetson, as the deploy run saw it, without ssh (owner decision of 2026-10-15): the last
#    step of deploy.yml (ops/jetson-verify.sh) prints one line JOBAPP-VERIFY services=...
#    result=ok|fail, which ops/deploy-checked.sh reads from the run's log and prints. Every
#    service of the compose file (with the tunnel profile) has a container (ps -a, so a stopped one
#    shows) that is running and healthy, alembic current at (head), the memory used, the count of
#    error lines in the last 10 minutes of the compose logs, which must be 0 (a JSON line at level
#    error or critical, " error " or "traceback", any case), and the browser, when its profile is
#    on. Only these figures come back: no log line, no environment value. A deploy without the line
#    is deployed and not checked, which fails the run: ops/remote.sh status asks again.
#
# Output: one line per action, then a summary block whose last line is "SHIP: OK", "SHIP: PLAN",
# "SHIP: NOTHING TO SHIP" or "SHIP: FAIL <reason>"; on a failure the same reason goes to stderr.
# Gate and deploy logs are kept in .local-check/ship/<run>/ of the main checkout (the last 10 runs).
# Exit status: 0 shipped, planned or nothing to ship; 1 a step failed or refused; 64 wrong
# arguments.
#
# For tests/ship_test.sh: SHIP_POLL_SECONDS (default 5, the wait between two reads of a pull
# request whose mergeable state GitHub has not computed yet).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/ship.sh"
GATE_SCRIPT="ops/local-check.sh"
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
# secrets (token, password, auth but not author, cookie, key names), an identifier that holds
# key, keys, access or creds (as a whole part, at its end, or before "id") followed by "=" or
# ":", the key prefixes gitleaks knows, a password in a URL, a run of 32 or more hex digits, or a
# run of 32 or more token characters that mixes upper case, lower case and digits. Stated cost:
# a line that merely mentions a token, holds a commit sha, or reads "monkey: " or
# "primary_key=True", is withheld too. Lines are cut at 400 characters.
B='(^|[^[:alnum:]])' # the start of a word, so that msg.send or hf_ratio stay
SECRET_WORDS='secret|token|passw|pwd|api[_-]?key|apikey|'"${B}"'auth([^o]|$)|authorization:|oauth|bearer|credential|cookie|private[ _-]?key|signature|dsn|-----begin'
SECRET_WORDS+='|(keys?|access|creds)(ids?)?([_.-][[:alnum:]_.-]*)?["'"'"']?[[:space:]]*[=:]'
SECRET_WORDS+='|ghp_|gho_|ghs_|ghu_|ghr_|github_pat_|glpat-|'"${B}"'sk-[[:alnum:]]|'"${B}"'[rs]k_(live|test)_|xox[abeprs]-|xapp-[[:alnum:]]'
SECRET_WORDS+='|akia[[:alnum:]]{8}|'"${B}"'(asia|abia|acca|a3t[[:alnum:]])[[:alnum:]]{8}|aiza[[:alnum:]_-]{8}|ya29\.|gocspx-|eyj[[:alnum:]_-]{8,}\.'
SECRET_WORDS+='|'"${B}"'hf_[[:alnum:]]{10,}|'"${B}"'npm_[[:alnum:]]{10,}|'"${B}"'sg\.[[:alnum:]_-]{8,}\.|dop_v1_|'"${B}"'pypi-[[:alnum:]]{10,}'
SECRET_WORDS+='|shp(at|ss|ca|pa)_|lin_api_|accountkey=|tskey-'
SECRET_WORDS+='|glsa_|glptt-|dckr_pat_|sntrys_|figd_|pplx-|lsv2_pt_|'"${B}"'(gsk|glc|r8)_[[:alnum:]]{8,}|'"${B}"'(hvs|sl)\.[[:alnum:]_-]{10,}'
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

for tool in git gh python3; do
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
# One line: state, draft, head, mergeable, the verdict of the latest "REVIEW VERDICT:" review, the
# sha its line 2 names and the dependencies of the body, numbers separated by spaces ("-" for an
# empty field). A line of dependencies may begin with list or quote marks ("- Depends on #12").
# shellcheck disable=SC2016 # a jq program: $r, $l and $d are jq variables
FACTS_JQ='([.reviews[] | select(.body | startswith("REVIEW VERDICT:"))] | last) as $r
| (($r.body // "") | gsub("\r"; "") | split("\n")) as $l
| ((.body // "") | split("\n") | map(select(test("^[\\s>*_-]*(depends|stacked) on"; "i")) | [scan("#([0-9]+)") | .[0]])
   | flatten | join(" ")) as $d
| [.state, (.isDraft | tostring), .headRefOid, .mergeable,
   (($l[0] // "") | ltrimstr("REVIEW VERDICT:") | gsub("^ +| +$"; "")),
   (($l[1] // "") | ltrimstr("Head:") | gsub("^ +| +$"; "")), $d]
| map(if . == "" or . == null then "-" else . end) | @tsv'
WHY=""
F_STATE="" F_DRAFT="" F_HEAD="" F_MERGEABLE="" F_VERDICT="" F_VHEAD="" F_DEPS=""
check_pr() { # check_pr <n>: WHY is empty when it may ship, else the reason; F_* hold the facts
  local tries=0 out
  WHY=""
  while :; do
    if ! out="$(gh pr view "$1" --json state,isDraft,headRefOid,mergeable,reviews,body --jq "$FACTS_JQ" 2>/dev/null)"; then
      WHY="gh cannot read it"
      return 0
    fi
    IFS=$'\t' read -r F_STATE F_DRAFT F_HEAD F_MERGEABLE F_VERDICT F_VHEAD F_DEPS <<<"$out"
    if [[ "$F_DEPS" == - ]]; then F_DEPS=""; fi
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

# --- 5. the board: move_cards runs right after the merges (GitHub closes the issues then) ---------
STUCK=()
move_cards() { # move_cards <n ...>: the issues these merged pull requests close, to Done
  local n number url item moved=() seen=" " issues
  for n in "$@"; do
    issues="$(issues_of "$n")" || {
      STUCK+=("the issues of #$n")
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
        STUCK+=("#$number")
      fi
    done <<<"$issues"
  done
  note cards "Done: $(joined ${moved[@]+"${moved[@]}"})$( ((${#STUCK[@]} == 0)) || printf '; not moved: %s' "$(joined "${STUCK[@]}")")"
}

# --- 1. selection -----------------------------------------------------------------------------------
SEL=()
SEL_HEAD=()
SEL_DEPS=()
SKIPPED=()
select_pr() {
  SEL+=("$1")
  SEL_HEAD+=("$F_HEAD")
  SEL_DEPS+=("$F_DEPS")
}
skip() { # skip <n> <why>
  say "skip   #$1: $2"
  SKIPPED+=("#$1 ($2)")
}
if ((APPROVED)); then
  open="$(gh pr list --state open --limit 200 --json number --jq '.[].number')" ||
    die "gh cannot list the open pull requests"
  numbers=()
  while read -r n; do
    if [[ -n "$n" ]]; then numbers+=("$n"); fi
  done < <(sort -n <<<"$open")
  for n in ${numbers[@]+"${numbers[@]}"}; do
    check_pr "$n"
    if [[ -n "$WHY" ]]; then skip "$n" "$WHY"; else select_pr "$n"; fi
  done
else
  for n in "${GIVEN[@]}"; do
    check_pr "$n"
    [[ -z "$WHY" ]] || die "#$n cannot ship: $WHY"
    select_pr "$n"
  done
fi
deps_of() { # deps_of <n>: the numbers its body depends on
  local i
  for i in "${!SEL[@]}"; do
    if [[ "${SEL[i]}" == "$1" ]]; then
      say "${SEL_DEPS[i]}"
      return 0
    fi
  done
}
in_list() { # in_list <n> <word ...>
  local m n="$1"
  shift
  for m in "$@"; do [[ "$m" != "$n" ]] || return 0; done
  return 1
}
KNOWN_MERGED=" "
KNOWN_OPEN=" ${SEL[*]-} "
is_merged() { # is_merged <n>: GitHub says the pull request is MERGED (asked once; unreadable is not)
  [[ "$KNOWN_MERGED" != *" $1 "* ]] || return 0
  [[ "$KNOWN_OPEN" != *" $1 "* ]] || return 1
  if [[ "$(gh pr view "$1" --json state --jq .state 2>/dev/null)" == MERGED ]]; then
    KNOWN_MERGED+="$1 "
    return 0
  fi
  KNOWN_OPEN+="$1 "
  return 1
}

# --- dependencies: SEL in an order where each dependency comes before its dependents ------------------
ORDERED=()
if ((APPROVED)); then
  # The smallest number whose dependencies are all merged or placed goes next; one that depends on
  # a pull request neither merged nor selected is skipped, which may skip others in a later pass.
  PENDING=(${SEL[@]+"${SEL[@]}"})
  while ((${#PENDING[@]})); do
    progress=0
    for n in "${PENDING[@]}"; do
      blocked="" waiting=0
      for d in $(deps_of "$n"); do
        in_list "$d" ${ORDERED[@]+"${ORDERED[@]}"} && continue
        if in_list "$d" "${PENDING[@]}"; then
          waiting=1
        elif ! is_merged "$d"; then
          blocked="$d"
          break
        fi
      done
      if [[ -n "$blocked" ]]; then
        skip "$n" "waits for #$blocked"
      elif ((waiting)); then
        continue
      else
        ORDERED+=("$n")
      fi
      left=()
      for m in "${PENDING[@]}"; do [[ "$m" == "$n" ]] || left+=("$m"); done
      PENDING=(${left[@]+"${left[@]}"})
      progress=1
      break
    done
    if ((progress == 0)); then # a cycle: each waits for another
      for n in "${PENDING[@]}"; do
        for d in $(deps_of "$n"); do
          if in_list "$d" "${PENDING[@]}"; then
            skip "$n" "waits for #$d"
            break
          fi
        done
      done
      PENDING=()
    fi
  done
else
  for i in "${!SEL[@]}"; do
    n="${SEL[i]}"
    for d in $(deps_of "$n"); do
      in_list "$d" "${SEL[@]:0:i}" || is_merged "$d" || die "#$n cannot ship: waits for #$d"
    done
  done
  ORDERED=("${SEL[@]}")
fi
note skipped "$(joined ${SKIPPED[@]+"${SKIPPED[@]}"})"
if ((${#ORDERED[@]} == 0)); then
  summary
  say "SHIP: NOTHING TO SHIP"
  exit 0
fi

# --- 2. order -----------------------------------------------------------------------------------------
GATE_PR=""
ORDER=()
REST=()
DEFERRED=()
OUT=() # deferred: their dependents wait too
defer() { # defer <n> <why>
  say "defer  #$1: $2"
  DEFERRED+=("#$1 ($2)")
  OUT+=("$1")
}
for n in "${ORDERED[@]}"; do
  blocked="" inside=""
  for d in $(deps_of "$n"); do
    if in_list "$d" ${OUT[@]+"${OUT[@]}"}; then
      blocked="$d"
      break
    fi
    if [[ -z "$inside" ]] && in_list "$d" "${ORDERED[@]}"; then inside="$d"; fi
  done
  if [[ -n "$blocked" ]]; then
    defer "$n" "waits for #$blocked"
  elif changes_gate "$n"; then
    if [[ -n "$GATE_PR" ]]; then
      defer "$n" "it changes $GATE_SCRIPT as #$GATE_PR does, one gate script per batch"
    elif [[ -n "$inside" ]]; then
      defer "$n" "waits for #$inside (it changes $GATE_SCRIPT, so it would be checked first)"
    else
      GATE_PR="$n"
    fi
  else
    REST+=("$n")
  fi
done
[[ -z "$GATE_PR" ]] || ORDER+=("$GATE_PR")
ORDER+=(${REST[@]+"${REST[@]}"})
((${#ORDER[@]})) || {
  note deferred "$(joined ${DEFERRED[@]+"${DEFERRED[@]}"})"
  summary
  say "SHIP: NOTHING TO SHIP"
  exit 0
}
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
merge_args() { # merge_args <n ...>: the --merge arguments, printed, each pinned to its approved head
  local n
  for n in "$@"; do printf -- ' --merge %s@%s' "$n" "$(head_of "$n")"; done
}

if ((PLAN)); then
  say "plan   ship in this order: $(labels "${ORDER[@]}")"
  if [[ -n "$GATE_PR" ]]; then
    say "plan   gate: the $GATE_SCRIPT of #$GATE_PR at $(short "$(head_of "$GATE_PR")") (it changes the gate), from a detached worktree of that head"
  else
    say "plan   gate: the $GATE_SCRIPT of the main checkout"
  fi
  say "plan   $GATE_SCRIPT$(merge_args "${ORDER[@]}"); on a failure, check again once, then bisect, drop and comment the failing pull request (and drop its dependents), check the rest again"
  say "plan   then gh pr merge --repo <owner/repo> --merge --delete-branch --match-head-commit <checked head>, one by one in this order"
  why="$(main_refusal)"
  if [[ -n "$why" ]]; then
    say "plan   git pull --ff-only in $MAIN: a real run would refuse now, $why"
  else
    say "plan   git pull --ff-only in $MAIN (clean, on main), its tree compared with the gate's tree"
  fi
  [[ ! -d "$RECORDS/lock" ]] ||
    say "plan   a batch check holds $RECORDS/lock now ($(cat "$RECORDS/lock/holder" 2>/dev/null || echo "holder unknown")): the gate of a real run would refuse to start"
  say "plan   ops/deploy-checked.sh <that commit>, then the figures its run printed (JOBAPP-VERIFY): compose health, alembic head, memory, error lines of 10 minutes, browser"
  cards=()
  for n in "${ORDER[@]}"; do
    issues="$(issues_of "$n")" || die "gh cannot read the issues #$n closes"
    while read -r number _; do
      [[ -n "$number" ]] && cards+=("#$number")
    done <<<"$issues"
  done
  say "plan   move to Done on the board right after the merges: $(joined ${cards[@]+"${cards[@]}"})"
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
if ! mkdir "$LOCK" 2>/dev/null; then
  holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  alive="not running"
  if [[ "$holder" =~ ^[0-9]+$ ]] && kill -0 "$holder" 2>/dev/null; then alive="running"; fi
  die "another ops/ship.sh holds $LOCK (process ${holder:-unknown}, $alive; remove the folder if no ship is running)"
fi
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
PASS_KEY="" # the batch (numbers joined by ",") of the latest bisect prefix that passed ...
PASS_TREE="" # ... the tree it passed on ...
PASS_RUN=0 # ... and the run that checked it
G_STATUS=0
G_LOG=""
G_LAST=""
G_KIND=""
G_TREE=""
G_STEPS=""
G_CULPRIT=""
heads_unmoved() { # heads_unmoved <n ...>: stops the run when a head is not the approved one any more
  local n now
  for n in "$@"; do
    now="$(gh pr view "$n" --json headRefOid --jq .headRefOid 2>/dev/null)" || {
      gate_notes
      die "gh cannot read the head of #$n: nothing merged"
    }
    [[ "$now" == "$(head_of "$n")" ]] || {
      gate_notes
      die "the head of #$n moved while shipping ($(short "$(head_of "$n")") approved, $(short "$now") now): nothing merged, run ops/ship.sh again"
    }
  done
}
run_gate() { # run_gate [<n> ...]: the gate on main with these merged at their approved heads (none: main alone)
  local script="$MAIN/$GATE_SCRIPT" from="main" args=() n
  if [[ -n "$GATE_PR" && $# -gt 0 && "$1" == "$GATE_PR" ]]; then
    script="$GATE_TREE/$GATE_SCRIPT"
    from="#$GATE_PR"
  fi
  # A push since the selection: the batch is not what was approved any more.
  heads_unmoved ${BATCH[@]+"${BATCH[@]}"}
  for n in "$@"; do args+=(--merge "$n@$(head_of "$n")"); done
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
tail_of() { # tail_of <gate log> <step>: the last 30 lines of that step's log (or the conflict), redacted
  if [[ "$2" == merge-conflict ]]; then
    awk '/^MERGE CONFLICT: /{on=1} /^LOCAL CHECK: /{next} on' "$1"
  else
    # The lines under "--- <step>: the last 40 lines of <path>" (the header, which names a local
    # path, left out) up to the next "--- " or "logs: ", without the blank lines before it.
    awk -v head="--- $2: " '
      index($0, head) == 1 { on = 1; blanks = 0; next }
      /^--- / || /^logs: / { on = 0 }
      on && /^$/ { blanks++; next }
      on { for (; blanks > 0; blanks--) print ""; print }' "$1"
  fi | redact | tail -n 30
}
key_of() { local IFS=,; printf '%s' "$*"; }
COMMENT_FAILED=()
DROPPED=()
FLAKY=()
BATCH=("${ORDER[@]}")
in_batch() { in_list "$1" ${BATCH[@]+"${BATCH[@]}"}; }
drop() { # drop <n> <failing steps> <gate log>: out of the batch, with a comment, and its dependents
  local n="$1" keep=() m d before=() body="$WORK/comment-$1.md" out=("$1") blamed="${2%%,*}"
  for m in "${BATCH[@]}"; do
    if [[ "$m" == "$n" ]]; then
      before=(${keep[@]+"${keep[@]}"})
      continue
    fi
    # The batch is in dependency order: a dependent comes after what it depends on.
    for d in $(deps_of "$m"); do
      if in_list "$d" "${out[@]}"; then
        out+=("$m")
        continue 2
      fi
    done
    keep+=("$m")
  done
  BATCH=(${keep[@]+"${keep[@]}"})
  DROPPED+=("#$n ($2)")
  say "drop   #$n: $2"
  for m in "${out[@]:1}"; do
    DROPPED+=("#$m (waits for #$n)")
    say "drop   #$m: waits for #$n"
  done
  {
    say "**ops/ship.sh took this pull request out of the batch:** the batch check (\`$GATE_SCRIPT\`) failed at \`$blamed\`."
    say ""
    say "Checked: head $(head_of "$n") merged on main$( ((${#before[@]})) && printf ' after %s' "$(joined "${before[@]/#/#}")"), $(date -u '+%Y-%m-%d %H:%M UTC')."
    say "The last lines of the log of \`$blamed\` follow; each line that may hold a secret is withheld. The full logs stay on the Mac."
    say ""
    say '```text'
    tail_of "$3" "$blamed"
    say '```'
  } >"$body"
  if ! gh pr comment "$n" --body-file "$body" >/dev/null 2>&1; then
    say "drop   #$n: the comment could not be posted"
    COMMENT_FAILED+=("#$n")
  fi
}
gate_notes() {
  note dropped "$(joined ${DROPPED[@]+"${DROPPED[@]}"})"
  ((${#COMMENT_FAILED[@]} == 0)) || note comments "not posted on $(joined "${COMMENT_FAILED[@]}")"
  local flaky="" f
  for f in ${FLAKY[@]+"${FLAKY[@]}"}; do flaky="${flaky:+$flaky; }$f"; done
  [[ -z "$flaky" ]] || note flaky "$flaky"
}
gate_broken() {
  gate_notes
  case "$G_STATUS" in
  3) die "the gate refused to start, a check holds the lock or the disk is short: $G_LAST" ;;
  *) die "the gate did not give a result (exit $G_STATUS, log $G_LOG): $G_LAST" ;;
  esac
}

while :; do
  ((${#BATCH[@]})) || {
    gate_notes
    die "every pull request of the batch failed the gate"
  }
  # What the bisect already passed is not gated again: after the culprit is dropped, the batch is
  # often exactly the longest prefix that passed.
  if [[ -n "$PASS_KEY" && "$(key_of "${BATCH[@]}")" == "$PASS_KEY" ]]; then
    say "gate   run $PASS_RUN already passed this batch, not run again"
    G_KIND=pass
    G_TREE="$PASS_TREE"
    break
  fi
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
  # The flake guard: a failure counts once the same batch fails at the same steps again.
  first="run $GATE_RUNS failed $G_STEPS"
  fail_steps="$G_STEPS"
  run_gate "${BATCH[@]}"
  case "$G_KIND" in
  pass)
    FLAKY+=("$first")
    break
    ;;
  fail | conflict)
    if [[ "$G_STEPS" != "$fail_steps" ]]; then
      FLAKY+=("$first, run $GATE_RUNS failed $G_STEPS")
      gate_notes
      die "flaky gate"
    fi
    ;;
  *) gate_broken ;;
  esac
  fail_log="$G_LOG"
  # The shortest failing prefix: the batch of length hi fails; prefixes shorter than lo pass. A
  # prefix that fails at other steps than the batch does not reproduce it: it counts as passing.
  lo=1
  hi=${#BATCH[@]}
  PASS_KEY=""
  while ((lo < hi)); do
    mid=$(((lo + hi) / 2))
    run_gate "${BATCH[@]:0:mid}"
    case "$G_KIND" in
    pass)
      lo=$((mid + 1))
      PASS_KEY="$(key_of "${BATCH[@]:0:mid}")"
      PASS_TREE="$G_TREE"
      PASS_RUN="$GATE_RUNS"
      ;;
    fail)
      if [[ "$G_STEPS" == "$fail_steps" ]]; then
        hi=$mid
        fail_log="$G_LOG"
      else
        FLAKY+=("run $GATE_RUNS failed $G_STEPS, a bisect prefix counted as passing")
        lo=$((mid + 1))
      fi
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
    fail)
      gate_notes
      die "main itself fails the gate at $G_STEPS (log $G_LOG): fix main first, nothing merged"
      ;;
    *) gate_broken ;;
    esac
  fi
  drop "$culprit" "$fail_steps" "$fail_log"
done
TREE="$G_TREE"
gate_notes
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

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || REPO=""
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "gh cannot name the repository of $MAIN: nothing merged"
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
MERGE_WARNINGS=()
stop_merging() { # stop_merging <index in the batch of the first one not merged> <reason>
  MERGING=0
  local rest=("${BATCH[@]:$1}")
  note merged "$(joined ${MERGED[@]+"${MERGED[@]/#/#}"})"
  note "not merged" "$(joined "${rest[@]/#/#}")"
  ((${#MERGE_WARNINGS[@]} == 0)) || note warnings "$(joined "${MERGE_WARNINGS[@]}")"
  ((${#MERGED[@]} == 0)) || move_cards "${MERGED[@]}"
  die "$2: main is not the checked tree, nothing deployed"
}
# INT or TERM during the merges: say what merged and what did not (the one in flight is asked
# of GitHub, since gh may have merged it just before the signal), move the cards of the merged
# ones, exit 130.
MERGING=0
MERGE_AT=0
on_signal() {
  trap '' INT TERM
  if ((MERGING)); then
    MERGING=0
    local at="" state=""
    if ((MERGE_AT < ${#BATCH[@]})); then
      at="${BATCH[MERGE_AT]}"
      state="$(gh pr view "$at" --json state --jq .state 2>/dev/null)" || state="unknown"
      if [[ "$state" == MERGED ]]; then
        MERGED+=("$at")
        MERGE_AT=$((MERGE_AT + 1))
      fi
    fi
    local rest=("${BATCH[@]:$MERGE_AT}")
    note merged "$(joined ${MERGED[@]+"${MERGED[@]/#/#}"})"
    note "not merged" "$(joined "${rest[@]/#/#}")"
    if [[ -z "$at" ]]; then
      note interrupted "after the last merge, before the cards and the deploy"
    else
      note interrupted "while merging #$at, which is ${state:-unknown} on GitHub"
    fi
    ((${#MERGE_WARNINGS[@]} == 0)) || note warnings "$(joined "${MERGE_WARNINGS[@]}")"
    ((${#MERGED[@]} == 0)) || move_cards "${MERGED[@]}"
    summary
    say "SHIP: FAIL interrupted during the merges: nothing deployed"
    printf '%s: interrupted during the merges\n' "$PROG" >&2
  fi
  exit 130
}
trap on_signal INT TERM
MERGING=1
for i in "${!BATCH[@]}"; do
  MERGE_AT="$i"
  n="${BATCH[i]}"
  gated="$(gated_head "$n")"
  if ((${#MERGED[@]})); then
    # GitHub recomputes the mergeable state after the merge before it; check_pr waits for it.
    check_pr "$n"
    [[ -z "$WHY" && "$F_HEAD" == "$gated" ]] ||
      stop_merging "$i" "#$n changed during the merges (${WHY:-head $(short "$F_HEAD")})"
  fi
  # From a folder outside the repository, with --repo: gh touches no local branch.
  merge_status=0
  (cd "$WORK" && gh pr merge "$n" --repo "$REPO" --merge --delete-branch --match-head-commit "$gated") >"$LOGS/merge-$n.log" 2>&1 ||
    merge_status=$?
  # An unreadable state is read once more: the merge may well have gone through.
  state="$(gh pr view "$n" --json state --jq .state 2>/dev/null)" || {
    sleep "$POLL_SECONDS"
    state="$(gh pr view "$n" --json state --jq .state 2>/dev/null)" || state="unknown"
  }
  if [[ "$state" != MERGED ]]; then
    if ((merge_status)); then stop_merging "$i" "gh pr merge #$n failed ($LOGS/merge-$n.log)"; fi
    stop_merging "$i" "#$n is $state after gh pr merge"
  fi
  if ((merge_status)); then
    MERGE_WARNINGS+=("gh pr merge #$n exited $merge_status after the merge ($LOGS/merge-$n.log)")
  fi
  MERGED+=("$n")
  MERGE_AT=$((i + 1))
  say "merge  #$n $(short "$gated")"
done
MERGING=0
trap 'exit 130' INT TERM
note merged "$(joined "${MERGED[@]/#/#}")"
((${#MERGE_WARNINGS[@]} == 0)) || note warnings "$(joined "${MERGE_WARNINGS[@]}")"
move_cards "${MERGED[@]}"

# figure <key> <JOBAPP-VERIFY line>: the value of one key, `-` when the line lacks it.
figure() {
  local value
  value="$(sed -n "s/.* $1=\([^ ]*\).*/\1/p" <<<" $2")"
  printf '%s' "${value:--}"
}
# check_jetson <JOBAPP-VERIFY line>: its figures in the summary; any problem stops the run.
check_jetson() {
  local line="$1" services healthy missing rev head mem_used mem_total errors browser unwell absent
  local problems=()
  services="$(figure services "$line")"
  healthy="$(figure healthy "$line")"
  missing="$(figure missing "$line")"
  rev="$(figure alembic "$line")"
  head="$(figure head "$line")"
  mem_used="$(figure mem_used "$line")"
  mem_total="$(figure mem_total "$line")"
  errors="$(figure errors "$line")"
  browser="$(figure browser "$line")"
  unwell="$(figure unwell "$line")"
  absent="$(figure absent "$line")"
  [[ "$services" != 0 ]] || problems+=("no compose service")
  unwell="${unwell//:/ }"
  [[ "$unwell" == - ]] || problems+=("not healthy: ${unwell//,/, }")
  [[ "$absent" == - ]] || problems+=("missing: ${absent//,/, }")
  [[ "$head" == 1 ]] || problems+=("alembic is not at head")
  [[ "$mem_used" != - ]] || problems+=("memory not read")
  if [[ "$errors" == - ]]; then
    problems+=("error lines not read")
  elif [[ "$errors" != 0 ]]; then
    problems+=("$errors error lines in 10 minutes")
  fi
  [[ "$browser" != running:* ]] || problems+=("browser ${browser/:/ }")
  [[ "$(figure result "$line")" == ok || ${#problems[@]} -gt 0 ]] || problems+=("the check on the Jetson failed")
  note jetson "$services services, $healthy healthy, $missing missing; alembic $rev; memory $mem_used of $mem_total MB used; $errors error lines in 10 minutes; browser $browser"
  ((${#problems[@]} == 0)) || die "the Jetson after the deploy: $(joined "${problems[@]}")"
}

# --- 6. pull, compare, deploy -----------------------------------------------------------------------
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
deploy_last="$(tail -n 1 "$LOGS/deploy.log" | redact)"
# The figures of the run (step 7), whatever its result: a run whose verify step failed is red.
verify="$(grep -E '^JOBAPP-VERIFY services=[0-9]+( [a-z_]+=[A-Za-z0-9_.:,-]+)*$' "$LOGS/deploy.log" | tail -n 1 || true)"
if ((deploy_status)); then
  note deploy "FAIL, exit $deploy_status ($LOGS/deploy.log)"
  [[ "$verify" != *" result=fail"* ]] || check_jetson "$verify"
  die "ops/deploy-checked.sh failed (exit $deploy_status): $deploy_last"
fi
note deploy "ok ($deploy_last)"

# --- 7. the Jetson ----------------------------------------------------------------------------------
say "check  the figures of the deploy run (JOBAPP-VERIFY)"
[[ -n "$verify" ]] ||
  die "the deploy run printed no JOBAPP-VERIFY line ($LOGS/deploy.log): deployed, the Jetson is not checked (ops/remote.sh status)"
check_jetson "$verify"
((${#STUCK[@]} == 0)) || die "deployed and checked, but these cards did not move to Done: $(joined "${STUCK[@]}")"
summary
say "SHIP: OK"
