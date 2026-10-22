"""Accessible section helpers, with a strict section-local Save designation."""

from dataclasses import dataclass
from urllib.parse import urlsplit

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

    def __init__(self, key: str, label: str) -> None:
        super().__init__()
        self.key, self.label = key, label

    def scope(self, root: base.Node) -> base.Node | None:
        found = next(
            (s for s in sections(root) if s.key == self.key and s.label == self.label), None
        )
        return found.node if found else None


def for_host(host: str):
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        from . import linkedin

        return linkedin
    if host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com"):
        from . import workday

        return workday
    return None


def kind(url: str, stub_base: str | None = None) -> str:
    parsed = urlsplit(url)
    helper = for_host(parsed.hostname or "")
    if helper:
        return helper.KIND
    if stub_base and url.startswith(stub_base.rstrip("/") + "/forms/a18/linkedin"):
        return "linkedin"
    return "generic"
