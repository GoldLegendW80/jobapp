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


def _origin(url: str) -> tuple[str, str] | None:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            return None
        return parts.scheme, parts.netloc
    except ValueError:
        return None


def target(adapter: base.Adapter | None, form_url: str, current_url: str) -> str | None:
    """Only the same known hosted form may select fallback, never an external page or frame."""
    if adapter is None or adapter.name not in KNOWN or adapter.framed:
        return None
    expected = adapter.form_url(form_url)
    if (
        not expected
        or not _origin(current_url)
        or _origin(current_url) != _origin(expected)
        or adapter.form_url(current_url) != expected
    ):
        return None
    return current_url


def binding(rows: Iterable[Any], form_url: str, vendor: str) -> dict[str, Any] | None:
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
        or set(value) != {"version", "vendor", "form_url", "target"}
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
    return value


class Generic(generic.Generic):
    """Generic fallback with field structures bound beside the approved submit designation."""

    def __init__(self, *args: Any, known: base.Adapter | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.known = known
        self.shapes: dict[str, str] = {}
        self.person_shapes: dict[str, str] = {}

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
