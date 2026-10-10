#!/usr/bin/env bash
# Tests of the Mac CI runners (issue #83): ops/ci-runners.sh, the image's hooks in ops/ci-runner/
# and what ci.yml and deploy.yml must keep saying about where jobs run. No Docker daemon, no
# GitHub: `docker`, `gh` and `sudo` are fakes on PATH that log their arguments. Runs under the
# Mac's bash 3.2 and under CI's bash 5.
#
#   tests/ci_runners_test.sh
# The greps below look for the literal text of the files, ${{ }} and $variables included.
# shellcheck disable=SC2016
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CANARY="CANARYTOKEN0123456789abcdefXYZ"

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
# run <expected exit> <cmd...>: stdout and stderr together in $WORK/out
run() {
  local want="$1" got=0
  shift
  "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "$* exited $got, expected $want: $(head -c 600 "$WORK/out")"
}
out_has() { grep -qF -- "$1" "$WORK/out" || fail "output lacks '$1': $(head -c 600 "$WORK/out")"; }
log_has() { grep -qF -- "$1" "$WORK/docker.log" || fail "docker was not called with '$1': $(head -c 900 "$WORK/docker.log")"; }
log_lacks() {
  if grep -qF -- "$1" "$WORK/docker.log"; then fail "docker was called with '$1': $(grep -F -- "$1" "$WORK/docker.log" | head -n 3)"; fi
}

step "where the jobs run: ci.yml on the Mac runners only, deploy.yml on the Jetson only"
RUNS_ON='    runs-on: ${{ fromJSON(vars.CI_RUNS_ON || '"'"'["self-hosted", "mac-ci"]'"'"') }}'
jobs_total="$(grep -c '^    runs-on:' .github/workflows/ci.yml)"
jobs_mac="$(grep -cxF -- "$RUNS_ON" .github/workflows/ci.yml)"
[[ "$jobs_total" -ge 7 && "$jobs_total" == "$jobs_mac" ]] ||
  fail "ci.yml: $jobs_mac of $jobs_total jobs use the CI_RUNS_ON expression with the [self-hosted, mac-ci] default"
ok "ci.yml: all $jobs_total jobs run on vars.CI_RUNS_ON, default [self-hosted, mac-ci] (never self-hosted alone)"
[[ "$(grep -c '^    runs-on:' .github/workflows/deploy.yml)" == 1 ]] || fail "deploy.yml has more than one runs-on"
grep -qx '    runs-on: \[self-hosted, jetson\]' .github/workflows/deploy.yml || fail "deploy.yml does not run on [self-hosted, jetson]"
if grep -v '^ *#' .github/workflows/deploy.yml | grep -q 'ubuntu-latest\|mac-ci'; then fail "deploy.yml names a hosted or a Mac runner"; fi
ok "deploy.yml: one job, on [self-hosted, jetson], no hosted minutes"
# The gates of issue #83 that must survive the move: named so that removing one fails here.
for gate in 'coverage report --fail-under=95' 'coverage combine .coverage.jobs .coverage.rest' \
  'python3 .github/scripts/e2e_groups.py' 'tests/deploy_test.sh' 'tests/ci_runners_test.sh' \
  'pre-commit run --all-files' 'group: [identity, sweep, apps]' 'pytest -m docker' \
  'ruff format --check' 'cancel-in-progress: true' 'max-parallel: 2' '-n "$PYTEST_WORKERS"' \
  '--maxWorkers="$VITEST_WORKERS"'; do
  grep -qF -- "$gate" .github/workflows/ci.yml || fail "ci.yml lost: $gate"
done
if grep -v '^ *#' .github/workflows/ci.yml | grep -q -- '-n auto'; then fail "ci.yml lets pytest take every core"; fi
ok "ci.yml keeps the coverage floor, both shards, the e2e groups and their guard, the shell tests, pre-commit, cancel in progress; worker counts written out, two e2e stacks at most"
if grep -v '^ *#' .github/workflows/ci.yml | grep -qi 'armcap'; then fail "ci.yml names the Mac's OpenSSL variable"; fi
grep -q 'CI_COMPOSE_OVERLAY' .github/workflows/ci.yml || fail "ci.yml does not read the runner's CI_COMPOSE_OVERLAY"
grep -q '^ENV OPENSSL_armcap=0 \\$' ops/ci-runner/Dockerfile || fail "ops/ci-runner/Dockerfile does not set OPENSSL_armcap=0"
grep -q '^    CI_COMPOSE_OVERLAY=compose.mac.yml$' ops/ci-runner/Dockerfile || fail "ops/ci-runner/Dockerfile does not set CI_COMPOSE_OVERLAY"
if grep -v '^ *#' ops/runner/Dockerfile ops/runner/entrypoint.sh | grep -qi 'armcap\|CI_COMPOSE_OVERLAY'; then
  fail "the Jetson runner (ops/runner) names a Mac only setting"
