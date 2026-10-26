"""The credential split of the mail profile, read from `docker compose config` (SPEC-mail.md,
"Its environment is a list, never the stack's `.env`").

Throwaway `.env` (the tracked template plus planted secrets), `.env.mailsend` and `.env.redis` in
a temporary project folder. The worker never holds MAIL_SEND_KEY; `gmailmcp` holds only the
variables it lists, none of the stack's secrets, and is never on `default`.
"""

import json
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The api folder alone is mounted in the dev container (compose.dev.yml): no compose.yml there.
pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.mail.yml").is_file(), reason="the repository root is not mounted here"
)

# What `gmailmcp` may receive, with or without the sending overlay (the Google endpoints point at
# the fake in a test stack and are refused in production). Anything else fails.
PROVIDER_ALLOWED = {
    "APP_ENV",
    "GMAIL_MCP_URL",
    "GMAIL_MCP_REDIS_PASSWORD_FILE",
    "MAIL_SEND_KEY",
    "GOOGLE_API_BASE_URL",
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_BASE_URL",
}
STACK_SECRETS = (
    "DATABASE_URL",
    "CREDENTIALS_KEY",
    "CREDENTIALS_KEY_PREVIOUS",
    "SECRET_KEY",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "BROWSER_CONTROL_SECRET",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "POSTGRES_PASSWORD",
    "TS_AUTHKEY",
)


def planted() -> str:
    return "plant" + secrets.token_hex(12)


def config(tmp_path: Path, files: list[str], *, mailsend: bool) -> tuple[dict[str, Any], dict]:
    """The merged config, and the planted values by variable name."""
    values = {name: planted() for name in STACK_SECRETS}
    values["MAIL_SEND_KEY"] = planted()
    values["REDIS_URL"] = f"redis://default:{planted()}@redis:6379/0"
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    stack = "".join(f"{name}={values[name]}\n" for name in STACK_SECRETS)
    (tmp_path / ".env").write_text(template + "\n" + stack, encoding="utf-8")
    if mailsend:
        (tmp_path / ".env.mailsend").write_text(
            f"MAIL_SEND_KEY={values['MAIL_SEND_KEY']}\n", encoding="utf-8"
        )
        (tmp_path / ".env.redis").write_text(f"REDIS_URL={values['REDIS_URL']}\n", encoding="utf-8")
    docker = shutil.which("docker")
    assert docker, "docker compose is needed to read the merged config"
    args = [docker, "compose", "--project-directory", str(tmp_path)]
    for name in files:
        args += ["-f", str(ROOT / name)]
    args += ["--profile", "mail", "--profile", "browser", "config", "--format", "json"]
    # MAIL_SEND_KEY in the caller's environment too: a `${MAIL_SEND_KEY}` interpolated into a
    # service would show in the result.
    env = {
        "PATH": str(Path(docker).parent) + ":/usr/bin:/bin",
        "MAIL_SEND_KEY": values["MAIL_SEND_KEY"],
    }
    done = subprocess.run(args, capture_output=True, text=True, env=env, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout), values


def environment(merged: dict[str, Any], service: str) -> dict[str, str]:
    return merged["services"][service].get("environment") or {}


def holds_nothing_of(merged: dict[str, Any], service: str, values: dict, names: tuple[str, ...]):
    text = json.dumps(merged["services"][service])
    for name in names:
        assert values[name] not in text, f"{service} holds the value of {name}"


STACKS = [
    pytest.param(["compose.yml"], False, id="base"),
    pytest.param(["compose.yml", "compose.mail.yml"], True, id="mail-overlay"),
    pytest.param(
        ["compose.yml", "tests/gmail-stub.compose.yml", "tests/mail-send.compose.yml"],
        False,
        id="synthetic-send",
    ),
]


@pytest.mark.parametrize(("files", "mailsend"), STACKS)
def test_worker_never_holds_the_signing_key(tmp_path: Path, files: list[str], mailsend: bool):
    merged, values = config(tmp_path, files, mailsend=mailsend)
    worker = merged["services"]["worker"]
    assert "MAIL_SEND_KEY" not in environment(merged, "worker")
    holds_nothing_of(merged, "worker", values, ("MAIL_SEND_KEY",))
    assert ".env.mailsend" not in json.dumps(worker.get("env_file") or [])


@pytest.mark.parametrize(("files", "mailsend"), STACKS)
def test_provider_holds_only_its_list(tmp_path: Path, files: list[str], mailsend: bool):
    merged, values = config(tmp_path, files, mailsend=mailsend)
    provider = environment(merged, "gmailmcp")
    assert set(provider) <= PROVIDER_ALLOWED, sorted(set(provider) - PROVIDER_ALLOWED)
    holds_nothing_of(merged, "gmailmcp", values, (*STACK_SECRETS, "REDIS_URL"))
    networks = set(merged["services"]["gmailmcp"]["networks"])
    if "tests/gmail-stub.compose.yml" in files:
        # The fake stack reaches gmailstub over `default` today (a follow up of #681 moves the
        # stub onto gmail_egress, as SPEC-mail says); a deployed stack never does.
        assert networks == {"default", "mailmcp", "gmail_egress"}
    else:
        assert networks == {"mailmcp", "gmail_egress"}


def test_overlay_gives_each_service_its_own_file(tmp_path: Path):
    merged, values = config(tmp_path, ["compose.yml", "compose.mail.yml"], mailsend=True)
    # The api and the provider get the signing key from .env.mailsend; api and worker get the
    # Redis credential of .env.redis; the provider never gets .env.redis nor the stack's .env.
    assert environment(merged, "api")["MAIL_SEND_KEY"] == values["MAIL_SEND_KEY"]
    assert environment(merged, "gmailmcp")["MAIL_SEND_KEY"] == values["MAIL_SEND_KEY"]
    assert environment(merged, "api")["REDIS_URL"] == values["REDIS_URL"]
    assert environment(merged, "worker")["REDIS_URL"] == values["REDIS_URL"]
    assert "REDIS_URL" not in environment(merged, "gmailmcp")
    assert set(merged["services"]["redis"]["networks"]) == {"default", "mailmcp"}
    # Without the overlay the provider has no key at all: it refuses every send.
    base, _ = config(tmp_path, ["compose.yml"], mailsend=False)
    assert "MAIL_SEND_KEY" not in environment(base, "gmailmcp")
    assert "MAIL_SEND_KEY" not in environment(base, "api")
