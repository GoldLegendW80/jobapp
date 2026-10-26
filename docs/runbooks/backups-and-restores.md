# Backups and restores

`./deploy.sh backup`, the schedule, the 7 days retention, restore and the guards.

Topics: How age is counted, and the one residue; What a run deletes, and what it never does; Two guards, decisions of 2026-10-11; What it costs; An explicit `BACKUP_KEEP` wins.

`./deploy.sh backup` runs `pg_dump -Fc` inside the `db` container and streams it to
`jetson:~/jobapp-backups/jobapp-YYYY-MM-DDTHHMMSS.dump` (UTC time). The dump is written to a hidden
`.partial` file, checked with `pg_restore --list` and a full read into `/dev/null` (which is what
catches a truncated or damaged file), then renamed; the directory is mode 700 and every dump mode
600. Once the dump is verified, and only then, the retention runs: the newest `BACKUP_KEEP` dumps
stay and a dump `BACKUP_KEEP` days old or more is deleted, 7 by default (Retention, below). A
backup is refused when the free disk would drop under the database size plus `BACKUP_MIN_FREE_MB` (100);
a disk that fills up during `pg_dump` makes it fail, and the partial file is removed. Every backup
and restore holds `flock` on `~/jobapp-backups/.lock`, so a second one at the same time is refused
with a clear message. The backup directory lives outside `~/jobapp`, so `rsync --delete` never
touches it; `backups/` and `*.dump` are git-ignored and never rsynced.

`./deploy.sh backup --pull` also copies the dump to `backups/` on the Mac (dir 700, file 600,
sha256 compared on both sides). That copy is the only one off the Jetson's SD card. No retention
ever touches `backups/`: a pulled dump stays on the Mac, with everything the database held that
day, until you delete it.

`./deploy.sh restore <file>` takes a name in `~/jobapp-backups`, a path on the Jetson, `latest`,
or a file on the Mac (uploaded to `~/jobapp-backups/incoming/` first). By default it restores into
the scratch database `jobapp_restore_check`, prints the row count of every table and drops it
(`--keep` leaves it, `./deploy.sh restore --drop-scratch` drops it later). A missing, empty,
foreign, truncated or damaged file is refused before any database is touched. An uploaded dump
stays in `incoming/` under the retention below, like any dump: once its name is 7 days old the
next nightly run deletes it, and restoring the local file again uploads it again.

`./deploy.sh restore <file> --into-live` replaces the live database. It verifies the file, asks you
to type `RESTORE jobapp` (`--yes` skips the question, for scripts; without a terminal and without
`--yes` it refuses), takes a pre-restore backup (`jobapp-...-pre-restore.dump`), stops `api`,
drops and recreates the database, restores (rolling back to the pre-restore dump if `pg_restore`
fails), runs `alembic upgrade head`, starts `api` and checks health. About 15 seconds of 502 on
`/api` for a small database.

