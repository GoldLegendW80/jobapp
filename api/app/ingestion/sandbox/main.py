"""The sandboxed child of the parser service: one process per job (task G2a of the ingestion plan).

server.py starts it for every job as

    python -I -B /app/app/ingestion/sandbox/main.py <kind> <options as JSON>

with an empty environment, the bytes of the document on standard input, and reads its answer, one
JSON document in UTF-8, from standard output. Standard error goes nowhere.

This file is standalone: the standard library only, never `app`, started by its absolute path
(`-I` implies `-P`, so `python -I -m app.ingestion.sandbox` could not work, and nothing of the
application may be importable here). It parses nothing itself. It locks itself down, then hands
the bytes to the handler of `kind`, one file of this directory per kind:

    pdf_text.py (G2b)   docx.py, linkedin_zip.py (G7)   image.py, pdf_render.py (G8)

A handler is `handle(data: bytes, options: dict, budget: Budget) -> dict` and returns the whole
answer: `{"ok": true, ...}` or `{"ok": false, "failure": "file", "code": "..."}` with integers
beside the code, never a sentence (the worker writes the sentences, app/ingestion/parser_client.py
checks every field). A kind whose file is not there yet answers `not_supported`. `ping` is built
in: it reports what the lockdown really is in this process (the healthcheck of the service reads
it), and the size and SHA-256 of the bytes it was sent, which is the echo of this task.

The lockdown, in this order, before any handler is imported (not in a `preexec_fn`: the dumpable
attribute is reset by execve, so it has to be set by the program that was executed):

1. `cryptography` is made unimportable (pypdf imports it when it is installed, and on an Apple
   silicon Mac its OpenSSL dies with SIGILL: the parser must never need OPENSSL_armcap).
2. The process refuses to go on as uid 0: RLIMIT_NPROC is not enforced for root.
3. Resource limits (LIMITS): address space, CPU seconds (SIGXCPU at 45 s, SIGKILL at 46 s for
   a process that ignores it), no file growth, 16 descriptors, no new process or thread, no
   core file, no POSIX message queue.
4. `prctl(PR_SET_DUMPABLE, 0)`: no core dump even through a pipe, no ptrace attach, and
   /proc/<pid> of this process closed to the other processes of the same uid.
5. `prctl(PR_SET_NO_NEW_PRIVS, 1)`, then a seccomp filter that answers EPERM to DENIED: no
   socket, no signal to another process, no other program, no read of another process, no
   io_uring (a request made through a ring never passes a filter), none of the calls that
   create an IPC object which would outlive this process inside the container, and no
   namespace of its own.
6. The state is read back, and two denied calls are tried: a kernel that accepted the filter
   without applying it is caught here.

It fails closed: when any of these steps fails the process exits with SANDBOX_EXIT, having
imported no handler, read no byte of the document and written nothing.

The deny list is short on purpose. A deny list cannot be complete: the container is the boundary
(no capability, no credential, no route out, a read only root). What it buys is that a handler
taken over by a hostile file cannot open a connection through a descriptor it creates, cannot
signal or read the server or a neighbour, and cannot become another program.
"""

import contextlib
import ctypes
import errno
import importlib.util
import json
import os
import resource
import stat
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# kind -> the file of this directory that handles it (None: built in). The table is fixed: a
# kind is never a path, and a task that adds a parser adds a file, never a line here.
HANDLERS = {
    "ping": None,
    "pdf_text": "pdf_text",
    "docx": "docx",
    "linkedin_zip": "linkedin_zip",
    "image": "image",
    "pdf_render": "pdf_render",
}

MIB = 1024 * 1024
MAX_INPUT = 20 * MIB  # the 20 MiB of an upload; server.py refuses more before starting a child
MAX_ANSWER = 4 * MIB  # server.py kills a child that writes more; the worker reads no more

# Exit statuses server.py tells apart. Every other outcome is an answer on standard output.
USAGE_EXIT = 64
SANDBOX_EXIT = 70

