#!/usr/bin/env bash
# Tests of the optional extra CA of the image builds (issue #359): JOBAPP_EXTRA_CA names a PEM
# bundle, compose.extra-ca.yml hands it to the builds as the BuildKit secret extra_ca.
#
#   tests/extra_ca_test.sh
#
# Covers: every Dockerfile step that downloads mounts the secret as optional, and nothing keeps it
# (no ENV, ARG, COPY or ADD of it, the joined bundle removed in the same step); the step images of
# ops/local-check.sh, cut out of the script and run with a fake docker on PATH, adds the override
# only when the variable is set, refuses a relative or missing path, never prints the bundle, and
# its stacks never get it; the builds of deploy.sh and of the deploy workflow, run with the variable
# set and the same fake, name compose.yml alone. With the Compose CLI (no daemon needed): the
# override gives every built service the secret, compose.yml alone gives none, and the override
# without the variable is refused. Every bundle here is invented: no certificate is read.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

passed=0
ok() {
  passed=$((passed + 1))
  printf 'ok   %s\n' "$*"
}
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

CANARY="extraca$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
printf -- '-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n' "$CANARY" >"$SCRATCH/bundle.pem"

# A fake docker that writes its arguments, one call per line.
FAKE="$SCRATCH/bin"
mkdir -p "$FAKE"
cat >"$FAKE/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$CALLS"
EOF
chmod +x "$FAKE/docker"
export CALLS="$SCRATCH/calls"

# --- the Dockerfiles ---------------------------------------------------------------------------
# instructions <Dockerfile>: one line per instruction, continuation lines joined, comments dropped.
instructions() {
  awk '/^[[:space:]]*#/ { next } { line = line $0 } /\\$/ { sub(/\\$/, "", line); next } { print line; line = "" }' "$1"
}
MOUNT="--mount=type=secret,id=extra_ca,required=false"
mounts=0
for file in api/Dockerfile renderer/Dockerfile web/Dockerfile browser/Dockerfile; do
  while IFS= read -r line; do
    case "$line" in
    RUN*) ;;
    ENV* | ARG* | COPY* | ADD*)
      [[ "$line" != *extra[_-]ca* ]] || fail "$file keeps the bundle beyond one step: $line"
      continue
      ;;
    *) continue ;;
    esac
    if [[ "$line" =~ (uv\ sync|pip\ install|pnpm\ install|playwright\ install|xcaddy\ build) ]]; then
      [[ "$line" == *"$MOUNT"* ]] || fail "$file downloads without the optional secret extra_ca: $line"
    fi
    [[ "$line" == *extra_ca* ]] || continue
    mounts=$((mounts + 1))
    [[ "$line" == *"$MOUNT "* ]] || fail "$file mounts extra_ca otherwise than optional at its default place: $line"
    [[ "$line" == *"if [ -s /run/secrets/extra_ca ]; then"* ]] || fail "$file uses extra_ca without checking it was given: $line"
    if [[ "$line" == *">/tmp/extra-ca.pem"* ]]; then
      [[ "$line" =~ rm\ -[rf]+\ [^\&]*/tmp/extra-ca\.pem ]] || fail "$file leaves the joined bundle in its layer: $line"
    fi
  done < <(instructions "$file")
done
[[ "$mounts" == 7 ]] || fail "expected 7 steps with extra_ca (api 3, renderer, web 2, browser), found $mounts"
ok "the 7 steps that download (uv, pip, pnpm, Playwright, xcaddy) mount extra_ca as optional, check it was given and remove the joined bundle; no ENV, ARG, COPY or ADD names it"

# --- the step images of the gate ---------------------------------------------------------------
# stack_files and s_images as ops/local-check.sh defines them, in a folder with a fake ci_env.sh.
mkdir -p "$SCRATCH/tree/.github/scripts"
printf '#!/usr/bin/env bash\necho EXAMPLE_SETTING=1\n' >"$SCRATCH/tree/.github/scripts/ci_env.sh"
chmod +x "$SCRATCH/tree/.github/scripts/ci_env.sh"
images() { # images <JOBAPP_EXTRA_CA or "unset">: s_images; its output in $SCRATCH/out, its exit code returned
  local value="$1" rc=0
  : >"$CALLS"
  (
    cd "$SCRATCH/tree"
    eval "$(sed -n -e '/^stack_files() {/,/^}/p' -e '/^s_images() {/,/^}/p' "$ROOT/ops/local-check.sh")"
    # shellcheck disable=SC2034 # read by the evaluated s_images
    PREFIX=jobapp-xcatest WORK="$SCRATCH/tree"
    # shellcheck disable=SC2030 # each case sets the variable in its own subshell
    if [[ "$value" == unset ]]; then unset JOBAPP_EXTRA_CA; else export JOBAPP_EXTRA_CA="$value"; fi
    PATH="$FAKE:$PATH" s_images
  ) >"$SCRATCH/out" 2>&1 || rc=$?
  return "$rc"
}
build_line='compose --progress plain -p jobapp-xcatest-build -f compose.yml -f tests/ats-stub.compose.yml'
if [[ "$(uname -s)-$(uname -m)" == Darwin-arm64 ]]; then build_line+=' -f compose.mac.yml'; fi
build_line+=" -f $SCRATCH/tree/images.yml"

