# What a CI run does

The three jobs of a `ci.yml` run, the measured timings, the plan of what a pull request has to run, the secret scan.

Topics: Measured, before and after; The plan: what a pull request has to run; The secret scan; A fresh run for an open pull request.

Three jobs (issue #91), and the run is green when the three are. The split follows what was
measured on the Mac's runners on 2026-10-10 with the eleven jobs CI had until then, on two
runners: 18.1 to 20.0 minutes of runner time in four runs (22.8 in a fifth, with a first
download of Chromium that stalled for 220 s), and 13.3 and 14.3 minutes from push to result
for the two runs that had the runners to themselves (38072303967, 38076641000). Three findings
shaped the new layout.

- **A job costs about 25 s before its first test**: set up 3 s, checkout 4 s, pnpm and node 5 to
  14 s, service containers 10 s, the hooks and post steps 5 s. Eleven jobs paid it eleven times,
  for jobs as short as 8 s of pre-commit. Three jobs pay it three times.
- **GitHub starts queued jobs in no particular order.** With more jobs than runners a 20 s job
  took a runner while a 4 minute job waited, so the run ended minutes after it had to. Three
  jobs for three runners all start at once; there is no order left to get wrong.
- **The e2e groups wait, the api tests compute.** A Playwright group used about 0.6 of its 3
  CPUs, the api tests 2.1 to 3. So the fast, CPU bound checks sit in front of the e2e groups in
  two of the jobs, and the api suite has the third to itself.

| job | what runs, in this order | the same steps in the old jobs | now (run 38098708076) |
|---|---|---|---|
| `api` | ruff; the whole pytest suite in one run on 3 workers; the coverage floor (95 percent of that run's data); the docker marked tests; the scoring timing test | pytest 101 s and 146 s in two shards, docker tests 18 s, service containers 10 s | 285 s: pytest 242 s, docker tests 18 s, service containers 5 s |
| `checks` | pre-commit and the secret scan of the run's commits; shellcheck and every `tests/*.sh` of CI, the e2e groups guard, the plan's own test; then the e2e group apps, then the e2e group ingestion (since 2026-10-11), each on a scratch stack of its own | pre-commit 8 s, shell 46 s, apps 173 s of journeys, stack up in 12 s and down in 11 s | 301 s: pre-commit 11 s, the secret scan under 1 s, shell 66 s, apps 185 s, stack up and migrated in 8 s, down in 1 s |
| `journeys` | eslint, tsc, build; Vitest in one run; the e2e group identity, then the e2e group sweep, each on a scratch stack of its own | web checks 29 s, Vitest 34 s and 25 s in two shards, identity 114 s, sweep 68 s | 311 s: web checks 32 s, Vitest 63 s, identity 114 s, sweep 63 s, each stack up in 5 to 6 s |

Which job holds which unit follows from its lengths: the api tests fill their job, the shell
unit sits in `checks`, and the web checks moved from `checks` to `journeys` after the first run
(255, 320 and 262 s there). Moving a unit is a cut and paste of its steps and one line of
`LANES` in `ci_plan.py`.

The e2e group ingestion (the documents of the profile, `web/e2e/ingestion-*.spec.ts`) joined on
2026-10-11, after the table above was measured, so its seconds are not in it. It is a block of
steps of the e2e unit at the end of `checks`, on a second scratch stack that starts once the
apps stack is gone: of the two jobs that already build the images, `checks` was the shorter in
two of the three runs above. As a job of its own in the old layout it took 79 s, set up
included. Its step names a Playwright config, not spec files: the config runs every
`ingestion-*.spec.ts`, so a task of that module adds a spec without touching the workflow.

**Measured, before and after** (2026-10-10 and 11, two runners online, each run started when no
other run was queued unless said; runner time is the sum of the jobs):

| case | before: eleven jobs | now: three jobs |
|---|---|---|
| a code pull request, everything runs | 13.3 and 14.3 min from start to result; 18.1 to 20.0 min of runner time in four runs | 8.7, 9.4 and 10.0 min; 13.3, 14.1 and 14.9 min of runner time (38080515377 twice, 38098708076). A fourth run took 11.9 and 22.8 min: GitHub's downloads stalled for 9 of them (a checkout of 394 s, a set up of 154 s) |
| documentation only (`tasks/`) | everything: 19.1 min of runner time (#38, run 38073436439) | 44, 44 and 45 s; 0.9 min of runner time (38080574728, three times) |
| the README alone | everything | 1.7 min; 1.9 min of runner time (38096293562) |
| `web/` alone | everything | 5.7 min; 10.8 min of runner time (38096406836) |
| a push to `main` (now: a manual run on the branch, the same plan) | 14.3 min; 20.0 min of runner time (38076641000) | 9.1, 9.2 and 9.4 min; 13.4, 13.9 and 14.7 min of runner time (38102580794, 38102061062, 38101529093) |
| a pull request against another branch than `main` | everything | everything, by rule: 9.1 min; 13.8 min of runner time (38103107022, the probe #94 that ran pre-commit alone before the rule: one file under `tasks/`) |

Per job, in seconds, the same steps in their old jobs and in their new one: `api` 336 (api
(jobs) 129, api (rest) 190, api coverage 17) against 260, 266 and 285; `checks` 299 (pre-commit
20, shell 57, e2e (apps) 222) against 281, 284 and 301; `journeys` 430 to 510 (web checks 52,
Vitest 66 and 47, e2e (identity) 170 to 200, e2e (sweep) 92 to 144) against 258, 294 and 311.
With two runners two jobs start at once and the third waits for the first to end, 258 to 289
s, which is the whole queue time of a run; with the third runner all three start together and
a run takes as long as its longest job, about 5 minutes (not measured: the third runner is not
installed yet). During the full runs the load average of the Mac peaked between 4.0 and 8.0
with agents at work beside CI (11.6 once, during a 44 s documentation run, so not CI's doing),
a runner's memory between 2.0 and 3.5 GB, each runner used 0.3 to 1.6 of its 3 CPUs on average
over a run, and the free disk space did not move by more than 1 GB across any run. One more
manual run of the eleven jobs, on a branch at `main`'s head with the
runners to itself, took 20.1 min and 34.6 min of runner time (38081748125): 11 of those minutes
were two stalled downloads from GitHub, the kind of delay the layout cannot remove and three
jobs meet three times instead of eleven.

There is no fourth job that sums the three up. The first version had one, a gate that needed
the three and checked their results: created when they ended, it queued behind every job other
runs had queued meanwhile and waited 8 minutes for a runner to do 7 seconds of work, so the run
that was done after 9.7 minutes reported after 17.9. Nothing is lost without it: no job has a
condition or waits for another, so none can be skipped (`ci_plan_test.py` and
`tests/ci_runners_test.sh` fail if one gets either), a failed job fails the run, and each job
checks its own steps against the plan (below).

What changed inside the jobs, each with its measured effect:

- **One pytest run instead of two shards and a combining job.** Every test is collected once by
  construction (`tests`, split only by the docker marker), the floor is read from the run's own
  data, and no artifact travels between runners. Sharding by measured duration was the other
  option: it needs a second set of service containers, a durations file to maintain, a partition
  test and the combining job (17 s), and the api job is not the longest of the three, so it
  would not end the run a second earlier.
- **The api tests themselves** (api/tests/identity/conftest.py, api/tests/pg.py). Of 139 identity
  tests, 80 percent of the time was the fixture around each test, not the test. `alembic upgrade
  head` ran before every test although the schema was already at head: now the version table is
  asked first. The TRUNCATE after every test took 87 ms on a Postgres that syncs to disk: the
  test server runs without fsync (its data is thrown away), 38 ms. The fixture opened two
  connections per test: it keeps one. On the Mac the 139 tests went from 33.1 s to 20.1 s. The
  whole suite on a runner is still about four minutes of 3 CPUs when the other runners are
  busy: what is left is mostly the next item.
- **Scratch stacks start and stop faster.** `up --wait` waited for the first health probe of
  each service, one interval after its start (5 to 15 s); the CI overlay
  (`tests/ats-stub.compose.yml`) probes every second while a service starts: on the runners a
  stack was up in 11 to 13 s before and is up in 5 s now. `down` waited for containers to stop
  that are thrown away with their volumes, 11 s per stack: with `--timeout 0`, 1 s. The same
  probing on the service containers of the api job: 10 s before, 4 s now.
- **Images are built in each runner's own Docker daemon, not once per run.** Measured: 22 s on
  a runner that never built them, under 2 s after. Sharing them through a registry or a saved
  archive would cost more than the 2 s it replaces.
- **Not done, with the reason.** Two e2e groups at once in one job (they would fit the CPUs):
  about 25 s saved for two stacks in a 4 GB runner and interleaved logs. One CPU set shared by
  the runners, so that the api tests could take what the e2e groups leave idle: about 25 s
  again, and the journeys would compete with pytest for the same CPUs. One FastAPI app shared
  by the api tests instead of one per test: FastAPI builds the state of its 55 routes per app,
  about 73 ms of the roughly 130 ms an identity test takes on the Mac after the changes above,
  the largest cost left; but sharing an app means resetting every piece of its state between
  tests: a change to the tests themselves, for an issue of its own.

**The plan: what a pull request has to run.** The first step of each job
(`.github/scripts/ci_plan.py`) reads the files the pull request changes (its merge commit against
`main`) and decides which units run: `api`, `web`, `shell`, `e2e`. Only a pull request against
`main` is narrowed. One against another branch (a stacked pull request) runs everything: its
files against that branch say nothing about what `main` receives, and changing its base to
`main` later starts no run, so narrowed checks would stay green on code no run had tested. The
rules are a short list in that script, first match wins:

| the pull request touches only | units | why the rest may be skipped |
|---|---|---|
| `tasks/`, `docs/` (but not `docs/runbooks/`), `CONTRIBUTING.md`, `CAPABILITY-MAP.md`, the `SPEC-*.md` files, the pull request template (a Markdown file at the root that is none of these runs everything) | none: pre-commit alone | nothing executable reads them (a test fails when something starts to) |
| `README.md`, `docs/runbooks/` | `shell` | `tests/deploy_test.sh` and `tests/migrate_test.sh` check that the recovery statements and the cron line they test are the ones written in the runbooks |
| `tasks/evidence/agents-a5/` | `shell` | `tests/agents_real_run_test.sh` compares against it |
| `tasks/ingestion/todo.md`, `tasks/evidence/ingestion-g0/citation_rules.txt` | `api` | `api/tests/ingestion/test_citation_examples.py` fails when the plan's table of citation examples is not the one its data generates, and compares the first 108 verdicts with that output |
| `web/` | `web`, `shell`, `e2e` | no api file reads a file of `web/` (a test fails when one starts to) |
| anything else, or a mix with anything else | all four | a path no rule names runs everything |

A push to `main`, a manual run, a pull request whose base is not `main` and any run where the
changed files cannot be read for certain run everything. (Until issue #116 a green run on `main`
was what deployed; a run deploys nothing now.)
Each job ends with `ci_plan.py verify`,
which fails it when a step the plan needed was skipped, or one it left out ran, so a wrong
condition in the workflow cannot pass for a test that did not run (and it fails on a job status
it does not know: only `failure` and `cancelled` skip the judgement). `ci_plan_test.py` checks
the rules, their reasons on this repository (it found the README check above the day it was
written), and that the workflow and the plan agree in both directions. From the plan: every
check listed under a unit in `LANES` exists in its job and runs under that unit's output. From
the workflow: every step of `ci.yml` has an `id` and is listed in `ci_plan.py` exactly once,
as a check of a unit (`LANES`), as a step of every run (`ALWAYS`: pre-commit and the secret
scan) or as a support step (`SUPPORT`: checkout, the plan, tools and installs, the start of a
stack, logs, teardown, summaries, uploads), and a support step runs no test command. What that
does not check: whether a new command that is a check was written as one; a reviewer reads a
change of `SUPPORT` as a change of the gate.
Within a job a failed check does not stop the checks after it: each unit reports for itself, and
the job fails if any step failed.

What the documentation rules rest on is itself a scan with limits. It reads the Python of
`api/`, `tests/` and `.github/scripts`, the TypeScript of `web/`, the shell of `tests/`, `ops/`
and `deploy.sh`, the Dockerfiles, the Compose files, `caddy/` and both workflows, and fails
when one of them names a documentation path whose rule does not run its unit (also when it
names `tasks` or `docs` as a bare word, or looks for `*.md`). It cannot see a path put
together at run time from variables, and it leaves the folder `tasks/evidence` alone when no
subfolder is named, because the journeys write their screenshots there.

**The secret scan.** Every run, whatever the plan, reads the commits it brings with gitleaks
(`.github/scripts/secret_scan.sh`, the step after pre-commit in `checks`): for a pull request
its commits and then the pull request as a whole (its merge commit against `main`), for
a push to `main` the commits of the push, for a manual run the whole history of the branch.
The commits are read three ways: each one on its own, so a secret added by one commit and
removed by the next is found; each merge commit for the lines somebody typed into it
(`git log --remerge-diff`, git 2.36 or later, refused loudly on an older one), because plain
`git log` prints no patch for a merge commit; and the commit messages. Not read, here or at a
commit: a binary file, for which git prints no patch. It
is the gitleaks that `.pre-commit-config.yaml` pins, started through pre-commit
(`pre-commit run gitleaks-commits --hook-stage manual`, which works locally too), with the
same rules and the same allowlist as at a commit: the default rules, a `gitleaks:allow` comment
on a line, and `.gitleaks.toml` or `.gitleaksignore` should the repository ever get one. Until
2026-10-11 CI had no secret scan in effect: the gitleaks hook reads the staged changes, a
checkout has none, and `pre-commit run --all-files` passed with "0 commits scanned" whatever
had been committed (shown in a throwaway clone with the example secret of gitleaks' own
README; `tests/secret_scan_test.sh` keeps showing it, and plants that example as a pull
request, as a push, in a commit that a later one reverts, in a merge inside the branch that
the next commit cleans again, in a commit message: the scan must fail each time). One scan of
the whole history on that day, every branch on GitHub included, found nothing; the reviewer
read the merge commits' own lines of every ref the same day, with the same result. The job that scans fetches the whole history
(`fetch-depth: 0`); the script refuses a shallow checkout, where it would read a history with
holes. When it fails on a real secret: revoke the secret where it is valid first, whatever
happens to the commit afterwards. The same holds for a secret that was pushed and then removed
by a forced push: if the next push cancelled its run, no scan ever read it, and it stays on
GitHub although it never reaches `main`. When it fails on a false alarm: a `gitleaks:allow`
comment on the line, in the same pull request. That is also the limit of the scan: a pull
request is judged by the rules of its own tree, so it can bring the allowance that lets its
own secret through (a comment, a `.gitleaks.toml`, a `.gitleaksignore`). The scan says so in
its log when a pull request touches any of them, and a reviewer reads them as a change to the
gate (CONTRIBUTING.md, step 5). Two things keep the step from passing without having read: it
runs under `env -u SKIP`, since pre-commit would skip it on a `SKIP` that names gitleaks, and
the pre-commit step before it prints no diff of what a fixer changed, which would show the
lines in clear in the log.

**A fresh run for an open pull request** after `main` moved: `ops/ci-rerun.sh <number>`. CI
tests a pull request at its merge commit with the base, which GitHub recomputes lazily, so
`gh run rerun` (the old commit), `gh workflow run` (the branch alone) and a quick close and
reopen (the stale merge commit) all test the wrong thing. The script waits until GitHub's merge
commit is the base as it is now plus the branch, then closes and reopens; `--check` only tells.
