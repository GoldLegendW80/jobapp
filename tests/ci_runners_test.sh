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
[[ "$jobs_total" -ge 3 && "$jobs_total" == "$jobs_mac" ]] ||
  fail "ci.yml: $jobs_mac of $jobs_total jobs use the CI_RUNS_ON expression with the [self-hosted, mac-ci] default"
ok "ci.yml: all $jobs_total jobs run on vars.CI_RUNS_ON, default [self-hosted, mac-ci] (never self-hosted alone)"
[[ "$(grep -c '^    runs-on:' .github/workflows/deploy.yml)" == 1 ]] || fail "deploy.yml has more than one runs-on"
grep -qx '    runs-on: \[self-hosted, jetson\]' .github/workflows/deploy.yml || fail "deploy.yml does not run on [self-hosted, jetson]"
if grep -v '^ *#' .github/workflows/deploy.yml | grep -q 'ubuntu-latest\|mac-ci'; then fail "deploy.yml names a hosted or a Mac runner"; fi
ok "deploy.yml: one job, on [self-hosted, jetson], no hosted minutes"
# The gates that must survive every change of layout (issues #83 and #91): named so that
# removing one fails here. .github/scripts/ci_plan_test.py checks that their steps follow the plan.
for gate in 'coverage report --fail-under=95' 'pytest tests -m "not docker"' 'pytest -m docker' \
  'tests/jobs/test_scoring.py::test_long_text_is_bounded_and_fast' 'ruff check' 'ruff format --check' \
  'pnpm lint' 'pnpm typecheck' 'pnpm build' 'vitest run' \
  'python3 .github/scripts/e2e_groups.py' 'python3 .github/scripts/e2e_groups_test.py' \
  'python3 .github/scripts/ci_plan_test.py' 'python3 .github/scripts/ci_plan.py verify' \
  'tests/deploy_test.sh' 'tests/env_check_test.sh' 'tests/agents_real_run_test.sh' \
  'tests/ci_runners_test.sh' 'tests/tailscale_test.sh' 'koalaman/shellcheck:v0.11.0' \
  'pre-commit run --all-files' 'playwright test -c e2e/identity-all.config.ts' \
  'playwright test jobs-sweep.spec.ts' 'agents-streaming.spec.ts' \
  'cancel-in-progress: true' '-n "$PYTEST_WORKERS"' \
  '--maxWorkers="$VITEST_WORKERS"'; do
  grep -qF -- "$gate" .github/workflows/ci.yml || fail "ci.yml lost: $gate"
done
if grep -v '^ *#' .github/workflows/ci.yml | grep -q -- '-n auto'; then fail "ci.yml lets pytest take every core"; fi
if grep -v '^ *#' .github/workflows/ci.yml | grep -q 'continue-on-error'; then fail "ci.yml lets a step fail"; fi
# The run's result stands for every job only while no job can be skipped: none waits for another
# and none has a condition of its own (step conditions are indented deeper).
if grep -qE '^    (needs|if):' .github/workflows/ci.yml; then fail "a job of ci.yml waits for another or has a condition: it could be skipped"; fi
ok "ci.yml keeps the coverage floor, the whole pytest suite and its docker and timing tests, the web checks, vitest, the three e2e groups and their guard, the shell tests, pre-commit, the plan's own test and verification, cancel in progress; no job that could be skipped; worker counts written out"
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
# Run 38066771735: pnpm/action-setup's pnpm needs libatomic.so.1.
grep -q ' libatomic1 ' ops/ci-runner/Dockerfile || fail "the image lacks libatomic1 (pnpm/action-setup)"
grep -q 'rm -f .runner .credentials .credentials_rsaparams' ops/ci-runner/entrypoint.sh || fail "configure does not clear a stale registration"
grep -q '^ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh \\$' ops/ci-runner/Dockerfile || fail "job-started hook not set in the image"
grep -q '^    ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh$' ops/ci-runner/Dockerfile || fail "job-completed hook not set in the image"
for f in job-started job-completed ci-clean; do
  grep -q "^COPY --chmod=755 $f.sh /usr/local/bin/" ops/ci-runner/Dockerfile || fail "the image does not copy $f.sh"
