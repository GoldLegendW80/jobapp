"""The `browser` service as every Compose file of the stack writes it (task B2).

Table driven, as D3's test of the renderer: for compose.yml alone and with each overlay that
starts a stack (compose.dev.yml, compose.mac.yml, and the test stack tests/browser.compose.yml,
which extends the service), the browser that results keeps every line of its hardening. An
overlay that publishes a port, adds a capability or an env_file, drops the seccomp profile,
joins `default` or gives more memory fails here before any stack starts. The service as it
really runs is test_browser_container.py.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yml").is_file(), reason="the repository root is not mounted here"
)

STACKS = [("compose.yml",), ("compose.yml", "compose.dev.yml"), ("compose.yml", "compose.mac.yml")]
MIB = 1024 * 1024
ENV = {
    "APP_ENV",  # only to refuse BROWSER_PROXY_ALLOW_PRIVATE under production (B6)
    "BROWSER_CONTROL_SECRET",
    "BROWSER_LEASE_MAX_S",
    "BROWSER_IDLE_S",
    "BROWSER_SESSION_MAX_S",
    "BROWSER_MEM_GUARD_PCT",
}


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8")) or {}


def merge(base: Any, over: Any) -> Any:
    """Compose's merge for what is judged here: mappings key by key, the overlay's sequence
    items added to the base's, scalars replaced."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = merge(base[key], value) if key in base else copy.deepcopy(value)
        return out
    if isinstance(base, list) and isinstance(over, list):
        return [*base, *(item for item in over if item not in base)]
    return copy.deepcopy(over)


