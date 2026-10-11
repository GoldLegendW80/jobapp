#!/usr/bin/env bash
# Backup and restore tests against the Jetson (platform task 9), run from the Mac. Rerunnable;
# everything it creates is removed at the end, whatever failed.
#
#   tests/backup_test.sh                    round trip into the scratch database and every refusal
#   BACKUP_TEST_LIVE=1 tests/backup_test.sh also ./deploy.sh restore --into-live end to end: takes a
#                                           real backup first, stops the api for a few seconds
#
# Seed data lives in a schema `restore_check` of the live database (pg_dump only dumps that
# database), created at the start and dropped at the end; the app's own tables are never written.
# Test dumps go to ~/jobapp-backups-test*, never to the real ~/jobapp-backups (except the safety
# backup of the live run). Covers: unicode, a 1 MB text, NULLs, empty values, binary bytes, 1000
# bulk rows, checksums before and after, missing, empty, garbage, truncated, damaged and plain SQL
# files refused, a refused live restore leaving the api untouched, upload of a local file, the
# lock against concurrent runs, retention keeping exactly N, retention by age read from the name
# (T10: copies of a dump under names 9 and 3 days old, in the folder, deploys/ and incoming/; a
# refused backup deleting none of them), the schedule line without a retention of its own and
# `backup-schedule status` reporting the one its runs apply, the disk space check, file modes.
# The T10 checks need the release with T10 deployed (status asks the ops/db.sh on the Jetson) and
# no BACKUP_KEEP in the .env of this repository or of the Jetson: they assert the default, 7.
set -euo pipefail
unset BACKUP_KEEP

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
LIVE="${BACKUP_TEST_LIVE:-0}"
TDIR=jobapp-backups-test
RETDIR=jobapp-backups-test-retention
export DEPLOY_HOST="$HOST" BACKUP_DIR="$TDIR"

WORK="$(mktemp -d)"
CTL="$(mktemp -d /tmp/jobapp-bt.XXXXXX)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=auto -o "ControlPath=$CTL/%C"
  -o ControlPersist=120 "$HOST")
r() { "${SSH[@]}" "$@"; }
PULLED=()
HOLDER=""
CRON_SAVED=0
cleanup() {
  [[ -z "$HOLDER" ]] || kill "$HOLDER" 2>/dev/null || true
  # The user's crontab exactly as it was before the backup-schedule step.
  ((CRON_SAVED == 0)) || r "crontab -" <"$WORK/crontab.orig" || true
  r "cd $RDIR && docker compose -f compose.yml exec -T db psql -X -q -U \$(sed -n 's/^POSTGRES_USER=//p' .env | tail -n 1) -d postgres \
    -c 'drop database if exists jobapp_restore_check with (force)' >/dev/null 2>&1;
    docker compose -f compose.yml exec -T db psql -X -q -U \$(sed -n 's/^POSTGRES_USER=//p' .env | tail -n 1) \
    -d \$(sed -n 's/^POSTGRES_DB=//p' .env | tail -n 1) -c 'drop schema if exists restore_check cascade' >/dev/null 2>&1;
    docker compose -f compose.yml start api >/dev/null 2>&1; rm -rf ~/$TDIR ~/$RETDIR" || true
  local f
  for f in ${PULLED[@]+"${PULLED[@]}"}; do rm -f "$f"; done
  ssh -o "ControlPath=$CTL/%C" -O exit "$HOST" >/dev/null 2>&1 || true
  rm -rf "$WORK" "$CTL"
}
trap cleanup EXIT

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
# run <expected exit> <cmd...>: output in $WORK/out
run() {
  local want="$1" got=0
  shift
  "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "$* exited $got, expected $want: $(tail -c 600 "$WORK/out")"
}
out_has() { grep -q -- "$1" "$WORK/out" || fail "output lacks '$1': $(tail -c 600 "$WORK/out")"; }

