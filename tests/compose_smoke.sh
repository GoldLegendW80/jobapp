#!/usr/bin/env bash
# Smoke test of the Compose stack on the Mac (task 3 of the platform plan). Rerunnable.
#
#   tests/compose_smoke.sh            run every check, then stop the dev stack (volumes kept)
#   KEEP_UP=1 tests/compose_smoke.sh  leave the dev stack running at the end
#
# Needs Docker, python3, curl and a .env at the repository root (copy .env.example).
# The persistence check runs on a throwaway project name and only ever deletes that project's
# volumes; the dev stack's pgdata is never removed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEV=(docker compose -f compose.yml -f compose.dev.yml)
PROD=(docker compose -f compose.yml)
SCRATCH_PROJECT="jobapp-smoke-$$"
HEALTH_URL="http://127.0.0.1:8000/api/health"
TEST_DB="jobapp_test"

passed=0
step() { printf '\n== %s\n' "$*"; }
ok() { passed=$((passed + 1)); printf '   ok: %s\n' "$*"; }
fail() { printf '   FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
  docker compose -p "$SCRATCH_PROJECT" -f compose.yml down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

[[ -f .env ]] || fail ".env is missing: cp .env.example .env and fill it in"

envval() { # value of NAME in .env, without sourcing it
  python3 - "$1" <<'PY'
import sys
for line in open(".env", encoding="utf-8"):
    key, sep, value = line.strip().partition("=")
    if sep and key == sys.argv[1]:
        print(value)
PY
}

http_status() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || true; }

wait_status() { # wait_status <code> <seconds>
  local want="$1" deadline=$((SECONDS + $2)) got=""
  while ((SECONDS < deadline)); do
    got="$(http_status)"
    [[ "$got" == "$want" ]] && return 0
    sleep 0.5
  done
  fail "health never returned $want within $2s (last: ${got:-none})"
}

psql_dev() { "${DEV[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$1"; }

PG_USER="$(envval POSTGRES_USER)"
PG_DB="$(envval POSTGRES_DB)"
PG_PASSWORD="$(envval POSTGRES_PASSWORD)"

step "compose config validates, with and without the dev override"
"${PROD[@]}" config -q || fail "compose.yml does not validate"
ok "compose.yml"
"${DEV[@]}" config -q || fail "compose.yml + compose.dev.yml does not validate"
ok "compose.yml + compose.dev.yml"

step "production config: no host ports, mem_limit and restart on every service, pgdata volume"
"${PROD[@]}" config --format json | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
services = cfg["services"]
assert set(services) == {"api", "db", "redis"}, sorted(services)
for name, svc in services.items():
    ports, restart = svc.get("ports"), svc.get("restart")
    assert not ports, f"{name} publishes {ports}"
    assert svc.get("mem_limit"), f"{name} has no mem_limit"
    assert restart == "unless-stopped", f"{name} restart is {restart}"
    assert svc.get("healthcheck"), f"{name} has no healthcheck"
total = sum(int(s["mem_limit"]) for s in services.values())
assert total <= 5 * 1024**3, total
assert "pgdata" in cfg["volumes"], cfg["volumes"]
deps = services["api"]["depends_on"]
assert deps["db"]["condition"] == deps["redis"]["condition"] == "service_healthy", deps
print(f"   total mem_limit today: {total // 1024**2} MiB")
' || fail "production config"
ok "no ports, limits, restart policy, healthchecks, depends_on healthy"
"${DEV[@]}" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
ips = {p.get("host_ip") for s in services.values() for p in s.get("ports", [])}
assert ips == {"127.0.0.1"}, ips
assert {int(p["published"]) for s in services.values() for p in s.get("ports", [])} == {8000, 5432}
' || fail "dev ports"
ok "dev publishes 8000 and 5432 on 127.0.0.1 only"

step "dev stack up, every service healthy"
"${DEV[@]}" up -d --build --wait --wait-timeout 180 >/dev/null 2>&1 || fail "stack did not become healthy"
for svc in api db redis; do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$("${DEV[@]}" ps -q "$svc")")"
  [[ "$health" == "healthy" ]] || fail "$svc is $health"
done
ok "api, db, redis healthy"

step "GET /api/health"
body="$(curl -s --max-time 5 "$HEALTH_URL")"
[[ "$body" == '{"status":"ok"}' ]] || fail "unexpected body: $body"
ok "$body"

step "alembic: upgrade head twice, current, downgrade base, upgrade again"
"${DEV[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
"${DEV[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
current="$("${DEV[@]}" exec -T api alembic current 2>/dev/null)"
[[ "$current" == "0001 (head)" ]] || fail "alembic current: $current"
ok "upgrade twice, current is $current"
"${DEV[@]}" exec -T api alembic downgrade base >/dev/null 2>&1
current="$("${DEV[@]}" exec -T api alembic current 2>/dev/null)"
[[ -z "$current" ]] || fail "after downgrade base, current is $current"
"${DEV[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
current="$("${DEV[@]}" exec -T api alembic current 2>/dev/null)"
[[ "$current" == "0001 (head)" ]] || fail "alembic current: $current"
ok "downgrade base then upgrade head: $current"

