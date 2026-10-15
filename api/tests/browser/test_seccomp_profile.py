"""browser/seccomp.json (browser B6, issue #230): Docker's default profile, unchanged, plus what
Chromium's namespace sandbox needs and nothing else (B1 measured it: new user, PID and network
namespaces, and chroot into /proc/self/fdinfo/). The container runs it with every capability
dropped but SETUID and SETGID, so the rules are read here as the runtime reads them for that set.
The container test (test_browser_container.py, test_chromium_sandbox_is_on) proves Chromium
starts sandboxed under it."""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "browser/seccomp.json"
pytestmark = pytest.mark.skipif(not PROFILE.is_file(), reason="the repository root is not mounted")

# Docker's default profile as moby/profiles holds it (seccomp/default.json on main, read on
# 2026-10-14; the same 60 rules B2 copied): sha256 of its json.dumps(sort_keys=True).
DOCKER_DEFAULT_SHA256 = "2ebf3bdba229d3d972cfc3721001306de96cc5113a14b29cb510c2edd91ca84a"
DOCKER_DEFAULT_RULES = 60
CAPS = {"CAP_SETUID", "CAP_SETGID"}  # compose.yml: cap_drop ALL, cap_add SETUID SETGID
KERNEL = (5, 15)  # the Jetson's Tegra kernel (B1)

SIGCHLD = 17
CLONE_VM, CLONE_FS, CLONE_FILES, CLONE_SIGHAND = 0x100, 0x200, 0x400, 0x800
CLONE_VFORK, CLONE_THREAD, CLONE_SYSVSEM = 0x4000, 0x10000, 0x40000
CLONE_NEWTIME, CLONE_NEWNS, CLONE_NEWCGROUP = 0x80, 0x20000, 0x2000000
CLONE_NEWUTS, CLONE_NEWIPC, CLONE_NEWUSER = 0x4000000, 0x8000000, 0x10000000
CLONE_NEWPID, CLONE_NEWNET = 0x20000000, 0x40000000
SANDBOX = CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNET


def profile() -> dict:
    return json.loads(PROFILE.read_text())