# Resource limits, soft and hard alike, but for the CPU. `as` starts at 512 MiB for every kind;
# G2b and G8 set it per kind from the peak they measure (below, after LIMITS).
DEFAULT_LIMITS = {
    "as": 512 * MIB,
    "cpu": 45,
    "fsize": 0,
    "nofile": 16,
    "nproc": 0,
    "core": 0,
    "msgqueue": 0,
}
LIMITS = {kind: dict(DEFAULT_LIMITS) for kind in HANDLERS}
# The PDF text reader (G2b), measured in the service's container on 2026-10-13
# (tasks/ingestion/todo.md, Measurements): the highest address space of a child was 95 MiB
# (200 heavy pages stopped at the time budget), 71 MiB for a 20 MiB file, 52 to 70 MiB for PDFs
# of 1 to 200 pages printed by a browser. 256 MiB is more than twice the worst of them; a file
# that asks for more ends as `bomb`, with the service untouched.
LIMITS["pdf_text"]["as"] = 256 * MIB
# The Word reader and the reader of a LinkedIn export (G7), measured in the service's container
# on 2026-10-13 (tasks/ingestion/todo.md, G7, Measurements). Honest files: the highest address
# space of a child was 80 MiB (an export whose two CSV files hold 20 MiB), 64 MiB for 19 MiB of
# XML in one part of a document, 45 MiB for a 20 MiB document of images, 40 MiB for a CV and
# for a small export. Nothing is extracted and no tree is built, but the XML parser keeps every
# distinct name it meets and every open element, so a hostile document costs far more than its
# bytes: 19 MiB of new element names took 544 MiB of resident memory with no bound, new
# attribute names 337 MiB, elements nested without end 194 MiB (the Mac, no limit). docx.py
# bounds what it can count (NAMES_MAX, DEPTH_MAX): those three end as `bomb` at 60 MiB. It
# cannot count the names of one single start tag before the parser has kept them all (623 MiB
# with no limit, 610 MiB of address space in this image): that file meets the 256 MiB below,
# the kernel refuses the memory, and the answer is still `bomb`, with the service untouched
# (test_archive_container.py runs it). So 256 MiB is three times the worst honest file, and
# the limit, not the sizing, is what holds against a hostile one.
LIMITS["docx"]["as"] = 256 * MIB
LIMITS["linkedin_zip"]["as"] = 256 * MIB
# The image reader and the renderer (G8). Set from the measurements of the plan (G8,
# Measurements); until those are written here both keep the 512 MiB every kind starts with.
IMAGE_AS = 512 * MIB
PDF_RENDER_AS = 512 * MIB
LIMITS["image"]["as"] = IMAGE_AS
LIMITS["pdf_render"]["as"] = PDF_RENDER_AS
# The hard CPU limit is one second above the soft one. At the soft limit the kernel sends
# SIGXCPU, whose default action ends the process, and the server reports `cpu_limit`. The
# kernel looks at the hard limit first, though: with both at 45 it would send SIGKILL straight
# away, which nothing tells from a kill for memory. A child that ignores SIGXCPU gets SIGKILL
# one second later.
CPU_HARD_EXTRA_S = 1

# What a handler stops at by itself, between two pages, so that it returns the pages it has
# instead of losing everything to the kill: under the 45 s of RLIMIT_CPU and the 60 s of the
# server's wall clock.
BUDGET_CPU_S = 35.0
BUDGET_WALL_S = 40.0

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4
PR_GET_SECCOMP = 21
PR_SET_SECCOMP = 22
PR_SET_NO_NEW_PRIVS = 38
PR_GET_NO_NEW_PRIVS = 39
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_JGE_K = 0x35
BPF_RET_K = 0x06
SECCOMP_DATA_NR = 0  # offsets in struct seccomp_data
SECCOMP_DATA_ARCH = 4
X32_SYSCALL_BIT = 0x40000000

# machine -> (audit architecture, whether numbers at or above X32_SYSCALL_BIT are refused).
# x86_64 only: the x32 calls share its audit architecture, so `socket` is also 0x40000029 there.
ARCHITECTURES = {
    "aarch64": (0xC00000B7, False),
    "x86_64": (0xC000003E, True),
}

