#!/usr/bin/env bash
# The deploy order of issue #81 with real Docker, Postgres and alembic, on the Mac (or any machine
# with Docker): migrate in a one off container of the NEW image while the OLD api serves, then
# start the new stack. Rerunnable; works on a scratch copy of the tree, its own Compose project
# and its own image tag, and removes all of it at the end. Not run in CI (it builds the api image
# four times); tests/deploy_test.sh covers the same paths with fakes there.
#
#   tests/migrate_test.sh
#
# 1. First install: no volume, no container. ops/migrate.sh starts db alone, applies every
#    migration of this tree to the empty database, then the api (the "old" release) starts.
# 2. What the one off container is: the api service's environment and network, no published
#    port, no restart policy, gone afterwards.
# 3. A release whose migration fails half way (a column added, then an error): exit 1, the column
#    is not there, the revision has not moved, the old api container is the same and answers.
# 4. A release whose migration hangs: stopped after MIGRATE_TIMEOUT_S, container removed, its
#    session ended in Postgres (no lock left on the table), revision unchanged, the old api answers.
#    A session of ANOTHER database of the cluster that holds the same advisory key is left alone.
# 5. The same migration, its container killed before the limit (what the kernel does when the
#    container runs out of memory): exit 137 is reported as killed, not as a timeout, and its
#    session is ended in Postgres too.
# 6. The recovery statement of docs/runbooks/continuous-delivery.md, run as written: it ends a leftover
#    session on the migration's lock in this database, not the one of another database.
# 7. A release that adds a nullable column (expand): applied by the new image's one off container
#    while the old api container, untouched, keeps answering and keeps reading the altered table.
# 8. At head: a no-op.
# 9. compose up: the api is recreated from the new image and answers on the migrated schema.
#
# Where it runs: by hand, on the Mac, when ops/migrate.sh, api/alembic/env.py or the api's
# Dockerfile change, and with no other scratch stack up (`docker ps`): the Mac is shared with the
# CI runners. Never a CI step. It leaves nothing: containers, volumes, network and image removed.
# Needs Docker, rsync, openssl and the timeout command (coreutils).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="mig81-$$"
PROJECT="jobapp-$TAG"
WORK="$(mktemp -d)"
TREE="$WORK/tree"

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

FILES=(-f compose.yml -f migrate-test.yml)
# Apple silicon: compose.mac.yml, or the api dies with SIGILL at import (docs/runbooks/development-on-the-mac.md, Apple silicon).
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then FILES+=(-f compose.mac.yml); fi
dc() { (cd "$TREE" && docker compose -p "$PROJECT" "${FILES[@]}" "$@"); }

