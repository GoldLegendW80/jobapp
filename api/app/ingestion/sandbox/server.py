"""The `parser` service: receives a document from the worker and has it parsed by a locked down
child, one child per job (task G2a of the ingestion plan; decision 9 of the owner, 2026-10-10).

    python -I /app/app/ingestion/sandbox/server.py            the service (compose.yml, `parser`)
    python -I /app/app/ingestion/sandbox/server.py --check    its healthcheck

Standalone: the standard library only, never `app`, started by its absolute path. It parses
nothing and decodes nothing of a document: it reads a header, holds the bytes, starts main.py
with them and passes on what the child wrote. It holds no secret (its environment is PARSER_PORT
and what the image sets), reaches nothing (an internal network shared with the worker alone) and
writes nothing (a read only root, no volume). It is non dumpable, so its children, which have
its uid, cannot read its memory or its /proc entry.

Protocol, one TCP connection per job, the client being app/ingestion/parser_client.py:

    client   {"kind": ..., "size": N, "options": {...}}\\n     one line of JSON, at most 4096 bytes
             N bytes                                           the document
    server   {"ok": ..., "failure": ..., "code": ..., "length": M}\\n
             M bytes                                           the child's answer, UTF-8 JSON

- The header is judged before a byte of the document is read: a line over 4096 bytes, a line
  that is no JSON object, an unknown kind, a size over 20 MiB are refused at once. What the
  client still sends is then thrown away for at most 2 s, unread, so that the refusal reaches a
  client that was still writing instead of being lost to a reset.
- The end of the answer is its `length`, never the end of the stream. `ok` true means a child
  ran and exited normally: its answer follows and says itself whether the document was read
  (`{"ok": true, ...}`) or not (`{"ok": false, "failure": "file", "code": ...}`). `ok` false is
  this server's own refusal or the child's death, `failure` is then "server", `length` 0 and
  `code` one of CODES.
- A connection that sends nothing for 10 s is given up, and a request has 60 s to arrive whole.
- One job at a time. A second one waits its turn, at most 75 s (the worker gives up at 75 s
  too), then is answered `busy`; so is a job that arrives while two already wait.
- `ping` without bytes is answered at once, by a child of its own, even while a job runs: a
  parse of 60 s does not turn the service unhealthy. `ping` with bytes is a job like another,
  whose child answers the size and the SHA-256 of what it received.
- The client keeps the connection open until it has its answer. Closing it cancels the job:
  the child is killed, and the next job starts.

A child gets an empty environment, the bytes on standard input, a pipe for its answer and
nothing else: no descriptor of this process, and its standard error goes nowhere (a hostile
file must not be able to write into the logs of the service). It is killed after 60 s of wall
time, and as soon as it has written more than 4 MiB. Nothing of a document is ever logged: the
log lines hold a kind, sizes, a code and durations.
"""

import asyncio
import contextlib
import ctypes
import json
import os
import resource
import signal
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD = (sys.executable, "-I", "-B", os.path.join(HERE, "main.py"))

DEFAULT_PORT = 7070
KINDS = (
    "pdf_text", "docx", "linkedin_zip", "image", "pdf_render", "mail_html", "design_pdf",
    "design_docx", "ping",
)  # fmt: skip
MIB = 1024 * 1024
MAX_HEADER = 4096
MAX_SIZE = 20 * MIB
MAX_ANSWER = 4 * MIB
# What is read, and dropped, from the pipe of a child once it is dead: what the kernel still
# held for it (64 KiB by default, 1 MiB at most for a process without privileges,
# fs.pipe-max-size) and what the reader had taken ahead (under 400 KiB). Nothing writes to the
# pipe of a dead child, so more than this means the pipe has another writer: reading stops.
DRAIN_MAX = 2 * MIB
REAP_POLL_S = 0.01
READ_TIMEOUT_S = 10.0  # silence on a connection
REQUEST_S = 60.0  # a whole request, however slowly it trickles in
WALL_S = 60.0  # a child, from its start to its end
PING_WALL_S = 10.0
QUEUE_WAIT_S = 75.0  # a job waiting for the one before it
DRAIN_S = 2.0  # what a refused client still sends is thrown away for that long
MAX_CONNECTIONS = 16
MAX_JOBS = 3  # one running and two waiting, each holding its document: 60 MiB at most
MAX_PINGS = 2
SANDBOX_EXIT = 70  # main.py: the lockdown failed, nothing was handled

