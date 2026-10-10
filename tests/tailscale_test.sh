#!/usr/bin/env bash
# Checks of the public entry point, Tailscale Funnel (service tailscale), that need no tailnet and
# no auth key. Rerunnable; touches nothing outside its scratch copies, network and project.
#
#   tests/tailscale_test.sh                   1 and 2 everywhere, 3 on an arm64 machine outside CI
#   TAILSCALE_LIVE=0 tests/tailscale_test.sh  skip 3
#   TAILSCALE_LIVE=1 tests/tailscale_test.sh  run 3 even in CI (needs arm64, or emulation)
#
# 1. compose config, on a scratch copy of compose.yml with a fake .env: tailscale is in the
#    `tunnel` profile only, pinned by version and digest, userspace networking, no published port,
#    not privileged, every capability dropped, no device (no /dev/net/tun), state on the tsstate
#    volume, the committed serve config mounted read only, TS_AUTHKEY and TS_HOSTNAME from .env,
#    128m, restart unless-stopped, a healthcheck on `tailscale status --json`, after a healthy
#    caddy; cloudflared is gone; tailscale/serve.json Funnels HTTPS 443 to http://caddy:8080 only.
# 2. caddy's :8080 listener (the real caddy image and caddy/Caddyfile, an echo stand-in for the
#    api, a scratch network): the client address comes from X-Forwarded-For only, what Funnel
#    sets, read strictly right to left; Cf-Connecting-Ip, X-Real-Ip and the like are ignored; the
#    published :80 trusts no header at all.
# 3. live, without a key: the pinned image started in a scratch Compose project with no
#    TS_AUTHKEY stays NeedsLogin and never turns healthy (containerboot gives up after 60s and the
#    restart policy starts it again), so compose up --wait fails, and its logs say why. It never
#    gets a key, so it never joins a tailnet (the real key is never read here).
# Needs Docker and python3.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CADDY_IMAGE="$(sed -n 's/^ARG CADDY_IMAGE=//p' web/Dockerfile)"
PYTHON_IMAGE="$(sed -n 's/^ARG PYTHON_IMAGE=//p' api/Dockerfile)"
TAG="tstest-$$"
WORK="$(mktemp -d)"
NET="jobapp-$TAG"
PROJECT="jobapp-$TAG"

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