cleanup() {
  if [[ -f "$TREE/compose.yml" ]]; then dc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker rm -f "$PROJECT-migrate" "$PROJECT-probe" >/dev/null 2>&1 || true
  docker image rm -f "jobapp-$TAG-api:latest" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

command -v timeout >/dev/null || fail "the timeout command is missing (brew install coreutils)"

# The scratch tree: what a deploy syncs, a generated .env, and the api image under its own tag so
# no jobapp-api:latest of another stack on this machine is replaced.
mkdir -p "$TREE"
rsync -a --exclude-from=.rsyncignore ./ "$TREE/"
.github/scripts/ci_env.sh 8080 >"$TREE/.env"
printf 'services:\n  api:\n    image: jobapp-%s-api:latest\n' "$TAG" >"$TREE/migrate-test.yml"
VERSIONS="$TREE/api/alembic/versions"
HEAD_REV="$(find "$VERSIONS" -name '[0-9]*.py' -exec basename {} \; | sort | tail -n 1 | cut -d_ -f1)"
[[ "$HEAD_REV" =~ ^[0-9]{4}$ ]] || fail "could not read the head revision of $VERSIONS"

# migrate <expected exit> [env assignments]: ops/migrate.sh as a deploy runs it, output in $WORK/out
migrate() {
  local want="$1" got=0
  shift
  (cd "$TREE" && env "$@" COMPOSE="docker compose -p $PROJECT ${FILES[*]}" \
    MIGRATE_CONTAINER="$PROJECT-migrate" MIGRATE_LOG_DIR="$WORK/logs" bash ops/migrate.sh) >"$WORK/out" 2>&1 || got=$?
  sed 's/^/   | /' "$WORK/out"
  [[ "$got" == "$want" ]] || fail "ops/migrate.sh exited $got, expected $want"
}
out_has() { grep -q -- "$1" "$WORK/out" || fail "output lacks '$1'"; }
# shellcheck disable=SC2016 # $POSTGRES_* and $1 expand in the db container
sql() { dc exec -T db sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"' sh "$1" | tr -d '\r'; }
revision() { sql "select version_num from alembic_version"; }
# sessions <text>: the sessions of the whole cluster whose query holds <text>, this one apart
sessions() { sql "select count(*) from pg_stat_activity where query like '%$1%' and pid <> pg_backend_pid()"; }
# gone <text>: wait up to 20 s for no session to hold <text>
gone() {
  local left=1
  for _ in $(seq 1 20); do
    left="$(sessions "$1")"
    [[ "$left" == 0 ]] && return 0
    sleep 1
  done
  return 1
}
# The advisory lock every `alembic upgrade` takes (api/alembic/env.py), as ops/migrate.sh holds it.
LOCK_ID="$(sed -n 's/^MIGRATION_LOCK_ID=//p' ops/migrate.sh)"
[[ "$LOCK_ID" =~ ^[0-9]+$ ]] || fail "could not read MIGRATION_LOCK_ID of ops/migrate.sh"
# hold_lock <database> <seconds>: a background session of <database> that takes the migration's
# advisory lock and then sleeps; pg_sleep(<seconds>) is how the checks recognise it.
hold_lock() {
  # shellcheck disable=SC2016 # $POSTGRES_USER and $1, $2, $3 expand in the db container
  dc exec -T db sh -c 'psql -X -q -U "$POSTGRES_USER" -d "$1" -c "select pg_advisory_lock($2)" -c "select pg_sleep($3)"' \
    sh "$1" "$LOCK_ID" "$2" </dev/null >/dev/null 2>&1 &
  for _ in $(seq 1 40); do
    [[ "$(sessions "pg_sleep($2)")" == 1 ]] && return 0
    sleep 0.5
  done
  fail "the session holding the lock in database $1 never appeared"
}
# end_session <text>: terminate the sessions whose query holds <text> (the holders above)
end_session() {
  sql "select pg_terminate_backend(pid) from pg_stat_activity where query like '%$1%' and pid <> pg_backend_pid()" >/dev/null
}
has_column() { sql "select count(*) from information_schema.columns where table_name = 'users' and column_name = '$1'"; }
api_id() { dc ps -q api; }
api_image() { docker inspect -f '{{.Image}}' "$(api_id)"; }
# The old api answering: its health (a database round trip), and a sign in attempt for an unknown
# account, which reads the users table the migration alters and must answer 401, not 500. Each
# check uses an address of its own: the api throttles a sixth failed attempt for one email (429).
api_answers() { # api_answers <email>
  dc exec -T api python -c '
import json, sys, urllib.error, urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4).read().decode())
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/auth/login",
    data=json.dumps({"email": sys.argv[1], "password": "not-a-password"}).encode(),
    headers={"Content-Type": "application/json", "X-Requested-With": "jobapp"},
)
try:
    print("login", urllib.request.urlopen(request, timeout=8).status)
except urllib.error.HTTPError as error:
    print("login", error.code)
' "$1" | tr -d '\r'
}
CHECKS=0
check_api() { # check_api <label>: both answers as expected
  local answers
  CHECKS=$((CHECKS + 1))
  answers="$(api_answers "nobody-$CHECKS@example.org")"
  [[ "$answers" == $'{"status":"ok"}\nlogin 401' ]] || fail "$1: the api answered: $answers"
}
# write_revision <rev> <down> <upgrade body>: a revision file in the scratch tree
write_revision() {
  cat >"$VERSIONS/$1_migrate_test.py" <<PY
"""Scratch revision of tests/migrate_test.sh."""

import sqlalchemy as sa
from alembic import op

revision: str = "$1"
down_revision: str | None = "$2"
branch_labels = None
depends_on = None


def upgrade() -> None:
$3


def downgrade() -> None:
    pass
PY
}
build() { dc --progress=quiet build api || fail "build of the api image failed"; }