step "pytest inside the api container against the compose db ($TEST_DB)"
if [[ "$(psql_dev "select 1 from pg_database where datname = '$TEST_DB'")" != "1" ]]; then
  psql_dev "create database $TEST_DB" >/dev/null
fi
encoded_pw="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$PG_PASSWORD")"
test_url="postgresql+psycopg://${PG_USER}:${encoded_pw}@db:5432/${TEST_DB}"
"${DEV[@]}" exec -T -e TEST_DATABASE_URL="$test_url" api pytest -q -m "not docker" -p no:cacheprovider \
  | tail -n 3
ok "pytest -m 'not docker' passed"

step "redis answers PING and persists with AOF"
[[ "$("${DEV[@]}" exec -T redis redis-cli ping | tr -d '\r')" == "PONG" ]] || fail "no PONG"
[[ "$("${DEV[@]}" exec -T redis redis-cli config get appendonly | tail -n 1 | tr -d '\r')" == "yes" ]] \
  || fail "appendonly is off"
ok "PONG, appendonly yes"

step "database down: health 503, then 200 again without restarting api"
api_started="$(docker inspect -f '{{.State.StartedAt}}' "$("${DEV[@]}" ps -q api)")"
"${DEV[@]}" stop db >/dev/null 2>&1
wait_status 503 20
ok "503 while db is stopped"
"${DEV[@]}" start db >/dev/null 2>&1
wait_status 200 60
ok "200 after db started again"
"${DEV[@]}" restart db >/dev/null 2>&1
wait_status 200 60
ok "200 after docker compose restart db"
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$("${DEV[@]}" ps -q api)")" == "$api_started" ]] \
  || fail "api was restarted"
ok "api container never restarted"

step "memory limits visible in docker stats"
stats="$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' $("${DEV[@]}" ps -q))"
printf '%s\n' "$stats" | sed 's/^/   /'
for pair in "api 512MiB" "db 768MiB" "redis 256MiB"; do
  set -- $pair
  grep -Eq "jobapp-$1-[0-9]+ .* / $2\$" <<<"$stats" || fail "$1 limit is not $2"
done
ok "api 512MiB, db 768MiB, redis 256MiB"

SCRATCH=(docker compose -p "$SCRATCH_PROJECT" -f compose.yml)
psql_scratch() { "${SCRATCH[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$1"; }

step "production stack alone on $SCRATCH_PROJECT: runtime image healthy, migrations, no ports"
"${SCRATCH[@]}" up -d --build --wait --wait-timeout 180 >/dev/null 2>&1 || fail "prod stack not healthy"
[[ -z "$(docker port "$("${SCRATCH[@]}" ps -q api)")$(docker port "$("${SCRATCH[@]}" ps -q db)")" ]] \
  || fail "prod stack publishes a host port"
"${SCRATCH[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
[[ "$("${SCRATCH[@]}" exec -T api alembic current 2>/dev/null)" == "0001 (head)" ]] \
  || fail "alembic in the runtime image"
"${SCRATCH[@]}" exec -T api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/api/health').read().decode())" \
  | grep -q '"status":"ok"' || fail "health inside the runtime image"
if "${SCRATCH[@]}" exec -T api python -c "import pytest" >/dev/null 2>&1; then
  fail "runtime image ships dev dependencies"
fi
ok "runtime image healthy, alembic at 0001, no host port, no dev dependencies"

step "volume persistence on throwaway project $SCRATCH_PROJECT"
psql_scratch "create table smoke (note text); insert into smoke values ('persisted ✓')" >/dev/null
"${SCRATCH[@]}" down >/dev/null 2>&1
"${SCRATCH[@]}" up -d --wait db >/dev/null 2>&1
[[ "$(psql_scratch "select note from smoke")" == "persisted ✓" ]] || fail "row lost after down + up"
ok "row survives down + up"
"${SCRATCH[@]}" down -v >/dev/null 2>&1
"${SCRATCH[@]}" up -d --wait db >/dev/null 2>&1
[[ "$(psql_scratch "select to_regclass('smoke') is null")" == "t" ]] || fail "data survived down -v"
ok "data gone after down -v"
"${SCRATCH[@]}" down -v >/dev/null 2>&1
[[ "$(psql_dev "select 1")" == "1" ]] || fail "dev database unreachable"
ok "dev stack untouched"

if [[ "${KEEP_UP:-0}" != "1" ]]; then
  step "stopping the dev stack (volumes kept)"
  "${DEV[@]}" down >/dev/null 2>&1
  ok "down"
fi

printf '\nAll %d checks passed.\n' "$passed"
