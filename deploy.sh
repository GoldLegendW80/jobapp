#!/usr/bin/env bash
# Ship the app to the Jetson and run it there (platform module, SPEC-platform.md).
#
#   ./deploy.sh [deploy] [--env]   rsync the tree, build on the Jetson, compose up, migrate, health
#   ./deploy.sh logs <service>     follow the logs of one service (last 200 lines first)
#   ./deploy.sh ps                 docker compose ps on the Jetson
#   ./deploy.sh stats              docker stats --no-stream of the stack
#   ./deploy.sh ssh                interactive shell in the deploy directory
#   ./deploy.sh url                print the current https://*.trycloudflare.com URL, alone on stdout
#   ./deploy.sh smoke [--full]     checks on the Jetson, then through the public URL; --full also
#                                  stops and starts db once (tests/jetson_smoke.sh)
#   ./deploy.sh backup | restore   reserved for task 9
#
# Settings, first found wins: the shell environment, then .env at the repository root, then
# the defaults below. DEPLOY_HOST (ssh alias, default jetson), DEPLOY_DIR (default ~/jobapp).
#
# Secrets: the Jetson's .env is generated once into .env.jetson (git-ignored, mode 600) with
# random POSTGRES_PASSWORD and SECRET_KEY, and copied to the Jetson only when the remote .env is
# missing or with --env. rsync never deletes the remote .env, and nothing here touches volumes.
# Remote command strings are built here on purpose, with every value quoted by printf %q.
# shellcheck disable=SC2029
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOCAL_ENV="$ROOT/.env.jetson"
MIN_DISK_GB=5
HEALTH_TIMEOUT_S=90
SSH_TIMEOUT_S=5
# How long `url` waits for cloudflared to log its URL after a (re)start.
URL_TIMEOUT_S="${DEPLOY_URL_TIMEOUT:-30}"
# How long the public checks wait for a fresh trycloudflare hostname to resolve and route. macOS
# caches a negative DNS answer for the zone's 60s, so this must outlast one negative TTL.
PUBLIC_TIMEOUT_S="${DEPLOY_PUBLIC_TIMEOUT:-90}"
# Compose profile of the cloudflared service (compose.yml): enabled on the Jetson only.
PROFILE=tunnel

# --- output -------------------------------------------------------------------------------
step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
note() { printf '   %s\n' "$*" >&2; } # progress that must stay off stdout (url)
warn() { printf '   WARNING: %s\n' "$*" >&2; }
die() {
  printf 'deploy.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- settings -----------------------------------------------------------------------------
envval() { # envval <file> <NAME>: value of NAME in a dotenv file, without sourcing it
  [[ -f "$1" ]] || return 0
  sed -n "s/^[[:space:]]*$2=//p" "$1" | tail -n 1
}

setting() { # setting <NAME> <default>: environment, then .env, then default
  local value="${!1:-}"
  [[ -n "$value" ]] || value="$(envval "$ROOT/.env" "$1")"
  printf '%s' "${value:-$2}"
}

HOST="$(setting DEPLOY_HOST jetson)"
# shellcheck disable=SC2088 # a literal ~/ that is stripped just below, not expanded locally
RDIR="$(setting DEPLOY_DIR '~/jobapp')"
RDIR="${RDIR#\~/}" # relative to the remote home, where ssh starts
[[ -n "$RDIR" && "$RDIR" != "~" && "$RDIR" != "/" ]] || die "DEPLOY_DIR must name a directory"
RDIR_Q="$(printf '%q' "$RDIR")"

# --- ssh ----------------------------------------------------------------------------------
# One multiplexed connection for the whole run: every remote step reuses it.
CTL_DIR=""
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout="$SSH_TIMEOUT_S" -o ServerAliveInterval=15
  -o ServerAliveCountMax=4)

ssh_master() {
  CTL_DIR="$(mktemp -d /tmp/jobapp-ssh.XXXXXX)"
  SSH_OPTS+=(-o ControlMaster=auto -o "ControlPath=$CTL_DIR/%C" -o ControlPersist=120)
}

