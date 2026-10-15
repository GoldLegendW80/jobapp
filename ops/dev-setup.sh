#!/usr/bin/env bash
# What a new laptop needs to develop jobapp with Claude Code, checked without changing anything.
# Read only: it installs, pulls, logs in and writes nothing, and never prints a token or a host.
#
#   ops/dev-setup.sh [--skip-jetson]
#
# Each line is `ok`, `MISS` (needed, with the command that fixes it) or `WARN` (useful, not
# needed). Checked: git; gh, signed in to github.com with the scopes `repo` and `project` (the
# board) and, as a warning, `workflow` (pushing a change of .github/workflows); Docker running,
# with at least DEV_SETUP_MIN_FREE_GIB (default 16) GiB free on the disk of this checkout (the
# merge gate refuses to start under 12 and uses about 3 while it runs); the pinned shellcheck
# image of the gate, present or pullable (`docker manifest inspect`, nothing is pulled); uv;
# Node at least the version of `.nvmrc` when the file exists, else of `engines.node` in
# web/package.json; pnpm of the major version `packageManager` of web/package.json names, or pnpm
# 10 or later above it (it runs the pinned version itself); python3 at least 3.12 (the scripts of
# .github/scripts parse the api's code); pre-commit, and its hook installed in this clone; the
# ssh alias `jetson` (DEV_SETUP_JETSON_ALIAS), reachable
# with BatchMode, so never a password prompt (--skip-jetson leaves it out, for a laptop that
# does not deploy); Claude Code (`claude`), as a warning. The new machine guide is the section
# "Develop on a new machine" of the README, and CLAUDE.md is where Claude Code starts.
#
# The last line is `DEV SETUP: OK` (exit 0) or `DEV SETUP: <n> missing` (exit 1). Wrong
# arguments: exit 64.
set -uo pipefail # not -e: every check runs and reports, whatever the one before it found

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/dev-setup.sh"
MIN_FREE_GIB="${DEV_SETUP_MIN_FREE_GIB:-16}"
JETSON_ALIAS="${DEV_SETUP_JETSON_ALIAS:-jetson}"
# The image of the Shellcheck step of .github/workflows/ci.yml (and of the gate).
SHELLCHECK_IMAGE="koalaman/shellcheck:v0.11.0"
MIN_PYTHON="3.12"

SKIP_JETSON=0
while (($#)); do
  case "$1" in
  --skip-jetson) SKIP_JETSON=1 ;;
  -h | --help)
    sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    printf '%s: unknown argument %s\nusage: %s [--skip-jetson]\n' "$PROG" "$1" "$PROG" >&2
    exit 64
    ;;
  esac
  shift
done

