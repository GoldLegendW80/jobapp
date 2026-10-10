"""OPENSSL_armcap=0 is a workaround for Docker on Apple silicon with SME and stays on the Mac.

`cryptography` 47 and later crash with SIGILL at import inside Docker on an M4 unless OpenSSL's
CPU detection is turned off (issue #19). Turned off, AES runs in portable code, so the variable
must never reach the Jetson: not through compose.yml (the only file a deploy uses), the image,
the .env template or the deploy scripts. The two Mac files must keep it, on the two services
that import `cryptography`, or every local stack dies on start.

CI runs on the Mac too since issue #83, in Linux arm64 runners inside Docker Desktop: there the
variable comes from the runner image (ops/ci-runner/Dockerfile), for the processes of a job and,
through CI_COMPOSE_OVERLAY=compose.mac.yml, for its scratch stacks. The workflow file itself
still never names it, so the same file runs unchanged on GitHub's machines, and the Jetson's own
runner (ops/runner) never has it.
"""

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NAME = "OPENSSL_armcap"

# The api folder alone is mounted in the dev container (compose.dev.yml), so the repository root
# is not there. CI and a checkout on the Mac always have it.
pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yml").is_file() and not os.environ.get("CI"),
    reason="the repository root is not mounted here",
)

# What runs on the Jetson or in CI: none of it may name the variable outside a comment.
PRODUCTION_FILES = [
    "compose.yml",
    "api/Dockerfile",
    ".env.example",
    "deploy.sh",
    "ops/env_check.sh",
    ".github/workflows/deploy.yml",
    ".github/workflows/ci.yml",
    "ops/runner/Dockerfile",
    "ops/runner/entrypoint.sh",
    "ops/runner/job-guard.sh",
    ".github/scripts/ci_env.sh",
    "tests/ats-stub.compose.yml",
    "tests/anthropic-stub.compose.yml",
    "tests/compose.a2-e2e.yml",
    "tests/compose.j5-e2e.yml",
]
MAC_FILES = ["compose.dev.yml", "compose.mac.yml"]


def code_lines(path: Path) -> list[str]:
    """The lines that are not whole-line comments (these files all comment with #)."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def environment_of(service: dict[str, object]) -> dict[str, str | None]:
    """A service's `environment`, from either Compose form: a mapping, or a list of NAME=value
    and bare NAME (pass the host's value through)."""
    value = service.get("environment") or {}
    if isinstance(value, dict):
        return {str(key): None if item is None else str(item) for key, item in value.items()}
    assert isinstance(value, list)
    pairs = (str(entry).partition("=") for entry in value)
    return {key: (item if sep else None) for key, sep, item in pairs}


@pytest.mark.parametrize("name", PRODUCTION_FILES)
def test_nothing_that_reaches_the_jetson_or_ci_names_it(name: str) -> None:
    offending = [line for line in code_lines(ROOT / name) if "armcap" in line.lower()]
    assert offending == [], f"{name} must not set or pass {NAME}: the Jetson keeps hardware AES"


def test_compose_yml_sets_it_on_no_service() -> None:
    document = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    assert {"api", "worker"} <= set(document["services"])
    for service, definition in document["services"].items():
        names = {key.lower() for key in environment_of(definition)}
        assert not any("armcap" in key for key in names), service
    # No shared block either: an extension field would reach every service that merges it.
    extensions = {key: value for key, value in document.items() if key.startswith("x-")}
    assert "armcap" not in yaml.safe_dump(extensions).lower()


@pytest.mark.parametrize("name", MAC_FILES)
def test_the_mac_files_set_it_for_the_api_and_the_worker_only(name: str) -> None:
    services = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]
    setting = {
        service: environment_of(definition)[NAME]
        for service, definition in services.items()
        if NAME in environment_of(definition)
    }
    assert setting == {"api": "0", "worker": "0"}


def test_the_mac_ci_runner_image_sets_it_and_names_the_mac_overlay() -> None:
    """Issue #83: a job on a Mac runner imports `cryptography` (pytest) and starts stacks."""
    lines = code_lines(ROOT / "ops/ci-runner/Dockerfile")
    assert f"ENV {NAME}=0 \\" in lines
    assert "    CI_COMPOSE_OVERLAY=compose.mac.yml" in lines
    workflow = "\n".join(code_lines(ROOT / ".github/workflows/ci.yml"))
    assert '-f "$CI_COMPOSE_OVERLAY"' in workflow
    assert "compose.mac.yml" not in workflow, "the overlay is the runner's choice, not ci.yml's"


def test_environment_of_reads_both_compose_forms() -> None:
    assert environment_of({}) == {}
    assert environment_of({"environment": {NAME: 0, "A": None}}) == {NAME: "0", "A": None}
    assert environment_of({"environment": [f"{NAME}=0", "A", "B="]}) == {
        NAME: "0",
        "A": None,
        "B": "",
    }


def test_code_lines_keeps_everything_but_whole_line_comments(tmp_path: Path) -> None:
    path = tmp_path / "compose.yml"
    path.write_text(
        f"# {NAME} is explained here\nservices:\n  api:\n    environment:\n"
        f"      {NAME}: '0'  # trailing\n      - {NAME}\n",
        encoding="utf-8",
    )
    lines = code_lines(path)
    assert len(lines) == 5
    assert sum("armcap" in line.lower() for line in lines) == 2
