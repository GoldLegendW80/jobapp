#!/usr/bin/env bash
# Ship the app to the Jetson and run it there (platform module, SPEC-platform.md).
#
#   ./deploy.sh [deploy] [--env]   rsync the tree, build on the Jetson, migrate (one off container of
#                                  the new image, the previous release still serving), the browser
#                                  behind its host firewall (ops/browser-egress.sh; a browser image
#                                  that does not build means no browser, the rest deploys), compose
#                                  up, health. A migration that fails stops the deploy before anything
#                                  restarts (ops/migrate.sh, issue #81)
#   ./deploy.sh logs <service>     follow the logs of one service (last 200 lines first)
#   ./deploy.sh ps                 docker compose ps on the Jetson
#   ./deploy.sh stats              docker stats --no-stream of the stack
#   ./deploy.sh ssh                interactive shell in the deploy directory
#   ./deploy.sh url                print the public Tailscale Funnel URL (https://<node>.<tailnet>.ts.net),
#                                  read from `tailscale status`, alone on stdout
#   ./deploy.sh smoke [--full]     checks on the Jetson, then through the public URL; --full also
#                                  stops and starts db once (tests/jetson_smoke.sh)
#   ./deploy.sh backup [--pull]    pg_dump -Fc to jetson:~/jobapp-backups/jobapp-<UTC time>.dump,
#                                  verified, then the retention (BACKUP_KEEP, 7: the newest 7
#                                  dumps stay, a dump 7 days old or more goes); --pull copies it
#                                  to backups/, which no retention ever touches
#   ./deploy.sh backups            list the dumps on the Jetson
#   ./deploy.sh restore <file> [--keep]
#                                  restore into the scratch database jobapp_restore_check and print
#                                  the row counts; the live database is not touched
#   ./deploy.sh restore <file> --into-live [--yes]
#                                  replace the live database (asks you to type RESTORE jobapp):
#                                  pre-restore backup, api and worker stopped, restore, migrations,
#                                  health
#   ./deploy.sh restore --drop-scratch   drop the scratch database left by --keep
#   ./deploy.sh backup-schedule install|remove|status
#                                  nightly backup in the Jetson user's crontab (no sudo); status
#                                  shows the line, the retention its runs apply, the newest and
#                                  the oldest dump of each folder and what is left alone there
#   ./deploy.sh admin create <email>
#                                  print a one-time admin setup link on the public URL (a 1 hour
#                                  password reset link when that admin exists); runs
#                                  python -m app.identity.cli create-admin in the api container
#   ./deploy.sh admin unlock <email> [--ip <ip>]
#                                  delete the failed sign in attempts of that email (and of that
#                                  address) in the 15 minute window, print how many went
#   ./deploy.sh profile import <email>
#                                  convert ../profile and ../engine on the Mac (app.profile.cli
#                                  convert) and load the result into that account's profile on
#                                  the Jetson (app.profile.cli import in the api container)
#   ./deploy.sh jobs import <email>
#                                  convert ../data (jobs.csv, jobs/<id>/) on the Mac
#                                  (app.jobs.cli convert) and load it into that account's jobs on
#                                  the Jetson (app.jobs.cli import, from stdin: no HTTP upload, so
#                                  Caddy's 10 MiB body limit does not apply); idempotent
#   ./deploy.sh jobs mark-overrides <email> [--apply]
#                                  list the account's fits that differ from the computed ones
#                                  (set by hand in the CSV); a dry run unless --apply, which
#                                  marks them so no rescore replaces them (app.jobs.cli)
#   ./deploy.sh apply pause|resume|status
#                                  stop (or allow again) every application submission at once:
#                                  a Redis flag the apply gate reads before its decision and
#                                  again just before the click (app.apply.admin)
#   ./deploy.sh runner install|status|remove
#                                  the self-hosted GitHub Actions runner on the Jetson (ops/runner/):
#                                  a container with the host Docker socket, registered to the repo
#                                  with labels self-hosted, jetson, arm64; tokens from gh on the Mac
#
# The job folder holding profile/, engine/ and data/ is the parent of this repository, or
# JOBAPP_SOURCE_DIR. Both imports convert first, so a bad source fails before any network call.
#
# <file> is a local file (uploaded first), a path on the Jetson, a name in the backup directory,
# or `latest`. The work on the Jetson is done by ops/db.sh, streamed over ssh.
#
# Settings, first found wins: the shell environment, then .env at the repository root, then
# the defaults below. DEPLOY_HOST (ssh alias, default jetson), DEPLOY_DIR (default ~/jobapp),
# BACKUP_DIR (on the Jetson, default ~/jobapp-backups), BACKUP_KEEP (the retention, one number
# for the count of dumps and for their age in days; no default here: when it is not set on this
# Mac nothing is sent, and ops/db.sh reads the BACKUP_KEEP of the Jetson's .env, then its own
# default, 7, at each run; set here it is sent with every backup command and written into the
# cron line by backup-schedule install), BACKUP_ACCEPT_SHRINK (shell only, 1 for one
# `./deploy.sh backup`: accepts a dump less than half the size of the last one, which otherwise
# holds the rotation),
# BACKUP_CRON (schedule of backup-schedule, default "17 3 * * *", the Jetson's local time),
# DEPLOY_LOCK_MAX_S (default 3600: the Jetson releases the deploy lock after that long even if
# this Mac went to sleep holding it), MIGRATE_TIMEOUT_S (default 300: the longest the migrations of
# one deploy may take before they are stopped and rolled back).
#
# Secrets: the Jetson's .env is generated once into .env.jetson (git-ignored, mode 600) with
# random POSTGRES_PASSWORD, SECRET_KEY and CREDENTIALS_KEY, and copied to the Jetson only when the remote .env is
# missing or with --env. rsync never deletes the remote .env, and nothing here touches volumes.
# Both .env files are syntax checked by ops/env_check.sh before anything changes (preflight), and
# a TS_AUTHKEY left in either once the node is logged in is warned about at the end of a deploy.
# Remote command strings are built here on purpose, with every value quoted by printf %q.
# shellcheck disable=SC2029
set -euo pipefail

CALLER_PWD="$PWD" # local paths given on the command line are relative to where it was run
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOCAL_ENV="$ROOT/.env.jetson"
MIN_DISK_GB=5
HEALTH_TIMEOUT_S=90
SSH_TIMEOUT_S=5
# How long `url` waits for tailscale to be connected after a (re)start.
URL_TIMEOUT_S="${DEPLOY_URL_TIMEOUT:-30}"
# How long the public checks wait for the Funnel URL to answer: the first request after a first
# login also waits for the node's Let's Encrypt certificate.
PUBLIC_TIMEOUT_S="${DEPLOY_PUBLIC_TIMEOUT:-90}"
# Compose profile of the tailscale service (compose.yml): enabled on the Jetson only.
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

