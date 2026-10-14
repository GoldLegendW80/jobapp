"""The child of the parser service, sandbox/main.py, where it can be tested without locking the
test process: the filter program run instruction by instruction, the lockdown against a libc and
a `resource` that record what they are asked and can refuse any step, the handler table, and
what a handler's every way of ending becomes. On any system.

The lockdown for real (each limit, each denied call, in the container of the service on Linux)
is test_container.py."""

import ast
import ctypes
import errno
import json
import os
import struct
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.ingestion.parser.helpers import FLAT_NAMES, SANDBOX, restoring_modules, sandbox_module

main = sandbox_module("main")
server = sandbox_module("server")

ALLOW = 0x7FFF0000
EPERM = 0x00050000 | errno.EPERM
KILL = 0x80000000
AARCH64 = 0xC00000B7
X86_64 = 0xC000003E
ARM_32 = 0x40000028  # a 32 bit process on an arm64 kernel
I386 = 0x40000003


def run_filter(code: bytes, arch: int, number: int) -> int:
    """What the kernel would answer: the classic BPF program run on one seccomp_data."""
    instructions = [struct.unpack_from("HBBI", code, at) for at in range(0, len(code), 8)]
    # struct seccomp_data: int nr, __u32 arch, __u64 instruction_pointer, __u64 args[6]. The
    # program loads nr as an unsigned word, which is how -1 and the x32 bit reach it.
    data = struct.pack("=IIQ6Q", number & 0xFFFFFFFF, arch, 0, 0, 0, 0, 0, 0, 0)
    accumulator, at = 0, 0
    for _ in range(len(instructions) + 1):
        code_, jump_true, jump_false, k = instructions[at]
        at += 1
        if code_ == main.BPF_LD_W_ABS:
            accumulator = struct.unpack_from("=I", data, k)[0]
        elif code_ == main.BPF_JEQ_K:
            at += jump_true if accumulator == k else jump_false
        elif code_ == main.BPF_JGE_K:
            at += jump_true if accumulator >= k else jump_false
        elif code_ == main.BPF_RET_K:
            return k
        else:
            raise AssertionError(f"unknown instruction {code_:#x}")
    raise AssertionError("the program does not end")


def filter_of(machine: str) -> tuple[bytes, int]:
    arch, refuse_x32 = main.ARCHITECTURES[machine]
    return main.program(arch, list(main.numbers(machine).values()), refuse_x32), arch


# --- the filter program -------------------------------------------------------------------------


@pytest.mark.parametrize("machine", ["aarch64", "x86_64"])
def test_every_denied_call_answers_eperm_and_nothing_else_does(machine: str) -> None:
    code, arch = filter_of(machine)
    denied = main.numbers(machine)
    assert len(denied) == len(main.DENIED) == 25
    for name, number in denied.items():
        assert run_filter(code, arch, number) == EPERM, name
    for number in range(600):
        if number not in denied.values():
            assert run_filter(code, arch, number) == ALLOW, number


@pytest.mark.parametrize("machine", ["aarch64", "x86_64"])
def test_another_architecture_ends_the_process(machine: str) -> None:
    code, arch = filter_of(machine)
    for other in {AARCH64, X86_64, ARM_32, I386, 0} - {arch}:
        for number in (0, 41, 59, 198, 221):
            assert run_filter(code, other, number) == KILL


def test_x32_numbers_are_refused_on_x86_64_and_only_there() -> None:
    """The x32 calls share the audit architecture of x86_64: `socket` is also 0x40000029 there,
    and a list by number alone would let it through."""
    code, arch = filter_of("x86_64")
    for number in (0x40000000, 0x40000000 + 41, 0x40000000 + 59, 0x400003FF, 0xFFFFFFFF):
        assert run_filter(code, arch, number) == EPERM, hex(number)
    assert run_filter(code, arch, 0x3FFFFFFF) == ALLOW
    code, arch = filter_of("aarch64")
    assert run_filter(code, arch, 0x40000000 + 198) == ALLOW  # no such call on arm64: ENOSYS


def test_the_program_is_well_formed() -> None:
    for machine in main.ARCHITECTURES:
        code, _ = filter_of(machine)
        instructions = [struct.unpack_from("HBBI", code, at) for at in range(0, len(code), 8)]
        assert len(code) % 8 == 0
        assert len(instructions) <= 4096
        assert instructions[-1] == (main.BPF_RET_K, 0, 0, EPERM)
        assert instructions[-2] == (main.BPF_RET_K, 0, 0, ALLOW)
        for at, (code_, jump_true, jump_false, _) in enumerate(instructions):
            if code_ in (main.BPF_JEQ_K, main.BPF_JGE_K):
                assert at + 1 + max(jump_true, jump_false) < len(instructions)


