#!/usr/bin/env bash
# Smoke test of the Compose stack on the Mac (tasks 3 and 6 of the platform plan). Rerunnable.
#
#   tests/compose_smoke.sh            run every check, then stop the dev stack (volumes kept)
#   KEEP_UP=1 tests/compose_smoke.sh  leave the dev stack running at the end
#
# Needs Docker, python3, curl and a .env at the repository root (copy .env.example).
# The persistence check runs on a throwaway project name and only ever deletes that project's
# volumes; the dev stack's pgdata is never removed, and nothing here downgrades the dev database:
# the migration round trip (downgrade base, upgrade head) runs on the throwaway project. It ran
# on the dev database while the only revision was the empty baseline; with real migrations it
# would drop every table of the database a developer works in.
#
#   COMPOSE_PROJECT_NAME=jobapp-x tests/compose_smoke.sh   the dev stack under another project
#                                                          name than jobapp (its own volumes)
#   SMOKE_DEV_OVERLAY=a.yml SMOKE_PROD_OVERLAY=b.yml tests/compose_smoke.sh
#       one more Compose file for the dev stack and one for the production stack: image tags
#       of one's own, on a machine where jobapp-*:dev and jobapp-*:latest are someone else's
#       (both stacks are built here, and a build replaces the tag it is given)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The project of the dev stack: compose.yml names it jobapp, COMPOSE_PROJECT_NAME overrides that.
PROJECT="${COMPOSE_PROJECT_NAME:-jobapp}"
DEV=(docker compose -f compose.yml -f compose.dev.yml)
[[ -z "${SMOKE_DEV_OVERLAY:-}" ]] || DEV+=(-f "$SMOKE_DEV_OVERLAY")
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
assert set(services) == {"api", "caddy", "db", "parser", "redis", "tailscale", "worker"}, sorted(services)
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
# parser (ingestion G2a): the image of the worker, hardened, on the internal network alone. The same
# keys are held one by one by api/tests/ingestion/parser/test_compose_parser.py.
parser = services["parser"]
assert parser["image"] == worker["image"] and "build" not in parser, parser["image"]
assert parser["command"] == ["python", "-I", "/app/app/ingestion/sandbox/server.py"], parser["command"]
assert parser["user"] == "10002:10002" and parser["read_only"] is True and parser["cap_drop"] == ["ALL"], parser
assert parser["security_opt"] == ["no-new-privileges:true"] and parser["ipc"] == "none", parser
assert parser["pids_limit"] == 32 and int(parser["mem_limit"]) == 640 * 1024**2, parser
assert parser["environment"] == {"PARSER_PORT": "7070"} and "env_file" not in parser, parser.get("environment")
assert not parser.get("volumes") and not parser.get("tmpfs") and not parser.get("depends_on"), parser
assert list(parser["networks"]) == ["parsing"], parser["networks"]
assert cfg["networks"]["parsing"]["internal"] is True, cfg["networks"]
assert sorted(worker["networks"]) == ["default", "parsing"], worker["networks"]
for name, svc in services.items():
    if name not in ("parser", "worker"):
        assert list(svc["networks"]) == ["default"], (name, svc["networks"])
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
ok "parser: the worker's image, uid 10002, hardened, no env_file, on the internal network that the worker alone shares"
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
for svc in api caddy db parser redis worker; do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$("${DEV[@]}" ps -q "$svc")")"
  [[ "$health" == "healthy" ]] || fail "$svc is $health"
done
ok "api, caddy, db, parser, redis, worker healthy"
# What the dev api runs, for the stand in that takes its place further down.
API_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$("${DEV[@]}" ps -q api)")"

