#!/usr/bin/env bash
# What a failed deploy rolls back to (.github/workflows/deploy.yml, issue #3).
#
#   ops/previous.sh tag-running            tag the image each running api, worker and caddy
#                                          container was started from as jobapp-<service>:previous
#   ops/previous.sh save-tree <app> <dir>  copy the deployed tree <app> to <dir> before the sync
#   ops/previous.sh restore-tree <dir> <app>
#                                          put that copy back over <app>
#
# tag-running reads the image id of the container itself (docker inspect -f '{{.Image}}'), so
# :previous is always an image that actually ran, never a :latest that was built but failed to
# start. A service with no running container keeps the :previous it already had, and says so.
# COMPOSE (default: docker compose -f compose.yml --profile tunnel) runs in the current directory.
# The worker has its own image since agents A2 (jobapp-worker, the api's code plus the Claude
# Agent SDK); on the first deploy of that change the running worker still runs jobapp-api, and
# that image id becomes jobapp-worker:previous, which is what a rollback must start again.
#
# The trees are copied with .rsyncignore, the deploy's own sync rules: .env*, .deployed-commit and
# the other ignored paths are neither saved nor overwritten, so a rollback never touches secrets.
# restore-tree on a missing copy (first deploy) does nothing and says so.
set -euo pipefail

IMAGES=(api:jobapp-api worker:jobapp-worker caddy:jobapp-caddy)

die() {
  printf 'ops/previous.sh: %s\n' "$*" >&2
  exit 1
}

tag_running() {
  local compose="${COMPOSE:-docker compose -f compose.yml --profile tunnel}" pair svc img ids id
  for pair in "${IMAGES[@]}"; do
    svc="${pair%%:*}"
    img="${pair#*:}"
    # word splitting of $compose is wanted: it is a command with its arguments
    # shellcheck disable=SC2086
    ids="$($compose ps -q --status running "$svc" 2>/dev/null || true)"
    id="$(head -n 1 <<<"$ids")"
    if [[ -z "$id" ]]; then
      echo "$svc: no running container, $img:previous left as it was"
      continue
    fi
    local image
    image="$(docker inspect -f '{{.Image}}' "$id")"
    [[ -n "$image" ]] || die "docker inspect gave no image for $svc ($id)"
    docker tag "$image" "$img:previous"
    echo "$svc: $img:previous = $image (running container ${id:0:12})"
  done
}

sync_tree() { # sync_tree <from> <to> <rsyncignore>
  # --checksum: a file the deploy rewrote within the same second at the same size must still be
  # put back; the tree is source code, a few megabytes, so hashing it costs nothing.
  rsync -a --checksum --delete --exclude-from="$3" "$1/" "$2/"
}

cmd="${1:-}"
case "$cmd" in
  tag-running)
    [[ $# -eq 1 ]] || die "usage: ops/previous.sh tag-running"
    tag_running
    ;;
  save-tree)
    [[ $# -eq 3 ]] || die "usage: ops/previous.sh save-tree <app> <dir>"
    app="$2" dir="$3"
    if [[ ! -f "$app/compose.yml" ]]; then
      rm -rf "$dir"
      echo "nothing deployed in $app yet: no previous tree"
      exit 0
    fi
    ignore="$app/.rsyncignore"
    [[ -f "$ignore" ]] || die "$ignore is missing"
    mkdir -p "$dir"
    # The ignore list of the tree being saved, so the copy holds what that deploy shipped.
    cp "$ignore" "$dir.rsyncignore"
    sync_tree "$app" "$dir" "$dir.rsyncignore"
    echo "previous tree saved: $dir ($(find "$dir" -type f | wc -l | tr -d ' ') files)"
    ;;
  restore-tree)
    [[ $# -eq 3 ]] || die "usage: ops/previous.sh restore-tree <dir> <app>"
    dir="$2" app="$3"
    if [[ ! -d "$dir" || ! -f "$dir.rsyncignore" ]]; then
      echo "no previous tree in $dir: files left as they are"
      exit 0
    fi
    sync_tree "$dir" "$app" "$dir.rsyncignore"
    echo "previous tree restored into $app"
    ;;
  *) die "usage: ops/previous.sh tag-running | save-tree <app> <dir> | restore-tree <dir> <app>" ;;
esac
