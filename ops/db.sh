#!/usr/bin/env bash
# Database backups and restores, run ON the Jetson next to compose.yml (platform task 9).
# ./deploy.sh streams this file over ssh (bash -s), so the Mac's copy always runs; the nightly
# cron line (./deploy.sh backup-schedule install) runs the copy deployed in ~/jobapp/ops/.
#
#   ops/db.sh backup [--tag NAME]   pg_dump -Fc of the live database to
#                                   $BACKUP_DIR/jobapp-YYYY-MM-DDTHHMMSS[-NAME].dump (UTC), verified,
#                                   then the retention below
#   ops/db.sh retention             what the retention is and where the number comes from, the
#                                   newest and the oldest dump of each folder, what is left alone
#                                   there, the scratch database if it exists, a rotation that is
#                                   held; changes nothing, and ends with status 3 while one is held
#   ops/db.sh prune                 the count rule alone, in $BACKUP_DIR; never deletes by age,
#                                   and nothing while the rotation of that folder is held
#   ops/db.sh list                  the dumps, oldest first, with their sizes
#   ops/db.sh verify <file>         refuses a missing, empty, foreign, corrupt or truncated dump
#   ops/db.sh restore <file> [--keep]
#                                   into the scratch database jobapp_restore_check, then prints the
#                                   row count of every table; --keep leaves the scratch database
#   ops/db.sh restore-live <file>   replaces the live database: automatic pre-restore backup, api
#                                   and worker stopped, drop, create, restore (rolled back to the
#                                   pre-restore dump if it fails), alembic upgrade head, api and
#                                   worker started healthy.
#                                   No confirmation here: ./deploy.sh restore --into-live asks it.
#
# <file>: a path, a name inside $BACKUP_DIR, or `latest`.
# Settings from the environment: JOBAPP_DIR (deploy directory, default the parent of this script),
# BACKUP_DIR (default ~/jobapp-backups), BACKUP_KEEP (below), BACKUP_MIN_FREE_MB (free disk
# that must remain beyond the size of the database, default 100). Relative paths are under $HOME.
# Every backup and restore holds an flock on $BACKUP_DIR/.lock, so two never overlap.
#
# Retention (T10, issue #49). One number, BACKUP_KEEP, read at each run: the environment of the
# run wins, then the BACKUP_KEEP line of $JOBAPP_DIR/.env, then 7. It sets two rules, applied by
# `backup` once the dump of that run is written and verified, never before, never on a failure:
#   count  the newest BACKUP_KEEP dumps of $BACKUP_DIR stay, the others go;
#   age    a dump whose name is BACKUP_KEEP days old or more goes, in $BACKUP_DIR and, when they
#          exist, in $BACKUP_DIR/deploys (the predeploy dumps of the deploy workflow) and in
#          $BACKUP_DIR/incoming (dumps uploaded from the Mac for a restore).
# The age of a dump is the number of UTC calendar days between the date in its own name and the
# date in the name of the dump the run has just written. Never an mtime: a copied or touched file
# is as old as its name says. Whole days, not seconds, so that two nightly runs a few seconds
# apart never disagree about the dump of 7 nights ago, and so that anyone can check the folder by
# reading names. With 7, a dump of any hour of UTC day D goes with the backup of day D + 7: one
# written between midnight UTC and the hour of that backup lives 7 days plus those hours, every
# other one less than 7 days. Nothing is deleted on a day without a verified backup.
# Never deleted: the dump the run wrote, which is the newest verified dump of the whole tree (so
# there is no exception per folder: the last predeploy dump goes at 7 days like any other, since
# a newer verified dump exists by then); a file whose name is not exactly
# jobapp-YYYY-MM-DDTHHMMSS[-tag].dump with a date that exists; anything that is not a regular file
# (a symbolic link is neither followed nor removed, and a $BACKUP_DIR that is itself a link gets
# its dump and no deletion at all). What is left alone is listed, run after run, and so is the
# scratch database of `restore --keep` while it exists.
# deploys/ is skipped while a backup or a restore holds its lock. Only `backup` deletes by age.
# Three more guards, since "verified" means that a dump reads to its end, not that it is on the
# disk or that the database still held its data (decisions of 2026-10-11, after the review):
#   the new dump and its folder are flushed to the disk before the first deletion (flush);
#   a run never leaves the server with its own dump alone: when every other dump would go, the
#   newest of them stays for one more run (spare_one);
#   a dump less than half the size of the last one accepted in its folder deletes nothing, and
#   neither do the runs after it, until a dump is that size again or one run is made with
#   BACKUP_ACCEPT_SHRINK=1 in its environment (size_check). That hold has no bound but the disk,
#   so it is made to be seen: an ERROR from the seventh run held, status 3 from `retention`,
#   and `prune` refused while it stands.
set -euo pipefail
umask 077
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
# Tests only (tests/deploy_test.sh): a folder of fake docker and flock found first.
if [[ -n "${DB_SH_TEST_BIN:-}" ]]; then
  PATH="$DB_SH_TEST_BIN:$PATH"
fi
export LC_ALL=C

die() {
  printf 'db.sh: %s\n' "$*" >&2
  exit 1
}
info() { printf '   %s\n' "$*"; }

under_home() { # under_home <path>: absolute, relative ones taken under $HOME
  # shellcheck disable=SC2088 # a literal ~/ prefix, replaced by $HOME
  case "$1" in
    /*) printf '%s' "$1" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    *) printf '%s/%s' "$HOME" "$1" ;;
  esac
}

if [[ -n "${JOBAPP_DIR:-}" ]]; then
  APP_DIR="$(under_home "$JOBAPP_DIR")"
elif [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
else
  die "set JOBAPP_DIR when this script is read from stdin"
fi
BACKUP_DIR="$(under_home "${BACKUP_DIR:-jobapp-backups}")"
# One spelling of the folder, used everywhere below: no slash and no /. at its end. With either,
# `[[ -L ... ]]` is false for a symbolic link, and a linked folder would pass for a real one in
# every check that keeps deletions out of it (the review's B3). Then never / or the home folder
# itself: this script sets the mode of that folder and reads every name in it.
while [[ "$BACKUP_DIR" != / && ("$BACKUP_DIR" == */ || "$BACKUP_DIR" == */.) ]]; do
  BACKUP_DIR="${BACKUP_DIR%.}"
  BACKUP_DIR="${BACKUP_DIR%/}"