# parser_checks <compose command...>: the parser of a running stack, from outside and from inside
# (ingestion G2a). PARSER_MOUNT is --code-mount for the dev stack, whose parser has the code
# mounted read only, and empty for the production stack, whose parser has nothing mounted.
parser_checks() {
  local parser_id worker_id summary line reach db_ip parser_ip name
  local -a secret_names=()
  parser_id="$("$@" ps -q parser)"
  worker_id="$("$@" ps -q worker)"
  "$@" exec -T parser python -I /app/app/ingestion/sandbox/server.py --check \
    || fail "the parser's healthcheck fails"
  ok "healthcheck: a child started, locked itself down and reported the filter and the limits in force"
  # The names of every variable of .env, never a value: none may be in the parser's environment.
  while read -r name; do
    secret_names+=(--secret-name "$name")
  done < <(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env .env.example | sort -u)
  # shellcheck disable=SC2086 # PARSER_MOUNT is one option or nothing
  summary="$(docker inspect "$parser_id" | python3 tests/parser_check.py inspect ${PARSER_MOUNT:-} "${secret_names[@]}")" \
    || fail "the parser container is not what compose.yml says"
  ok "$summary"
  [[ "$(docker inspect -f '{{.Image}}' "$parser_id")" == "$(docker inspect -f '{{.Image}}' "$worker_id")" ]] \
    || fail "the parser does not run the image of the worker beside it"
  ok "the parser runs the worker's image, $(docker inspect -f '{{.Config.Image}}' "$parser_id"), which it did not build"
  db_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$("$@" ps -q db)")"
  parser_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$parser_id")"
  reach="$("$@" exec -T parser python -I - reach db:5432 redis:6379 api:8000 caddy:80 example.org:443 \
    "$db_ip:5432" 1.1.1.1:443 <tests/parser_check.py)"
  [[ "$(wc -l <<<"$reach")" -eq 7 ]] || fail "the probe inside the parser did not run: $reach"
  ! grep -q 'connected=1' <<<"$reach" || fail "the parser reached something: $reach"
  ! grep -Eq '^[a-z][a-z.]*:[0-9]+ resolved=1' <<<"$reach" || fail "the parser resolved a name: $reach"
  ok "the parser cannot resolve db, redis, api, caddy or an outside name, nor connect to the db's address or to 1.1.1.1"
  reach="$("$@" exec -T api python - reach "parser:7070" "$parser_ip:7070" db:5432 <tests/parser_check.py)"
  grep -q '^parser:7070 resolved=0 connected=0$' <<<"$reach" || fail "the api resolved the parser: $reach"
  grep -q "^$parser_ip:7070 resolved=1 connected=0\$" <<<"$reach" || fail "the api reached the parser: $reach"
  grep -q '^db:5432 resolved=1 connected=1$' <<<"$reach" || fail "the control failed, the probe reaches nothing: $reach"
  ok "the api cannot resolve the parser or connect to its address (and the same probe reaches db)"
  line="$("$@" exec -T worker python -m app.ingestion.parser_client ping --bytes 1048576)" \
    || fail "the worker's ping of the parser failed: $line"
  grep -q '"ok":true' <<<"$line" || fail "the worker's ping: $line"
  ok "the worker reaches it, 1 MiB through a sandboxed child and back: $line"
}

step "parser: the sandbox in force, nothing to steal, nowhere to go (dev stack)"
PARSER_MOUNT=--code-mount parser_checks "${DEV[@]}"

step "GET /api/health"
body="$(curl -s --max-time 5 "$HEALTH_URL")"
[[ "$body" == '{"status":"ok"}' ]] || fail "unexpected body: $body"
ok "$body"

step "alembic: one head, upgrade head twice, current is that head"
# The head is whatever the migrations of this tree say, never a number written here: every module
# adds revisions. One line "<revision> (head)": two heads would be two lines and fail here.
head="$("${DEV[@]}" exec -T api alembic heads 2>/dev/null | tr -d '\r')"
[[ "$head" =~ ^[0-9a-z_]+\ \(head\)$ ]] || fail "alembic heads: ${head:-nothing}"
"${DEV[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
"${DEV[@]}" exec -T api alembic upgrade head >/dev/null 2>&1
current="$("${DEV[@]}" exec -T api alembic current 2>/dev/null | tr -d '\r')"
[[ "$current" == "$head" ]] || fail "alembic current: $current, head: $head"
ok "upgrade twice, current is $current (no downgrade here: this is the dev database)"

step "pytest inside the api container against the compose db ($TEST_DB)"
if [[ "$(psql_dev "select 1 from pg_database where datname = '$TEST_DB'")" != "1" ]]; then
  psql_dev "create database $TEST_DB" >/dev/null
fi
encoded_pw="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$PG_PASSWORD")"
test_url="postgresql+psycopg://${PG_USER}:${encoded_pw}@db:5432/${TEST_DB}"
# A few files, not the suite: the point here is that the dev image holds the test tools and
# reaches the compose database. The whole suite is CI's (the api job). In this container it is
# one process next to uvicorn under the api's 512m: on 2026-10-11 it stood at 438 MiB at 95
# percent of the tests, after more than twenty minutes, and the run ended with status 137.
"${DEV[@]}" exec -T -e TEST_DATABASE_URL="$test_url" api pytest -q -m "not docker" -p no:cacheprovider \
  tests/test_health.py tests/test_db.py tests/test_migrations.py tests/ingestion/parser \
  | tail -n 3
ok "pytest -m 'not docker' passed on tests/test_health.py, test_db.py, test_migrations.py and tests/ingestion/parser"

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
# 10 MiB exactly passes caddy and is answered by the api: its 401, since a body with no session
# is refused unread on every route that is not public (issue #100; a 405 before). With the header
# the api's CSRF check asks of every write: without it the api answers 403 before anything else.
[[ "$(curl_c /api/health -X POST -H 'X-Requested-With: jobapp' --data-binary @<(big 10485760))" == "401" ]] \
  && grep -q 'Sign in to continue' "$CURL_DIR/b" || fail "10 MiB exactly did not reach the api"