cleanup() {
  if [[ -f "$WORK/scratch/compose.yml" ]]; then
    (cd "$WORK/scratch" && docker compose -p "$PROJECT" --profile tunnel down -v --remove-orphans >/dev/null 2>&1) || true
  fi
  docker rm -f "$TAG-caddy" "$TAG-echo" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# A scratch copy of the stack definition: compose.yml, the serve config, empty build contexts and
# a fake .env, so `config` and `up` never read the real .env (or a key) of this checkout.
mkdir -p "$WORK/scratch/tailscale" "$WORK/scratch/web" "$WORK/scratch/api" "$WORK/scratch/caddy"
cp compose.yml "$WORK/scratch/"
cp tailscale/serve.json "$WORK/scratch/tailscale/"
cat >"$WORK/scratch/.env" <<'EOF'
APP_ENV=production
SECRET_KEY=x
POSTGRES_USER=u
POSTGRES_PASSWORD=p
POSTGRES_DB=d
DATABASE_URL=postgresql+psycopg://u:p@db:5432/d
REDIS_URL=redis://redis:6379/0
TS_HOSTNAME=jobapp-config-check
TS_AUTHKEY=tskey-auth-fake-config-check-only
EOF
# sc <args...>: docker compose in the scratch copy, with no TS_* from this shell.
sc() { (cd "$WORK/scratch" && env -u TS_AUTHKEY -u TS_HOSTNAME docker compose -p "$PROJECT" "$@"); }

step "compose config: tailscale pinned, userspace, unprivileged, unpublished, in the tunnel profile"
sc --profile tunnel config --format json >"$WORK/config.json" || fail "compose.yml does not validate"
python3 - "$WORK/config.json" <<'PY' || fail "tailscale service"
import json, re, sys
cfg = json.load(open(sys.argv[1]))
services = cfg["services"]
assert "cloudflared" not in services, "cloudflared is still defined"
ts = services["tailscale"]
assert re.fullmatch(r"tailscale/tailscale:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", ts["image"]), ts["image"]
assert ts.get("platform") == "linux/arm64", ts.get("platform")
assert ts["profiles"] == ["tunnel"], ts.get("profiles")
assert not ts.get("ports"), ts.get("ports")
assert not ts.get("expose"), ts.get("expose")
assert not ts.get("privileged"), "privileged"
assert not ts.get("cap_add"), ts.get("cap_add")
assert ts.get("cap_drop") == ["ALL"], ts.get("cap_drop")
assert "no-new-privileges:true" in ts.get("security_opt", []), ts.get("security_opt")
assert not ts.get("devices"), ts.get("devices")
assert ts.get("network_mode") in (None, ""), ts.get("network_mode")
env = ts["environment"]
assert env["TS_USERSPACE"] == "true", env
assert env["TS_AUTH_ONCE"] == "true", env
assert env["TS_AUTHKEY"] == "tskey-auth-fake-config-check-only", "TS_AUTHKEY not read from .env"
assert env["TS_HOSTNAME"] == "jobapp-config-check", "TS_HOSTNAME not read from .env"
assert env["TS_STATE_DIR"] == "/var/lib/tailscale", env
assert env["TS_SERVE_CONFIG"] == "/config/serve.json", env
assert "TS_EXTRA_ARGS" not in env, env
mounts = {(v["type"], v.get("source", "").rsplit("/", 2)[-2:] == ["tailscale", "serve.json"] or v.get("source"), v["target"], v.get("read_only", False)) for v in ts["volumes"]}
assert ("volume", "tsstate", "/var/lib/tailscale", False) in mounts, mounts
assert ("bind", True, "/config/serve.json", True) in mounts, mounts
assert len(ts["volumes"]) == 2, ts["volumes"]
assert "tsstate" in cfg["volumes"], cfg["volumes"]
assert int(ts["mem_limit"]) == 128 * 1024**2, ts["mem_limit"]
assert ts["restart"] == "unless-stopped", ts["restart"]
test = " ".join(ts["healthcheck"]["test"])
assert "tailscale status --json" in test and '"BackendState": "Running"' in test, test
assert ts["depends_on"]["caddy"]["condition"] == "service_healthy", ts["depends_on"]
for name, svc in services.items():
    assert not svc.get("privileged"), f"{name} is privileged"
    assert "/dev/net/tun" not in json.dumps(svc.get("devices", [])), f"{name} has /dev/net/tun"
    assert "NET_ADMIN" not in (svc.get("cap_add") or []), f"{name} adds NET_ADMIN"
PY
ok "pinned by version and digest (arm64), TS_USERSPACE, cap_drop ALL, no-new-privileges, no port, no device, state on tsstate, serve.json read only, 128m, restart, healthcheck, after a healthy caddy"
ok "TS_AUTHKEY and TS_HOSTNAME come from .env; no service is privileged or has NET_ADMIN or /dev/net/tun; no cloudflared"
sed -i.bak '/^TS_/d' "$WORK/scratch/.env"
sc --profile tunnel config --format json >"$WORK/config-nokey.json" || fail "compose.yml needs TS_AUTHKEY to validate"
python3 - "$WORK/config-nokey.json" <<'PY' || fail "defaults without TS_ lines"
import json, sys
env = json.load(open(sys.argv[1]))["services"]["tailscale"]["environment"]
assert env["TS_AUTHKEY"] == "", env["TS_AUTHKEY"]
assert env["TS_HOSTNAME"] == "jobapp", env["TS_HOSTNAME"]
PY
ok "without TS_ lines (the key removed after the first login) the config validates: empty key, hostname jobapp"
for profile in "" "--profile tunnel"; do
  # shellcheck disable=SC2086 # empty or two words on purpose
  services="$(sc $profile config --services)"
  if [[ -z "$profile" ]]; then
    ! grep -qx tailscale <<<"$services" || fail "tailscale starts without --profile tunnel"
  else
    grep -qx tailscale <<<"$services" || fail "tailscale missing with --profile tunnel"
  fi
done
ok "tailscale only with --profile tunnel: a plain compose.yml or the dev stack never joins the tailnet"

python3 - tailscale/serve.json <<'PY' || fail "tailscale/serve.json"
import json, sys
serve = json.load(open(sys.argv[1]))
assert set(serve) == {"TCP", "Web", "AllowFunnel"}, serve.keys()
assert serve["TCP"] == {"443": {"HTTPS": True}}, serve["TCP"]
assert serve["Web"] == {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {"Proxy": "http://caddy:8080"}}}}, serve["Web"]
assert serve["AllowFunnel"] == {"${TS_CERT_DOMAIN}:443": True}, serve["AllowFunnel"]
PY
ok "serve.json: HTTPS 443 only, / proxied to http://caddy:8080 (the tunnel listener), Funnel on for that port only"