# The deny list: (name, number on aarch64, number on x86_64), from the kernel's unistd tables.
# Each line is a way out of the process that the container alone does not close.
DENIED = (
    # a connection: no descriptor of a socket can be made here, and none is inherited
    ("socket", 198, 41),
    ("socketpair", 199, 53),
    ("bind", 200, 49),
    ("connect", 203, 42),
    # another process, read or written
    ("ptrace", 117, 101),
    ("process_vm_readv", 270, 310),
    ("process_vm_writev", 271, 311),
    # another process, signalled (the server is the parent and has the same uid)
    ("kill", 129, 62),
    ("tkill", 130, 200),
    ("tgkill", 131, 234),
    ("rt_sigqueueinfo", 138, 129),
    ("rt_tgsigqueueinfo", 240, 297),
    ("pidfd_open", 434, 434),
    ("pidfd_send_signal", 424, 424),
    # another program
    ("execve", 221, 59),
    ("execveat", 281, 322),
    # io_uring: what is asked through a ring is never shown to a filter
    ("io_uring_setup", 425, 425),
    ("io_uring_enter", 426, 426),
    ("io_uring_register", 427, 427),
    # IPC objects, which live as long as the container and not as long as this process: memory
    # or data left behind for the jobs that follow
    ("shmget", 194, 29),
    ("msgget", 186, 68),
    ("semget", 190, 64),
    ("mq_open", 180, 240),
    # a namespace of its own: in a new user namespace a process holds every capability, which
    # is where kernel exploits start. Docker's default profile refuses it; this does too where
    # that profile is off (Docker Desktop on the Mac, a privileged CI runner)
    ("unshare", 97, 272),
    ("setns", 268, 308),
)
DENIED_NAMES = tuple(name for name, _, _ in DENIED)

# pidfd_open is the one probe that succeeds with a descriptor where nothing filters it.
_RETURNS_DESCRIPTOR = frozenset({434})
# A key no IPC object of this container has: the probes look one up, they never create one.
_NO_SUCH_KEY = 0x6A6F6221
# Directories a process could hope to write in. The root is read only and the service has no
# tmpfs; this is what proves it from inside (the ping names the ones where a file was created).
WRITE_PROBES = ("/tmp", "/var/tmp", "/dev/shm", "/dev/mqueue", "/run", "/app", HERE, "/")  # noqa: S108


class SandboxError(Exception):
    """A step of the lockdown failed. The process must end without handling anything."""


def numbers(machine: str) -> dict[str, int]:
    """The denied system calls of this machine, name -> number."""
    column = {"aarch64": 1, "x86_64": 2}[machine]
    return {row[0]: row[column] for row in DENIED}


def program(arch: int, denied: list[int], refuse_x32: bool) -> bytes:
    """The classic BPF program of the filter, built by hand.

    Any other architecture than `arch` ends the process (a 32 bit call on a 64 bit kernel has
    other numbers, so a list by number would mean nothing for it). Then every number of `denied`
    answers EPERM, every number at or above the x32 bit too when `refuse_x32`, and everything
    else is allowed."""

    def instruction(code: int, jt: int, jf: int, k: int) -> bytes:
        return struct.pack("HBBI", code, jt, jf, k)

    tests = len(denied) + (1 if refuse_x32 else 0)
    if tests > 250:  # a jump is one byte
        raise ValueError("too many denied calls for one-byte jumps")
    out = [
        instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_ARCH),
        instruction(BPF_JEQ_K, 1, 0, arch),
        instruction(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
        instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_NR),
    ]
    # Each test jumps, when it matches, over the tests left and over the ALLOW return, onto the
    # EPERM return that ends the program.
    left = tests
    if refuse_x32:
        out.append(instruction(BPF_JGE_K, left, 0, X32_SYSCALL_BIT))
        left -= 1
    for number in denied:
        out.append(instruction(BPF_JEQ_K, left, 0, number))
        left -= 1
    out.append(instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    out.append(instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    return b"".join(out)


class _Program(ctypes.Structure):  # struct sock_fprog
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def load_libc():
    """libc with the two functions used here declared, so that no argument is ever guessed."""
    libc = ctypes.CDLL(None, use_errno=True)
    word = ctypes.c_ulong
    libc.prctl.argtypes = (ctypes.c_int, word, word, word, word)
    libc.prctl.restype = ctypes.c_int
    libc.syscall.restype = ctypes.c_long
    return libc


def set_limits(limits: dict[str, int], res=resource) -> None:
    for name, value in limits.items():
        hard = value + CPU_HARD_EXTRA_S if name == "cpu" else value
        res.setrlimit(getattr(res, "RLIMIT_" + name.upper()), (value, hard))


def read_limits(limits: dict[str, int], res=resource) -> dict[str, int]:
    """The soft limits as the kernel holds them, for the names of `limits`."""
    return {name: res.getrlimit(getattr(res, "RLIMIT_" + name.upper()))[0] for name in limits}


def install_filter(libc, machine: str) -> None:
    """PR_SET_NO_NEW_PRIVS, then the filter. Raises SandboxError when either is refused."""
    if machine not in ARCHITECTURES:
        raise SandboxError("architecture")
    arch, refuse_x32 = ARCHITECTURES[machine]
    code = program(arch, list(numbers(machine).values()), refuse_x32)
    buffer = ctypes.create_string_buffer(code, len(code))
    prog = _Program(len(code) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxError("no_new_privs")
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(prog), 0, 0) != 0:
        raise SandboxError("seccomp")


def probe(libc, number: int, *args: int) -> str:
    """The errno name a system call answers with these arguments, or ALLOWED when it succeeds.
    Every probe is written to fail or to do nothing when the filter is not there."""
    ctypes.set_errno(0)
    result = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(arg) for arg in args))
    if result >= 0:
        if result > 2 and number in _RETURNS_DESCRIPTOR:
            os.close(result)  # only where the filter is not there: nothing is left open
        return "ALLOWED"
    return errno.errorcode.get(ctypes.get_errno(), "E?")


