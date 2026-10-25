# CI runners on the Mac

The optional self-hosted runners on the Mac (`ops/ci-runners.sh`): shape, budget, disk, reboot, updating, security.

Topics: Shape; Where things live; Budget; Disk budget; When the Mac is off, asleep or Docker Desktop is closed; When a runner is recreated; After a reboot; Back to GitHub's machines; Updating; Security; Apple silicon.

Optional and stopped by default since issue #116: the batch check (`ops/local-check.sh`) runs
natively and needs no runner. The runners serve a run of `ci.yml` started by hand, and are
started for it with `ops/ci-runners.sh start`. What follows describes them when they are up.

CI ran on self-hosted runners on the Mac (issue #83), `mac-ci-1` to `mac-ci-3`, labels
`self-hosted, Linux, ARM64, mac-ci`: three, one per job of a run (the workflow is the same with
one or two, the jobs then queue). Every job of `ci.yml` targets `[self-hosted, mac-ci]`, never
`self-hosted` alone, so a CI job cannot land on the Jetson's runner, and `deploy.yml` keeps
`[self-hosted, jetson]`.

```bash
ops/ci-runners.sh install [count]   # build the image, register and start the runners (default 3, the most; as many as exist when there are some); safe to run again
ops/ci-runners.sh status            # containers, whether each listens for jobs, GitHub's view (online, busy), memory, disk used
ops/ci-runners.sh stop              # stop them; queued jobs wait
ops/ci-runners.sh start             # start them again, back when each listens for jobs
ops/ci-runners.sh update            # rebuild the image from ops/ci-runner, recreate each container when its runner is idle (registration and caches kept)
ops/ci-runners.sh prune             # empty the tool caches and every runner's Docker daemon
ops/ci-runners.sh uninstall         # unregister, remove containers, volumes and image
```

**Shape.** Each runner is the official actions runner for Linux arm64 (version and SHA-256
pinned in `ops/ci-runner/Dockerfile`, checked at build) in a container of Docker Desktop, with a
Docker daemon of its own inside (the same Engine and Compose versions as the Jetson). Chosen over
runners installed natively on macOS for three reasons. It is the Jetson's platform, Linux arm64,
so tests run on what production runs on. GitHub's `services:` and container features need a Linux
runner with Docker, so the workflow is the one that ran on GitHub's machines, and one variable
sends it back there. And a job sees a machine of its own: its own `127.0.0.1`, container names,
Compose project and ports, so parallel jobs cannot collide, and nothing a job starts, stops or
prunes is in the Mac's own Docker daemon, where Harald's containers and the agents' scratch
stacks live. A native runner would run job code as Harald's macOS user, next to his keys.

**Where things live.** Nothing in the repository, and in the home folder only the empty folder
`~/actions-runners/jobapp-ci/disk` (Disk budget, below): the image
`jobapp-ci-runner:<version>`, the containers `jobapp-ci-1` to `jobapp-ci-3` (restart
unless-stopped), the volume `jobapp-ci` (each runner's directory, registration and work folder,
plus the caches the runners share: uv, the pnpm store, Playwright's Chromium, pre-commit) and one
volume `jobapp-ci-docker-<n>` per runner (its Docker daemon's images and layer cache). All carry
the label `jobapp.ci=runner`. To go from two runners to three: `ops/ci-runners.sh install 3`
(the script refuses more, and refuses to go down: `uninstall` first).

**Budget.** The Mac is shared: Harald works on it, and agents run scratch stacks and test suites
in the same Docker Desktop (12 CPUs, about 18 GB). On 2026-10-10 three runner trials on top of
four agents took the load average to 82 while the disk filled, and the Mac went down, so CI has a fixed share and
cannot take more:

| | per runner | three runners | what was measured |
|---|---|---|---|
| CPUs | 3, pinned (runner n: CPUs 3(n-1) to 3n-1) | 9 of Docker's 12 at most (macOS places Docker's virtual CPUs on the cores it likes) | the api tests used 2.1 to 3 CPUs, a Playwright group about 0.6, Vitest 1.2 to 2.3 for under a minute; so a whole run of the three jobs is expected at 4 to 6 cores' worth |
| Memory | 4 GB, swap included | 12 GB of about 18 at most | in the samples of `docker stats`, every 10 s over the measured runs of the three jobs: up to 3.5 GB on a runner, and the peaks of two runners in one run add up to 5.2 to 6.3 GB; the kernel's own count (`memory.peak` of the runner's container, file cache included) stands at the 4 GB limit on both runners, which was reached 1,641 and 3,092 times in their first 3 hours and about 68 jobs each (`memory.events`, `max`) and answered by giving cache back each time: `oom` 0, `oom_kill` 0. Every job writes these counters to its summary |
| Disk | see the disk budget below | about 14 GB, 20 GB at most | 11.1 GB for two runners after eight full runs |

That leaves Harald's own work and one agent's stack 3 of Docker's CPUs at all times and 6 or
more in practice, and about 10 GB of Docker's memory. A runner takes one job at a time, so three
runners are the bound on what runs at once, whatever the number of pushes: the other jobs queue,
and a newer push to the same branch cancels the older run. Inside a job the worker counts are
written out in `ci.yml` (`PYTEST_WORKERS` and `VITEST_WORKERS`, 3 each, never "all cores";
Playwright runs one worker). Why not more: a fourth runner would take the last 3 CPUs for a
workflow that has three jobs, and 4 CPUs per runner would speed up only the api job, which is not
the one the run waits for. Why not less memory: the samples reach 3.5 GB of the 4, and the
limit is a ceiling, not a reservation, so lowering it frees nothing. Why not more: nothing was
killed at 4 GB, and what fills it is file cache.

**Disk budget.** Disk is the scarcest resource of this Mac, and a full disk does not fail one
job: it corrupts Docker Desktop's store for every container (2026-10-10: a trial of three e2e
groups at once on a disk with 22 GB free, next to 21.8 GB of dangling volumes left by test runs,
filled it). Everything of CI sits in Docker Desktop's disk image, which grows on the Mac's own
disk:

