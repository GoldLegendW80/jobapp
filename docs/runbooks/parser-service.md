# The parser service

The sandboxed service that parses every uploaded document and what a taken over parser can still do.

Every parser of an uploaded document (PDF, DOCX, archive, image) will run in the `parser` service
and nowhere else (decision of the owner, 2026-10-10; ingestion G2a, SPEC-platform.md, The parser
service). Parsers of hostile files are where exploits live, and the worker holds decrypted model
credentials during agent runs, so the parsers get a container with nothing to steal and nowhere
to go. In G2a it parses nothing yet: the five kinds answer `not_supported` until G2b (PDF text),
G7 (DOCX, LinkedIn export) and G8 (images, scanned pages) each add their file.

- **The container:** the worker's image (no second image), uid 10002, no capability, no new
  privilege, a read only root, no volume, no tmpfs, `ipc: none`, 32 pids, 640m, no `env_file`: its
  environment is `PARSER_PORT` and what the image sets. It is on the internal network `parsing`
  alone, which the worker shares: it cannot resolve or reach db, redis, the api or the internet,
  and only the worker can reach it. It never imports `cryptography`, so `compose.mac.yml` has no
  entry for it.
- **One child per document:** `sandbox/server.py` holds the bytes and starts `sandbox/main.py`
  for each job with an empty environment. The child locks itself down before it imports a
  parser: 512 MiB of address space, 45 s of CPU (SIGKILL at 46 s), no file growth, 16 descriptors,
  no new process or thread, no core file, non dumpable, no new privileges, and a seccomp filter
  that answers EPERM to 25 system calls (sockets, signals, ptrace, exec, io_uring, IPC objects,
  namespaces). When any of it fails the child exits without reading the document. The server
  kills a child after 60 s and when it writes more than 4 MiB. One job at a time.
- **The worker's side:** `app/ingestion/parser_client.py`. Every byte of an answer is treated as
  hostile and checked before anything is stored; an answer that breaks a rule is a failure of
  the server and nothing of it is kept. Five errors, each saying whose failure it is: down,
  busy, timeout, refused (the file, or the server), malformed.

```bash
docker compose exec parser python -I /app/app/ingestion/sandbox/server.py --check   # the healthcheck: exit 0, or "unhealthy: <why>"
docker compose exec worker python -m app.ingestion.parser_client ping               # from the worker: one line of JSON, exit 0 when the sandbox is in force
docker compose exec worker python -m app.ingestion.parser_client ping --bytes 1048576   # 1 MiB through a child, counted and hashed
docker compose logs parser                                                          # one JSON line per job: kind, sizes, code, ms; never content
tests/ingestion_parser_jetson.sh                                                    # on the Jetson, read only: the filter on its kernel, memory, timings
```

**Healthy means the sandbox is in force**, not that a port answers. The healthcheck starts a
child, and the child reports what holds in itself: each of the 25 denied calls tried and
answering EPERM, each limit read back at its value, non dumpable, no capability, an empty
environment, three descriptors and no socket, nowhere to create a file, the server's `/proc`
entry closed to it. Anything else is `unhealthy`. On a kernel where the filter cannot be
installed the service never turns healthy and a deploy rolls back: there is no fallback without
the filter. `"filters": 2` in the ping line means the filter sits on top of Docker's default
seccomp profile; `1` means Docker has none there (Docker Desktop on the Mac).

Memory, measured on the Mac on 2026-10-13 (M4, Docker Desktop 27.4, kernel 6.10, arm64); the
Jetson's figures come from `tests/ingestion_parser_jetson.sh` after the first deploy:

| | Idle | After 135 hostile jobs | Limit |
|---|---|---|---|
| parser | 18 MiB (one Python process) | 28 MiB, one process | 640 MiB |

A ping takes about 40 ms and 27 MiB in its child; an echo of 20 MiB about 150 ms and 83 MiB in its
child, 110 MiB for the container at its highest.

**What a parser that was taken over can still do.** It runs code as uid 10002 in a container
with no credential and no route out. It can return wrong text or a hostile JPEG for the one
document it is parsing (the worker checks shape, sizes and codes, the user sees the text, and
the JPEG goes to the model and to the owner's own browser); use the CPU and the memory of the
container up to its limits until it is killed; import whatever the worker's image installs (it
cannot start the Claude Code binary: exec is denied); address the worker's container, the one
neighbour on its network, which listens on nothing; make itself dumpable again, which exposes
only itself; and attack the kernel through the system calls the filter allows. The deny list
is short on purpose and cannot be complete: the container is the boundary. Never give the
service an `env_file`, a volume, a second network or a published port.
