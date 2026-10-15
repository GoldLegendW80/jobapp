#!/usr/bin/env bash
# Put an issue or a pull request on the project board and set its three fields, in one command.
# The orchestrator runs it on every change of a task's state (docs/agents/orchestration.md).
#
#   ops/card.sh <issue or pull request url> <status> <module> <horizon>
#
#   status   todo | doing | done                     (the Status field: Todo, In Progress, Done)
#   module   platform | identity | profile | jobs | agents | ingestion | mail | documents |
#            browser | apply                          (the Module field, the modules of
#            CAPABILITY-MAP.md), or the raw option id of the Module field
#   horizon  now | next | later                       (the Horizon field)
#
# Example: ops/card.sh https://github.com/GoldLegendW80/jobapp/issues/324 doing platform now
#
# The board is project 1 of GoldLegendW80 ("Jobapp roadmap"). Adding an item that is already on
# the board returns the same item, so the command is safe to repeat. It needs gh authenticated
# with the `project` scope (`gh auth refresh -s project`). The ids below come from
# `gh project field-list 1 --owner GoldLegendW80 --format json`; if a field or an option is ever
# recreated on the board, read them again there and update this table.
#
#   Field    id                                 options
#   Status   PVTSSF_lAHOA9eVYc4BmeB8zhlG8_A     Todo f75ad846, In Progress 47fc9ee4, Done 98236657
#   Module   PVTSSF_lAHOA9eVYc4BmeB8zhlG9Os     platform 2b39a4a2, identity e4b7434c,
#                                               profile d51eccf3, jobs a406e707, agents af22ff23,
#                                               ingestion f0a4e79d, mail 45dd04b1,
#                                               documents fcf5dab9, browser 9743ac93,
#                                               apply 23d87376
#   Horizon  PVTSSF_lAHOA9eVYc4BmeB8zhlG9O0     Now ff3c9fd9, Next 2f98b287, Later a62bd178
#
# Exit status: 0 the card is set, 64 wrong arguments (nothing is called), else gh's own.
set -euo pipefail

PROG="ops/card.sh"
OWNER="GoldLegendW80"
BOARD_NUMBER=1
BOARD_ID="PVT_kwHOA9eVYc4BmeB8"
STATUS_FIELD="PVTSSF_lAHOA9eVYc4BmeB8zhlG8_A"
MODULE_FIELD="PVTSSF_lAHOA9eVYc4BmeB8zhlG9Os"
HORIZON_FIELD="PVTSSF_lAHOA9eVYc4BmeB8zhlG9O0"

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
todo) status=f75ad846 ;;
doing) status=47fc9ee4 ;;
done) status=98236657 ;;
*) usage_error "unknown status '$2' (todo, doing, done)" ;;
esac

case "$3" in
platform) module=2b39a4a2 ;;
identity) module=e4b7434c ;;
profile) module=d51eccf3 ;;
jobs) module=a406e707 ;;
agents) module=af22ff23 ;;
ingestion) module=f0a4e79d ;;
mail) module=45dd04b1 ;;
documents) module=fcf5dab9 ;;
browser) module=9743ac93 ;;
apply) module=23d87376 ;;
2b39a4a2 | e4b7434c | d51eccf3 | a406e707 | af22ff23 | f0a4e79d | 45dd04b1 | fcf5dab9 | 9743ac93 | 23d87376) module="$3" ;;
*) usage_error "unknown module '$3' (platform, identity, profile, jobs, agents, ingestion, mail, documents, browser, apply, or an option id)" ;;
esac

case "$4" in
now) horizon=ff3c9fd9 ;;
next) horizon=2f98b287 ;;
later) horizon=a62bd178 ;;
*) usage_error "unknown horizon '$4' (now, next, later)" ;;
esac

item="$(gh project item-add "$BOARD_NUMBER" --owner "$OWNER" --url "$url" --format json --jq .id)"
[[ -n "$item" ]] || {
  printf '%s: gh returned no item id for %s\n' "$PROG" "$url" >&2
  exit 1
}
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$STATUS_FIELD" --single-select-option-id "$status" >/dev/null
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$MODULE_FIELD" --single-select-option-id "$module" >/dev/null
gh project item-edit --project-id "$BOARD_ID" --id "$item" --field-id "$HORIZON_FIELD" --single-select-option-id "$horizon" >/dev/null
printf 'card %s: %s, %s, %s\n' "$url" "$2" "$3" "$4"