missing=0
ok() { printf 'ok    %s\n' "$1"; }
miss() {
  missing=$((missing + 1))
  printf 'MISS  %s\n      fix: %s\n' "$1" "$2"
}
warn() { printf 'WARN  %s\n      fix: %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }

# version_ge <a> <b>: true when the dotted version a is at least b (a leading v is ignored,
# missing parts count as 0, anything after the digits of a part is ignored: 9.15.9-rc reads 9.15.9).
version_ge() {
  local a="${1#v}" b="${2#v}" i x y
  local IFS=.
  # shellcheck disable=SC2206 # splitting on dots is the point
  local -a av=($a) bv=($b)
  for i in 0 1 2; do
    x="${av[i]:-0}"
    y="${bv[i]:-0}"
    x="${x%%[!0-9]*}"
    y="${y%%[!0-9]*}"
    x="${x:-0}"
    y="${y:-0}"
    if ((10#$x > 10#$y)); then return 0; fi
    if ((10#$x < 10#$y)); then return 1; fi
  done
  return 0
}

# first_version <text>: the first dotted number in the text (for `tool --version` lines).
first_version() { printf '%s\n' "$1" | grep -Eo '[0-9]+(\.[0-9]+)+' | head -n 1; }

echo "jobapp dev setup, read only, in $ROOT"

# --- git --------------------------------------------------------------------------------------
if have git; then
  ok "git $(first_version "$(git --version 2>/dev/null)")"
else
  miss "git is not installed" "xcode-select --install (macOS) or your package manager"
fi

# --- gh: signed in, with the scopes the workflow uses --------------------------------------
if ! have gh; then
  miss "gh (GitHub CLI) is not installed" "brew install gh, then gh auth login -h github.com -s project,workflow"
else
  # The output is read, never printed: it can name the account and a masked token.
  if auth="$(gh auth status -h github.com 2>&1)"; then
    scopes="$(printf '%s\n' "$auth" | grep -i 'token scopes' | head -n 1)"
    if [[ -z "$scopes" ]]; then
      warn "gh is signed in, but its token scopes cannot be read (a GH_TOKEN or a fine grained token?)" \
        "make sure it can read and write this repository and edit project 1 of the owner"
    else
      need=""
      [[ "$scopes" == *"'repo'"* ]] || need="$need repo"
      [[ "$scopes" == *"'project'"* ]] || need="$need project"
      if [[ -n "$need" ]]; then
        miss "gh token lacks the scope(s):$need" "gh auth refresh -h github.com -s repo,project"
      else
        ok "gh signed in to github.com with the scopes repo and project"
      fi
      [[ "$scopes" == *"'workflow'"* ]] ||
        warn "gh token lacks the scope workflow (needed only to push a change of .github/workflows)" \
          "gh auth refresh -h github.com -s workflow"
    fi
  else
    miss "gh is not signed in to github.com" "gh auth login -h github.com -s project,workflow"
  fi
fi

# --- Docker, the disk, the shellcheck image ------------------------------------------------
docker_up=0
if ! have docker; then
  miss "docker is not installed" "install Docker Desktop (https://docs.docker.com/desktop/), then open it"
elif docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  docker_up=1
  ok "docker running"
else
  miss "docker is installed but its daemon does not answer" "open Docker Desktop (open -a Docker on macOS) and wait until it is running"
fi

free_gib="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR == 2 { printf "%d", $4 / 1048576 }')"
if [[ -z "$free_gib" ]]; then
  warn "could not read the free disk of $ROOT" "check df -h by hand: the gate needs 12 GiB free to start"
elif ((free_gib < MIN_FREE_GIB)); then
  miss "$free_gib GiB free on the disk of this checkout, under $MIN_FREE_GIB" \
    "free some disk first (docker system df shows what Docker holds; remove only what you created)"
else
  ok "$free_gib GiB free on the disk of this checkout (at least $MIN_FREE_GIB)"
fi

if ((docker_up)); then
  if docker image inspect "$SHELLCHECK_IMAGE" >/dev/null 2>&1; then
    ok "shellcheck image $SHELLCHECK_IMAGE present"
  elif docker manifest inspect "$SHELLCHECK_IMAGE" >/dev/null 2>&1; then
    ok "shellcheck image $SHELLCHECK_IMAGE pullable (not pulled yet: docker pull $SHELLCHECK_IMAGE)"
  else
    miss "shellcheck image $SHELLCHECK_IMAGE neither present nor reachable" "docker pull $SHELLCHECK_IMAGE (needs Docker Hub)"
  fi
fi

# --- uv, Node, pnpm, python3 ---------------------------------------------------------------
if have uv; then
  ok "uv $(first_version "$(uv --version 2>/dev/null)")"
else
  miss "uv is not installed" "brew install uv (or curl -LsSf https://astral.sh/uv/install.sh | sh)"
fi

PACKAGE_JSON="$ROOT/web/package.json"
node_min=""
if [[ -f "$ROOT/.nvmrc" ]]; then
  node_min="$(first_version "$(head -n 1 "$ROOT/.nvmrc")")"
  [[ -n "$node_min" ]] || node_min="$(tr -dc '0-9' <"$ROOT/.nvmrc")"
elif [[ -f "$PACKAGE_JSON" ]]; then
  node_min="$(first_version "$(grep -E '"node"[[:space:]]*:' "$PACKAGE_JSON" | head -n 1)")"
fi
if ! have node; then
  miss "node is not installed" "brew install node@22 (or nvm install ${node_min:-22}), at least ${node_min:-22}"
else
  node_have="$(first_version "$(node --version 2>/dev/null)")"
  if [[ -z "$node_min" ]]; then
    warn "node $node_have, but no required version found (.nvmrc or engines.node of web/package.json)" "check web/package.json"
  elif version_ge "$node_have" "$node_min"; then
    ok "node $node_have (at least $node_min)"
  else
    miss "node $node_have, under the $node_min the web app needs" "install Node $node_min or newer (brew install node@22, or nvm install $node_min)"
  fi
fi

pnpm_want=""
if [[ -f "$PACKAGE_JSON" ]]; then
  pnpm_want="$(grep -Eo '"packageManager"[[:space:]]*:[[:space:]]*"pnpm@[0-9][0-9.]*' "$PACKAGE_JSON" | grep -Eo '[0-9][0-9.]*$' | head -n 1)"
fi
# Node 25 and later no longer ship corepack; npm comes with Node.
pnpm_fix() {
  if have corepack; then
    printf 'npm install -g pnpm@%s (or corepack enable && corepack prepare pnpm@%s --activate)' "$1" "$1"
  else
    printf 'npm install -g pnpm@%s' "$1"
  fi
}
if ! have pnpm; then
  miss "pnpm is not installed" "$(pnpm_fix "${pnpm_want:-9}")"
else
  # Off for this call: in web/, pnpm 10 and later would download the pinned pnpm and name it instead.
  pnpm_have="$(first_version "$(npm_config_manage_package_manager_versions=false pnpm --version 2>/dev/null)")"
  pnpm_have_major="${pnpm_have%%.*}"
  pnpm_want_major="${pnpm_want%%.*}"
  if [[ -z "$pnpm_want" ]]; then
    warn "pnpm $pnpm_have, but web/package.json names no packageManager" "check web/package.json"
  elif [[ "$pnpm_have_major" == "$pnpm_want_major" ]]; then
    ok "pnpm $pnpm_have (web/package.json pins $pnpm_want)"
  # pnpm 10 and later run the pinned version themselves (managePackageManagerVersions, on by default).
  elif ((10#${pnpm_have_major:-0} >= 10 && 10#${pnpm_have_major:-0} > 10#$pnpm_want_major)); then
    ok "pnpm $pnpm_have, runs the pinned $pnpm_want itself (packageManager of web/package.json)"
  else
    miss "pnpm $pnpm_have, web/package.json pins $pnpm_want" "$(pnpm_fix "$pnpm_want")"
  fi
fi

if have python3; then
  py_have="$(first_version "$(python3 --version 2>&1)")"
  if version_ge "$py_have" "$MIN_PYTHON"; then
    ok "python3 $py_have (at least $MIN_PYTHON)"
  else
    miss "python3 $py_have, under $MIN_PYTHON (the checks of .github/scripts parse the api's Python $MIN_PYTHON code)" \
      "brew install python@3.12 (or uv python install 3.12) and put it first on PATH"
  fi
else
  miss "python3 is not installed" "brew install python@3.12 (or uv python install 3.12)"
fi

# --- pre-commit and its hook in this clone ---------------------------------------------------
if have pre-commit; then
  ok "pre-commit $(first_version "$(pre-commit --version 2>/dev/null)")"
else
  miss "pre-commit is not installed" "uv tool install pre-commit (or brew install pre-commit)"
fi
hooks="$(git -C "$ROOT" rev-parse --git-path hooks 2>/dev/null || true)"
[[ -z "$hooks" || "$hooks" == /* ]] || hooks="$ROOT/$hooks"
if [[ -n "$hooks" && -f "$hooks/pre-commit" ]] && grep -q 'pre-commit' "$hooks/pre-commit"; then
  ok "pre-commit hook installed in this clone"
else
  miss "the pre-commit hook is not installed in this clone (commits would skip the secret scan)" \
    "pre-commit install (from the repository root, once per clone)"
fi

# --- the Jetson over ssh, without a prompt ---------------------------------------------------
if ((SKIP_JETSON)); then
  ok "jetson not checked (--skip-jetson)"
elif ! have ssh; then
  miss "ssh is not installed" "install OpenSSH"
elif ssh -o BatchMode=yes -o ConnectTimeout=8 "$JETSON_ALIAS" true </dev/null >/dev/null 2>&1; then
  ok "ssh $JETSON_ALIAS reachable without a prompt"
else
  # `ssh -G` resolves the configuration without connecting; a name that resolves to itself has
  # no Host block. The host name itself is never printed.
  resolved="$(ssh -G "$JETSON_ALIAS" 2>/dev/null | awk '$1 == "hostname" { print $2; exit }')"
  if [[ "$resolved" == "$JETSON_ALIAS" ]]; then
    miss "ssh $JETSON_ALIAS: no Host $JETSON_ALIAS block in ~/.ssh/config" \
      "add a Host $JETSON_ALIAS block (HostName, User, IdentityFile: ask the owner, never commit them), then ssh $JETSON_ALIAS true"
  else
    miss "ssh $JETSON_ALIAS does not connect without a prompt" \
      "join the tailnet (tailscale up), put your public key in the Jetson's authorized_keys, then ssh $JETSON_ALIAS true"
  fi
fi

# --- Claude Code ----------------------------------------------------------------------------
if have claude; then
  ok "claude (Claude Code) $(first_version "$(claude --version 2>/dev/null)")"
else
  warn "claude (Claude Code) is not installed" "npm install -g @anthropic-ai/claude-code, then run claude from the repository root"
fi

if ((missing)); then
  echo "DEV SETUP: $missing missing"
  exit 1
fi
echo "DEV SETUP: OK"
