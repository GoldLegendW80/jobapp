"""Bounded opening of known profile sections, using exact accessible names only.

These descriptors never grant Save. Their only clickable controls are the site's known
Edit or navigation entry points, and a dialog's own Close or Cancel. Save stays in the gate.
"""

from dataclasses import dataclass
from typing import Any

from app.apply.answers import option_key
from app.apply.ats import base


@dataclass(frozen=True)
class Entry:
    key: str
    label: str
    open_names: tuple[str, ...]
    aliases: tuple[str, ...]
    link: bool = False


def named(text: str) -> str:
    return option_key(text)


def active(root: base.Node, entry: Entry) -> base.Node | None:
    aliases = {named(s) for s in (*entry.aliases, entry.label, *entry.open_names)}
    candidates = [
        n
        for n in root.walk()
        if n.role in ("dialog", "form", "region")
        and named(n.name) in aliases
        and base.discover_fields(n, frozenset()).fields
    ]
    # An unnamed form inside a named dialog belongs to that dialog. A second dialog is ambiguous.
    dialogs = [n for n in candidates if n.role == "dialog"]
    if dialogs:
        return dialogs[0] if len(dialogs) == 1 else None
    return candidates[0] if len(candidates) == 1 else None


def available(root: base.Node, entries: tuple[Entry, ...]) -> list[Entry]:
    out = []
    for entry in entries:
        names = {named(s) for s in entry.open_names}
        controls = [
            n
            for n in root.walk()
            if n.role in (("button", "link") if entry.link else ("button",))
            and named(n.name) in names
            and n.ref
            and "disabled" not in n.attrs
        ]
        if active(root, entry) is not None or len(controls) == 1:
            out.append(entry)
    return out


async def open_entry(tools: Any, entry: Entry) -> base.Node | None:
    root = base.parse_snapshot(str((await tools.snapshot()).get("snapshot") or ""))
    current = active(root, entry)
    if current is not None:
        return current
    names = {named(s) for s in entry.open_names}
    controls = [
        n
        for n in root.walk()
        if n.role in (("button", "link") if entry.link else ("button",))
        and named(n.name) in names
        and n.ref
        and "disabled" not in n.attrs
    ]
    if len(controls) != 1:
        return None
    control = controls[0]
    if not (await tools.click(control.ref, control.name)).get("ok"):
        return None
    for _ in range(3):
        shot = await tools.snapshot()
        if not shot.get("ok") or shot.get("cut"):
            return None
        root = base.parse_snapshot(str(shot.get("snapshot") or ""))
        if found := active(root, entry):
            return found
    return None


async def close_entry(tools: Any, entry: Entry) -> bool:
    root = base.parse_snapshot(str((await tools.snapshot()).get("snapshot") or ""))
    current = active(root, entry)
    if current is None or current.role != "dialog":
        return True
    controls = [
        n
        for n in current.walk()
        if n.role == "button"
        and named(n.name) in ("close", "cancel", "fermer", "annuler")
        and n.ref
    ]
    if len(controls) != 1:
        return False
    return bool((await tools.click(controls[0].ref, controls[0].name)).get("ok"))