Nightly backups: `./deploy.sh backup-schedule install` adds one line to the crontab of the Jetson
user (no sudo; idempotent; other lines kept), `remove` takes it out, `status` shows it, the
retention its runs apply and where that number comes from, the newest and the oldest dump of
each folder, what is left alone there, and the end of `~/jobapp-backups/backup.log`. It runs the
deployed `~/jobapp/ops/db.sh`, so deploy first. The line, at `BACKUP_CRON` (default
`17 3 * * *`, the Jetson's local time):

```
17 3 * * * JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups "$HOME/jobapp/ops/db.sh" backup >>"$HOME/jobapp-backups/backup.log" 2>&1 # jobapp-backup (./deploy.sh backup-schedule)
```

The line names no `BACKUP_KEEP`: each run reads the retention where every backup reads it (next
section), so a new default arrives with a deploy and a new value with an edit of the Jetson's
`.env`, without installing the line again. A line installed before 2026-10-11 still carries
`BACKUP_KEEP=14` and keeps 14 days until `./deploy.sh backup-schedule install` replaces it;
`status` shows which one is there.

## Retention: 7 days

Decided by the owner on 2026-10-10 (SPEC-ingestion.md, Retention): a document a user deletes is
gone from the app at once, and from the server's backups within 7 days. `BACKUP_KEEP` is the
setting that makes the second half true: one number, 7 by default, for two rules that
`ops/db.sh backup` applies once the dump of its run is written and verified.

- **Count:** the newest `BACKUP_KEEP` dumps of the run's folder stay, the others are deleted.
- **Age:** a dump `BACKUP_KEEP` days old or more is deleted, in `~/jobapp-backups` (the nightly
  dumps, the ones made by hand, the pre-restore dumps), in `~/jobapp-backups/deploys` (the
  predeploy dumps) and in `~/jobapp-backups/incoming` (dumps uploaded from the Mac for a restore).
  The backup a deploy takes applies both rules in `deploys/`, and the nightly run applies the age
  rule there again: predeploy dumps have no retention of their own.

**How age is counted, and the one residue.** Age is the number of UTC calendar days between the
date in the dump's own name and the date in the name of the dump the run has just written. It is
never the file's modification time: a copied or touched file is as old as its name says. Whole
days rather than seconds, for two reasons: two nightly runs start a few seconds apart, and a rule
in seconds would keep or delete the dump of 7 nights ago depending on those seconds; and anyone
can check a folder by reading its names. So with 7, a dump taken at any hour of UTC day D is
deleted by the nightly run of day D + 7. The folder of the nightly run then holds at most seven
dumps, and they are seven nights only when nothing else was written there: the count rule counts
every dump of the folder, the ones made by hand and before a restore included, so a day with
several of them shortens how far back the seven reach. A dump taken
later in the UTC day than the nightly run lives less than 7 days. One taken earlier, between
midnight UTC and the end of that run, lives 7 days plus the time that separated it from the end
of the run: the seconds a run takes for the nightly dump itself, and for a dump taken just after
midnight UTC as long as the schedule's hour is after midnight UTC. The schedule is 03:17 in the
Jetson's local time. On the assumption that its time zone is Europe/Paris, which this change did
not check, that is 01:17 UTC in summer and 02:17 UTC in winter, so at most about 1 hour 17
minutes or 2 hours 17 minutes; if the Jetson's clock is on UTC it is 3 hours 17 minutes. For a
user: a document deleted on day D is in no dump of the Jetson once the run of day D + 7 has
ended. That is within 7 days of the deletion, except for a document deleted in those small hours
just after a dump taken in them, which stays 7 days and at most those hours. `BACKUP_KEEP=6`
removes the residue at the price of one night of history.

**What a run deletes, and what it never does.**

- Nothing before the new dump is written, verified and on the disk: its data is flushed (`sync`)
  before it gets its name, and the folder after, so a power cut never finds the old dumps gone
  and the new one empty. A backup that fails (the database down, no disk, `pg_dump`, one of the
  two verifications or a flush failing, another backup holding the lock) deletes nothing and
  exits non zero. After ten days switched off, the first run writes its dump, then deletes the
  old ones, never the reverse.
- Never the dump the run has just written and verified, the newest verified dump on the server.
  No folder has an exception of its own: the last predeploy dump goes at 7 days like any other,
  even when no deploy has replaced it, since a newer verified dump exists by then.
- Never under a restore or another backup. Every backup and restore holds the lock of the folder
  it is run for (`~/jobapp-backups`, or `deploys/` for the deploy's backup and for a restore run
  with `BACKUP_DIR` set to it), the nightly run skips `deploys/` while that folder's lock is held
  (the next run deletes what is too old there), a dump being written is a hidden `.partial` file
  until it is verified, and a restore never deletes. A lock file that cannot be opened is said as
  that, and the run ends non zero. `ops/db.sh prune` applies the count rule alone.
- Only a regular file named exactly `jobapp-YYYY-MM-DDTHHMMSS[-tag].dump`, with a date that
  exists, directly in one of the three folders, one full path at a time. Anything else there (an
  upload under another name, a `.partial` left by a killed run, a folder, a symbolic link, which
  is neither followed nor removed) is left alone and listed at every run. A backup folder that is
  itself a symbolic link gets its dump and no deletion at all, however its name is written (a
  slash or a `/.` after it is trimmed before anything looks at it), and every run says so. A dump
  whose name is ahead of the run's date (a clock that was wrong) is kept until that date has
  passed, and said.

**Two guards, decisions of 2026-10-11** (after the review of pull request #101). A verified dump
is one that reads to its end. It says nothing of what the database held when it was taken, and
a rule by age deletes every other dump in one run after a week without a backup.

- **A run never leaves the server with its own dump alone.** When every other dump would go in
  the same run (the Jetson off for a week, a week of failed nights, a clock ahead), the newest of
  them that is not an empty file, by the time in its name, stays for one more run, and the run
  says which and why. The next run that leaves another dump beside its own deletes it. The
  residue is one run more than 7 days for that one dump, in that case only: one night when the
  next nightly run succeeds, longer when it fails or when a deploy's backup, which sees its own
  folder alone, comes in between. (`BACKUP_KEEP=1` therefore keeps two dumps.)
- **A dump less than half the size of the last one accepted in its folder deletes nothing.** A
  database that lost its data still dumps, and the dump still verifies. The run keeps its dump,
  deletes nothing in any folder, prints a warning with both sizes, and ends well, so a deploy is
  not stopped by it. Every later run of that folder is measured against that last accepted
  dump, not against the small ones, and deletes nothing either, until a dump is at least half
  its size again; `ops/db.sh prune` is refused meanwhile. A run is never measured against a
  pre-restore dump, an empty file or a dump named after it, and each run prints which dump it
  was measured against. When the database was meant to shrink, or was restored to an older and
  smaller state, one backup accepts it, for that run alone:
  `BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup` (for the predeploy folder, add
  `BACKUP_DIR=jobapp-backups/deploys`; left alone, that one ends by itself within 7 days, when
  the nightly run has deleted the dump it is measured against).
  **This hold delays the 7 days, and it has no bound but the disk.** The very thing the 7 days
  are about can cause it: a user who deletes more than half of what the database holds. Nothing
  lifts it but a larger dump or the owner's command, and the folder grows by one dump at every
  run until the disk is full. So it is made to be seen, not left in a log:
  `./deploy.sh backup-schedule status` ends with a non zero status while a hold stands, with
  both sizes, the number of runs held, since which dump, and the command that lifts it; from the
  seventh run held the line is an ERROR; and a deploy whose backup is held carries the line as
  an annotation on the summary page of its run. The hold itself is a small file, `.size-hold`,
  in the folder: removing it by hand ends the hold, and anything there that is not a plain file
  stops every deletion until it is removed.

**For the 7 days to be true on the Jetson**, all of this must hold. `./deploy.sh backup-schedule
status` shows each point, and every nightly run writes the same lines to `backup.log`. What
counts in its output is what stands above "last lines of": the state now. The lines under it,
each marked with a bar, are those of the last run as it was then, so a WARNING there (about 14,
from the night before a reinstall) is the past.

1. The schedule is installed, by a release that has this retention, and `status` says
   `BACKUP_KEEP=7`. A line installed before 2026-10-11 carries `BACKUP_KEEP=14` and keeps 14
   days until it is installed again.
2. No `BACKUP_KEEP` above 7, in the Jetson's `~/jobapp/.env` or in the cron line. `status` names
   where the number comes from and prints a warning above 7, as every backup does.
3. The nightly backup succeeds. A run that fails deletes nothing, so every deletion waits for the
   next run that works: `status` gives the age of the newest and of the oldest dump of each
   folder, warns after two days without a verified backup, and marks a dump past the limit.
4. No hold on the rotation (the second guard above): `status` ends with a non zero status while
   one stands, and nothing is deleted until it is lifted.
5. No copy of the data sits under a name the rotation does not delete or in a place it does not
   look. A file of the backup folders that is not named like a dump, and the scratch database
   `jobapp_restore_check` left by `restore --keep` (a whole copy of a dump inside Postgres, until
   `./deploy.sh restore --drop-scratch`), are reported by `status` and by every run, and never
   deleted by them. A dump copied anywhere else on the Jetson is seen by nothing: so are the
   whole dumps of the live database that `tests/backup_test.sh` leaves in
   `~/jobapp-backups-test*` when it is killed half way (it removes them when it ends).
6. Outside the rotation by nature, so outside the 7 days: the dumps pulled to the Mac
   (`backups/`), which stay until you delete them; any copy of the database volume
   `jobapp_pgdata` (an archive, a snapshot, an image of the Jetson's disk); and a restore, which
   brings back whatever was deleted after its dump was taken.

**What it costs.** After 7 days no restore point on the Jetson predates a bad change: data
damaged by a migration or a bug more than 7 days ago cannot be recovered from the server's dumps,
predeploy dumps included (until 2026-10-11 the newest 10 of those stayed whatever their age, and
14 nightly ones). In so many words: the first backup that works after 7 days without one deletes
every other dump but the newest of them, in the three folders, in that one run, and that last
one at the run after. A dump pulled to the Mac in time is the only older copy.

**An explicit `BACKUP_KEEP` wins, for both rules:** 14 keeps 14 dumps and 14 days. The promise
is 7 days whatever the setting, so a value above 7 breaks it knowingly, and every backup then
prints a warning. Where the number is read, first found wins: the environment of the run (a
`BACKUP_KEEP` set on the Mac, in the shell or in `.env`, is sent with every `./deploy.sh backup`
and written into the cron line by `backup-schedule install`), then the `BACKUP_KEEP` line of the
Jetson's `~/jobapp/.env` (read as compose reads it, so also when it starts with `export`), then
the default of `ops/db.sh`. A value that is not a whole number from 1 to 9999 is refused by every
backup and by `prune`, with the place it was read from, and `status` shows that refusal; a deploy
then stops at its predeploy backup until the line is put right. `backups`, `restore` and
`restore --into-live` use no retention: they print the warning and do their work.

```bash
./deploy.sh backup-schedule status   # the line, BACKUP_KEEP and its source, the newest and oldest dump of each folder, what is left alone
./deploy.sh backups                  # every dump on the Jetson: the date in each name is what the retention reads
```