cleanup() {
  if [[ -n "$CTL_DIR" ]]; then
    ssh "${SSH_OPTS[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
    rm -rf "$CTL_DIR"
  fi
  [[ -z "${LOCK_DIR:-}" ]] || rm -rf "$LOCK_DIR"
}
trap cleanup EXIT

remote() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

RCOMPOSE="docker compose -f compose.yml --profile $PROFILE"
rcompose() { # rcompose <args...>: docker compose -f compose.yml --profile tunnel in the deploy directory
  local args
  args="$(printf ' %q' "$@")"
  remote "cd $RDIR_Q && $RCOMPOSE$args"
}

require_remote() {
  local err
  if ! err="$(ssh "${SSH_OPTS[@]}" "$HOST" true 2>&1)"; then
    die "cannot reach $HOST over ssh within ${SSH_TIMEOUT_S}s: ${err:-no answer}"
  fi
}

require_deployed() {
  remote "test -f $RDIR_Q/compose.yml" || die "nothing deployed in $HOST:$RDIR yet, run ./deploy.sh first"
}

# --- deploy steps -------------------------------------------------------------------------
TIMES=()
record() { # record <label> <start SECONDS>: keep and print the duration of one phase
  TIMES+=("$1: $((SECONDS - $2))s")
  info "$1: $((SECONDS - $2))s"
}

take_lock() {
  LOCK_DIR="${TMPDIR:-/tmp}/jobapp-deploy-$HOST.lock"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    local pid
    pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      LOCK_DIR=""
      die "another deploy to $HOST is running (pid $pid)"
    fi
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" || die "cannot take the deploy lock $LOCK_DIR"
  fi
  echo $$ >"$LOCK_DIR/pid"
}

preflight() {
  step "preflight: $HOST:$RDIR"
  command -v rsync >/dev/null || die "rsync is not installed on this machine"
  require_remote
  info "ssh ok"

  local facts arch docker_v compose_v disk_kb mem_kb
  # shellcheck disable=SC2016 # expanded by the remote shell
  facts="$(remote 'uname -m
docker info --format "{{.ServerVersion}}" 2>&1 | head -n 1
docker compose version --short 2>&1 | head -n 1
df -Pk "$HOME" | awk "NR==2 {print \$4}"
awk "/^MemAvailable:/ {print \$2}" /proc/meminfo')"
  {
    read -r arch
    read -r docker_v
    read -r compose_v
    read -r disk_kb
    read -r mem_kb
  } <<<"$facts"

  [[ "$arch" == "aarch64" ]] || die "$HOST is $arch, this stack is built for aarch64"
  [[ "$docker_v" =~ ^[0-9]+\. ]] ||
    die "docker does not answer on $HOST without sudo: $docker_v (is the user in the docker group?)"
  [[ "$compose_v" =~ ^v?[0-9]+\. ]] || die "docker compose plugin missing on $HOST: $compose_v"
  info "aarch64, docker $docker_v, compose $compose_v"

  ((disk_kb > MIN_DISK_GB * 1024 * 1024)) ||
    die "only $((disk_kb / 1024 / 1024))G free on $HOST, need more than ${MIN_DISK_GB}G"
  info "disk: $((disk_kb / 1024 / 1024))G free"
  if ((mem_kb < 2 * 1024 * 1024)); then
    warn "only $((mem_kb / 1024))M of RAM available on $HOST, builds may swap"
  else
    info "memory: $((mem_kb / 1024))M available"
  fi

  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    local dirty
    dirty="$(git -C "$ROOT" status --porcelain | wc -l | tr -d ' ')"
    if ((dirty > 0)); then
      warn "$dirty uncommitted change(s): deploying the working tree, not $(git -C "$ROOT" rev-parse --short HEAD)"
    else
      info "tree clean at $(git -C "$ROOT" rev-parse --short HEAD)"
    fi
  fi
}

