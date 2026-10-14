"""The parser service for real: the worker's image, started by Compose from the service that
compose.yml defines, on Linux, with a handler that behaves like a parser a hostile file took
over (handlers/hostile.py) mounted where a parser will be.

Each limit and each line of the deny list is tried from inside a child, and fails the way the
plan's table says; the server survives all of it; the service cannot resolve or reach db, redis,
the api or the internet, and the worker can reach it; the protocol holds against a clumsy
client; and the worker's own client, from where the worker stands, turns every ending into its
error.

Marked `docker`: it needs a Docker daemon (CI's api job runs these, serially). The image is
built by the fixture from api/Dockerfile (target `worker`) under a tag of its own, unless
PARSER_TEST_IMAGE names one. About a minute: the two limits that take one (60 s of wall clock,
45 s of CPU) run side by side on instances of their own while the rest runs.
"""

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion import parser_client
from tests.ingestion.parser.helpers import API, HERE, ROOT, sandbox_module
from tests.pg import docker_available

pytestmark = [
    pytest.mark.docker,
    # The api folder alone is mounted in the dev container: no compose.yml, no Docker there.
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]

main = sandbox_module("main")
server = sandbox_module("server")
MIB = 1024 * 1024
OK = {"ok": True, "failure": None, "code": "ok"}
REFUSED = {"ok": False, "failure": "server", "length": 0}
# The wall clock of a child, and what killing and reaping it may add on a loaded machine: the
# duration the service reports ends once the child is reaped.
WALL_MS = int(server.WALL_S * 1000)
REAP_MS = 10_000