def probes(pid: int) -> dict[str, tuple[int, ...]]:
    """Harmless arguments for every denied call: an invalid descriptor, a null pointer, signal 0,
    a lookup of a key that does not exist, no flag. Without the filter none of them changes
    anything (pidfd_open alone returns a descriptor, which probe closes)."""
    return {
        "socket": (-1, -1, -1),
        "socketpair": (-1, -1, -1, 0),
        "bind": (-1, 0, 0),
        "connect": (-1, 0, 0),
        "ptrace": (-1, 0, 0, 0),
        "process_vm_readv": (pid, 0, 0, 0, 0, 0),
        "process_vm_writev": (pid, 0, 0, 0, 0, 0),
        "kill": (pid, 0),
        "tkill": (pid, 0),
        "tgkill": (pid, pid, 0),
        "rt_sigqueueinfo": (pid, 0, 0),
        "rt_tgsigqueueinfo": (pid, pid, 0, 0),
        "pidfd_open": (pid, 0),
        "pidfd_send_signal": (-1, 0, 0, 0),
        "execve": (0, 0, 0),
        "execveat": (-1, 0, 0, 0, 0),
        "io_uring_setup": (0, 0),
        "io_uring_enter": (-1, 0, 0, 0, 0, 0),
        "io_uring_register": (-1, 0, 0, 0),
        "shmget": (_NO_SUCH_KEY, 0, 0),
        "msgget": (_NO_SUCH_KEY, 0),
        "semget": (_NO_SUCH_KEY, 0, 0),
        "mq_open": (0, 0, 0, 0),
        "unshare": (0,),
        "setns": (-1, 0),
    }


def try_denied(libc, machine: str, names: tuple[str, ...] = DENIED_NAMES) -> dict[str, str]:
    """What each denied call answers now: EPERM for every one when the filter is in force."""
    table, arguments = numbers(machine), probes(os.getpid())
    return {name: probe(libc, table[name], *arguments[name]) for name in names}


def harden(kind: str, libc=None, res=resource, machine: str | None = None, uid: int | None = None):
    """The whole lockdown of this process, then its verification. Returns libc for the report.
    Raises SandboxError naming the step that failed; the caller exits without doing anything."""
    machine = os.uname().machine if machine is None else machine
    if (os.getuid() if uid is None else uid) == 0:
        raise SandboxError("root")
    limits = LIMITS[kind]
    try:
        set_limits(limits, res)
    except (OSError, ValueError) as exc:
        raise SandboxError("limits") from exc
    libc = load_libc() if libc is None else libc
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise SandboxError("dumpable")
    install_filter(libc, machine)
    # Read back: what was asked for is what holds.
    if read_limits(limits, res) != limits:
        raise SandboxError("limits_not_set")
    if libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise SandboxError("still_dumpable")
    if libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
        raise SandboxError("new_privs_possible")
    if libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER:
        raise SandboxError("no_filter")
    # And it filters: two calls that succeed in any container without this filter.
    if set(try_denied(libc, machine, ("kill", "socket")).values()) != {"EPERM"}:
        raise SandboxError("filter_not_applied")
    return libc