renv() { r "sed -n 's/^$1=//p' $RDIR/.env | tail -n 1"; }
PGU="$(renv POSTGRES_USER)"
PGDB="$(renv POSTGRES_DB)"
# sql <database>: run stdin in psql on the Jetson, tuples only
sql() { r "cd $RDIR && docker compose -f compose.yml exec -T db psql -X -q -tA -v ON_ERROR_STOP=1 -U $PGU -d $1"; }
CHECKSUM="select count(*) || ':' || md5(string_agg(t::text, E'\\n' order by id)) from restore_check.seed t"
backup_path() { sed -n 's/^backup=//p' "$WORK/out" | tail -n 1; }
api_started() { r "docker inspect -f '{{.State.StartedAt}}' \$(cd $RDIR && docker compose -f compose.yml ps -q api)"; }
dumps_in() { r "find ~/$1 -maxdepth 1 -type f -name 'jobapp-*.dump' | sort"; }
ndumps() { r "find ~/$1 -maxdepth 1 -type f -name 'jobapp-*.dump' | wc -l"; } # counted on the Jetson: no padding

if [[ "$LIVE" == 1 ]]; then
  step "safety backup of the live database, before any seed, into the real ~/jobapp-backups"
  run 0 env BACKUP_DIR=jobapp-backups ./deploy.sh backup
  ok "$(backup_path)"
fi

step "seed: schema restore_check in the live database $PGDB"
sql "$PGDB" <<'SQL'
set client_min_messages = warning;
drop schema if exists restore_check cascade;
create schema restore_check;
create table restore_check.seed (
  id int primary key, label text, body text, data bytea, doc jsonb, n numeric, at timestamptz
);
insert into restore_check.seed values
  (1, 'café ñ 漢字 🚀 עברית Ελληνικά', repeat('Ωμέγα 漢字 🚀 ', 45000),
   (select decode(string_agg(md5(random()::text), ''), 'hex') from generate_series(1, 20000)),
   '{"name": "Zoë 😀", "nested": {"k": [1, null, "ü"]}}', 3.14, '2026-10-08 12:00:00+02'),
  (2, null, null, null, null, null, null),
  (3, '', '', '\x', '{}', 0, 'epoch'),
  (4, E'quote '' double " backslash \\ tab \t newline \n end', E'line1\nline2\r\n',
   decode('00ff00ff00', 'hex'), '[]', 12345678901234567890.123456789, 'infinity');
insert into restore_check.seed
  select g, 'row ' || g, md5(g::text), null, jsonb_build_object('g', g), g / 7.0, now()
  from generate_series(5, 1004) g;
SQL
BODY_LEN="$(sql "$PGDB" <<<"select octet_length(body) from restore_check.seed where id = 1")"
((BODY_LEN >= 1000000)) || fail "body is only $BODY_LEN bytes"
SUM0="$(sql "$PGDB" <<<"$CHECKSUM")"
ok "1004 rows: unicode, a $BODY_LEN byte text, 320 KB of random bytes, NULLs, empty values, escapes; checksum $SUM0"

step "backup into ~/$TDIR, with --pull"
run 0 ./deploy.sh backup --pull
B1="$(backup_path)"
[[ "$B1" =~ /$TDIR/jobapp-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}\.dump$ ]] || fail "unexpected name: $B1"
out_has "verified: pg_restore --list"
ok "named $(basename "$B1"), verified with pg_restore --list and a full read"
modes="$(r "stat -c %a ~/$TDIR $B1")"
[[ "$modes" == $'700\n600' ]] || fail "modes: $modes"
[[ -z "$(r "find ~/$TDIR -name '*.partial'")" ]] || fail "a .partial file was left"
ok "directory 700, dump 600, no .partial left"
LOCAL_B1="backups/$(basename "$B1")"
PULLED+=("$ROOT/$LOCAL_B1")
[[ -f "$LOCAL_B1" && "$(stat -f %Lp "$LOCAL_B1")" == 600 && "$(stat -f %Lp backups)" == 700 ]] ||
  fail "pulled copy missing or wrong mode"