def test_a_list_too_long_for_one_byte_jumps_is_refused() -> None:
    with pytest.raises(ValueError, match="too many"):
        main.program(AARCH64, list(range(251)), False)
    main.program(AARCH64, list(range(250)), False)


def test_the_tables_hold_the_calls_the_plan_names_once_each() -> None:
    names = main.DENIED_NAMES
    assert len(set(names)) == len(names)
    for column in (1, 2):
        numbers = [row[column] for row in main.DENIED]
        assert len(set(numbers)) == len(numbers)
    # The probe of G0 and the additions of the review: signals by every call, pidfd, io_uring.
    for name in (
        "socket", "socketpair", "bind", "connect", "ptrace", "process_vm_readv",
        "process_vm_writev", "kill", "tkill", "tgkill", "execve", "execveat",
        "rt_sigqueueinfo", "rt_tgsigqueueinfo", "pidfd_open", "pidfd_send_signal",
        "io_uring_setup", "io_uring_enter", "io_uring_register",
    ):  # fmt: skip
        assert name in names
    assert main.numbers("aarch64")["socket"] == 198
    assert main.numbers("x86_64")["socket"] == 41
    assert main.numbers("aarch64")["execve"] == 221
    assert main.numbers("x86_64")["execve"] == 59
    assert set(main.probes(1)) == set(names)


def test_the_three_files_agree_on_the_deny_list_the_limits_and_the_sizes() -> None:
    """main.py, server.py and the worker's client are standalone on purpose, so each holds its
    own copy of what it needs. This keeps the copies the same."""
    from app.ingestion import parser_client

    assert server.REQUIRED_DENIED == main.DENIED_NAMES == parser_client.DENIED_NAMES
    assert server.EXPECTED_LIMITS == main.LIMITS["ping"] == dict(parser_client.EXPECTED_LIMITS)
    assert set(server.KINDS) == set(main.HANDLERS) == set(parser_client.KINDS)
    assert server.MAX_SIZE == main.MAX_INPUT == parser_client.MAX_SIZE == 20 * 1024 * 1024
    assert server.MAX_ANSWER == main.MAX_ANSWER == parser_client.MAX_ANSWER == 4 * 1024 * 1024
    assert server.MAX_HEADER == parser_client.MAX_HEADER == 4096
    assert server.SANDBOX_EXIT == main.SANDBOX_EXIT != 0
    assert set(server.CODES) - {"ok"} == parser_client.SERVER_CODES
    assert server.DEFAULT_PORT == parser_client.PORT == 7070
    # The worker waits longer than the parser lets a child live, and as long as a job queues.
    assert server.WALL_S < parser_client.TIMEOUT_S == server.QUEUE_WAIT_S
    assert main.BUDGET_CPU_S < main.DEFAULT_LIMITS["cpu"] < server.WALL_S
    assert main.BUDGET_WALL_S < server.WALL_S


def test_the_limits_are_those_of_the_plan() -> None:
    assert main.DEFAULT_LIMITS == {
        "as": 512 * 1024 * 1024,
        "cpu": 45,
        "fsize": 0,
        "nofile": 16,
        "nproc": 0,
        "core": 0,
        "msgqueue": 0,
    }
    assert set(main.LIMITS) == set(main.HANDLERS)
    assert server.WALL_S == 60
    assert server.READ_TIMEOUT_S == 10
    assert server.QUEUE_WAIT_S == 75