done
for f in ops/ci-runners.sh ops/ci-rerun.sh ops/ci-runner/entrypoint.sh ops/ci-runner/job-started.sh ops/ci-runner/job-completed.sh ops/ci-runner/ci-clean.sh tests/ci_runners_test.sh; do
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
    elif [[ "$*" == *"name=jobapp-ci-try"* ]]; then
      # What a trial left behind, when the test says so.
      printf '%s\n' ${FAKE_TRY_CONTAINERS:-}
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
    if [[ "$*" == *"--entrypoint test"* ]]; then [[ "${FAKE_REGISTERED:-}" == 1 ]] && exit 0 || exit 1; fi
    # configure and remove: the token arrives on stdin, and only there.
    if [[ "$*" == *" configure" || "$*" == *" remove" ]]; then cat >>"$FAKE_STDIN"; fi
    ;;
  "logs "*)
    # What a runner's listener prints once it takes jobs, unless the test says it never does.
    [[ "${FAKE_NOT_LISTENING:-}" == 1 ]] || printf '%s\n' "√ Connected to GitHub" "Current runner version: '2.338.0'" "2026-10-10 17:22:25Z: Listening for Jobs"
    ;;
  "image inspect")
    [[ "$*" == *"{{.Id}}"* ]] && echo "sha256:new"
    exit 0
    ;;
  "image ls") printf '%s\n' jobapp-ci-runner:2.338.0 jobapp-ci-runner:2.300.0 ;;
  "inspect "*)
    # A container exists once created in this test (on the new image, running once started), or
    # because the test says so: FAKE_RUNNERS names it, FAKE_IMAGE says which image it runs and
    # FAKE_STOPPED that it was stopped by hand.
    name="${!#}"
    if [[ "$name" == jobapp-ci-try* ]]; then
      # A trial container: running only when the test says so.
      if [[ "${FAKE_TRY_RUNNING:-}" == 1 ]]; then echo true; else echo false; fi
      exit 0
    fi
    if grep -q "^create --name $name " "$FAKE_LOG"; then
      running=false
      if grep -q "^start $name\$" "$FAKE_LOG"; then running=true; fi
      image=new
    elif [[ " ${FAKE_RUNNERS:-} " == *" $name "* && -n "${FAKE_IMAGE:-}" ]]; then
      running=true
      if [[ " ${FAKE_STOPPED:-} " == *" $name "* ]]; then running=false; fi
      image="$FAKE_IMAGE"
    else
      running=""
    fi
    case "$*" in
      *"{{.State.Running}} {{.Image}}"*)
        [[ -n "$running" ]] || exit 1
        echo "$running sha256:$image"
        ;;
      *"{{.State.Running}}"*)
        [[ -n "$running" ]] || exit 1
        echo "$running"
        ;;
      *"{{.State.StartedAt}}"*) echo "2026-10-10T17:22:00Z" ;;
      *) echo "running, restarts 0" ;;
    esac
    ;;
  "volume inspect") exit 1 ;;
  "volume ls")
    if [[ "$*" == *"name=jobapp-ci-try"* ]]; then printf '%s\n' ${FAKE_TRY_VOLUMES:-}; fi
    ;;
  "volume rm")
    # A volume a container still uses cannot be removed.
    if [[ " ${FAKE_VOLUME_BUSY:-} " == *" ${!#} "* ]]; then exit 1; fi
    ;;
  "volume prune") echo "Total reclaimed space: 0B" ;;
esac
exit 0
FAKE
cat >"$WORK/bin/gh" <<'FAKE'
#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$FAKE_LOG"
# GitHub does not answer: every read of the runners fails (no `;;&` here: bash 3.2).
if [[ "${FAKE_GH_FAIL:-}" == 1 && "$*" == *"actions/runners --paginate"* ]]; then
  echo "gh: HTTP 502" >&2
  exit 1
