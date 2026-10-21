"""The API and worker startup leave no logging route past the credential filter (#155)."""

import io
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

API_DIR = Path(__file__).resolve().parents[1]
ROOT = API_DIR.parent


def _assert_topology(canary: str) -> list[str]:
    from app.logging_config import JsonFormatter, SafeStreamHandler, SecretFilter, hidden

    root = logging.getLogger()
    assert root.handlers, "root has no handler"
    for handler in root.handlers:
        assert isinstance(handler, SafeStreamHandler), type(handler).__name__
        assert isinstance(handler.formatter, JsonFormatter)
        assert any(isinstance(item, SecretFilter) for item in handler.filters)
    loggers = [root] + [
        logger for logger in root.manager.loggerDict.values() if isinstance(logger, logging.Logger)
    ]
    for logger in loggers[1:]:
        assert not logger.handlers, f"{logger.name} owns a handler"
        assert logger.propagate, f"{logger.name} stops propagation"
        parent = logger.parent
        while parent is not root:
            assert parent is not None, f"{logger.name} does not reach root"
            assert parent.propagate, f"{parent.name} stops {logger.name}"
            parent = parent.parent
    enabled = [logger for logger in loggers if not logger.disabled]
    with hidden(canary):
        for logger in enabled:
            logger.critical("topology canary %s", canary)
    return sorted(logger.name for logger in enabled)


def _probe(target: str, canary: str) -> None:
    """Called in a fresh interpreter, without pytest's capture and logging plugins."""
    if target == "api":
        from starlette.testclient import TestClient
        from uvicorn import Config

        # Uvicorn configures its own handlers before loading the app factory.
        config = Config("app.main:create_app", factory=True, access_log=False)
        config.load()
        with TestClient(config.loaded_app):
            names = _assert_topology(canary)
        assert {"app", "uvicorn.error", "sqlalchemy.engine.Engine"} <= set(names)
    else:
        from app import worker

        async def started(settings, specs):
            nonlocal names
            context = worker.make_context(settings)
            try:
                # Real queue registrations and arq constructors, without opening Redis.
                workers = [worker.build_worker(spec, settings, context) for spec in specs]
                assert workers
                names = _assert_topology(canary)
            finally:
                context["engine"].dispose()
            return 0

        with patch.object(worker, "serve", started):
            assert worker.main([]) == 0
        assert {"app.worker", "arq.worker", "sqlalchemy.engine.Engine"} <= set(names)
    print(json.dumps({"checked": names}))


@pytest.mark.parametrize("target", ["api", "worker"])
def test_startup_routes_the_logger_tree_through_filtered_root_handlers(target: str) -> None:
    canary = f"topology-canary-{uuid.uuid4().hex}"
    ran = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tests.test_logging_config import _probe; _probe(*sys.argv[1:])",
            target,
            canary,
        ],
        cwd=API_DIR,
        # No developer settings or credentials enter the subprocess.
        env={
            "PATH": os.defpath,
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://e2e:e2e@127.0.0.1:1/e2e",
            "LOG_LEVEL": "debug",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert canary not in ran.stdout + ran.stderr
    assert ran.returncode == 0, ran.stderr[-4000:]
    assert ran.stderr == ""
    lines = [json.loads(line) for line in ran.stdout.splitlines()]
    names = lines[-1]["checked"]
    emitted = [line for line in lines[:-1] if line["msg"] == "topology canary [redacted]"]
    assert sorted(line["logger"] for line in emitted) == names


@pytest.mark.parametrize("broken", ["own handler", "own handler stops", "stops", "root filter"])
def test_the_topology_check_rejects_routes_that_can_bypass_scrubbing(broken: str) -> None:
    from app.logging_config import JsonFormatter, SafeStreamHandler, SecretFilter

    # A separate hierarchy keeps the fault probes away from pytest's own loggers.
    root = logging.RootLogger(logging.DEBUG)
    root.manager = logging.Manager(root)
    logger = root.manager.getLogger("third.party")
    handler = SafeStreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretFilter())
    root.addHandler(handler)
    if broken.startswith("own handler"):
        logger.addHandler(logging.StreamHandler(io.StringIO()))
    if "stops" in broken:
        logger.propagate = False
    if broken == "root filter":
        # Checking only the first root handler would miss this one.
        unfiltered = SafeStreamHandler(io.StringIO())
        unfiltered.setFormatter(JsonFormatter())
        root.addHandler(unfiltered)
    with patch.object(logging, "getLogger", return_value=root), pytest.raises(AssertionError):
        _assert_topology(uuid.uuid4().hex)


@pytest.mark.skipif(
    not (ROOT / "compose.yml").is_file() and not os.environ.get("CI"),
    reason="the API dev container mounts no repository root",
)
def test_compose_starts_the_worker_without_the_arq_cli_logging_handler() -> None:
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["worker"]["command"] == ["python", "-m", "app.worker"]
    for name in ("compose.dev.yml", "compose.mac.yml"):
        overlay = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        command = overlay["services"].get("worker", {}).get("command")
        assert command is None or command == ["python", "-m", "app.worker"]
