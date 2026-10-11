#!/usr/bin/env bash
# Smoke test of the Compose stack on the Mac (tasks 3 and 6 of the platform plan). Rerunnable.
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
ECHO_NAME="jobapp-smoke-echo-$$"

passed=0
step() { printf '\n== %s\n' "$*"; }
ok() { passed=$((passed + 1)); printf '   ok: %s\n' "$*"; }
fail() { printf '   FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
  docker compose -p "$SCRATCH_PROJECT" -f compose.yml down -v --remove-orphans >/dev/null 2>&1 || true
  docker rm -f -v "$ECHO_NAME" >/dev/null 2>&1 || true
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
# Shell environment first, then .env, then the compose default, as compose itself resolves it.
HTTP_PORT="${HTTP_PORT:-$(envval HTTP_PORT)}"
HTTP_PORT="${HTTP_PORT:-80}"
CADDY="http://127.0.0.1:${HTTP_PORT}"

step "compose config validates, with and without the dev override"
"${PROD[@]}" config -q || fail "compose.yml does not validate"
ok "compose.yml"
"${DEV[@]}" config -q || fail "compose.yml + compose.dev.yml does not validate"
ok "compose.yml + compose.dev.yml"

step "production config: only caddy publishes a port, mem_limit and restart everywhere, pgdata"
"${PROD[@]}" --profile tunnel config --format json | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
services = cfg["services"]
assert set(services) == {"api", "caddy", "db", "redis", "tailscale", "worker"}, sorted(services)
for name, svc in services.items():
    ports, restart = svc.get("ports"), svc.get("restart")
    if name == "caddy":
        assert [(p["target"], p["host_ip"]) for p in ports] == [(80, "127.0.0.1")], ports
    else:
        assert not ports, f"{name} publishes {ports}"
    assert svc.get("mem_limit"), f"{name} has no mem_limit"
    assert restart == "unless-stopped", f"{name} restart is {restart}"
    assert svc.get("healthcheck"), f"{name} has no healthcheck"
total = sum(int(s["mem_limit"]) for s in services.values())
assert total <= 5 * 1024**3, total
assert "pgdata" in cfg["volumes"], cfg["volumes"]
deps = services["api"]["depends_on"]
assert deps["db"]["condition"] == deps["redis"]["condition"] == "service_healthy", deps
assert int(services["caddy"]["mem_limit"]) == 128 * 1024**2, services["caddy"]["mem_limit"]
assert services["caddy"]["build"]["target"] == "caddy", services["caddy"]["build"]
worker = services["worker"]
assert worker["image"] == "jobapp-worker:latest", worker["image"]
assert worker["build"]["target"] == "worker", worker["build"]
assert services["api"]["build"]["target"] == "runtime", services["api"]["build"]
assert worker["command"] == ["python", "-m", "app.worker"], worker["command"]
assert worker["tmpfs"] == ["/tmp:size=128m,mode=1777"], worker.get("tmpfs")
assert int(worker["mem_limit"]) == 1536 * 1024**2, worker["mem_limit"]
assert worker["depends_on"]["db"]["condition"] == worker["depends_on"]["redis"]["condition"] == "service_healthy"
# tailscale: the full checks of the service are in tests/tailscale_test.sh.
ts = services["tailscale"]
import re
assert re.fullmatch(r"tailscale/tailscale:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", ts["image"]), ts["image"]
assert ts["profiles"] == ["tunnel"], ts.get("profiles")
assert ts["depends_on"]["caddy"]["condition"] == "service_healthy", ts["depends_on"]
assert int(ts["mem_limit"]) == 128 * 1024**2, ts["mem_limit"]
assert ts["environment"]["TS_USERSPACE"] == "true" and not ts.get("privileged"), ts["environment"]
print(f"   total mem_limit today: {total // 1024**2} MiB")
' || fail "production config"
ok "caddy alone publishes, on 127.0.0.1; limits, restart policy, healthchecks, depends_on healthy; tailscale pinned, userspace, in the tunnel profile"
for files in "compose.yml" "compose.yml -f compose.dev.yml"; do
  # shellcheck disable=SC2086 # two words on purpose
  ! docker compose -f $files config --services | grep -qx tailscale || fail "$files starts tailscale without --profile tunnel"
done
ok "no tunnel without --profile tunnel: neither the dev stack nor a plain compose.yml opens one on the Mac"
"${DEV[@]}" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
ips = {p.get("host_ip") for s in services.values() for p in s.get("ports", [])}
assert ips == {"127.0.0.1"}, ips
published = {int(p["published"]) for s in services.values() for p in s.get("ports", [])}
assert published == {8000, 5432, int(sys.argv[1])}, published
' "$HTTP_PORT" || fail "dev ports"
ok "dev publishes 8000, 5432 and $HTTP_PORT on 127.0.0.1 only"

step "dev stack up, every service healthy"
"${DEV[@]}" up -d --build --wait --wait-timeout 180 >/dev/null 2>&1 || fail "stack did not become healthy"
for svc in api caddy db redis worker; do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$("${DEV[@]}" ps -q "$svc")")"
  [[ "$health" == "healthy" ]] || fail "$svc is $health"
done
ok "api, caddy, db, redis, worker healthy"

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

step "caddy: static build at $CADDY"
# curl_c <path> [curl args...]: GET through caddy, prints "<status>" and keeps headers and body.
CURL_DIR="$(mktemp -d)"
trap 'cleanup; rm -rf "$CURL_DIR"' EXIT
curl_c() {
  local path="$1"
  shift
  curl -s --path-as-is --max-time 10 -D "$CURL_DIR/h" -o "$CURL_DIR/b" -w '%{http_code}' "$@" "$CADDY$path" || true
}
hdr() { grep -i "^$1:" "$CURL_DIR/h" | head -n 1 | cut -d' ' -f2- | tr -d '\r'; }
body_has_root() { grep -q '<div id="root"></div>' "$CURL_DIR/b"; }

[[ "$(curl_c /)" == "200" ]] && body_has_root || fail "/ does not serve index.html"
[[ "$(hdr Cache-Control)" == "no-cache" ]] || fail "index.html Cache-Control: $(hdr Cache-Control)"
[[ "$(hdr Content-Type)" == "text/html; charset=utf-8" ]] || fail "/ Content-Type: $(hdr Content-Type)"
ok "/ is index.html, Cache-Control no-cache"
for route in /some/deep/route "/some/route?x=1" /jobs/42/ /caf%C3%A9; do
  [[ "$(curl_c "$route")" == "200" ]] && body_has_root || fail "$route did not fall back to index.html"
done
ok "client routes fall back to index.html (deep, query string, trailing slash, unicode)"
for h in "X-Content-Type-Options: nosniff" "Referrer-Policy: strict-origin-when-cross-origin" \
  "X-Frame-Options: DENY"; do
  [[ "$(hdr "${h%%:*}")" == "${h#*: }" ]] || fail "missing header $h"
done
grep -qi "^Content-Security-Policy: frame-ancestors 'none'" "$CURL_DIR/h" || fail "no frame-ancestors CSP"
grep -qi '^Server:' "$CURL_DIR/h" && fail "Server header leaks"
ok "security headers present, no Server header"

curl_c / >/dev/null
asset="$(grep -o '/assets/[^"]*\.js' "$CURL_DIR/b" | head -n 1)"
[[ -n "$asset" ]] || fail "no hashed script in index.html"
[[ "$(curl_c "$asset")" == "200" ]] || fail "$asset not served"
[[ "$(hdr Cache-Control)" == "public, max-age=31536000, immutable" ]] || fail "$asset Cache-Control: $(hdr Cache-Control)"
[[ "$(hdr Content-Type)" == "text/javascript; charset=utf-8" ]] || fail "$asset Content-Type: $(hdr Content-Type)"
ok "$asset immutable for a year, text/javascript"
[[ "$(curl_c /assets/nope.js)" == "404" ]] || fail "missing asset is not 404"
body_has_root && fail "missing asset served index.html"
[[ "$(hdr Cache-Control)" == "no-store" ]] || fail "404 is cacheable: $(hdr Cache-Control)"
[[ "$(hdr X-Content-Type-Options)" == "nosniff" ]] || fail "404 lacks security headers"
ok "/assets/nope.js is a 404, no-store, not index.html"
for enc in zstd gzip; do
  [[ "$(curl_c "$asset" -H "Accept-Encoding: $enc")" == "200" ]] || fail "$enc request failed"
  [[ "$(hdr Content-Encoding)" == "$enc" ]] || fail "no $enc encoding: $(hdr Content-Encoding)"
