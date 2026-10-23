"""What the O7 tests share: the fake feeds server (tests/feeds_stub.py) run in process, a feeds
runner pointed at it, and a search section with feeds. Every address is the server's own on a
loopback port; nothing here reads Remote OK, We Work Remotely or Hacker News."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.opportunities import sources
from tests.jobs.sweep_helpers import search
from tests.opportunities.conftest import OWNER_RULE

STUB_PATH = Path(__file__).resolve().parents[3] / "tests" / "feeds_stub.py"


def load_stub() -> ModuleType:
    spec = importlib.util.spec_from_file_location("feeds_stub", STUB_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stub_module = load_stub()


@pytest.fixture
def feeds_stub() -> Iterator[Any]:
    with stub_module.FeedsStub(slow_s=2.0) as server:
        yield server


async def no_sleep(_: float) -> None:
    return None


def runner(
    server: Any, variant: str = "ok", *, max_comments: int = 500, **client: Any
) -> sources.FeedRunner:
    """A feeds hook reading `variant` of the fake server, without waits between retries."""
    options: dict[str, Any] = {"timeout_s": 1.0, "sleep": no_sleep, **client}

    def factory() -> sources.FeedClient:
        return sources.FeedClient(stub_base_url=server.base(variant), **options)

    return sources.FeedRunner(factory, sources.readers(max_comments))


def feed(kind: str, enabled: bool = True, terms: list[str] | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "terms": {"terms": terms or [], "advanced_regex": False},
        "enabled": enabled,
    }


def feeds_search(*feeds: dict[str, Any], boards: list[dict[str, Any]] | None = None, **extra: Any):
    """A search section with these feeds, the owner's never apply rule, and no board unless
    given."""
    values: dict[str, Any] = {"never_apply": [OWNER_RULE], "feeds": list(feeds)}
    values.update(extra)
    return search(*(boards or []), **values)


def stats(server: Any) -> dict[str, int]:
    with urllib.request.urlopen(f"{server.url}/_stats", timeout=5) as answer:  # noqa: S310
        counts: dict[str, int] = json.loads(answer.read())
        return counts


def requests_to(server: Any, part: str) -> int:
    return sum(count for path, count in stats(server).items() if part in path)