git check-ignore -q "$LOCAL_B1" || fail "$LOCAL_B1 is not git-ignored"
rsync -an --exclude-from=.rsyncignore --out-format='%n' ./ "$WORK/rs/" | grep -q '^backups/' && fail "backups/ would be rsynced"
ok "--pull: $LOCAL_B1 mode 600 in a 700 directory, git-ignored, excluded from rsync"

step "restore into the scratch database: checksums match"
run 0 ./deploy.sh restore "$(basename "$B1")" --keep
out_has "restore_check.seed *| *1004"
out_has "public.alembic_version *|"
SUM1="$(sql jobapp_restore_check <<<"$CHECKSUM")"
[[ "$SUM1" == "$SUM0" ]] || fail "scratch checksum $SUM1 differs from $SUM0"
ok "row counts printed; checksum identical in jobapp_restore_check ($SUM1)"
[[ "$(sql "$PGDB" <<<"$CHECKSUM")" == "$SUM0" ]] || fail "the live data changed"
ok "live database untouched"
run 0 ./deploy.sh backup
out_has "left alone: the scratch database jobapp_restore_check exists"
ok "T10: while restore --keep leaves the scratch database, a whole copy of a dump, every backup says so"
run 0 ./deploy.sh restore --drop-scratch
[[ "$(sql postgres <<<"select count(*) from pg_database where datname = 'jobapp_restore_check'")" == 0 ]] ||
  fail "scratch database still there"
run 0 ./deploy.sh restore latest
out_has "dropped jobapp_restore_check"
[[ "$(sql postgres <<<"select count(*) from pg_database where datname = 'jobapp_restore_check'")" == 0 ]] ||
  fail "scratch database left without --keep"
ok "--drop-scratch drops it; without --keep the scratch database is dropped after the counts; latest resolves"

step "a local file is uploaded, then restored"
run 0 ./deploy.sh restore "$ROOT/$LOCAL_B1"
out_has "restore_check.seed *| *1004"
[[ "$(r "stat -c %a ~/$TDIR/incoming ~/$TDIR/incoming/$(basename "$B1")")" == $'700\n600' ]] || fail "incoming modes"
ok "uploaded to ~/$TDIR/incoming (700, file 600), checksum compared, restored"

step "refusals: missing, empty, garbage, truncated, damaged, plain SQL, directory"
r "cd ~/$TDIR && size=\$(stat -c %s $B1) && head -c \$((size * 6 / 10)) $B1 > truncated.dump &&
  head -c 30 $B1 > header-only.dump && : > empty.dump && head -c 4096 /dev/urandom > garbage.dump &&
  cp $B1 damaged.dump && dd if=/dev/zero of=damaged.dump bs=1 count=256 seek=\$((size * 7 / 10)) conv=notrunc status=none &&
  printf 'select 1;\n' > plain.sql.dump && mkdir -p adir.dump"
for c in "nope.dump|no such backup" "empty.dump|empty file, not a backup" \
  "garbage.dump|not a valid pg_dump custom-format archive" "header-only.dump|not a valid pg_dump" \
  "plain.sql.dump|not a valid pg_dump custom-format archive" "adir.dump|not a regular file" \
  "truncated.dump|corrupt or truncated backup, refused\\|end of file" "damaged.dump|corrupt or truncated backup, refused"; do
  f="${c%%|*}"
  want="${c#*|}"
  run 1 ./deploy.sh restore "$f"
  out_has "$want"
  ok "$f: $(grep -o 'db.sh: .*' "$WORK/out" | head -n 1 | cut -c1-110)"
done
[[ "$(sql postgres <<<"select count(*) from pg_database where datname = 'jobapp_restore_check'")" == 0 ]] ||
  fail "a refused file created the scratch database"
