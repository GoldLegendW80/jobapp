#!/usr/bin/env bash
# Syntax check of a dotenv file before docker compose reads it (issue #18).
#
#   ops/env_check.sh <file> [label]     exit 0 when every line is valid, 1 otherwise, 2 unreadable
#
# On 2026-10-10 a paste of grep output into the Jetson's ~/jobapp/.env ("1 matches in 1 files:",
# ".env.jetson:20:KEY=value") made docker compose refuse the file and three deploys failed at the
# build, far from the cause. This check runs first: in ./deploy.sh preflight (the local
# .env.jetson, and the Jetson's .env streamed over ssh to `bash -s`), in deploy.yml before the
# build, and in tests/jetson_smoke.sh.
#
# A valid line is one of:
#   - blank (spaces or tabs only), or a comment: optional spaces, then #
#   - KEY=VALUE, KEY matching ^[A-Za-z_][A-Za-z0-9_]*$ from the first character (no spaces, no
#     `export `), then = right after it, then a value that is
#       empty;
#       unquoted: no space, tab or quote; = is fine (compose splits on the first =, so base64
#       padding stays in the value);
#       'single quoted' (no ' inside) or "double quoted" (\" and \\ escapes, no bare " inside),
#       closed on the same line;
#     followed by nothing but spaces and an optional comment (spaces, then #...).
# A trailing carriage return (a file saved with CRLF) is ignored, as compose ignores it.
# This is stricter than compose in one way only: a multi-line quoted value is refused.
#
# Each invalid line is reported as "<label>: line N: invalid (<reason>)" on stderr. Neither the
# value nor the rest of the line is ever printed: a .env holds secrets, and a broken line may be
# one whose = sits somewhere unexpected.
#
# Sourced (deploy.sh, the tests), it defines check_env_file and runs nothing.

ENV_KEY_RE='^[A-Za-z_][A-Za-z0-9_]*$'
ENV_COMMENT_RE='^([[:space:]]*|[[:space:]]+#.*)$'
ENV_UNQUOTED_RE="^[^[:space:]\"']*"
ENV_SINGLE_RE="^'[^']*'"
ENV_DOUBLE_RE='^"([^"\\]|\\.)*"'

# env_line_problem <line>: nothing for a valid line, else the reason (never a part of the line).
env_line_problem() {
  local line="${1%$'\r'}" key value rest
  [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && return 0
  if [[ "$line" != *=* ]]; then
    printf 'not KEY=VALUE'
    return 0
  fi
  key="${line%%=*}"
  value="${line#*=}"
  if [[ ! "$key" =~ $ENV_KEY_RE ]]; then
    printf 'the name before = is not a variable name (letters, digits, _; no spaces)'
    return 0
  fi
  case "$value" in
    \"*)
      if [[ ! "$value" =~ $ENV_DOUBLE_RE ]]; then
        printf 'a double quote is not closed on the same line'
        return 0
      fi
      ;;
    \'*)
      if [[ ! "$value" =~ $ENV_SINGLE_RE ]]; then
        printf 'a single quote is not closed on the same line'
        return 0
      fi
      ;;
    *) [[ "$value" =~ $ENV_UNQUOTED_RE ]] ;;
  esac
  rest="${value:${#BASH_REMATCH[0]}}"
  if [[ ! "$rest" =~ $ENV_COMMENT_RE ]]; then
    printf 'spaces or quotes in the value need the whole value in quotes'
  fi
  return 0
}

# check_env_file <file> [label]: every problem on stderr; 0 valid, 1 invalid, 2 unreadable.
check_env_file() {
  local file="$1" label="${2:-$1}" n=0 bad=0 line problem
  if [[ ! -f "$file" || ! -r "$file" ]]; then
    printf '%s: missing or unreadable\n' "$label" >&2
    return 2
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    problem="$(env_line_problem "$line")"
    if [[ -n "$problem" ]]; then
      printf '%s: line %d: invalid (%s)\n' "$label" "$n" "$problem" >&2
      bad=$((bad + 1))
    fi
  done <"$file"
  if ((bad > 0)); then
    printf '%s: %d invalid line(s); docker compose would refuse this file\n' "$label" "$bad" >&2
    return 1
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]:-$0}" == "$0" ]]; then
  set -uo pipefail
  [[ $# -ge 1 && $# -le 2 ]] || {
    printf 'usage: ops/env_check.sh <file> [label]\n' >&2
    exit 2
  }
  check_env_file "$@"
fi
