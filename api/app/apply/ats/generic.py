"""The generic strategy (SPEC-apply, "Per ATS strategies", the last row; apply A8): any page with a
form that no adapter knows. Review first only: the fill (app/apply/run.py) hands a Full auto
application with such a form to the person (`no_adapter`), as before this strategy existed.

The form agent (app/apply/form_agent.py) reads the page, fills its fields from the profile and
moves through a form of several steps; it never types a value of its own, and it names the
submit element with `designate_submit(ref)`. What is accepted is decided here, in code:

- `check_designation`: the element is a `button` (a link styled as one is a link), not disabled,
  shown in the snapshot (a hidden element has no ref), named as a submit (`SUBMIT_NAMES`, a closed
  list in English and French, never a step's `NEXT_NAMES`), in the same frame as every field of
  the form, inside the smallest part of the page that holds them all (a `form` around the fields
  when the page names one), after the last field, and the only such button there. Anything else
  is refused, and an application without an accepted designation needs the person.
- `marked`: the tool layer's submit detector (browser B5) marks it. Asked without a click: a
  `browser_select_option` is judged before it touches the element, and the detector refuses it
  as `submit` when the element is a submit control by type, a selector of the lease or a wrapper
  of one (the `check` judgement reads no names: a button that is a submit by its name alone is
  refused by a click but not marked here, so it is left to the person); any other answer is
  "not marked".

The submit lease (app/apply/submit.py) refills with no model: `configured` gives the adapter the
approved designation (`designated:submit`, `designated:step:<n>` rows, covered by the approval's
hash), `stages` splits the approved rows by step, and `advance` clicks the recorded Next of a
step through the tool layer, which refuses a submit there. On each step the adapter's submit is
that step's Next button, and on the last one the designated element, so A6's anchors and read
back hold per step. The highlight of the "before" screenshot is drawn here on the JPEG, from the
element's box that browserd reads (`browser_box`).

Declared open points: a frame's origin is not in the snapshot, so a form inside an iframe of
another site is accepted as the page's form (as the framed adapters of `base`); a submit named
otherwise than `SUBMIT_NAMES` ("Go", "Done", an icon only button, another language) is refused
and left to the person; a submit placed above the last field is refused; `addressed` reads a page
that names an AI reader with the words of `AI_READER` only.
"""

import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from PIL import Image, ImageDraw

from app.apply.ats.base import (
    Adapter,
    Discovery,
    Node,
    Skipped,
    Tools,
    discover_fields,
    fold,
    label_of,
    parse_snapshot,
)

SUBMIT_NAMES = frozenset(
    {
        "submit", "submit application", "submit my application", "submit your application",
        "apply", "apply now", "send", "send application", "send my application",
        "complete application", "envoyer", "envoyer ma candidature", "envoyer la candidature",
        "postuler", "je postule", "soumettre", "soumettre ma candidature",
        "soumettre la candidature", "valider ma candidature",
    }
)  # fmt: skip
NEXT_NAMES = frozenset(
    {
        "next", "next step", "continue", "save and continue", "suivant", "etape suivante",
        "continuer",
    }
)  # fmt: skip
STEPS_MAX = 5
SUBMIT_KEY = "designated:submit"
STEP_KEY = "designated:step:"
PROBE = "__designation_probe__"
OUTLINE = (225, 29, 72)
OUTLINE_PX = 4
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")


def button_name(name: str) -> str:
    """A name as the lists compare it: folded, marks and edge symbols (an arrow) off."""
    return _EDGES.sub("", fold(label_of(name)[0]))


def _frame(node: Node) -> Node | None:
    up = node.parent
    while up is not None and up.role != "iframe":
        up = up.parent
    return up


def _ancestors(node: Node) -> list[Node]:
    out, up = [], node.parent
    while up is not None:
        out.append(up)
        up = up.parent
    return out


def _region(nodes: Sequence[Node]) -> Node | None:
    """The smallest subtree that holds every node (a named `form` around them, else their
    lowest common ancestor), never one of the nodes itself."""
    common = _ancestors(nodes[0])
    for other in nodes[1:]:
        mine = {id(n) for n in _ancestors(other)}
        common = [n for n in common if id(n) in mine]
    if not common:
        return None
    form = next((n for n in common if n.role == "form"), None)
    return form or common[0]