generate_env() {
  local pg_password secret
  pg_password="$(openssl rand -hex 32)"
  secret="$(openssl rand -hex 48)"
  (
    umask 077
    cat >"$LOCAL_ENV" <<EOF
# Production environment of the Jetson, generated by deploy.sh on $(date +%Y-%m-%d).
# Git-ignored; the only other copy is $HOST:$RDIR/.env (mode 600). Keep it: Postgres keeps the
# password of its first start, so a new POSTGRES_PASSWORD needs ALTER USER on the live database.
APP_ENV=production
SECRET_KEY=$secret
LOG_LEVEL=info

POSTGRES_USER=jobapp
POSTGRES_PASSWORD=$pg_password
POSTGRES_DB=jobapp
DATABASE_URL=postgresql+psycopg://jobapp:$pg_password@db:5432/jobapp

REDIS_URL=redis://redis:6379/0

# Loopback only: reached with ssh or through the tunnel, never from the LAN.
HTTP_BIND=127.0.0.1
HTTP_PORT=${JETSON_HTTP_PORT:-80}
EOF
  )
}

ensure_local_env() {
  if [[ -f "$LOCAL_ENV" ]]; then
    return 0
  fi
  if remote "test -f $RDIR_Q/.env"; then
    # The Jetson already has its secrets: keep a local copy rather than inventing new ones.
    (
      umask 077
      remote "cat $RDIR_Q/.env" >"$LOCAL_ENV"
    )
    info "copied the existing $HOST:$RDIR/.env to .env.jetson"
  else
    generate_env
    info "generated .env.jetson with a random POSTGRES_PASSWORD and SECRET_KEY"
  fi
}

push_env() { # push_env <force 0|1>
  step "environment"
  ensure_local_env
  chmod 600 "$LOCAL_ENV"
  local remote_has=0
  remote "test -f $RDIR_Q/.env" && remote_has=1
  if ((remote_has == 1 && $1 == 0)); then
    info "remote .env kept (pass --env to replace it with .env.jetson)"
  else
    if ((remote_has == 1)); then
      local old new
      old="$(remote "sed -n 's/^POSTGRES_PASSWORD=//p' $RDIR_Q/.env")"
      new="$(envval "$LOCAL_ENV" POSTGRES_PASSWORD)"
      if [[ "$old" != "$new" ]] && remote 'docker volume inspect jobapp_pgdata >/dev/null 2>&1'; then
        die "--env would change POSTGRES_PASSWORD but the jobapp_pgdata volume keeps the old one; run ALTER USER first or restore the old value in .env.jetson"
      fi
    fi
    remote "umask 077 && cat > $RDIR_Q/.env.tmp && mv $RDIR_Q/.env.tmp $RDIR_Q/.env" <"$LOCAL_ENV"
    info "copied .env.jetson to $HOST:$RDIR/.env"
  fi
  remote "chmod 600 $RDIR_Q/.env"
  local mode
  mode="$(remote "stat -c %a $RDIR_Q/.env")"
  [[ "$mode" == "600" ]] || die "remote .env has mode $mode, expected 600"
  info "remote .env mode 600"
}

sync_tree() {
  step "rsync to $HOST:$RDIR"
  remote "mkdir -p $RDIR_Q"
  local ssh_cmd
  ssh_cmd="ssh$(printf ' %q' "${SSH_OPTS[@]}")"
  # .rsyncignore excludes .env*, so --delete never removes the remote .env.
  rsync -a --delete --exclude-from="$ROOT/.rsyncignore" -e "$ssh_cmd" "$ROOT/" "$HOST:$RDIR/"
  info "files: $(remote "find $RDIR_Q -type f | wc -l") on $HOST"
}