done
curl -s --compressed --max-time 10 "$CADDY$asset" | cmp -s - <(curl -s --max-time 10 -H 'Accept-Encoding: identity' "$CADDY$asset") \
  || fail "compressed asset differs from the original"
ok "zstd and gzip encodings, decompressed bytes identical"
# curl -I writes the headers where the body would go: no HTML may follow them.
[[ "$(curl_c / -I)" == "200" ]] && ! grep -qi '<html' "$CURL_DIR/b" || fail "HEAD / not 200 with headers only"
[[ "$(hdr Content-Length)" =~ ^[1-9][0-9]*$ ]] || fail "HEAD / has no Content-Length"
ok "HEAD / answers 200 with headers only"
for attack in /../etc/passwd /%2e%2e/%2e%2e/etc/passwd /assets/../../../etc/passwd \
  /assets/%2e%2e/%2e%2e/etc/passwd /assets/..%2f..%2f..%2fetc%2fpasswd "/..%5c..%5cetc%5cpasswd" \
  /api/../../etc/passwd /%252e%252e/etc/passwd; do
  code="$(curl_c "$attack")"
  grep -q 'root:' "$CURL_DIR/b" && fail "$attack leaked /etc/passwd"
  [[ "$code" == "200" || "$code" == "400" || "$code" == "404" ]] || fail "$attack answered $code"