done
[[ -n "$BACKUP_DIR" && "$BACKUP_DIR" != / && "$BACKUP_DIR" != "${HOME%/}" ]] ||
  die "BACKUP_DIR must name a folder of its own, not / and not the home folder, got '${BACKUP_DIR:-}'"
MIN_FREE_MB="${BACKUP_MIN_FREE_MB:-100}"
SCRATCH_DB=jobapp_restore_check
DEFAULT_KEEP=7
# What users are promised: a deleted document leaves the server's backups within this many days
# (the owner's decision of 2026-10-10, SPEC-ingestion.md, Retention; the delete dialog of the
# ingestion page says it). A BACKUP_KEEP above it still wins and breaks that promise, so every
# run says it.
PROMISED_DAYS=7

[[ "$MIN_FREE_MB" =~ ^[0-9]+$ ]] || die "BACKUP_MIN_FREE_MB must be a whole number, got '$MIN_FREE_MB'"
[[ -f "$APP_DIR/compose.yml" && -f "$APP_DIR/.env" ]] || die "no deployed stack in $APP_DIR (compose.yml and .env)"
case "$BACKUP_DIR/" in
  "$APP_DIR/"*) die "BACKUP_DIR $BACKUP_DIR is inside the deploy directory, which rsync --delete empties" ;;
esac

envval() { sed -n "s/^[[:space:]]*$1=//p" "$APP_DIR/.env" | tail -n 1; }

# BACKUP_KEEP: the environment of this run, then the line of the deployed .env (written as compose
# reads it, so with `export ` before the name too; spaces, a carriage return and one pair of
# quotes around the value dropped), then the default. An explicit value wins for both rules: 14
# keeps 14 dumps and 14 days. A value that is not a number from 1 to 9999 is refused by the
# commands that use it, before they do anything (backup, prune, retention); the commands that
# delete nothing (list, verify, restore, restore-live, drop-scratch) say it and go on, so that a
# typo in .env never stands between the owner and a restore.
if [[ -n "${BACKUP_KEEP:-}" ]]; then
  KEEP="$BACKUP_KEEP"
  KEEP_FROM="set in the environment of this run"
else
  KEEP="$(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?BACKUP_KEEP=//p' "$APP_DIR/.env" | tail -n 1)"
  KEEP="${KEEP%$'\r'}"
  KEEP="${KEEP#"${KEEP%%[![:space:]]*}"}"
  KEEP="${KEEP%"${KEEP##*[![:space:]]}"}"
  case "$KEEP" in
    \"*\")
      KEEP="${KEEP#\"}"
      KEEP="${KEEP%\"}"
      ;;
    \'*\')
      KEEP="${KEEP#\'}"
      KEEP="${KEEP%\'}"
      ;;
  esac
  if [[ -n "$KEEP" ]]; then
    KEEP_FROM="set in $APP_DIR/.env"
  else
    KEEP="$DEFAULT_KEEP"
    KEEP_FROM="the default of ops/db.sh"
  fi
fi
KEEP_ERROR=""
[[ "$KEEP" =~ ^[1-9][0-9]{0,3}$ ]] || KEEP_ERROR="BACKUP_KEEP must be a whole number from 1 to 9999, got '$KEEP' ($KEEP_FROM)"

PGUSER_="$(envval POSTGRES_USER)"
PGDB="$(envval POSTGRES_DB)"
[[ -n "$PGUSER_" && -n "$PGDB" ]] || die "POSTGRES_USER or POSTGRES_DB missing from $APP_DIR/.env"
[[ "$PGDB" != "$SCRATCH_DB" ]] || die "the live database cannot be named $SCRATCH_DB"

dc() { (cd "$APP_DIR" && docker compose -f compose.yml --profile tunnel "$@"); }
# psql_ <database> [psql args]: psql in the db container over its local socket, stops on error
psql_() {
  local db="$1"
  shift
  dc exec -T -e PGOPTIONS="-c client_min_messages=warning" db psql -X -q -v ON_ERROR_STOP=1 -U "$PGUSER_" -d "$db" "$@"
}
human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%sB' "$1"; }

require_db() {
  local id state
  id="$(dc ps -q db 2>/dev/null || true)"
  [[ -n "$id" ]] || die "the db service is not running in $APP_DIR"
  state="$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")"
  [[ "$state" == "running healthy" ]] || die "the db service is '$state', not 'running healthy'"
}

LOCKED=0
take_lock() {
  ((LOCKED == 0)) || return 0
  mkdir -p "$BACKUP_DIR"
  # Not through a symbolic link: the mode of a folder that is somewhere else is not ours to set.
  [[ -L "$BACKUP_DIR" ]] || chmod 700 "$BACKUP_DIR"
  { exec 9>"$BACKUP_DIR/.lock"; } 2>/dev/null ||
    die "cannot open the lock file $BACKUP_DIR/.lock (its mode, or what it is): no backup, nothing deleted"
  flock -n 9 || die "another backup or restore is running on this host (lock $BACKUP_DIR/.lock), try again when it ends"
  LOCKED=1
}

# linked_dir: true when $BACKUP_DIR itself is a symbolic link. A dump is still written through it
# (a backup that is refused is worse than one in a linked folder), but nothing is ever deleted
# through a link, so no rule applies there, and every run and `retention` say it.
linked_dir() {
  [[ -L "$BACKUP_DIR" ]] || return 1
  info "left alone: $BACKUP_DIR is a symbolic link. Nothing is deleted through a link, so no retention applies to what it points to: make it a real folder"
}