def _order(root: Node) -> dict[int, int]:
    return {id(n): i for i, n in enumerate(root.walk())}


def check_designation(root: Node, ref: str) -> str:
    """'' when the element `ref` names may be the form's submit, else why not (a code)."""
    by_ref = {n.ref: n for n in root.walk() if n.ref}
    target = by_ref.get(ref)
    if not ref or target is None:
        return "stale_ref"
    if target.role != "button":
        return "not_a_button"
    if "disabled" in target.attrs:
        return "disabled"
    name = button_name(target.name)
    if name in NEXT_NAMES:
        return "a_step"
    if name not in SUBMIT_NAMES:
        return "not_a_submit_name"
    fields = [by_ref[f.ref] for f in discover_fields(root, SUBMIT_NAMES).fields if f.ref in by_ref]
    if not fields:
        return "no_fields"
    frame = _frame(target)
    if any(_frame(f) is not frame for f in fields):
        return "another_frame"
    region = _region(fields)
    if region is None or region not in _ancestors(target):
        return "outside_the_form"
    order = _order(root)
    if order[id(target)] < max(order[id(f)] for f in fields):
        return "before_a_field"
    rivals = [
        n
        for n in region.walk()
        if n.role == "button" and _frame(n) is frame and button_name(n.name) in SUBMIT_NAMES
    ]
    return "ambiguous" if len(rivals) != 1 else ""


async def marked(tools: Tools, ref: str, name: str = "") -> bool:
    """The tool layer's submit detector marks the element (module docstring); nothing clicked."""
    result = await tools.select_option(ref, [PROBE], name)
    return not result.get("ok") and result.get("error") == "submit"


AI_READER = re.compile(
    r"\b(?:ai (?:assistants?|agents?|models?|tools?|systems?)|artificial intelligence|"
    r"(?:large )?language models?|llms?|chatgpt|claude|gpt-?\d*|automated assistants?|"
    r"if you are an ai|assistants? ia|agents? ia|intelligence artificielle)\b"
)


def addressed(text: str) -> bool:
    """The page names an AI reader (SPEC-apply: a form that addresses AI assistants is reported).
    Reported only: nothing the page asks is done."""
    return AI_READER.search(fold(text)) is not None


# --- the adapter ---------------------------------------------------------------------------------


class Generic(Adapter):
    """Any form, read whole (never framed). Without a designation (the fill), its submits are every
    name of the two lists, so a Next or a submit is never reported as an unknown widget; with one
    (the submit lease), the current step's Next, then the designated element."""

    name = "generic"

    def __init__(
        self,
        stub_base: str | None = None,
        *,
        framed: bool = False,
        submit: str = "",
        steps: Sequence[str] = (),
    ) -> None:
        super().__init__(stub_base, framed=False)
        self.submit, self.steps, self.step = submit, tuple(steps), 0

    @property  # type: ignore[override]
    def submit_names(self) -> frozenset[str]:
        if not self.submit:
            return SUBMIT_NAMES | NEXT_NAMES
        current = self.steps[self.step] if self.step < len(self.steps) else self.submit
        return frozenset({fold(current)})

    def form_url(self, posting_url: str) -> str | None:
        return None

    def recognises(self, snapshot: str) -> bool:
        """Any page that shows a field: the last adapter of `ADAPTERS`."""
        return bool(discover_fields(parse_snapshot(snapshot), self.submit_names).fields)

    def anchor_problem(self, found: Discovery) -> str:
        if not found.fields:
            return "anchor_missing"
        if self.submit and len(found.submit_refs) != 1:
            return "submit_missing" if not found.submit_refs else "submit_ambiguous"
        return ""

    async def read(self, tools: Tools) -> tuple[Discovery | None, str]:
        """The base read, its keys and unknown widgets marked with the step after the first."""
        found, code = await super().read(tools)
        if found is not None and self.step:
            mark = f"s{self.step + 1}/"
            found.fields = [_rekey(f, mark + f.key) for f in found.fields]
            found.skipped = [
                Skipped(f"Step {self.step + 1}: {s.label}", s.role, s.reason) for s in found.skipped
            ]
        return found, code


def _rekey(item: Any, key: str) -> Any:
    return replace(item, key=key)


# --- the submit lease ----------------------------------------------------------------------------


class Row(Protocol):
    key: str
    label: str
    value: Any