# Every code this server puts in a header. The child's own codes travel in its answer.
CODES = (
    "ok",
    "bad_request",  # the header is no JSON object of the expected shape
    "header_too_long",
    "unknown_kind",
    "too_large",
    "read_timeout",
    "busy",
    "wall_limit",  # the child was killed after WALL_S
    "cpu_limit",  # the child was ended by RLIMIT_CPU
    "output_cap",  # the child wrote more than MAX_ANSWER
    "sandbox",  # the child could not lock itself down, and so did nothing
    "no_child",  # the child could not be started at all
    "crashed",  # any other death: a signal (the memory of the container, a fault), a status
)

PR_SET_DUMPABLE = 4

# What --check requires of the ping child's report. The limits are those of main.py for `ping`,
# and the names those of its deny list: tests/ingestion/parser keeps the two files in step.
EXPECTED_LIMITS = {
    "as": 512 * MIB,
    "cpu": 45,
    "fsize": 0,
    "nofile": 16,
    "nproc": 0,
    "core": 0,
    "msgqueue": 0,
}
REQUIRED_DENIED = (
    "socket",
    "socketpair",
    "bind",
    "connect",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "kill",
    "tkill",
    "tgkill",
    "rt_sigqueueinfo",
    "rt_tgsigqueueinfo",
    "pidfd_open",
    "pidfd_send_signal",
    "execve",
    "execveat",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "shmget",
    "msgget",
    "semget",
    "mq_open",
    "unshare",
    "setns",
)


class RefusedError(Exception):
    """The request is answered with this code and no child."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ClientGoneError(Exception):
    """The client closed the connection: nothing is answered."""


class Outcome:
    """How a job ended: `code` (ok, or why there is no answer), and the child's answer."""

    __slots__ = ("body", "code", "ms", "read", "status")

    def __init__(
        self, code: str, body: bytes = b"", status: int | None = None, ms: int = 0, read: int = 0
    ):
        self.code, self.body, self.status, self.ms = code, body, status, ms
        self.read = read  # the bytes read from the child's output, kept or not


def log(event: str, **fields: object) -> None:
    """One JSON line on standard error. Never a byte of a document: callers pass a kind, sizes,
    a code, durations."""
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    print(json.dumps(line, separators=(",", ":")), file=sys.stderr, flush=True)


def parse_header(line: bytes) -> tuple[str, int, str]:
    """(kind, size, options as compact JSON) of a request line, or RefusedError with the reason.
    The line comes from the network: every way of failing to read it is a refusal."""
    if len(line) > MAX_HEADER:
        raise RefusedError("header_too_long")
    try:
        header = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise RefusedError("bad_request") from None
    if not isinstance(header, dict) or not set(header) <= {"kind", "size", "options"}:
        raise RefusedError("bad_request")
    kind, size, options = header.get("kind"), header.get("size"), header.get("options", {})
    if not isinstance(kind, str):
        raise RefusedError("bad_request")
    if kind not in KINDS:
        raise RefusedError("unknown_kind")
    if type(size) is not int or size < 0 or not isinstance(options, dict):
        raise RefusedError("bad_request")
    if size > MAX_SIZE:
        raise RefusedError("too_large")
    # Written again by this process: the child reads what the server wrote, in ASCII.
    try:
        return kind, size, json.dumps(options, separators=(",", ":"))
    except (ValueError, RecursionError):  # nesting the encoder does not follow
        raise RefusedError("bad_request") from None


def header_line(code: str, length: int = 0) -> bytes:
    ok = code == "ok"
    header = {"ok": ok, "failure": None if ok else "server", "code": code, "length": length}
    return json.dumps(header, separators=(",", ":")).encode("ascii") + b"\n"