fi
# Issue #83, the leak that filled the Mac's disk: throwaway Postgres, Redis and caddy containers
# removed without their anonymous volumes. Every removal in the test helpers takes -v.
if grep -n '"rm", "-f"' api/tests/pg.py api/tests/redis_server.py | grep -v '"rm", "-f", "-v"'; then
  fail "a test helper removes a container without its volumes (docker rm -f -v)"
fi
if grep -n 'docker rm ' tests/tailscale_test.sh tests/compose_smoke.sh tests/e2e_jetson.sh tests/e2e_jobs_jetson.sh tests/e2e_jobs_sweep_jetson.sh | grep -v -- ' -v '; then
  fail "a test script removes a container without its volumes (docker rm -v)"
fi
grep -q 'down -v --remove-orphans' .github/workflows/ci.yml || fail "the e2e teardown does not remove its volumes"
ok "test helpers and scripts remove containers with their anonymous volumes; the e2e teardown is down -v"
ok "the Mac's OpenSSL workaround comes from the Mac runner image only, through CI_COMPOSE_OVERLAY; ci.yml and the Jetson runner never name it"

step "runner image: pinned, verified, hooks baked in"
version="$(sed -n 's/^ARG RUNNER_VERSION=//p' ops/ci-runner/Dockerfile)"
sha="$(sed -n 's/^ARG RUNNER_SHA256=//p' ops/ci-runner/Dockerfile)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "RUNNER_VERSION is not pinned: '$version'"
[[ "$sha" =~ ^[0-9a-f]{64}$ ]] || fail "RUNNER_SHA256 is not a sha256"
grep -q 'echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c -' ops/ci-runner/Dockerfile || fail "the runner archive is not checked against RUNNER_SHA256"
grep -q "actions/runner/releases/download/v\${RUNNER_VERSION}/actions-runner-linux-arm64-\${RUNNER_VERSION}.tar.gz" ops/ci-runner/Dockerfile ||
  fail "the runner does not come from GitHub's release"
grep -q '^FROM ubuntu:24.04@sha256:[0-9a-f]\{64\}$' ops/ci-runner/Dockerfile || fail "the base image is not pinned by digest"
grep -q '^ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh \\$' ops/ci-runner/Dockerfile || fail "job-started hook not set in the image"
grep -q '^    ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh$' ops/ci-runner/Dockerfile || fail "job-completed hook not set in the image"
for f in job-started job-completed ci-clean; do
  grep -q "^COPY --chmod=755 $f.sh /usr/local/bin/" ops/ci-runner/Dockerfile || fail "the image does not copy $f.sh"
done
for f in ops/ci-runners.sh ops/ci-runner/entrypoint.sh ops/ci-runner/job-started.sh ops/ci-runner/job-completed.sh ops/ci-runner/ci-clean.sh tests/ci_runners_test.sh; do
  [[ -x "$f" ]] || fail "$f is not executable"
done
ok "runner $version from GitHub's release, sha256 checked, ubuntu by digest, both hooks set in the image"

