#!/usr/bin/env bash
# Entry point of the jobapp-runner image (ops/runner/Dockerfile). ./deploy.sh runner drives it:
#
#   configure   register with GitHub: the registration token is read from stdin (never from an
#               argument or the environment, so it shows in no `docker inspect` and no process
#               list); RUNNER_URL, RUNNER_NAME and RUNNER_LABELS come from the environment
#   remove      unregister: the removal token is read from stdin
#   run         the long-running listener (the container's default command)
#
# The runner directory ($RUNNER_HOME, a named volume) is seeded from /opt/actions-runner the first
# time; the runner's own self-updates then land in the volume and survive restarts.
set -euo pipefail

: "${RUNNER_HOME:?RUNNER_HOME is not set}"
die() {
  printf 'runner-entrypoint: %s\n' "$*" >&2
  exit 1
}

seed() {
  if [[ ! -x "$RUNNER_HOME/config.sh" ]]; then
    cp -a /opt/actions-runner/. "$RUNNER_HOME/"
    printf 'runner-entrypoint: seeded %s with runner %s\n' "$RUNNER_HOME" "$(cat /opt/actions-runner/.image-version)"
  fi
}

read_token() {
  local token=""
  IFS= read -r token || true
  [[ "$token" =~ ^[A-Za-z0-9_-]{20,}$ ]] || die "expected a token on stdin"
  printf '%s' "$token"
}

cmd="${1:-run}"
case "$cmd" in
  configure)
    : "${RUNNER_URL:?set RUNNER_URL}" "${RUNNER_NAME:?set RUNNER_NAME}" "${RUNNER_LABELS:?set RUNNER_LABELS}"
    seed
    token="$(read_token)"
    cd "$RUNNER_HOME"
    ./config.sh --unattended --replace --url "$RUNNER_URL" --token "$token" \
      --name "$RUNNER_NAME" --labels "$RUNNER_LABELS" --work _work
    ;;
  remove)
    seed
    token="$(read_token)"
    cd "$RUNNER_HOME"
    [[ -f .runner ]] || die "not configured, nothing to remove"
    ./config.sh remove --token "$token"
    ;;
  run)
    seed
    cd "$RUNNER_HOME"
    [[ -f .runner ]] || die "not configured: run ./deploy.sh runner install from the Mac"
    exec ./run.sh
    ;;
  *) die "unknown command '$cmd' (configure, remove or run)" ;;
esac
