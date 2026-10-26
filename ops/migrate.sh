#!/usr/bin/env bash
# Apply the database migrations of the images just built, BEFORE the new code starts (issue #81).
# Run ON the Jetson in the deploy directory, next to compose.yml and .env, by ./deploy.sh (over
# ssh) and by .github/workflows/deploy.yml, both under the Jetson's deploy lock.
#
#   ops/migrate.sh
#
# `alembic upgrade head` in a one off container of the api service as compose.yml defines it NOW,
# so of the image the deploy has just built: the same env_file and network as the api, no
# published port (`compose run` publishes none, and the api has none), removed when it ends. The
# api and worker containers that are running are not touched: the previous release keeps serving
# while the schema moves, which is why a migration must stay readable by the previous release
# (expand first, contract in a later release: CONTRIBUTING.md, Migrations).
#
# Fails safe. Any failure exits non zero before anything was restarted:
#   a failing migration   alembic runs the whole upgrade in one transaction (alembic/env.py), so
#                         Postgres rolls it back and the schema is what it was
#   a lock timeout        a statement that waited LOCK_TIMEOUT (5 s) for a lock the running
#                         release holds fails with SQLSTATE 55P03 and the upgrade rolls back
#                         whole. That is no fault of the migration, so it is run again, at most
#                         MIGRATE_ATTEMPTS times in all, MIGRATE_RETRY_WAIT_S apart (issue #408);
#                         after the last one it stops with "lock timeout: run the deploy again"
#   a timeout             the container is killed after MIGRATE_TIMEOUT_S. Postgres only notices
#                         a client that went away when the running statement ends, and would
#                         keep the migration's locks until then, so the sessions of this database
#                         that hold or wait for the migration's advisory lock are terminated:
#                         the open transaction rolls back at once
#   a kill                exit 137 before the limit is not a timeout: the container was killed,
#                         possibly for memory (the api's mem_limit). Said as such, and its
#                         session is ended in Postgres the same way
#   another run          a migration container that is still running is never killed: this run
#                         stops. Two upgrades that do meet queue on the advisory lock taken in
#                         alembic/env.py (pg_advisory_xact_lock), they never race
# It never downgrades. At head it is a no-op that costs one container start.
#
# The database must be reachable: a db service that is not running is started first (a first
# install creates the volume and the empty database here); one that runs is left exactly as it is.
#
# Output: figures only, because .github/workflows/deploy.yml runs it and the run log of this public
# repository is readable by anyone: each step as it starts (`step: <from> -> <to>`, revision ids),
# the revision reached, and on a failure the step it failed in, the exit code and the class of the
# exception, never a line of alembic's own output (a failing data migration prints SQL with its
# parameters and Postgres' DETAIL with row values). The whole output is kept in a file on this
# machine, MIGRATE_LOG_DIR/migrate-<time>.log (mode 600, its folder 700, the newest 20 kept),
# which is never inside the deploy directory; the credentials of anything shaped like a URL are
# masked there too.
#
# Settings from the environment: COMPOSE (default: docker compose -f compose.yml --profile tunnel),
# MIGRATE_TIMEOUT_S (default 300), MIGRATE_CONTAINER (default jobapp-migrate, one per project),
# MIGRATE_LOG_DIR (default: logs in BACKUP_DIR, else ~/jobapp-backups/logs), MIGRATE_ATTEMPTS
# (default 3) and MIGRATE_RETRY_WAIT_S (default 5) for a lock timeout.
set -euo pipefail
export LC_ALL=C

COMPOSE="${COMPOSE:-docker compose -f compose.yml --profile tunnel}"
TIMEOUT_S="${MIGRATE_TIMEOUT_S:-300}"
ATTEMPTS="${MIGRATE_ATTEMPTS:-3}"
RETRY_WAIT_S="${MIGRATE_RETRY_WAIT_S:-5}"
NAME="${MIGRATE_CONTAINER:-jobapp-migrate}"
LOG_DIR="${MIGRATE_LOG_DIR:-${BACKUP_DIR:-$HOME/jobapp-backups}/logs}"
LOG_KEEP=20
DB_WAIT_S=120
# MIGRATION_LOCK_ID of api/alembic/env.py: every `alembic upgrade` session holds or waits for it.
MIGRATION_LOCK_ID=7406202610

die() {
  printf 'migrate.sh: %s\n' "$*" >&2
  exit 1
}
info() { printf '   %s\n' "$*"; }
# word splitting of $COMPOSE is wanted: it is a command with its arguments
# shellcheck disable=SC2086
dc() { $COMPOSE "$@"; }

