"""Code-owned choice of generic replay after a known ATS changes its form.

The reserved answer participates in the ordinary approval hash. Composite answer ownership,
the spent authorisation and the final document checks bind it to this application and person.
It is never a model tool argument or an editable answer.
"""

import json
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit

from app.apply.ats import base, generic

KEY = "designated:strategy"
KNOWN = frozenset({"ashby", "greenhouse", "lever", "workday", "google_careers", "vie", "linkedin"})


def canonical_url(url: str) -> str | None:
    """Normalise host case and default port, rejecting malformed authorities."""
    try:
        parts = urlsplit(url)
        port = parts.port  # validates numeric form and range before any adapter reads it
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or parts.netloc.endswith(":")
            or port == 0
            or any(c.isspace() or ord(c) < 32 for c in url)
        ):
            return None
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            authority += f":{port}"
        return parts._replace(netloc=authority).geturl()
    except ValueError:
        return None


def _origin(url: str) -> tuple[str, str] | None:
    canonical = canonical_url(url)
    if canonical is None:
        return None
    parts = urlsplit(canonical)
    return parts.scheme, parts.netloc


def target(adapter: base.Adapter | None, form_url: str, current_url: str) -> str | None:
    """The same hosted form or exact company page, never a page-selected new origin."""
    original, current = canonical_url(form_url), canonical_url(current_url)
    if adapter is None or adapter.name not in KNOWN or original is None or current is None:
        return None
    if adapter.framed and adapter.form_url(original) is None:
        return current if current == original else None
    expected = adapter.form_url(original)
    if (
        not expected
        or _origin(current) != _origin(expected)
        or adapter.form_url(current) != expected
    ):
        return None
    return current


def binding(
    rows: Iterable[Any], form_url: str, vendor: str, *, allow_frame: bool = False
) -> dict[str, Any] | None:
    """Validate the hash-bound strategy. Frames require explicit reviewed-replay opt in."""
    found = [r for r in rows if getattr(r, "field_key", getattr(r, "key", "")) == KEY]
    if len(found) != 1:
        return None
    row = found[0]
    value = row.value
    if (
        str(getattr(row.source, "value", row.source)) != "identity"
        or row.kind != "designated"
        or row.required
        or not isinstance(value, dict)
        or set(value)
        not in (
            {"version", "vendor", "form_url", "target"},
            {"version", "vendor", "form_url", "target", "frame"},
        )
        or type(value["version"]) is not int
        or value["version"] != 1
        or vendor not in KNOWN
        or value["vendor"] != vendor
        or value["form_url"] != form_url
        or not isinstance(value["target"], str)
        or not _origin(value["target"])
        or _origin(value["target"]) != _origin(form_url)
    ):
        return None
    if "frame" in value and (
        not allow_frame or type(value["frame"]) is not int or not 0 <= value["frame"] < 100
    ):
        return None
    return value


class Generic(generic.Generic):
    """Generic fallback with field structures bound beside the approved submit designation."""

    def __init__(
        self, *args: Any, known: base.Adapter | None = None, frame: int | None = None, **kwargs: Any
    ):
        super().__init__(*args, **kwargs)
        self.known = known
        self.shapes: dict[str, str] = {}
        self.person_shapes: dict[str, str] = {}
        self.frame = frame

    def scope(self, root: base.Node) -> base.Node | None:
        if self.frame is None:
            return root
        frames = [node for node in root.walk() if node.role == "iframe"]
        return frames[self.frame] if self.frame < len(frames) else None

    def pending_at(self, root: base.Node) -> str:
        return (self.known.pending_at(root) if self.known else "") or super().pending_at(root)

    async def read(self, tools: base.Tools):
        found, code = await super().read(tools)
        if found is not None and not code:
            shape = json.dumps(
                [[f.key, f.label, f.kind, list(f.options), f.required] for f in found.fields],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            self.shapes[str(self.step)] = shape
            expected = self.person_shapes.get(str(self.step))
            if expected is not None and expected != shape:
                return None, "form_changed"
        return found, code

    def approved(self, rows: Sequence[generic.Row]) -> None:
        self.person_shapes = {
            r.key.removeprefix("designated:shape:"): r.value
            for r in rows
            if r.key.startswith("designated:shape:")
        }