step "1. first install: empty database, every migration applied before the api starts"
build
migrate 0
out_has "db is not running: starting it"
out_has "migrations applied: "
out_has "$HEAD_REV (head)"
[[ "$(revision)" == "$HEAD_REV" ]] || fail "the database is at '$(revision)', not $HEAD_REV"
[[ -z "$(api_id)" ]] || fail "the api was started by the migration step"
ok "ops/migrate.sh started db alone and migrated the empty database to $HEAD_REV; no api container yet"
dc up -d --wait --wait-timeout 180 api >/dev/null 2>&1 || fail "the old api did not become healthy"
OLD_ID="$(api_id)"
OLD_IMAGE="$(api_image)"
check_api "old release"
ok "the old release is up: container ${OLD_ID:0:12}, image ${OLD_IMAGE:7:12}, health ok, sign in answers 401"

step "2. the one off container: the api's environment and network, no port, no restart policy"
(cd "$TREE" && docker compose -p "$PROJECT" "${FILES[@]}" run --rm --no-deps -T --name "$PROJECT-probe" api sleep 30 >/dev/null 2>&1) &
probe=$!
for _ in $(seq 1 40); do
  [[ "$(docker inspect -f '{{.State.Status}}' "$PROJECT-probe" 2>/dev/null || true)" == "running" ]] && break
  sleep 0.5
done
facts="$(docker inspect -f 'ports={{len .NetworkSettings.Ports}} published={{len .HostConfig.PortBindings}} restart={{.HostConfig.RestartPolicy.Name}} autoremove={{.HostConfig.AutoRemove}} networks={{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$PROJECT-probe")"
has_url="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PROJECT-probe" | grep -c '^DATABASE_URL=' || true)"
api_networks="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$OLD_ID")"
docker rm -f "$PROJECT-probe" >/dev/null 2>&1 || true
wait "$probe" 2>/dev/null || true
info_line="$facts database_url_set=$has_url"
printf '   | %s\n' "$info_line"
[[ "$facts" == *"published=0 "* ]] || fail "the one off container publishes a port: $facts"
[[ "$facts" == *"restart=no "* || "$facts" == *"restart= "* ]] || fail "the one off container has a restart policy: $facts"
[[ "$facts" == *"autoremove=true "* ]] || fail "the one off container is not removed when it ends: $facts"
[[ "$facts" == *"networks=$api_networks" ]] || fail "the one off container is not on the api's network ($api_networks): $facts"
[[ "$has_url" == 1 ]] || fail "the one off container does not get the api's env_file"
ok "compose run --rm --no-deps of the api service: same network and env_file as the api, nothing published, no restart, auto removed"

step "3. a migration that fails half way: nothing changes, the old api keeps serving"
write_revision 9999 "$HEAD_REV" '    op.add_column("users", sa.Column("migrate_test_half", sa.Text(), nullable=True))
    op.execute("select 1 / 0")'
build
migrate 1
out_has "alembic upgrade head failed (exit 1; the whole output is on this machine in migrate-"
out_has "failed in step: $HEAD_REV -> 9999"
grep -q "division by zero" "$WORK/out" && fail "Postgres' error reached the output"
grep -q "division by zero" "$WORK"/logs/migrate-*.log || fail "Postgres' error is not in the kept output"
out_has "nothing was restarted"
[[ "$(revision)" == "$HEAD_REV" ]] || fail "the revision moved to '$(revision)' after a failed migration"
[[ "$(has_column migrate_test_half)" == 0 ]] || fail "the column of the failed migration is there: the transaction did not roll back"
[[ "$(api_id)" == "$OLD_ID" && "$(api_image)" == "$OLD_IMAGE" ]] || fail "the api container changed"
check_api "after a failed migration"
[[ -z "$(docker ps -aq --filter "name=^$PROJECT-migrate$")" ]] || fail "the one off container was left behind"
grep -q "$(sed -n 's/^POSTGRES_PASSWORD=//p' "$TREE/.env")" "$WORK/out" && fail "the database password is in the output"
ok "exit 1, the half applied column rolled back, revision still $HEAD_REV, same api container answering, no container left, no password in the log"