check_port() {
  local port bind used ours
  port="$(remote "sed -n 's/^HTTP_PORT=//p' $RDIR_Q/.env | tail -n 1")"
  bind="$(remote "sed -n 's/^HTTP_BIND=//p' $RDIR_Q/.env | tail -n 1")"
  HTTP_PORT="${port:-80}"
  HTTP_BIND="${bind:-127.0.0.1}"
  used="$(remote "ss -ltnH 'sport = :$HTTP_PORT'" || true)"
  # Taken by our own caddy is fine: compose recreates it on the same port.
  ours="$(remote "id=\$(cd $RDIR_Q && $RCOMPOSE ps -q caddy 2>/dev/null) &&
    [ -n \"\$id\" ] && docker port \"\$id\" 80/tcp" 2>/dev/null || true)"
  if [[ -n "$used" && "$ours" != *":$HTTP_PORT" ]]; then
    die "port $HTTP_PORT is already taken on $HOST; set HTTP_PORT to a free port in .env.jetson and deploy with --env"
  fi
  info "caddy on $HTTP_BIND:$HTTP_PORT"
}

build_images() {
  step "build on $HOST (native arm64, BuildKit cache)"
  local svc start
  for svc in api caddy; do
    start=$SECONDS
    rcompose --progress=quiet build "$svc" || die "build of $svc failed"
    record "build $svc" "$start"
  done
}

start_stack() {
  step "compose up"
  local start=$SECONDS
  if ! rcompose up -d --remove-orphans --wait --wait-timeout 180; then
    rcompose ps -a || true
    die "the stack did not become healthy; see ./deploy.sh logs <service>"
  fi
  record "up --wait" "$start"
}

migrate() {
  step "migrations"
  local start=$SECONDS
  rcompose exec -T api alembic upgrade head || die "alembic upgrade head failed"
  info "alembic current: $(rcompose exec -T api alembic current 2>/dev/null | tr -d '\r')"
  record migrations "$start"
}

health() {
  step "health through caddy on $HOST loopback"
  local url="http://127.0.0.1:$HTTP_PORT" deadline=$((SECONDS + HEALTH_TIMEOUT_S)) body=""
  while ((SECONDS < deadline)); do
    body="$(remote "curl -s --max-time 5 $url/api/health" || true)"
    [[ "$body" == '{"status":"ok"}' ]] && break
    sleep 2
  done
  [[ "$body" == '{"status":"ok"}' ]] || die "$url/api/health answered '${body:-nothing}' for ${HEALTH_TIMEOUT_S}s"
  info "/api/health: $body"
  remote "curl -s --max-time 5 $url/some/route" | grep -q '<div id="root">' ||
    die "$url/some/route does not serve the React shell"
  info "/some/route: React shell"
}

# --- tunnel -------------------------------------------------------------------------------
# extract_url: the last https://<name>.trycloudflare.com on stdin. cloudflared prints it in a box
# ("Your quick Tunnel has been created! Visit it at ..."); api.trycloudflare.com is the service it
# asks for the hostname, never the answer.
extract_url() {
  grep -oE 'https://[a-z0-9]+(-[a-z0-9]+)*\.trycloudflare\.com([^A-Za-z0-9.-]|$)' |
    sed -E 's/(\.com).*$/\1/' | grep -v '^https://api\.' | tail -n 1 || true
}