| | size | bound |
|---|---|---|
| Runner image, stored once | 1.9 GB | fixed |
| Docker data of a runner (images, layer cache): volume `jobapp-ci-docker-<n>` | 2.4 and 2.5 GB after eight full runs | 4 GB (`CI_DOCKER_MAX_MB`), then emptied; build cache alone 1 GB (`CI_BUILD_CACHE_KEEP`) |
| Runner directories and shared tool caches (uv, pnpm store, Chromium, pre-commit): volume `jobapp-ci` | 4.3 GB for two runners after eight full runs | the caches grow only when a lock file changes; `prune` empties them |
| Work folder of a job | emptied after every job | 0 between jobs |

Measured after eight full runs on two runners: 11.1 GB in all, with the Docker data of a
runner where a single run leaves it (2.4 GB after the three e2e groups of a trial). For three
runners that is about 14 GB, 20 GB at the bounds. Three things enforce it.
`install` refuses a Mac with less free space than 4 GB per runner plus 6
(`CI_RUNNER_MIN_FREE_GB`). Before every job, the job-started hook reads the Mac's free space
and fails the job with the figure when it is under 10 GB (`CI_MIN_FREE_GB`): the hook sees the
Mac's disk through `~/actions-runners/jobapp-ci/disk`, an empty folder mounted read only, the
only thing of the Mac a runner sees. After every job, the job-completed hook removes the job's
containers with their volumes, its networks, every unused volume, dangling images and build
cache beyond 1 GB, and empties the work folder (checkout, `node_modules`, virtualenv,
Playwright's reports and traces, which are uploaded as artifacts by then, kept 14 days by
GitHub). Runs do not add up: the caches are keyed by content, so a second run of the same
commit adds nothing (2,404 MB of Docker data after the first e2e group, 2,407 MB after the
third).