def designation_of(rows: Iterable[Row]) -> tuple[str, tuple[str, ...]]:
    """The approved submit's name and the Next names of the steps, in order."""
    submit, steps = "", {}
    for row in rows:
        if row.key == SUBMIT_KEY and isinstance(row.value, str):
            submit = row.value
        elif row.key.startswith(STEP_KEY) and isinstance(row.value, str):
            index = row.key.removeprefix(STEP_KEY)
            if index.isdigit():
                steps[int(index)] = row.value
    ordered = tuple(steps[i] for i in sorted(steps))
    if sorted(steps) != list(range(1, len(steps) + 1)):
        return "", ()
    return submit, ordered


def configured(adapter: Adapter, rows: Sequence[Row]) -> Adapter | None:
    """The adapter for the refill: a Generic carrying the approved designation (None without
    one, which ends the lease before anything is typed); any other adapter as it is."""
    if not isinstance(adapter, Generic):
        return adapter
    submit, steps = designation_of(rows)
    if not submit:
        return None
    return Generic(adapter.stub_base, submit=submit, steps=steps)


def _step_of(row: Row) -> int:
    found = re.match(r"s(\d+)/", row.key)
    if found:
        return int(found.group(1)) - 1
    found = re.match(r"Step (\d+): ", row.label) if row.key.startswith("skipped:") else None
    return int(found.group(1)) - 1 if found else 0


def stages[R: Row](adapter: Adapter, rows: Sequence[R]) -> list[list[R]]:
    """The approved rows per step (one list for any adapter but a Generic with steps); the
    designation's own rows left out."""
    if not isinstance(adapter, Generic):
        return [list(rows)]
    out: list[list[R]] = [[] for _ in range(len(adapter.steps) + 1)]
    for row in rows:
        if row.key.startswith("designated:"):
            continue
        index = _step_of(row)
        if index >= len(out):
            return []
        out[index].append(row)
    return out


async def advance(adapter: Generic, tools: Tools, found: Discovery) -> str:
    """Clicks the current step's Next (the read's one submit ref); '' or why it stopped."""
    if len(found.submit_refs) != 1 or adapter.step >= len(adapter.steps):
        return "form_changed"
    result = await tools.click(found.submit_refs[0], adapter.steps[adapter.step])
    if not result.get("ok"):
        return str(result.get("error") or "failed")
    adapter.step += 1
    return ""


async def recheck(adapter: Adapter, tools: Tools, found: Discovery) -> str:
    """The submit lease's last word on a Generic's element before the click: the check and the
    detector again, on the read whose ref is clicked."""
    if not isinstance(adapter, Generic):
        return ""
    if len(found.submit_refs) != 1 or found.root is None:
        return "submit_missing"
    ref = found.submit_refs[0]
    if code := check_designation(found.root, ref):
        return code
    return "" if await marked(tools, ref, adapter.submit) else "not_marked"


# --- the highlight -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> "Box | None":
        try:
            box = cls(*(float(raw[k]) for k in ("x", "y", "width", "height", "scale")))
        except (KeyError, TypeError, ValueError):
            return None
        sizes = (box.width, box.height, box.scale)
        return box if all(v > 0 for v in sizes) and box.x >= 0 and box.y >= 0 else None


def draw(jpeg: bytes, box: Box) -> bytes | None:
    """The screenshot with the box outlined, or None when the box is not inside it."""
    try:
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except (OSError, ValueError):
        return None
    left, top = box.x * box.scale, box.y * box.scale
    right, bottom = left + box.width * box.scale, top + box.height * box.scale
    if right > image.width or bottom > image.height:
        return None
    pad = OUTLINE_PX + 2
    outline = (max(0, left - pad), max(0, top - pad))
    corner = (min(image.width - 1, right + pad), min(image.height - 1, bottom + pad))
    ImageDraw.Draw(image).rectangle((outline, corner), outline=OUTLINE, width=OUTLINE_PX)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


async def highlighted(tools: Any, ref: str, shot: bytes | None) -> bytes | None:
    """The "before" screenshot with the designated element outlined; None when it cannot be."""
    box_of = getattr(tools, "box", None)
    if shot is None or box_of is None:
        return None
    raw = await box_of(ref, "Submit")
    box = Box.of(raw) if raw.get("ok") else None
    return draw(shot, box) if box is not None else None