@pytest.mark.parametrize("name", ["main.py", "server.py"])
def test_the_two_files_import_the_standard_library_and_nothing_else(name: str) -> None:
    """Standalone: no `app`, no `cryptography`, nothing installed. They run under `python -I`
    from an absolute path, in a process that must hold nothing of the application."""
    tree = ast.parse((SANDBOX / name).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import needs a package"
            imported.add((node.module or "").split(".")[0])
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
    assert not imported & {"app", "cryptography", "subprocess", "multiprocessing", "pickle"}
    assert not (SANDBOX / "__init__.py").exists(), "the sandbox is no package of the app"


# --- the lockdown, against fakes that record and can refuse ------------------------------------


class FakeResource:
    """`resource` as main.py uses it: remembers every limit set."""

    RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NOFILE = 1, 2, 3, 4
    RLIMIT_NPROC, RLIMIT_CORE, RLIMIT_MSGQUEUE = 5, 6, 7

    def __init__(self, refuse: int | None = None, ignore: int | None = None) -> None:
        self.limits: dict[int, tuple[int, int]] = {}
        self.refuse, self.ignore = refuse, ignore

    def setrlimit(self, which: int, value: tuple[int, int]) -> None:
        if which == self.refuse:
            raise ValueError("not allowed to raise maximum limit")
        if which != self.ignore:
            self.limits[which] = value

    def getrlimit(self, which: int) -> tuple[int, int]:
        return self.limits.get(which, (2**63 - 1, 2**63 - 1))


class FakeLibc:
    """libc as main.py uses it. `fail` names the prctl options that are refused; `leaky` makes
    the filter accept everything although it was installed."""

    def __init__(self, fail: tuple[int, ...] = (), leaky: bool = False) -> None:
        self.calls: list[int] = []
        self.fail, self.leaky = fail, leaky
        self.state = {main.PR_GET_DUMPABLE: 1, main.PR_GET_NO_NEW_PRIVS: 0, main.PR_GET_SECCOMP: 0}

    def prctl(self, option: int, first: int, *_: int) -> int:
        self.calls.append(option)
        if option in self.fail:
            return -1
        if option == main.PR_SET_DUMPABLE:
            self.state[main.PR_GET_DUMPABLE] = first
        elif option == main.PR_SET_NO_NEW_PRIVS:
            self.state[main.PR_GET_NO_NEW_PRIVS] = first
        elif option == main.PR_SET_SECCOMP:
            assert self.state[main.PR_GET_NO_NEW_PRIVS] == 1, "no_new_privs comes first"
            assert first == main.SECCOMP_MODE_FILTER
            self.state[main.PR_GET_SECCOMP] = first
        return self.state.get(option, 0)

    def syscall(self, *_: object) -> int:
        filtered = self.state[main.PR_GET_SECCOMP] == main.SECCOMP_MODE_FILTER and not self.leaky
        ctypes.set_errno(errno.EPERM if filtered else 0)
        return -1 if filtered else 0


def harden(libc: FakeLibc, res: FakeResource, **more: object) -> object:
    arguments = {"machine": "aarch64", "uid": 10002, **more}
    return main.harden("ping", libc=libc, res=res, **arguments)  # type: ignore[arg-type]


def test_the_lockdown_sets_every_limit_then_the_attributes_then_the_filter() -> None:
    libc, res = FakeLibc(), FakeResource()
    assert harden(libc, res) is libc
    assert res.limits == {
        res.RLIMIT_AS: (512 * 1024 * 1024,) * 2,
        # SIGXCPU at 45 s; SIGKILL a second later, for a child that ignores it.
        res.RLIMIT_CPU: (45, 46),
        res.RLIMIT_FSIZE: (0, 0),
        res.RLIMIT_NOFILE: (16, 16),
        res.RLIMIT_NPROC: (0, 0),
        res.RLIMIT_CORE: (0, 0),
        res.RLIMIT_MSGQUEUE: (0, 0),
    }
    setting = [call for call in libc.calls if call in (4, 38, 22)]
    assert setting == [main.PR_SET_DUMPABLE, main.PR_SET_NO_NEW_PRIVS, main.PR_SET_SECCOMP]
    assert libc.state == {
        main.PR_GET_DUMPABLE: 0,
        main.PR_GET_NO_NEW_PRIVS: 1,
        main.PR_GET_SECCOMP: 2,
    }


@pytest.mark.parametrize(
    ("libc", "res", "more", "step"),
    [
        (FakeLibc(), FakeResource(), {"uid": 0}, "root"),
        (FakeLibc(), FakeResource(refuse=FakeResource.RLIMIT_NPROC), {}, "limits"),
        (FakeLibc(), FakeResource(ignore=FakeResource.RLIMIT_AS), {}, "limits_not_set"),
        (FakeLibc(fail=(main.PR_SET_DUMPABLE,)), FakeResource(), {}, "dumpable"),
        (FakeLibc(fail=(main.PR_SET_NO_NEW_PRIVS,)), FakeResource(), {}, "no_new_privs"),
        (FakeLibc(fail=(main.PR_SET_SECCOMP,)), FakeResource(), {}, "seccomp"),
        (FakeLibc(), FakeResource(), {"machine": "riscv64"}, "architecture"),
        (FakeLibc(leaky=True), FakeResource(), {}, "filter_not_applied"),
    ],
)
def test_the_lockdown_fails_closed_on_every_step(
    libc: FakeLibc, res: FakeResource, more: dict, step: str
) -> None:
    """Whatever cannot be done, harden raises and the process ends having handled nothing. The
    last case is a kernel that takes the filter and does not apply it."""
    with pytest.raises(main.SandboxError, match=f"^{step}$"):
        harden(libc, res, **more)
    if step in ("no_new_privs", "seccomp", "architecture"):
        assert libc.state[main.PR_GET_SECCOMP] == 0


def test_a_filter_refused_is_never_reported_as_installed() -> None:
    libc = FakeLibc(fail=(main.PR_SET_NO_NEW_PRIVS,))
    with pytest.raises(main.SandboxError):
        main.install_filter(libc, "aarch64")
    assert main.PR_SET_SECCOMP not in libc.calls, "no filter without no_new_privs"


def test_a_probe_says_allowed_when_the_call_goes_through() -> None:
    libc = FakeLibc()
    assert main.try_denied(libc, "aarch64", ("kill", "socket")) == {"kill": "ALLOWED"} | {
        "socket": "ALLOWED"
    }
    libc.state[main.PR_GET_SECCOMP] = 2
    assert set(main.try_denied(libc, "x86_64").values()) == {"EPERM"}
    assert list(main.try_denied(libc, "x86_64")) == list(main.DENIED_NAMES)


# --- handlers -----------------------------------------------------------------------------------


def write_handler(directory: Path, name: str, body: str) -> None:
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def forget_handlers() -> Iterator[None]:
    """A handler is loaded under its own name: each test starts, and ends, with the entries of
    sys.modules it found."""
    with restoring_modules(*FLAT_NAMES):
        yield


def test_usage_is_the_cpu_time_and_the_two_peaks_of_this_process_alone() -> None:
    """VmHWM and VmPeak of /proc, not ru_maxrss: that one survives fork and execve on Linux, so
    a child would report the highest the server ever reached."""
    fields = {"VmHWM": "38012 kB", "VmPeak": "53440 kB"}
    found = main.usage(field=lambda name: fields[name])
    assert (found["max_rss_kb"], found["max_vm_kb"]) == (38012, 53440)
    assert found["cpu_ms"] > 0
    assert set(found) == {"cpu_ms", "max_rss_kb", "max_vm_kb"}

    def no_proc(_: str) -> str:
        raise FileNotFoundError("/proc/self/status")

    assert main.usage(field=no_proc) == {
        "cpu_ms": main.usage(field=no_proc)["cpu_ms"],
        "max_rss_kb": 0,
        "max_vm_kb": 0,
    }
    assert main.usage(field=lambda _: "") == {**main.usage(field=lambda _: ""), "max_rss_kb": 0}


def test_the_pdf_reader_has_half_the_address_space_of_the_other_kinds() -> None:
    """Set from the measurements of G2b (tasks/ingestion/todo.md): 256 MiB, where the worst
    child of every fixture reached 95. The two readers of an archive have the same since G7,
    from their own measurements, and since G8 the renderer; the image reader has 320 MiB,
    where 25 megapixels of progressive JPEG reached 203. The ping keeps the 512 MiB the
    healthcheck expects."""
    for kind in ("pdf_text", "docx", "linkedin_zip", "pdf_render"):
        assert main.LIMITS[kind] == {**main.DEFAULT_LIMITS, "as": 256 * main.MIB}
    assert main.LIMITS["image"] == {**main.DEFAULT_LIMITS, "as": 320 * main.MIB}
    assert (main.IMAGE_AS, main.PDF_RENDER_AS) == (320 * main.MIB, 256 * main.MIB)
    assert main.LIMITS["ping"] == main.DEFAULT_LIMITS
    assert main.LIMITS["pdf_text"] is not main.LIMITS["ping"]


def test_a_kind_without_its_file_is_not_supported_yet(tmp_path: Path) -> None:
    for kind in ("pdf_text", "docx", "linkedin_zip", "image", "pdf_render"):
        assert main.load_handler(kind, str(tmp_path)) is None
        assert main.run_handler(kind, b"%PDF-", {}, str(tmp_path)) == {
            "ok": False,
            "failure": "server",
            "code": "not_supported",
        }


def test_the_table_names_the_five_handlers_and_the_tasks_add_their_files() -> None:
    """G2b added pdf_text.py; G7 and G8 each add theirs: until then the service answers
    not_supported for their kinds."""
    present = sorted(path.name for path in SANDBOX.glob("*.py"))
    assert set(present) >= {"main.py", "server.py", "pdf_text.py"}
    assert main.HANDLERS["ping"] is None
    assert sorted(name for name in main.HANDLERS.values() if name) == [
        "docx", "image", "linkedin_zip", "mail_html", "pdf_render", "pdf_text",
    ]  # fmt: skip


def test_a_handler_is_called_with_the_bytes_the_options_and_a_budget(tmp_path: Path) -> None:
    write_handler(
        tmp_path,
        "pdf_text",
        "def handle(data, options, budget):\n"
        "    return {'ok': True, 'text': data.decode() + options['more'],"
        " 'pages': 1, 'pages_text': 1, 'pages_image': 0, 'truncated': budget.spent()}\n",
    )
    assert main.run_handler("pdf_text", b"abc", {"more": "d"}, str(tmp_path)) == {
        "ok": True,
        "text": "abcd",
        "pages": 1,
        "pages_text": 1,
        "pages_image": 0,
        "truncated": False,
    }


def test_a_handler_is_loaded_from_its_file_and_not_from_what_is_installed(tmp_path: Path) -> None:
    """`docx` and `image` could be the names of installed packages one day: the table names a
    file of the directory, and that file is what runs."""
    write_handler(tmp_path, "docx", "def handle(d, o, b):\n    return {'ok': True, 'text': 'x'}\n")
    assert main.run_handler("docx", b"", {}, str(tmp_path)) == {"ok": True, "text": "x"}
    assert sys.modules["docx"].__file__ == str(tmp_path / "docx.py")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "def handle(d, o, b):\n    raise ValueError('a sentence of the file')\n",
            "file/unreadable",
        ),
        ("def handle(d, o, b):\n    return handle(d, o, b)\n", "file/unreadable"),
        ("def handle(d, o, b):\n    raise MemoryError\n", "server/memory_limit"),
        ("def handle(d, o, b):\n    return 'not a dict'\n", "server/handler_error"),
        ("def handle(d, o, b):\n    return {'ok': 'yes'}\n", "server/handler_error"),
        ("raise RuntimeError('at import')\n", "server/handler_error"),
        ("import a_module_that_is_not_installed\n", "server/handler_error"),
        ("raise MemoryError\n", "server/memory_limit"),
        ("x = 1\n", "server/handler_error"),
    ],
)
def test_every_way_a_handler_fails_is_a_code_never_its_words(
    tmp_path: Path, body: str, expected: str
) -> None:
    write_handler(tmp_path, "image", body)
    answer = main.run_handler("image", b"", {}, str(tmp_path))
    assert f"{answer['failure']}/{answer['code']}" == expected
    assert set(answer) == {"ok", "failure", "code"}
    assert answer["ok"] is False