# --- fakes ------------------------------------------------------------------------------------
mkdir -p "$WORK/bin"
cat >"$WORK/bin/docker" <<'FAKE'
#!/usr/bin/env bash
# Logs its arguments; plays a daemon with what FAKE_* say.
printf '%s\n' "$*" >>"$FAKE_LOG"
case "$1 ${2:-}" in
  "info "*)
    [[ "${FAKE_DOCKER_DOWN:-}" == 1 ]] && exit 1
    [[ "$*" == *NCPU* ]] && echo 12
    exit 0
    ;;
  "ps "*)
    if [[ "$*" == *"--filter network=n1"* ]]; then
      echo svc1
    elif [[ "$*" == *"label=jobapp.ci=runner"* ]]; then
      printf '%s\n' ${FAKE_RUNNERS:-}
    else
      printf '%s\n' ${FAKE_CONTAINERS:-}
    fi
    ;;
  "network ls")
    if [[ "$*" == *"name=^github_network_"* ]]; then
      [[ -n "${FAKE_RUNNER_NET:-}" ]] && echo n1
    else
      [[ -n "${FAKE_RUNNER_NET:-}" ]] && echo "n1 github_network_abc"
      [[ -n "${FAKE_NETS:-}" ]] && echo "n2 jobapp-ci_default"
    fi
    exit 0
    ;;
  "run "*)
    if [[ "$*" == *"--entrypoint test"* ]]; then exit 1; fi
    # configure and remove: the token arrives on stdin, and only there.
    if [[ "$*" == *" configure" || "$*" == *" remove" ]]; then cat >>"$FAKE_STDIN"; fi
    ;;
  "image inspect") exit 0 ;;
  "inspect "*) echo "running, restarts 0" ;;
  "volume prune") echo "Total reclaimed space: 0B" ;;
esac
exit 0
FAKE
cat >"$WORK/bin/gh" <<'FAKE'
#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$FAKE_LOG"
case "$*" in
  *registration-token* | *remove-token*) echo "$FAKE_TOKEN" ;;
  *"actions/runners --paginate"*startswith*) [[ "${FAKE_BUSY:-}" == 1 ]] && echo mac-ci-1 ;;
  *"actions/runners --paginate"*"select(.name"*)
    # Listed and online once a container was started for it; gone once unregistered.
    if grep -q ' remove$' "$FAKE_LOG"; then exit 0; fi
    if grep -q '^run -d ' "$FAKE_LOG" || [[ "${FAKE_LISTED:-}" == 1 ]]; then echo "7 online false"; fi
    ;;
esac
exit 0
FAKE
cat >"$WORK/bin/sudo" <<'FAKE'
#!/usr/bin/env bash
# `sudo -n du -sm /var/lib/docker`: the size the test asks for.
printf '%s\t/var/lib/docker\n' "${FAKE_DOCKER_MB:-100}"
FAKE
cat >"$WORK/bin/df" <<'FAKE'
#!/usr/bin/env bash
# `df -Pk $HOME`: the free space the test asks for, 100 GB otherwise.
[[ "${FAKE_DF_FAIL:-}" == 1 ]] && exit 1
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/disk 480000000 1 ${FAKE_FREE_KB:-104857600} 1% /"
FAKE
chmod +x "$WORK/bin/docker" "$WORK/bin/gh" "$WORK/bin/sudo" "$WORK/bin/df"
fake() { # fake <VAR=value...> -- <cmd...>: the command with the fakes first on PATH
  local envs=()
  while [[ "$1" != -- ]]; do
    envs+=("$1")
    shift
  done
  shift
  : >"$WORK/docker.log"
  : >"$WORK/stdin.log"
  env PATH="$WORK/bin:$PATH" FAKE_LOG="$WORK/docker.log" FAKE_STDIN="$WORK/stdin.log" \
    FAKE_TOKEN="$CANARY" CI_RUNNER_DIR="$WORK/rd" ${envs[@]+"${envs[@]}"} "$@"
}