def kernel(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def applies(rule: dict, arch: str) -> bool:
    inc, exc = rule.get("includes", {}), rule.get("excludes", {})
    if not set(inc.get("caps", [])) <= CAPS or set(exc.get("caps", [])) & CAPS:
        return False
    if "arches" in inc and arch not in inc["arches"]:
        return False
    if arch in exc.get("arches", []):
        return False
    return "minKernel" not in inc or kernel(inc["minKernel"]) <= KERNEL


def matches(arg: dict, args: tuple[int, ...]) -> bool:
    value = args[arg["index"]] if arg["index"] < len(args) else 0
    if arg["op"] == "SCMP_CMP_MASKED_EQ":
        return value & arg["value"] == arg.get("valueTwo", 0)
    if arg["op"] == "SCMP_CMP_EQ":
        return value == arg["value"]
    if arg["op"] == "SCMP_CMP_NE":
        return value != arg["value"]
    raise AssertionError(f"operator not modelled: {arg['op']}")


def verdict(name: str, *args: int, arch: str = "arm64") -> str:
    """ALLOW, or the errno the kernel answers, for one call (an allow rule wins, as in libseccomp
    where the default is an error and every rule here allows or names an errno)."""
    data, errno = profile(), None
    for rule in data["syscalls"]:
        if name not in rule["names"] or not applies(rule, arch):
            continue
        if all(matches(arg, args) for arg in rule.get("args", [])):
            if rule["action"] == "SCMP_ACT_ALLOW":
                return "ALLOW"
            errno = rule.get("errnoRet", data["defaultErrnoRet"])
    return f"ERRNO {errno if errno is not None else data['defaultErrnoRet']}"


def test_the_first_rules_are_dockers_default_unchanged() -> None:
    data = profile()
    default = dict(data, syscalls=data["syscalls"][:DOCKER_DEFAULT_RULES])
    digest = hashlib.sha256(json.dumps(default, sort_keys=True).encode()).hexdigest()
    assert digest == DOCKER_DEFAULT_SHA256


def test_only_the_namespace_rules_are_added() -> None:
    added = profile()["syscalls"][DOCKER_DEFAULT_RULES:]
    assert [rule["names"] for rule in added] == [["clone"], ["clone"], ["unshare"], ["chroot"]]
    assert all(rule["action"] == "SCMP_ACT_ALLOW" for rule in added)
    assert not any("setns" in rule["names"] for rule in added)


@pytest.mark.parametrize(
    ("name", "flags", "expected"),
    [
        # What Chromium's sandbox does (B1): its zygote, its probe, its chroot helper.
        ("clone", SANDBOX | SIGCHLD, "ALLOW"),
        ("clone", CLONE_NEWUSER | SIGCHLD, "ALLOW"),
        ("clone", CLONE_NEWUSER | CLONE_NEWPID | SIGCHLD, "ALLOW"),
        ("clone", CLONE_FS | CLONE_VM | CLONE_VFORK | SIGCHLD, "ALLOW"),
        ("unshare", CLONE_NEWUSER, "ALLOW"),
        ("unshare", CLONE_NEWNET, "ALLOW"),
        ("unshare", SANDBOX, "ALLOW"),
        ("unshare", CLONE_FS | CLONE_FILES | CLONE_SYSVSEM, "ALLOW"),
        # What every process does: Docker's default already allowed it.
        ("clone", CLONE_VM | CLONE_FS | CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD, "ALLOW"),
        ("clone", SIGCHLD, "ALLOW"),
        # The other namespaces, alone and riding along with the allowed ones.
        ("clone", CLONE_NEWNS | SIGCHLD, "ERRNO 1"),
        ("clone", SANDBOX | CLONE_NEWNS, "ERRNO 1"),
        ("clone", CLONE_NEWUSER | CLONE_NEWUTS, "ERRNO 1"),
        ("clone", CLONE_NEWUSER | CLONE_NEWIPC, "ERRNO 1"),
        ("clone", CLONE_NEWUSER | CLONE_NEWCGROUP, "ERRNO 1"),
        ("clone", CLONE_NEWUTS, "ERRNO 1"),
        ("clone", CLONE_NEWIPC, "ERRNO 1"),
        ("clone", CLONE_NEWCGROUP, "ERRNO 1"),
        ("unshare", CLONE_NEWNS, "ERRNO 1"),
        ("unshare", CLONE_NEWUSER | CLONE_NEWNS, "ERRNO 1"),
        ("unshare", SANDBOX | CLONE_NEWNS, "ERRNO 1"),
        ("unshare", CLONE_NEWUTS, "ERRNO 1"),
        ("unshare", CLONE_NEWIPC, "ERRNO 1"),
        ("unshare", CLONE_NEWCGROUP, "ERRNO 1"),
        ("unshare", CLONE_NEWTIME, "ERRNO 1"),
        ("unshare", CLONE_NEWUSER | CLONE_NEWTIME, "ERRNO 1"),
        # Entering another namespace, and what a user namespace would otherwise open up.
        ("setns", 0, "ERRNO 1"),
        ("setns", CLONE_NEWUSER, "ERRNO 1"),
        ("mount", 0, "ERRNO 1"),
        ("umount2", 0, "ERRNO 1"),
        ("pivot_root", 0, "ERRNO 1"),
        ("open_tree", 0, "ERRNO 1"),
        ("move_mount", 0, "ERRNO 1"),
        ("fsopen", 0, "ERRNO 1"),
        ("bpf", 0, "ERRNO 1"),
        ("ptrace", 0, "ALLOW"),  # Docker's default on kernels from 4.8, unchanged here
        ("chroot", 0, "ALLOW"),
        # glibc falls back to clone when clone3 answers ENOSYS (Docker's default, unchanged).
        ("clone3", 0, "ERRNO 38"),
    ],
)
def test_the_sandbox_calls_and_their_neighbours(name: str, flags: int, expected: str) -> None:
    assert verdict(name, flags) == expected
    assert verdict(name, flags, arch="amd64") == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [(SANDBOX | SIGCHLD, "ALLOW"), (CLONE_NEWNS | SIGCHLD, "ERRNO 1"), (SIGCHLD, "ALLOW")],
)
def test_clone_on_s390_reads_its_second_argument(flags: int, expected: str) -> None:
    assert verdict("clone", 0, flags, arch="s390x") == expected
    assert verdict("clone", flags, 0, arch="s390x") == "ALLOW"  # the first is the stack there