# Files to remove, and the api and the worker to start again if the script stops half way,
# whatever the reason. Both use the database: neither may run while it is dropped and restored.
PARTIAL=""
API_STOPPED=0
on_exit() {
  local rc=$?
  [[ -z "$PARTIAL" ]] || rm -f "$PARTIAL"
  if ((API_STOPPED == 1)); then
    printf 'db.sh: starting api and worker again after a failure\n' >&2
    dc start api worker >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap on_exit EXIT

resolve() { # resolve <file>: absolute path of a dump, `latest` or a name in BACKUP_DIR accepted
  local f="$1"
  if [[ "$f" == latest ]]; then
    f="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'jobapp-*.dump' 2>/dev/null | sort | tail -n 1)"
    [[ -n "$f" ]] || die "no backup in $BACKUP_DIR yet"
  elif [[ "$f" != */* ]]; then
    f="$BACKUP_DIR/$f"
  else
    f="$(under_home "$f")"
  fi
  printf '%s' "$f"
}

# verify <file>: a readable, non-empty pg_dump custom archive whose every block reads to the end.
# --list only reads the table of contents at the head of the file; the full read into /dev/null
# is what catches a truncated or damaged data section.
verify() {
  local f="$1" toc err
  [[ -e "$f" ]] || die "no such backup: $f"
  [[ -f "$f" ]] || die "not a regular file: $f"
  [[ -r "$f" ]] || die "cannot read $f"
  [[ -s "$f" ]] || die "empty file, not a backup: $f"
  require_db
  err="$(mktemp)"
  if ! toc="$(dc exec -T db pg_restore --list <"$f" 2>"$err")"; then
    local why
    why="$(head -c 300 "$err")"
    rm -f "$err"
    die "not a valid pg_dump custom-format archive: $f ($why)"
  fi
  if ! dc exec -T db pg_restore -f /dev/null <"$f" 2>"$err"; then
    local why
    why="$(head -c 300 "$err")"
    rm -f "$err"
    die "corrupt or truncated backup, refused: $f ($why)"
  fi
  rm -f "$err"
  VERIFY_ENTRIES="$(grep -cv '^;' <<<"$toc" || true)"
  VERIFY_DB="$(sed -n 's/^;[[:space:]]*dbname: //p' <<<"$toc" | head -n 1)"
}

# --- retention (header: Retention) ----------------------------------------------------------
# A dump's name, whole: the UTC time do_backup wrote and the tag cmd_backup accepts. Nothing the
# rotation deletes is named any other way.
DUMP_RE='^jobapp-([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})(-[a-z0-9-]+)?\.dump$'

# dump_day <file name>: sets DUMP_DAY to the day number (days since 1970-01-01) of the UTC date in
# a dump's name. Fails for any other name, and for a date or a time that does not exist.
dump_day() {
  local y m d dim=31 era yoe doy
  [[ "$1" =~ $DUMP_RE ]] || return 1
  y=$((10#${BASH_REMATCH[1]}))
  m=$((10#${BASH_REMATCH[2]}))
  d=$((10#${BASH_REMATCH[3]}))
  ((10#${BASH_REMATCH[4]} <= 23 && 10#${BASH_REMATCH[5]} <= 59 && 10#${BASH_REMATCH[6]} <= 60)) || return 1
  ((y >= 1970 && m >= 1 && m <= 12 && d >= 1)) || return 1
  case "$m" in
    4 | 6 | 9 | 11) dim=30 ;;
    2)
      dim=28
      if ((y % 4 == 0 && y % 100 != 0 || y % 400 == 0)); then dim=29; fi
      ;;
  esac
  ((d <= dim)) || return 1
  # Days from a civil date (the usual era arithmetic, years counted from March).
  if ((m <= 2)); then y=$((y - 1)); fi
  era=$((y / 400))
  yoe=$((y - era * 400))
  doy=$(((153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1))
  DUMP_DAY=$((era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468))
}

# scan <dir>: DUMPS gets the dumps of <dir> (regular files with a dump's name), oldest first, and
# ODD every other entry, except what this script and the deploy keep there themselves: the two
# lock files, backup.log, the folders deploys, incoming and previous-tree, and the
# previous-tree.rsyncignore that ops/previous.sh writes next to that folder at every deploy.
# Names only, one level, never through a symbolic link: a link is not a dump, whatever it is called.
DUMPS=()
ODD=()
scan() {
  local dir="$1" p name
  DUMPS=()
  ODD=()
  shopt -s nullglob dotglob
  for p in "$dir"/*; do # LC_ALL=C: in byte order, which for dump names is oldest first
    name="${p##*/}"
    if [[ -L "$p" ]]; then
      ODD+=("$name (a symbolic link)")
    elif [[ -f "$p" ]] && dump_day "$name"; then
      DUMPS+=("$name")
    elif [[ -d "$p" ]]; then
      case "$name" in
        deploys | incoming | previous-tree) ;;
        *) ODD+=("$name (a directory)") ;;
      esac
    else
      case "$name" in
        .lock | .deploy.lock | backup.log | previous-tree.rsyncignore | "$HOLD_NAME") ;;
        *) ODD+=("$name") ;;
      esac
    fi
  done
  shopt -u nullglob dotglob
}

# remove_dump <dir> <name>: the one place a dump is deleted. One regular file with a dump's name,
# directly in <dir>: never a pattern, never a directory, never through a symbolic link.
remove_dump() {
  local dir="$1" name="$2"
  [[ "$name" =~ $DUMP_RE ]] || die "refusing to delete '$name': not a dump's name"
  [[ -f "$dir/$name" && ! -L "$dir/$name" ]] || die "refusing to delete $dir/$name: not a regular file"
  rm -f -- "$dir/$name" || die "could not delete $dir/$name"
}

