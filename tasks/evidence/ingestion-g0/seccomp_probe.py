"""Can a Python process on Linux arm64 install a seccomp deny list on itself with the standard
library only (ctypes), without a privilege and without a new dependency? Throwaway probe for the
plan of the parser service (second review of PR #38, blocking finding 3). Not product code.

It builds a classic BPF program by hand: check the architecture, then answer EPERM to socket,
socketpair, connect, bind, ptrace, process_vm_readv, process_vm_writev, kill, tkill, tgkill,
execve and execveat, and allow everything else. It installs it after PR_SET_NO_NEW_PRIVS and then
tries each denied call, and checks that reading, writing a pipe and importing still work.

    docker run --rm --user 10002:10002 --cap-drop ALL --security-opt no-new-privileges \
        --read-only --network none -v "$PWD/tasks/evidence/ingestion-g0:/probe:ro" \
        python:3.12.15-slim-trixie python -I /probe/seccomp_probe.py
"""

import ctypes
import errno
import json
import os
import platform
import signal
import socket
import struct
import sys

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
PR_GET_SECCOMP = 21
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_KILL_PROCESS = 0x80000000
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_RET_K = 0x06

# Audit architecture and syscall numbers, from the kernel's unistd tables.
TABLES = {
    "aarch64": (
        0xC00000B7,
        {
            "socket": 198,
            "socketpair": 199,
            "bind": 200,
            "connect": 203,
            "ptrace": 117,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "kill": 129,
            "tkill": 130,
            "tgkill": 131,
            "execve": 221,
            "execveat": 281,
        },
    ),
    "x86_64": (
        0xC000003E,
        {
            "socket": 41,
            "socketpair": 53,
            "bind": 49,
            "connect": 42,
            "ptrace": 101,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "kill": 62,
            "tkill": 200,
            "tgkill": 234,
            "execve": 59,
            "execveat": 322,
        },
    ),
}


def program(arch: int, denied: list[int]) -> bytes:
    def ins(code: int, jt: int, jf: int, k: int) -> bytes:
        return struct.pack("HBBI", code, jt, jf, k)

    out = [
        ins(BPF_LD_W_ABS, 0, 0, 4),  # seccomp_data.arch
        ins(BPF_JEQ_K, 1, 0, arch),
        ins(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
        ins(BPF_LD_W_ABS, 0, 0, 0),  # seccomp_data.nr
    ]
    for index, number in enumerate(denied):
        # jump to the EPERM return, which sits after the remaining tests and the ALLOW return
        out.append(ins(BPF_JEQ_K, len(denied) - index, 0, number))
    out.append(ins(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    out.append(ins(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    return b"".join(out)


class Prog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def install() -> None:
    arch, numbers = TABLES[platform.machine()]
    code = program(arch, list(numbers.values()))
    buffer = ctypes.create_string_buffer(code, len(code))
    prog = Prog(len(code) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP")


def attempt(call) -> str:
    try:
        call()
    except PermissionError:
        return "EPERM"
    except OSError as exc:
        return f"other error: {errno.errorcode.get(exc.errno, exc.errno)}"
    return "ALLOWED"


def main() -> int:
    result: dict[str, object] = {
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "uid": os.getuid(),
    }
    install()
    libc = ctypes.CDLL(None, use_errno=True)
    result["seccomp_mode"] = libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0)
    result["denied"] = {
        "socket": attempt(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        "socket_unix": attempt(
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        ),
        "socketpair": attempt(socket.socketpair),
        "kill_parent": attempt(lambda: os.kill(os.getppid(), 0)),
        "kill_self": attempt(lambda: os.kill(os.getpid(), signal.SIGTERM)),
        "execve": attempt(
            lambda: os.execv(sys.executable, [sys.executable, "-c", "pass"])
        ),
        "ptrace": attempt(
            lambda: (
                (_ for _ in ()).throw(PermissionError())
                if libc.ptrace(16, os.getppid(), 0, 0) == -1
                and ctypes.get_errno() == errno.EPERM
                else None
            )
        ),
    }
    import zlib  # an import after the filter: opening and mapping files still works

    with open(sys.executable, "rb") as handle:
        head = handle.read(4)
    read_end, write_end = os.pipe()
    os.write(write_end, zlib.compress(b"x" * 100_000))
    result["still_works"] = {
        "import": True,
        "pipe": len(zlib.decompress(os.read(read_end, 65536))) == 100_000,
        "open_read": len(head) == 4,
    }
    ok = result["seccomp_mode"] == 2 and all(
        v == "EPERM" for v in result["denied"].values()
    )
    result["verdict"] = "feasible" if ok else "NOT as expected"
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
