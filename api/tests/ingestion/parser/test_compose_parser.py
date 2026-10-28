"""The `parser` service as the Compose files write it: every hardening key, the network it sits
on, and the files that give the worker another image and must give the parser the same.

Read from the YAML, so these run with the unit tests on every change (the service as it really
runs is test_container.py and tests/compose_smoke.sh). A line removed from compose.yml fails
here before any stack is started."""

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.ingestion.parser.helpers import HERE, ROOT, sandbox_module

# The api folder alone is mounted in the dev container (compose.dev.yml): no compose.yml there.
pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yml").is_file() and not os.environ.get("CI"),
    reason="the repository root is not mounted here",
)

server = sandbox_module("server")
MIB = 1024 * 1024


def compose(name: str = "compose.yml") -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def overlays() -> list[Path]:
    """Every Compose file of the repository but compose.yml and the tests' own stack."""
    found = [*ROOT.glob("compose.*.yml"), *(ROOT / "tests").glob("*.yml")]
    return sorted(path for path in found if path.name != "compose.yml")


@pytest.fixture(scope="module")
def parser() -> dict[str, Any]:
    return compose()["services"]["parser"]


def test_the_parser_runs_the_workers_image_from_the_servers_absolute_path(parser: dict) -> None:
    services = compose()["services"]
    assert parser["image"] == services["worker"]["image"] == "jobapp-worker:latest"
    assert "build" not in parser, "no build of its own: two builds to one tag race"
    assert parser["pull_policy"] == "never", "the tag exists on this host alone"
    assert parser["command"] == ["python", "-I", "/app/app/ingestion/sandbox/server.py"]
    assert parser["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-I",
        "/app/app/ingestion/sandbox/server.py",
        "--check",
    ]
    assert "entrypoint" not in parser


def test_every_hardening_key_of_the_plan_is_set(parser: dict) -> None:
    assert parser["user"] == "10002:10002"
    assert parser["user"] != compose()["services"].get("api", {}).get("user", "10001:10001")
    assert parser["cap_drop"] == ["ALL"]
    assert "cap_add" not in parser
    assert parser["security_opt"] == ["no-new-privileges:true"]
    assert parser["read_only"] is True
    assert parser["pids_limit"] == 32
    assert parser["mem_limit"] == "640m"
    assert parser["ipc"] == "none", "takes /dev/shm away, writable even in a read only container"
    assert parser["restart"] == "unless-stopped"
    for forbidden in (
        "privileged",
        "pid",
        "network_mode",
        "volumes",
        "tmpfs",
        "devices",
        "ports",
        "env_file",
        "secrets",
        "configs",
        "depends_on",
        "userns_mode",
        "extra_hosts",
        "dns",
        "links",
        "volumes_from",
        "sysctls",
        "group_add",
    ):
        assert forbidden not in parser, forbidden
    assert set(parser) == {
        "restart",
        "image",
        "pull_policy",
        "command",
        "user",
        "read_only",
        "cap_drop",
        "security_opt",
        "ipc",
        "pids_limit",
        "environment",
        "networks",
        "healthcheck",
        "mem_limit",
        "expose",
    }, "a key added to the service is a decision: add it here with its reason"


def test_the_environment_is_the_port_alone(parser: dict) -> None:
    assert parser["environment"] == {"PARSER_PORT": "7070"}
    assert parser["expose"] == ["7070"]
    assert int(parser["environment"]["PARSER_PORT"]) == server.DEFAULT_PORT


def test_the_parser_is_on_the_internal_network_and_only_the_worker_shares_it() -> None:
    document = compose()
    services = document["services"]
    # `rendering` is the renderer's own (documents D3, tests/documents/test_compose_renderer.py).
    assert document["networks"] == {
        "mailmcp": {"internal": True},
        "gmail_egress": {},  # the account-bound Gmail provider's own way out
        "parsing": {"internal": True},
        "rendering": {"internal": True},
        "browsing": {"internal": True},  # browser B2
        "egress": {},  # browser B2: the browser's own way out
    }
    assert services["parser"]["networks"] == ["parsing"]
    assert services["worker"]["networks"] == [
        "default",
        "parsing",
        "rendering",
        "browsing",
        "mailmcp",
    ]
    assert services["renderer"]["networks"] == ["rendering"]
    # browser B2: the api and caddy also join `browsing`, the browser is on `browsing` and `egress`.
    assert services["api"]["networks"] == ["default", "browsing", "mailmcp"]
    assert services["caddy"]["networks"] == ["default", "browsing"]
    assert services["browser"]["networks"] == ["browsing", "egress"]
    assert services["gmailmcp"]["networks"] == ["mailmcp", "gmail_egress"]
    # Check membership as well as each service's list: another service cannot join an
    # isolated parser, renderer or provider network while leaving those lists unchanged.
    for network, members in {
        "parsing": {"parser", "worker"},
        "rendering": {"renderer", "worker"},
        "mailmcp": {"api", "worker", "gmailmcp"},
        "gmail_egress": {"gmailmcp"},
    }.items():
        assert {
            name for name, service in services.items() if network in service.get("networks", [])
        } == members
    for name, service in services.items():
        assert "network_mode" not in service, name
        if name not in ("parser", "renderer", "worker", "api", "caddy", "browser", "gmailmcp"):
            assert "networks" not in service, f"{name} must stay on the default network alone"


def test_no_other_service_depends_on_the_parser() -> None:
    """The worker stays healthy without it: a parse that cannot connect fails as a failure of
    the server and is tried again later."""
    for name, service in compose()["services"].items():
        assert "parser" not in (service.get("depends_on") or {}), name


