#!/usr/bin/env bash
# The whole identity journey on the Jetson through the public URL (identity I8, Checkpoint I-C):
# one Playwright run of web/e2e/identity-all.config.ts with throwaway e2e accounts only, framed by
# the guarantees that the live data is untouched.
#
#   tests/e2e_jetson.sh            against $(./deploy.sh url), evidence in tasks/evidence/identity/
#
# 1. Removes Docker test containers stuck in "Created" on the Mac (jobapp-test-pg-*), refuses to
#    start while e2e rows are left on the Jetson from an earlier run unless E2E_CLEAN_FIRST=1.
# 2. Snapshots Harald's account (role, is_active, created/updated/last sign in times, md5 of the
#    password hash, name, sessions, profile revision and md5 of its sections, invites).
# 3. Runs the config: every screenshot in light and dark, desktop and 375px, into
#    tasks/evidence/identity/<spec>-<page>-<viewport>-<scheme>.png. Each spec unlocks its own
#    failed sign in attempts (`./deploy.sh admin unlock`) so the per address budget never runs out.
# 4. Whatever happened, deletes every e2e row (users, with their sessions and profile through the
#    cascade, invites, login attempts), then proves the snapshot identical, zero e2e rows, and no
#    failed sign in left in the window for Harald's email or for this Mac's addresses.
# 5. Writes tasks/evidence/identity/index.md (screenshots by page) and run.log.
#
# Settings as deploy.sh: DEPLOY_HOST (default jetson), DEPLOY_DIR (default ~/jobapp).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
HARALD="hthautin@gmail.com"
ADMIN="e2e-admin@example.org"
E2E_LIKE="e2e-%@example.org"
EVIDENCE="$ROOT/tasks/evidence/identity"
LOG="$EVIDENCE/run.log"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die() {
  printf 'e2e_jetson.sh: %s\n' "$*" >&2
  exit 1
}

# sql: run the SQL on stdin in the Jetson's database, unaligned, fields separated by |.
sql() {
  # shellcheck disable=SC2016 # $POSTGRES_* expand in the db container
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T db sh -c" \
    "'psql -X -q -v ON_ERROR_STOP=1 -tA -F \"|\" -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
}

snapshot() { # one line per field of Harald's account, nothing secret (the hash only as md5)
  sql <<SQL
select 'email=' || u.email, 'name=' || u.name, 'role=' || u.role, 'is_active=' || u.is_active,
       'created_at=' || u.created_at, 'updated_at=' || u.updated_at,
       'last_login_at=' || coalesce(u.last_login_at::text, 'null'),
       'password_hash_md5=' || md5(u.password_hash),
       'sessions=' || (select count(*) from sessions s where s.user_id = u.id),
       'sessions_md5=' || coalesce((select md5(string_agg(s.id::text || s.expires_at::text, ',' order by s.id))
                                    from sessions s where s.user_id = u.id), 'none'),
       'profile_revision=' || coalesce(p.revision::text, 'none'),
       'profile_updated_at=' || coalesce(p.updated_at::text, 'none'),
       'profile_sections_md5=' || coalesce(md5(p.sections::text), 'none'),
       'profile_revisions=' || (select count(*) from profile_revisions r where r.user_id = u.id),
       'invites=' || (select count(*) from invites i where i.email = u.email)
from users u left join profiles p on p.user_id = u.id where u.email = '$HARALD';
SQL
}

residue() { # e2e rows left: users, sessions, profiles, invites, login attempts
  sql <<SQL
select 'users=' || (select count(*) from users where email like '$E2E_LIKE'),
       'sessions=' || (select count(*) from sessions s join users u on u.id = s.user_id where u.email like '$E2E_LIKE'),
       'profiles=' || (select count(*) from profiles p join users u on u.id = p.user_id where u.email like '$E2E_LIKE'),
       'invites=' || (select count(*) from invites where email like '$E2E_LIKE'),
       'login_attempts=' || (select count(*) from login_attempts where email like '$E2E_LIKE');
SQL
}

clean_e2e() { # every row of the e2e accounts; sessions and profile rows cascade with the user
  sql <<SQL
begin;
delete from invites where email like '$E2E_LIKE'
  or created_by in (select id from users where email like '$E2E_LIKE');
delete from login_attempts where email like '$E2E_LIKE';
delete from users where email like '$E2E_LIKE';
commit;
SQL
}

mac_ips() { # the addresses Cloudflare sees for this Mac, IPv4 and IPv6 when it has one
  local family
  for family in -4 -6; do
    curl -s --max-time 10 "$family" "$URL/cdn-cgi/trace" 2>/dev/null | sed -n 's/^ip=//p' || true
  done
}

stuck_containers() {
  local ids
  ids="$(docker ps -a --filter 'name=jobapp-test-pg-' --filter status=created -q 2>/dev/null || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086 # one id per word
    docker rm $ids >/dev/null
    info "removed $(wc -w <<<"$ids" | tr -d ' ') jobapp-test-pg-* container(s) stuck in Created"
  else
    info "no jobapp-test-pg-* container stuck in Created"
  fi
}

mkdir -p "$EVIDENCE"
exec > >(tee "$LOG") 2>&1
printf 'tests/e2e_jetson.sh, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

step "before"
stuck_containers
URL="$(./deploy.sh url 2>/dev/null)" || die "no public URL (./deploy.sh url)"
info "public URL: $URL"
left="$(residue)"
if [[ "$left" != "users=0|sessions=0|profiles=0|invites=0|login_attempts=0" ]]; then
  [[ "${E2E_CLEAN_FIRST:-0}" == "1" ]] || die "e2e rows left by an earlier run ($left); rerun with E2E_CLEAN_FIRST=1"
  clean_e2e >/dev/null
  info "removed e2e rows of an earlier run: $left"