ok "no refused file reached pg_restore into a database"
head -c 5000 "$LOCAL_B1" >"$WORK/local-truncated.dump"
run 1 ./deploy.sh restore "$WORK/local-truncated.dump"
out_has "not a valid pg_dump\|truncated"
ok "a truncated local file is uploaded, then refused"
run 1 ./deploy.sh restore "$WORK/does-not-exist/x.dump"
out_has "no such backup"
ok "a missing local path is looked up on the Jetson and refused there"

before="$(api_started)"
run 1 ./deploy.sh restore truncated.dump --into-live --yes
out_has "refused, nothing changed"
[[ "$(api_started)" == "$before" ]] || fail "the api was restarted by a refused live restore"
[[ "$(sql "$PGDB" <<<"$CHECKSUM")" == "$SUM0" ]] || fail "live data changed"
[[ -z "$(r "find ~/$TDIR -name '*pre-restore*'")" ]] || fail "a pre-restore backup was taken for a refused file"
ok "--into-live with a truncated file: refused before anything, api not restarted, live data intact"

step "lock: one backup or restore at a time"
# The holder keeps the lock 12s; the two refused commands below take a few seconds.
r "flock ~/$TDIR/.lock sleep 12" &
HOLDER=$!
sleep 3
run 1 ./deploy.sh backup
out_has "another backup or restore is running on this host"
run 1 ./deploy.sh restore "$(basename "$B1")"
out_has "another backup or restore is running"
wait "$HOLDER" || fail "the lock holder failed"
HOLDER=""
ok "backup and restore refused while the lock is held"
n0="$(ndumps "$TDIR")"
pids=()
for i in 1 2 3; do
  ./deploy.sh backup >"$WORK/par$i" 2>&1 &
  pids+=($!)
done
okn=0
for i in 1 2 3; do
  if wait "${pids[$((i - 1))]}"; then
    okn=$((okn + 1))
  else
    grep -q "another backup or restore is running" "$WORK/par$i" || fail "parallel run $i failed otherwise: $(tail -c 300 "$WORK/par$i")"
  fi
done
n1="$(ndumps "$TDIR")"
((okn >= 1 && n1 - n0 == okn)) || fail "$okn successes but $((n1 - n0)) new dumps"
[[ -z "$(r "find ~/$TDIR -name '*.partial'")" ]] || fail ".partial left by parallel runs"
ok "3 backups at once: $okn succeeded, the others refused by the lock, $((n1 - n0)) new dump(s), no partial file"

step "retention keeps exactly BACKUP_KEEP"
for i in 1 2 3 4 5; do
  run 0 env BACKUP_DIR="$RETDIR" BACKUP_KEEP=3 ./deploy.sh backup
  printf '%s\n' "$(backup_path)" >>"$WORK/made"
done
kept="$(dumps_in "$RETDIR")"
[[ "$(wc -l <<<"$kept" | tr -d ' ')" == 3 ]] || fail "kept: $kept"
[[ "$kept" == "$(tail -n 3 "$WORK/made")" ]] || fail "not the newest three: $kept"
out_has "deleted old backup"
ok "5 backups with BACKUP_KEEP=3: exactly the newest 3 left"