usage() { # the header comment, up to the settings paragraph
  awk 'NR > 1 && /^# Settings/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "${BASH_SOURCE[0]}"
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

# backup_dir: BACKUP_DIR as every command here uses it, sends it and writes it into the cron
# line: relative to the Jetson's home when it starts with ~/, and without a slash or a /. at its
# end. ops/db.sh trims them too, since `[[ -L folder/ ]]` is false for a symbolic link and a
# linked backup folder must never be deleted in (the review's B3); the name is trimmed here as
# well so that one spelling of it exists, in the lock, in the cron line and in the messages.
backup_dir() {
  local d
  # shellcheck disable=SC2088 # a literal ~/ that is stripped just below, not expanded locally
  d="$(setting BACKUP_DIR '~/jobapp-backups')"
  d="${d#\~/}"
  while [[ "$d" != / && ("$d" == */ || "$d" == */.) ]]; do
    d="${d%.}"
    d="${d%/}"
  done
  printf '%s' "$d"
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
  if [[ -n "${LOCK_PID:-}" ]]; then
    # Closing the FIFO ends the remote `cat`, which releases the Jetson's deploy lock.
    exec 8>&- || true
    wait "$LOCK_PID" 2>/dev/null || true
  fi
  if [[ -n "$CTL_DIR" ]]; then
    ssh "${SSH_OPTS[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
    rm -rf "$CTL_DIR"
  fi
  [[ -z "${LOCK_DIR:-}" ]] || rm -rf "$LOCK_DIR"
  [[ -z "${IMPORT_DIR:-}" ]] || rm -rf "$IMPORT_DIR"
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
# lock_script <dir> <max seconds>: the remote shell that takes flock on <dir>/.deploy.lock (waiting
# up to 15 minutes), prints `locked`, then holds it until its stdin closes or <max seconds> pass,
# whichever comes first. The cap is enforced on the Jetson: a Mac that sleeps mid deploy keeps its
# ssh session half open for hours (sshd only notices through TCP keepalive), and without the cap
# that session would block every GitHub deploy until then. A deploy takes minutes, so an hour is
# far above any real one.
lock_script() {
  printf 'mkdir -p %q && exec 9>>%q && { flock -n 9 || { echo busy; flock -w 900 9; }; } && echo locked && { timeout %q cat >/dev/null; true; }' \
    "$1" "$1/.deploy.lock" "$2"
}

# remote_lock: hold flock on <BACKUP_DIR>/.deploy.lock on the Jetson for the rest of this run. It
# is the lock .github/workflows/deploy.yml takes on the self-hosted runner, so a deploy from this
# Mac and one from GitHub never rsync or compose at the same time. A background ssh holds it while
# its stdin, a FIFO kept open on fd 8, stays open; cleanup closes fd 8 and the lock goes. The
# Jetson drops it on its own after DEPLOY_LOCK_MAX_S (lock_script).
remote_lock() {
  step "deploy lock on $HOST"
  local bdir max fifo="$CTL_DIR/lock.in" out="$CTL_DIR/lock.out" deadline
  bdir="$(backup_dir)"
  max="$(setting DEPLOY_LOCK_MAX_S 3600)"
  [[ "$max" =~ ^[1-9][0-9]*$ ]] || die "DEPLOY_LOCK_MAX_S must be a whole number of seconds, got '$max'"
  mkfifo "$fifo"
  remote "$(lock_script "$bdir" "$max")" <"$fifo" >"$out" 2>&1 &
  LOCK_PID=$!
  exec 8>"$fifo"
  deadline=$((SECONDS + 960))
  until grep -q '^locked' "$out" 2>/dev/null; do
    kill -0 "$LOCK_PID" 2>/dev/null || die "could not take the deploy lock on $HOST: $(cat "$out" 2>/dev/null)"
    ((SECONDS < deadline)) || die "the deploy lock on $HOST stayed busy for 16 minutes"
    sleep 1
  done
  if grep -q '^busy' "$out"; then
    info "waited for another deploy (GitHub or another Mac) to finish"
  fi
  info "held: $bdir/.deploy.lock (released by the Jetson after ${max}s at the latest)"
}
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

preflight() { # preflight [force_env 0|1]
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
  check_env_files "${1:-0}"
}

# --- .env syntax and the leftover auth key (issue #18) --------------------------------------
# check_env_files <force_env 0|1>: ops/env_check.sh on .env.jetson, then on the Jetson's .env
# (the script streamed to `bash -s` over ssh: the file never leaves the Jetson, and no value is
# ever printed), before anything is synced, built or restarted. With --env the remote file is
# about to be replaced by .env.jetson, so only .env.jetson counts.
check_env_files() {
  if [[ -f "$LOCAL_ENV" ]]; then
    bash "$ROOT/ops/env_check.sh" "$LOCAL_ENV" .env.jetson ||
      die ".env.jetson has invalid lines (above): fix them before deploying"
    info ".env.jetson: syntax ok"
  fi
  remote "test -f $RDIR_Q/.env" || return 0
  if (($1 == 1)); then
    info "$HOST:$RDIR/.env: replaced by .env.jetson (--env), not checked"
    return 0
  fi
  remote "bash -s -- $RDIR_Q/.env $(printf '%q' "$HOST:$RDIR/.env")" <"$ROOT/ops/env_check.sh" ||
    die "$HOST:$RDIR/.env has invalid lines (above): fix them there (./deploy.sh ssh) or replace the file with ./deploy.sh --env"
  info "$HOST:$RDIR/.env: syntax ok"
}

# A TS_AUTHKEY line with a value, quoted or not (an empty TS_AUTHKEY= is fine).
AUTHKEY_RE="^[[:space:]]*TS_AUTHKEY=[\"']?[^[:space:]#\"']"

# warn_leftover_authkey: once the node is logged in (the caller has its URL), a TS_AUTHKEY still
# in .env.jetson or in the Jetson's .env is a live secret for nothing: the login lives in the
# tsstate volume, and the api and the worker load the whole .env (env_file), so they hold the key.
# Warned, never removed here: the files are Harald's.
warn_leftover_authkey() {
  local where=()
  if [[ -f "$LOCAL_ENV" ]] && grep -Eq "$AUTHKEY_RE" "$LOCAL_ENV"; then where+=(.env.jetson); fi
  # grep on the Jetson: the file's contents never cross the ssh connection.
  if remote "grep -Eq $(printf '%q' "$AUTHKEY_RE") $RDIR_Q/.env 2>/dev/null"; then where+=("$HOST:$RDIR/.env"); fi
  ((${#where[@]} > 0)) || return 0
  warn "TS_AUTHKEY is still set in ${where[*]} although tailscale is logged in: delete that line" \
    "(the login is kept in the tsstate volume; the api and the worker read every key of .env)"
}

generate_env() {
  local pg_password secret credentials_key
  # Only ever a new file: an existing one holds keys that data already depends on.
  [[ ! -e "$LOCAL_ENV" ]] || die "$LOCAL_ENV exists; generate_env never overwrites it"
  pg_password="$(openssl rand -hex 32)"
  secret="$(openssl rand -hex 48)"
  credentials_key="$(openssl rand -base64 32)"
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

# Encryption key of the users' model credentials (agents A1): 32 random bytes, base64. Keep it:
# a lost or changed key makes every stored credential unreadable (rotation: .env.example).
CREDENTIALS_KEY=$credentials_key

# Loopback only: reached with ssh or through Tailscale Funnel, never from the LAN.
HTTP_BIND=127.0.0.1
HTTP_PORT=${JETSON_HTTP_PORT:-80}

# Tailscale Funnel (service tailscale): the node name, so the public URL is
# https://<TS_HOSTNAME>.<tailnet>.ts.net. For the first login only, add TS_AUTHKEY=<one-time key
# from the admin console>; remove it once ./deploy.sh url answers (the login is kept in tsstate).
TS_HOSTNAME=jobapp
EOF
  )
}

ensure_local_env() {
  if [[ -f "$LOCAL_ENV" ]]; then
    warn_missing_credentials_key
    return 0
  fi
  if remote "test -f $RDIR_Q/.env"; then
    # The Jetson already has its secrets: keep a local copy rather than inventing new ones.
    (
      umask 077
      remote "cat $RDIR_Q/.env" >"$LOCAL_ENV"
    )
    info "copied the existing $HOST:$RDIR/.env to .env.jetson"
    warn_missing_credentials_key
  else
    generate_env
    info "generated .env.jetson with a random POSTGRES_PASSWORD, SECRET_KEY and CREDENTIALS_KEY"
  fi
}

# An existing .env is never rewritten; say so when it predates CREDENTIALS_KEY, which the api and
# the worker require in production.
warn_missing_credentials_key() {
  [[ -n "$(envval "$LOCAL_ENV" CREDENTIALS_KEY)" ]] && return 0
  warn ".env.jetson has no CREDENTIALS_KEY: the api and worker refuse to start in production." \
    "Add one line, CREDENTIALS_KEY=\$(openssl rand -base64 32), to .env.jetson and to the Jetson's .env"
}

push_env() { # push_env <force 0|1>
  step "environment"
  ensure_local_env
  # A .env.jetson copied from the Jetson just now was not there for the preflight check.
  bash "$ROOT/ops/env_check.sh" "$LOCAL_ENV" .env.jetson || die ".env.jetson has invalid lines (above)"
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
  for svc in api worker caddy renderer; do
    start=$SECONDS
    rcompose --progress=quiet build "$svc" || die "build of $svc failed"
    record "build $svc" "$start"
  done
  # The browser too (behind the `browser` profile); browser_egress decides whether it starts. A
  # failed build does not stop the deploy (owner decision of 2026-10-14): everything else deploys
  # without the browser, which stays down until a deploy builds its image again.
  start=$SECONDS
  if rcompose --profile browser --progress=quiet build browser; then
    record "build browser" "$start"
  else
    BROWSER_BUILD_FAILED=1
    record "build browser FAILED" "$start"
    warn "build of browser failed: everything else deploys, the browser is not deployed"
  fi
}

# browser_egress (browser B6): the browser starts only behind the host firewall of its egress
# network. ops/browser-egress.sh creates its networks, then applies and checks the rules through
# the root owned copy the owner installed once (sudo bash ops/browser-egress.sh install);
# otherwise it removes the browser container and the rest of the stack deploys without it. The same
# when the browser image did not build (BROWSER_BUILD_FAILED): `skip` removes the previous container.
browser_egress() {
  step "browser behind its host firewall (ops/browser-egress.sh)"
  if [[ -n "${BROWSER_BUILD_FAILED:-}" ]]; then
    # The previous browser container is removed (not_started, same path as a failed firewall
    # check), so no browser runs without an image that was built and checked in this deploy.
    remote "cd $RDIR_Q && COMPOSE=$(printf '%q' "$RCOMPOSE") bash ops/browser-egress.sh skip 'the browser image did not build'" || true
    TIMES+=("browser: NOT deployed, its image did not build; fix the build and deploy again")
    warn "the browser is not deployed (its image did not build); everything else deploys"
  elif remote "cd $RDIR_Q && COMPOSE=$(printf '%q' "$RCOMPOSE") bash ops/browser-egress.sh deploy"; then
    RCOMPOSE="$RCOMPOSE --profile browser"
  else
    warn "the browser is not started (above); everything else deploys"
  fi
}

# migrate: ops/migrate.sh of the tree just synced, on the Jetson, BEFORE start_stack (issue #81).
# `alembic upgrade head` runs in a one off container of the api image just built, against the live
# database, while the containers of the previous release keep serving: new code never queries a
# schema that lacks its columns. That needs migrations the previous release can live with (expand
# first, contract later: CONTRIBUTING.md, Migrations). A failure stops the deploy here, with the
# schema as it was (one transaction) and nothing restarted. Never a downgrade.
migrate() {
  step "migrations (one off container of the new api image, before the new code starts)"
  local start=$SECONDS timeout_s
  timeout_s="$(setting MIGRATE_TIMEOUT_S 300)"
  [[ "$timeout_s" =~ ^[1-9][0-9]*$ ]] || die "MIGRATE_TIMEOUT_S must be a whole number of seconds, got '$timeout_s'"
  if ! remote "cd $RDIR_Q && MIGRATE_TIMEOUT_S=$timeout_s bash ops/migrate.sh"; then
    die "the migrations did not apply (above), so the deploy stopped before starting the new code: the containers on $HOST were not touched and still run the previous release (./deploy.sh ps). $HOST:$RDIR and the :latest images already hold the new release, so do not run compose up there by hand. Fix the migration and run ./deploy.sh again"
  fi
  record migrations "$start"
}

start_stack() {
  step "compose up"
  local start=$SECONDS
  if ! rcompose up -d --remove-orphans --wait --wait-timeout 180; then
    rcompose ps -a || true
    die "the stack did not become healthy; see ./deploy.sh logs <service>. The migrations of this release are applied and stay applied (never downgraded automatically): the previous release runs on that schema by the expand rule of CONTRIBUTING.md"
  fi
  record "up --wait" "$start"
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

# --- public URL (tailscale: Tailscale Funnel) ----------------------------------------------
# ts_field <name>: the first "<name>": "<string>" of `tailscale status --json --peers=false` on
# stdin. Without peers, Self comes first and is the only node, so DNSName and HostName are this
# node's; BackendState and AuthURL are top-level fields printed before Self.
ts_field() {
  sed -n "s/^[[:space:]]*\"$1\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1 || true
}

# public_url: print https://<node>.<tailnet>.ts.net, the stable Funnel URL, on stdout. It comes
# from the running tailscale container (Self.DNSName), waiting up to URL_TIMEOUT_S for it to
# connect after a (re)start. A container that is missing, stopped or not logged in is an exit 1
# with the reason on stderr, stdout empty.
public_url() {
  local deadline=$((SECONDS + URL_TIMEOUT_S)) id state json backend="" dns="" waited=0
  while :; do
    id="$(rcompose ps -a -q tailscale 2>/dev/null | head -n 1 || true)"
    [[ -n "$id" ]] || die "tailscale is not running on $HOST: no container (deploy with ./deploy.sh, then ./deploy.sh ps)"
    state="$(remote "docker inspect -f '{{.State.Status}}' $(printf '%q' "$id")" || true)"
    if [[ "$state" == "running" ]]; then
      json="$(remote "docker exec $(printf '%q' "$id") tailscale status --json --peers=false" 2>/dev/null || true)"
      backend="$(ts_field BackendState <<<"$json")"
      dns="$(ts_field DNSName <<<"$json")"
      dns="${dns%.}"
      case "$backend" in
        Running)
          [[ "$dns" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?\.[a-z0-9-]+\.ts\.net$ ]] && break
          die "tailscale on $HOST is connected but reports no ts.net name ('${dns:-none}'): is MagicDNS on in the tailnet?"
          ;;
        NeedsLogin)
          local auth
          auth="$(ts_field AuthURL <<<"$json")"
          die "tailscale on $HOST is not logged in (NeedsLogin): put a fresh one-time TS_AUTHKEY in $RDIR/.env there and run ./deploy.sh${auth:+, or log in once at $auth}"
          ;;
        NeedsMachineAuth)
          die "tailscale on $HOST waits for the device to be approved in the Tailscale admin console (Machines)"
          ;;
      esac
    elif [[ "$state" != "restarting" && "$state" != "created" ]]; then
      die "tailscale is ${state:-gone} on $HOST, not running (./deploy.sh logs tailscale; start it with ./deploy.sh)"
    fi
    if ((SECONDS >= deadline)); then
      die "tailscale ($state, ${backend:-no status yet}) did not connect within ${URL_TIMEOUT_S}s; see ./deploy.sh logs tailscale"
    fi
    if ((waited == 0)); then
      note "waiting up to ${URL_TIMEOUT_S}s for tailscale to connect"
      waited=1
    fi
    sleep 2
  done
  # A node of the same name already in the tailnet makes this one jobapp-1: the URL moved.
  local host
  host="$(ts_field HostName <<<"$json")"
  if [[ -n "$host" && "${dns%%.*}" != "$host" ]]; then
    warn "the node is ${dns%%.*}, not $host: another device holds that name; remove it in the admin console (Machines) and rename this one, or OAuth redirect URIs will not match"
  fi
  printf 'https://%s\n' "$dns"
}

# --- public checks, from this machine through Tailscale Funnel ------------------------------
PIN=() # --resolve host:443:ip, set when the system resolver still caches a negative answer
pcurl() { # pcurl <url> [curl args]: status code on stdout, headers in $PDIR/h, body in $PDIR/b
  local url="$1"
  shift
  curl -s --max-time 30 ${PIN[@]+"${PIN[@]}"} -D "$PDIR/h" -o "$PDIR/b" -w '%{http_code}' "$@" "$url" || true
}
pbrief() { # one line about the last body: an HTML page's <title> (error pages), else its start
  local title
  title="$(tr -d '\r\n' <"$PDIR/b" | sed -n 's/.*<title>\([^<]*\)<\/title>.*/\1/p' | head -c 120)"
  if [[ -n "$title" ]]; then printf 'page "%s"' "$title"; else tr -d '\r\n' <"$PDIR/b" | head -c 120; fi
}
phdr() { tr -d '\r' <"$PDIR/h" | grep -i "^$1:" | tail -n 1 | cut -d' ' -f2-; }

# wait_public <url>: until caddy's /healthz answers through Funnel. Right after a first login the
# ts.net name takes a few seconds to exist in public DNS and the first request waits for the
# node's certificate; back off 2, 4, 8, 15, 15... seconds, a handful of requests, not a stream.
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

# public_ipv4: this machine's public IPv4 as an outside service sees it (over HTTPS, the path the
# smoke's own requests take; DNS to OpenDNS as a fallback), or nothing when neither answers.
public_ipv4() {
  local ip
  ip="$(curl -s -4 --max-time 8 https://api.ipify.org 2>/dev/null || true)"
  if [[ ! "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && command -v dig >/dev/null; then
    ip="$(dig -4 +short +time=3 +tries=1 myip.opendns.com A @resolver1.opendns.com 2>/dev/null | tail -n 1 || true)"
  fi
  [[ ! "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || printf '%s' "$ip"
}

# is_public_ip <ip>: a globally routable address (not private, loopback, the tailnet's
# 100.64.0.0/10, documentation or link-local ranges).
is_public_ip() {
  python3 -c 'import ipaddress, sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]).is_global else 1)' "$1" 2>/dev/null
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
  preflight "$force_env"
  remote_lock
  local start=$SECONDS
  sync_tree
  record rsync "$start"
  push_env "$force_env"
  check_port
  build_images
  migrate
  browser_egress
  start_stack
  health
  step "public URL"
  local url
  url="$(public_url)"
  warn_leftover_authkey
  PDIR="$(mktemp -d)"
  if wait_public "$url"; then
    info "$url answers through Tailscale Funnel"
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

check_email() { # check_email <command label> <email>
  # Checked here so a typo never reaches the Jetson; the CLI then validates the address itself.
  # A leading dash would be read as an option by the CLI, and whitespace or control characters
  # are never part of an address.
  local label="$1" email="$2"
  [[ "$email" == *@* && "$email" != -* && ${#email} -le 320 ]] ||
    die "$label needs one email address, got '$email'"
  [[ ! "$email" =~ [[:space:][:cntrl:]] ]] || die "$label: the email contains whitespace or control characters"
}

cmd_admin() {
  if [[ "${1:-}" == "unlock" ]]; then
    shift
    cmd_admin_unlock "$@"
    return
  fi
  [[ $# -eq 2 && "$1" == "create" ]] || die "usage: ./deploy.sh admin create <email>, or admin unlock <email> [--ip <ip>]"
  local email="$2"
  check_email "admin create" "$email"
  ssh_master
  require_remote
  require_deployed
  local url
  url="$(public_url)" # dies with the reason when tailscale has no URL
  note "public URL: $url"
  # The link (and its token) goes to stdout only, never to a log.
  rcompose exec -T api python -m app.identity.cli create-admin "$email" --base-url "$url"
}

# admin unlock: delete the failed sign in attempts of one email (and of one address with --ip)
# still inside the 15 minute throttle window, and print how many rows went. The values reach
# psql as variables (:'email', :'ip'), never spliced into the SQL; Postgres parses the address.
UNLOCK_SQL="with gone as (delete from login_attempts where not success
  and at > now() - interval '15 minutes'
  and (email = :'email' or (:'ip' <> '' and ip = nullif(:'ip', '')::inet)) returning 1)
select count(*) from gone;"

cmd_admin_unlock() {
  local usage="usage: ./deploy.sh admin unlock <email> [--ip <ip>]" email="" ip=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ip)
        [[ $# -ge 2 && -n "$2" ]] || die "$usage"
        ip="$2"
        shift 2
        ;;
      -*) die "unknown option for admin unlock: $1 ($usage)" ;;
      *)
        [[ -z "$email" ]] || die "$usage"
        email="$1"
        shift
        ;;
    esac
  done
  [[ "$email" == *@* && ${#email} -le 320 ]] || die "admin unlock needs one email address, got '$email'"
  [[ ! "$email" =~ [[:space:][:cntrl:]] ]] || die "admin unlock: the email contains whitespace or control characters"
  [[ -z "$ip" || ("$ip" =~ ^[0-9A-Fa-f:.]+$ && ${#ip} -le 45) ]] || die "admin unlock: '$ip' is not an IP address"
  ssh_master
  require_remote
  require_deployed
  local removed
  # shellcheck disable=SC2016 # $POSTGRES_* and $1, $2 expand in the db container
  removed="$(rcompose exec -T db sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v email="$1" -v ip="$2"' \
    sh "$email" "$ip" <<<"$UNLOCK_SQL" | tr -d '\r')" || die "admin unlock: psql refused it, nothing deleted"
  [[ "$removed" =~ ^[0-9]+$ ]] || die "admin unlock: unexpected answer from psql: $removed"
  info "removed $removed failed sign in attempt(s) of $email${ip:+ and of $ip} in the last 15 minutes"
}

# import_into <module> <email> <source dir>: convert on the Mac with python -m app.<module>.cli
# convert into a private temporary file, then stream it to python -m app.<module>.cli import in
# the api container on the Jetson. The CLI prints the summary; its exit code is passed through.
import_into() {
  local module="$1" email="$2" source="$3" document
  [[ -d "$source" ]] || die "$module import: $source is not a folder (set JOBAPP_SOURCE_DIR to the job folder)"
  command -v uv >/dev/null || die "$module import: uv is needed on this Mac to convert the sources"
  IMPORT_DIR="$(mktemp -d /tmp/jobapp-import.XXXXXX)"
  chmod 700 "$IMPORT_DIR"
  document="$IMPORT_DIR/$module.json"
  step "convert $source"
  # The document goes to a file, never to stdout: a failed conversion sends nothing.
  uv run --native-tls --directory "$ROOT/api" python -m "app.$module.cli" convert \
    --source-dir "$source" --out "$document" || die "$module import: conversion failed, nothing was sent"
  ssh_master
  require_remote
  require_deployed
  step "import into $HOST"
  rcompose exec -T api python -m "app.$module.cli" import --email "$email" --file - <"$document"
}

job_dir() { # the job folder holding profile/, engine/ and data/
  local dir
  dir="$(setting JOBAPP_SOURCE_DIR "$ROOT/..")"
  (cd "$dir" 2>/dev/null && pwd) || printf '%s' "$dir"
}

cmd_profile() {
  [[ $# -eq 2 && "$1" == "import" ]] || die "usage: ./deploy.sh profile import <email>"
  check_email "profile import" "$2"
  import_into profile "$2" "$(job_dir)"
}

cmd_jobs() {
  if [[ "${1:-}" == "mark-overrides" ]]; then
    jobs_mark_overrides "${@:2}"
    return
  fi
  [[ $# -eq 2 && "$1" == "import" ]] ||
    die "usage: ./deploy.sh jobs import <email> | ./deploy.sh jobs mark-overrides <email> [--apply]"
  check_email "jobs import" "$2"
  import_into jobs "$2" "$(job_dir)/data"
}

# jobs_mark_overrides <email> [--apply]: app.jobs.cli mark-overrides in the api container on the
# Jetson; a dry run unless --apply.
jobs_mark_overrides() {
  [[ $# -eq 1 || ($# -eq 2 && "$2" == "--apply") ]] ||
    die "usage: ./deploy.sh jobs mark-overrides <email> [--apply]"
  check_email "jobs mark-overrides" "$1"
  local mode=" (dry run)"
  [[ $# -eq 2 ]] && mode=""
  ssh_master
  require_remote
  require_deployed
  step "mark-overrides on $HOST$mode"
  rcompose exec -T api python -m app.jobs.cli mark-overrides --email "$1" "${@:2}"
}

# cmd_apply pause|resume|status: the administrator's pause of every submission (apply A6), set
# through app.apply.admin in the api container on the Jetson.
cmd_apply() {
  [[ $# -eq 1 && ("$1" == "pause" || "$1" == "resume" || "$1" == "status") ]] ||
    die "usage: ./deploy.sh apply pause|resume|status"
  ssh_master
  require_remote
  require_deployed
  step "apply $1 on $HOST"
  rcompose exec -T api python -m app.apply.admin "$1"
}

cmd_url() {
  [[ $# -eq 0 ]] || die "usage: ./deploy.sh url"
  ssh_master
  require_remote
  require_deployed
  public_url
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

  step "public, through Tailscale Funnel"
  local url
  if ! url="$(public_url)"; then
    printf '\nSMOKE FAILED: no public URL\n' >&2
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
    flunk "$url/healthz never answered within ${PUBLIC_TIMEOUT_S}s (DNS, Funnel not allowed for the node, or tailscale not connected: ./deploy.sh logs tailscale)"
    printf '\nSMOKE FAILED: %d check(s)\n' "$failed" >&2
    exit 1
  fi
  pass "caddy answers through Funnel"

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

  # The visitor's address must reach the api, and no header a visitor sends may replace it. One
  # tagged request carries every client address header there is, forged; the api must log this
  # machine's public IPv4 (as an echo service sees it) and none of the forged values. Funnel sets
  # X-Forwarded-For itself, replacing the visitor's, and caddy's :8080 trusts no other header.
  # IPv4 for both, so the two requests cannot leave by different address families.
  local rid mine logged forged="203.0.113.9 203.0.113.10 203.0.113.11 203.0.113.12 203.0.113.13"
  rid="smoke-$(date +%s)-$RANDOM"
  mine="$(public_ipv4)"
  code="$(pcurl "$url/api/health" -4 -H "X-Request-ID: $rid" -H 'X-Forwarded-For: 203.0.113.9' \
    -H 'Cf-Connecting-Ip: 203.0.113.10' -H 'X-Real-Ip: 203.0.113.11' -H 'True-Client-Ip: 203.0.113.12' \
    -H 'Forwarded: for=203.0.113.13' -H 'Tailscale-User-Login: forged@example.org')"
  logged=""
  local i
  for i in 1 2 3 4 5; do
    logged="$(rcompose logs --no-log-prefix --since 5m api 2>/dev/null | grep -F "\"request_id\": \"$rid\"" |
      sed -n 's/.*"client": "\([^"]*\)".*/\1/p' | tail -n 1 || true)"
    [[ -n "$logged" ]] && break
    sleep "$i"
  done
  logged="${logged#::ffff:}"
  if [[ -z "$logged" ]]; then
    flunk "the api logged no request $rid (HTTP $code)"
  elif [[ " $forged " == *" $logged "* ]]; then
    flunk "the api logged the forged address $logged: a visitor can spoof its IP through Funnel"
  elif ! is_public_ip "$logged"; then
    flunk "the api logged '$logged', not a public address (reached over the tailnet or a private path, not through Funnel?)"
  elif [[ -n "$mine" && "$logged" != "$mine" ]]; then
    flunk "the api logged client $logged for $rid, but this machine's public IPv4 is $mine"
  elif [[ -n "$mine" ]]; then
    pass "the api logs the visitor's address $logged (Funnel's X-Forwarded-For); forged X-Forwarded-For, Cf-Connecting-Ip, X-Real-Ip, True-Client-Ip and Forwarded ignored"
  else
    pass "the api logs the public address $logged and none of the forged ones (this machine's own IPv4 could not be looked up to compare)"
  fi

  # A 5 MB body, half the caddy limit, crosses Funnel and caddy and is answered by the api, not
  # by caddy's 413 or a proxy error. The api's answer is its 401: a body with no session is
  # refused before it is read on every route that is not public (api/app/body_limit.py, issue
  # #100; it was the 405 of a POST on a GET route before). It carries the CSRF header every
  # non-GET api request needs (identity), else the api would answer 403 first.
  # The status and the sentence expected here are read by api/tests/identity/
  # test_body_limit_api.py, which sends the same request to the app: change both together.
  code="$(head -c 5000000 /dev/zero | pcurl "$url/api/health" -X POST --data-binary @- -H 'Content-Type: application/octet-stream' -H 'X-Requested-With: jobapp')"
  if [[ "$code" == "401" ]] && grep -q 'Sign in to continue' "$PDIR/b"; then
    pass "5 MB upload reaches the api through Funnel (the api's 401: no session, no body)"
  else
    flunk "5 MB upload answered $code: $(pbrief)"
  fi

  # Identity through Funnel: no session is a 401, a state-changing call without the CSRF
  # header a 403.
  # Protected routes of every kind (the user, the admin area, a user's own data, and a write
  # carrying the CSRF header) answer 401 without a session, a forged cookie included.
  local protected
  for protected in "GET /api/auth/me" "GET /api/admin/users" "GET /api/admin/invites" "GET /api/profile" \
    "PATCH /api/auth/me"; do
    local body=()
    [[ "$protected" == GET* ]] || body=(-H 'Content-Type: application/json' --data-binary '{"name": "x"}')
    code="$(pcurl "$url${protected#* }" -X "${protected%% *}" -H 'X-Requested-With: jobapp' \
      -H 'Cookie: jobapp_session=forged' ${body[@]+"${body[@]}"})"
    if [[ "$code" == "401" ]] && grep -q '"detail"' "$PDIR/b"; then
      pass "$protected without a session: 401"
    else
      flunk "$protected without a session answered $code: $(pbrief)"
    fi
  done
  code="$(pcurl "$url/api/auth/logout" -X POST)"
  if [[ "$code" == "403" ]] && grep -q 'X-Requested-With' "$PDIR/b"; then
    pass "a POST without X-Requested-With is refused (403)"
  else
    flunk "a POST without X-Requested-With answered $code: $(pbrief)"
  fi

  if ((failed > 0)); then
    printf '\nSMOKE FAILED: %d check(s), see FAIL above\n' "$failed" >&2
    exit 1
  fi
  printf '\nSmoke passed on %s and through %s\n' "$HOST" "$url"
}

# --- backups and restores -----------------------------------------------------------------
# ops/db.sh does the work on the Jetson; this side parses options, asks the confirmation, moves
# files between the two machines and runs the health check after a live restore.
BDIR="$(backup_dir)"
# Empty unless set on this Mac (environment or .env): ops/db.sh then decides on the Jetson, from
# the BACKUP_KEEP of the .env there, then its default of 7 (ops/db.sh, Retention).
BACKUP_KEEP="$(setting BACKUP_KEEP '')"
BACKUP_CRON="$(setting BACKUP_CRON '17 3 * * *')"
LOCAL_BACKUPS="$ROOT/backups"
CRON_MARK="# jobapp-backup (./deploy.sh backup-schedule)"
CONFIRM_PHRASE="RESTORE jobapp"

check_backup_settings() {
  [[ -z "$BACKUP_KEEP" || "$BACKUP_KEEP" =~ ^[1-9][0-9]{0,3}$ ]] ||
    die "BACKUP_KEEP must be a whole number from 1 to 9999, got '$BACKUP_KEEP'"
  [[ -n "$BDIR" && "$BDIR" != "~" && "$BDIR" != "/" && "$BDIR" != "." ]] || die "BACKUP_DIR must name a directory"
  [[ "$BDIR" =~ ^[A-Za-z0-9._/-]+$ ]] || die "BACKUP_DIR may only hold letters, digits, . _ / and -: '$BDIR'"
  case "$BDIR/" in
    "$RDIR/"*) die "BACKUP_DIR ($BDIR) must not be inside DEPLOY_DIR ($RDIR): rsync --delete would erase it" ;;
  esac
}

rdb() { # rdb <args...>: ops/db.sh of this tree, run by bash on the Jetson
  local args env
  args="$(printf ' %q' "$@")"
  env="JOBAPP_DIR=$RDIR_Q BACKUP_DIR=$(printf '%q' "$BDIR")"
  [[ -z "$BACKUP_KEEP" ]] || env+=" BACKUP_KEEP=$(printf '%q' "$BACKUP_KEEP")"
  [[ -z "${BACKUP_MIN_FREE_MB:-}" ]] || env+=" BACKUP_MIN_FREE_MB=$(printf '%q' "$BACKUP_MIN_FREE_MB")"
  # From the shell of this run alone, never from a file: it accepts one smaller dump (ops/db.sh).
  [[ -z "${BACKUP_ACCEPT_SHRINK:-}" ]] || env+=" BACKUP_ACCEPT_SHRINK=$(printf '%q' "$BACKUP_ACCEPT_SHRINK")"
  remote "$env bash -s --$args" <"$ROOT/ops/db.sh"
}

backup_remote() { # the usual checks before any backup subcommand
  check_backup_settings
  ssh_master
  require_remote
  require_deployed
}

# pull_backup <remote file>: copy to backups/ (dir 700, file 600), compare sha256 on both sides.
pull_backup() {
  local rfile="$1" name lfile rsum lsum
  name="$(basename "$rfile")"
  lfile="$LOCAL_BACKUPS/$name"
  step "pull to $lfile"
  mkdir -p "$LOCAL_BACKUPS"
  chmod 700 "$LOCAL_BACKUPS"
  (
    umask 077
    remote "cat $(printf '%q' "$rfile")" >"$lfile.partial"
  ) || die "copy of $rfile failed"
  rsum="$(remote "sha256sum $(printf '%q' "$rfile")" | cut -d' ' -f1)"
  lsum="$(shasum -a 256 "$lfile.partial" | cut -d' ' -f1)"
  if [[ -z "$rsum" || "$rsum" != "$lsum" ]]; then
    rm -f "$lfile.partial"
    die "checksum mismatch pulling $rfile (remote ${rsum:-none}, local $lsum)"
  fi
  chmod 600 "$lfile.partial"
  mv "$lfile.partial" "$lfile"
  info "sha256 $lsum on both sides"
  info "$lfile ($(wc -c <"$lfile" | tr -d ' ') bytes, mode 600, git-ignored and never rsynced)"
}

cmd_backup() {
  local pull=0 arg
  for arg in "$@"; do
    case "$arg" in
      --pull) pull=1 ;;
      *) die "unknown option for backup: $arg (usage: ./deploy.sh backup [--pull])" ;;
    esac
  done
  backup_remote
  step "backup on $HOST"
  local out rfile
  out="$(mktemp)"
  if ! rdb backup | tee "$out"; then
    rm -f "$out"
    die "backup failed, see above"
  fi
  rfile="$(sed -n 's/^backup=//p' "$out" | tail -n 1)"
  rm -f "$out"
  [[ -n "$rfile" ]] || die "ops/db.sh reported no backup file"
  ((pull == 0)) || pull_backup "$rfile"
}

cmd_backups() {
  [[ $# -eq 0 ]] || die "usage: ./deploy.sh backups"
  backup_remote
  rdb list
}

read_port() { # HTTP_PORT of the Jetson's .env, for health
  HTTP_PORT="$(remote "sed -n 's/^HTTP_PORT=//p' $RDIR_Q/.env | tail -n 1")"
  HTTP_PORT="${HTTP_PORT:-80}"
}

# upload_backup <local file>: to <backup dir>/incoming/ on the Jetson, mode 600; prints its path
upload_backup() {
  local lfile="$1" rdir rfile lsum rsum
  rdir="$BDIR/incoming"
  rfile="$rdir/$(basename "$lfile")"
  [[ "$(basename "$lfile")" =~ ^[A-Za-z0-9._-]+$ ]] || die "rename $lfile to letters, digits, . _ and - before restoring it"
  note "uploading $lfile to $HOST:$rfile"
  remote "umask 077 && mkdir -p $(printf '%q' "$rdir") && chmod 700 $(printf '%q' "$BDIR") $(printf '%q' "$rdir") &&
    cat > $(printf '%q' "$rfile.partial") && mv $(printf '%q' "$rfile.partial") $(printf '%q' "$rfile")" <"$lfile" ||
    die "upload of $lfile failed"
  lsum="$(shasum -a 256 "$lfile" | cut -d' ' -f1)"
  rsum="$(remote "sha256sum $(printf '%q' "$rfile")" | cut -d' ' -f1)"
  [[ "$lsum" == "$rsum" ]] || die "checksum mismatch after uploading $lfile"
  printf '%s' "$rfile"
}

cmd_restore() {
  local file="" live=0 yes=0 keep=0 drop=0 arg
  for arg in "$@"; do
    case "$arg" in
      --into-live) live=1 ;;
      --yes) yes=1 ;;
      --keep) keep=1 ;;
      --drop-scratch) drop=1 ;;
      -*) die "unknown option for restore: $arg" ;;
      *)
        [[ -z "$file" ]] || die "restore takes one file, got '$file' and '$arg'"
        file="$arg"
        ;;
    esac
  done
  if ((drop)); then
    [[ -z "$file" && $live -eq 0 && $keep -eq 0 ]] || die "--drop-scratch takes no file and no other option"
    backup_remote
    rdb drop-scratch
    return 0
  fi
  [[ -n "$file" ]] || die "usage: ./deploy.sh restore <file> [--keep] | <file> --into-live [--yes] | --drop-scratch"
  ((live == 0 || keep == 0)) || die "--keep is for the scratch restore, not with --into-live"
  ((yes == 0 || live == 1)) || die "--yes only confirms --into-live"
  if ((live == 1 && yes == 0)) && [[ ! -t 0 ]]; then
    die "--into-live replaces the live database: run it in a terminal to type the confirmation, or pass --yes"
  fi
  backup_remote

  # A file on this machine is uploaded first; anything else names a file on the Jetson.
  local lpath="$file"
  [[ "$lpath" == /* ]] || lpath="$CALLER_PWD/$lpath"
  if [[ -f "$lpath" ]]; then
    file="$(upload_backup "$lpath")"
  elif [[ "$file" == */* && -e "$lpath" ]]; then
    die "$file is not a regular file"
  fi

  if ((live == 0)); then
    step "restore $file into the scratch database on $HOST"
    local args=(restore "$file")
    ((keep == 0)) || args+=(--keep)
    rdb "${args[@]}" || die "restore check failed, see above"
    return 0
  fi

  step "verify $file on $HOST"
  local out
  out="$(mktemp)"
  if ! rdb verify "$file" | tee "$out"; then
    rm -f "$out"
    die "refused, nothing changed"
  fi
  # From here on the resolved path, so `latest` cannot change under a backup made meanwhile.
  file="$(sed -n 's/^file=//p' "$out" | tail -n 1)"
  rm -f "$out"
  [[ -n "$file" ]] || die "ops/db.sh did not name the verified file, nothing changed"
  if ((yes == 0)); then
    printf '\n   This replaces the live database on %s with %s.\n' "$HOST" "$file" >&2
    printf '   A pre-restore backup is taken first. Type %s to go on: ' "$CONFIRM_PHRASE" >&2
    local answer=""
    read -r answer || true
    [[ "$answer" == "$CONFIRM_PHRASE" ]] || die "confirmation not given, nothing changed"
  fi
  step "restore $file into the live database on $HOST"
  rdb restore-live "$file" || die "live restore failed, see above (the api was started again)"
  read_port
  health
  info "run ./deploy.sh smoke for the full checks"
}

# cron_line: the crontab line, every path absolute through $HOME (cron's sh expands it). It names
# no BACKUP_KEEP unless one is set on this Mac: each run then reads the retention from the
# Jetson's .env, then from the default of the ops/db.sh deployed there, so changing the retention
# takes a deploy or an edit of that .env, not a reinstall. A BACKUP_KEEP set here is written into
# the line and wins over both until the line is installed again without it.
cron_line() {
  local logdir
  case "$BDIR" in
    /*) logdir="$BDIR" ;;
    *) logdir="\$HOME/$BDIR" ;;
  esac
  # shellcheck disable=SC2016 # $HOME is for cron's shell on the Jetson
  printf '%s JOBAPP_DIR=%s BACKUP_DIR=%s%s "$HOME/%s/ops/db.sh" backup >>"%s/backup.log" 2>&1 %s' \
    "$BACKUP_CRON" "$RDIR" "$BDIR" "${BACKUP_KEEP:+ BACKUP_KEEP=$BACKUP_KEEP}" "$RDIR" "$logdir" "$CRON_MARK"
}

# schedule_retention <installed cron line, or ''>: what the nightly run keeps. Asked of the
# ops/db.sh deployed on the Jetson, the copy cron runs (not the one of this tree), with the
# BACKUP_KEEP the line carries when it carries one.
schedule_retention() {
  local line="$1" pinned="" env out
  if [[ "$line" =~ (^|[[:space:]])BACKUP_KEEP=([^[:space:]]+) ]]; then
    pinned="${BASH_REMATCH[2]}"
  fi
  env="JOBAPP_DIR=$RDIR_Q BACKUP_DIR=$(printf '%q' "$BDIR")"
  if [[ -n "$pinned" ]]; then
    env+=" BACKUP_KEEP=$(printf '%q' "$pinned")"
    info "the line carries BACKUP_KEEP=$pinned, which wins over $RDIR/.env on $HOST and over the default of ops/db.sh until the line is installed again without it"
  elif [[ -n "$line" ]]; then
    info "the line carries no BACKUP_KEEP: each run reads it from $RDIR/.env on $HOST, then from the default of the ops/db.sh deployed there"
  fi
  local rc=0 dir
  out="$(remote "$env bash $RDIR_Q/ops/db.sh retention" 2>&1)" || rc=$?
  # 3: the report is whole, and a rotation is held or its mark cannot be read (ops/db.sh).
  if ((rc == 0 || rc == 3)); then
    printf '%s\n' "$out" | grep -v -e '^keep=' -e '^held=' || true
  else
    info "retention: not reported. The ops/db.sh deployed on $HOST is older than this command, or it failed:"
    printf '%s\n' "$out" | sed 's/^/     /'
    info "deploy, then run ./deploy.sh backup-schedule status again"
  fi
  SCHEDULE_HELD=""
  if ((rc == 3)); then
    SCHEDULE_HELD="a rotation is held on $HOST, or its mark cannot be read (above)"
    while IFS= read -r dir; do
      dir="${dir#held=}"
      # The one command that lifts it, when the database was meant to shrink.
      if [[ "${dir##*/}" == deploys ]]; then
        info "to lift the hold of $dir: BACKUP_DIR=$BDIR/deploys BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup"
      else
        info "to lift the hold of $dir: BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup"
      fi
    done < <(printf '%s\n' "$out" | grep '^held=' || true)
  fi
}

cmd_backup_schedule() {
  local action="${1:-}"
  [[ $# -eq 1 && "$action" =~ ^(install|remove|status)$ ]] ||
    die "usage: ./deploy.sh backup-schedule install|remove|status"
  [[ "$RDIR" =~ ^[A-Za-z0-9._/-]+$ ]] || die "DEPLOY_DIR may only hold letters, digits, . _ / and - for cron"
  [[ "$BACKUP_CRON" =~ ^[0-9*/,-]+(\ [0-9*/,-]+){4}$ ]] ||
    die "BACKUP_CRON must be five cron fields of digits, * / , and -, got '$BACKUP_CRON'"
  backup_remote
  local mark_q line_q line
  mark_q="$(printf '%q' "$CRON_MARK")"
  case "$action" in
    install)
      remote "test -x $RDIR_Q/ops/db.sh" ||
        die "$HOST:$RDIR/ops/db.sh is missing or not executable; deploy first with ./deploy.sh"
      line_q="$(printf '%q' "$(cron_line)")"
      remote "umask 077 && mkdir -p $(printf '%q' "$BDIR") && chmod 700 $(printf '%q' "$BDIR") &&
        { crontab -l 2>/dev/null | grep -vF $mark_q; printf '%s\n' $line_q; } | crontab -" ||
        die "crontab update failed"
      info "installed in the crontab of $(remote whoami)@$HOST (replacing any previous jobapp line):"
      line="$(remote "crontab -l | grep -F $mark_q")" || die "the line is not in the crontab of $HOST after installing it"
      printf '%s\n' "$line" | sed 's/^/   /'
      schedule_retention "$line"
      ;;
    remove)
      remote "crontab -l 2>/dev/null | grep -vF $mark_q | crontab -" || die "crontab update failed"
      info "no jobapp backup line left in the crontab of $HOST; other lines kept"
      ;;
    status)
      line="$(remote "crontab -l 2>/dev/null | grep -F $mark_q" || true)"
      if [[ -n "$line" ]]; then
        info "installed: $line"
      else
        info "not installed (./deploy.sh backup-schedule install); a backup run on $HOST would apply:"
      fi
      schedule_retention "$line"
      local logf="$BDIR/backup.log" # relative paths resolve in the remote home, where ssh starts
      # Everything above this point is the state now. The log is the past: its last lines are
      # those of the last run, which may have been made under another retention (a WARNING about
      # 14 from the night before a reinstall, for instance), so they are told apart.
      remote "test -f $(printf '%q' "$logf") && { echo '   last lines of $logf (the last run, as it was then; the state now is above):'; tail -n 5 $(printf '%q' "$logf") | sed 's/^/     | /'; } || true"
      # A hold must be seen (the review's N12): the status of this command says it.
      [[ -z "$SCHEDULE_HELD" ]] || die "$SCHEDULE_HELD: its backups delete nothing until it is lifted"
      ;;
  esac
}

# --- self-hosted GitHub Actions runner (ops/runner/) ----------------------------------------
# The official runner in a container on the Jetson, no sudo: it talks to the host Docker daemon
# through its socket and sees ~/jobapp and ~/jobapp-backups at the same paths, so the deploy
# workflow (.github/workflows/deploy.yml) runs docker compose there exactly as this script does.
# Registration and removal tokens come from `gh api` on this Mac, go to the container on stdin and
# are never written anywhere. Docker socket access makes the runner root-equivalent on the Jetson
# (README, Continuous delivery).
RUNNER_REPO="$(setting JOBAPP_RUNNER_REPO GoldLegendW80/jobapp)"
RUNNER_NAME="$(setting JOBAPP_RUNNER_NAME jetson)"
RUNNER_LABELS="jetson,arm64" # GitHub adds self-hosted, Linux and ARM64 itself
RUNNER_CONTAINER=jobapp-runner
RUNNER_VOLUME=jobapp-runner
RUNNER_MEM=768m

runner_version() { # the RUNNER_VERSION pinned in ops/runner/Dockerfile
  sed -n 's/^ARG RUNNER_VERSION=//p' "$ROOT/ops/runner/Dockerfile" | head -n 1
}

runner_checks() { # settings and local tools, before any network call
  [[ "$RUNNER_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "JOBAPP_RUNNER_REPO must be owner/name, got '$RUNNER_REPO'"
  [[ "$RUNNER_NAME" =~ ^[A-Za-z0-9_.-]+$ ]] || die "JOBAPP_RUNNER_NAME may only hold letters, digits, . _ and -: '$RUNNER_NAME'"
  [[ "$RDIR" =~ ^[A-Za-z0-9._-]+$ ]] || die "the runner needs DEPLOY_DIR to be a folder directly in the Jetson home, got '$RDIR'"
  [[ "$BDIR" =~ ^[A-Za-z0-9._-]+$ ]] || die "the runner needs BACKUP_DIR to be a folder directly in the Jetson home, got '$BDIR'"
  command -v gh >/dev/null || die "gh is needed on this Mac for the runner tokens"
  [[ -n "$(runner_version)" ]] || die "ops/runner/Dockerfile pins no RUNNER_VERSION"
}

runner_github() { # runner_github: "<id> <status> <busy> <labels>" of RUNNER_NAME on GitHub, or nothing
  gh api "repos/$RUNNER_REPO/actions/runners" --paginate \
    --jq ".runners[] | select(.name == \"$RUNNER_NAME\") | \"\(.id) \(.status) \(.busy) \([.labels[].name] | join(\",\"))\"" |
    head -n 1
}

# runner_opts: the docker run options shared by configure, run and remove, built from the Jetson
# user's facts (name, uid, gid, home, docker group); each value quoted for the remote shell.
runner_opts() {
  local facts user uid gid home dgid
  # shellcheck disable=SC2016 # expanded by the remote shell
  facts="$(remote 'id -un; id -u; id -g; printf "%s\n" "$HOME"; stat -c %g /var/run/docker.sock')"
  {
    read -r user
    read -r uid
    read -r gid
    read -r home
    read -r dgid
  } <<<"$facts"
  [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$dgid" =~ ^[0-9]+$ && "$home" == /* ]] ||
    die "could not read the Jetson user's ids and home"
  RUNNER_USER="$user" RUNNER_UID="$uid" RUNNER_GID="$gid"
  RUNNER_IMAGE="jobapp-runner:$(runner_version)"
  RUNNER_OPTS="--network host --group-add $dgid"
  RUNNER_OPTS+=" -v /var/run/docker.sock:/var/run/docker.sock"
  RUNNER_OPTS+=" -v /usr/bin/docker:/usr/bin/docker:ro"
  RUNNER_OPTS+=" -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro"
  RUNNER_OPTS+=" -v $RUNNER_VOLUME:$(printf '%q' "$home/actions-runner")"
  RUNNER_OPTS+=" -v $(printf '%q' "$home/$RDIR:$home/$RDIR")"
  RUNNER_OPTS+=" -v $(printf '%q' "$home/$BDIR:$home/$BDIR")"
  RUNNER_OPTS+=" -e JOBAPP_DIR=$(printf '%q' "$home/$RDIR") -e BACKUP_DIR=$(printf '%q' "$home/$BDIR")"
  RUNNER_OPTS+=" -e RUNNER_URL=$(printf '%q' "https://github.com/$RUNNER_REPO")"
  RUNNER_OPTS+=" -e RUNNER_NAME=$(printf '%q' "$RUNNER_NAME") -e RUNNER_LABELS=$RUNNER_LABELS"
}

runner_install() {
  step "runner image on $HOST"
  # The two files of ops/runner as a tar context on stdin: nothing copied to the Jetson's disk.
  # Plain ustar: macOS's bsdtar otherwise adds pax headers for extended attributes, which docker
  # build does not read as a tar and parses as a Dockerfile.
  local tar_opts=(--format ustar)
  tar --version 2>/dev/null | grep -q bsdtar && tar_opts+=(--no-xattrs --no-mac-metadata --no-acls)
  COPYFILE_DISABLE=1 tar "${tar_opts[@]}" -C "$ROOT/ops/runner" -cf - Dockerfile entrypoint.sh job-guard.sh |
    remote "docker build -q --build-arg RUNNER_USER=$(printf '%q' "$RUNNER_USER") --build-arg RUNNER_UID=$RUNNER_UID --build-arg RUNNER_GID=$RUNNER_GID -t $RUNNER_IMAGE -" >/dev/null ||
    die "docker build of $RUNNER_IMAGE failed on $HOST"
  info "$RUNNER_IMAGE built (arm64, ubuntu 22.04, actions runner $(runner_version))"
  remote "mkdir -p $RDIR_Q && umask 077 && mkdir -p $(printf '%q' "$BDIR")"

  step "registration as $RUNNER_NAME on $RUNNER_REPO"
  if remote "docker run --rm --entrypoint test $RUNNER_OPTS $RUNNER_IMAGE -f actions-runner/.runner" 2>/dev/null &&
    [[ -n "$(runner_github)" ]]; then
    info "already registered (volume $RUNNER_VOLUME kept); ./deploy.sh runner remove first to register again"
  else
    local token
    token="$(gh api -X POST "repos/$RUNNER_REPO/actions/runners/registration-token" --jq .token)" ||
      die "gh could not create a registration token (needs admin rights on $RUNNER_REPO)"
    remote "docker rm -f $RUNNER_CONTAINER >/dev/null 2>&1; docker run --rm -i $RUNNER_OPTS $RUNNER_IMAGE configure" <<<"$token" ||
      die "registration failed"
    token=""
    info "registered"
  fi

  step "container $RUNNER_CONTAINER (restart unless-stopped, $RUNNER_MEM)"
  remote "docker rm -f $RUNNER_CONTAINER >/dev/null 2>&1; docker run -d --name $RUNNER_CONTAINER --restart unless-stopped \
    --memory $RUNNER_MEM --log-opt max-size=10m --log-opt max-file=3 --label jobapp.role=runner \
    $RUNNER_OPTS $RUNNER_IMAGE run" >/dev/null || die "the runner container did not start"
  local deadline=$((SECONDS + 90)) state=""
  while ((SECONDS < deadline)); do
    state="$(runner_github)"
    [[ "$state" == *" online "* ]] && break
    sleep 3
  done
  [[ "$state" == *" online "* ]] || die "$RUNNER_NAME is not online on GitHub after 90s: ${state:-not listed}; see docker logs $RUNNER_CONTAINER on $HOST"
  info "online on GitHub: ${state#* }"
}

runner_status() {
  step "runner $RUNNER_NAME"
  local container github
  container="$(remote "docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}, restart {{.HostConfig.RestartPolicy.Name}}, image {{.Config.Image}}' $RUNNER_CONTAINER" 2>/dev/null || true)"
  info "container on $HOST: ${container:-none}"
  github="$(runner_github || true)"
  if [[ -n "$github" ]]; then
    local _id state busy labels
    read -r _id state busy labels <<<"$github"
    info "GitHub: $state, busy $busy, labels $labels"
  else
    info "GitHub: not registered on $RUNNER_REPO"
  fi
  [[ "$container" == running* && "$github" == *" online "* ]]
}

runner_remove() {
  step "unregister $RUNNER_NAME from $RUNNER_REPO"
  remote "docker rm -f $RUNNER_CONTAINER >/dev/null 2>&1 || true"
  local token removed=0
  if remote "docker image inspect $RUNNER_IMAGE >/dev/null 2>&1"; then
    token="$(gh api -X POST "repos/$RUNNER_REPO/actions/runners/remove-token" --jq .token)" ||
      die "gh could not create a removal token"
    remote "docker run --rm -i $RUNNER_OPTS $RUNNER_IMAGE remove" <<<"$token" && removed=1
    token=""
  fi
  if ((removed == 0)); then
    # No image or no local registration left: delete the GitHub side by id.
    local id
    id="$(runner_github | cut -d' ' -f1)"
    [[ -z "$id" ]] || gh api -X DELETE "repos/$RUNNER_REPO/actions/runners/$id" || die "could not delete runner $id on GitHub"
  fi
  remote "docker volume rm $RUNNER_VOLUME >/dev/null 2>&1 || true"
  [[ -z "$(runner_github)" ]] || die "$RUNNER_NAME is still listed on GitHub"
  info "container and volume $RUNNER_VOLUME removed, $RUNNER_NAME no longer listed; image $RUNNER_IMAGE kept"
}

cmd_runner() {
  local action="${1:-}"
  [[ $# -eq 1 && "$action" =~ ^(install|status|remove)$ ]] || die "usage: ./deploy.sh runner install|status|remove"
  runner_checks
  ssh_master
  require_remote
  runner_opts
  case "$action" in
    install) runner_install ;;
    status) runner_status ;;
    remove) runner_remove ;;
  esac
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
    backup) cmd_backup "$@" ;;
    backups) cmd_backups "$@" ;;
    restore) cmd_restore "$@" ;;
    backup-schedule) cmd_backup_schedule "$@" ;;
    admin) cmd_admin "$@" ;;
    apply) cmd_apply "$@" ;;
    profile) cmd_profile "$@" ;;
    jobs) cmd_jobs "$@" ;;
    runner) cmd_runner "$@" ;;
    -h | --help | help) usage ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