images unset || fail "s_images failed with the variable unset: $(cat "$SCRATCH/out")"
[[ "$(cat "$CALLS")" == "$build_line build api worker caddy renderer browser" ]] ||
  fail "unset, the build is not today's: $(cat "$CALLS")"
images "" || fail "s_images failed with the variable empty: $(cat "$SCRATCH/out")"
[[ "$(cat "$CALLS")" == "$build_line build api worker caddy renderer browser" ]] ||
  fail "empty, the build is not today's: $(cat "$CALLS")"
images "$SCRATCH/bundle.pem" || fail "s_images failed with a readable bundle: $(cat "$SCRATCH/out")"
[[ "$(cat "$CALLS")" == "$build_line -f compose.extra-ca.yml build api worker caddy renderer browser" ]] ||
  fail "set, the build does not add compose.extra-ca.yml: $(cat "$CALLS")"
grep -q '^JOBAPP_EXTRA_CA is set: the builds also trust its bundle' "$SCRATCH/out" || fail "the step does not say it used the bundle: $(cat "$SCRATCH/out")"
! grep -rq "$CANARY" "$SCRATCH/out" "$CALLS" "$SCRATCH/tree" || fail "the content of the bundle reached the output, the calls or the tree"
ok "the step images adds compose.extra-ca.yml when JOBAPP_EXTRA_CA is set, builds as before when it is unset or empty, and never prints the bundle"

cp "$SCRATCH/bundle.pem" "$SCRATCH/tree/relative.pem"
for bad in relative.pem "$SCRATCH/missing.pem" "$SCRATCH"; do
  rc=0
  images "$bad" || rc=$?
  [[ "$rc" == 1 && ! -s "$CALLS" ]] || fail "JOBAPP_EXTRA_CA=$bad: exit $rc, calls: $(cat "$CALLS")"
  grep -q 'JOBAPP_EXTRA_CA is set but does not name a readable file by its absolute path' "$SCRATCH/out" ||
    fail "JOBAPP_EXTRA_CA=$bad is refused without its reason: $(cat "$SCRATCH/out")"
done
ok "a relative path, a missing file or a folder in JOBAPP_EXTRA_CA fails the step before any build"

# From here on the variable is set, as on a machine behind such a proxy.
# shellcheck disable=SC2031 # the subshells of images() above leave it to this line
export JOBAPP_EXTRA_CA="$SCRATCH/bundle.pem"
stacks="$(
  eval "$(sed -n -e '/^stack_files() {/,/^}/p' ops/local-check.sh)"
  # shellcheck disable=SC2034 # read by the evaluated stack_files
  WORK=/nowhere
  stack_files
)"
[[ "$stacks" != *extra-ca* ]] || fail "the files of the journey stacks name the override: $stacks"
outside="$(sed '/^s_images() {/,/^}/d' ops/local-check.sh | grep -v '^ *#' | grep -n 'extra[_-]ca' || true)"
[[ -z "$outside" ]] || fail "ops/local-check.sh names the extra CA outside its step images: $outside"
ok "the journey stacks of the gate never get the override, with the variable set"

# --- the deploy path -----------------------------------------------------------------------------
for file in deploy.sh .github/workflows/deploy.yml ops/deploy-checked.sh compose.yml; do
  ! grep -nE 'JOBAPP_EXTRA_CA|extra[_-]ca' "$file" || fail "$file names the extra CA"
done
! grep -q '^secrets:' compose.yml || fail "compose.yml declares secrets"
deploy_calls_ok() { # deploy_calls_ok <what>: the five builds, each with compose.yml alone
  local line count=0
  while IFS= read -r line; do
    [[ "$line" =~ ^compose\ -f\ compose\.yml\ --profile\ tunnel(\ --profile\ browser)?\ --progress=(quiet|plain)\ build\ (api|worker|caddy|renderer|browser)$ ]] ||
      fail "$1 builds with more than compose.yml: $line"
    count=$((count + 1))
  done <"$CALLS"
  [[ "$count" == 5 ]] || fail "$1 made $count builds, expected 5: $(cat "$CALLS")"
}