# tunnel_url: print the URL of the running cloudflared's current start on stdout, waiting up to
# URL_TIMEOUT_S for it to appear. Only logs since the container's last StartedAt are read, so a
# restart never yields the previous, dead hostname. Diagnostics on stderr; exit 1 if none.
tunnel_url() {
  local deadline=$((SECONDS + URL_TIMEOUT_S)) id state started url="" waited=0
  while :; do
    id="$(rcompose ps -a -q cloudflared 2>/dev/null | head -n 1 || true)"
    [[ -n "$id" ]] || die "cloudflared is not running on $HOST: no container (deploy with ./deploy.sh, then ./deploy.sh ps)"
    read -r state started <<<"$(remote "docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' $(printf '%q' "$id")" || true)"
    if [[ "$state" == "running" ]]; then
      url="$(remote "docker logs --since $(printf '%q' "$started") $(printf '%q' "$id") 2>&1" | extract_url)"
      [[ -n "$url" ]] && break
    elif [[ "$state" != "restarting" && "$state" != "created" ]]; then
      die "cloudflared is ${state:-gone} on $HOST, not running (./deploy.sh logs cloudflared; start it with ./deploy.sh)"
    fi
    if ((SECONDS >= deadline)); then
      die "cloudflared ($state since ${started:-?}) logged no trycloudflare URL within ${URL_TIMEOUT_S}s; see ./deploy.sh logs cloudflared"
    fi
    if ((waited == 0)); then
      note "waiting up to ${URL_TIMEOUT_S}s for cloudflared to log its URL"
      waited=1
    fi
    sleep 2
  done
  printf '%s\n' "$url"
}

# --- public checks, from this machine through the tunnel ------------------------------------
PIN=() # --resolve host:443:ip, set when the system resolver still caches a negative answer
pcurl() { # pcurl <url> [curl args]: status code on stdout, headers in $PDIR/h, body in $PDIR/b
  local url="$1"
  shift
  curl -s --max-time 30 ${PIN[@]+"${PIN[@]}"} -D "$PDIR/h" -o "$PDIR/b" -w '%{http_code}' "$@" "$url" || true
}
pbrief() { # one line about the last body: an HTML page's <title> (Cloudflare's error pages), else its start
  local title
  title="$(tr -d '\r\n' <"$PDIR/b" | sed -n 's/.*<title>\([^<]*\)<\/title>.*/\1/p' | head -c 120)"
  if [[ -n "$title" ]]; then printf 'page "%s"' "$title"; else tr -d '\r\n' <"$PDIR/b" | head -c 120; fi
}
phdr() { tr -d '\r' <"$PDIR/h" | grep -i "^$1:" | tail -n 1 | cut -d' ' -f2-; }