def test_no_extension_field_can_bring_a_secret_or_a_volume_to_the_parser() -> None:
    document = compose()
    assert set(document) == {"name", "x-restart", "services", "networks", "volumes"}
    assert document["x-restart"] == {"restart": "unless-stopped"}


@pytest.mark.parametrize("path", overlays(), ids=lambda path: path.name)
def test_a_file_that_gives_the_worker_another_image_gives_the_parser_the_same(path: Path) -> None:
    """compose.dev.yml and tests/compose.a2-e2e.yml run the worker from a tag of their own. A
    parser left on jobapp-worker:latest there would run other code than the worker beside it, or
    no image at all on a machine that never built that tag."""
    services = yaml.safe_load(path.read_text(encoding="utf-8")).get("services", {})
    worker, parser = services.get("worker", {}), services.get("parser", {})
    assert parser.get("image") == worker.get("image"), path.name
    if "image" in worker:
        assert worker["image"].startswith("jobapp-worker:")


@pytest.mark.parametrize("path", overlays(), ids=lambda path: path.name)
def test_no_file_loosens_the_parser(path: Path) -> None:
    """An overlay may give the parser its image, a quicker first probe and, for development, the
    code mounted read only. Nothing else: no secret, no network, no capability."""
    parser = yaml.safe_load(path.read_text(encoding="utf-8")).get("services", {}).get("parser", {})
    assert set(parser) <= {"image", "healthcheck", "volumes"}, path.name
    assert set(parser.get("healthcheck", {})) <= {"start_period", "start_interval"}
    for volume in parser.get("volumes", []):
        assert volume == "./api/app:/app/app:ro", "a bind mount is not covered by read_only"
    if "volumes" in parser:
        assert path.name == "compose.dev.yml"


def test_the_overlays_with_another_worker_image_are_the_two_the_plan_names() -> None:
    with_image = sorted(
        path.name
        for path in overlays()
        if "image" in yaml.safe_load(path.read_text(encoding="utf-8"))["services"].get("worker", {})
    )
    assert with_image == ["compose.a2-e2e.yml", "compose.dev.yml"]


def test_the_mac_overlay_has_no_entry_for_the_parser() -> None:
    """compose.mac.yml sets OPENSSL_armcap=0 for what imports `cryptography`. The parser never
    does (test_sandbox_unit.py, test_container.py): an entry here would say otherwise."""
    assert "parser" not in compose("compose.mac.yml")["services"]
    assert "environment" not in compose("compose.dev.yml")["services"]["parser"]


def test_the_ci_stack_probes_the_parser_as_soon_as_it_starts() -> None:
    """Without it every e2e stack of CI would wait one whole interval, 15 s, for the first
    probe of the parser."""
    overlay = yaml.safe_load((ROOT / "tests/ats-stub.compose.yml").read_text(encoding="utf-8"))
    assert overlay["services"]["parser"] == {
        "healthcheck": {"start_period": "30s", "start_interval": "1s"}
    }


def test_the_memory_limits_keep_the_core_exception_and_optional_mail_cost_explicit() -> None:
    """Keep the 5 GiB target and B2's 128 MiB core exception; mail adds another 128 MiB.
    Declared limits do not establish physical Jetson capacity acceptance."""
    services = compose()["services"]
    limits = {name: service["mem_limit"] for name, service in services.items()}
    core_limits = {name: limit for name, limit in limits.items() if name != "gmailmcp"}
    assert core_limits == {
        "caddy": "128m",
        "tailscale": "128m",
        "api": "512m",
        "worker": "1536m",
        "parser": "640m",
        "db": "768m",
        "redis": "256m",
        "renderer": "256m",
        "browser": "1024m",
    }
    # 128m over the 5G budget with the browser (B2) until G8 sets the parser's limit from its
    # Jetson peaks: the parser at 512m brings the stack to 5120m (compose.yml, budget comment).
    assert sum(int(limit[:-1]) for limit in core_limits.values()) == 5248 == 5 * 1024 + 128
    assert services["gmailmcp"]["profiles"] == ["mail"]
    assert limits["gmailmcp"] == "128m"
    assert sum(int(limit[:-1]) for limit in limits.values()) == 5376 == 5 * 1024 + 256


def test_the_test_stack_takes_the_service_from_compose_yml_and_adds_no_privilege() -> None:
    """test_container.py tests what compose.yml defines only as long as its own file extends
    that service and loosens nothing."""
    document = yaml.safe_load((HERE / "compose.sandbox.yml").read_text(encoding="utf-8"))
    template = document["x-parser"]
    assert template["extends"] == {"file": "../../../../compose.yml", "service": "parser"}
    assert (HERE / template["extends"]["file"]).resolve() == ROOT / "compose.yml"
    assert set(template) == {"extends", "image", "restart", "healthcheck", "volumes"}
    # The tests' own handler stands for two kinds, and the null device takes the file of a
    # third away (a kind without a handler, since G8 gave every kind its file): read only, in
    # the folder of the handlers, and nothing of the host besides.
    assert template["volumes"] == [
        "./handlers/hostile.py:/app/app/ingestion/sandbox/pdf_render.py:ro",
        "./handlers/hostile.py:/app/app/ingestion/sandbox/docx.py:ro",
        "/dev/null:/app/app/ingestion/sandbox/image.py:ro",
    ]
    for name in ("parser", "parser-cpu", "parser-kill", "parser-wall"):
        assert document["services"][name] == template
    unfiltered = document["services"]["parser-unfiltered"]
    assert {key: value for key, value in unfiltered.items() if key != "security_opt"} == template
    assert unfiltered["security_opt"] == ["seccomp=./no-filter-install.json"]
    assert document["networks"] == {"parsing": {"internal": True}}
    assert document["services"]["client"]["networks"] == ["default", "parsing"]
    assert list(document["services"]["peers"]["networks"]) == ["default"]