fi
case "$*" in
  *registration-token* | *remove-token*) echo "$FAKE_TOKEN" ;;
  *"actions/runners --paginate"*startswith*) [[ "${FAKE_BUSY:-}" == 1 ]] && echo mac-ci-1 ;;
  *"actions/runners --paginate"*"select(.name"*)
    # Listed and online once a container was started for it; gone once unregistered.
    if grep -q ' remove$' "$FAKE_LOG"; then exit 0; fi
    if grep -q '^start ' "$FAKE_LOG" || [[ "${FAKE_LISTED:-}" == 1 ]]; then
      if [[ "${FAKE_BUSY:-}" == 1 ]]; then echo "7 online true"; else echo "7 online false"; fi
    fi
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
[[ "$(grep -c '^create --name ' "$WORK/docker.log")" == 2 ]] || fail "expected 2 runner containers"
log_has "create --name jobapp-ci-1 --hostname mac-ci-1 --restart unless-stopped --privileged --cpuset-cpus 0-2 --memory 4g --memory-swap 4g"
log_has "create --name jobapp-ci-2 --hostname mac-ci-2 --restart unless-stopped --privileged --cpuset-cpus 3-5 --memory 4g --memory-swap 4g"
# One at a time: runner 1 is created, started and heard listening before runner 2 is touched.
order="$(grep -nE '^(create --name|start|logs --since [^ ]+) jobapp-ci-[12]' "$WORK/docker.log" | sed -E 's/^[0-9]+:(create|start|logs).* (jobapp-ci-[12]).*/\1 \2/' | uniq | tr '\n' ',')"
[[ "$order" == "create jobapp-ci-1,start jobapp-ci-1,logs jobapp-ci-1,create jobapp-ci-2,start jobapp-ci-2,logs jobapp-ci-2,"* ]] ||
  fail "the runners were not started one at a time, each heard listening first: $order"
log_has "-v jobapp-ci-docker-2:/var/lib/docker"
log_has "-e RUNNER_LABELS=mac-ci"
log_has "-e RUNNER_URL=https://github.com/GoldLegendW80/jobapp"
if grep -E '^(run|create) ' "$WORK/docker.log" | grep -q 'docker\.sock\|/Users\|/home/\|\$HOME'; then
  fail "a runner container gets the host's Docker socket or a host folder"
fi
[[ -d "$WORK/rd/disk" && -z "$(ls -A "$WORK/rd/disk")" ]] || fail "the disk probe folder is missing or not empty"
[[ "$(grep -E '^(run|create) ' "$WORK/docker.log" | grep -o -- '-v [^ ]*' | sort -u | tr '\n' ' ')" == "-v $WORK/rd/disk:/host-disk:ro -v jobapp-ci-docker-1:/var/lib/docker -v jobapp-ci-docker-2:/var/lib/docker -v jobapp-ci:/ci " ]] ||
  fail "unexpected mounts: $(grep -E '^(run|create) ' "$WORK/docker.log" | grep -o -- '-v [^ ]*' | sort -u | tr '\n' ' ')"
# Every -e the containers get, by name.
allowed="CI_RUNNER_INDEX RUNNER_URL RUNNER_NAME RUNNER_LABELS"
while IFS= read -r name; do
  [[ " $allowed " == *" $name "* ]] || fail "the containers get an unexpected variable: $name"
done < <(grep -o -- '-e [A-Za-z_]*=' "$WORK/docker.log" | sed 's/^-e //; s/=$//' | sort -u)
out_has "online on GitHub and listening: 2 runner(s)"
out_has "jobapp-ci-2: listening for jobs"
ok "2 runners, one at a time, each heard listening before the next: token on stdin only; labels mac-ci; privileged, restart unless-stopped, own CPUs, memory and Docker volume; no host socket, no host folder but the empty read only disk probe, only $allowed"
run 0 fake -- ops/ci-runners.sh install
[[ "$(grep -c '^create --name ' "$WORK/docker.log")" == 3 ]] || fail "the default is not 3 runners"
log_has "create --name jobapp-ci-3 --hostname mac-ci-3 --restart unless-stopped --privileged --cpuset-cpus 6-8 --memory 4g --memory-swap 4g"
if grep '^create --name ' "$WORK/docker.log" | grep -vq -- '--cpuset-cpus [036]-[258] --memory 4g --memory-swap 4g'; then
  fail "a runner has no CPU set or no memory limit"