class Server:
    """The connections of the service and the one job slot. Every bound is an argument, with the
    values of the service as defaults, so that the tests can run it with short ones."""

    def __init__(
        self,
        child: tuple[str, ...] = CHILD,
        *,
        read_timeout_s: float = READ_TIMEOUT_S,
        request_s: float = REQUEST_S,
        wall_s: float = WALL_S,
        ping_wall_s: float = PING_WALL_S,
        queue_wait_s: float = QUEUE_WAIT_S,
        drain_s: float = DRAIN_S,
        max_answer: int = MAX_ANSWER,
        max_connections: int = MAX_CONNECTIONS,
        max_jobs: int = MAX_JOBS,
        max_pings: int = MAX_PINGS,
        quiet: bool = False,
    ) -> None:
        self.child = tuple(child)
        self.read_timeout_s, self.request_s = read_timeout_s, request_s
        self.wall_s, self.ping_wall_s = wall_s, ping_wall_s
        self.queue_wait_s, self.drain_s = queue_wait_s, drain_s
        self.max_answer = max_answer
        self.max_connections, self.max_jobs = max_connections, max_jobs
        self.quiet = quiet
        self.connections = 0
        self.jobs = 0
        self.slot = asyncio.Lock()
        self.pings = asyncio.Semaphore(max_pings)
        self.tasks: set[asyncio.Task] = set()

    # --- one connection ---------------------------------------------------------------------

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        self.connections += 1
        try:
            if self.connections > self.max_connections:
                await self.refuse(reader, writer, "busy")
            else:
                await self.serve(reader, writer)
        except (ClientGoneError, ConnectionError, TimeoutError):
            pass  # the client left, or stopped reading: there is nobody to answer
        finally:
            self.connections -= 1
            self.tasks.discard(task)  # type: ignore[arg-type]
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        deadline = time.monotonic() + self.request_s
        try:
            line = await asyncio.wait_for(reader.readuntil(b"\n"), self.read_timeout_s)
            kind, size, options = parse_header(line)
        except asyncio.LimitOverrunError:
            return await self.refuse(reader, writer, "header_too_long")
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                raise ClientGoneError from None
            code = "header_too_long" if len(exc.partial) > MAX_HEADER else "bad_request"
            return await self.refuse(reader, writer, code)
        except TimeoutError:
            return await self.refuse(reader, writer, "read_timeout")
        except RefusedError as refusal:
            return await self.refuse(reader, writer, refusal.code)

        if kind == "ping" and size == 0:
            async with self.pings:
                outcome = await self.run_child(kind, options, b"", self.ping_wall_s)
            if outcome.code != "ok":
                self.log("ping", code=outcome.code, status=outcome.status)
            return await self.answer(writer, outcome)

        if self.jobs >= self.max_jobs:
            return await self.refuse(reader, writer, "busy")
        self.jobs += 1
        try:
            try:
                data = await self.read_body(reader, size, deadline)
            except TimeoutError:
                return await self.refuse(reader, writer, "read_timeout")
            outcome = await self.while_connected(reader, self.queued(kind, options, data))
            del data
        finally:
            self.jobs -= 1
        # `status` is how the child ended (0, its exit status, minus the signal that killed
        # it) and `read` how much of its output was read: the record that a child past a
        # limit was killed, and not read to its end.
        self.log(
            "job",
            kind=kind,
            size=size,
            code=outcome.code,
            ms=outcome.ms,
            out=len(outcome.body),
            status=outcome.status,
            read=outcome.read,
        )
        await self.answer(writer, outcome)

    async def read_body(self, reader: asyncio.StreamReader, size: int, deadline: float) -> bytes:
        """Exactly `size` bytes. TimeoutError after read_timeout_s of silence or when the whole
        request is late; ClientGoneError when the stream ends first."""
        chunks, left = [], size
        while left:
            patience = min(self.read_timeout_s, deadline - time.monotonic())
            if patience <= 0:
                raise TimeoutError
            chunk = await asyncio.wait_for(reader.read(min(left, 1 << 16)), patience)
            if not chunk:
                raise ClientGoneError
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    async def while_connected(self, reader: asyncio.StreamReader, job) -> Outcome:
        """Run `job` for as long as the client is there. The client has sent everything and
        waits: the end of its stream, or one more byte, means that nobody wants the answer."""
        running = asyncio.ensure_future(job)
        leaving = asyncio.ensure_future(reader.read(1))
        try:
            await asyncio.wait({running, leaving}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (leaving,) if running.done() else (leaving, running):
                task.cancel()
            await asyncio.gather(running, leaving, return_exceptions=True)
        if running.cancelled():
            raise ClientGoneError
        return running.result()

    async def queued(self, kind: str, options: str, data: bytes) -> Outcome:
        """One job at a time: wait for the slot, at most queue_wait_s, then run the child."""
        try:
            await asyncio.wait_for(self.slot.acquire(), self.queue_wait_s)
        except TimeoutError:
            return Outcome("busy")
        try:
            return await self.run_child(kind, options, data, self.wall_s)
        finally:
            self.slot.release()

    # --- the child --------------------------------------------------------------------------

    async def run_child(self, kind: str, options: str, data: bytes, wall_s: float) -> Outcome:
        """Start the child of one job, feed it, read its answer under the cap, reap it."""
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.child,
                kind,
                options,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
                cwd="/",
                close_fds=True,
            )
        except OSError:  # the process limit of the container, a missing interpreter
            return Outcome("no_child")
        stdin, stdout = proc.stdin, proc.stdout

        async def feed() -> None:
            # A child that ends without reading everything (a refusal, a death) closes the pipe.
            with contextlib.suppress(ConnectionError):
                stdin.write(data)
                await stdin.drain()
            with contextlib.suppress(ConnectionError):
                stdin.close()
                await stdin.wait_closed()

        read = 0

        async def collect() -> bytes | None:
            nonlocal read
            chunks, total = [], 0
            while chunk := await stdout.read(1 << 16):
                total += len(chunk)
                read += len(chunk)
                if total > self.max_answer:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)

        async def reap() -> None:
            nonlocal read
            # The process first: not a byte more is read until it is dead, so it is the kill
            # that ends a child past the cap or the clock, never this service reading on
            # whatever it writes. `returncode` is set when the loop has reaped the process,
            # whatever its pipes (Process.wait() waits for the pipes as well).
            while proc.returncode is None:  # noqa: ASYNC110, asyncio has no event for it
                await asyncio.sleep(REAP_POLL_S)
            # Then what it left in the pipe. Past its buffer limit asyncio stops reading a
            # pipe nobody reads and never sees its end, and Process.wait() would wait for
            # ever, the job with it (issue #104). So the pipe is read to its end, nothing of
            # it is kept, and DRAIN_MAX bounds it by what a dead child can have left, not by
            # what a child wrote.
            drained = 0
            while drained <= DRAIN_MAX and (chunk := await stdout.read(1 << 16)):
                drained += len(chunk)
            read += drained
            await proc.wait()

        feeding = asyncio.ensure_future(feed())
        code, body = "wall_limit", b""
        try:
            async with asyncio.timeout(wall_s):
                answer = await collect()
                if answer is None:
                    code = "output_cap"
                else:
                    await proc.wait()
                    code, body = "ok", answer
        except TimeoutError:
            pass
        finally:
            # Whatever happened, this job's process is gone before the next one starts.
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            feeding.cancel()
            await asyncio.gather(feeding, return_exceptions=True)
            await asyncio.shield(reap())
        status = proc.returncode
        if code == "ok" and status != 0:
            body = b""
            if status == SANDBOX_EXIT:
                code = "sandbox"
            elif status == -signal.SIGXCPU:
                code = "cpu_limit"
            else:
                code = "crashed"
        return Outcome(code, body, status, int((time.monotonic() - started) * 1000), read)

    # --- answers ----------------------------------------------------------------------------

    async def answer(self, writer: asyncio.StreamWriter, outcome: Outcome) -> None:
        writer.write(header_line(outcome.code, len(outcome.body)) + outcome.body)
        await asyncio.wait_for(writer.drain(), self.read_timeout_s)

    async def refuse(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, code: str
    ) -> None:
        """Answer `code`, then throw away what the client still sends, for a moment: closing a
        socket that holds unread bytes resets the connection, and the refusal with it."""
        self.log("refused", code=code)
        writer.write(header_line(code))
        await asyncio.wait_for(writer.drain(), self.read_timeout_s)
        if writer.can_write_eof():
            writer.write_eof()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(self.drain_s):
                while await reader.read(1 << 16):
                    pass

    def log(self, event: str, **fields: object) -> None:
        if not self.quiet:
            log(event, **fields)

    async def close(self) -> None:
        """Cancel every connection: a running child is killed and reaped on the way."""
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def start(server: Server, host: str, port: int) -> asyncio.Server:
    # limit: the longest line readuntil accepts, and the size of the read buffer (the document
    # itself is read in blocks, never as a line).
    return await asyncio.start_server(server.handle, host, port, limit=MAX_HEADER, backlog=16)