step "4. a migration that hangs: stopped after MIGRATE_TIMEOUT_S"
write_revision 9999 "$HEAD_REV" '    op.add_column("users", sa.Column("migrate_test_slow", sa.Text(), nullable=True))
    op.execute("select pg_sleep(120)")'
build
# The same advisory key held in another database of the cluster: pg_locks shows it with the same
# classid, objid and objsubid, and it must survive (advisory locks are per database, so it does
# not block the migration either).
hold_lock postgres 591
started=$SECONDS
migrate 1 MIGRATE_TIMEOUT_S=8
took=$((SECONDS - started))
out_has "did not finish within 8s and were stopped"
out_has "migration sessions ended in Postgres: 1"
((took < 60)) || fail "the timeout took ${took}s"
[[ -z "$(docker ps -aq --filter "name=^$PROJECT-migrate$")" ]] || fail "the one off container outlived the timeout"
gone "pg_sleep(120)" || fail "the migration is still running in Postgres after the timeout"
[[ "$(sessions "pg_sleep(591)")" == 1 ]] || fail "the session of another database holding the same key was ended"
[[ "$(revision)" == "$HEAD_REV" && "$(has_column migrate_test_slow)" == 0 ]] || fail "the timed out migration left something behind"
[[ "$(api_id)" == "$OLD_ID" ]] || fail "the api container changed"
check_api "after a timed out migration"
ok "stopped after ${took}s, container gone, no query left in Postgres, the same key held in database postgres left alone, schema and revision unchanged, the old api answers"

step "5. a migration container killed before the limit: said as killed, not as a timeout"
# What an out of memory kill looks like from outside: SIGKILL of the container while the
# migration runs, long before MIGRATE_TIMEOUT_S.
(
  for _ in $(seq 1 80); do
    [[ "$(sessions "pg_sleep(120)")" == 1 ]] && break
    sleep 0.5
  done
  docker kill --signal KILL "$PROJECT-migrate" >/dev/null
) &
killer=$!
started=$SECONDS
migrate 1 MIGRATE_TIMEOUT_S=120
took=$((SECONDS - started))
wait "$killer" || fail "the migration container was never there to kill"
out_has "the migration container was killed after [0-9]*s, before the 120s limit (exit 137): possibly out of memory"
out_has "This is not a timeout"
out_has "migration sessions ended in Postgres: 1"
grep -q "did not finish within" "$WORK/out" && fail "a kill before the limit was reported as a timeout"
((took < 60)) || fail "the kill took ${took}s to be seen"
[[ -z "$(docker ps -aq --filter "name=^$PROJECT-migrate$")" ]] || fail "the killed container was left behind"
gone "pg_sleep(120)" || fail "the killed migration is still running in Postgres"
[[ "$(sessions "pg_sleep(591)")" == 1 ]] || fail "the session of another database holding the same key was ended"
[[ "$(revision)" == "$HEAD_REV" && "$(has_column migrate_test_slow)" == 0 ]] || fail "the killed migration left something behind"
[[ "$(api_id)" == "$OLD_ID" ]] || fail "the api container changed"
check_api "after a killed migration"
ok "exit 137 after ${took}s of a 120s limit: reported as killed, possibly out of memory, its session ended in Postgres, schema and revision unchanged, the old api answers"