fi
snapshot | tr '|' '\n' >"$WORK/before"
[[ -s "$WORK/before" ]] || die "no account $HARALD on $HOST"
sed 's/^/   /' "$WORK/before"
rm -f "$EVIDENCE"/*.png

step "Playwright: web/e2e/identity-all.config.ts through $URL"
status=0
E2E_CADDY_URL="$URL" E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL="$ADMIN" E2E_EVIDENCE=identity \
  E2E_SHOT_PREFIX='{spec}-' pnpm --dir web exec playwright test -c e2e/identity-all.config.ts || status=$?

step "after: e2e rows removed, Harald's account unchanged, nobody throttled"
info "e2e rows before the cleanup: $(residue)"
clean_e2e >/dev/null
left="$(residue)"
info "e2e rows after the cleanup: $left"
[[ "$left" == "users=0|sessions=0|profiles=0|invites=0|login_attempts=0" ]] || status=1
snapshot | tr '|' '\n' >"$WORK/after"
if diff -u "$WORK/before" "$WORK/after"; then
  info "Harald's account: identical before and after ($(wc -l <"$WORK/after" | tr -d ' ') fields)"
else
  printf '   FAIL: Harald'"'"'s account changed\n'
  status=1
fi
ips=""
in_list=""
for ip in $(mac_ips | sort -u); do
  [[ "$ip" =~ ^[0-9A-Fa-f:.]+$ ]] || continue
  ips+="${ips:+, }$ip"
  in_list+="${in_list:+,}'$ip'"
done
failures="$(sql <<SQL
select 'email=' || (select count(*) from login_attempts where email = '$HARALD' and not success
                      and at > now() - interval '15 minutes'),
       'mac_ips=' || (select count(*) from login_attempts where not success and at > now() - interval '15 minutes'
                      and ip in (${in_list:-null})),
       'all=' || (select count(*) from login_attempts where not success and at > now() - interval '15 minutes');
SQL
)"
info "failed sign ins in the 15 minute window: $failures (Mac addresses: ${ips:-unknown})"
[[ "$failures" == "email=0|mac_ips=0|all=0" ]] || status=1
stuck_containers

step "index"
# row <label> <page...>: one line of the index, each page with its four shots
row() {
  local label="$1" page v line
  shift
  line="| $label |"
  for page in "$@"; do
    for v in desktop-light desktop-dark mobile-light mobile-dark; do
      [[ -e "$EVIDENCE/$page-$v.png" ]] || {
        line+=" $page-$v MISSING"
        status=1
        continue
      }
    done
    line+=" \`$page\`: [desktop light]($page-desktop-light.png), [dark]($page-desktop-dark.png),"
    line+=" [375 light]($page-mobile-light.png), [dark]($page-mobile-dark.png)<br>"
  done
  printf '%s |\n' "${line%<br>}"
}
# shellcheck disable=SC2016 # backticks are Markdown here
{
  printf '# Identity: evidence of the Jetson journey\n\n'
  printf 'Written by `tests/e2e_jetson.sh` on %s against %s, one run of\n' "$(date -u +%Y-%m-%d)" "$URL"
  printf '`web/e2e/identity-all.config.ts` (log: [run.log](run.log)). Every page in light and dark, desktop\n'
  printf '1280px and mobile 375px, each checked for horizontal overflow. Throwaway e2e accounts only, all\n'
  printf 'removed afterwards; Harald'"'"'s account was never signed in to and is unchanged (see run.log).\n\n'
  printf '## The pages\n\n| Page | Screenshots |\n|---|---|\n'
  row "Sign in" identity-login identity-login-error
  row "Invite (set up your account)" identity-invite
  row "Reset (choose a new password)" identity-reset
  row "Invalid link" identity-invalid-link invites-invalid-link
  row "Shell" identity-shell-admin
  row "Account" account-account account-account-renamed-menu account-account-wrong-current account-account-password-changed
  row "Admin users" users-admin-users users-admin-users-disabled users-admin-disable-confirm users-admin-self-demote-confirm users-friend-admin-users
  row "Admin invites" invites-admin-invites-active invites-admin-invite-link invites-admin-revoke-confirm invites-admin-invites-used invites-admin-invite-dialog-error
  row "Member refused the admin area" invites-member-admin-404 users-admin-demoted-404
  row "Throttled sign in" throttle-login-throttled
  row "Disabled and old password refused" users-friend-disabled-sign-in account-old-password-refused
  row "Profile tabs" profile-identity profile-experience profile-facts profile-skills profile-search profile-answers
  row "Profile editing" profile-identity-crop profile-identity-photo profile-identity-conflict profile-unsaved-guard
  row "Profile editors" editors-facts editors-skills editors-skills-regex-error editors-search editors-search-check-found
  row "Profile history" history-identity-history-diff history-facts-history-diff history-identity-history
  row "Profile import" history-import-refused history-import-summary history-identity-after-import
  printf '\n## Every file\n\n'
  find "$EVIDENCE" -name '*.png' -exec basename {} \; | sort | sed 's/.*/- [&](&)/'
} >"$EVIDENCE/index.md"
info "$(find "$EVIDENCE" -name '*.png' | wc -l | tr -d ' ') screenshots, index in tasks/evidence/identity/index.md"

if ((status != 0)); then
  printf '\nE2E FAILED (exit %d), see above\n' "$status"
  exit 1
fi
printf '\nIdentity journey passed on %s through %s\n' "$HOST" "$URL"
