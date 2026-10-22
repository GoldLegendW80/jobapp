#!/usr/bin/env bash
# Put an issue or a pull request on the project board and set its three fields, in one command.
# Agents run it when they start a task; ops/board-sync.py makes every other move of a card.
#
#   ops/card.sh <issue or pull request url> <status> <module> <horizon>
#
#   status   todo | doing | done        the Status field: Ready, Building, Live
#   module   opportunities | platform | identity | profile | jobs | agents | ingestion | mail |
#            documents | browser | apply (the Module field, the modules of CAPABILITY-MAP.md),
#            or the raw option id of the Module field
#   horizon  now | next | later         the Horizon field: Now, Next, Later
#
# Agents run `ops/card.sh <issue url> doing <module> now` when they start a task. Every other
# move (In review, Approved, Gating, Merged, Live, ...) is made by ops/board-sync.py from GitHub
# facts, which ops/ship.sh runs; a sync keeps Building while the task has no pull request yet
# (docs/agents/orchestration.md, The board).
#
# Example: ops/card.sh https://github.com/GoldLegendW80/jobapp/issues/324 doing platform now
#
# The board is project 1 of GoldLegendW80 ("Jobapp roadmap"). Adding an item that is already on
# the board returns the same item, so the command is safe to repeat. It needs gh authenticated
# with the `project` scope (`gh auth refresh -s project`). The ids of the fields and of their
# options are looked up by name at run time, in one `gh project field-list` call, so a field
# whose options are replaced (ops/board-sync.py --setup) needs no edit here.
#
# Exit status: 0 the card is set, 64 wrong arguments (nothing is called), 1 a field or an option
# the board lacks, else gh's own.
set -euo pipefail

PROG="ops/card.sh"
OWNER="GoldLegendW80"
BOARD_NUMBER=1
BOARD_ID="PVT_kwHOA9eVYc4BmeB8"

usage_error() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  printf 'usage: %s <issue or pull request url> <todo|doing|done> <module> <now|next|later>\n' "$PROG" >&2
  exit 64
}

[[ $# -eq 4 ]] || usage_error "four arguments expected, got $#"
url="$1"
case "$url" in
https://github.com/"$OWNER"/*/issues/[0-9]* | https://github.com/"$OWNER"/*/pull/[0-9]*) ;;
*) usage_error "not an issue or pull request url of $OWNER: $url" ;;
esac

case "$2" in
todo) status="Ready" ;;
doing) status="Building" ;;
done) status="Live" ;;
*) usage_error "unknown status '$2' (todo, doing, done)" ;;
esac

MODULES=" opportunities platform identity profile jobs agents ingestion mail documents browser apply "
if [[ "$3" =~ ^[0-9a-f]{8}$ ]]; then
  module_id="$3"
elif [[ "$3" =~ ^[a-z]+$ && "$MODULES" == *" $3 "* ]]; then
  module_id=""
else
  usage_error "unknown module '$3' (${MODULES# }or an option id)"
fi

case "$4" in
now) horizon="Now" ;;
next) horizon="Next" ;;
later) horizon="Later" ;;
*) usage_error "unknown horizon '$4' (now, next, later)" ;;
esac

# The claim lives on the issue itself as the label `building` (ops/board-sync.py reads it): doing
# sets it, todo and done remove it. A pull request carries no claim, its own state says enough.
if [[ "$url" == */issues/* ]]; then
  if [[ "$2" == doing ]]; then
    gh issue edit "$url" --add-label building >/dev/null
  else
    gh issue edit "$url" --remove-label building >/dev/null 2>&1 || true
  fi
fi
# Once spec cards exist (label spec-card, owner 2026-10-21) the board shows only them and
# ops/board-sync.py keeps every other card archived: the label is the whole claim.
repo="${url#https://github.com/}"
repo="${repo%%/issues/*}"
repo="${repo%%/pull/*}"
if [[ -n "$(gh issue list --repo "$repo" --label spec-card --state all --limit 1 --json number --jq '.[].number')" ]]; then
  printf 'card %s: %s (claim label only: the board shows one card per spec)\n' "$url" "$2"
  exit 0
fi

# One line per option of each single select field: field name, field id, option name, option id.
# shellcheck disable=SC2016 # a jq program: $f and $id are jq variables
FIELDS="$(gh project field-list "$BOARD_NUMBER" --owner "$OWNER" --format json \
  --jq '.fields[] | select(.options) | .name as $f | .id as $id | .options[] | [$f, $id, .name, .id] | @tsv')"
FIELD_ID="" OPTION_ID=""
lookup() { # lookup <field name> <option name or ""> [<option id>]: FIELD_ID and OPTION_ID
  local field fid name oid
  FIELD_ID="" OPTION_ID=""
  while IFS=$'\t' read -r field fid name oid; do
    [[ "$field" == "$1" ]] || continue
    FIELD_ID="$fid"
    if [[ "$name" == "$2" || "$oid" == "${3:-}" ]]; then OPTION_ID="$oid"; fi
  done <<<"$FIELDS"
  [[ -n "$FIELD_ID" ]] || {
    printf '%s: the board has no single select field %s\n' "$PROG" "$1" >&2
    exit 1
  }
  [[ -n "$OPTION_ID" ]] || {
    printf '%s: the %s field of the board has no option %s\n' "$PROG" "$1" "${3:-$2}" >&2
    exit 1
  }
}
lookup Status "$status"
STATUS_FIELD="$FIELD_ID" status_id="$OPTION_ID"
if [[ -n "$module_id" ]]; then lookup Module "" "$module_id"; else lookup Module "$3"; fi
MODULE_FIELD="$FIELD_ID" module_id="$OPTION_ID"
lookup Horizon "$horizon"
HORIZON_FIELD="$FIELD_ID" horizon_id="$OPTION_ID"

item="$(gh project item-add "$BOARD_NUMBER" --owner "$OWNER" --url "$url" --format json --jq .id)"
[[ -n "$item" ]] || {
  printf '%s: gh returned no item id for %s\n' "$PROG" "$url" >&2
  exit 1
}
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$STATUS_FIELD" --single-select-option-id "$status_id" >/dev/null
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$MODULE_FIELD" --single-select-option-id "$module_id" >/dev/null
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$HORIZON_FIELD" --single-select-option-id "$horizon_id" >/dev/null
printf 'card %s: %s, %s, %s\n' "$url" "$2" "$3" "$4"