ok "bodies over 10 MiB refused with 413, 10 MiB exactly reaches the api (its 401, no session)"
# --- public route body limit and timeouts (issue #100) ---------------------------------------
# A caller with no session reaches only the public routes under /api/auth/, whose bodies are a
# few hundred bytes. Caddy caps them at 64 KiB; every other route keeps 10 MiB (above). The CSRF
# header lets a small body reach the api; an over-limit body is Caddy's 413 before that.
csrf=(-H 'X-Requested-With: jobapp' -H 'Content-Type: application/json')
[[ "$(curl_c /api/auth/login -X POST "${csrf[@]}" --data-binary @<(big 65537))" == "413" ]] \
  || fail "64 KiB + 1 to a public route not refused"
[[ "$(curl_c /api/auth/login -X POST "${csrf[@]}" --data-binary @<(big 65536))" != "413" ]] \
  || fail "64 KiB exactly to a public route was refused by caddy"
# 200 KB on a route outside /api/auth/ passes caddy. The api answers it 401 without reading it:
# no session, no body (api/app/body_limit.py).
[[ "$(curl_c /api/jobs/import -X POST "${csrf[@]}" --data-binary @<(big 200000))" == "401" ]] \
  && grep -q 'Sign in to continue' "$CURL_DIR/b" || fail "200 KB to a route outside /api/auth/ did not reach the api"
ok "public /api/auth/ routes capped at 64 KiB, other routes keep 10 MiB"
# Request headers that never finish are dropped by caddy after read_header (10s), not after its
# default minute. (A stalled body is left to caddy's default of one minute and, before that, to
# the api's own rules.)
python3 - "$HTTP_PORT" <<'PY' || fail "caddy did not drop a request whose headers never finish within 20s"
import socket, sys, time
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=5)
s.settimeout(30)
s.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nX-Partial")
start = time.monotonic()
try:
    s.recv(32)
except OSError:
    pass
finally:
    s.close()
held = time.monotonic() - start
assert held < 20, f"held {held:.1f}s"
PY
ok "unfinished request headers are dropped within caddy's read_header timeout"
# --- end public route body limit and timeouts ------------------------------------------------
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
  -v "$ROOT/tests/echo_upstream.py:/echo.py:ro" "$API_IMAGE" python /echo.py >/dev/null
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
for pair in "api 512MiB" "caddy 128MiB" "db 768MiB" "redis 256MiB" "parser 640MiB" "worker 1.5GiB"; do
  set -- $pair
  grep -Eq "^$PROJECT-$1-[0-9]+ .* / $2\$" <<<"$stats" || fail "$1 limit is not $2"
done
ok "api 512MiB, caddy 128MiB, db 768MiB, redis 256MiB, parser 640MiB, worker 1.5GiB"

# compose.mac.yml: OPENSSL_armcap=0 for api and worker, or the runtime image dies with SIGILL at
# import inside Docker on an M4 (README, Apple silicon). This script only ever runs on the Mac.
SCRATCH=(docker compose -p "$SCRATCH_PROJECT" -f compose.yml -f compose.mac.yml)
[[ -z "${SMOKE_PROD_OVERLAY:-}" ]] || SCRATCH+=(-f "$SMOKE_PROD_OVERLAY")
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
scratch_alembic() { "${SCRATCH[@]}" exec -T api alembic "$@" 2>/dev/null | tr -d '\r'; }
scratch_alembic upgrade head >/dev/null
[[ "$(scratch_alembic current)" == "$head" ]] || fail "alembic in the runtime image: $(scratch_alembic current), head: $head"
"${SCRATCH[@]}" exec -T api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/api/health').read().decode())" \
  | grep -q '"status":"ok"' || fail "health inside the runtime image"
if "${SCRATCH[@]}" exec -T api python -c "import pytest" >/dev/null 2>&1; then
  fail "runtime image ships dev dependencies"
fi
ok "runtime image healthy, alembic at $head, api and db publish nothing, no dev dependencies"

step "parser of the production stack, started from an image only the worker builds"
PARSER_MOUNT="" parser_checks "${SCRATCH[@]}"

step "alembic round trip on throwaway project $SCRATCH_PROJECT: downgrade base, upgrade head"
# Here and only here: this database is deleted with the project at the end of the script.
scratch_alembic downgrade base >/dev/null || fail "downgrade base"
current="$(scratch_alembic current)"
[[ -z "$current" ]] || fail "after downgrade base, current is $current"
scratch_alembic upgrade head >/dev/null || fail "upgrade head after downgrade base"
current="$(scratch_alembic current)"
[[ "$current" == "$head" ]] || fail "alembic current: $current, head: $head"
ok "downgrade base then upgrade head: $current"

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