fi
run 0 fake HOME="$WORK/home-off" -- ops/ci-runners.sh install 1
out_has "Docker Desktop does not start at login"
mkdir -p "$WORK/home-on/Library/Group Containers/group.com.docker"
echo '{"AutoStart": true}' >"$WORK/home-on/Library/Group Containers/group.com.docker/settings-store.json"
run 0 fake HOME="$WORK/home-on" -- ops/ci-runners.sh install 1
out_has "come back by themselves after a reboot"
[[ "$(cat "$WORK/home-on/Library/Group Containers/group.com.docker/settings-store.json")" == '{"AutoStart": true}' ]] || fail "Docker Desktop's settings were written"
# With runners installed, install without a count keeps their number.
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_REGISTERED=1 FAKE_IMAGE=new -- ops/ci-runners.sh install
log_lacks "jobapp-ci-3"
out_has "online on GitHub and listening: 2 runner(s)"
ok "the default is 3 runners on a Mac without any (CPUs 0-2, 3-5 and 6-8, 4g each with no swap beyond) and the number that exists otherwise; install says whether Docker Desktop starts at login and never writes its settings"

step "update, start, stop and prune: idle runners only, known states only, listeners heard; uninstall removes only what install made"
# update: a container on an older image is recreated, but only once its runner is idle.
untouched() { # no runner container was stopped, removed, created or started
  if grep -qE '^(stop|rm|create|start) ' "$WORK/docker.log"; then
    fail "a runner container was touched: $(grep -E '^(stop|rm|create|start) ' "$WORK/docker.log" | head -n 3)"
  fi
}
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_BUSY=1 FAKE_IMAGE=old -- env CI_UPDATE_WAIT_S=0 ops/ci-runners.sh update
out_has "mac-ci-1 is still running a job after 0s: nothing was changed on it"
untouched
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_IMAGE=old -- ops/ci-runners.sh update
[[ "$(grep -c '^create --name ' "$WORK/docker.log")" == 2 ]] || fail "update did not recreate both idle runners"
# The listener is stopped, not killed, so it ends its session at GitHub; then the container goes.
order="$(grep -nE '^(stop -t 30|rm -f|create --name|start|logs --since [^ ]+) jobapp-ci-[12]' "$WORK/docker.log" | sed -E 's/^[0-9]+:(stop|rm|create|start|logs).* (jobapp-ci-[12]).*/\1 \2/' | uniq | tr '\n' ',')"
[[ "$order" == "stop jobapp-ci-1,rm jobapp-ci-1,create jobapp-ci-1,start jobapp-ci-1,logs jobapp-ci-1,stop jobapp-ci-2,rm jobapp-ci-2,create jobapp-ci-2,start jobapp-ci-2,logs jobapp-ci-2," ]] ||
  fail "update did not go one runner at a time, stop, remove, create, start, hear it listen: $order"
out_has "online on GitHub and listening: 2 of 2 runner(s)"
# The image the containers ran is pruned once none of them runs it: after the last recreation.
[[ "$(grep -n 'image prune -f --filter label=jobapp.ci=runner' "$WORK/docker.log" | tail -n 1 | cut -d: -f1)" -gt "$(grep -n '^start jobapp-ci-2' "$WORK/docker.log" | cut -d: -f1)" ]] ||
  fail "the replaced image is not pruned after the last recreation"