step "caddy :8080 trusts Funnel's X-Forwarded-For only; :80 trusts nothing"
docker network create "$NET" >/dev/null
docker run -d --name "$TAG-echo" --network "$NET" --network-alias api \
  -v "$ROOT/tests/echo_upstream.py:/echo.py:ro" "$PYTHON_IMAGE" python /echo.py >/dev/null
docker run -d --name "$TAG-caddy" --network "$NET" --network-alias caddy \
  -v "$ROOT/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
# seen <url> [Header: value]...: the X-Forwarded-For and -Proto the api stand-in received for a
# request sent from the echo container, a private peer on the network as tailscale is.
seen() {
  docker exec -i "$TAG-echo" python - "$@" <<'PY'
import json, sys, time, urllib.request
url, headers = sys.argv[1], dict(h.split(": ", 1) for h in sys.argv[2:])
for attempt in range(40):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=3) as r:
            h = {k.lower(): v for k, v in json.load(r)["headers"].items()}
            print(h.get("x-forwarded-for", ""), h.get("x-forwarded-proto", ""))
            break
    except OSError:
        time.sleep(0.25)
else:
    sys.exit(f"no answer from {url}")
PY
}
peer="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$TAG-echo")"
[[ -n "$peer" ]] || fail "no address for the echo container"
forged=("Cf-Connecting-Ip: 203.0.113.10" "X-Real-Ip: 203.0.113.11" "True-Client-Ip: 203.0.113.12" "Forwarded: for=203.0.113.13")
read -r xff proto <<<"$(seen http://caddy:8080/api/echo "X-Forwarded-For: 198.51.100.7" "X-Forwarded-Proto: https" "${forged[@]}")"
[[ "$xff" == "198.51.100.7" ]] || fail ":8080 gave the api X-Forwarded-For '$xff', expected Funnel's 198.51.100.7"
[[ "$proto" == "https" ]] || fail ":8080 gave the api X-Forwarded-Proto '$proto', expected Funnel's https"
ok ":8080, as Funnel sends it: the api sees the visitor 198.51.100.7 and https; Cf-Connecting-Ip, X-Real-Ip, True-Client-Ip, Forwarded ignored"
read -r xff _ <<<"$(seen http://caddy:8080/api/echo "X-Forwarded-For: 203.0.113.9, 198.51.100.7")"
[[ "$xff" == "198.51.100.7" ]] || fail ":8080 with a list gave '$xff', expected the rightmost untrusted 198.51.100.7"
ok ":8080 reads a list right to left (trusted_proxies_strict): a value prepended by a visitor never wins"
read -r xff _ <<<"$(seen http://caddy:8080/api/echo "${forged[@]}")"
[[ "$xff" == "$peer" ]] || fail ":8080 without X-Forwarded-For gave '$xff', expected the peer $peer"
ok ":8080 without X-Forwarded-For: the peer $peer, never a forged Cf-Connecting-Ip"
read -r xff proto <<<"$(seen http://caddy:80/api/echo "X-Forwarded-For: 198.51.100.7" "X-Forwarded-Proto: https" "${forged[@]}")"
[[ "$xff" == "$peer" && "$proto" == "http" ]] || fail ":80 gave '$xff' '$proto', expected the peer $peer and http"
ok ":80 trusts no header: the peer $peer and http, whatever the client sends"