step "retention by age (T10): the date in a dump's name decides, in the folder, deploys/ and incoming/"
# Copies of a real dump under names 9 and 3 days before today (UTC, on the Jetson's clock). Their
# mtime is now: only the name says they are old. No BACKUP_KEEP is given, so the default applies.
OLD="$(r "date -u -d '9 days ago' +%Y-%m-%dT%H%M%S")"
RECENT="$(r "date -u -d '3 days ago' +%Y-%m-%dT%H%M%S")"
[[ "$OLD" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}$ && "$RECENT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}$ ]] ||
  fail "could not read dates on the Jetson: '$OLD' '$RECENT'"
last="$(tail -n 1 "$WORK/made")"
r "cd ~/$RETDIR && mkdir -p deploys incoming &&
  cp $last jobapp-$OLD.dump && cp $last jobapp-$RECENT.dump &&
  cp $last deploys/jobapp-$OLD-predeploy-aaaaaaaaaaaa.dump && cp $last deploys/jobapp-$RECENT-predeploy-bbbbbbbbbbbb.dump &&
  cp $last incoming/jobapp-$OLD.dump && cp $last incoming/notes.dump"
run 0 env BACKUP_DIR="$RETDIR" ./deploy.sh backup
NEWEST="$(backup_path)"
out_has "BACKUP_KEEP=7, the default of ops/db.sh"
left="$(r "cd ~/$RETDIR && find . -type f -name '*.dump' | sort")"
grep -q "jobapp-$OLD" <<<"$left" && fail "a dump named 9 days ago is still there: $left"
for f in "./jobapp-$RECENT.dump" "./deploys/jobapp-$RECENT-predeploy-bbbbbbbbbbbb.dump" "./incoming/notes.dump" "./$(basename "$NEWEST")"; do
  grep -qxF "$f" <<<"$left" || fail "$f is gone: $left"
done
[[ "$(grep -c '^\./jobapp-' <<<"$left")" == 5 ]] || fail "the folder of the run does not hold the 3 of before, the copy of 3 days ago and the new one: $left"
out_has "deleted old backup jobapp-$OLD.dump ("
out_has "deleted old backup jobapp-$OLD-predeploy-aaaaaaaaaaaa.dump ("
out_has "incoming, not a dump of this script: notes.dump"
ok "default 7: the copies named 9 days ago are deleted in the folder, deploys/ and incoming/ although written just now; those named 3 days ago stay; a file that is not named like a dump is left alone and reported"

run 0 ./deploy.sh backups
out_has "retention: BACKUP_KEEP=7"
ok "./deploy.sh backups ends with the retention in force"

step "backup-schedule: install twice, other crontab lines kept, the line runs like cron, remove"
r "crontab -l 2>/dev/null || true" >"$WORK/crontab.orig"
CRON_SAVED=1
MARK="jobapp-backup (./deploy.sh backup-schedule)"
r "{ crontab -l 2>/dev/null; echo '# jobapp-test: a foreign line that must survive'; } | crontab -"
run 0 ./deploy.sh backup-schedule install
run 0 ./deploy.sh backup-schedule install
ct="$(r 'crontab -l')"
[[ "$(grep -cF "$MARK" <<<"$ct")" == 1 ]] || fail "not exactly one jobapp line: $ct"
grep -q '^# jobapp-test: a foreign line' <<<"$ct" || fail "a foreign crontab line was lost: $ct"
ok "installed twice, one line: $(grep -F "$MARK" <<<"$ct")"
# T10: the line names no retention, so its runs read the Jetson's .env, then the default of the
# ops/db.sh deployed there. (A BACKUP_KEEP in the .env of this repository would be written in.)
grep -F "$MARK" <<<"$ct" | grep -q "BACKUP_KEEP=" &&
  fail "the installed line carries a BACKUP_KEEP: is one set in the .env of this repository? $ct"
run 0 ./deploy.sh backup-schedule status
out_has "installed: 17 3 \* \* \*"
out_has "the line carries no BACKUP_KEEP"
grep -q "retention: BACKUP_KEEP=7 (the default of ops/db.sh)" "$WORK/out" ||
  fail "status does not report 7 from the default of the deployed ops/db.sh (is the release with T10 deployed, and the Jetson's .env without a BACKUP_KEEP?): $(tail -c 900 "$WORK/out")"
ok "the line carries no BACKUP_KEEP, and status reports what its runs apply: 7, the default of the ops/db.sh deployed on the Jetson"
# The command part of the line, run the way cron runs it: sh, PATH=/usr/bin:/bin, HOME only.
n0="$(ndumps "$TDIR")"
cmd="$(grep -F "$MARK" <<<"$ct" | cut -d' ' -f6-)"
r "env -i HOME=\$HOME LOGNAME=\$USER PATH=/usr/bin:/bin sh -c $(printf '%q' "$cmd")"
[[ "$(ndumps "$TDIR")" == $((n0 + 1)) ]] || fail "the cron command made no dump"
r "grep -q '^backup=' ~/$TDIR/backup.log" || fail "backup.log lacks the result: $(r "tail -n 5 ~/$TDIR/backup.log")"
ok "the cron command, under cron's bare environment, made a verified dump and logged to backup.log"
run 0 ./deploy.sh backup-schedule remove
ct="$(r 'crontab -l')"
! grep -qF "$MARK" <<<"$ct" || fail "line still there after remove"
grep -q '^# jobapp-test: a foreign line' <<<"$ct" || fail "remove lost a foreign line"
run 0 ./deploy.sh backup-schedule remove
run 0 ./deploy.sh backup-schedule status
out_has "not installed"
ok "remove (twice) deletes only the jobapp line; status says not installed"

step "disk space check"
# T10: a copy named 9 days ago is there when the backup is refused. A backup that fails deletes
# nothing, so it must still be there afterwards.
r "cp $B1 ~/$TDIR/jobapp-$OLD.dump"
n0="$(ndumps "$TDIR")"
run 1 env BACKUP_MIN_FREE_MB=100000000 ./deploy.sh backup
out_has "not enough disk for a backup"
[[ "$(ndumps "$TDIR")" == "$n0" && -z "$(r "find ~/$TDIR -name '*.partial'")" ]] || fail "a file was written"
r "test -f ~/$TDIR/jobapp-$OLD.dump" || fail "a refused backup deleted a dump past the limit"
ok "refused before pg_dump when free space is short, nothing written, and nothing deleted: the dump named 9 days ago is still there"
r "rm -f ~/$TDIR/jobapp-$OLD.dump"

if [[ "$LIVE" == 1 ]]; then
  step "live restore, end to end"
  head="$(sql "$PGDB" <<<"select version_num from alembic_version")"
  sql "$PGDB" <<<"delete from restore_check.seed where id = 3; insert into restore_check.seed (id, label) values (99999, 'after the backup');"
  SUM_MUT="$(sql "$PGDB" <<<"$CHECKSUM")"
  [[ "$SUM_MUT" != "$SUM0" ]] || fail "mutation did not change the checksum"
  before="$(api_started)"
  start=$SECONDS
  run 0 ./deploy.sh restore "$(basename "$B1")" --into-live --yes
  out_has "pre-restore backup"
  out_has "alembic upgrade head"
  out_has '/api/health: {"status":"ok"}'
  ok "restore --into-live --yes in $((SECONDS - start))s: pre-restore backup, api stopped, restore, migrations, health ok"
  [[ "$(sql "$PGDB" <<<"$CHECKSUM")" == "$SUM0" ]] || fail "live checksum after restore differs"
  [[ "$(sql "$PGDB" <<<"select version_num from alembic_version")" == "$head" ]] || fail "alembic head changed"
  [[ "$(api_started)" != "$before" ]] || fail "the api was not restarted"
  ok "the seed is back byte for byte ($SUM0), the mutation is gone, alembic at $head, api restarted"
  pre="$(r "find ~/$TDIR -maxdepth 1 -name 'jobapp-*-pre-restore.dump'" | tail -n 1)"
  [[ -n "$pre" ]] || fail "no pre-restore backup"
  run 0 ./deploy.sh restore "$(basename "$pre")" --keep
  [[ "$(sql jobapp_restore_check <<<"$CHECKSUM")" == "$SUM_MUT" ]] || fail "pre-restore backup lacks the mutated state"
  run 0 ./deploy.sh restore --drop-scratch
  ok "the pre-restore backup holds the state just before the restore ($(basename "$pre"))"
  run 0 ./deploy.sh smoke
  ok "./deploy.sh smoke passes after the live restore"
fi

printf '\nAll %d checks passed.\n' "$passed"
