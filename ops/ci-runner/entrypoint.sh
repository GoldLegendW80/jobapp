#!/usr/bin/env bash
# Entry point of the jobapp-ci-runner image (ops/ci-runner/Dockerfile). ops/ci-runners.sh drives
# it; CI_RUNNER_INDEX (1, 2, ...) names the instance.
#
#   configure   register with GitHub: the registration token is read from stdin and handed to
#               config.sh as ACTIONS_RUNNER_INPUT_TOKEN, never as an argument, so it shows in no
#               process list and no `docker inspect`; RUNNER_URL, RUNNER_NAME and RUNNER_LABELS
#               come from the environment
#   remove      unregister: the removal token is read from stdin, passed the same way
#   run         the container's default command: the Docker daemon of this instance, then the
#               runner's listener as the user `runner`. When either ends, the container exits and
#               Docker starts it again (restart unless-stopped).
#   exec ...    no runner and no registration: the Docker daemon, then the given command as the
#               user `runner`. For trying a CI step by hand in the runner's exact environment:
#                 docker run --rm -it --privileged -e CI_RUNNER_INDEX=99 -v jobapp-ci-try:/ci \
#                   -v jobapp-ci-try-docker:/var/lib/docker jobapp-ci-runner:<version> exec bash
#
# One volume, /ci, is shared by the instances: /ci/runner-<n> (the runner, its registration, its
# self-updates and its _work directory), /ci/home-<n> (HOME of its jobs) and /ci/cache (uv, pnpm,
# Playwright, pre-commit). /var/lib/docker is a volume of this instance alone.
set -euo pipefail

die() {
  printf 'ci-runner-entrypoint: %s\n' "$*" >&2
  exit 1
}
say() { printf 'ci-runner-entrypoint: %s\n' "$*"; }

[[ "${CI_RUNNER_INDEX:-}" =~ ^[0-9]+$ ]] || die "CI_RUNNER_INDEX must be a number"
[[ "$(id -u)" == 0 ]] || die "must start as root (it starts dockerd, then drops to the user runner)"
RUNNER_HOME="/ci/runner-$CI_RUNNER_INDEX"
JOB_HOME="/ci/home-$CI_RUNNER_INDEX"

prepare_dirs() { # prepare_dirs [noseed]
  mkdir -p "$RUNNER_HOME" "$JOB_HOME" /ci/cache
  # Top levels only: the trees below are already the runner's.
  chown runner:runner /ci "$RUNNER_HOME" "$JOB_HOME" /ci/cache
  [[ "${1:-}" != noseed ]] || return 0
  if [[ ! -x "$RUNNER_HOME/config.sh" ]]; then
    cp -a /opt/actions-runner/. "$RUNNER_HOME/"
    chown -R runner:runner "$RUNNER_HOME"
    say "seeded $RUNNER_HOME with runner $(cat /opt/actions-runner/.image-version)"
  fi
}

# as_runner <command...>: as the user runner, with its own HOME and nothing of root's environment
# but what the image and `docker run -e` set.
as_runner() {
  setpriv --reuid runner --regid runner --init-groups \
    env HOME="$JOB_HOME" USER=runner LOGNAME=runner "$@"
}

read_token() {
  local token=""
  IFS= read -r token || true
  [[ "$token" =~ ^[A-Za-z0-9_-]{20,}$ ]] || die "expected a token on stdin"
  printf '%s' "$token"
}

# cgroup v2: a container that runs containers must move its own processes out of the root of its
# cgroup before it can hand controllers (memory, cpu, pids) to the groups below. Without this,
# mem_limit in a job's compose file is refused. The same steps as the docker:dind image.
nest_cgroups() {
  [[ -f /sys/fs/cgroup/cgroup.controllers ]] || return 0
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 </sys/fs/cgroup/cgroup.procs >/sys/fs/cgroup/init/cgroup.procs 2>/dev/null || true
  sed -e 's/ / +/g' -e 's/^/+/' </sys/fs/cgroup/cgroup.controllers \
    >/sys/fs/cgroup/cgroup.subtree_control
}