done
ok "path traversal attempts never leave /srv"

step "caddy: /api proxied to the api"
[[ "$(curl_c /api/health)" == "200" ]] || fail "/api/health through caddy"
[[ "$(cat "$CURL_DIR/b")" == '{"status":"ok"}' ]] || fail "health body: $(cat "$CURL_DIR/b")"
[[ "$(hdr X-Content-Type-Options)" == "nosniff" ]] || fail "api answer lacks security headers"
ok "/api/health: $(cat "$CURL_DIR/b")"
[[ "$(curl_c /api/nope)" == "404" ]] || fail "/api/nope is not 404"
[[ "$(cat "$CURL_DIR/b")" == '{"detail":"Not Found"}' ]] || fail "/api/nope body: $(head -c 80 "$CURL_DIR/b")"
ok "/api/nope is the api's JSON 404, not the SPA"
[[ "$(curl_c /api/health -H 'X-Request-ID: smoke-req-1')" == "200" ]] || fail "health with request id"
[[ "$(hdr X-Request-ID)" == "smoke-req-1" ]] || fail "X-Request-ID not passed through: $(hdr X-Request-ID)"
curl_c /api/health >/dev/null
[[ "$(hdr X-Request-ID)" =~ ^[0-9a-f-]{36}$ ]] || fail "caddy did not generate a request id: $(hdr X-Request-ID)"
ok "X-Request-ID kept when sent, a UUID from caddy otherwise"
big() { head -c "$1" /dev/zero; }
[[ "$(curl_c /api/health -X POST --data-binary @<(big 10485761))" == "413" ]] || fail "10 MiB + 1 not refused"
[[ "$(curl_c / -X POST --data-binary @<(big 20000000))" == "413" ]] || fail "20 MB to / not refused"
[[ "$(curl_c /api/health -X POST --data-binary @<(big 10485760))" == "405" ]] || fail "10 MiB exactly did not reach the api"
ok "bodies over 10 MiB refused with 413, 10 MiB exactly reaches the api (405 from FastAPI)"
# The upload of a document is the one route that takes 20 MiB (SPEC-ingestion.md, task G1).
# No session here: a body that Caddy lets through reaches the api, which answers 401; a body
# over the limit is Caddy's own 413.
up=/api/ingestion/documents
csrf=(-H 'X-Requested-With: jobapp')
[[ "$(curl_c "$up" -X POST "${csrf[@]}" --data-binary @<(big 20971520))" == "401" ]] || fail "20 MiB did not reach the api on $up"
[[ "$(curl_c "$up" -X POST "${csrf[@]}" --data-binary @<(big 20971521))" == "413" ]] || fail "20 MiB + 1 not refused on $up"
[[ "$(curl_c "$up" -X POST "${csrf[@]}" --data-binary @<(big 10485761))" == "401" ]] || fail "10 MiB + 1 did not reach the api on $up"
for other in "$up/text" "$up/" "/API/INGESTION/DOCUMENTS" /api/ingestion//documents /api/ingestion/./documents \
  "$up/00000000-0000-4000-8000-000000000000/confirm" /api/ingestion/other; do
  [[ "$(curl_c "$other" -X POST "${csrf[@]}" --data-binary @<(big 10485761))" == "413" ]] || fail "10 MiB + 1 not refused on POST $other"
