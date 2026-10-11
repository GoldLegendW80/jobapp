"""Shared by the tests of the parser service: the two standalone files of the sandbox loaded by
their path (they are no package and import nothing of the application), and a stand in for the
child, so that the server can be run in the test process on any system."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

API = Path(__file__).resolve().parents[3]
ROOT = API.parent
SANDBOX = API / "app" / "ingestion" / "sandbox"
HERE = Path(__file__).resolve().parent
STAND_IN = (sys.executable, str(HERE / "stand_in_child.py"))

_loaded: dict[str, ModuleType] = {}


def sandbox_module(name: str) -> ModuleType:
    """sandbox/main.py or sandbox/server.py as a module, loaded once."""
    if name not in _loaded:
        spec = importlib.util.spec_from_file_location(
            f"parser_sandbox_{name}", SANDBOX / f"{name}.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded[name] = module
    return _loaded[name]