step "job-started hook: this repository's ci.yml only, then a clean Docker daemon"
started() { # started <exit> <repository> <workflow ref>
  run "$1" fake FAKE_CONTAINERS="c1 c2" -- env GITHUB_REPOSITORY="$2" GITHUB_WORKFLOW_REF="$3" GITHUB_EVENT_NAME=pull_request \
    ops/ci-runner/job-started.sh
}
started 0 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/ci.yml@refs/pull/84/merge"
out_has "allowed"
log_has "rm -f -v c1 c2"
log_has "volume prune -af"
started 0 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/ci.yml@refs/heads/main"
started 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/deploy.yml@refs/heads/main"
out_has "refused: workflow"
log_lacks "rm -f"
started 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/ci.yml.evil.yml@refs/heads/main"
started 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/other.yml@refs/heads/x"
started 1 someone/fork "someone/fork/.github/workflows/ci.yml@refs/heads/main"
out_has "refused: repository"
started 1 "" ""
ok "allowed: ci.yml of this repository from a pull request and from main, leftovers removed first; refused: deploy.yml, another workflow, a look-alike name, another repository, no context"
ci_ref="GoldLegendW80/jobapp/.github/workflows/ci.yml@refs/heads/main"
run 0 fake -- env GITHUB_REPOSITORY=GoldLegendW80/jobapp GITHUB_WORKFLOW_REF="$ci_ref" ops/ci-runner/job-started.sh
out_has "100 GB free on the disk"
# 9 GB free: under the 10 GB a job needs. 300 MB is what the Mac had left on 2026-10-10.
run 1 fake FAKE_FREE_KB=9437184 -- env GITHUB_REPOSITORY=GoldLegendW80/jobapp GITHUB_WORKFLOW_REF="$ci_ref" ops/ci-runner/job-started.sh
out_has "refused: only 9 GB free on the disk"
out_has "CI needs 10 GB"
run 1 fake FAKE_FREE_KB=307200 -- env GITHUB_REPOSITORY=GoldLegendW80/jobapp GITHUB_WORKFLOW_REF="$ci_ref" ops/ci-runner/job-started.sh
out_has "refused: only 0 GB free"
run 0 fake FAKE_FREE_KB=9437184 -- env CI_MIN_FREE_GB=5 GITHUB_REPOSITORY=GoldLegendW80/jobapp GITHUB_WORKFLOW_REF="$ci_ref" ops/ci-runner/job-started.sh
run 1 fake FAKE_DF_FAIL=1 -- env GITHUB_REPOSITORY=GoldLegendW80/jobapp GITHUB_WORKFLOW_REF="$ci_ref" ops/ci-runner/job-started.sh
out_has "could not read the free disk space"
grep -q -- '-v "$PROBE_DIR:/host-disk:ro"' ops/ci-runners.sh || fail "the runners do not get the read only disk probe"
ok "disk guard: a job starts with 10 GB free or more, is refused with the figure under it (CI_MIN_FREE_GB) and when the space cannot be read"

step "ci-clean.sh: what it removes, what it leaves, its disk bound"
run 0 fake FAKE_CONTAINERS="svc1 c2 c3" FAKE_RUNNER_NET=1 FAKE_NETS=1 -- ops/ci-runner/ci-clean.sh job
log_has "rm -f -v c2 c3"
log_lacks "rm -f -v svc1"
log_has "network rm n2"
log_lacks "network rm n1"
log_has "image prune -f"
log_has "builder prune -f --keep-storage 1GB"
log_lacks "prune -af --volumes"
log_lacks "image prune -af"
ok "after a job: the job's containers and networks go, the runner's own service containers and github_network stay, images and 1GB of build cache stay"
run 0 fake FAKE_CONTAINERS="svc1 c2" FAKE_RUNNER_NET=1 FAKE_NETS=1 -- ops/ci-runner/ci-clean.sh all
log_has "rm -f -v svc1 c2"
log_has "network rm n1 n2"
ok "before a job: everything goes, a leftover github_network included"
run 0 fake FAKE_DOCKER_MB=4500 -- ops/ci-runner/ci-clean.sh job
out_has "over the bound of 4000 MB"
log_has "image prune -af"
log_has "builder prune -af"
run 0 fake FAKE_DOCKER_MB=4500 -- env CI_DOCKER_MAX_MB=8000 CI_BUILD_CACHE_KEEP=500MB ops/ci-runner/ci-clean.sh job
log_lacks "image prune -af"
log_has "--keep-storage 500MB"
ok "over CI_DOCKER_MAX_MB every image and the build cache are pruned; under it they stay"
run 0 fake FAKE_DOCKER_DOWN=1 -- ops/ci-runner/ci-clean.sh job
out_has "nothing cleaned"
run 0 fake FAKE_DOCKER_DOWN=1 -- env GITHUB_WORKSPACE="$WORK/none" ops/ci-runner/job-completed.sh
ok "a daemon that does not answer never fails the job"
ws="$WORK/runner-1/_work/jobapp/jobapp"
mkdir -p "$ws/web/test-results" "$ws/web/node_modules/x" "$ws/api/.venv" "$ws/.git" "$WORK/runner-1/_work/_tool/node" "$WORK/elsewhere/keep"
touch "$ws/.env"
run 0 fake -- env GITHUB_WORKSPACE="$ws" ops/ci-runner/job-completed.sh
[[ -d "$ws" && -z "$(ls -A "$ws")" ]] || fail "job-completed.sh did not empty the work folder: $(ls -A "$ws")"
[[ -d "$WORK/runner-1/_work/_tool/node" ]] || fail "job-completed.sh removed the tool cache"
run 0 fake -- env GITHUB_WORKSPACE="$WORK/elsewhere" ops/ci-runner/job-completed.sh
[[ -d "$WORK/elsewhere/keep" ]] || fail "job-completed.sh emptied a folder that is not a runner's work folder"
run 0 fake -- env GITHUB_WORKSPACE="" ops/ci-runner/job-completed.sh
ok "job-completed hook: the job's work folder is emptied (checkout, node_modules, virtualenv, Playwright output, .env), the tool cache and any folder outside _work are left"