def merged(files: tuple[str, ...]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for name in files:
        config = merge(config, load(name))
    return config


def browser_test_stack() -> dict[str, Any]:
    """tests/browser.compose.yml: its browser is compose.yml's through `extends`, plus what
    the test changes (the image, the pages server allowed by the proxy)."""
    overlay = load("tests/browser.compose.yml")
    own = overlay["services"]["browser"]
    assert own["extends"] == {"file": "compose.yml", "service": "browser"}  # --project-directory .
    extra = {k: v for k, v in own.items() if k != "extends"}
    assert set(extra) == {"image", "environment"}, extra
    assert extra["environment"] == {"BROWSER_PROXY_ALLOW_PRIVATE": "pages"}
    base = load("compose.yml")
    service = merge(base["services"]["browser"], extra)
    return {
        **base,
        "services": {**overlay["services"], "browser": service},
        "networks": overlay["networks"],
        "volumes": overlay["volumes"],
    }


def size(value: str) -> int:
    units = {"k": 1024, "m": MIB, "g": 1024 * MIB}
    return int(value[:-1]) * units[value[-1].lower()] if value[-1].isalpha() else int(value)


@pytest.fixture(params=[*STACKS, ("tests/browser.compose.yml",)], ids=lambda f: "+".join(f))
def stack(request: pytest.FixtureRequest) -> dict[str, Any]:
    if request.param == ("tests/browser.compose.yml",):
        return browser_test_stack()
    return merged(request.param)


def test_root_with_setuid_and_setgid_only_and_no_new_privileges(stack: dict) -> None:
    browser = stack["services"]["browser"]
    assert browser["user"] == "0:0"
    assert browser["cap_drop"] == ["ALL"]
    assert sorted(browser["cap_add"]) == ["SETGID", "SETUID"]
    assert browser["security_opt"][0] == "no-new-privileges:true"
    for forbidden in ("privileged", "userns_mode", "devices", "pid", "ipc", "cgroup_parent"):
        assert forbidden not in browser, forbidden


def test_chromium_sandbox_kept_by_the_seccomp_profile(stack: dict) -> None:
    """Item 1 of B1's review: Docker's default profile plus the namespace rules (narrowed by B6,
    test_seccomp_profile.py), never unconfined, and Chromium never started with --no-sandbox."""
    browser = stack["services"]["browser"]
    assert browser["security_opt"] == ["no-new-privileges:true", "seccomp=./browser/seccomp.json"]
    profile = json.loads((ROOT / "browser/seccomp.json").read_text(encoding="utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    added = profile["syscalls"][-4:]
    assert [rule["names"] for rule in added] == [["clone"], ["clone"], ["unshare"], ["chroot"]]
    for rule in profile["syscalls"][:-4]:
        # Docker's own rules: the namespace calls are allowed there only with CAP_SYS_ADMIN or
        # without the CLONE_NEW* flags, which is what the appended rule widens.
        if rule["action"] == "SCMP_ACT_ALLOW" and "unshare" in rule["names"]:
            assert rule.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"], rule
    code = (ROOT / "browser/browserd/chromium.py").read_text(encoding="utf-8")
    assert '"--no-sandbox"' not in code
    assert '"--headless' not in code


def test_read_only_root_and_two_tmpfs(stack: dict) -> None:
    browser = stack["services"]["browser"]
    assert browser["read_only"] is True
    targets = {entry.partition(":")[0]: entry.partition(":")[2] for entry in browser["tmpfs"]}
    assert targets == {"/tmp": "size=64m,mode=1777", "/leases": "size=64m,mode=1733"}  # noqa: S108
    assert browser["volumes"] == ["browserprofiles:/profiles"]
    assert "browserprofiles" in stack["volumes"]


def test_no_app_secret_nor_env_file(stack: dict) -> None:
    browser = stack["services"]["browser"]
    for forbidden in ("env_file", "secrets", "configs"):
        assert forbidden not in browser, forbidden
    assert set(browser["environment"]) - {"BROWSER_PROXY_ALLOW_PRIVATE"} == ENV
    assert (
        "BROWSER_PROXY_ALLOW_PRIVATE"
        not in load("compose.yml")["services"]["browser"]["environment"]
    )


def test_memory_swap_shm_and_pids(stack: dict) -> None:
    """Items 6 and 9: 1024m and no swap beyond it (memswap_limit equal), 128m of shm."""
    browser = stack["services"]["browser"]
    assert size(browser["mem_limit"]) == 1024 * MIB
    assert browser["memswap_limit"] == browser["mem_limit"]
    assert size(browser["shm_size"]) == 128 * MIB
    assert browser["pids_limit"] == 1024
    assert browser["stop_grace_period"] == "15s"


def test_networks_browsing_internal_and_egress_its_own(stack: dict) -> None:
    services = stack["services"]
    assert sorted(services["browser"]["networks"]) == ["browsing", "egress"]
    assert stack["networks"]["browsing"]["internal"] is True
    if "pages" not in stack["services"]:  # the test stack's egress is internal: no real host
        assert not (stack["networks"]["egress"] or {}).get("internal")
    on_browsing = sorted(n for n, s in services.items() if "browsing" in (s.get("networks") or []))
    on_egress = sorted(n for n, s in services.items() if "egress" in (s.get("networks") or []))
    if "pages" in services:  # the test stack: the pages server is the public side
        assert on_egress == ["browser", "pages"]
    else:
        assert on_browsing == ["api", "browser", "caddy", "worker"]
        assert on_egress == ["browser"]
    for forbidden in ("ports", "network_mode", "extra_hosts", "links", "external_links"):
        assert forbidden not in services["browser"], forbidden


def test_own_image_profile_command_and_health_check(stack: dict) -> None:
    browser = stack["services"]["browser"]
    assert browser["profiles"] == ["browser"]
    assert browser["command"] == ["python", "-m", "browserd"]
    assert browser["healthcheck"]["test"] == ["CMD", "python", "-m", "browserd", "--check"]
    assert browser["expose"] == ["9300"]
    assert browser["restart"] == "unless-stopped"


def test_playwright_pinned_exactly() -> None:
    """Item 2: the version browserd/pages.py names is the one the image installs."""
    dockerfile = (ROOT / "browser/Dockerfile").read_text(encoding="utf-8")
    assert "ARG PLAYWRIGHT_VERSION=1.55.0\n" in dockerfile
    assert '"playwright==${PLAYWRIGHT_VERSION}"' in dockerfile
    pages = (ROOT / "browser/browserd/pages.py").read_text(encoding="utf-8")
    assert 'PLAYWRIGHT_VERSION = "1.55.0"' in pages
    assert pages.count("send(SNAPSHOT_METHOD") == 1
    others = [
        p.name
        for p in (ROOT / "browser/browserd").rglob("*.py")
        if p.name != "pages.py" and "snapshotForAI" in p.read_text(encoding="utf-8")
    ]
    assert others == [], "the private snapshot call lives in browserd/pages.py alone"


def test_a_weakened_overlay_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {
        "services": {
            "browser": {"ports": ["9300:9300"], "cap_add": ["SYS_ADMIN"], "networks": ["default"]}
        }
    }
    real = load
    monkeypatch.setitem(globals(), "load", lambda name: bad if name == "bad.yml" else real(name))
    stack = merged(("compose.yml", "bad.yml"))
    with pytest.raises(AssertionError):
        test_root_with_setuid_and_setgid_only_and_no_new_privileges(stack)
    with pytest.raises(AssertionError):
        test_networks_browsing_internal_and_egress_its_own(stack)
