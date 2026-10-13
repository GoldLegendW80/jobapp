"""A handler that behaves like a parser a hostile file took over. Test only: test_container.py
mounts it in the container of the parser service in the place of a real handler, so it runs
exactly where a parser runs, under sandbox/main.py's lockdown, and tries to get out.

`options["act"]` chooses what it does. `attempts` tries one thing after another and reports
how each one ended, as the name of an exception or of an errno, never raising: the test reads
the whole table. The other acts are the ones that end the process or the job.
"""
# ruff: noqa: S108, S606
# What the linter warns about is what this file is for: it writes where it should not and
# starts what it should not, to show that it cannot.

import ctypes
import errno
import os
import resource
import signal
import socket
import stat
import sys
import threading
import time

LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syscall.restype = ctypes.c_long
PTRACE_ATTACH = 16
PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4
PR_SET_SECCOMP = 22
# aarch64, x86_64
NUMBERS = {
    "io_uring_setup": (425, 425),
    "rt_sigqueueinfo": (138, 129),
    "rt_tgsigqueueinfo": (240, 297),
    "tkill": (130, 200),
    "tgkill": (131, 234),
    "process_vm_readv": (270, 310),
    "process_vm_writev": (271, 311),
    "execveat": (281, 322),
    "bind": (200, 49),
    "connect": (203, 42),
    "shmget": (194, 29),
    "msgget": (186, 68),
    "semget": (190, 64),
    "mq_open": (180, 240),
    "seccomp": (277, 317),
}
COLUMN = 0 if os.uname().machine == "aarch64" else 1


def outcome(action) -> str:
    """How `action` ended: the errno name of an OSError, else the exception's class, else what
    it returned (a call that goes through is the finding)."""
    try:
        result = action()
    except OSError as exc:
        return errno.errorcode.get(exc.errno, type(exc).__name__)
    except BaseException as exc:
        return type(exc).__name__
    return f"DONE:{result}"


def raw(name: str, *arguments: int) -> str:
    """A system call by its number, past anything Python or libc would check first."""

    def call() -> int:
        ctypes.set_errno(0)
        number = ctypes.c_long(NUMBERS[name][COLUMN])
        result = LIBC.syscall(number, *(ctypes.c_long(argument) for argument in arguments))
        if result < 0:
            raise OSError(ctypes.get_errno(), name)
        return result

    return outcome(call)


def read_file(path: str) -> int:
    with open(path, "rb") as handle:
        return len(handle.read(64))


def write_file(path: str) -> int:
    with open(path, "wb") as handle:
        return handle.write(b"x")


def create_file(path: str) -> str:
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    os.unlink(path)
    return "created"


def open_many() -> int:
    """Descriptors until the limit says no: how many this process can hold in all."""
    held, refusal = [], 0
    while len(held) < 4096:
        try:
            held.append(os.open("/dev/null", os.O_RDONLY))
        except OSError as exc:
            refusal = exc.errno
            break
    top = max(held, default=-1) + 1
    for number in held:
        os.close(number)
    return top if refusal == errno.EMFILE else -1


def descriptors() -> dict:
    kinds = {}
    for number in range(256):
        try:
            mode = os.fstat(number).st_mode
        except OSError:
            continue
        if stat.S_ISFIFO(mode):
            kinds[str(number)] = "pipe"
        elif stat.S_ISSOCK(mode):
            kinds[str(number)] = "socket"
        elif stat.S_ISCHR(mode):
            kinds[str(number)] = "device"
        else:
            kinds[str(number)] = "other"
    return kinds


def start_thread() -> str:
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return "started"


def fork() -> str:
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return "forked"


def run_program() -> str:
    import subprocess

    return subprocess.run([sys.executable, "-c", "pass"], check=False).returncode


def ptrace_parent() -> int:
    ctypes.set_errno(0)
    result = LIBC.ptrace(PTRACE_ATTACH, os.getppid(), 0, 0)
    if result < 0:
        raise OSError(ctypes.get_errno(), "ptrace")
    return result


def mount_proc() -> str:
    ctypes.set_errno(0)
    if LIBC.mount(b"proc", b"/proc", b"proc", 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "mount")
    return "mounted"


def inherited_socket() -> str:
    """Any descriptor above the three pipes, used as a socket."""
    for number in range(3, 256):
        try:
            os.fstat(number)
        except OSError:
            continue
        return outcome(lambda number=number: socket.socket(fileno=number).send(b"x"))
    return "none"


