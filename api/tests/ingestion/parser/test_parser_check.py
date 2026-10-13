"""tests/parser_check.py, what the smoke scripts use to judge the parser container as it runs
(tests/compose_smoke.sh on the Mac, tests/jetson_smoke.sh on every deploy). A judge that
passed anything would make those checks worth nothing: every key it looks at is changed here,
one at a time, and must be reported."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from typing import Any

import pytest

from tests.ingestion.parser.helpers import ROOT

SCRIPT = ROOT / "tests" / "parser_check.py"
pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() and not os.environ.get("CI"),
    reason="the repository root is not mounted here",
)


def load() -> Any:
    spec = importlib.util.spec_from_file_location("parser_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def container() -> dict[str, Any]:
    """`docker inspect` of the parser as compose.yml starts it, cut to what is judged."""
    return {
        "RestartCount": 0,
        "Config": {
            "User": "10002:10002",
            "Env": [
                "PARSER_PORT=7070",
                "PATH=/app/.venv/bin:/usr/bin",
                "GPG_KEY=7169605F",
                "LANG=C",
            ],
        },
        "HostConfig": {
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 32,
            "Memory": 640 * 1024 * 1024,
            "IpcMode": "none",
            "PidMode": "",
            "UsernsMode": "",
            "PortBindings": {},
        },
        "NetworkSettings": {"Networks": {"jobapp_parsing": {}}, "Ports": {"7070/tcp": None}},
        "Mounts": [],
        "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
    }


def changed(path: str, value: Any) -> dict[str, Any]:
    found = copy.deepcopy(container())
    *parents, last = path.split(".")
    target = found
    for key in parents:
        target = target[key]
    target[last] = value
    return found


def test_the_parser_as_compose_yml_starts_it_has_no_problem() -> None:
    check = load()
    assert check.problems_of(container()) == []
    assert check.problems_of(container(), secret_names=["SECRET_KEY", "DATABASE_URL"]) == []


@pytest.mark.parametrize(
    ("path", "value", "word"),
    [
        ("Config.User", "10001:10001", "user"),
        ("Config.User", "", "user"),
        ("Config.User", "0", "user"),
        ("HostConfig.CapDrop", None, "cap_drop"),
        ("HostConfig.CapDrop", ["NET_RAW"], "cap_drop"),
        ("HostConfig.CapAdd", ["SYS_PTRACE"], "cap_add"),
        ("HostConfig.Privileged", True, "privileged"),
        ("HostConfig.ReadonlyRootfs", False, "read only root"),
        ("HostConfig.SecurityOpt", None, "security_opt"),
        (
            "HostConfig.SecurityOpt",
            ["no-new-privileges:true", "seccomp=unconfined"],
            "security_opt",
        ),
        ("HostConfig.PidsLimit", 0, "pids limit"),
        ("HostConfig.PidsLimit", None, "pids limit"),
        ("HostConfig.Memory", 0, "memory limit"),
        ("HostConfig.Memory", 1536 * 1024 * 1024, "memory limit"),
        ("HostConfig.IpcMode", "private", "ipc mode"),
        ("HostConfig.IpcMode", "host", "ipc mode"),
        ("HostConfig.PidMode", "host", "pid mode"),
        ("HostConfig.PidMode", "container:abc", "pid mode"),
        ("HostConfig.UsernsMode", "host", "userns mode"),
        ("HostConfig.Tmpfs", {"/scratch": "size=128m"}, "tmpfs"),
        ("HostConfig.Devices", [{"PathOnHost": "/dev/fuse"}], "devices"),
        ("HostConfig.PortBindings", {"7070/tcp": [{"HostPort": "7070"}]}, "port bindings"),
        ("NetworkSettings.Networks", {"jobapp_default": {}}, "networks"),
        ("NetworkSettings.Networks", {"jobapp_default": {}, "jobapp_parsing": {}}, "networks"),
        ("NetworkSettings.Networks", {}, "networks"),
        ("NetworkSettings.Ports", {"7070/tcp": [{"HostIp": "127.0.0.1"}]}, "published ports"),
        ("Mounts", [{"Destination": "/scratch", "RW": True}], "mount at /scratch"),
        ("Mounts", [{"Destination": "/app/app", "RW": False}], "mount at /app/app"),
        ("Mounts", [{"Destination": "/var/run/docker.sock", "RW": True}], "docker.sock"),
        ("Config.Env", ["PATH=/usr/bin"], "PARSER_PORT"),
        ("Config.Env", ["PARSER_PORT=7070", "CREDENTIALS_KEY=x"], "CREDENTIALS_KEY"),
        ("Config.Env", ["PARSER_PORT=7070", "DATABASE_URL=x"], "DATABASE_URL"),
        ("Config.Env", ["PARSER_PORT=7070", "REDIS_URL=x"], "REDIS_URL"),
        ("Config.Env", ["PARSER_PORT=7070", "ANTHROPIC_API_KEY=x"], "ANTHROPIC_API_KEY"),
        ("Config.Env", ["PARSER_PORT=7070", "TS_AUTHKEY=x"], "TS_AUTHKEY"),
        ("Config.Env", ["PARSER_PORT=7070", "POSTGRES_PASSWORD=x"], "POSTGRES_PASSWORD"),
        ("Config.Env", ["PARSER_PORT=7070", "OPENSSL_armcap=0"], "armcap"),
        ("State.Running", False, "running"),
        ("State.Health", {"Status": "unhealthy"}, "health"),
        ("State.Health", {"Status": "starting"}, "health"),
        ("State.Health", None, "health"),
        ("State.OOMKilled", True, "killed for memory"),
    ],
)
def test_every_key_it_judges_is_reported_when_it_is_wrong(path: str, value: Any, word: str) -> None:
    problems = load().problems_of(changed(path, value))
    assert len(problems) >= 1
    assert any(word in problem for problem in problems), problems


def test_a_variable_of_the_stacks_env_file_is_a_problem_whatever_its_name() -> None:
    """The names of every variable of .env are given to the judge: one of them in the parser's
    environment means an env_file reached it, even where the name says nothing (APP_ENV)."""
    check = load()
    found = changed("Config.Env", ["PARSER_PORT=7070", "APP_ENV=production", "LOG_LEVEL=info"])
    assert check.problems_of(found) == []
    problems = check.problems_of(found, secret_names=["APP_ENV", "LOG_LEVEL", "SECRET_KEY"])
    assert problems == [
        "APP_ENV of the stack's .env is in its environment",
        "LOG_LEVEL of the stack's .env is in its environment",
    ]


def test_a_value_is_never_printed() -> None:
    check = load()
    found = changed("Config.Env", ["PARSER_PORT=7070", "CREDENTIALS_KEY=PRIVATE-value-canary"])
    assert "PRIVATE" not in " ".join(check.problems_of(found, secret_names=["CREDENTIALS_KEY"]))


def test_the_code_mounted_read_only_is_allowed_for_the_dev_stack_alone() -> None:
    check = load()
    dev = changed("Mounts", [{"Destination": "/app/app", "RW": False}])
    assert check.problems_of(dev, code_mount=True) == []
    assert check.problems_of(dev) == ["mount at /app/app (writable: False)"]
    writable = changed("Mounts", [{"Destination": "/app/app", "RW": True}])
    assert check.problems_of(writable, code_mount=True) == ["mount at /app/app (writable: True)"]
    other = changed("Mounts", [{"Destination": "/app", "RW": False}])
    assert check.problems_of(other, code_mount=True) == ["mount at /app (writable: False)"]


def run(*arguments: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_judges_what_docker_inspect_prints() -> None:
    done = run("inspect", "--secret-name", "SECRET_KEY", stdin=json.dumps([container()]))
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == (
        "uid 10002:10002, no capability, no new privileges, read only root, ipc none, 32 pids, "
        "640 MiB, network jobapp_parsing alone, nothing published or mounted, 4 variables and "
        "none of the 1 names of .env, restarts 0\n"
    )
    bad = changed("Config.Env", ["PARSER_PORT=7070", "SECRET_KEY=PRIVATE-value-canary"])
    bad["HostConfig"]["ReadonlyRootfs"] = False
    done = run("inspect", "--secret-name", "SECRET_KEY", stdin=json.dumps([bad]))
    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.splitlines() == [
        "parser: read only root is False, expected True",
        "parser: SECRET_KEY of the stack's .env is in its environment",
    ]
    assert "PRIVATE" not in done.stderr


@pytest.mark.parametrize(
    ("arguments", "stdin"),
    [
        (["inspect"], ""),
        (["inspect"], "not json"),
        (["inspect"], "[]"),
        (["inspect"], json.dumps([container(), container()])),
        (["inspect", "--no-such-option"], json.dumps([container()])),
        (["inspect", "--secret-name"], json.dumps([container()])),
        ([], ""),
        (["reach"], ""),
        (["judge"], ""),
    ],
)
def test_the_command_refuses_what_it_cannot_judge(arguments: list[str], stdin: str) -> None:
    """Exit 2, never 0: a smoke script that fed it nothing must not read that as a pass."""
    done = run(*arguments, stdin=stdin)
    assert done.returncode == 2
    assert done.stdout == ""


def test_the_probe_says_what_resolved_and_what_connected() -> None:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            closed = free.getsockname()[1]
        done = run("reach", f"127.0.0.1:{port}", f"127.0.0.1:{closed}", "no-such-host.invalid:80")
    assert done.returncode == 0
    assert done.stdout.splitlines() == [
        f"127.0.0.1:{port} resolved=1 connected=1",
        f"127.0.0.1:{closed} resolved=1 connected=0",
        "no-such-host.invalid:80 resolved=0 connected=0",
    ]
