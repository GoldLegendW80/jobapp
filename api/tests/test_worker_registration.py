"""The worker imports every module's registration modules, found on disk (issue #279).

A module registers its agents, tools, hooks and writers when one of its files of
REGISTRATION_MODULES is imported. The worker finds those files instead of naming them, so a
module that adds one is known to the worker with no line of app/worker.py to edit; these tests
fail when such a file exists and a path that builds the queues does not import it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import worker
from app.ingestion import agents

API_DIR = Path(__file__).resolve().parents[1]
APP_DIR = API_DIR / "app"


def test_every_registration_file_of_a_package_is_found() -> None:
    # Read with a glob of its own, not with the function under test.
    on_disk = sorted(
        f"app.{path.parent.name}.{path.stem}"
        for name in worker.REGISTRATION_MODULES
        for path in APP_DIR.glob(f"*/{name}.py")
        if (path.parent / "__init__.py").is_file()
    )
    assert sorted(worker.registration_modules()) == on_disk
    # What the worker registered before this was found and not named: the ingestion agents and
    # the engine that chains them.
    assert {"app.ingestion.agents", "app.ingestion.engine"} <= set(on_disk)


def test_found_in_any_package_and_nothing_else(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    files = [
        "__init__.py",
        "agents.py",  # app's own file, not a module package's
        "alpha/__init__.py",
        "alpha/writers.py",
        "alpha/agents.py",
        "alpha/writer_cv.py",  # imported by writers.py, not a registration module
        "beta/__init__.py",
        "beta/jobs/__init__.py",  # a package counts like a file
        "beta/engine.py",
        "gamma/agents.py",  # no __init__.py: not a package of app
        "delta/__init__.py",
        "delta/notes.py",
        "delta/agents.txt",
        "delta/writers/readme.md",  # a folder without __init__.py
        "epsilon/__init__.py",
        "epsilon/sub/__init__.py",
        "epsilon/sub/agents.py",  # one level down: the package is epsilon, not sub
    ]
    for name in files:
        (app_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (app_dir / name).write_text("", encoding="utf-8")
    assert worker.registration_modules(app_dir) == [
        "app.alpha.agents",
        "app.alpha.writers",
        "app.beta.engine",
        "app.beta.jobs",
    ]
    (tmp_path / "empty").mkdir()
    assert worker.registration_modules(tmp_path / "empty") == []


# Every function that builds queues the worker consumes: each must leave every registration
# module imported. A builder added later (documents_queue, D6) is checked once it exists.
BUILDERS = ("queues", "agents_queue", "documents_queue")


@pytest.mark.parametrize("builder", BUILDERS)
def test_each_queue_path_imports_every_registration_module(builder: str) -> None:
    if not hasattr(worker, builder):
        pytest.skip(f"app.worker has no {builder} on this tree")
    # A fresh interpreter: the test process has imported most of app already.
    script = (
        "import json, sys\n"
        "from app import worker\n"
        "names = worker.registration_modules()\n"
        "before = [n for n in names if n in sys.modules]\n"
        f"worker.{builder}()\n"
        "after = [n for n in names if n not in sys.modules]\n"
        "print(json.dumps({'names': names, 'missing': after, 'before': before}))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    result = json.loads(ran.stdout.strip().splitlines()[-1])
    assert result["names"], "no registration module found"
    assert result["missing"] == []
    # The engine is imported by the queues, never by `import app.worker` (its --check must not
    # need the SDK).
    assert "app.ingestion.engine" not in result["before"]


def test_a_fresh_worker_builds_the_tools_of_both_ingestion_agents() -> None:
    """N12 of the review of PR #148: the queue the worker builds leaves the tools of every
    ingestion agent registered, so its runs do not fail on a tool the definition names."""
    script = (
        "import json, uuid\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "from app import worker\n"
        "from app.agents import tools\n"
        "worker.agents_queue()\n"
        "from app.ingestion import agents\n"
        "built = {\n"
        "    one.name: tools.build(sessionmaker(), uuid.uuid4(), one.tools).allowed_names\n"
        "    for one in (agents.EXTRACT, agents.TRANSCRIBE)\n"
        "}\n"
        "print(json.dumps(built))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    built = json.loads(ran.stdout.strip().splitlines()[-1])
    assert sorted(built) == sorted([agents.EXTRACT.name, agents.TRANSCRIBE.name])
    for one in (agents.EXTRACT, agents.TRANSCRIBE):
        assert len(built[one.name]) == len(one.tools) > 0