# --- the healthcheck ----------------------------------------------------------------------------


def sandbox_problems(report: object) -> list[str]:
    """Why the report of a ping child does not show the sandbox in force (empty when it does).
    This is the definition of healthy: a child that started, locked itself down and answered,
    with the filter really refusing every denied call and every limit at its value."""
    if not isinstance(report, dict):
        return ["no_report"]
    problems = []
    if report.get("seccomp") != 2:
        problems.append("seccomp_mode")
    denied = report.get("denied")
    if not isinstance(denied, dict):
        denied = {}
    problems += [f"allowed:{name}" for name in REQUIRED_DENIED if denied.get(name) != "EPERM"]
    if report.get("limits") != EXPECTED_LIMITS:
        problems.append("limits")
    for name, wanted in (
        ("no_new_privs", 1),
        ("dumpable", 0),
        ("capabilities", 0),
        ("isolated", True),
        ("environ", 0),
        ("sockets", 0),
        ("descriptors", [0, 1, 2]),
        ("writable", []),
        ("parent_hidden", True),
        ("cryptography_blocked", True),
    ):
        # `is` for the booleans: 1 is not True here, and 0 is not False.
        got = report.get(name)
        if got != wanted or type(got) is not type(wanted):
            problems.append(name)
    if type(report.get("uid")) is not int or report["uid"] <= 0:
        problems.append("uid")
    return problems


