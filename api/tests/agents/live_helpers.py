"""Helpers of the live text tests (issue #33): a store that records what the worker wrote, a
browser stand-in applying the stream's events by the rules of web/src/features/agents/
live-text.ts, and a parser of the stream's bytes."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.live import MemoryLiveStore

Parsed = tuple[str, int | None, dict[str, Any]]


class RecordingStore(MemoryLiveStore):
    """A MemoryLiveStore that keeps every write, in order: ("open", block, after),
    ("append", block, text), ("clear", block)."""

    def __init__(self) -> None:
        super().__init__()
        self.ops: list[tuple[Any, ...]] = []

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        await super().open(run_id, block, after, previous)
        self.ops.append(("open", block, after))

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        await super().append(run_id, block, text)
        self.ops.append(("append", block, text))

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        await super().clear(run_id, block)
        self.ops.append(("clear", block))

    def written(self) -> dict[int, str]:
        """The text appended to each block, in order."""
        found: dict[int, str] = {}
        for op in self.ops:
            if op[0] == "open":
                found.setdefault(op[1], "")
            elif op[0] == "append":
                found[op[1]] = found.get(op[1], "") + op[2]
        return found

    def everything(self) -> str:
        return json.dumps(self.ops, ensure_ascii=False)


def parse(chunks: list[bytes]) -> list[Parsed]:
    """(event name, id or None, data) of every event in a stream's chunks."""
    found: list[Parsed] = []
    for chunk in chunks:
        name: str | None = None
        event_id: int | None = None
        for line in chunk.decode().split("\n"):
            if line.startswith("id: "):
                event_id = int(line.removeprefix("id: "))
            elif line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: ") and name:
                found.append((name, event_id, json.loads(line.removeprefix("data: "))))
    return found


def utf16(text: str) -> bytes:
    return text.encode("utf-16-le", "surrogatepass")


@dataclass
class Browser:
    """What a browser tab holds of one run: its steps, and the live text of the block being
    written. The rules are those of the web reducer (live-text.ts), in UTF-16 code units as a
    JavaScript string counts them."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    live: dict[str, Any] | None = None
    ended: bool = False
    # Every live text shown, in order, for the prefix assertions.
    shown: list[tuple[int, str]] = field(default_factory=list)

    @property
    def last_id(self) -> int:
        return self.steps[-1]["seq"] if self.steps else 0

    def text_seq(self) -> int:
        return max((s["seq"] for s in self.steps if s["kind"] == "text"), default=0)

    def take(self, event: Parsed) -> None:
        name, event_id, data = event
        if name == "step":
            if data["seq"] <= self.last_id:
                return
            assert event_id == data["seq"]
            self.steps.append(data)
            if (
                self.live is not None
                and data["kind"] == "text"
                and data["seq"] > self.live["after"]
            ):
                self.live = None
            if data["kind"] == "status" and data["data"].get("status") in (
                "done",
                "failed",
                "cancelled",
            ):
                self.live = None
                self.ended = True
        elif name == "partial":
            assert event_id is None, "a partial must not move Last-Event-ID"
            self.partial(data)

    def partial(self, data: dict[str, Any]) -> None:
        if self.ended or data["after"] < self.text_seq():
            return
        current = self.live
        if current is not None and data["block"] < current["block"]:
            return
        base = (
            current["units"] if current is not None and current["block"] == data["block"] else b""
        )
        offset = data["offset"] * 2
        if offset > len(base):
            return
        merged = base[:offset] + utf16(data["text"])
        if base.startswith(merged):
            merged = base
        self.live = {"block": data["block"], "after": data["after"], "units": merged}
        self.shown.append((data["block"], merged.decode("utf-16-le", "surrogatepass")))

    def live_text(self) -> str | None:
        if self.live is None:
            return None
        return str(self.live["units"].decode("utf-16-le", "surrogatepass"))

    def texts(self) -> list[str]:
        return [s["data"]["text"] for s in self.steps if s["kind"] == "text"]