step "live: the pinned image without an auth key never comes up"
live="${TAILSCALE_LIVE:-}"
if [[ -z "$live" ]]; then
  live=1
  [[ "$(uname -m)" == "arm64" || "$(uname -m)" == "aarch64" ]] || live=0
  [[ -z "${CI:-}" ]] || live=0
fi
if [[ "$live" != "1" ]]; then
  printf '   skipped (arm64 outside CI only; TAILSCALE_LIVE=1 forces it)\n'
else
  # The scratch .env has no TS_ line any more, and sc unsets TS_* of this shell: no key at all.
  grep -q '^TS_AUTHKEY' "$WORK/scratch/.env" && fail "the scratch .env holds a key"
  sc --profile tunnel up -d --no-deps tailscale >/dev/null 2>&1 || fail "the tailscale container did not start"
  id="$(sc --profile tunnel ps -q tailscale)"
  [[ -n "$id" ]] || fail "no tailscale container"
  [[ "$(docker exec "$id" printenv TS_AUTHKEY || true)" == "" ]] || fail "the container got a key"
  read -r priv capdrop devices mem < <(docker inspect -f '{{.HostConfig.Privileged}} {{.HostConfig.CapDrop}} {{len .HostConfig.Devices}} {{.HostConfig.Memory}}' "$id")
  [[ "$priv $capdrop $devices $mem" == "false [ALL] 0 134217728" ]] || fail "container settings: $priv $capdrop $devices $mem"
  ok "running unprivileged, every capability dropped, no device, 128 MiB"
  backend=""
  deadline=$((SECONDS + 60))
  while ((SECONDS < deadline)); do
    backend="$(docker exec "$id" tailscale status --json --peers=false 2>/dev/null |
      sed -n 's/^[[:space:]]*"BackendState":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    [[ "$backend" == "NeedsLogin" ]] && break
    sleep 2
  done
  [[ "$backend" == "NeedsLogin" ]] || fail "BackendState is '${backend:-none}', expected NeedsLogin"
  ok "tailscale status --json: BackendState NeedsLogin (no key, no saved login)"
  [[ "$(docker exec "$id" tailscale status --json --peers=false | sed -n 's/^[[:space:]]*"TUN":[[:space:]]*\([a-z]*\).*/\1/p' | head -n 1)" == "false" ]] ||
    fail "TUN is not false: not userspace networking"
  ok "TUN false: userspace networking, no /dev/net/tun"
  # containerboot gives `tailscale up` 60s, then exits; the restart policy starts it again, so the
  # service never turns healthy, and compose up --wait, what a deploy runs, fails.
  start=$SECONDS
  if sc --profile tunnel up -d --no-deps --wait --wait-timeout 150 tailscale >"$WORK/up.log" 2>&1; then
    fail "compose up --wait succeeded without a key"
  fi
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" != "healthy" ]] || fail "healthy without a key"
  ok "compose up --wait fails after $((SECONDS - start))s: $(tail -n 1 "$WORK/up.log")"
  logs="$(docker logs "$id" 2>&1 | tail -n 400)"
  grep -q 'To authenticate, visit' <<<"$logs" || fail "the logs do not say it needs a login: $(tail -n 5 <<<"$logs")"
  grep -q 'failed to auth tailscale' <<<"$logs" || fail "the logs do not say the login failed: $(tail -n 5 <<<"$logs")"
  ok "the logs say why: 'To authenticate, visit ...', then 'failed to auth tailscale', and the container restarts ($(docker inspect -f '{{.RestartCount}}' "$id") so far)"
fi

printf '\nAll %d checks passed.\n' "$passed"
