"""D6: `routers.load` against a stand-in package: a router file that does not exist yet is
skipped, one that exists and fails to import fails the start, a name that is not a router too."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path

import pytest
from fastapi import APIRouter

from app.documents import routers


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"standin_{uuid.uuid4().hex}"
    folder = tmp_path / name
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    (folder / "first.py").write_text("from fastapi import APIRouter\nrouter = APIRouter()\n")
    (folder / "second.py").write_text("from fastapi import APIRouter\nrouter = APIRouter()\n")
    (folder / "broken.py").write_text("import a_module_that_does_not_exist\nrouter = None\n")
    (folder / "plain.py").write_text("router = object()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return name


def test_a_router_file_that_does_not_exist_yet_is_skipped(package: str) -> None:
    names = (("second", "router"), ("missing", "router"), ("first", "router"))
    loaded = routers.load(names, package)
    assert [type(found) for found in loaded] == [APIRouter, APIRouter]
    second = importlib.import_module(f"{package}.second").router
    first = importlib.import_module(f"{package}.first").router
    assert loaded == (second, first)  # in the order of the list


def test_a_router_file_that_fails_to_import_fails_the_start(package: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        routers.load((("first", "router"), ("broken", "router")), package)


def test_a_name_that_is_not_a_router_fails_the_start(package: str) -> None:
    with pytest.raises(TypeError, match=r"plain\.router is not an APIRouter"):
        routers.load((("plain", "router"),), package)
    with pytest.raises(AttributeError):
        routers.load((("first", "no_such_router"),), package)


def test_the_app_list_loads_every_router_that_exists() -> None:
    loaded = routers.load()
    assert loaded
    assert all(isinstance(found, APIRouter) for found in loaded)