def check(port: int, timeout_s: float = 5.0) -> list[str]:
    """Ask the running service for a ping through the loopback and judge the child's report.
    A child is started for it, so a healthy answer means the limits and the filter install."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_s) as conn:
            conn.sendall(b'{"kind":"ping","size":0,"options":{}}\n')
            stream = conn.makefile("rb")
            line = stream.readline(MAX_HEADER + 1)
            header = json.loads(line)
            if not isinstance(header, dict) or header.get("ok") is not True:
                code = header.get("code") if isinstance(header, dict) else None
                return [f"refused:{code}" if code in CODES else "refused"]
            length = header.get("length")
            if type(length) is not int or not 0 < length <= MAX_ANSWER:
                return ["bad_length"]
            answer = json.loads(stream.read(length))
    except OSError:
        return ["unreachable"]
    except (ValueError, RecursionError):
        return ["bad_answer"]
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        return ["bad_answer"]
    return sandbox_problems(answer.get("sandbox"))


# --- the process --------------------------------------------------------------------------------


def lock_down_server(libc=None, res=resource, uid: int | None = None) -> None:
    """What the server does to itself before it listens: no core file, and non dumpable, so
    that a child (same uid, same PID namespace) can neither attach to it nor read its
    /proc/<pid>/environ or its memory. The children are started by fork and exec as before:
    the attribute concerns who may look at this process, not what it may do."""
    if (os.getuid() if uid is None else uid) == 0:
        raise SystemExit("the parser must not run as root: RLIMIT_NPROC would not hold")
    res.setrlimit(res.RLIMIT_CORE, (0, 0))
    if libc is None:  # pragma: no cover, Linux: the service itself, run in the container
        libc = ctypes.CDLL(None, use_errno=True)
        word = ctypes.c_ulong
        libc.prctl.argtypes = (ctypes.c_int, word, word, word, word)
        libc.prctl.restype = ctypes.c_int
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise SystemExit("PR_SET_DUMPABLE failed: the parser does not start dumpable")


def port_from(environ: dict[str, str]) -> int:
    try:
        port = int(environ.get("PARSER_PORT", DEFAULT_PORT))
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        raise SystemExit("PARSER_PORT is not a port number")
    return port


async def run(port: int) -> None:  # pragma: no cover, the service itself: run in the container
    server = Server()
    listener = await start(server, "0.0.0.0", port)  # noqa: S104, the internal network alone
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    log("listening", port=port, uid=os.getuid())
    await stop.wait()
    listener.close()
    await server.close()
    log("stopped")


def main(argv: list[str]) -> int:
    port = port_from(dict(os.environ))
    if argv[1:] == ["--check"]:
        problems = check(port)
        if problems:
            print("unhealthy: " + " ".join(problems), file=sys.stderr)
        return 1 if problems else 0
    if argv[1:]:
        print(__doc__, file=sys.stderr)
        return 2
    lock_down_server()  # pragma: no cover, the service itself: run in the container
    asyncio.run(run(port))  # pragma: no cover
    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
