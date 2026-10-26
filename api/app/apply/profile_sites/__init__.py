"""Accessible section helpers, with a strict section-local Save designation."""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from app.apply import guardrails
from app.apply.ats import base, generic

SAVE_NAMES = frozenset(
    {
        "save",
        "save changes",
        "save profile",
        "update",
        "update profile",
        "enregistrer",
        "enregistrer les modifications",
        "mettre a jour",
        "sauvegarder",
    }
)
AUTOSAVE_TEXT = (
    "changes are saved automatically",
    "saves as you type",
    "enregistrement automatique",
    "modifications sont enregistrees automatiquement",
)


@dataclass
class Section:
    key: str
    label: str
    node: base.Node
    fields: list[base.Field]


def sections(root: base.Node) -> list[Section]:
    candidates = [
        n for n in root.walk() if n.role == "form" and base.discover_fields(n, SAVE_NAMES).fields
    ]
    if not candidates:
        candidates = [
            n
            for n in root.walk()
            if n.role in ("region", "group")
            and n.name
            and base.discover_fields(n, SAVE_NAMES).fields
        ]
        candidates = [
            n for n in candidates if not any(c is not n and c in list(n.walk()) for c in candidates)
        ]
    if not candidates and base.discover_fields(root, SAVE_NAMES).fields:
        candidates = [root]
    result = []
    for i, node in enumerate(candidates):
        label = node.name or next(
            (n.name for n in node.walk() if n.role == "heading" and n.name), "Profile"
        )
        result.append(
            Section(str(i + 1), label, node, base.discover_fields(node, SAVE_NAMES).fields)
        )
    return result


def save(section: Section, ref: str = "") -> tuple[base.Node | None, str]:
    candidates = [
        n
        for n in section.node.walk()
        if n.role == "button"
        and generic.button_name(n.name) in SAVE_NAMES
        and "disabled" not in n.attrs
        and n.ref
    ]
    if len(candidates) != 1:
        return None, "save_ambiguous" if candidates else "save_missing"
    target = candidates[0]
    if ref and ref != target.ref:
        return None, "outside_section"
    by_ref = {n.ref: n for n in section.node.walk() if n.ref}
    if any(
        generic._frame(by_ref[f.ref]) is not generic._frame(target)
        for f in section.fields
        if f.ref in by_ref
    ):
        return None, "another_frame"
    return target, ""


def autosave(root: base.Node) -> bool:
    text = base.fold(" ".join(root.words()))
    return any(s in text for s in AUTOSAVE_TEXT) or any(not save(s)[0] for s in sections(root))


class SectionAdapter(base.Adapter):
    submit_names = SAVE_NAMES

    def __init__(self, key: str, label: str, kind: str = "generic") -> None:
        super().__init__()
        self.key, self.label, self.kind = key, label, kind
        self.step = 0

    def section(self, root: base.Node) -> Section | None:
        helper = for_kind(self.kind)
        if helper:
            from .known import active

            entry = next((e for e in helper.ENTRIES if e.key == self.key), None)
            node = active(root, entry) if entry else None
            if node is not None:
                return Section(
                    entry.key, entry.label, node, base.discover_fields(node, SAVE_NAMES).fields
                )
        return next(
            (s for s in sections(root) if s.key == self.key and s.label == self.label), None
        )

    def scope(self, root: base.Node) -> base.Node | None:
        found = self.section(root)
        return found.node if found else None


def for_kind(kind: str):
    if kind == "linkedin":
        from . import linkedin

        return linkedin
    if kind == "workday":
        from . import workday

        return workday
    return None


async def catalogue(tools, kind: str) -> list[Section]:
    root = base.parse_snapshot(str((await tools.snapshot()).get("snapshot") or ""))
    helper = for_kind(kind)
    if helper:
        from .known import available

        entries = available(root, helper.ENTRIES)
        if entries:
            return [Section(e.key, e.label, root, []) for e in entries]
    return sections(root)


async def open_section(tools, kind: str, key: str, label: str) -> Section | None:
    helper = for_kind(kind)
    if helper:
        from .known import open_entry

        entry = next((e for e in helper.ENTRIES if e.key == key), None)
        if entry:
            node = await open_entry(tools, entry)
            return (
                Section(key, label, node, base.discover_fields(node, SAVE_NAMES).fields)
                if node
                else None
            )
    root = base.parse_snapshot(str((await tools.snapshot()).get("snapshot") or ""))
    return next((s for s in sections(root) if s.key == key and s.label == label), None)


async def close_section(tools, kind: str, key: str) -> bool:
    helper = for_kind(kind)
    if helper:
        from .known import close_entry

        entry = next((e for e in helper.ENTRIES if e.key == key), None)
        return await close_entry(tools, entry) if entry else True
    return True


def normal_host(hostname: str) -> str | None:
    """The host Chrome opens for this spelling (guardrails.browser_host: IDNA 2003, the dot
    variants, percent escapes, case, a trailing dot), or None when it does not encode or still
    holds a character no host has: the caller refuses the address (fail closed)."""
    try:
        unquote(hostname).encode("idna")
    except UnicodeError:
        return None
    host = guardrails.browser_host(hostname)
    if not host or any(c in host for c in "/\\?#@%[] ") or (":" in host and ":" not in hostname):
        return None
    return host


def for_host(host: str):
    host = normal_host(host) or ""
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        from . import linkedin

        return linkedin
    if host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com"):
        from . import workday

        return workday
    return None


def kind(url: str, stub_base: str | None = None) -> str:
    try:
        hostname = urlsplit(url.strip()).hostname or ""
    except ValueError:
        hostname = ""
    helper = for_host(hostname)
    if helper:
        return helper.KIND
    if stub_base and url.startswith(stub_base.rstrip("/") + "/forms/a18/linkedin"):
        return "linkedin"
    if stub_base and url.startswith(stub_base.rstrip("/") + "/forms/a18/workday"):
        return "workday"
    return "generic"