class Budget:
    """The time a handler may use before it returns what it has: BUDGET_CPU_S of CPU or
    BUDGET_WALL_S of wall time, whichever comes first, counted from the moment the budget is
    made (right after the lockdown: what the process used to start, some tens of milliseconds,
    stays far inside the ten seconds between this budget and RLIMIT_CPU)."""

    def __init__(self, cpu_s: float = BUDGET_CPU_S, wall_s: float = BUDGET_WALL_S) -> None:
        self.cpu_s, self.wall_s = cpu_s, wall_s
        self.cpu_started = time.process_time()
        self.started = time.monotonic()

    def spent(self) -> bool:
        return (
            time.process_time() - self.cpu_started >= self.cpu_s
            or time.monotonic() - self.started >= self.wall_s
        )


def status_field(name: str) -> str:
    """One field of /proc/self/status ('' when the kernel does not print it)."""
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def writable_places(places: tuple[str, ...] = WRITE_PROBES) -> list[str]:
    """The directories of `places` where this process can create a file (an empty one: with
    RLIMIT_FSIZE at 0 it could not grow). What it creates it removes."""
    found = []
    for place in places:
        path = os.path.join(place, f".parser-probe-{os.getpid()}")
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except OSError:
            continue
        found.append(place)
        with contextlib.suppress(OSError):
            os.unlink(path)
    return found


def parent_hidden() -> bool:
    """Whether the environment of the server, the parent, is closed to this process. It has the
    same uid: only the server being non dumpable closes it."""
    try:
        with open(f"/proc/{os.getppid()}/environ", "rb") as handle:
            handle.read(1)
    except OSError:
        return True
    return False


def open_descriptors(highest: int = 256) -> tuple[list[int], int]:
    """The descriptors this process holds, and how many of them are sockets. Looked up one by
    one: a descriptor inherited above RLIMIT_NOFILE stays open, and a listing would open one."""
    held, sockets = [], 0
    for number in range(highest):
        try:
            mode = os.fstat(number).st_mode
        except OSError:
            continue
        held.append(number)
        sockets += stat.S_ISSOCK(mode)
    return held, sockets


def received_environment() -> int:
    """The size in bytes of the environment this process was started with. Not len(os.environ):
    Python adds LC_CTYPE to its own copy when it finds no locale, which an empty one has not.
    Read before the lockdown: the /proc entry of a non dumpable process belongs to root, and
    the process itself can no longer open it."""
    with open("/proc/self/environ", "rb") as handle:
        return len(handle.read())


def sandbox_report(kind: str, libc, machine: str, environment: int) -> dict[str, object]:
    """What the lockdown is in this process, measured now. Numbers, booleans and names from
    fixed lists only; the kernel release is the one string, and the worker checks its shape."""
    filters = status_field("Seccomp_filters")
    descriptors, sockets = open_descriptors()
    return {
        "seccomp": libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0),
        "filters": int(filters) if filters.isdigit() else -1,
        "no_new_privs": libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0),
        "dumpable": libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0),
        "capabilities": int(status_field("CapEff") or "0", 16),
        "limits": read_limits(LIMITS[kind]),
        "denied": try_denied(libc, machine),
        "isolated": sys.flags.isolated == 1,
        "environ": environment,
        "uid": os.getuid(),
        "descriptors": descriptors,
        "sockets": sockets,
        "writable": writable_places(),
        "parent_hidden": parent_hidden(),
        "cryptography_blocked": "cryptography" in sys.modules
        and sys.modules["cryptography"] is None,
        "machine": machine,
        "kernel": os.uname().release,
    }


def ping(kind: str, data: bytes, libc, machine: str, environment: int) -> dict[str, object]:
    import hashlib  # here: no other kind needs it, and a job should start light

    report = sandbox_report(kind, libc, machine, environment)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "ok": True,
        "kind": "ping",
        "sandbox": report,
        # The echo: the bytes arrived whole, and nothing of them goes back.
        "received": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "usage": {
            "cpu_ms": int((usage.ru_utime + usage.ru_stime) * 1000),
            "max_rss_kb": int(usage.ru_maxrss),
        },
    }