# figures <file>: alembic's output, line by line. Every line goes to <file>, the user:password@ of
# anything shaped like a URL masked as ***:***@; only the step of each `Running upgrade <from> ->
# <to>, <message>` comes out, its ids as plain tokens (`-` otherwise, `base` for the first one).
# Flushed, so a long migration shows its progress as it goes.
figures() {
  awk -v file="$1" '
    { gsub("://[^:/@ \t]*:[^@ \t]*@", "://***:***@"); print >> file; fflush(file) }
    /Running upgrade / {
      s = $0; sub(/.*Running upgrade /, "", s); n = split(s, a, " -> ")
      from = a[1]; to = (n > 1) ? a[2] : ""; sub(/,.*/, "", to)
      if (from == "") from = "base"
      if (from !~ /^[A-Za-z0-9_]+$/) from = "-"
      if (to !~ /^[A-Za-z0-9_]+$/) to = "-"
      printf "   step: %s -> %s\n", from, to; fflush()
    }'
}
# attempt_mark <n>: the line written to the kept file before each attempt (through figures, so it
# never reaches the output), where the figures of failed_at start again.
attempt_mark() { printf '== migrate.sh attempt %s of %s ==\n' "$1" "$ATTEMPTS"; }
# failed_at <file>: four figures of the last attempt in <file> (the lines after the last attempt
# mark): the step it was in, the class of its last exception (a dotted name or one ending in Error
# or Exception, at the start of a line), each `-` when there is none; then `lock` when that last
# exception line is a lock timeout (SQLSTATE 55P03, psycopg's LockNotAvailable: the upgrade waited
# on the running release), `-` otherwise; then how many steps it started. Only the last exception
# line decides the lock timeout, so a migration that printed those words, or a lock timeout caught
# before another failure, is that other failure.
failed_at() {
  awk '
    /^== migrate\.sh attempt [0-9]+ of [0-9]+ ==$/ { step = ""; cls = ""; last = ""; n = 0; next }
    /Running upgrade / { n++; s = $0; sub(/.*Running upgrade /, "", s); split(s, a, " -> "); from = a[1]; to = a[2]; sub(/,.*/, "", to); if (from == "") from = "base"; step = from " -> " to }
    /^([A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*(: |:?$)/ || /^[A-Za-z_][A-Za-z0-9_]*(Error|Exception)(: |:?$)/ { last = $0; c = $0; sub(/:.*/, "", c); cls = c }
    END {
      if (step !~ /^[A-Za-z0-9_]+ -> [A-Za-z0-9_]+$/) step = "-"
      if (cls == "") cls = "-"
      print step; print cls
      print ((last ~ /LockNotAvailable|55P03|canceling statement due to lock timeout/) ? "lock" : "-")
      print n + 0
    }' "$1"
}

[[ $# -eq 0 ]] || die "usage: ops/migrate.sh (no argument; settings come from the environment)"
[[ "$TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] || die "MIGRATE_TIMEOUT_S must be a whole number of seconds, got '$TIMEOUT_S'"
[[ "$ATTEMPTS" =~ ^[1-9]$ ]] || die "MIGRATE_ATTEMPTS must be a whole number from 1 to 9, got '$ATTEMPTS'"
[[ "$RETRY_WAIT_S" =~ ^[0-9]+$ ]] || die "MIGRATE_RETRY_WAIT_S must be a whole number of seconds, got '$RETRY_WAIT_S'"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "MIGRATE_CONTAINER must be a container name, got '$NAME'"
[[ -f compose.yml && -f .env ]] || die "no deployed tree here ($PWD): compose.yml and .env are needed"
command -v timeout >/dev/null || die "the timeout command (coreutils) is missing: nothing was run"
[[ "$LOG_DIR" == /* ]] || die "MIGRATE_LOG_DIR must be an absolute path, got '$LOG_DIR'"
case "$LOG_DIR/" in
  "$PWD"/*) die "MIGRATE_LOG_DIR must be outside the deploy directory ($PWD): nothing was run" ;;
esac
if ! mkdir -p "$LOG_DIR" || ! chmod 700 "$LOG_DIR"; then
  die "cannot make $LOG_DIR private to this user: nothing was run"
fi

STARTED=0
cleanup() {
  # Only the container this run started: after a timeout or a signal it may outlive its client.
  if ((STARTED == 1)); then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

state="$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || true)"
case "$state" in
  "") ;;
  running | restarting | paused | created)
    die "another migration is in progress (container $NAME is $state): nothing was run. Wait for it to end; if it is a leftover, remove it with: docker rm -f $NAME"
    ;;
  *)
    docker rm -f "$NAME" >/dev/null
    info "removed the $state container $NAME left by an earlier run"
    ;;
esac

if [[ -z "$(dc ps -q --status running db 2>/dev/null || true)" ]]; then
  info "db is not running: starting it (a first install creates the empty database here)"
  dc up -d --wait --wait-timeout "$DB_WAIT_S" db ||
    die "db did not become healthy within ${DB_WAIT_S}s: no migration was run, the api and the worker were not touched"
fi

# The newest LOG_KEEP outputs stay; the names sort by time.
find "$LOG_DIR" -maxdepth 1 -type f -name 'migrate-*.log' | sort -r | awk -v keep="$LOG_KEEP" 'NR > keep' |
  while IFS= read -r old; do rm -f -- "$old"; done
LOG="$LOG_DIR/migrate-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
(umask 077 && : >"$LOG") || die "cannot write $LOG: nothing was run"
info "alembic upgrade head in the one off container $NAME (new api image, at most ${TIMEOUT_S}s); its output is kept on this machine in ${LOG##*/}"
STARTED=1
start=$SECONDS
attempt=1
while :; do
  rc=0
  attempt_mark "$attempt" | figures "$LOG"
  # shellcheck disable=SC2086 # $COMPOSE is a command with its arguments
  timeout -k 20 "$TIMEOUT_S" $COMPOSE run --rm --no-deps -T --name "$NAME" api \
    sh -c 'alembic upgrade head && alembic current' </dev/null 2>&1 | figures "$LOG" || rc="${PIPESTATUS[0]}"
  locked=0
  if ((rc != 0 && rc != 124 && rc != 137)); then
    { read -r _ && read -r _ && read -r lock; } < <(failed_at "$LOG")
    [[ "$lock" != lock ]] || locked=1
  fi
  ((locked == 1 && attempt < ATTEMPTS)) || break
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  info "lock timeout (SQLSTATE 55P03) in attempt $attempt of $ATTEMPTS: the upgrade was rolled back whole; trying again in ${RETRY_WAIT_S}s"
  sleep "$RETRY_WAIT_S"
  attempt=$((attempt + 1))
done

elapsed=$((SECONDS - start))
if ((rc == 124 || rc == 137)); then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  # The client is gone, the session may not be: Postgres notices a vanished client only when its
  # statement ends. End the sessions of THIS database that hold or wait for the migration's
  # advisory lock (pg_locks is cluster wide, hence the database clause), so their locks go and
  # nothing can commit. docs/runbooks/continuous-delivery.md gives the same statement for a human.
  # shellcheck disable=SC2016 # $POSTGRES_* and $1 expand in the db container
  ended="$(dc exec -T db sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"' sh \
    "select count(*) from (select pg_terminate_backend(pid) from pg_locks where locktype = 'advisory' and objsubid = 1 and ((classid::bigint << 32) | objid::bigint) = $MIGRATION_LOCK_ID and database = (select oid from pg_database where datname = current_database()) and pid <> pg_backend_pid()) ended" \
    2>/dev/null | tr -d '\r' || true)"
  if [[ "$ended" =~ ^[0-9]+$ ]]; then
    info "migration sessions ended in Postgres: $ended"
  else
    printf 'migrate.sh: WARNING: could not check Postgres for a leftover migration session; it ends with its statement. To see it: select pid, state, query_start from pg_stat_activity where state <> %s\n' "'idle'" >&2
  fi
  # 137 is a SIGKILL. timeout sends one only 20 s after the limit, so a 137 before the limit did
  # not come from it: the kernel killed the container for memory (the mem_limit of the api
  # service applies to it), or someone killed it.
  if ((rc == 137 && elapsed < TIMEOUT_S)); then
    die "the migration container was killed after ${elapsed}s, before the ${TIMEOUT_S}s limit (exit 137): possibly out of memory (the mem_limit of the api service applies to it; docker events and dmesg on the host tell), or killed by hand. This is not a timeout. The open transaction is rolled back, the schema is what it was, and nothing was restarted"
  fi
  die "the migrations did not finish within ${TIMEOUT_S}s and were stopped: the open transaction is rolled back, the schema is what it was, and nothing was restarted. A migration that waits on a lock held by the running release is the usual cause; MIGRATE_TIMEOUT_S raises the limit"
fi
if ((rc != 0)); then
  { read -r step && read -r cls; } < <(failed_at "$LOG")
  info "failed in step: $step"
  info "error: $cls"
  if ((locked == 1)); then
    die "lock timeout: run the deploy again. A statement waited more than 5s for a lock the running release holds (SQLSTATE 55P03) in each of $attempt attempts; the upgrade runs in one transaction, so the schema is what it was, and nothing was restarted"
  fi
  die "alembic upgrade head failed (exit $rc; the whole output is on this machine in ${LOG##*/}): the upgrade runs in one transaction, so the schema is what it was, and nothing was restarted. Fix the migration and deploy again"
fi

revision="$(grep -E '^[A-Za-z0-9_]+ \(head\)$' "$LOG" | tail -n 1 || true)"
info "revision: ${revision:--}"
# The steps of the attempt that succeeded: an earlier one rolled back after a lock timeout.
{ read -r _ && read -r _ && read -r _ && read -r applied; } < <(failed_at "$LOG")
if ((applied > 0)); then
  info "migrations applied: $applied, in $((SECONDS - start))s; the schema is at head and the previous release still serves"
else
  info "already at head, nothing to apply ($((SECONDS - start))s)"
fi
