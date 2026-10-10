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
# Output: alembic's own log lines (INFO, the revisions applied) and the revision reached. No value
# of .env is ever printed: the settings hold the database URL as a secret and alembic does not log
# it; as a second guard, the credentials of anything shaped like a URL are masked on the way out.
#
# Settings from the environment: COMPOSE (default: docker compose -f compose.yml --profile tunnel),
# MIGRATE_TIMEOUT_S (default 300), MIGRATE_CONTAINER (default jobapp-migrate, one per project).
set -euo pipefail
export LC_ALL=C

COMPOSE="${COMPOSE:-docker compose -f compose.yml --profile tunnel}"
TIMEOUT_S="${MIGRATE_TIMEOUT_S:-300}"
NAME="${MIGRATE_CONTAINER:-jobapp-migrate}"
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

# mask: user:password@ of anything shaped like a URL becomes ***:***@. Line by line and flushed,
# so a long migration shows its progress as it goes.
mask() {
  awk '{ gsub("://[^:/@ \t]*:[^@ \t]*@", "://***:***@"); print; fflush() }'
}

[[ $# -eq 0 ]] || die "usage: ops/migrate.sh (no argument; settings come from the environment)"
[[ "$TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] || die "MIGRATE_TIMEOUT_S must be a whole number of seconds, got '$TIMEOUT_S'"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "MIGRATE_CONTAINER must be a container name, got '$NAME'"
[[ -f compose.yml && -f .env ]] || die "no deployed tree here ($PWD): compose.yml and .env are needed"
command -v timeout >/dev/null || die "the timeout command (coreutils) is missing: nothing was run"

OUT=""
STARTED=0
cleanup() {
  [[ -z "$OUT" ]] || rm -f "$OUT"
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

info "alembic upgrade head in the one off container $NAME (new api image, at most ${TIMEOUT_S}s)"
OUT="$(mktemp)"
STARTED=1
start=$SECONDS
rc=0
# shellcheck disable=SC2086 # $COMPOSE is a command with its arguments
timeout -k 20 "$TIMEOUT_S" $COMPOSE run --rm --no-deps -T --name "$NAME" api \
  sh -c 'alembic upgrade head && alembic current' </dev/null 2>&1 | mask | tee "$OUT" || rc="${PIPESTATUS[0]}"

elapsed=$((SECONDS - start))
if ((rc == 124 || rc == 137)); then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  # The client is gone, the session may not be: Postgres notices a vanished client only when its
  # statement ends. End the sessions of THIS database that hold or wait for the migration's
  # advisory lock (pg_locks is cluster wide, hence the database clause), so their locks go and
  # nothing can commit. README, Continuous delivery, gives the same statement for a human.
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
  die "alembic upgrade head failed (exit $rc, see above): the upgrade runs in one transaction, so the schema is what it was, and nothing was restarted. Fix the migration and deploy again"
fi

applied="$(grep -c 'Running upgrade ' "$OUT" || true)"
if ((applied > 0)); then
  info "migrations applied: $applied, in $((SECONDS - start))s; the schema is at head and the previous release still serves"
else
  info "already at head, nothing to apply ($((SECONDS - start))s)"
fi