def attempts() -> dict:
    parent = os.getppid()
    me = os.getpid()
    here = os.path.dirname(os.path.abspath(__file__))
    found = {
        # a connection
        "socket_inet": outcome(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        "socket_inet6": outcome(lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)),
        "socket_unix": outcome(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)),
        "socket_netlink": outcome(lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW)),
        "socketpair": outcome(socket.socketpair),
        "create_connection": outcome(lambda: socket.create_connection(("127.0.0.1", 7070), 2)),
        "resolve": outcome(lambda: socket.getaddrinfo("example.org", 80)),
        "bind": raw("bind", 0, 0, 0),
        "connect": raw("connect", 0, 0, 0),
        "inherited_socket": inherited_socket(),
        # a new process, a thread, another program
        "fork": outcome(fork),
        "thread": outcome(start_thread),
        "subprocess": outcome(run_program),
        "execv": outcome(lambda: os.execv(sys.executable, [sys.executable, "-c", "pass"])),
        "posix_spawn": outcome(lambda: os.posix_spawn(sys.executable, [sys.executable], {})),
        "execveat": raw("execveat", -1, 0, 0, 0, 0),
        # the parent, which is the server: signalled, traced, read
        "kill_parent_0": outcome(lambda: os.kill(parent, 0)),
        "kill_parent_term": outcome(lambda: os.kill(parent, signal.SIGTERM)),
        "kill_parent_kill": outcome(lambda: os.kill(parent, signal.SIGKILL)),
        "kill_everyone": outcome(lambda: os.kill(-1, signal.SIGKILL)),
        "killpg": outcome(lambda: os.killpg(os.getpgid(parent), signal.SIGKILL)),
        "kill_self": outcome(lambda: os.kill(me, signal.SIGTERM)),
        "raise_signal": outcome(lambda: signal.raise_signal(signal.SIGTERM)),
        "pthread_kill": outcome(
            lambda: signal.pthread_kill(threading.main_thread().ident, signal.SIGTERM)
        ),
        "tkill": raw("tkill", parent, signal.SIGKILL),
        "tgkill": raw("tgkill", parent, parent, signal.SIGKILL),
        "rt_sigqueueinfo": raw("rt_sigqueueinfo", parent, signal.SIGKILL, 0),
        "rt_tgsigqueueinfo": raw("rt_tgsigqueueinfo", parent, parent, signal.SIGKILL, 0),
        "pidfd_open": outcome(lambda: os.pidfd_open(parent)),
        "pidfd_send_signal": outcome(lambda: signal.pidfd_send_signal(0, signal.SIGKILL)),
        "ptrace_attach": outcome(ptrace_parent),
        "process_vm_readv": raw("process_vm_readv", parent, 0, 0, 0, 0, 0),
        "process_vm_writev": raw("process_vm_writev", parent, 0, 0, 0, 0, 0),
        "parent_environ": outcome(lambda: read_file(f"/proc/{parent}/environ")),
        "parent_mem": outcome(lambda: read_file(f"/proc/{parent}/mem")),
        "parent_maps": outcome(lambda: read_file(f"/proc/{parent}/maps")),
        "parent_fd": outcome(lambda: len(os.listdir(f"/proc/{parent}/fd"))),
        "parent_cwd": outcome(lambda: os.readlink(f"/proc/{parent}/cwd")),
        "parent_exe": outcome(lambda: os.readlink(f"/proc/{parent}/exe")),
        "pid1_environ": outcome(lambda: read_file("/proc/1/environ")),
        # io_uring, and the filter itself
        "io_uring_setup": raw("io_uring_setup", 4, 0),
        "filter_off": outcome(lambda: LIBC.prctl(PR_SET_SECCOMP, 0, 0, 0, 0)),
        # IPC objects, which would outlive this process
        "shmget": raw("shmget", 0, 4096, 0o1600),
        "msgget": raw("msgget", 0, 0o1600),
        "semget": raw("semget", 0, 1, 0o1600),
        "mq_open": raw("mq_open", 0, 0, 0, 0),
        # a file
        "write_tmp": outcome(lambda: write_file("/tmp/hostile")),
        "write_var_tmp": outcome(lambda: write_file("/var/tmp/hostile")),
        "write_dev_shm": outcome(lambda: write_file("/dev/shm/hostile")),
        "create_dev_shm": outcome(lambda: create_file("/dev/shm/hostile")),
        "create_dev_mqueue": outcome(lambda: create_file("/dev/mqueue/hostile")),
        "write_app": outcome(lambda: write_file("/app/hostile")),
        "write_sandbox": outcome(lambda: write_file(os.path.join(here, "docx.py"))),
        "write_root": outcome(lambda: write_file("/hostile")),
        "write_home": outcome(lambda: write_file(os.path.expanduser("~/hostile"))),
        "write_cwd": outcome(lambda: write_file("hostile")),
        "mkdir": outcome(lambda: os.mkdir("/tmp/hostile-dir")),
        "memfd_grow": outcome(lambda: os.write(os.memfd_create("hostile"), b"x")),
        # the limits, raised again
        "raise_nproc": outcome(lambda: resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))),
        "raise_nofile": outcome(lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096))),
        "raise_fsize": outcome(lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20,) * 2)),
        "raise_as": outcome(lambda: resource.setrlimit(resource.RLIMIT_AS, (-1, -1))),
        "raise_core": outcome(lambda: resource.setrlimit(resource.RLIMIT_CORE, (-1, -1))),
        "raise_cpu": outcome(lambda: resource.setrlimit(resource.RLIMIT_CPU, (-1, -1))),
        # privileges
        "setuid_0": outcome(lambda: os.setuid(0)),
        "setgid_0": outcome(lambda: os.setgid(0)),
        "chroot": outcome(lambda: os.chroot("/")),
        "unshare_user": outcome(lambda: os.unshare(os.CLONE_NEWUSER)),
        "unshare_net": outcome(lambda: os.unshare(os.CLONE_NEWNET)),
        "mount_proc": outcome(mount_proc),
        # the application, and what the parser must never load
        "import_app": outcome(lambda: __import__("app")),
        "import_app_config": outcome(lambda: __import__("app.config")),
        "import_cryptography": outcome(lambda: __import__("cryptography")),
        "import_cryptography_fernet": outcome(lambda: __import__("cryptography.fernet")),
        "import_psycopg": outcome(lambda: __import__("psycopg").__name__),
        "read_env_file": outcome(lambda: read_file("/app/.env")),
    }
    return found