step "6. the recovery statement of the runbook, run as written against this stack"
# The command and the SQL are cut out of docs/runbooks/continuous-delivery.md. `bash -c` stands for the shell ssh runs the
# command in on the Jetson; only the directory and the Compose project are replaced.
readme_cmd="$(sed -n "s/^ssh jetson '\(cd ~\/jobapp && docker compose .* exec -T db sh -c .*\)' <<'SQL'\$/\1/p" docs/runbooks/continuous-delivery.md)"
readme_sql="$(sed -n "/^ssh jetson .* <<'SQL'\$/,/^SQL\$/p" docs/runbooks/continuous-delivery.md | sed '1d;$d')"
[[ -n "$readme_cmd" && "$readme_sql" == *"pg_terminate_backend"* ]] || fail "the recovery statement was not found in docs/runbooks/continuous-delivery.md"
on_jetson='cd ~/jobapp && docker compose -f compose.yml --profile tunnel'
here="cd $(printf '%q' "$TREE") && docker compose -p $PROJECT ${FILES[*]}"
[[ "$readme_cmd" == "$on_jetson "* ]] || fail "the runbook command does not start as expected: $readme_cmd"
local_cmd="$here${readme_cmd#"$on_jetson"}"
PG_DB="$(sed -n 's/^POSTGRES_DB=//p' "$TREE/.env")"
# A leftover: a session of this database holding the migration's lock, as a migration whose
# container was removed by hand leaves one.
hold_lock "$PG_DB" 592
answer="$(bash -c "$local_cmd" <<<"$readme_sql" 2>&1 | tr -d '\r' || true)"
while IFS= read -r line; do printf '   | %s\n' "$line"; done <<<"$answer"
[[ "$(grep -c ' | t$' <<<"$answer")" == 1 && "$answer" == *"(1 row)"* ]] || fail "the runbook statement did not end exactly one session"
gone "pg_sleep(592)" || fail "the leftover session is still there after the runbook statement"
[[ "$(sessions "pg_sleep(591)")" == 1 ]] || fail "the runbook statement ended the session of another database"
answer="$(bash -c "$local_cmd" <<<"$readme_sql" 2>&1 | tr -d '\r' || true)"
[[ "$answer" == *"(0 rows)"* ]] || fail "a second run of the runbook statement found something: $answer"
end_session "pg_sleep(591)"
gone "pg_sleep(591)" || fail "the test's own session in database postgres could not be ended"
check_api "after the runbook statement"
ok "the statement of the runbook ends the leftover session of this database (1 row, then 0 rows), leaves the same key in database postgres alone, and the old api answers"

step "7. an expanding migration: applied by the new image while the old api serves"
write_revision 9999 "$HEAD_REV" '    op.add_column("users", sa.Column("migrate_test_note", sa.Text(), nullable=True))'
build
NEW_IMAGE="$(docker image inspect -f '{{.Id}}' "jobapp-$TAG-api:latest")"
[[ "$NEW_IMAGE" != "$OLD_IMAGE" ]] || fail "the new image is the old one"
migrate 0
out_has "step: $HEAD_REV -> 9999"
out_has "migrations applied: 1"
out_has "9999 (head)"
[[ "$(revision)" == "9999" && "$(has_column migrate_test_note)" == 1 ]] || fail "the migration did not apply"
[[ "$(api_id)" == "$OLD_ID" && "$(api_image)" == "$OLD_IMAGE" ]] || fail "the old api container was touched"
[[ "$(docker inspect -f '{{.State.Status}} {{.State.Health.Status}} {{.RestartCount}}' "$OLD_ID")" == "running healthy 0" ]] ||
  fail "the old api is not running healthy without a restart"
check_api "old release on the new schema"
ok "revision 9999 and the new column are in; the old container ${OLD_ID:0:12} (old image) was not restarted and answers on the new schema"

step "8. at head: a no-op"
started=$SECONDS
migrate 0
out_has "already at head, nothing to apply"
[[ "$(revision)" == "9999" ]] || fail "the no-op moved the revision"
ok "nothing to apply, $((SECONDS - started))s"

step "9. compose up: the new release starts on the migrated schema"
dc up -d --wait --wait-timeout 180 api >/dev/null 2>&1 || fail "the new api did not become healthy"
[[ "$(api_id)" != "$OLD_ID" ]] || fail "the api container was not recreated"
[[ "$(api_image)" == "$NEW_IMAGE" ]] || fail "the api does not run the new image"
check_api "new release"
current="$(dc exec -T api alembic current 2>/dev/null | tr -d '\r' | tail -n 1)"
[[ "$current" == "9999 (head)" ]] || fail "alembic current in the new api says '$current'"
ok "the api was recreated from the new image, answers, and reports 9999 (head)"

printf '\nAll %d checks passed.\n' "$passed"