def usage(field=status_field) -> dict[str, int]:
    """What this process cost so far: CPU time, the highest resident memory (VmHWM) and the
    highest address space (VmPeak, the figure RLIMIT_AS bounds), 0 where the system does not
    say. Added to every answer that read a document: the worker logs it, and the limits of each
    kind are set from what real documents cost (tasks/ingestion/todo.md, Measurements).

    Not getrusage's ru_maxrss for the memory: on Linux that figure survives fork and execve, so
    a child reports at least the highest the server ever reached (77 MiB for every job once the
    server had held one 20 MiB document, measured in G2b). The two fields of /proc are those of
    this process's own memory since it was executed."""
    used = resource.getrusage(resource.RUSAGE_SELF)
    found = {"cpu_ms": int((used.ru_utime + used.ru_stime) * 1000)}
    for name, key in (("VmHWM", "max_rss_kb"), ("VmPeak", "max_vm_kb")):
        try:
            value = field(name).split(" ")[0]
        except OSError:
            value = ""
        found[key] = int(value) if value.isdigit() else 0
    return found


def load_handler(kind: str, directory: str = HERE):
    """The `handle` function of the file that parses `kind`, or None when that file is not in
    `directory` yet. Loaded by its path, under the name of the table, so that nothing installed
    in the interpreter can stand in for it."""
    name = HANDLERS[kind]
    path = os.path.join(directory, name + ".py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.handle


def failure(kind: str, code: str) -> dict[str, object]:
    return {"ok": False, "failure": kind, "code": code}


def run_handler(kind: str, data: bytes, options: dict, directory: str = HERE) -> dict[str, object]:
    """The answer of the handler of `kind`. A handler that cannot be loaded is the server's
    failure; a handler that raises while it parses could not read the file, whatever it raised
    (a hostile file must not be able to choose a sentence), except for the memory limit."""
    try:
        handle = load_handler(kind, directory)
    except MemoryError:
        return failure("server", "memory_limit")
    except Exception:
        return failure("server", "handler_error")
    if handle is None:
        return failure("server", "not_supported")
    try:
        answer = handle(data, options, Budget())
    except MemoryError:
        return failure("server", "memory_limit")
    except Exception:
        return failure("file", "unreadable")
    if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
        return failure("server", "handler_error")
    return answer


def encode(answer: dict[str, object]) -> bytes:
    """The answer as UTF-8 JSON. Not ASCII escapes: 500,000 characters outside the basic plane
    would be 6 MB of escapes and 2 MB of UTF-8, and the answer is capped at 4 MiB. A lone
    surrogate cannot be encoded and becomes `?`; the worker refuses any that is left."""
    try:
        text = json.dumps(answer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        payload = text.encode("utf-8", "replace")
    except (TypeError, ValueError, RecursionError):
        return encode(failure("server", "handler_error"))
    if len(payload) > MAX_ANSWER:
        return encode(failure("server", "output_cap"))
    return payload


def parse_arguments(argv: list[str]) -> tuple[str, dict]:
    """(kind, options) from the command line server.py wrote; SystemExit(USAGE_EXIT) otherwise."""
    if len(argv) != 3 or argv[1] not in HANDLERS:
        raise SystemExit(USAGE_EXIT)
    try:
        options = json.loads(argv[2])
    except (ValueError, RecursionError):
        raise SystemExit(USAGE_EXIT) from None
    if not isinstance(options, dict):
        raise SystemExit(USAGE_EXIT)
    return argv[1], options


# Not run in the test process: it locks the process that calls it for good. The docker marked
# tests of tests/ingestion/parser run it for real, in the container of the service.
def main(argv: list[str]) -> int:  # pragma: no cover
    sys.modules["cryptography"] = None  # type: ignore[assignment]
    kind, options = parse_arguments(argv)
    machine = os.uname().machine
    try:
        environment = received_environment()
        libc = harden(kind, machine=machine)
    except Exception:  # SandboxError, and anything else a step raised: closed either way
        return SANDBOX_EXIT
    # Only now: the directory of the handlers, so that a handler can import a helper beside it.
    sys.path.insert(0, HERE)
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
    except MemoryError:
        data, answer = b"", failure("server", "memory_limit")
    else:
        if len(data) > MAX_INPUT:
            answer = failure("server", "too_large")
        elif kind == "ping":
            answer = ping(kind, data, libc, machine, environment)
        else:
            answer = run_handler(kind, data, options)
            if answer.get("ok") is True:
                answer["usage"] = usage()
    del data
    sys.stdout.buffer.write(encode(answer))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