: >"$CALLS"
mkdir -p "$SCRATCH/jetson"
(
  eval "$(sed -n -e '/^step() {/p' -e '/^info() {/p' -e '/^warn() {/p' -e '/^die() {/,/^}/p' \
    -e '/^record() {/,/^}/p' -e '/^rcompose() {/,/^}/p' -e '/^build_images() {/,/^}/p' deploy.sh)"
  # shellcheck disable=SC2034 # read by the evaluated functions
  # shellcheck disable=SC2016 # the $PROFILE of deploy.sh, matched as written
  HOST=fakejetson RDIR_Q="$SCRATCH/jetson" TIMES=() RCOMPOSE="$(sed -n 's/^RCOMPOSE="\(.*\) --profile \$PROFILE"$/\1 --profile tunnel/p' deploy.sh)"
  # shellcheck disable=SC2329 # called by the evaluated rcompose: the command runs here, not on a Jetson
  remote() { PATH="$FAKE:$PATH" bash -c "$*"; }
  build_images
) >"$SCRATCH/out" 2>&1 || fail "build_images of deploy.sh failed: $(cat "$SCRATCH/out")"
deploy_calls_ok "deploy.sh"

: >"$CALLS"
compose="$(sed -n 's/^      COMPOSE: //p' .github/workflows/deploy.yml)"
[[ "$compose" == "docker compose -f compose.yml --profile tunnel" ]] || fail "deploy.yml composes with: $compose"
sed -n '/^          for svc in api worker caddy renderer; do$/,/^          echo "build browser: /p' .github/workflows/deploy.yml |
  sed 's/^          //' >"$SCRATCH/workflow-build.sh"
[[ "$(wc -l <"$SCRATCH/workflow-build.sh")" -gt 10 ]] || fail "the build block of deploy.yml was not found"
(
  cd "$SCRATCH/jetson"
  # shellcheck disable=SC2329 # called by the build block of the workflow
  restore_previous() { echo restore >>"$CALLS"; }
  export COMPOSE="$compose" PATH="$FAKE:$PATH"
  # shellcheck disable=SC1091
  . "$SCRATCH/workflow-build.sh"
) >"$SCRATCH/out" 2>&1 || fail "the build block of deploy.yml failed: $(cat "$SCRATCH/out")"
deploy_calls_ok "deploy.yml"
ok "deploy.sh and deploy.yml build with compose.yml alone, with the variable set, and never name the extra CA"

# --- the Compose files, read by the Compose CLI ---------------------------------------------------
if ! docker compose version >/dev/null 2>&1; then
  ok "SKIPPED the Compose CLI part: no docker compose here"
else
  printf 'POSTGRES_DB=example\nPOSTGRES_USER=example\nPOSTGRES_PASSWORD=example\n' >"$SCRATCH/invented.env"
  secrets_of() { # secrets_of <compose args...>: "service=secrets" per built service, then the top level secrets
    docker compose --env-file "$SCRATCH/invented.env" "$@" --profile browser --profile tunnel \
      config --no-env-resolution --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
for name, service in sorted(config["services"].items()):
    if "build" in service:
        built = [secret["source"] for secret in service["build"].get("secrets", [])]
        runtime = service.get("secrets")
        print(f"{name}={built} runtime={runtime}")
print("top", {k: v.get("file") for k, v in config.get("secrets", {}).items()})
'
  }
  plain="$(
    unset JOBAPP_EXTRA_CA
    secrets_of -f compose.yml
  )"
  [[ "$plain" == "api=[] runtime=None
browser=[] runtime=None
caddy=[] runtime=None
renderer=[] runtime=None
worker=[] runtime=None
top {}" ]] || fail "compose.yml alone: $plain"
  with="$(secrets_of -f compose.yml -f compose.mac.yml -f compose.extra-ca.yml)"
  [[ "$with" == "api=['extra_ca'] runtime=None
browser=['extra_ca'] runtime=None
caddy=['extra_ca'] runtime=None
renderer=['extra_ca'] runtime=None
worker=['extra_ca'] runtime=None
top {'extra_ca': '$SCRATCH/bundle.pem'}" ]] || fail "with compose.extra-ca.yml: $with"
  dev="$(secrets_of -f compose.yml -f compose.dev.yml -f compose.extra-ca.yml)"
  [[ "$(grep -c "=\['extra_ca'\] runtime=None$" <<<"$dev")" == 5 ]] || fail "with compose.dev.yml: $dev"
  rc=0
  (
    unset JOBAPP_EXTRA_CA
    docker compose --env-file "$SCRATCH/invented.env" -f compose.yml -f compose.extra-ca.yml config --no-env-resolution -q
  ) >"$SCRATCH/out" 2>&1 || rc=$?
  if [[ "$rc" == 0 ]] || ! grep -q 'required variable JOBAPP_EXTRA_CA is missing a value' "$SCRATCH/out"; then
    fail "the override without the variable: exit $rc, $(cat "$SCRATCH/out")"
  fi
  ok "with compose.extra-ca.yml every built service (dev targets too) gets the build secret extra_ca and no container does; compose.yml alone has none; the override without the variable is refused"
fi

printf '\n%d passed\n' "$passed"