step "ops/ci-runners.sh: arguments"
run 1 ops/ci-runners.sh
out_has "usage: ops/ci-runners.sh install"
run 1 ops/ci-runners.sh frobnicate
run 1 ops/ci-runners.sh status extra
run 1 fake -- ops/ci-runners.sh install 0
out_has "count must be a number"
run 1 fake -- ops/ci-runners.sh install 2 3
run 1 fake -- env CI_RUNNER_REPO="bad repo" ops/ci-runners.sh status
out_has "CI_RUNNER_REPO must be owner/name"
run 1 fake FAKE_DOCKER_DOWN=1 -- ops/ci-runners.sh status
out_has "Docker Desktop is not running"
ok "no command, unknown command, extra arguments, a bad count, a bad repository and a stopped Docker refused"
# 22 GB free is what the Mac had on 2026-10-10, when a trial filled its disk.
run 1 fake -- ops/ci-runners.sh install 4
out_has "at most 3 runners on this Mac"
log_lacks "run "
# 4 GB per runner plus 6: 14 GB for two runners, 18 GB for three.
run 1 fake FAKE_FREE_KB=13631488 -- ops/ci-runners.sh install 2
out_has "only 13 GB free on this Mac"
out_has "at least 14 GB"
log_lacks "build"
log_lacks "run "
run 0 fake FAKE_FREE_KB=15728640 -- ops/ci-runners.sh install 2
run 1 fake FAKE_FREE_KB=15728640 -- ops/ci-runners.sh install 3
run 0 fake FAKE_FREE_KB=5242880 -- env CI_RUNNER_MIN_FREE_GB=4 ops/ci-runners.sh install 1
run 1 fake -- env CI_RUNNER_CPUS=5 ops/ci-runners.sh install 3
out_has "would need CPUs 10-14 and Docker has 12"
log_lacks "run "
ok "install refuses more than 3 runners, a disk short of 4 GB per runner plus 6 before it builds or starts anything (CI_RUNNER_MIN_FREE_GB), and CPUs Docker does not have"

step "install against a fake docker and a fake gh: the token never shows, nothing of the shell leaks"
run 0 fake -- env GH_TOKEN=leak-gh ANTHROPIC_API_KEY=leak-anthropic CLAUDE_CODE_X=leak-claude ops/ci-runners.sh install 2
if grep -q "$CANARY" "$WORK/out"; then fail "the registration token is in the output"; fi
if grep -q "$CANARY" "$WORK/docker.log"; then fail "the registration token is in a command line"; fi
[[ "$(grep -c "$CANARY" "$WORK/stdin.log")" == 2 ]] || fail "the token did not reach configure on stdin once per runner"
if grep -q 'leak-\|GH_TOKEN\|ANTHROPIC\|CLAUDE\|--env-file' "$WORK/docker.log"; then fail "a variable of the calling shell reached docker"; fi
[[ "$(grep -c '^run -d ' "$WORK/docker.log")" == 2 ]] || fail "expected 2 runner containers"
log_has "run -d --name jobapp-ci-1 --hostname mac-ci-1 --restart unless-stopped --privileged --cpuset-cpus 0-2 --memory 4g --memory-swap 4g"
log_has "run -d --name jobapp-ci-2 --hostname mac-ci-2 --restart unless-stopped --privileged --cpuset-cpus 3-5 --memory 4g --memory-swap 4g"
log_has "-v jobapp-ci-docker-2:/var/lib/docker"
log_has "-e RUNNER_LABELS=mac-ci"
log_has "-e RUNNER_URL=https://github.com/GoldLegendW80/jobapp"
if grep '^run ' "$WORK/docker.log" | grep -q 'docker\.sock\|/Users\|/home/\|\$HOME'; then
  fail "a runner container gets the host's Docker socket or a host folder"
