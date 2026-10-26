"""The `renderer` service as every Compose file of the stack writes it (task D3).

Table driven: for compose.yml alone and for compose.yml with each overlay that starts a stack
(compose.dev.yml, compose.mac.yml), the renderer that results must keep every hardening line of
the review of D1. An overlay that adds a port, a capability, an environment, `unconfined`, a
network or more memory, or takes a line away, fails here before any stack is started. Read from
the YAML, so these run with the unit tests on every change; the service as it really runs is
test_renderer_container.py.
"""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]

# The api folder alone is mounted in the dev container (compose.dev.yml): no compose.yml there.
pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yml").is_file(), reason="the repository root is not mounted here"
)

STACKS = [("compose.yml",), ("compose.yml", "compose.dev.yml"), ("compose.yml", "compose.mac.yml")]
MIB = 1024 * 1024


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8")) or {}


def merged(files: tuple[str, ...]) -> dict[str, Any]:
    """Compose's merge, for what is judged here: mappings key by key, sequences of the overlay
    added to the base's (ports, tmpfs, cap_add, security_opt, networks), scalars replaced."""

    def merge(base: Any, over: Any) -> Any:
        if isinstance(base, dict) and isinstance(over, dict):
            out = dict(base)
            for key, value in over.items():
                out[key] = merge(base[key], value) if key in base else copy.deepcopy(value)
            return out
        if isinstance(base, list) and isinstance(over, list):
            return [*base, *(item for item in over if item not in base)]
        return copy.deepcopy(over)

    config: dict[str, Any] = {}
    for name in files:
        config = merge(config, load(name))
    return config


def size(value: str) -> int:
    units = {"k": 1024, "m": MIB, "g": 1024 * MIB}
    return int(value[:-1]) * units[value[-1].lower()] if value[-1].isalpha() else int(value)


@pytest.fixture(params=STACKS, ids=lambda files: "+".join(files))
def stack(request: pytest.FixtureRequest) -> dict[str, Any]:
    return merged(request.param)


def test_every_file_of_the_table_exists() -> None:
    for files in STACKS:
        for name in files:
            assert (ROOT / name).is_file(), name


def test_item_1_non_root_uid_10003_and_no_app_secret_or_environment(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["user"] == "10003:10003"
    users = {name: s.get("user") for name, s in stack["services"].items() if name != "renderer"}
    assert "10003:10003" not in users.values(), users
    for forbidden in ("env_file", "environment", "secrets", "configs", "volumes", "privileged"):
        assert forbidden not in renderer, forbidden


def test_item_2_read_only_root_and_a_64m_tmpfs_on_tmp(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["read_only"] is True
    assert len(renderer["tmpfs"]) == 1
    target, _, options = renderer["tmpfs"][0].partition(":")
    assert target == "/tmp"  # noqa: S108  (a path inside the container)
    sizes = [size(o[5:]) for o in options.split(",") if o.startswith("size=")]
    assert sizes == [64 * MIB], renderer["tmpfs"]


def test_item_3_no_capability_and_no_new_privileges(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["cap_drop"] == ["ALL"]
    assert "cap_add" not in renderer
    assert "no-new-privileges:true" in renderer["security_opt"]


def test_item_4_docker_default_seccomp_profile_kept(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["security_opt"] == ["no-new-privileges:true"], "no seccomp or apparmor line"
    for forbidden in ("privileged", "cap_add", "userns_mode", "devices", "pid", "cgroup_parent"):
        assert forbidden not in renderer, forbidden
    text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "--no-sandbox" in text
    assert "The container is the boundary" in text


def test_item_5_pids_memory_and_no_ipc(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["pids_limit"] == 128
    assert size(renderer["mem_limit"]) == 256 * MIB  # D3's Jetson peak 134 MiB; 384m before B2
    assert renderer["ipc"] == "none"
    assert renderer["restart"] == "unless-stopped"
    server = (ROOT / "renderer" / "server.py").read_text(encoding="utf-8")
    assert '"--disable-dev-shm-usage"' in server


def test_item_6_an_internal_network_of_the_renderer_and_the_worker_only(stack: dict) -> None:
    services = stack["services"]
    assert list(services["renderer"]["networks"]) == ["rendering"]
    assert stack["networks"]["rendering"]["internal"] is True
    on_it = sorted(name for name, s in services.items() if "rendering" in (s.get("networks") or []))
    assert on_it == ["renderer", "worker"]
    for forbidden in ("ports", "network_mode", "extra_hosts", "links", "external_links"):
        assert forbidden not in services["renderer"], forbidden


def test_the_service_runs_its_own_image_and_its_health_check(stack: dict) -> None:
    renderer = stack["services"]["renderer"]
    assert renderer["image"] == "jobapp-renderer:latest"
    assert renderer["build"]["context"] == "./renderer"
    assert renderer["command"] == ["python", "-I", "/renderer/server.py"]
    assert renderer["healthcheck"]["test"] == [
        "CMD", "python", "-I", "/renderer/server.py", "--check",
    ]  # fmt: skip
    assert renderer["expose"] == ["7071"]


def test_memory_accounting_keeps_the_core_and_mail_limits_explicit() -> None:
    services = load("compose.yml")["services"]
    total = sum(size(s["mem_limit"]) for s in services.values())
    core = sum(size(s["mem_limit"]) for name, s in services.items() if name != "gmailmcp")
    target = 5 * 1024 * MIB
    # Keep the existing core allowance and platform target explicit. Email's separately
    # limited provider adds 128MiB; this accounting does not claim Jetson capacity acceptance.
    assert core == 5248 * MIB, core // MIB
    assert services["gmailmcp"]["profiles"] == ["mail"]
    assert size(services["gmailmcp"]["mem_limit"]) == 128 * MIB
    assert total == core + 128 * MIB, total // MIB
    assert core - target == 128 * MIB
    assert total - target == 256 * MIB


def test_a_weakened_overlay_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The checks above catch an overlay that weakens the service (the table can fail)."""
    bad = {"services": {"renderer": {"ports": ["7071:7071"], "cap_add": ["SYS_ADMIN"]}}}
    real = load

    def fake(name: str) -> dict[str, Any]:
        return bad if name == "bad.yml" else real(name)

    monkeypatch.setitem(globals(), "load", fake)
    stack = merged(("compose.yml", "bad.yml"))
    with pytest.raises(AssertionError):
        test_item_3_no_capability_and_no_new_privileges(stack)
    with pytest.raises(AssertionError):
        test_item_6_an_internal_network_of_the_renderer_and_the_worker_only(stack)