def test_the_answer_is_utf8_and_fits_500000_characters_outside_the_basic_plane() -> None:
    """ASCII escapes would be 12 bytes for each of these characters, 6 MB, over the 4 MiB cap."""
    text = "\U0001f600" * 500_000
    payload = main.encode({"ok": True, "text": text})
    assert len(payload) < 2_100_000 < main.MAX_ANSWER
    assert json.loads(payload)["text"] == text
    assert b"\\u" not in payload


def test_what_cannot_be_encoded_is_replaced_or_reported() -> None:
    assert json.loads(main.encode({"ok": True, "text": "a\ud800b"}))["text"] == "a?b"
    for answer in (
        {"ok": True, "x": float("nan")},
        {"ok": True, "x": object()},
        {"ok": True, "x": {1, 2}},
    ):
        assert json.loads(main.encode(answer)) == {
            "ok": False,
            "failure": "server",
            "code": "handler_error",
        }
    nested: list = []
    for _ in range(100_000):
        nested = [nested]
    assert json.loads(main.encode({"ok": True, "x": nested}))["code"] == "handler_error"
    too_big = main.encode({"ok": True, "text": "x" * (main.MAX_ANSWER + 1)})
    assert json.loads(too_big) == {"ok": False, "failure": "server", "code": "output_cap"}