fi
[[ -d "$WORK/rd/disk" && -z "$(ls -A "$WORK/rd/disk")" ]] || fail "the disk probe folder is missing or not empty"
[[ "$(grep '^run ' "$WORK/docker.log" | grep -o -- '-v [^ ]*' | sort -u | tr '\n' ' ')" == "-v $WORK/rd/disk:/host-disk:ro -v jobapp-ci-docker-1:/var/lib/docker -v jobapp-ci-docker-2:/var/lib/docker -v jobapp-ci:/ci " ]] ||
  fail "unexpected mounts: $(grep '^run ' "$WORK/docker.log" | grep -o -- '-v [^ ]*' | sort -u | tr '\n' ' ')"
# Every -e the containers get, by name.
allowed="CI_RUNNER_INDEX RUNNER_URL RUNNER_NAME RUNNER_LABELS"
while IFS= read -r name; do
  [[ " $allowed " == *" $name "* ]] || fail "the containers get an unexpected variable: $name"
done < <(grep -o -- '-e [A-Za-z_]*=' "$WORK/docker.log" | sed 's/^-e //; s/=$//' | sort -u)
out_has "online on GitHub: 2 runner(s)"
ok "2 runners: token on stdin only; labels mac-ci; privileged, restart unless-stopped, own CPUs, memory and Docker volume; no host socket, no host folder but the empty read only disk probe, only $allowed"
run 0 fake -- ops/ci-runners.sh install
[[ "$(grep -c '^run -d ' "$WORK/docker.log")" == 2 ]] || fail "the default is not 2 runners"
if grep '^run -d ' "$WORK/docker.log" | grep -vq -- '--cpuset-cpus [03]-[25] --memory 4g --memory-swap 4g'; then
  fail "a runner has no CPU set or no memory limit"
fi
run 0 fake HOME="$WORK/home-off" -- ops/ci-runners.sh install 1
out_has "Docker Desktop does not start at login"
mkdir -p "$WORK/home-on/Library/Group Containers/group.com.docker"
echo '{"AutoStart": true}' >"$WORK/home-on/Library/Group Containers/group.com.docker/settings-store.json"
run 0 fake HOME="$WORK/home-on" -- ops/ci-runners.sh install 1
out_has "come back by themselves after a reboot"
[[ "$(cat "$WORK/home-on/Library/Group Containers/group.com.docker/settings-store.json")" == '{"AutoStart": true}' ]] || fail "Docker Desktop's settings were written"
ok "the default is 2 runners, CPUs 0-2 and 3-5, 4g each with no swap beyond; install says whether Docker Desktop starts at login and never writes its settings"

step "update, stop and prune wait for jobs; uninstall removes only what install made"
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_BUSY=1 -- ops/ci-runners.sh update
out_has "a job is running"
log_lacks "run -d"
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_BUSY=1 -- ops/ci-runners.sh prune
log_lacks "prune"
run 1 fake -- ops/ci-runners.sh update
out_has "no runner installed"
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 -- ops/ci-runners.sh stop
log_has "stop -t 30 jobapp-ci-1"
log_has "stop -t 30 jobapp-ci-2"
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2 other-thing jobapp-ci-x" FAKE_LISTED=1 -- ops/ci-runners.sh uninstall
if grep -q "$CANARY" "$WORK/out" "$WORK/docker.log"; then fail "the removal token shows"; fi
[[ "$(grep -c "$CANARY" "$WORK/stdin.log")" == 2 ]] || fail "the removal token did not reach remove on stdin once per runner"
log_has "volume rm jobapp-ci-docker-1"
log_has "volume rm jobapp-ci-docker-2"
log_has "volume rm jobapp-ci"
log_has "image rm jobapp-ci-runner:$version"
if grep -E '^(rm|volume rm|image rm|stop) ' "$WORK/docker.log" | grep -vE '^(rm -f jobapp-ci-[0-9]+|volume rm jobapp-ci(-docker-[0-9]+)?|image rm jobapp-ci-runner:[0-9.]+)$' | grep -q .; then
  fail "uninstall removed something that is not a CI runner object: $(grep -E '^(rm|volume rm|image rm) ' "$WORK/docker.log")"
fi
if grep -q 'prune' "$WORK/docker.log"; then fail "uninstall prunes"; fi
ok "update and prune refused while a job runs; stop stops each runner; uninstall: removal token on stdin only, only jobapp-ci-<n>, their volumes and the image, never a prune"

printf '\nci_runners_test: %s checks passed\n' "$passed"
