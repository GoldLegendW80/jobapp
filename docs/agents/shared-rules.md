# Rules shared by every builder and reviewer (read once, follow exactly)

Both [implementer.md](implementer.md) and [reviewer.md](reviewer.md) start here. The hard rules of
[CLAUDE.md](../../CLAUDE.md) come first; this file adds what they leave open.

## Git

- The main checkout (the clone the orchestrator works from) is never edited and never switches
  branch. Work only in your own worktree under `.claude/worktrees/` (git ignored), with absolute
  paths. The command that makes it is in your role file.
- One git command per call, `git -C <worktree> ...`. If a hook of your shell rewrites commands
  (an rtk style proxy), call git by its absolute path (`/usr/bin/git` on macOS).
- Plain pushes only, never force. Never bypass git hooks (no `core.hooksPath=/dev/null`, no
  `--no-verify`): if a hook fails, fix the cause.
- Never push to `main`, never merge, never deploy. The orchestrator merges with `ops/ship.sh`.

## Context costs money

- Never print a whole file or log: `grep -n`, `sed -n 'a,bp'`, `| tail -n 30`,
  `pytest -q ... | tail -n 15`.
- Never read `tasks/ingestion/todo.md` or `tasks/mail/todo.md` whole (one holds a table of
  hundreds of examples): grep the section heading, read by line range.
- No exploratory reruns. Wait for nothing after the push.
- Hand back after about 90 tool calls even if unfinished, saying what is left.

## Safety

The hard rules of CLAUDE.md hold (no cybersecurity feature work, tests on `e2e-*@example.org`
accounts only, and the others). What they leave open:
- Never read `.env` files (`.env`, `.env.jetson`, `.env.local`, `.env.*.local`, `.env.production`, `.env.prod`, `.env.mailsend`, `.env.redis`), the `.secrets/` folder or production credentials;
  never print, grep or log a secret. `.env.example` is the tracked template: read and edit it freely (add a new setting there).
- Never contact a real Google host; never send mail.
- The repository is PUBLIC (since 2026-10-15). Fixtures, evidence and examples use invented
  data (`Example Robotics`, `e2e-someone@example.org`).
- Anything that needs sudo, a password or a 2FA code on any machine is the owner's: write the
  exact command in the hand back and stop.

## Processes and Docker

- Never start a process that outlives your own command: no `&` background jobs, no `while :`
  loops, no `nohup`, no artificial CPU or memory load ("burners") to simulate a busy machine. A
  load test, if asked for, runs real tests side by side in the foreground of ONE command and ends
  with it. (A reviewer once left 24 CPU loops running for 2.5 hours and broke two merge gates.)
- Never run `docker system prune`, `docker network prune`, `docker volume prune` or
  `docker image prune` with filters wider than your own names: the machine hosts the owner's
  other projects. Remove only the containers, networks, volumes and images you created, by name.
- Shared machine: other agents may run beside you. If the folder `.local-check/lock` of the main
  checkout exists, the gate is running: start nothing heavy until it is gone. If `df` shows under
  12 GiB free, stop building images and say so in the hand back.

## Shared files

Never delete or edit files you did not create outside your worktree: other agents' worktrees,
the orchestrator's notes, `.claude/reviews/` drafts of others, the main checkout. Work only in
your own worktree and your own files.