def state() -> dict:
    """What this process is, read without trying anything."""
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "groups": os.getgroups(),
        "pid": os.getpid(),
        "parent": os.getppid(),
        "environ": dict(os.environ),
        "argv_kind": sys.argv[1],
        "isolated": sys.flags.isolated,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "path": sys.path,
        "cwd": os.getcwd(),
        "descriptors": descriptors(),
        "open_limit_reached_at": open_many(),
        "dumpable": LIBC.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0),
        "limits": {
            name: list(resource.getrlimit(getattr(resource, "RLIMIT_" + name.upper())))
            for name in ("as", "cpu", "fsize", "nofile", "nproc", "core", "msgqueue")
        },
        "cryptography_in_modules": sys.modules.get("cryptography", "absent") is None,
        "modules_of_the_app": sorted(name for name in sys.modules if name.split(".")[0] == "app"),
        "processes": sorted(int(name) for name in os.listdir("/proc") if name.isdigit()),
        "kernel": os.uname().release,
    }


def handle(data: bytes, options: dict, budget) -> dict:
    act = options.get("act", "echo")
    if act == "attempts":
        return {"ok": True, "attempts": attempts()}
    if act == "state":
        return {"ok": True, "state": state(), "received": len(data), "spent": budget.spent()}
    if act == "answer":  # whatever the test wants a taken over child to say
        return options["answer"]
    if act == "sleep":  # blocked: only the wall clock of the server ends it
        time.sleep(float(options["s"]))
    elif act == "spin":  # busy: RLIMIT_CPU ends it
        if options.get("ignore_sigxcpu"):  # and deaf to the polite signal: the hard limit does
            signal.signal(signal.SIGXCPU, signal.SIG_IGN)
        while True:
            pass
    elif act == "allocate":  # past RLIMIT_AS at once
        return {"ok": True, "size": len(bytearray(int(options["mib"]) * 1024 * 1024))}
    elif act == "allocate_and_touch":  # in steps, each one written to, until it fails
        blocks = []
        for _ in range(int(options["mib"])):
            blocks.append(bytearray(os.urandom(1024)) * 1024)
        return {"ok": True, "size": len(blocks)}
    elif act == "swallow_memory_error":  # a parser that catches MemoryError and goes on
        try:
            bytearray(int(options["mib"]) * 1024 * 1024)
        except MemoryError:
            return {"ok": True, "text": "went on after MemoryError"}
    elif act == "flood":  # more than the answer may be, straight into the pipe
        block = b"x" * 65536
        for _ in range(int(options["blocks"])):
            os.write(1, block)
    elif act == "text":  # an answer of that many characters, one of them given
        return {"ok": True, "text": options.get("char", "x") * int(options["chars"])}
    elif act == "stderr":  # words for the log of the service
        os.write(2, options["text"].encode() * 100)
        sys.stderr.write(options["text"])
        return {"ok": True, "text": "wrote to stderr"}
    elif act == "write_raw":  # its own bytes as the answer, past main.py's encoder
        os.write(1, options["text"].encode("utf-8", "surrogatepass"))
        os._exit(0)
    elif act == "segfault":
        ctypes.string_at(0)
    elif act == "abort":
        os.abort()
    elif act == "exit":
        sys.exit(int(options["status"]))
    elif act == "os_exit":
        os._exit(int(options["status"]))
    elif act == "undumpable_off":  # makes itself dumpable again, then dies of a fault
        LIBC.prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)
        return {"ok": True, "dumpable": LIBC.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)}
    elif act == "raise":
        raise ValueError(options.get("text", "words of a document"))
    elif act == "recurse":
        return handle(data, options, budget)
    elif act == "leave_ipc":  # tries to leave something for the job after this one
        return {
            "ok": True,
            "attempts": {
                "shmget": raw("shmget", 0x48305354, 4096, 0o1600),
                "msgget": raw("msgget", 0x48305354, 0o1600),
                "semget": raw("semget", 0x48305354, 1, 0o1600),
                "create_dev_mqueue": outcome(lambda: create_file("/dev/mqueue/left-behind")),
                "create_dev_shm": outcome(lambda: create_file("/dev/shm/left-behind")),
            },
        }
    return {"ok": True, "received": len(data)}