start_dockerd() {
  # A daemon killed with the container leaves its pid files behind in the container's layer.
  rm -f /var/run/docker.pid /var/run/docker.sock /run/containerd/containerd.pid
  dockerd >/var/log/dockerd.log 2>&1 &
  DOCKERD_PID=$!
  local deadline=$((SECONDS + 60))
  until docker info >/dev/null 2>&1; do
    kill -0 "$DOCKERD_PID" 2>/dev/null || {
      tail -n 30 /var/log/dockerd.log >&2
      die "dockerd exited"
    }
    ((SECONDS < deadline)) || {
      tail -n 30 /var/log/dockerd.log >&2
      die "dockerd did not answer within 60s"
    }
    sleep 1
  done
  say "dockerd $(docker info --format '{{.ServerVersion}}, {{.Driver}}, cgroup v{{.CgroupVersion}}')"
}

cmd="${1:-run}"
case "$cmd" in
  configure)
    : "${RUNNER_URL:?set RUNNER_URL}" "${RUNNER_NAME:?set RUNNER_NAME}" "${RUNNER_LABELS:?set RUNNER_LABELS}"
    prepare_dirs
    token="$(read_token)"
    cd "$RUNNER_HOME"
    # configure is only called for a runner GitHub does not list: files of an older registration
    # (GitHub drops a runner that stayed offline for 14 days) would make config.sh refuse.
    rm -f .runner .credentials .credentials_rsaparams
    # --no-default-labels is not used: GitHub's own self-hosted, Linux and ARM64 stay.
    ACTIONS_RUNNER_INPUT_TOKEN="$token" as_runner ./config.sh --unattended --replace \
      --url "$RUNNER_URL" --name "$RUNNER_NAME" --labels "$RUNNER_LABELS" --work _work
    ;;
  remove)
    prepare_dirs
    token="$(read_token)"
    cd "$RUNNER_HOME"
    [[ -f .runner ]] || die "not configured, nothing to remove"
    ACTIONS_RUNNER_INPUT_TOKEN="$token" as_runner ./config.sh remove --unattended
    ;;
  run)
    prepare_dirs
    [[ -f "$RUNNER_HOME/.runner" ]] || die "not configured: run ops/ci-runners.sh install on the Mac"
    nest_cgroups
    start_dockerd
    # What a job killed with the container left behind (the hooks could not run).
    as_runner /usr/local/bin/ci-clean.sh all || true
    cd "$RUNNER_HOME"
    as_runner ./run.sh &
    RUNNER_PID=$!
    # shellcheck disable=SC2329 # invoked by the trap below
    stop() {
      say "stopping"
      kill -TERM "$RUNNER_PID" 2>/dev/null || true
      wait "$RUNNER_PID" 2>/dev/null || true
      kill -TERM "$DOCKERD_PID" 2>/dev/null || true
      wait "$DOCKERD_PID" 2>/dev/null || true
      exit 0
    }
    trap stop TERM INT
    # Whichever ends first ends the container; Docker restarts it.
    status=0
    wait -n "$RUNNER_PID" "$DOCKERD_PID" || status=$?
    say "the runner or dockerd ended (status $status): leaving so that Docker restarts the container"
    kill -TERM "$RUNNER_PID" "$DOCKERD_PID" 2>/dev/null || true
    wait || true
    exit 1
    ;;
  exec)
    shift
    [[ $# -gt 0 ]] || die "exec needs a command"
    prepare_dirs noseed
    nest_cgroups
    start_dockerd
    cd "$JOB_HOME"
    status=0
    as_runner "$@" || status=$?
    kill -TERM "$DOCKERD_PID" 2>/dev/null || true
    wait "$DOCKERD_PID" 2>/dev/null || true
    exit "$status"
    ;;
  *) die "unknown command '$cmd' (configure, remove, run or exec)" ;;
esac