report_odd() { # report_odd <dir>: what scan found there that is not a dump
  local i
  for ((i = 0; i < ${#ODD[@]}; i++)); do
    info "left alone in $1, not a dump of this script: ${ODD[$i]} (outside the retention, delete it by hand)"
  done
}

# A run first plans, over every folder it looks at, then deletes: what goes is decided on the
# whole tree, since one rule needs it (the run never leaves its own dump alone, below).
PLAN_DIR=()   # the deletions planned, folder by folder, oldest first: where,
PLAN_NAME=()  #   which dump,
PLAN_WHY=()   #   and why ('' once spared)
SEEN_DIR=()   # each folder planned,
SEEN_TOTAL=() #   and how many dumps it holds
NOTES=()      # lines for the end of the run: what is left alone, what is named ahead
OTHERS_KEPT=0 # the dumps no rule takes, the run's own not counted
plan_reset() {
  PLAN_DIR=()
  PLAN_NAME=()
  PLAN_WHY=()
  SEEN_DIR=()
  SEEN_TOTAL=()
  NOTES=()
  OTHERS_KEPT=0
}

# plan_dir <dir> <count rule 0|1> <day of the new dump, or ''> <its name, or ''>
# The count rule takes all but the newest BACKUP_KEEP dumps of <dir>. With a day, a dump
# BACKUP_KEEP days old or more goes too, the newest of <dir> like the others. The named dump, the
# one this run wrote and verified, is never planned.
plan_dir() {
  local dir="$1" by_count="$2" ref="$3" fresh="$4" n i name age why
  scan "$dir"
  n=${#DUMPS[@]}
  SEEN_DIR+=("$dir")
  SEEN_TOTAL+=("$n")
  for ((i = 0; i < n; i++)); do
    name="${DUMPS[$i]}"
    [[ "$name" != "$fresh" ]] || continue
    why=""
    if ((by_count == 1 && i < n - KEEP)); then why="beyond the newest $KEEP"; fi
    if [[ -n "$ref" ]]; then
      dump_day "$name"
      age=$((ref - DUMP_DAY))
      if ((age >= KEEP)); then why="$age days old, the limit is $KEEP"; fi
      # A name ahead of this run's: the clock was wrong when one of the two was made. Kept, since
      # it is not old by its name, and said, since it will not go before that date has passed.
      if ((age < 0)) && [[ -z "$why" ]]; then
        NOTES+=("kept $name, whose name is $((-age)) day(s) ahead of this run's (a clock was wrong): by its name it goes in $((KEEP - age)) days, sooner by hand")
      fi
    fi
    if [[ -n "$why" ]]; then
      PLAN_DIR+=("$dir")
      PLAN_NAME+=("$name")
      PLAN_WHY+=("$why")
    elif [[ -s "$dir/$name" ]]; then
      # An empty file with a dump's name stays like any dump by the rules, but it is nothing to
      # restore, so it does not count as a dump left on the server.
      OTHERS_KEPT=$((OTHERS_KEPT + 1))
    fi
  done
  for ((i = 0; i < ${#ODD[@]}; i++)); do
    NOTES+=("left alone in $dir, not a dump of this script: ${ODD[$i]} (outside the retention, delete it by hand)")
  done
}

# spare_one: a run never leaves the server with its own dump alone (decision of 2026-10-11, after
# the review). "Verified" means that the dump reads to its end, not that the database it was
# taken from still held its data: after a week without a backup, or with a clock ahead, every
# other dump is past the limit in the same run. When that is so, the newest of them that is not
# an empty file, by the time in its name, stays for one more run. (Not empty is all that can be
# asked of it here: no record says which older dumps were verified, though every dump this script
# names was.) The next run that has another dump beside its own deletes it. The price is one run
# more than BACKUP_KEEP days for that dump, in that case only: one night when the nightly run is
# the next one and succeeds.
spare_one() {
  local i best=""
  ((OTHERS_KEPT == 0)) || return 0
  for ((i = 0; i < ${#PLAN_NAME[@]}; i++)); do
    [[ -s "${PLAN_DIR[$i]}/${PLAN_NAME[$i]}" ]] || continue
    # the 17 characters of the time alone; at the same time, the folder looked at first wins
    if [[ -z "$best" ]]; then
      best=$i
    elif [[ "${PLAN_NAME[$i]:7:17}" > "${PLAN_NAME[$best]:7:17}" ]]; then
      best=$i
    fi
  done
  [[ -n "$best" ]] || return 0
  info "kept ${PLAN_NAME[$best]} in ${PLAN_DIR[$best]} although it is ${PLAN_WHY[$best]}: every other dump goes in this run, and a run never leaves the server with its own dump alone. The next run that leaves another dump beside its own deletes it"
  PLAN_WHY[best]=""
}

# plan_run: the deletions, then one line per folder and the notes.
plan_run() {
  local i j gone
  for ((i = 0; i < ${#PLAN_NAME[@]}; i++)); do
    [[ -n "${PLAN_WHY[$i]}" ]] || continue
    remove_dump "${PLAN_DIR[$i]}" "${PLAN_NAME[$i]}"
    info "deleted old backup ${PLAN_NAME[$i]} (${PLAN_WHY[$i]})"
  done
  for ((j = 0; j < ${#SEEN_DIR[@]}; j++)); do
    gone=0
    for ((i = 0; i < ${#PLAN_NAME[@]}; i++)); do
      if [[ "${PLAN_DIR[$i]}" == "${SEEN_DIR[$j]}" && -n "${PLAN_WHY[$i]}" ]]; then gone=$((gone + 1)); fi
    done
    info "retention: $((SEEN_TOTAL[j] - gone)) backup(s) kept in ${SEEN_DIR[$j]} (BACKUP_KEEP=$KEEP, $KEEP_FROM)"
  done
  for ((i = 0; i < ${#NOTES[@]}; i++)); do
    info "${NOTES[$i]}"
  done
}

# size_check <name of the run's dump>: fails when the rotation of $BACKUP_DIR is held (decision
# of 2026-10-11, after the review). A database that lost its data still dumps, and the dump still
# verifies. So a dump less than half the size of the last one accepted in its folder deletes
# nothing: the run keeps it, names both sizes, and leaves a mark, .size-hold, that holds the name
# of that last accepted dump. While the mark stands, every run of this folder is measured against
# that dump, not against the small ones that followed, and deletes nothing either. It is lifted by
# a dump that is at least half that size again, or by one run with BACKUP_ACCEPT_SHRINK=1 when the
# database was meant to shrink. That variable is read from the environment of the run alone.
#
# The mark is three lines: the last accepted dump, the first dump held, the number of runs held
# so far. It is read only when it is a regular file that can be read: a symbolic link there is
# never followed, a FIFO or a device never opened. Anything else under that name means that
# whether a hold stands cannot be known, and then nothing is deleted and the run ends non zero.
# It is written to a file beside it and renamed, so it is whole or absent, and a mark that cannot
# be written ends the run non zero too: a hold must not end because it could not be remembered.
# A hold has no bound of its own. Nothing but a larger dump or the owner's variable ends it, the
# folder grows by one dump a run until the disk is full, and the 7 days wait with it. So it has
# to be seen: from the seventh run held the line is an ERROR, `retention` ends with status 3
# while a hold stands, `./deploy.sh backup-schedule status` with 1, and the deploy workflow
# turns the line of its backup into an annotation of the run.
HOLD_NAME=.size-hold
HOLD_ERROR_RUNS=7
RETENTION_HELD=3 # exit status of `retention` while a hold stands or cannot be known

# mark_kind <dir>: sets MARK to none (no mark there), plain (a regular file that can be read) or
# bad (anything else under that name). Decided from the name alone: nothing is opened.
MARK=none
mark_kind() {
  local hold="$1/$HOLD_NAME"
  if [[ ! -e "$hold" && ! -L "$hold" ]]; then
    MARK=none
  elif [[ -f "$hold" && ! -L "$hold" && -r "$hold" ]]; then
    MARK=plain
  else
    MARK=bad
  fi
}

# hold_read <dir>: after mark_kind, sets HOLD_REF (the last accepted dump, '' when there is no
# mark or it names no dump of <dir>), HOLD_FIRST (the first dump held) and HOLD_RUNS.
HOLD_REF=""
HOLD_FIRST=""
HOLD_RUNS=0
hold_read() {
  local dir="$1" a="" b="" c=""
  HOLD_REF=""
  HOLD_FIRST=""
  HOLD_RUNS=0
  mark_kind "$dir"
  [[ "$MARK" == plain ]] || return 0
  {
    IFS= read -r a || true
    IFS= read -r b || true
    IFS= read -r c || true
  } <"$dir/$HOLD_NAME"
  [[ "$a" =~ $DUMP_RE && -f "$dir/$a" && ! -L "$dir/$a" ]] || return 0
  HOLD_REF="$a"
  if [[ "$b" =~ $DUMP_RE ]]; then HOLD_FIRST="$b"; fi
  HOLD_RUNS=1
  if [[ "$c" =~ ^[1-9][0-9]{0,5}$ ]]; then HOLD_RUNS="$c"; fi
}

# hold_since <runs> <first dump held>: WARNING or, from the seventh run held, ERROR, with the count.
hold_since() {
  local runs="$1" first="$2"
  if ((runs >= HOLD_ERROR_RUNS)); then
    printf 'ERROR: held for %s runs, since %s. Nothing is deleted there, the folder grows by a dump at every run without any bound but the disk, and the 7 days wait with it' "$runs" "${first:-an earlier run}"
  else
    printf 'WARNING: held for %s run(s), since %s' "$runs" "${first:-an earlier run}"
  fi
}

size_check() {
  local fresh="$1" dir="$BACKUP_DIR" hold="$BACKUP_DIR/$HOLD_NAME" new ref first runs ref_size i name
  new="$(stat -c %s "$BACKUP_FILE")"
  hold_read "$dir"
  if [[ "$MARK" == bad ]]; then
    ROTATE_TROUBLE="$hold is not a regular file that can be read (a symbolic link, a FIFO, a folder, or its mode): whether the rotation of $dir is held cannot be known, so nothing was deleted. Look at it, remove it, and run a backup"
    info "WARNING: $ROTATE_TROUBLE"
    return 1
  fi
  if [[ -n "${BACKUP_ACCEPT_SHRINK:-}" ]]; then
    if [[ "$MARK" == plain ]]; then
      rm -f "$hold" || {
        ROTATE_TROUBLE="could not remove the mark $hold: nothing was deleted"
        return 1
      }
      info "BACKUP_ACCEPT_SHRINK is set: the size of this dump ($new bytes) is accepted and the hold on the rotation of $dir is lifted"
    else
      info "size check: BACKUP_ACCEPT_SHRINK is set, this dump ($new bytes) is accepted as it is"
    fi
    return 0
  fi
  ref="$HOLD_REF"
  first="$HOLD_FIRST"
  runs="$HOLD_RUNS"
  if [[ -z "$ref" || "$ref" == "$fresh" ]]; then
    # No hold stands. The dump to be measured against is the newest other dump of the folder that
    # a backup run measured in its turn: not a pre-restore dump, which restore-live writes without
    # this check, not a dump named after this one, whose date a wrong clock wrote, and not an
    # empty file, against which any dump would pass.
    [[ "$MARK" != plain ]] || info "size check: the mark $hold names no dump of this folder, so no hold stands on its word"
    ref=""
    first=""
    runs=0
    scan "$dir"
    for ((i = ${#DUMPS[@]} - 1; i >= 0; i--)); do
      name="${DUMPS[$i]}"
      [[ "$name" != "$fresh" && "$name" != *-pre-restore.dump && -s "$dir/$name" ]] || continue
      [[ ! "${name:7:17}" > "${fresh:7:17}" ]] || continue
      ref="$name"
      break
    done
  fi
  if [[ -n "$ref" ]]; then
    ref_size="$(stat -c %s "$dir/$ref")"
    if ((new * 2 < ref_size)); then
      runs=$((runs + 1))
      [[ -n "$first" ]] || first="$fresh"
      # Whole or absent: written to a new file beside the mark, then renamed over it. A new
      # file: whatever waits under that name is removed first (a link itself, never what it
      # points to), and the write refuses to open anything that exists (set -C).
      rm -f "$hold.new" 2>/dev/null || true
      if ! { (
        set -C
        printf '%s\n%s\n%s\n' "$ref" "$first" "$runs" >"$hold.new"
      ) && mv -f -- "$hold.new" "$hold"; } 2>/dev/null; then
        ROTATE_TROUBLE="could not write the mark $hold: this run deleted nothing, but the hold on the rotation of $dir is not remembered, and the next run may delete"
      fi
      info "$(hold_since "$runs" "$first"). This dump is $new bytes, less than half of $ref ($ref_size bytes), the last one accepted in $dir. A database that lost its data dumps like this, so this run keeps its dump and deletes NOTHING, in any folder, and so does every run of this folder until a dump is at least half that size again. Look at the database today. If it was meant to shrink, or was restored to a smaller state, run one backup of this folder with BACKUP_ACCEPT_SHRINK=1"
      return 1
    fi
    info "size check: this dump is $new bytes, measured against $ref ($ref_size bytes): passed"
  else
    info "size check: this dump is $new bytes, with no other dump of this folder to be measured against: passed"
  fi
  if [[ "$MARK" == plain ]]; then
    rm -f "$hold" || {
      ROTATE_TROUBLE="could not remove the mark $hold: nothing was deleted"
      return 1
    }
    [[ -z "$HOLD_REF" ]] || info "the size check passes again after $HOLD_RUNS run(s) held: the hold on the rotation of $dir is lifted"
  fi
  return 0
}

# hold_note <dir>: said by `retention` and by `prune` while the rotation of <dir> is held, or
# while its mark cannot be read. Sets HELD to 1 then, and prints held=<dir> for ./deploy.sh.
HELD=0
hold_note() {
  local dir="$1" n
  hold_read "$dir"
  if [[ "$MARK" == bad ]]; then
    HELD=1
    info "WARNING: $dir/$HOLD_NAME is not a regular file that can be read (a symbolic link, a FIFO, a folder, or its mode): whether the rotation of $dir is held cannot be known, and its backups delete nothing until it is removed"
    return 0
  fi
  [[ -n "$HOLD_REF" ]] || return 0
  HELD=1
  scan "$dir"
  n=${#DUMPS[@]}
  info "$(hold_since "$HOLD_RUNS" "$HOLD_FIRST"): the rotation of $dir. A dump came out less than half the size of $HOLD_REF ($(stat -c %s "$dir/$HOLD_REF") bytes), the last one accepted there, and its newest is ${DUMPS[$((n - 1))]} ($(stat -c %s "$dir/${DUMPS[$((n - 1))]}") bytes). The backups of that folder delete nothing until a dump is at least half that size again, or until one is run with BACKUP_ACCEPT_SHRINK=1"
  printf 'held=%s\n' "$dir"
}

promise_note() { # said by every run whose retention is longer than what users are promised
  ((KEEP <= PROMISED_DAYS)) ||
    info "WARNING: BACKUP_KEEP=$KEEP keeps dumps up to $KEEP days, more than the $PROMISED_DAYS days within which a deleted document is promised to leave the server's backups: with this setting that promise is not kept"
}

# scratch_note: the scratch database of `restore --keep` is a whole copy of a dump inside
# Postgres, which no rule here deletes. Said by every backup and by `retention` while it exists;
# never dropped here. A database that does not answer is said too, never taken for "none".
scratch_note() {
  local n=""
  n="$(psql_ postgres -tA -c "select count(*) from pg_database where datname = '$SCRATCH_DB'" 2>/dev/null)" || n=""
  case "${n//[[:space:]]/}" in
    0) ;;
    1) info "left alone: the scratch database $SCRATCH_DB exists, a whole copy of a dump kept by restore --keep and outside the retention (drop it with ./deploy.sh restore --drop-scratch)" ;;
    *) info "not checked: whether the scratch database $SCRATCH_DB of restore --keep exists (the db service did not answer)" ;;
  esac
}

# rotate: after do_backup, and only then. The day everything is measured from is the one in the
# name of the dump this run wrote, so there is no rotation without a verified dump, and that dump
# is the one thing no rule deletes: the newest verified dump of the whole tree.
ROTATE_TROUBLE="" # why the rotation is not complete, when it is not: cmd_backup ends non zero on it
rotate() {
  local fresh="${BACKUP_FILE##*/}" ref sub dir i held_deploys=0
  if [[ ! -f "$BACKUP_FILE" || -L "$BACKUP_FILE" ]] || ! dump_day "$fresh"; then
    die "no verified dump from this run to measure ages from, nothing deleted"
  fi
  ref="$DUMP_DAY"
  if linked_dir || ! size_check "$fresh"; then
    scratch_note
    promise_note
    return 0
  fi
  plan_reset
  plan_dir "$BACKUP_DIR" 1 "$ref" "$fresh"
  for sub in deploys incoming; do
    dir="$BACKUP_DIR/$sub"
    [[ -e "$dir" || -L "$dir" ]] || continue
    if [[ -L "$dir" || ! -d "$dir" ]]; then
      info "left alone: $dir is a symbolic link or a file, not a folder of dumps"
      continue
    fi
    if [[ "$sub" == deploys ]]; then
      # A predeploy backup, or a restore run from that folder, holds its lock: never under them.
      # (The other way round, a deploy that asks for this lock in the instant it is held here is
      # refused like any second backup, and is run again.) A lock file that cannot be opened is
      # another matter: nothing holds it, the age rule would never run there again, so the run
      # says what it is and ends non zero once the rest is done. Either way the dumps of the
      # folder stay, and they count as dumps this run leaves on the server.
      if ! { exec 8>>"$dir/.lock"; } 2>/dev/null; then
        ROTATE_TROUBLE="cannot open the lock file $dir/.lock (its mode, or what it is): nothing was deleted in $dir and nothing will be until that is put right"
        info "skipped $dir: $ROTATE_TROUBLE"
        held_deploys=2
      elif ! flock -n 8; then
        info "skipped $dir: a backup or a restore holds its lock, the next run deletes what is too old there"
        exec 8>&-
        held_deploys=2
      else
        held_deploys=1 # descriptor 8 holds the lock until the deletions are done
      fi
      if ((held_deploys == 2)); then
        scan "$dir"
        for ((i = 0; i < ${#DUMPS[@]}; i++)); do
          if [[ -s "$dir/${DUMPS[$i]}" ]]; then OTHERS_KEPT=$((OTHERS_KEPT + 1)); fi
        done
        continue
      fi
    fi
    plan_dir "$dir" 0 "$ref" ""
  done
  spare_one
  plan_run
  ((held_deploys != 1)) || exec 8>&-
  scratch_note
  promise_note
}

# flush <path>: the data of a file, or the entries of a folder, on the disk before the script goes
# on. Without it a new dump lives in the page cache for half a minute while the deletions that
# follow are journaled within seconds, and a power cut in between leaves an empty file under the
# new name and the old dumps gone. `sync <path>` is fsync, in coreutils 8.24 and later (Ubuntu
# 22.04 has 8.32); a sync that takes no path flushes every disk instead. A flush that fails ends
# the run: nothing is deleted on the word of a dump that may not be on the disk.
SYNC_TAKES_PATHS=""
flush() {
  local help
  if [[ -z "$SYNC_TAKES_PATHS" ]]; then
    help="$(sync --help 2>/dev/null || true)"
    if [[ "$help" == *FILE* ]]; then SYNC_TAKES_PATHS=1; else SYNC_TAKES_PATHS=0; fi
  fi
  if ((SYNC_TAKES_PATHS == 1)); then
    sync "$1" || die "could not flush $1 to the disk, nothing deleted"
  else
    sync || die "could not flush the disks, nothing deleted"
  fi
}

# do_backup [tag]: sets BACKUP_FILE. The dump streams out of the container into a hidden .partial
# file, is verified, flushed to the disk, renamed, and the folder flushed too: a half-written dump
# never carries a backup's name, and a named one is on the disk before anything else happens.
do_backup() {
  local tag="${1:-}" size_b avail_kb need_kb name final
  take_lock
  require_db
  size_b="$(psql_ "$PGDB" -tA -c 'select pg_database_size(current_database())')"
  avail_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')"
  need_kb=$((size_b / 1024 + MIN_FREE_MB * 1024))
  ((avail_kb > need_kb)) ||
    die "not enough disk for a backup: $((avail_kb / 1024))M free in $BACKUP_DIR, need more than $((need_kb / 1024))M (database $(human "$size_b") plus BACKUP_MIN_FREE_MB=$MIN_FREE_MB)"
  while :; do
    name="jobapp-$(date -u +%Y-%m-%dT%H%M%S)${tag:+-$tag}.dump"
    final="$BACKUP_DIR/$name"
    [[ -e "$final" ]] || break
    sleep 1
  done
  PARTIAL="$BACKUP_DIR/.$name.partial"
  local err
  err="$(mktemp)"
  if ! dc exec -T db pg_dump -U "$PGUSER_" -d "$PGDB" -Fc --no-password >"$PARTIAL" 2>"$err"; then
    local why
    why="$(head -c 300 "$err")"
    rm -f "$err"
    die "pg_dump failed, nothing kept: $why"
  fi
  rm -f "$err"
  verify "$PARTIAL"
  chmod 600 "$PARTIAL"
  flush "$PARTIAL"
  mv -- "$PARTIAL" "$final"
  PARTIAL=""
  flush "$BACKUP_DIR"
  BACKUP_FILE="$final"
  info "backup: $final"
  info "size: $(human "$(stat -c %s "$final")") ($(stat -c %s "$final") bytes), database $(human "$size_b")"
  info "verified: pg_restore --list ($VERIFY_ENTRIES entries) and a full read"
}

row_counts() { # row_counts <database>: exact count(*) of every table outside the system schemas
  psql_ "$1" -P footer=off -c "
    select n.nspname || '.' || c.relname as \"table\",
           (xpath('/row/c/text()', query_to_xml(
              format('select count(*) as c from %I.%I', n.nspname, c.relname), false, true, '')))[1]::text::bigint
             as \"rows\"
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r', 'p') and n.nspname not in ('pg_catalog', 'information_schema')
      and n.nspname not like 'pg_toast%'
    order by 1"
}

# restore_into <database> <file>: drop, create, pg_restore stopping at the first error.
restore_into() {
  local db="$1" f="$2"
  psql_ postgres -c "drop database if exists \"$db\" with (force)" \
    -c "create database \"$db\" owner \"$PGUSER_\"" || return 1
  dc exec -T db pg_restore -U "$PGUSER_" -d "$db" --no-owner --exit-on-error <"$f" || return 1
}

cmd_backup() {
  local tag=""
  while (($#)); do
    case "$1" in
      --tag)
        [[ $# -ge 2 && "$2" =~ ^[a-z0-9-]+$ ]] || die "--tag needs a name of lowercase letters, digits and hyphens"
        tag="$2"
        shift 2
        ;;
      *) die "unknown option for backup: $1" ;;
    esac
  done
  [[ -z "${BACKUP_ACCEPT_SHRINK:-}" || "$BACKUP_ACCEPT_SHRINK" == 1 ]] ||
    die "BACKUP_ACCEPT_SHRINK must be 1 or not set, got '$BACKUP_ACCEPT_SHRINK'"
  # The order is the safety: a dump that could not be written, verified or flushed to the disk
  # ends the script in do_backup, before anything is deleted.
  do_backup "$tag"
  rotate
  printf 'backup=%s\n' "$BACKUP_FILE"
  [[ -z "$ROTATE_TROUBLE" ]] || die "the dump is written and kept, but the rotation is not complete: $ROTATE_TROUBLE"
}

# cmd_prune: the count rule in $BACKUP_DIR, as before T10. No dump was written by this run, so
# there is no day to measure ages from and nothing is deleted by age.
cmd_prune() {
  take_lock
  if linked_dir; then return 0; fi
  # A hold is a hold for every command that deletes: what prune would take by count is exactly
  # the older, larger dumps the hold exists to keep (the review's N11).
  HELD=0
  hold_note "$BACKUP_DIR"
  ((HELD == 0)) || die "prune deletes nothing while the rotation of $BACKUP_DIR is held, or while its mark cannot be read (above). Lift the hold first: a backup with BACKUP_ACCEPT_SHRINK=1 if the database was meant to shrink"
  plan_reset
  plan_dir "$BACKUP_DIR" 1 "" ""
  plan_run
  info "prune applies the count rule alone: only a backup, once its dump is verified, deletes by age"
}

# cmd_retention: what a backup run here keeps, and whether the folders show it: the number and
# where it comes from, the newest and the oldest dump of each folder against today's UTC date,
# what is left alone, the scratch database. Changes nothing and takes no lock.
cmd_retention() {
  local today dir n i newest oldest note ahead ahead_name
  dump_day "jobapp-$(date -u +%Y-%m-%dT%H%M%S).dump" || die "cannot read today's date from the clock"
  today="$DUMP_DAY"
  info "retention: BACKUP_KEEP=$KEEP ($KEEP_FROM). After each verified backup the newest $KEEP dumps of $BACKUP_DIR stay, and a dump whose name is $KEEP days old or more is deleted, there, in deploys/ and in incoming/"
  promise_note
  if linked_dir; then
    scratch_note
    printf 'keep=%s\n' "$KEEP"
    return 0
  fi
  for dir in "$BACKUP_DIR" "$BACKUP_DIR/deploys" "$BACKUP_DIR/incoming"; do
    # As rotate does: a folder of dumps is a real folder, never a symbolic link.
    if [[ "$dir" != "$BACKUP_DIR" ]] && [[ -L "$dir" || (-e "$dir" && ! -d "$dir") ]]; then
      info "left alone: $dir is a symbolic link or a file, not a folder of dumps"
      continue
    fi
    [[ -d "$dir" ]] || continue
    scan "$dir"
    n=${#DUMPS[@]}
    # Ages are taken from the dumps named up to today. One named ahead of today (a clock that was
    # wrong) is counted and named apart: it has no age yet, and it must not hide a week without a
    # backup behind a date in the future.
    newest="" ahead=0 ahead_name=""
    for ((i = 0; i < n; i++)); do
      dump_day "${DUMPS[$i]}"
      if ((DUMP_DAY > today)); then
        ahead=$((ahead + 1))
        ahead_name="${DUMPS[$i]}"
      else
        newest=$((today - DUMP_DAY))
      fi
    done
    if ((n == 0)); then
      info "$dir: no dump"
    elif [[ -z "$newest" ]]; then
      info "$dir: $n dump(s), every one named ahead of today (a clock was wrong), the last $ahead_name: none goes before its date has passed, sooner by hand"
    else
      dump_day "${DUMPS[0]}"
      oldest=$((today - DUMP_DAY))
      note=""
      ((oldest < KEEP)) || note=", PAST the limit of $KEEP days: the next verified backup deletes it"
      ((ahead == 0)) || note="$note; $ahead more named ahead of today (a clock was wrong), the last $ahead_name: it goes once its date has passed, sooner by hand"
      info "$dir: $((n - ahead)) dump(s), the newest $newest day(s) old, the oldest $oldest day(s) old (${DUMPS[0]})$note"
    fi
    # In the folder of the nightly run a newest dump of two days or more, or none dated up to
    # today, means that run failed or did not happen, and then nothing is deleted anywhere.
    if [[ "$dir" == "$BACKUP_DIR" ]] && ((n > 0)); then
      if [[ -z "$newest" ]]; then
        info "WARNING: no verified backup in $dir dated up to today: nothing is deleted until one succeeds"
      elif ((newest >= 2)); then
        info "WARNING: no verified backup in $dir for $newest days: nothing is deleted until one succeeds"
      fi
    fi
    report_odd "$dir"
    hold_note "$dir"
  done
  scratch_note
  printf 'keep=%s\n' "$KEEP"
  # A hold must be seen (the review's N12): the report is complete, and its status says so.
  ((HELD == 0)) || exit "$RETENTION_HELD"
}

cmd_list() {
  [[ -d "$BACKUP_DIR" ]] || {
    info "no backup directory yet: $BACKUP_DIR"
    return 0
  }
  local f n=0
  while IFS= read -r f; do
    printf '   %s  %8s  %s\n' "$(stat -c %a "$f")" "$(human "$(stat -c %s "$f")")" "$f"
    n=$((n + 1))
  done < <(find "$BACKUP_DIR" -maxdepth 2 -type f -name '*.dump' | sort)
  info "$n dump(s) in $BACKUP_DIR (directory mode $(stat -c %a "$BACKUP_DIR"))"
  if [[ -n "$KEEP_ERROR" ]]; then
    info "retention: none while this stands: $KEEP_ERROR"
  else
    info "retention: BACKUP_KEEP=$KEEP ($KEEP_FROM): after each verified backup the newest $KEEP stay and a dump $KEEP days old or more goes"
  fi
}

cmd_verify() {
  [[ $# -eq 1 ]] || die "usage: db.sh verify <file>"
  local f
  f="$(resolve "$1")"
  verify "$f"
  info "ok: $f, $(human "$(stat -c %s "$f")"), $VERIFY_ENTRIES entries, dumped from database '${VERIFY_DB:-?}'"
  printf 'file=%s\n' "$f"
}

cmd_restore() {
  local keep=0 file=""
  while (($#)); do
    case "$1" in
      --keep) keep=1 ;;
      -*) die "unknown option for restore: $1" ;;
      *)
        [[ -z "$file" ]] || die "restore takes one file"
        file="$1"
        ;;
    esac
    shift
  done
  [[ -n "$file" ]] || die "usage: db.sh restore <file> [--keep]"
  file="$(resolve "$file")"
  take_lock
  verify "$file"
  info "verified $file ($VERIFY_ENTRIES entries)"
  restore_into "$SCRATCH_DB" "$file" || die "pg_restore into $SCRATCH_DB failed, see above"
  info "restored into the scratch database $SCRATCH_DB; row counts:"
  row_counts "$SCRATCH_DB"
  if ((keep)); then
    info "kept $SCRATCH_DB (drop it with: ./deploy.sh restore --drop-scratch)"
  else
    psql_ postgres -c "drop database if exists \"$SCRATCH_DB\" with (force)"
    info "dropped $SCRATCH_DB; the live database $PGDB was not touched"
  fi
}

cmd_drop_scratch() {
  take_lock
  require_db
  psql_ postgres -c "drop database if exists \"$SCRATCH_DB\" with (force)"
  info "dropped $SCRATCH_DB if it existed"
}

cmd_restore_live() {
  [[ $# -eq 1 ]] || die "usage: db.sh restore-live <file>"
  local file pre
  file="$(resolve "$1")"
  take_lock
  verify "$file"
  info "verified $file ($VERIFY_ENTRIES entries)"
  printf '\n== pre-restore backup of the live database %s\n' "$PGDB"
  do_backup pre-restore
  pre="$BACKUP_FILE"
  printf '\n== stop api and worker\n'
  API_STOPPED=1
  dc stop api worker
  printf '\n== drop, create and restore %s\n' "$PGDB"
  if ! restore_into "$PGDB" "$file"; then
    printf 'db.sh: restore of %s failed; rolling back to %s\n' "$file" "$pre" >&2
    restore_into "$PGDB" "$pre" || die "ROLLBACK FAILED too: the live database is incomplete; restore $pre by hand"
    dc up -d --no-deps --wait --wait-timeout 120 api worker
    API_STOPPED=0
    die "restore failed, the live database was rolled back to $pre"
  fi
  printf '\n== alembic upgrade head\n'
  dc run --rm --no-deps -T api alembic upgrade head
  printf '\n== start api and worker\n'
  dc up -d --no-deps --wait --wait-timeout 120 api worker
  API_STOPPED=0
  info "row counts of $PGDB after the restore:"
  row_counts "$PGDB"
  info "restored $file into $PGDB; the previous content is in $pre"
}

cmd="${1:-}"
[[ $# -eq 0 ]] || shift
# An invalid BACKUP_KEEP (above): refused here, before anything, by the commands that use it.
case "$cmd" in
  backup | prune | retention) [[ -z "$KEEP_ERROR" ]] || die "$KEEP_ERROR" ;;
  list | verify | restore | restore-live | drop-scratch)
    [[ -z "$KEEP_ERROR" ]] ||
      printf 'db.sh: WARNING: %s. %s deletes nothing and goes on; every backup is refused until this is put right\n' "$KEEP_ERROR" "$cmd" >&2
    ;;
esac
case "$cmd" in
  backup) cmd_backup "$@" ;;
  list) cmd_list ;;
  verify) cmd_verify "$@" ;;
  restore) cmd_restore "$@" ;;
  restore-live) cmd_restore_live "$@" ;;
  drop-scratch) cmd_drop_scratch ;;
  prune) cmd_prune ;;
  retention) cmd_retention ;;
  *) die "usage: db.sh backup [--tag NAME] | list | verify <file> | restore <file> [--keep] | restore-live <file> | drop-scratch | prune | retention" ;;
esac