The leak behind the dangling volumes: the Postgres, Redis and caddy images declare anonymous
volumes, and the test helpers removed their throwaway containers without them. Fixed at the
source: `api/tests/pg.py` and `api/tests/redis_server.py` (every local `pytest` run without
`TEST_DATABASE_URL`, and the docker-marked tests), `tests/tailscale_test.sh`,
`tests/compose_smoke.sh` and the three `tests/e2e_*jetson.sh` now remove with `-v`, and
`tests/ci_runners_test.sh` fails if one stops doing so. On a scratch stack, `docker compose down`
without `-v` keeps its named volumes on purpose; finish with `down -v`.

**When the Mac is off, asleep or Docker Desktop is closed.** The runners are offline and CI
waits: jobs stay queued (GitHub keeps a queued job for 24 hours, then fails it) and start when
the Mac is back. A job that was running when the Mac went to sleep can fail with a lost runner:
rerun it (`gh run rerun <id> --failed`). A deploy does not depend on them: it is dispatched to
the Jetson's runner by `ops/deploy-checked.sh`. No power setting was changed; for a long unattended session, `caffeinate -s`
in a terminal keeps a Mac on mains power awake while it runs.

**When a runner is recreated** (`update`, `install` on an existing set): one at a time, each
only when GitHub reports it idle (a call GitHub does not answer stops the command: unknown is
not idle), stopped so that its listener ends its session, and the command does not go on before
the new container's log says "Listening for Jobs". That last wait exists because GitHub shows
a runner as online before its listener has a session: on 2026-10-10 a run queued in that gap
(38071312967) sat queued for 14 minutes and was never assigned, with both runners online. If a
run stays queued while `ops/ci-runners.sh status` shows the runners listening and idle, GitHub
will not hand it over any more: cancel it and start it again (`gh run cancel <id>`, then
`gh run rerun <id>` for a push or a manual run, `ops/ci-rerun.sh <number>` for a pull request).

**After a reboot.** The runner containers have `restart: unless-stopped`: they start with the
Docker daemon, register as online within a minute and take the queued jobs, with nothing to
type. The daemon is Docker Desktop, and whether it starts when Harald logs in is its own
setting, off on 2026-10-10: Docker Desktop, Settings, General, tick "Start Docker Desktop when
you sign in to your computer". Until that box is ticked, a reboot leaves CI off until Docker
Desktop is opened by hand. `ops/ci-runners.sh install` and `status` read the setting and say
which case applies; they never change it. The Mac must also reach the login session: with
FileVault a reboot stops at the login window until someone signs in. A job cut by the reboot
is rerun as above; its leftovers are removed when the container starts and again before the next
job.

**Back to GitHub's machines.** One repository variable, no edit of the workflow:

```bash
gh variable set CI_RUNS_ON --body '"ubuntu-latest"'    # CI on GitHub's runners (uses the free minutes)
gh variable delete CI_RUNS_ON                          # back on the Mac
```

`CI_RUNS_ON` is the JSON of a `runs-on` value; unset, it is `["self-hosted", "mac-ci"]`. The
steps that differ test `runner.environment`: on GitHub's machines the caches go to GitHub's cache
service as before, on the Mac they stay on the runner. The three jobs and the worker counts
(`PYTEST_WORKERS`, `VITEST_WORKERS`) apply there too: correct, and slower than the eleven
parallel jobs `main` once had on those machines, where runners were not the scarce thing.