class Stack:
    """The running test stack: compose commands, and the driver in the client container."""

    def __init__(self, project: str, image: str) -> None:
        self.project, self.image = project, image
        self.containers: dict[str, str] = {}
        self.compose = ["docker", "compose", "-p", project, "-f", str(HERE / "compose.sandbox.yml")]
        # Compose interpolates the whole of compose.yml to read its `parser` service.
        self.env = {
            **os.environ,
            "PARSER_TEST_IMAGE": image,
            "POSTGRES_USER": "unused",
            "POSTGRES_PASSWORD": "unused",
            "POSTGRES_DB": "unused",
        }

    def run(self, *arguments: str, check: bool = True, timeout: float = 300) -> str:
        done = subprocess.run(
            [*self.compose, *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if check and done.returncode != 0:
            raise AssertionError(f"compose {arguments[:4]} failed:\n{done.stdout}\n{done.stderr}")
        return done.stdout

    def execute(
        self, service: str, *command: str, stdin: str | None = None, timeout: float = 300
    ) -> subprocess.CompletedProcess:
        """A command in a running container. By `docker exec`, not through Compose: a hundred
        of these, and Compose reads and interpolates its files for each one."""
        return subprocess.run(
            ["docker", "exec", "-i", self.container(service), *command],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def driver(self, *arguments: str, timeout: float = 300) -> Any:
        done = self.execute("client", "python", "/driver/driver.py", *arguments, timeout=timeout)
        assert done.returncode == 0, f"driver {arguments[:3]} failed:\n{done.stderr[-2000:]}"
        return without_usage(json.loads(done.stdout))

    def raw(self, options: dict, size: int = 1, kind: str = "pdf_render", host: str = "parser"):
        return self.driver("raw", host, kind, json.dumps(options), str(size))

    def parse(self, options: dict, size: int = 1, kind: str = "pdf_render", host: str = "parser"):
        return self.driver("parse", host, kind, json.dumps(options), str(size))

    def container(self, service: str) -> str:
        if service not in self.containers:
            self.containers[service] = self.run("ps", "-q", service).strip()
        return self.containers[service]

    def inspect(self, service: str) -> dict:
        out = subprocess.run(
            ["docker", "inspect", self.container(service)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout)[0]

    def inside(self, service: str, code: str) -> subprocess.CompletedProcess:
        return self.execute(service, "python", "-I", "-c", code)


def without_usage(found: Any) -> Any:
    """What the driver gave, without the cost that a child reports since G2b in every answer
    that read its document (`usage`: CPU, resident memory, address space). Its values are
    checked here once for all, and it is taken out: the tests below are about the rest."""
    if isinstance(found, list):
        for item in found:
            without_usage(item)
    elif isinstance(found, dict):
        body = found.get("body")
        used = body.get("usage") if isinstance(body, dict) else None
        # A ping has a report of its own, with two figures, which its tests read: it stays.
        if isinstance(used, dict) and set(used) == {"cpu_ms", "max_rss_kb", "max_vm_kb"}:
            assert all(type(value) is int and value >= 0 for value in used.values()), used
            del body["usage"]
        for value in found.values():
            without_usage(value)
    return found


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("PARSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-worker:parser-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-parser-test-{os.getpid()}", image)
    try:
        running.run(
            "up", "-d", "--wait", "--wait-timeout", "120",
            "parser", "parser-cpu", "parser-kill", "parser-wall", "client", "peers",
        )  # fmt: skip
        running.run("up", "-d", "parser-unfiltered")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)


@pytest.fixture(scope="module")
def slow(stack: Stack) -> Iterator[dict]:
    """Everything that only time ends, started together on instances of their own as soon as
    the stack is up: the three jobs that take a minute, and the two clients the service gives
    up after 10 s of silence (they hold no job slot, so they share an instance with a job; the
    service is then shown alive by a ping, which a running job does not delay). A test that
    needs one waits for it."""
    found: dict[str, Any] = {}

    def job(name: str, host: str, options: dict) -> None:
        found[name] = stack.raw(options, host=host)

    def clumsy(name: str, host: str) -> None:
        found[name] = stack.driver("clumsy", host, name, "ping")

    threads = [
        threading.Thread(target=job, args=("spin", "parser-cpu", {"act": "spin"})),
        threading.Thread(
            target=job, args=("deaf", "parser-kill", {"act": "spin", "ignore_sigxcpu": True})
        ),
        threading.Thread(target=job, args=("sleep", "parser-wall", {"act": "sleep", "s": 600})),
        threading.Thread(target=clumsy, args=("silent", "parser-cpu")),
        threading.Thread(target=clumsy, args=("half_a_document", "parser-kill")),
    ]
    for thread in threads:
        thread.start()

    def wait(*names: str) -> dict:
        deadline = time.monotonic() + 180
        while not set(names) <= set(found) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert set(names) <= set(found), f"did not come back: {sorted(set(names) - set(found))}"
        return found

    yield {"wait": wait}
    for thread in threads:
        thread.join(180)


@pytest.fixture(scope="module", autouse=True)
def start_the_slow_jobs_first(slow: dict) -> None:
    """Module wide and automatic: the minute starts with the first test of the file."""


@pytest.fixture(scope="module")
def attempts(stack: Stack) -> dict[str, str]:
    """Everything the hostile handler tries in one child, and how each try ended."""
    answer = stack.raw({"act": "attempts"})
    assert answer["header"] == {**OK, "length": answer["body_bytes"]}
    return answer["body"]["attempts"]


@pytest.fixture(scope="module")
def state(stack: Stack) -> dict[str, Any]:
    return stack.raw({"act": "state"}, size=5)["body"]["state"]


# compose.yml: the probe of the parser (`server.py --check`, and the ping child it asks the
# service for) is given 8 s, every 15 s.
PROBE_TIMEOUT_S = 8


def left_over(stack: Stack, seen: dict, host: str = "parser") -> set[int]:
    """The processes a child saw besides the server and itself that are still there at a later
    look. The health probe of the container is a process of the same namespace for the moment
    it lasts, and so is the ping child it asks for: on a loaded machine a look falls on one
    now and then (issue #104: `[1, 62, 63]`, where 63 is younger than the child that looked,
    so not what the job before it left). What a job leaves behind stays; a probe is over
    within its timeout."""
    others = set(seen["processes"]) - {1, seen["pid"]}
    if others:
        time.sleep(PROBE_TIMEOUT_S + 1)
        later = stack.raw({"act": "state"}, host=host)["body"]["state"]
        others &= set(later["processes"])
    return others


def alive(stack: Stack, service: str = "parser") -> None:
    """The service is the same process as at the start, healthy, and serves a job."""
    found = stack.inspect(service)["State"]
    assert found["Running"] is True
    assert found["Health"]["Status"] == "healthy"
    assert stack.inspect(service)["RestartCount"] == 0
    after = stack.raw({"act": "echo"}, size=3, host=service)
    assert after["body"] == {"ok": True, "received": 3}


# --- the container, as it runs ------------------------------------------------------------------

# What the image itself sets. None is a secret: GPG_KEY is the fingerprint of the key that
# signs Python's releases, set by the official python image.
IMAGE_ENVIRONMENT = {
    "PATH", "HOME", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK",
}  # fmt: skip


def test_the_container_runs_as_compose_yml_hardens_it(stack: Stack) -> None:
    found = stack.inspect("parser")
    host, config = found["HostConfig"], found["Config"]
    assert config["User"] == "10002:10002"
    assert config["Cmd"] == ["python", "-I", "/app/app/ingestion/sandbox/server.py"]
    assert host["CapDrop"] == ["ALL"]
    assert not host.get("CapAdd")
    assert host["Privileged"] is False
    assert host["ReadonlyRootfs"] is True
    assert host["SecurityOpt"] == ["no-new-privileges:true"]
    assert host["PidsLimit"] == 32
    assert host["Memory"] == 640 * MIB
    assert host["IpcMode"] == "none"
    assert host["PidMode"] == "", "its own PID namespace"
    assert host["NetworkMode"] == f"{stack.project}_parsing"
    assert list(found["NetworkSettings"]["Networks"]) == [f"{stack.project}_parsing"]
    assert not found["NetworkSettings"]["Ports"].get("7070/tcp"), "no published port"
    assert not host.get("PortBindings")
    assert not host.get("Tmpfs")
    assert not host.get("Devices")
    # Nothing mounted but the tests' handler, twice, and the null device over the file of
    # `image` (compose.sandbox.yml): the service of compose.yml has no volume at all.
    assert {mount["Destination"] for mount in found["Mounts"]} == {
        "/app/app/ingestion/sandbox/pdf_render.py",
        "/app/app/ingestion/sandbox/docx.py",
        "/app/app/ingestion/sandbox/image.py",
    }
    assert all(mount["RW"] is False for mount in found["Mounts"])


def test_the_judge_of_the_smoke_scripts_agrees_on_the_real_container(stack: Stack) -> None:
    """tests/parser_check.py is what tests/compose_smoke.sh and tests/jetson_smoke.sh judge the
    running container with. On this stack it must find the two handler files the tests mount,
    and nothing else."""
    inspected = subprocess.run(
        ["docker", "inspect", stack.container("parser")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    done = subprocess.run(
        [
            "python3",
            str(ROOT / "tests" / "parser_check.py"),
            "inspect",
            "--secret-name",
            "SECRET_KEY",
        ],
        input=inspected,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert sorted(done.stderr.splitlines()) == [
        "parser: mount at /app/app/ingestion/sandbox/docx.py (writable: False)",
        "parser: mount at /app/app/ingestion/sandbox/image.py (writable: False)",
        "parser: mount at /app/app/ingestion/sandbox/pdf_render.py (writable: False)",
    ]


def test_the_environment_holds_the_port_and_what_the_image_sets_and_no_secret(
    stack: Stack,
) -> None:
    names = {entry.partition("=")[0] for entry in stack.inspect("parser")["Config"]["Env"]}
    assert names == IMAGE_ENVIRONMENT | {"PARSER_PORT"}
    for name in names - {"GPG_KEY"}:
        for word in ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "DSN", "DATABASE", "REDIS"):
            assert word not in name.upper(), name
    assert not any("armcap" in name.lower() for name in names), "it never imports cryptography"


def test_the_network_it_sits_on_is_internal(stack: Stack) -> None:
    out = subprocess.run(
        ["docker", "network", "inspect", f"{stack.project}_parsing"],
        capture_output=True,
        text=True,
        check=True,
    )
    network = json.loads(out.stdout)[0]
    assert network["Internal"] is True
    members = {entry["Name"] for entry in network["Containers"].values()}
    project = stack.project
    assert members == {
        f"{project}-{service}-1"
        for service in (
            "parser",
            "parser-cpu",
            "parser-kill",
            "parser-wall",
            "parser-unfiltered",
            "client",
        )
    }, "the parsers and the worker's stand in, and nothing else"


def test_the_healthcheck_passes_and_proves_the_sandbox(stack: Stack) -> None:
    check = "/app/app/ingestion/sandbox/server.py"
    done = stack.execute("parser", "python", "-I", check, "--check")
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    # The same ping through the worker's client, with what the child reported.
    ping = stack.driver("ping", "parser")
    assert ping["in_force"] is True
    assert ping["problems"] == []
    sandbox = ping["sandbox"]
    assert sandbox["seccomp"] == 2
    assert sandbox["filters"] >= 1, "one filter where Docker has none of its own, two elsewhere"
    assert sandbox["denied"] == dict.fromkeys(main.DENIED_NAMES, "EPERM")
    assert sandbox["limits"] == main.LIMITS["ping"]
    assert (sandbox["dumpable"], sandbox["no_new_privs"], sandbox["capabilities"]) == (0, 1, 0)
    assert (sandbox["uid"], sandbox["environ"], sandbox["sockets"]) == (10002, 0, 0)
    assert sandbox["descriptors"] == [0, 1, 2]
    assert sandbox["writable"] == []
    assert sandbox["parent_hidden"] is True
    assert sandbox["isolated"] is True
    assert sandbox["cryptography_blocked"] is True
    assert server.sandbox_problems(sandbox) == []


def test_the_service_imports_nothing_of_the_application_and_no_cryptography(
    stack: Stack,
) -> None:
    """Both files, loaded in the container as the service loads them: what is in sys.modules
    afterwards. On an Apple silicon Mac importing `cryptography` without OPENSSL_armcap ends
    in SIGILL, and this container has no such variable: it is healthy because it never does."""
    code = (
        "import runpy, sys\n"
        "for name in ('server', 'main'):\n"
        "    runpy.run_path(f'/app/app/ingestion/sandbox/{name}.py', run_name='loaded')\n"
        "tops = {name.split('.')[0] for name in sys.modules}\n"
        "print(sorted(tops - sys.stdlib_module_names - {'loaded'}))\n"
        "print(sorted(tops & {'app', 'cryptography', 'pydantic', 'sqlalchemy', 'arq'}))\n"
    )
    done = stack.inside("parser", code)
    assert done.returncode == 0, done.stderr
    outside_stdlib, forbidden = done.stdout.splitlines()
    assert forbidden == "[]"
    assert all(name.startswith("_") for name in json.loads(outside_stdlib.replace("'", '"')))


def test_site_packages_are_importable_isolated_from_the_venvs_interpreter(stack: Stack) -> None:
    """Assumption A2 of the plan: `python -I` started from the venv finds what G2b, G7 and G8
    will import (pypdf, defusedxml, pypdfium2, Pillow). Pillow is there today."""
    done = stack.inside(
        "parser", "import sys, PIL; print(sys.flags.isolated, sys.prefix, PIL.__version__)"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[:2] == ["1", "/app/.venv"]


# --- what a child that was taken over cannot do -------------------------------------------------

EXPECTED_ATTEMPTS = {
    # a connection: the filter answers EPERM whatever the family
    "socket_inet": "EPERM",
    "socket_inet6": "EPERM",
    "socket_unix": "EPERM",
    "socket_netlink": "EPERM",
    "socketpair": "EPERM",
    "create_connection": "EPERM",
    "resolve": "gaierror",
    "bind": "EPERM",
    "connect": "EPERM",
    "inherited_socket": "none",
    # a process, a thread, a program: RLIMIT_NPROC is 0 and execve is denied
    "fork": "EAGAIN",
    "thread": "RuntimeError",
    "subprocess": "EAGAIN",
    "posix_spawn": "EAGAIN",
    "execv": "EPERM",
    "execveat": "EPERM",
    # the server, its parent: by every call that sends a signal
    "kill_parent_0": "EPERM",
    "kill_parent_term": "EPERM",
    "kill_parent_kill": "EPERM",
    "kill_everyone": "EPERM",
    "killpg": "EPERM",
    "kill_self": "EPERM",
    "raise_signal": "EPERM",
    "pthread_kill": "EPERM",
    "tkill": "EPERM",
    "tgkill": "EPERM",
    "rt_sigqueueinfo": "EPERM",
    "rt_tgsigqueueinfo": "EPERM",
    "pidfd_open": "EPERM",
    "pidfd_send_signal": "EPERM",
    # the server, traced or read: denied calls, and a non dumpable server
    "ptrace_attach": "EPERM",
    "process_vm_readv": "EPERM",
    "process_vm_writev": "EPERM",
    "parent_environ": "EACCES",
    "parent_mem": "EACCES",
    "parent_maps": "EACCES",
    "parent_fd": "EACCES",
    "parent_cwd": "EACCES",
    "parent_exe": "EACCES",
    "pid1_environ": "EACCES",
    "io_uring_setup": "EPERM",
    # something left for the next job
    "shmget": "EPERM",
    "msgget": "EPERM",
    "semget": "EPERM",
    "mq_open": "EPERM",
    "create_dev_mqueue": "EMFILE",
    "write_dev_shm": "ENOENT",
    "create_dev_shm": "ENOENT",
    # a file: a read only root, and no growth where a file could exist
    "write_tmp": "EROFS",
    "write_var_tmp": "EROFS",
    "write_app": "EROFS",
    "write_sandbox": "EROFS",
    "write_root": "EROFS",
    "write_cwd": "EROFS",
    "write_home": "ENOENT",
    "mkdir": "EROFS",
    "memfd_grow": "EFBIG",
    # the limits cannot be raised again
    "raise_nproc": "ValueError",
    "raise_nofile": "ValueError",
    "raise_fsize": "ValueError",
    "raise_as": "ValueError",
    "raise_core": "ValueError",
    "raise_cpu": "ValueError",
    # privileges and namespaces
    "setuid_0": "EPERM",
    "setgid_0": "EPERM",
    "chroot": "EPERM",
    "unshare_user": "EPERM",
    "unshare_net": "EPERM",
    "mount_proc": "EPERM",
    # nothing of the application, and no cryptography
    "import_app": "ModuleNotFoundError",
    "import_app_config": "ModuleNotFoundError",
    "import_cryptography": "ModuleNotFoundError",
    "import_cryptography_fernet": "ModuleNotFoundError",
    "read_env_file": "ENOENT",
}


@pytest.mark.parametrize("attempt", sorted(EXPECTED_ATTEMPTS))
def test_an_attempt_to_get_out_fails_the_way_the_table_says(
    attempts: dict[str, str], attempt: str
) -> None:
    assert attempts[attempt] == EXPECTED_ATTEMPTS[attempt]


def test_every_attempt_was_judged_and_none_went_through(attempts: dict[str, str]) -> None:
    informative = {
        # The filter cannot be taken off: mode 0 is no mode. prctl answers -1.
        "filter_off": "DONE:-1",
        # What is installed in the image can be imported: the container is the boundary.
        "import_psycopg": "DONE:psycopg",
    }
    assert set(attempts) == set(EXPECTED_ATTEMPTS) | set(informative)
    for name, expected in informative.items():
        assert attempts[name] == expected
    went_through = {
        name: how
        for name, how in attempts.items()
        if how.startswith("DONE") and name not in informative
    }
    assert went_through == {}


def test_every_call_of_the_deny_list_was_tried_by_a_real_caller(attempts: dict[str, str]) -> None:
    """The ping probes each denied number with arguments that do nothing. These are the same
    calls made the way an attacker would make them, each through Python or libc."""
    tried_as = {
        "socket": "socket_inet", "socketpair": "socketpair", "bind": "bind",
        "connect": "create_connection", "ptrace": "ptrace_attach",
        "process_vm_readv": "process_vm_readv", "process_vm_writev": "process_vm_writev",
        "kill": "kill_parent_kill", "tkill": "tkill", "tgkill": "pthread_kill",
        "rt_sigqueueinfo": "rt_sigqueueinfo", "rt_tgsigqueueinfo": "rt_tgsigqueueinfo",
        "pidfd_open": "pidfd_open", "pidfd_send_signal": "pidfd_send_signal",
        "execve": "execv", "execveat": "execveat", "io_uring_setup": "io_uring_setup",
        "shmget": "shmget", "msgget": "msgget", "semget": "semget", "mq_open": "mq_open",
        "unshare": "unshare_user",
    }  # fmt: skip
    # io_uring_enter and io_uring_register need a ring, and setns a namespace descriptor, which
    # nothing here can obtain: the ping's probe by number is their test.
    assert set(tried_as) == set(main.DENIED_NAMES) - {
        "io_uring_enter",
        "io_uring_register",
        "setns",
    }
    for call, attempt in tried_as.items():
        assert attempts[attempt] == "EPERM", call


def test_the_child_is_isolated_with_an_empty_environment_and_three_pipes(
    state: dict[str, Any],
) -> None:
    assert (state["uid"], state["gid"], state["groups"]) == (10002, 10002, [10002])
    # What Python itself adds when it finds no locale: the child was started with nothing.
    assert state["environ"] == {"LC_CTYPE": "C.UTF-8"}
    assert state["isolated"] == 1
    assert state["dont_write_bytecode"] == 1
    assert state["cwd"] == "/"
    # Its own directory first, so that a handler can import a helper beside it; nothing of the
    # application, no current directory, no user site.
    assert state["path"][0] == "/app/app/ingestion/sandbox"
    assert "/app" not in state["path"]
    assert "" not in state["path"]
    assert state["path"][-1] == "/app/.venv/lib/python3.12/site-packages"
    assert state["modules_of_the_app"] == []
    assert state["cryptography_in_modules"] is True, "blocked before any handler was imported"
    assert state["descriptors"] == {"0": "pipe", "1": "pipe", "2": "device"}
    assert state["argv_kind"] == "pdf_render"


def test_the_child_sees_the_server_and_itself_and_no_other_process(
    stack: Stack, state: dict[str, Any]
) -> None:
    assert state["parent"] == 1
    assert {1, state["pid"]} <= set(state["processes"])
    assert left_over(stack, state) == set()


def test_the_limits_of_the_table_hold_in_the_child(state: dict[str, Any]) -> None:
    # The child of `state` is of the kind pdf_render: its address space is the 256 MiB that G8
    # measured for the renderer (main.PDF_RENDER_AS), no longer the 512 MiB every kind began with.
    assert main.LIMITS["pdf_render"]["as"] == main.PDF_RENDER_AS == 256 * MIB
    assert state["limits"] == {
        "as": [256 * MIB, 256 * MIB],
        "cpu": [45, 46],
        "fsize": [0, 0],
        "nofile": [16, 16],
        "nproc": [0, 0],
        "core": [0, 0],
        "msgqueue": [0, 0],
    }
    assert state["dumpable"] == 0
    # The handler opened descriptors until it was refused: the 17th failed, and it had imported
    # what it needs and written its answer under 16.
    assert state["open_limit_reached_at"] == 16


# --- the limits that end a job ------------------------------------------------------------------


def test_allocating_past_the_address_space_fails_the_job_and_not_the_service(stack: Stack) -> None:
    for options in ({"act": "allocate", "mib": 600}, {"act": "allocate_and_touch", "mib": 2000}):
        answer = stack.raw(options)
        assert answer["body"] == {"ok": False, "failure": "server", "code": "memory_limit"}
    found = stack.parse({"act": "allocate", "mib": 4096})
    assert (found["error"], found["failure"], found["code"]) == (
        "ParserRefusedError",
        "server",
        "memory_limit",
    )
    # Under the limit it is an ordinary job.
    assert stack.raw({"act": "allocate", "mib": 200})["body"] == {"ok": True, "size": 200 * MIB}
    alive(stack)


def test_a_parser_that_swallows_the_memory_error_goes_on(stack: Stack) -> None:
    """Why the address space limit alone does not end a job (pypdf may catch MemoryError): the
    child is still there afterwards. The wall clock is what ends it."""
    answer = stack.raw({"act": "swallow_memory_error", "mib": 600})
    assert answer["body"] == {"ok": True, "text": "went on after MemoryError"}


def test_a_child_that_floods_its_output_is_killed_at_the_cap(stack: Stack) -> None:
    """The flood has no end: nothing but a kill ends this child, and the service waits for its
    death before it reads on, so with the kill taken away the job never answers (tried on PR
    #156 with an image whose server does not kill: this test fails on its bound of time). The
    service's own record then says how the child ended and how much of its output was read:
    SIGKILL, and the cap plus what a dead child leaves in its pipe, not what it wrote."""
    answer = stack.raw({"act": "flood"})
    assert answer["header"] == {**REFUSED, "code": "output_cap"}
    # A kill and its reaping: REAP_MS, the rule of every other kill of this file. The time is
    # printed for whoever has to judge the bound: pytest shows it with -rP, or when this fails.
    print(f"flood_ms={answer['ms']}")
    assert answer["ms"] < REAP_MS
    found = stack.parse({"act": "flood"})
    assert (found["error"], found["code"], found["failure"]) == (
        "ParserRefusedError",
        "output_cap",
        "server",
    )
    log = stack.run("logs", "--no-log-prefix", "--no-color", "parser")
    lines = [json.loads(line) for line in log.splitlines() if line.strip()]
    capped = [line for line in lines if line["event"] == "job" and line["code"] == "output_cap"]
    assert len(capped) >= 2  # the two jobs above are the last two
    for line in capped[-2:]:
        assert line["status"] == -signal.SIGKILL
        assert line["read"] <= server.MAX_ANSWER + (1 << 16) + server.DRAIN_MAX
    alive(stack)


def test_an_answer_of_3_mib_arrives_although_no_file_may_grow(stack: Stack) -> None:
    """RLIMIT_FSIZE is 0: pipes are not files."""
    answer = stack.raw({"act": "text", "chars": 3 * MIB})
    # The text, its envelope, and the few bytes of what the child says it cost.
    least = 3 * MIB + len('{"ok":true,"text":""}')
    assert least < answer["header"].pop("length") <= least + 100
    assert answer["header"] == OK
    found = stack.parse({"act": "text", "chars": 500_000, "char": "\U0001f600"})
    assert found["parsed"]["text_chars"] == 500_000, "2 MB of UTF-8, not 6 MB of escapes"
    # One character more than a document may hold is the worker's to refuse.
    found = stack.parse({"act": "text", "chars": 500_001})
    assert (found["error"], found["code"]) == ("ParserMalformedError", "text_too_long")


@pytest.mark.parametrize(
    ("options", "header"),
    [
        ({"act": "segfault"}, {**REFUSED, "code": "crashed"}),
        ({"act": "abort"}, {**REFUSED, "code": "crashed"}),
        ({"act": "exit", "status": 3}, {**REFUSED, "code": "crashed"}),
        ({"act": "exit", "status": 70}, {**REFUSED, "code": "sandbox"}),
        ({"act": "os_exit", "status": 0}, {**OK, "length": 0}),
    ],
)
def test_a_child_that_dies_is_a_failure_of_the_server_and_leaves_no_core(
    stack: Stack, options: dict, header: dict
) -> None:
    """With the kind docx, which stands here for every kind that is not in
    parser_client.FILE_DEATH_KINDS (pdf_text, docx, linkedin_zip): the death of its child is
    the server's failure, the worker tries the document again."""
    assert "docx" not in parser_client.FILE_DEATH_KINDS
    assert stack.raw(options, kind="docx")["header"] == header
    found = stack.parse(options, kind="docx")
    assert found["failure"] == "server"
    assert found["error"] in ("ParserRefusedError", "ParserMalformedError")
    no_core_and_alive(stack)


@pytest.mark.parametrize(
    ("options", "header", "outcome"),
    [
        ({"act": "segfault"}, {**REFUSED, "code": "crashed"}, ("file", "bomb")),
        ({"act": "abort"}, {**REFUSED, "code": "crashed"}, ("file", "bomb")),
        ({"act": "exit", "status": 3}, {**REFUSED, "code": "crashed"}, ("file", "bomb")),
        ({"act": "exit", "status": 70}, {**REFUSED, "code": "sandbox"}, ("server", None)),
        ({"act": "os_exit", "status": 0}, {**OK, "length": 0}, ("server", None)),
    ],
)
def test_a_child_of_the_two_kinds_of_g8_that_dies_is_the_files_failure_and_leaves_no_core(
    stack: Stack, options: dict, header: dict, outcome: tuple[str, str | None]
) -> None:
    """The kinds image and pdf_render hand the file to a C library (G8, FILE_DEATH_KINDS): the
    server answers what it answers for every kind, and the client reads the death of that child
    (`crashed`) as the file's `bomb`, so the document is failed at once and never read twice.
    What is not a death of the child stays the server's: a sandbox that could not be set
    (exit 70) and a child that left without an answer."""
    assert set(parser_client.FILE_DEATH_KINDS) == {"image", "pdf_render"}
    assert stack.raw(options)["header"] == header, "the server's own answer did not change"
    found = stack.parse(options)
    failure, code = outcome
    assert found["failure"] == failure
    if code is None:
        assert found["error"] in ("ParserRefusedError", "ParserMalformedError")
    else:
        assert (found["error"], found["code"]) == ("ParserRefusedError", code)
    no_core_and_alive(stack)


def no_core_and_alive(stack: Stack) -> None:
    listing = stack.inside(
        "parser",
        "import os; print(sorted(n for d in ('/', '/tmp', '/app') for n in os.listdir(d)"
        " if 'core' in n.lower()))",
    )
    assert listing.stdout.strip() == "[]"
    alive(stack)


def test_what_a_handler_raises_is_a_code_and_never_its_words(stack: Stack) -> None:
    answer = stack.raw({"act": "raise", "text": "PRIVATE-exception-canary"})
    assert answer["body"] == {"ok": False, "failure": "file", "code": "unreadable"}
    assert stack.raw({"act": "recurse"})["body"] == {
        "ok": False,
        "failure": "file",
        "code": "unreadable",
    }
    found = stack.parse({"act": "raise", "text": "PRIVATE-exception-canary"})
    assert (found["error"], found["failure"], found["code"]) == (
        "ParserRefusedError",
        "file",
        "unreadable",
    )


def test_a_child_cannot_leave_anything_for_the_job_after_it(stack: Stack) -> None:
    left = stack.raw({"act": "leave_ipc"})["body"]["attempts"]
    assert left == {
        "shmget": "EPERM",
        "msgget": "EPERM",
        "semget": "EPERM",
        "create_dev_mqueue": "EMFILE",
        "create_dev_shm": "ENOENT",
    }
    assert stack.driver("ping", "parser")["sandbox"]["writable"] == []


def test_a_child_can_make_itself_dumpable_again_which_exposes_only_itself(stack: Stack) -> None:
    """Stated, not prevented: PR_SET_DUMPABLE is the process's own to set. It does not reopen
    the server (still closed to the next child) and RLIMIT_CORE cannot be raised."""
    assert stack.raw({"act": "undumpable_off"})["body"] == {"ok": True, "dumpable": 1}
    attempts_after = stack.raw({"act": "attempts"})["body"]["attempts"]
    assert attempts_after["parent_environ"] == "EACCES"
    assert attempts_after["raise_core"] == "ValueError"


# --- the protocol, against the real service -----------------------------------------------------


def served_after(found: dict) -> None:
    assert found["after"]["body"] == {"ok": True, "received": 3}
    assert found["after_ms"] < 10_000


def test_a_header_line_of_5_kb_is_refused(stack: Stack) -> None:
    found = stack.driver("clumsy", "parser", "header_5k")
    assert found["first"]["header"] == {**REFUSED, "code": "header_too_long"}
    served_after(found)


def test_a_size_of_20_mib_and_one_byte_is_refused_before_any_byte_is_read(stack: Stack) -> None:
    found = stack.driver("clumsy", "parser", "size_over")
    assert found["first"]["header"] == {**REFUSED, "code": "too_large"}
    assert found["first"]["ms"] < 3000, "answered on the header alone: no document was awaited"
    served_after(found)
    found = stack.driver("clumsy", "parser", "size_over_sending")
    assert found["first"]["header"] == {**REFUSED, "code": "too_large"}
    served_after(found)
    assert stack.raw({"act": "echo"}, size=20 * MIB)["body"] == {"ok": True, "received": 20 * MIB}


def test_a_client_that_sends_nothing_is_given_up_after_10_s(slow: dict) -> None:
    found = slow["wait"]("silent")["silent"]
    assert found["first"]["header"] == {**REFUSED, "code": "read_timeout"}
    assert 9_500 <= found["first"]["ms"] <= 15_000
    assert found["after"]["header"]["ok"] is True, "the service answers a ping right after"


def test_a_document_that_stops_arriving_is_given_up_after_10_s(slow: dict) -> None:
    found = slow["wait"]("half_a_document")["half_a_document"]
    assert found["first"]["header"] == {**REFUSED, "code": "read_timeout"}
    assert 9_500 <= found["first"]["ms"] <= 15_000
    assert found["after"]["header"]["ok"] is True


@pytest.mark.parametrize(
    ("what", "code"),
    [("unknown_kind", "unknown_kind"), ("not_json", "bad_request")],
)
def test_a_request_that_is_not_one_is_refused(stack: Stack, what: str, code: str) -> None:
    found = stack.driver("clumsy", "parser", what)
    assert found["first"]["header"] == {**REFUSED, "code": code}
    served_after(found)


def test_a_client_that_closes_mid_document_or_mid_job_leaves_the_service_free(
    stack: Stack,
) -> None:
    served_after(stack.driver("clumsy", "parser", "closes_mid_document"))
    # It left a job of 50 s: the child is killed, and the next job does not wait for it.
    found = stack.driver("clumsy", "parser", "leaves_during_job")
    served_after(found)
    after = stack.raw({"act": "state"})["body"]["state"]
    assert left_over(stack, after) == set()


def test_a_ping_is_answered_at_once_during_a_job(stack: Stack) -> None:
    found = stack.driver("during", "parser", "6")
    assert len(found["pings"]) == 5
    for ping in found["pings"]:
        assert ping["ok"] is True
        assert ping["ms"] < 2000
    assert found["job_still_running"] is True, "the pings came back while the job ran"
    assert found["job"]["body"] == {"ok": True, "received": 1}
    assert found["job"]["ms"] >= 6000


def test_jobs_sent_at_once_run_one_at_a_time(stack: Stack) -> None:
    found = stack.driver("together", "parser", "2", "3")
    times = [job["at"] for job in found["jobs"]]
    assert [job["ok"] for job in found["jobs"]] == ["ok", "ok", "ok"]
    assert 1.9 <= times[0] < 4
    assert times[1] - times[0] >= 1.8, "the second ran after the first, not beside it"
    assert times[2] - times[1] >= 1.8


# --- the worker's client, from where the worker stands ------------------------------------------


def test_the_kinds_without_a_handler_answer_not_supported_yet(stack: Stack) -> None:
    """Since G8 the image holds a file for each of the five kinds of the plan, so none is left
    without a handler: compose.sandbox.yml takes the file of `image` away in this stack (the
    null device mounted over it, which is no regular file), and that kind answers through the
    whole chain what a kind without its file answers. pdf_render and docx are the tests' own
    here; test_pdf_container.py, test_archive_container.py and test_image_container.py are
    about the real handlers."""
    sandbox = "/app/app/ingestion/sandbox"
    files = (
        "import os; print(sorted(n for n in os.listdir('" + sandbox + "')"
        " if os.path.isfile('" + sandbox + "/' + n)))"
    )
    handlers = {name + ".py" for name in main.HANDLERS.values() if name}
    assert handlers == {
        "pdf_text.py",
        "docx.py",
        "linkedin_zip.py",
        "image.py",
        "pdf_render.py",
        "mail_html.py",  # mail M3: the HTML of a mail, tests/mail/test_parser_container.py
    }
    # The image as it is built (the client runs it with nothing mounted there): all five.
    shipped = set(json.loads(stack.inside("client", files).stdout.replace("'", '"')))
    assert handlers <= shipped
    # The parser of this stack: the same without image.py.
    here = set(json.loads(stack.inside("parser", files).stdout.replace("'", '"')))
    assert handlers - here == {"image.py"}
    for kind in ("image",):
        answer = stack.raw({}, size=1024, kind=kind)
        assert answer["body"] == {"ok": False, "failure": "server", "code": "not_supported"}
        found = stack.parse({}, size=1024, kind=kind)
        assert (found["error"], found["failure"], found["code"]) == (
            "ParserRefusedError",
            "server",
            "not_supported",
        )


def test_a_result_and_a_refusal_of_the_file_come_through_the_whole_chain(stack: Stack) -> None:
    answer = {
        "ok": True,
        "text": "page one\f\fpage three",
        "pages": 12,
        "pages_text": 2,
        "pages_image": 1,
        "notes": [{"code": "pages_not_read", "from": 4, "to": 12}],
    }
    found = stack.parse({"act": "answer", "answer": answer}, size=MIB, kind="docx")
    assert found["parsed"] == {
        "text_chars": 20,
        "text_start": "page one\f\fpage three",
        "pages": 12,
        "notes": [["pages_not_read", {"from": 4, "to": 12}]],
        "images": 0,
    }
    refusal = {"ok": False, "failure": "file", "code": "protected"}
    found = stack.parse({"act": "answer", "answer": refusal})
    assert (found["error"], found["failure"], found["code"]) == (
        "ParserRefusedError",
        "file",
        "protected",
    )


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("not json", "not_json"),
        ("[" * 500, "not_json"),
        ('{"ok": true, "text": "a\\u0000b"}', "nul"),
        ('{"ok": true, "text": "a\\ud800b"}', "surrogate"),
        ('{"ok": true, "pages": -3, "pages_text": 0, "pages_image": 0}', "pages"),
        (
            '{"ok": true, "text": "a\\fb\\fc", "pages": 2, "pages_text": 2, "pages_image": 0}',
            "form_feeds",
        ),
        (
            '{"ok": true, "images": [{"position": 0, "width": 9, "height": 9,'
            ' "jpeg": "iVBORw0KGgo="}]}',
            "image_not_jpeg",
        ),
        ('{"ok": true, "notes": [{"code": "Pages 3 to 9 were not read."}]}', "note_code"),
        ('{"ok": false, "failure": "file", "code": "Call this number."}', "failure"),
        ('{"ok": true, "ok": false}', "duplicate_key"),
        ('{"ok": true, "html": "<script>"}', "keys"),
    ],
)
def test_what_a_taken_over_child_writes_is_refused_by_the_worker(
    stack: Stack, text: str, rule: str
) -> None:
    """The child writes these bytes itself, past main.py's encoder, and exits normally: the
    server passes them on unread, and the worker's client is the one door they must pass."""
    found = stack.parse({"act": "write_raw", "text": text})
    assert (found["error"], found["failure"], found["code"]) == (
        "ParserMalformedError",
        "server",
        rule,
    )


def test_20_mib_go_through_a_sandboxed_child_and_come_back_counted_and_hashed(
    stack: Stack,
) -> None:
    found = stack.driver("ping", "parser", str(20 * MIB))
    assert found["received"] == 20 * MIB
    assert found["in_force"] is True
    assert found["ms"] < 20_000


# --- the filter that cannot be installed --------------------------------------------------------


def test_without_the_filter_the_child_does_nothing_and_the_service_is_unhealthy(
    stack: Stack,
) -> None:
    """`parser-unfiltered` runs under a profile that refuses the installation of a seccomp
    filter, as a kernel without CONFIG_SECCOMP_FILTER would. The child exits before it imports
    a handler or reads a byte; the job is a failure of the server; the healthcheck fails, which
    is what stops a deploy on such a kernel."""
    check = "/app/app/ingestion/sandbox/server.py"
    done = stack.execute("parser-unfiltered", "python", "-I", check, "--check")
    assert (done.returncode, done.stderr) == (1, "unhealthy: refused:sandbox\n")
    for options in ({"act": "attempts"}, {"act": "echo"}):
        answer = stack.raw(options, size=4096, host="parser-unfiltered")
        assert answer["header"] == {**REFUSED, "code": "sandbox"}
    found = stack.parse({"act": "echo"}, host="parser-unfiltered")
    assert (found["error"], found["failure"], found["code"]) == (
        "ParserRefusedError",
        "server",
        "sandbox",
    )
    ping = stack.driver("ping", "parser-unfiltered")
    assert ping == {"error": "ParserRefusedError", "code": "sandbox"}
    health = stack.inspect("parser-unfiltered")["State"]["Health"]
    assert health["Status"] in ("starting", "unhealthy")
    assert all(entry["ExitCode"] == 1 for entry in health["Log"])


# --- the network --------------------------------------------------------------------------------


def reach(stack: Stack, service: str, host: str, port: int) -> dict:
    """Resolve and connect from inside a service (the parser has no driver mounted: the same
    code goes in on standard input)."""
    code = (HERE / "driver.py").read_text(encoding="utf-8")
    done = stack.execute(service, "python", "-I", "-", "reach", host, str(port), stdin=code)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


@pytest.mark.parametrize(
    ("host", "port"),
    [("db", 5432), ("redis", 6379), ("api", 8000), ("peers", 5432), ("caddy", 80)],
)
def test_the_parser_cannot_resolve_a_service_of_the_default_network(
    stack: Stack, host: str, port: int
) -> None:
    assert reach(stack, "parser", host, port) == {
        "resolved": None,
        "connected": False,
        "resolve_error": "gaierror",
    }


def test_the_parser_cannot_resolve_an_outside_name_or_reach_an_outside_address(
    stack: Stack,
) -> None:
    for name in ("example.org", "api.anthropic.com", "github.com"):
        found = reach(stack, "parser", name, 443)
        assert (found["resolved"], found["connected"]) == (None, False), name
    for address in ("1.1.1.1", "8.8.8.8", "93.184.215.14"):
        found = reach(stack, "parser", address, 443)
        assert found["connected"] is False, address
    # Nor the address of a container of the other network, known without any name.
    peers = stack.inspect("peers")["NetworkSettings"]["Networks"][f"{stack.project}_default"]
    found = reach(stack, "parser", peers["IPAddress"], 5432)
    assert found["connected"] is False


def test_the_control_the_same_probe_reaches_what_it_should(stack: Stack) -> None:
    """Without this the tests above could pass on a probe that never connects to anything."""
    for host, port in (("db", 5432), ("redis", 6379), ("api", 8000), ("parser", 7070)):
        assert reach(stack, "client", host, port)["connected"] is True, host
    assert reach(stack, "parser", "127.0.0.1", 7070)["connected"] is True


def test_the_worker_reaches_the_parser_and_nothing_of_the_default_network_does(
    stack: Stack,
) -> None:
    assert stack.driver("ping", "parser")["in_force"] is True
    for host in ("parser", "parser-cpu", "parser-kill", "parser-wall"):
        assert reach(stack, "peers", host, 7070) == {
            "resolved": None,
            "connected": False,
            "resolve_error": "gaierror",
        }
    parser = stack.inspect("parser")["NetworkSettings"]["Networks"][f"{stack.project}_parsing"]
    assert reach(stack, "peers", parser["IPAddress"], 7070)["connected"] is False


# --- the limits that take a minute --------------------------------------------------------------
# Started with the first test of the file, on instances of their own; awaited here, last.


def test_the_cpu_limit_ends_a_busy_child_at_45_s_of_cpu(stack: Stack, slow: dict) -> None:
    spin = slow["wait"]("spin")["spin"]
    assert spin["header"] == {**REFUSED, "code": "cpu_limit"}
    # 45 s of CPU take 45 s of wall clock or more, and the code says the wall clock (60 s) did
    # not end it: how much more is what the machine gave the child, which no bound here knows.
    assert 44_000 <= spin["ms"] <= WALL_MS + REAP_MS, "SIGXCPU at 45 s of CPU"
    alive(stack, "parser-cpu")


def test_a_child_that_ignores_the_cpu_signal_is_killed_a_second_later(
    stack: Stack, slow: dict
) -> None:
    """SIGXCPU can be ignored, and a child that was taken over would ignore it. The hard limit
    is one second above the soft one, and its SIGKILL cannot be."""
    deaf = slow["wait"]("deaf")["deaf"]
    assert deaf["header"] == {**REFUSED, "code": "crashed"}
    assert 45_000 <= deaf["ms"] <= WALL_MS + REAP_MS  # issue #104: 59 002 on a loaded machine
    alive(stack, "parser-kill")


def test_the_wall_clock_ends_a_sleeping_child_at_60_s_and_reaps_it(
    stack: Stack, slow: dict
) -> None:
    sleep = slow["wait"]("sleep")["sleep"]
    assert sleep["header"] == {**REFUSED, "code": "wall_limit"}
    assert WALL_MS - 1_000 <= sleep["ms"] <= WALL_MS + REAP_MS
    # Reaped: the next child sees the server and itself, and no process left over.
    after = stack.raw({"act": "state"}, host="parser-wall")["body"]["state"]
    assert left_over(stack, after, host="parser-wall") == set()
    alive(stack, "parser-wall")


# --- afterwards ---------------------------------------------------------------------------------


def test_zz_the_log_of_the_service_holds_no_word_of_a_child_or_of_a_document(
    stack: Stack,
) -> None:
    """Last in the file. Everything above went through this service: handlers that raised with
    words, wrote to their standard error, answered sentences. Its log is one JSON object per
    line with a kind, sizes, a code and durations."""
    stack.raw({"act": "stderr", "text": "PRIVATE-stderr-canary "})
    stack.raw({"act": "raise", "text": "PRIVATE-exception-canary"})
    stack.raw({"act": "write_raw", "text": "PRIVATE-answer-canary"})
    log = stack.run("logs", "--no-log-prefix", "--no-color", "parser")
    assert "PRIVATE" not in log
    assert "Traceback" not in log
    lines = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert len(lines) > 30
    events = {line["event"] for line in lines}
    assert events <= {"listening", "job", "refused", "ping", "stopped"}
    for line in lines:
        assert set(line) <= {
            "ts",
            "event",
            "kind",
            "size",
            "code",
            "ms",
            "out",
            "port",
            "uid",
            "status",
            "read",
        }
        assert line.get("code", "ok") in server.CODES
        assert line.get("kind", "ping") in server.KINDS


def test_zz_the_service_is_still_the_process_it_was_and_small(stack: Stack) -> None:
    for service in ("parser", "parser-cpu", "parser-kill", "parser-wall"):
        alive(stack, service)
    out = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}} {{.PIDs}}",
            stack.container("parser"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    used, pids = out[0], int(out[-1])
    assert used.endswith("MiB"), used
    assert float(used[:-3]) < 100, "one Python process with the standard library, after it all"
    assert pids == 1, "no child left, no thread kept"