# wait_public <url>: until caddy's /healthz answers through the tunnel. A fresh hostname takes a
# few seconds to exist in DNS and to route to this connector; back off 2, 4, 8, 15, 15... seconds,
# so a slow start costs a handful of requests, not a stream (quick tunnels are rate limited).
wait_public() {
  local url="$1" host deadline=$((SECONDS + PUBLIC_TIMEOUT_S)) delay=2 code ip
  host="${url#https://}"
  while :; do
    code="$(pcurl "$url/healthz")"
    [[ "$code" == "200" && "$(cat "$PDIR/b")" == "ok" ]] && return 0
    if [[ "$code" == "000" && ${#PIN[@]} -eq 0 ]] && command -v dig >/dev/null; then
      # Not resolvable here yet: ask 1.1.1.1 directly, past the local negative cache.
      ip="$(dig +short +time=3 +tries=1 @1.1.1.1 A "$host" 2>/dev/null | grep -E '^[0-9.]+$' | head -n 1 || true)"
      [[ -z "$ip" ]] || PIN=(--resolve "$host:443:$ip")
    fi
    ((SECONDS + delay <= deadline)) || return 1
    note "$host not answering yet (HTTP $code), retrying in ${delay}s"
    sleep "$delay"
    delay=$((delay * 2 > 15 ? 15 : delay * 2))
  done
}

cmd_deploy() {
  local force_env=0 arg
  for arg in "$@"; do
    case "$arg" in
      --env) force_env=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "unknown option for deploy: $arg" ;;
    esac
  done
  local t0=$SECONDS
  take_lock
  ssh_master
  preflight
  local start=$SECONDS
  sync_tree
  record rsync "$start"
  push_env "$force_env"
  check_port
  build_images
  start_stack
  migrate
  health
  step "public URL"
  local url
  url="$(tunnel_url)"
  PDIR="$(mktemp -d)"
  if wait_public "$url"; then
    info "answers through the tunnel"
  else
    warn "$url does not answer from here yet; try ./deploy.sh smoke in a minute"
  fi
  rm -rf "$PDIR"
  step "done in $((SECONDS - t0))s"
  printf '   %s\n' "${TIMES[@]}"
  printf '\n%s\n' "$url"
}

# --- other subcommands --------------------------------------------------------------------
cmd_logs() {
  [[ $# -eq 1 ]] || die "usage: ./deploy.sh logs <service>"
  require_remote
  require_deployed
  local tty=-T
  [[ -t 0 ]] && tty=-t
  ssh "${SSH_OPTS[@]}" "$tty" "$HOST" \
    "cd $RDIR_Q && docker compose -f compose.yml logs -f --tail 200 $(printf '%q' "$1")"
}

cmd_ps() {
  require_remote
  require_deployed
  rcompose ps -a
}

cmd_stats() {
  require_remote
  require_deployed
  remote "cd $RDIR_Q && docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}' \$($RCOMPOSE ps -q)"
}

cmd_ssh() {
  require_remote
  ssh -t "${SSH_OPTS[@]}" "$HOST" "cd $RDIR_Q 2>/dev/null; exec \$SHELL -l"
}

cmd_url() {
  [[ $# -eq 0 ]] || die "usage: ./deploy.sh url"
  ssh_master
  require_remote
  require_deployed
  tunnel_url
}

# smoke: tests/jetson_smoke.sh over ssh (quick unless --full), then the public checks. Every
# check runs and is reported; the exit code is 1 if any failed.
cmd_smoke() {
  local full=0 arg
  for arg in "$@"; do
    case "$arg" in
      --full) full=1 ;;
      *) die "unknown option for smoke: $arg (usage: ./deploy.sh smoke [--full])" ;;
    esac
  done
  ssh_master
  require_remote
  require_deployed
  local failed=0
  step "on $HOST: tests/jetson_smoke.sh$( ((full)) && echo ' (full: stops and starts db)')"
  if ! DEPLOY_HOST="$HOST" DEPLOY_DIR="$RDIR" JETSON_SMOKE_FULL="$full" "$ROOT/tests/jetson_smoke.sh"; then
    failed=$((failed + 1))
  fi

  step "public, through the quick tunnel"
  local url
  if ! url="$(tunnel_url)"; then
    printf '\nSMOKE FAILED: no tunnel URL\n' >&2
    exit 1
  fi
  info "$url"
  PDIR="$(mktemp -d)"
  # shellcheck disable=SC2064 # expand PDIR now
  trap "rm -rf '$PDIR'; cleanup" EXIT
  pass() { info "ok: $*"; }
  flunk() {
    printf '   FAIL: %s\n' "$*" >&2
    failed=$((failed + 1))
  }
  if ! wait_public "$url"; then
    flunk "$url/healthz never answered within ${PUBLIC_TIMEOUT_S}s (DNS, or cloudflared not connected)"
    printf '\nSMOKE FAILED: %d check(s)\n' "$failed" >&2
    exit 1
  fi
  pass "caddy answers through the tunnel"

  local code body
  code="$(pcurl "$url/api/health")"
  body="$(cat "$PDIR/b")"
  if [[ "$code" == "200" && "$body" == '{"status":"ok"}' ]]; then
    pass "/api/health: $body"
  else
    flunk "/api/health answered $code: $(pbrief)"
  fi

  local route
  for route in / "/some/deep/route?x=1"; do
    code="$(pcurl "$url$route")"
    if [[ "$code" == "200" ]] && grep -q '<div id="root">' "$PDIR/b"; then
      pass "$route serves the React shell"
    else
      flunk "$route answered $code without the React shell"
    fi
  done
  local want got hname
  for want in "X-Frame-Options: DENY" "X-Content-Type-Options: nosniff" \
    "Referrer-Policy: strict-origin-when-cross-origin" \
    "Content-Security-Policy: frame-ancestors 'none'; base-uri 'self'; object-src 'none'" \
    "Cache-Control: no-cache"; do
    hname="${want%%:*}"
    got="$(phdr "$hname")"
    [[ "$got" == "${want#*: }" ]] || flunk "$hname is '${got}', expected '${want#*: }'"
  done
  pass "security headers and no-cache on index.html"

  # The visitor's address must reach the api: Cloudflare's view of this machine (cdn-cgi/trace)
  # against the client the api logs for one tagged request carrying a spoofed X-Forwarded-For.
  # IPv4 for both, so the two requests cannot leave by different address families.
  local rid trace_ip logged
  rid="smoke-$(date +%s)-$RANDOM"
  pcurl "$url/cdn-cgi/trace" -4 >/dev/null
  trace_ip="$(sed -n 's/^ip=//p' "$PDIR/b")"
  code="$(pcurl "$url/api/health" -4 -H "X-Request-ID: $rid" -H 'X-Forwarded-For: 203.0.113.9')"
  logged=""
  local i
  for i in 1 2 3 4 5; do
    logged="$(rcompose logs --no-log-prefix --since 5m api 2>/dev/null | grep -F "\"request_id\": \"$rid\"" |
      sed -n 's/.*"client": "\([^"]*\)".*/\1/p' | tail -n 1 || true)"
    [[ -n "$logged" ]] && break
    sleep "$i"
  done
  if [[ -z "$trace_ip" ]]; then
    flunk "cdn-cgi/trace gave no ip (HTTP $code for the tagged request)"
  elif [[ "$logged" == "$trace_ip" ]]; then
    pass "the api logs the visitor's address $logged (Cf-Connecting-Ip), spoofed headers ignored"
  else
    flunk "the api logged client '${logged:-nothing}' for $rid, Cloudflare sees this machine as $trace_ip"
  fi

  # The Cloudflare edge refuses a visitor who sends Cf-Connecting-Ip (403), so the header caddy
  # trusts on the tunnel listener cannot be forged from outside.
  code="$(pcurl "$url/api/health" -H 'Cf-Connecting-Ip: 203.0.113.10')"
  if [[ "$code" == "403" ]]; then
    pass "a forged Cf-Connecting-Ip is refused by the Cloudflare edge (403)"
  else
    flunk "a forged Cf-Connecting-Ip answered $code, expected the edge's 403"
  fi

  # A 5 MB body, half the caddy limit, crosses the tunnel and reaches the api, which answers 405
  # (POST on a GET route) rather than caddy's 413 or a tunnel error.
  code="$(head -c 5000000 /dev/zero | pcurl "$url/api/health" -X POST --data-binary @- -H 'Content-Type: application/octet-stream')"
  if [[ "$code" == "405" ]] && grep -q 'Method Not Allowed' "$PDIR/b"; then
    pass "5 MB upload reaches the api through the tunnel (405 from FastAPI)"
  else
    flunk "5 MB upload answered $code: $(pbrief)"
  fi

  if ((failed > 0)); then
    printf '\nSMOKE FAILED: %d check(s), see FAIL above\n' "$failed" >&2
    exit 1
  fi
  printf '\nSmoke passed on %s and through %s\n' "$HOST" "$url"
}

not_yet() {
  printf 'deploy.sh %s: not yet implemented (%s of the platform plan, tasks/todo.md)\n' "$1" "$2" >&2
  exit 3
}

main() {
  local cmd="${1:-deploy}"
  [[ $# -eq 0 ]] || shift
  case "$cmd" in
    deploy) cmd_deploy "$@" ;;
    --env) cmd_deploy --env "$@" ;;
    logs) cmd_logs "$@" ;;
    ps) cmd_ps ;;
    stats) cmd_stats ;;
    ssh) cmd_ssh ;;
    url) cmd_url "$@" ;;
    smoke) cmd_smoke "$@" ;;
    backup | restore) not_yet "$cmd" "task 9" ;;
    -h | --help | help) usage ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
