#!/usr/bin/env bash
# Database backups and restores, run ON the Jetson next to compose.yml (platform task 9).
# ./deploy.sh streams this file over ssh (bash -s), so the Mac's copy always runs; the nightly
# cron line (./deploy.sh backup-schedule install) runs the copy deployed in ~/jobapp/ops/.
#
#   ops/db.sh backup [--tag NAME]   pg_dump -Fc of the live database to
#                                   $BACKUP_DIR/jobapp-YYYY-MM-DDTHHMMSS[-NAME].dump (UTC), verified,
#                                   then keeps the newest $BACKUP_KEEP dumps and deletes the rest
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
# BACKUP_DIR (default ~/jobapp-backups), BACKUP_KEEP (default 14), BACKUP_MIN_FREE_MB (free disk
# that must remain beyond the size of the database, default 100). Relative paths are under $HOME.
# Every backup and restore holds an flock on $BACKUP_DIR/.lock, so two never overlap.
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
KEEP="${BACKUP_KEEP:-14}"
MIN_FREE_MB="${BACKUP_MIN_FREE_MB:-100}"
SCRATCH_DB=jobapp_restore_check

[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || die "BACKUP_KEEP must be a whole number of at least 1, got '$KEEP'"
[[ "$MIN_FREE_MB" =~ ^[0-9]+$ ]] || die "BACKUP_MIN_FREE_MB must be a whole number, got '$MIN_FREE_MB'"
[[ -f "$APP_DIR/compose.yml" && -f "$APP_DIR/.env" ]] || die "no deployed stack in $APP_DIR (compose.yml and .env)"
case "$BACKUP_DIR/" in
  "$APP_DIR/"*) die "BACKUP_DIR $BACKUP_DIR is inside the deploy directory, which rsync --delete empties" ;;
esac

envval() { sed -n "s/^[[:space:]]*$1=//p" "$APP_DIR/.env" | tail -n 1; }
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
  chmod 700 "$BACKUP_DIR"
  exec 9>"$BACKUP_DIR/.lock"
  flock -n 9 || die "another backup or restore is running on this host (lock $BACKUP_DIR/.lock), try again when it ends"
  LOCKED=1
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

prune() {
  local files n i
  mapfile -t files < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'jobapp-*.dump' | sort)
  n=${#files[@]}
  if ((n > KEEP)); then
    for ((i = 0; i < n - KEEP; i++)); do
      rm -f -- "${files[$i]}"
      info "deleted old backup $(basename "${files[$i]}")"
    done
  fi
  info "retention: $((n > KEEP ? KEEP : n)) backup(s) kept in $BACKUP_DIR (BACKUP_KEEP=$KEEP)"
}

# do_backup [tag]: sets BACKUP_FILE. The dump streams out of the container into a hidden .partial
# file, is verified, then renamed, so a half-written dump never carries a backup's name.
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
  mv -- "$PARTIAL" "$final"
  PARTIAL=""
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
  do_backup "$tag"
  prune
  printf 'backup=%s\n' "$BACKUP_FILE"
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
case "$cmd" in
  backup) cmd_backup "$@" ;;
  list) cmd_list ;;
  verify) cmd_verify "$@" ;;
  restore) cmd_restore "$@" ;;
  restore-live) cmd_restore_live "$@" ;;
  drop-scratch) cmd_drop_scratch ;;
  prune)
    take_lock
    prune
    ;;
  *) die "usage: db.sh backup [--tag NAME] | list | verify <file> | restore <file> [--keep] | restore-live <file> | drop-scratch | prune" ;;
esac