**Updating.** `ops/ci-runners.sh update`, run from a checkout that holds the wanted
`ops/ci-runner/`, rebuilds the image and recreates the containers that do not run it yet, one at
a time, each when its runner is idle: it waits for a running job to end (up to an hour,
`CI_UPDATE_WAIT_S`), so it is safe while jobs are queued, and running it again changes nothing.
A job that GitHub hands to a runner in the very second its container is recreated is lost and
shows as failed: `gh run rerun <id> --failed`. `update` changes the Docker Engine, the packages
and the hooks of the image. It does not move the runner itself: its binaries live in the
`jobapp-ci` volume, seeded once from the image, and update themselves when GitHub publishes a
release. `RUNNER_VERSION` and its SHA-256 in the Dockerfile therefore only set what a new
runner starts from; to start again from the pinned version, `uninstall` then `install`.
`install` on an existing set follows the same rule (idle runners only, containers already on the
image left alone) and refuses a lower count than what exists.

**Security.** Read this as guard rails against mistakes, not as a boundary against hostile code.

What the setup does: the runners are registered to this private repository only, and a
job-started hook in the image (`ops/ci-runner/job-started.sh`) refuses every job that is not
this repository's `ci.yml`. A container gets no variable of the shell that installs it (no
token, no key, no `.env`) and not the Mac's Docker socket; the only thing of the Mac it sees is
the empty probe folder, read only. The registration token goes from `gh api` into the
container's stdin and is stored nowhere. Jobs use throwaway secrets (`.github/scripts/ci_env.sh`)
and synthetic data, never the Jetson's database or Harald's files. `ci.yml` uses no repository
secret and its token can only read the repository.

What it does not do: `--privileged` is not a wall. A job is root in a privileged container (it
has passwordless sudo and a Docker daemon), so code pushed to any branch of this repository can,
on purpose, read the runners' long lived credentials (they sit in the `jobapp-ci` volume, which
both runners share), rewrite the hooks or the runner's files so that later jobs skip them, reach
Docker Desktop's virtual machine with every other container and volume in it, and through Docker
Desktop's file sharing reach `/Users`, so Harald's home folder. It can also leave a poisoned
entry in the shared caches for the next run (`ops/ci-runners.sh prune` empties them).

This is acceptable today for one reason: the repository is private, its only collaborator is its
owner, and the same agents already run code as his macOS user. It stops being acceptable, and the
runners must be uninstalled or moved first, as soon as any of these is true:

- the repository becomes public, or fork pull requests are allowed to run workflows;
- a second collaborator, or a bot that opens pull requests (Dependabot, Renovate), is added;
- a workflow that targets `mac-ci` gets a `pull_request_target`, `workflow_run`, `issue_comment`
  or `issues` trigger;
- a repository secret is given to `ci.yml`.

The upgrade then is a virtual machine of its own for CI (Lima or Colima), or ephemeral runners
with a fresh volume per job.

**Apple silicon.** The runners sit in the same Linux guest as every other container of this Mac,
so they need `OPENSSL_armcap=0` too (development-on-the-mac.md). The runner image sets it
for a job's own processes and sets `CI_COMPOSE_OVERLAY=compose.mac.yml`, which the e2e jobs add
to their `compose up`. `ci.yml` never names the variable and `compose.yml` never has it.

To try a CI step by hand in the runner's exact environment, without registering anything:

```bash
ops/ci-runners.sh try                         # a shell in a container shaped like a runner
ops/ci-runners.sh try tests/deploy_test.sh    # or one command
```

It is a privileged container too (it runs a Docker daemon), on Docker's last 3 CPUs with the
runner's memory limit, and it starts empty: copy the checkout in with `docker cp . jobapp-ci-try:/ci/src`.
With three runners busy a trial is a fourth consumer: it takes those last 3 CPUs and brings CI
to 16 of about 18 GB at the limits, the share the budget leaves to Harald's own work, so run
one when CI is quiet.
Its container and its two scratch volumes (`jobapp-ci-try`, `jobapp-ci-try-docker`, a few GB
once images are built) are removed when the command ends and when the script is stopped
(Ctrl C, a closed terminal, a kill). A trial ended with `kill -9`, cut by a crash, or started
with a plain `docker run`, leaves them behind: `status` names them, `prune` and `uninstall`
remove them, these three names and no look-alike.