# A runner that never says it listens stops the update there: the next one is not touched.
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_IMAGE=old FAKE_NOT_LISTENING=1 -- env CI_LISTEN_WAIT_S=0 ops/ci-runners.sh update
out_has 'jobapp-ci-1 did not say "Listening for Jobs" within 0s'
log_lacks "jobapp-ci-2 "
# GitHub does not answer: unknown is not idle. Nothing is touched, by update, install or prune.
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_IMAGE=old FAKE_GH_FAIL=1 -- ops/ci-runners.sh update
out_has "GitHub did not answer about mac-ci-1"
out_has "nothing was changed on it"
untouched
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_REGISTERED=1 FAKE_IMAGE=old FAKE_GH_FAIL=1 -- ops/ci-runners.sh install
out_has "GitHub did not answer about mac-ci-1"
untouched
if grep -q 'registration-token' "$WORK/docker.log"; then fail "install asked for a token for a runner whose state it could not read"; fi
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_IMAGE=new FAKE_GH_FAIL=1 -- ops/ci-runners.sh prune
out_has "whether a job is running is unknown"
log_lacks "prune"
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_IMAGE=new FAKE_GH_FAIL=1 -- ops/ci-runners.sh status
out_has "unknown, GitHub did not answer"
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_IMAGE=new FAKE_GH_FAIL=1 -- ops/ci-runners.sh uninstall
out_has "GitHub did not answer"
if grep -q '^volume rm' "$WORK/docker.log"; then fail "uninstall removed volumes without knowing the runner was unregistered"; fi
# A runner GitHub no longer lists is not recreated by update: install registers it again.
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_IMAGE=old -- ops/ci-runners.sh update
out_has "mac-ci-1 is not registered at GitHub any more"
untouched
# A runner stopped by hand gets the new image and stays stopped; the other one is updated.
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_IMAGE=old FAKE_STOPPED="jobapp-ci-2" -- ops/ci-runners.sh update
log_has "create --name jobapp-ci-2 "
log_has "start jobapp-ci-1"
log_lacks "start jobapp-ci-2"
out_has "jobapp-ci-2: was stopped and stays stopped"
out_has "online on GitHub and listening: 1 of 2 runner(s)"
# No update on a disk where no job would start anyway.
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_IMAGE=old FAKE_FREE_KB=9437184 -- ops/ci-runners.sh update
out_has "only 9 GB free on this Mac"
log_lacks "build"
untouched
# Run again, or after an install: containers already on the built image are left alone, busy or not.
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_BUSY=1 FAKE_IMAGE=new -- ops/ci-runners.sh update
out_has "jobapp-ci-2: already running"
untouched
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_REGISTERED=1 FAKE_BUSY=1 FAKE_IMAGE=new -- ops/ci-runners.sh install
untouched
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_REGISTERED=1 FAKE_BUSY=1 FAKE_IMAGE=old -- env CI_UPDATE_WAIT_S=0 ops/ci-runners.sh install
out_has "still running a job"
untouched
# start waits for the listeners too; status says whether each one listens.
run 0 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2" FAKE_LISTED=1 FAKE_IMAGE=new -- ops/ci-runners.sh start
out_has "jobapp-ci-2: listening for jobs"
run 1 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_LISTED=1 FAKE_IMAGE=new FAKE_NOT_LISTENING=1 -- ops/ci-runners.sh status
out_has "not listening for jobs"
run 1 fake FAKE_RUNNERS="jobapp-ci-1 jobapp-ci-2 jobapp-ci-3" FAKE_LISTED=1 -- ops/ci-runners.sh install 2
out_has "3 runners exist and 2 were asked for"
log_lacks "build"
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
log_has "image rm jobapp-ci-runner:2.300.0"
if grep -E '^(rm|volume rm|image rm|stop) ' "$WORK/docker.log" | grep -vE '^(stop -t 30 jobapp-ci-[0-9]+|rm -f jobapp-ci-[0-9]+|rm -v jobapp-ci-try[a-z0-9-]*|volume rm jobapp-ci(-docker-[0-9]+|-try[a-z0-9-]*)?|image rm jobapp-ci-runner:[0-9.]+)$' | grep -q .; then
  fail "uninstall removed something that is not a CI runner object: $(grep -E '^(rm|volume rm|image rm) ' "$WORK/docker.log")"
fi
if grep -q 'prune' "$WORK/docker.log"; then fail "uninstall prunes"; fi
ok "update and a second install recreate a container only when it is not on the built image and its runner is idle, one at a time (stop, remove, create, start, wait for Listening for Jobs), and wait otherwise; the replaced image is pruned after the last one; a runner that does not listen, GitHub not answering, a runner GitHub no longer lists and a disk under 10 GB each stop the command with nothing touched; a stopped runner stays stopped; a lower count is refused; prune refused while a job runs; start and status hear the listeners; stop stops each runner; uninstall: removal token on stdin only, only jobapp-ci-<n>, their volumes and every tag of the image, never a prune"

step "trials: try removes its volumes, status shows what a trial left, prune and uninstall remove it"
LEFT_VOLUMES="jobapp-ci-try jobapp-ci-try-docker jobapp-ci-try-docker2 myjobapp-ci-try-x"
run 0 fake FAKE_TRY_VOLUMES="jobapp-ci-try jobapp-ci-try-docker" -- ops/ci-runners.sh try
log_has "run --rm --name jobapp-ci-try --label jobapp.ci=try --privileged --cpuset-cpus 9-11 --memory 4g --memory-swap 4g -e CI_RUNNER_INDEX=99 -v jobapp-ci-try:/ci -v jobapp-ci-try-docker:/var/lib/docker -v $WORK/rd/disk:/host-disk:ro jobapp-ci-runner:$version exec bash"
log_has "volume rm jobapp-ci-try"
log_has "volume rm jobapp-ci-try-docker"
[[ "$(grep -n '^volume rm jobapp-ci-try$' "$WORK/docker.log" | cut -d: -f1)" -gt "$(grep -n '^run --rm --name jobapp-ci-try ' "$WORK/docker.log" | cut -d: -f1)" ]] ||
  fail "the trial volumes are not removed after the trial"