done
for method in PUT PATCH DELETE; do
  [[ "$(curl_c "$up" -X "$method" "${csrf[@]}" --data-binary @<(big 10485761))" == "413" ]] || fail "10 MiB + 1 not refused on $method $up"
done
ok "20 MiB on POST $up only: its other spellings, its other methods and every other route keep 10 MiB"
curl_c "$up" -X POST "${csrf[@]}" -H "X-Filename: UTF-8''smoke-private-name.txt" --data-binary 'x' >/dev/null
sleep 1
"${DEV[@]}" logs --no-color caddy 2>/dev/null | grep -qi 'smoke-private-name\|x-filename' && fail "caddy logs the name of an uploaded document"
ok "the name of an uploaded document (X-Filename) is in no caddy log line"

step "caddy: headers and streaming limit, against an echo stand-in for the api"
"${DEV[@]}" stop api >/dev/null 2>&1
network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$("${DEV[@]}" ps -q caddy)")"
docker run -d --rm --name "$ECHO_NAME" --network "$network" --network-alias api \
  -v "$ROOT/tests/echo_upstream.py:/echo.py:ro" jobapp-api:dev python /echo.py >/dev/null
deadline=$((SECONDS + 20))
until [[ "$(curl_c /api/echo)" == "200" ]]; do
  ((SECONDS < deadline)) || fail "echo stand-in never answered"
  sleep 0.3
done
curl_c "/api/echo?q=1" -H 'X-Request-ID: smoke-req-2' -H 'X-Forwarded-For: 203.0.113.9' >/dev/null
python3 - "$CURL_DIR/b" <<'PY' || fail "headers seen by the api"
import ipaddress, json, sys
seen = json.load(open(sys.argv[1]))
h = {k.lower(): v for k, v in seen["headers"].items()}
assert seen["path"] == "/api/echo?q=1", seen["path"]
assert h["x-request-id"] == "smoke-req-2", h
assert ipaddress.ip_address(h["x-forwarded-for"]).is_private, h  # spoofed value replaced
assert h["x-forwarded-proto"] == "http" and h["x-forwarded-host"], h
PY
ok "api sees /api prefix, X-Request-ID, X-Forwarded-For (spoof replaced), -Proto, -Host"
[[ "$(curl_c /api/echo -X POST -H 'Transfer-Encoding: chunked' --data-binary @<(big 12000000))" == "413" ]] \
  || fail "chunked body over the limit not cut"
[[ "$(curl_c /api/echo -X POST -H 'Transfer-Encoding: chunked' --data-binary @<(big 5000000))" == "200" ]] \
  && grep -q '"body_bytes": 5000000' "$CURL_DIR/b" || fail "chunked 5 MB body not delivered whole"
ok "chunked bodies: 5 MB delivered whole, 12 MB cut with 413"
[[ "$(curl_c /api/ingestion/documents -X POST -H 'Transfer-Encoding: chunked' --data-binary @<(big 12000000))" == "200" ]] \
  && grep -q '"body_bytes": 12000000' "$CURL_DIR/b" || fail "chunked 12 MB body not delivered whole on the upload route"