@pytest.mark.parametrize(
    "argv",
    [
        ["main.py"],
        ["main.py", "ping"],
        ["main.py", "ping", "{}", "more"],
        ["main.py", "no_such_kind", "{}"],
        ["main.py", "../server", "{}"],
        ["main.py", "ping", "not json"],
        ["main.py", "ping", "[1]"],
        ["main.py", "ping", "[" * 100_000],
    ],
)
def test_a_command_line_the_server_did_not_write_ends_the_process(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main.parse_arguments(argv)
    assert exit_.value.code == main.USAGE_EXIT


def test_a_command_line_of_the_server_is_read() -> None:
    assert main.parse_arguments(["main.py", "pdf_render", '{"from":0,"to":7}']) == (
        "pdf_render",
        {"from": 0, "to": 7},
    )


def test_the_budget_is_spent_after_its_cpu_or_its_wall_time() -> None:
    assert not main.Budget().spent()
    assert main.Budget(cpu_s=0.0).spent()
    assert main.Budget(wall_s=0.0).spent()
    # Counted from its own start, not from the start of the process: this test process may
    # have run for minutes (it had, in CI, the first time), a child has run for milliseconds.
    budget = main.Budget(cpu_s=0.05, wall_s=3600)
    assert not budget.spent()
    while time.process_time() - budget.cpu_started < 0.06:
        sum(range(10_000))
    assert budget.spent()
    assert time.process_time() > 0.05
    assert (main.BUDGET_CPU_S, main.BUDGET_WALL_S) == (35.0, 40.0)


def test_places_a_file_can_be_created_in_are_found_and_left_clean(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing")
    assert main.writable_places((str(tmp_path), missing)) == [str(tmp_path)]
    assert list(tmp_path.iterdir()) == []


def test_open_descriptors_are_counted_one_by_one() -> None:
    import socket

    before, sockets_before = main.open_descriptors()
    with socket.socket() as sock:
        held, sockets = main.open_descriptors()
        assert sock.fileno() in held
        assert sockets == sockets_before + 1
    assert sock.fileno() not in main.open_descriptors()[0] or sock.fileno() in before


# --- on Linux, in a process that is not locked down ---------------------------------------------

linux = pytest.mark.skipif(sys.platform != "linux", reason="reads /proc and calls Linux by number")


@linux
def test_the_report_of_a_process_that_is_not_locked_down_shows_it() -> None:
    """The control of the healthcheck. The report is built here in the test process, which no
    filter and no limit holds: the denied calls go through, the limits are not those of the
    plan, the parent can be read, and the judgement of the service says so. A healthcheck that
    passed on this would prove nothing."""
    libc = main.load_libc()
    machine = os.uname().machine
    before, _ = main.open_descriptors()
    report = main.sandbox_report("ping", libc, machine, main.received_environment())
    assert main.open_descriptors()[0] == before, "a probe left a descriptor open"
    assert report["denied"]["kill"] == "ALLOWED"
    assert report["denied"]["pidfd_open"] in ("ALLOWED", "EPERM")  # Docker's own profile
    assert report["denied"]["socket"] != "EPERM"
    assert report["denied"]["execve"] == "EFAULT"
    assert set(report["denied"]) == set(main.DENIED_NAMES)
    assert report["limits"] != main.LIMITS["ping"]
    assert report["dumpable"] == 1
    assert report["environ"] > 0
    # The parent of the test process is readable where it has one of its own uid (a shell, the
    # test runner); started by `docker exec` it has none inside the container.
    assert report["parent_hidden"] is not Path(f"/proc/{os.getppid()}/environ").exists()
    assert report["cryptography_blocked"] is False
    assert report["kernel"] == os.uname().release
    problems = server.sandbox_problems(report)
    for expected in ("allowed:kill", "allowed:socket", "allowed:execve", "limits", "dumpable"):
        assert expected in problems
    assert "environ" in problems
    assert ("parent_hidden" in problems) is not report["parent_hidden"]


@linux
def test_a_ping_in_a_process_that_is_not_locked_down_counts_and_hashes_its_bytes() -> None:
    import hashlib

    libc = main.load_libc()
    answer = main.ping("ping", b"abc", libc, os.uname().machine, 0)
    assert answer["ok"] is True
    assert answer["kind"] == "ping"
    assert answer["received"] == 3
    assert answer["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert answer["usage"]["max_rss_kb"] > 1000
    assert json.loads(main.encode(answer))["sandbox"]["environ"] == 0


@linux
def test_the_fields_of_proc_are_read() -> None:
    assert main.status_field("Seccomp") in ("0", "2")
    assert main.status_field("NoSuchField") == ""
    assert int(main.status_field("CapEff"), 16) >= 0
    assert main.received_environment() > 0