if grep -q 'registration-token\|RUNNER_URL\|RUNNER_NAME\|restart' "$WORK/docker.log"; then fail "a trial registers a runner or restarts by itself"; fi
run 0 fake -- ops/ci-runners.sh try tests/deploy_test.sh --verbose
log_has "jobapp-ci-runner:$version exec tests/deploy_test.sh --verbose"
run 1 fake FAKE_TRY_CONTAINERS="jobapp-ci-try" -- ops/ci-runners.sh try
out_has "a trial container exists already"
log_lacks "run --rm"
run 1 fake FAKE_FREE_KB=9437184 -- ops/ci-runners.sh try
out_has "only 9 GB free on this Mac"
log_lacks "run --rm"
ok "try: a privileged container on the last CPUs with the runner's memory limit, two scratch volumes removed when it ends, no registration, no restart policy; refused beside another trial and on a short disk"
run 0 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_LISTED=1 FAKE_IMAGE=new FAKE_TRY_CONTAINERS="jobapp-ci-try" FAKE_TRY_VOLUMES="$LEFT_VOLUMES" -- ops/ci-runners.sh status
out_has "left by a trial"
out_has "container jobapp-ci-try"
out_has "volume jobapp-ci-try-docker2"
if grep -q 'myjobapp' "$WORK/out"; then fail "status counts a volume that is not a trial's"; fi
if grep -qE '^(rm|volume rm) ' "$WORK/docker.log"; then fail "status removed something"; fi
run 0 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_LISTED=1 FAKE_IMAGE=new FAKE_TRY_CONTAINERS="jobapp-ci-try" FAKE_TRY_VOLUMES="$LEFT_VOLUMES" -- ops/ci-runners.sh prune
log_has "rm -v jobapp-ci-try"
log_has "volume rm jobapp-ci-try-docker2"
log_lacks "myjobapp"
run 0 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_LISTED=1 FAKE_IMAGE=new FAKE_TRY_CONTAINERS="jobapp-ci-try" FAKE_TRY_RUNNING=1 FAKE_TRY_VOLUMES="jobapp-ci-try jobapp-ci-try-docker" FAKE_VOLUME_BUSY="jobapp-ci-try jobapp-ci-try-docker" -- ops/ci-runners.sh prune
out_has "trial container jobapp-ci-try is running: left"
out_has "trial volume jobapp-ci-try-docker is still used by a container: left"
log_lacks "rm -v jobapp-ci-try"
run 0 fake FAKE_RUNNERS="jobapp-ci-1" FAKE_LISTED=1 FAKE_IMAGE=new FAKE_TRY_VOLUMES="jobapp-ci-try jobapp-ci-try-docker" -- ops/ci-runners.sh uninstall
out_has "trial volume jobapp-ci-try-docker removed"
ok "status names a trial's container and volumes and removes nothing; prune and uninstall remove them, but not a running trial, not a volume in use, not a name that only looks alike"