[[ "$(curl_c /api/ingestion/documents -X POST -H 'Transfer-Encoding: chunked' --data-binary @<(big 21000000))" == "413" ]] \
  || fail "chunked body over 20 MiB not cut on the upload route"
[[ "$(curl_c /api/ingestion/documents/text -X POST -H 'Transfer-Encoding: chunked' --data-binary @<(big 12000000))" == "413" ]] \
  || fail "chunked 12 MB body not cut on another ingestion route"
ok "chunked bodies on the upload route: 12 MB delivered whole, 21 MB cut with 413; 12 MB cut on its neighbour"
docker rm -f -v "$ECHO_NAME" >/dev/null

step "caddy: api stopped gives 502 at once, database down passes the 503 through"
start=$SECONDS
[[ "$(curl_c /api/health)" == "502" ]] || fail "api stopped did not give 502"
((SECONDS - start <= 4)) || fail "502 took $((SECONDS - start))s"
[[ "$(hdr Cache-Control)" == "no-store" ]] || fail "502 is cacheable"
[[ "$(curl_c /)" == "200" ]] && body_has_root || fail "the shell is not served while the api is down"
ok "502 within $((SECONDS - start))s, no-store; the shell still loads"
"${DEV[@]}" start api >/dev/null 2>&1
HEALTH_URL="$CADDY/api/health" wait_status 200 60
ok "200 again through caddy after api start"
"${DEV[@]}" stop db >/dev/null 2>&1
HEALTH_URL="$CADDY/api/health" wait_status 503 20
[[ "$(curl_c /api/health)" == "503" ]] && grep -q '"database unavailable"' "$CURL_DIR/b" \
  || fail "503 body: $(cat "$CURL_DIR/b")"
ok "db down: the api's 503 JSON reaches the client unchanged"
"${DEV[@]}" start db >/dev/null 2>&1
HEALTH_URL="$CADDY/api/health" wait_status 200 60
ok "db back: 200 through caddy"

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
for pair in "api 512MiB" "caddy 128MiB" "db 768MiB" "redis 256MiB"; do
  set -- $pair
  grep -Eq "jobapp-$1-[0-9]+ .* / $2\$" <<<"$stats" || fail "$1 limit is not $2"
done
ok "api 512MiB, caddy 128MiB, db 768MiB, redis 256MiB"

# compose.mac.yml: OPENSSL_armcap=0 for api and worker, or the runtime image dies with SIGILL at
# import inside Docker on an M4 (README, Apple silicon). This script only ever runs on the Mac.
SCRATCH=(docker compose -p "$SCRATCH_PROJECT" -f compose.yml -f compose.mac.yml)
psql_scratch() { "${SCRATCH[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$1"; }

step "production stack alone on $SCRATCH_PROJECT: runtime image healthy, migrations, only caddy published"
# An ephemeral host port, so the dev caddy keeps its own.
HTTP_PORT=0 "${SCRATCH[@]}" up -d --build --wait --wait-timeout 180 >/dev/null 2>&1 || fail "prod stack not healthy"
scratch_caddy="$(docker port "$("${SCRATCH[@]}" ps -q caddy)" 80/tcp | head -n 1)"
[[ "$scratch_caddy" == 127.0.0.1:* ]] || fail "prod caddy published on $scratch_caddy"
[[ "$(curl -s --max-time 5 "http://$scratch_caddy/api/health")" == '{"status":"ok"}' ]] \
  || fail "health through the prod caddy"
curl -s --max-time 5 "http://$scratch_caddy/some/route" | grep -q '<div id="root"></div>' \
  || fail "SPA fallback through the prod caddy"
ok "prod caddy on $scratch_caddy: /api/health ok, /some/route is the shell"
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
ok "runtime image healthy, alembic at 0001, api and db publish nothing, no dev dependencies"

step "volume persistence on throwaway project $SCRATCH_PROJECT"
psql_scratch "create table smoke (note text); insert into smoke values ('persisted ✓')" >/dev/null
"${SCRATCH[@]}" down >/dev/null 2>&1
HTTP_PORT=0 "${SCRATCH[@]}" up -d --wait db >/dev/null 2>&1
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