step "ops/ci-rerun.sh: a fresh run of an open pull request, only from GitHub's fresh merge commit"
mkdir -p "$WORK/bin2"
cat >"$WORK/bin2/gh" <<'FAKE'
#!/usr/bin/env bash
# Pull request 7, feature/x at headsha1111, base main at basesha2222. FAKE_MERGE says what
# GitHub reports as its merge commit: fresh (default), stale (the old base, for ever), late
# (unknown, then stale, then fresh from the third question on) or conflict.
printf 'gh %s\n' "$*" >>"$FAKE_LOG"
asked="$(cat "$FAKE_STATE" 2>/dev/null || echo 0)"
case "$*" in
  *"/pulls/7 --jq .state") echo "${FAKE_PR_STATE:-open}" ;;
  *"/pulls/7 --jq .base.ref") echo main ;;
  *"/pulls/7 --jq .head.ref") echo feature/x ;;
  *"/pulls/7 --jq .head.sha") echo headsha1111 ;;
  *"/commits/main --jq .sha") echo basesha2222 ;;
  *"/pulls/7 --jq .mergeable | tostring")
    asked=$((asked + 1))
    echo "$asked" >"$FAKE_STATE"
    case "${FAKE_MERGE:-fresh}" in
      conflict) echo false ;;
      late) if ((asked < 2)); then echo null; else echo true; fi ;;
      *) echo true ;;
    esac
    ;;
  *"/pulls/7 --jq .merge_commit_sha"*)
    case "${FAKE_MERGE:-fresh}" in
      stale) echo mergeOLD ;;
      late) if ((asked < 3)); then echo mergeOLD; else echo mergeNEW; fi ;;
      *) echo mergeNEW ;;
    esac
    ;;
  *"/commits/mergeNEW --jq"*) echo "basesha2222 headsha1111" ;;
  *"/commits/mergeOLD --jq"*) echo "oldbase0000 headsha1111" ;;
  "pr reopen 7 "*) if [[ "${FAKE_REOPEN_FAIL:-}" == 1 ]]; then exit 1; fi ;;
  "pr close 7 "*) ;;
  *) echo "fake gh: unexpected call: $*" >&2; exit 1 ;;
esac
exit 0
FAKE
chmod +x "$WORK/bin2/gh"
rerun() { # rerun <exit> <VAR=value...> -- <arguments of ci-rerun.sh>
  local want="$1" envs=()
  shift
  while [[ "$1" != -- ]]; do
    envs+=("$1")
    shift
  done
  shift
  : >"$WORK/docker.log"
  rm -f "$WORK/asked"
  run "$want" env PATH="$WORK/bin2:$PATH" FAKE_LOG="$WORK/docker.log" FAKE_STATE="$WORK/asked" \
    CI_RERUN_POLL_S=0 ${envs[@]+"${envs[@]}"} ops/ci-rerun.sh "$@"
}
rerun 0 -- 7
order="$(grep -n '^gh pr ' "$WORK/docker.log" | sed 's/^[0-9]*:gh pr \([a-z]*\) 7 .*/\1/' | tr '\n' ',')"
[[ "$order" == "close,reopen," ]] || fail "expected one close then one reopen, got: $order"
log_has "pr close 7 --repo GoldLegendW80/jobapp"
out_has "GitHub's merge commit mergeNE is main at basesha plus the branch"
rerun 0 -- 7 --check
log_lacks "gh pr "
out_has "Nothing was changed"
ok "a fresh merge commit (the base as it is now plus the head): close, then reopen; --check only says so"
rerun 0 FAKE_MERGE=late -- 7
[[ "$(cat "$WORK/asked")" == 3 ]] || fail "expected three questions before GitHub's merge commit was fresh, asked $(cat "$WORK/asked")"
[[ "$(grep -c '^gh pr ' "$WORK/docker.log")" == 2 ]] || fail "expected a close and a reopen after the wait"
rerun 1 FAKE_MERGE=stale CI_RERUN_WAIT_S=0 -- 7
out_has "still reports a merge commit that is not main at basesha plus headsha"
out_has "Nothing was changed"
log_lacks "gh pr "
ok "while GitHub's merge commit is unknown or still the old base it waits, and gives up without closing anything"
rerun 1 FAKE_MERGE=conflict -- 7
out_has "conflicts with main"
log_lacks "gh pr "
rerun 1 FAKE_PR_STATE=closed -- 7
out_has "pull request 7 is closed"
log_lacks "gh pr "
rerun 1 FAKE_REOPEN_FAIL=1 -- 7
out_has "PULL REQUEST 7 IS CLOSED and could not be reopened"
[[ "$(grep -c '^gh pr reopen ' "$WORK/docker.log")" == 3 ]] || fail "expected three attempts to reopen"
rerun 1 -- abc
rerun 1 -- 7 --force
rerun 1 --
out_has "usage: ops/ci-rerun.sh"
ok "a conflict, a closed pull request and bad arguments change nothing; a reopen that fails is tried three times and then says the pull request is closed"

printf '\nci_runners_test: %s checks passed\n' "$passed"
