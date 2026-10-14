"""The adapter contract of the apply module's ATS strategies (SPEC-apply, "Per ATS strategies"),
and what every adapter shares: reading the page, finding the fields, filling one field.

An adapter sees the form the way the browser's tool layer shows it (browser B5): Playwright's AI
snapshot, a tree of accessible roles and names with element refs (`[ref=e8]`, `[ref=f1e8]` inside
a frame). It never reads a class, an id or a hidden input, so what works on the fake ATS forms
works on a page whose names and ids change (real Greenhouse names its questions
`question_<digits>`; real Ashby posts JSON and has no hidden input behind a yes or no group).
Fields are found by label and role; a value is checked on the page after it is set (a pressed
button, a ticked box, the option shown, the typed text read back).

Reading is a closed world that fails closed:
- A control is a field only in the shapes below. Anything else (a radio group, a slider, a list
  box) is reported `unsupported_widget` and left to the agent or the person; an unrecognised
  button is never clicked.
  * `textbox`, `searchbox`, `spinbutton`: a text field named by its accessible name;
  * `combobox` with `option` children: a native select; without: a react-select or a typeahead,
    whose options are read by opening it once (a typeahead shows none until something is typed);
  * `checkbox`: a box, its label is its name;
  * two buttons named exactly Yes and No (or Oui and Non) side by side: a yes or no group, its
    label the text of the group around them;
  * a button named as the label of the group it stands in (Chrome's file input), or named as an
    upload action ("Upload File", "Attach") in a group with a label: a file field. A file field
    is uploaded to only when the resolver answers it with a document, so a misread button is
    never clicked by an upload.
- The page is the adapter's form only when its anchors are there: exactly one submit button with
  one of the adapter's names, and every standard field it knows. A missing anchor is
  `form_changed` before anything is typed; a field that is gone when its turn comes is
  `form_changed` too, and nothing more is typed.
- A label is the accessible name with the required marks taken off (a trailing `*`, "(required)",
  "(obligatoire)"); a name holding only marks is no label.

Nothing here clicks a submit element: the adapter has no such call (the gate's `gate_click`,
apply A6, is the only one). No answer value, typed text or page text is logged: codes and counts.

Declared open points: a `textbox` cannot be told from a multi line one in the snapshot, so every
text field is resolved as `text` (a newline typed into a one line field is refused by the tool
layer and reported `failed`); a yes or no group whose buttons do not expose a pressed state is
`unverified`, never `filled` (apply A6 reads values back before the click); a box the page ticked
by itself and the resolver leaves stays as the page set it, reported with its state.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from app.apply import answers

logger = logging.getLogger(__name__)

FieldKind = Literal["text", "select", "combobox", "yes_no", "checkbox", "file"]
Status = Literal["filled", "unverified", "failed", "left"]
OPTION_WAITS = 6  # snapshots taken while a typeahead's options load, 0.5 s apart
OPTION_WAIT_S = 0.5
NAME_MAX = 500  # characters of a label or option kept


class Tools(Protocol):
    """What an adapter calls: `app.browser.tools.LeaseTools` (B5), bound to one lease."""

    async def snapshot(self) -> dict[str, Any]: ...
    async def navigate(self, url: str) -> dict[str, Any]: ...
    async def click(self, ref: str, element: str = "") -> dict[str, Any]: ...
    async def type(
        self, ref: str, text: str, element: str = "", submit: bool = False
    ) -> dict[str, Any]: ...
    async def select_option(
        self, ref: str, values: list[str], element: str = ""
    ) -> dict[str, Any]: ...
    async def check(self, ref: str, checked: bool = True, element: str = "") -> dict[str, Any]: ...
    async def upload(self, ref: str, names: list[str], element: str = "") -> dict[str, Any]: ...
    async def press_key(self, key: str) -> dict[str, Any]: ...
    async def wait_for(
        self, *, time: float | None = None, text: str | None = None, text_gone: str | None = None
    ) -> dict[str, Any]: ...


# --- the snapshot as a tree -------------------------------------------------------------------


@dataclass(eq=False)
class Node:
    role: str
    name: str = ""
    text: str = ""
    ref: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    def walk(self) -> Iterator["Node"]:
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def words(self, skip: "Node | None" = None) -> list[str]:
        """The visible text of this subtree in order (names of named nodes, texts), `skip`'s
        subtree left out."""
        out: list[str] = []
        stack = [self]
        while stack:
            node = stack.pop()
            if node is skip:
                continue
            if node.name and node.role not in ("generic", "text"):
                out.append(node.name)
            elif node.text:
                out.append(node.text)
            stack.extend(reversed(node.children))
        return out


_LINE = re.compile(
    r'(?P<role>[a-z][\w-]*)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
    r"(?::(?: (?P<text>.*))?)?"
)
_ATTR = re.compile(r"\[([^\]=]+)(?:=([^\]]*))?\]")


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            value = json.loads(raw)
        except ValueError:
            return raw[1:-1]
        return value if isinstance(value, str) else raw
    return raw


def parse_snapshot(text: str) -> Node:
    """The tree of a `browser_snapshot` text. Lines it does not read (a `/url:` property, the
    cut marker) are skipped; the parse is one pass over the lines."""
    root = Node("root")
    stack: list[tuple[int, Node]] = [(-1, root)]
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        depth = len(line) - len(stripped)
        found = _LINE.fullmatch(stripped[2:].rstrip())
        if found is None:
            continue
        attrs = {k: (v or "") for k, v in _ATTR.findall(found["attrs"] or "")}
        node = Node(
            role=found["role"],
            name=_scalar(f'"{found["name"]}"')[:NAME_MAX] if found["name"] is not None else "",
            text=_scalar(found["text"] or "")[:NAME_MAX],
            ref=attrs.pop("ref", ""),
            attrs=attrs,
        )
        while stack[-1][0] >= depth:
            stack.pop()
        node.parent = stack[-1][1]
        node.parent.children.append(node)
        stack.append((depth, node))
    return root


def fold(text: str) -> str:
    """Case, accents, curly quotes and spaces folded: how labels and names are compared."""
    plain = unicodedata.normalize("NFKD", text.replace("\u2019", "'")).casefold()
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip()


_MARKS = re.compile(r"\s*(?:\*|✱|\((?:required|optional|obligatoire|facultatif)\))\s*$", re.I)


def label_of(name: str) -> tuple[str, bool]:
    """The label without its trailing required or optional marks, and whether it was marked
    required."""
    label, required = name.strip(), False
    while True:
        found = _MARKS.search(label)
        if found is None or not label:
            return label, required
        mark = fold(found.group(0)).strip()
        required = required or mark in ("*", "✱", "(required)", "(obligatoire)")
        label = label[: found.start()].rstrip()


def page_text(snapshot: str) -> str:
    """The visible text of a snapshot, one line per node, for `confirmation` and the closed
    page check."""
    return "\n".join(parse_snapshot(snapshot).words())


# --- fields ------------------------------------------------------------------------------------

YES_NO = ({"yes", "no"}, {"oui", "non"})
UPLOAD_ACTIONS = frozenset(
    {
        "upload file", "upload", "attach", "attach file", "choose file", "choose a file",
        "browse", "select file", "add file", "televerser", "telecharger", "joindre",
        "joindre un fichier", "choisir un fichier", "parcourir",
    }
)  # fmt: skip
TEXT_ROLES = ("textbox", "searchbox", "spinbutton")
UNSUPPORTED_ROLES = ("radio", "radiogroup", "slider", "switch", "listbox", "menu", "tree")
# The Ashby "Autofill from resume" box and its kin: the app fills, the page's parser never does.
AUTOFILL = re.compile(r"\bauto ?-?fill|\bremplissage automatique|\bremplir automatiquement")


@dataclass(frozen=True)
class Field:
    """One field of the form as discovered: `key` names it across snapshots (the folded label,
    its kind and its rank among fields with the same label and kind)."""

    key: str
    label: str
    kind: FieldKind
    required: bool = False
    options: tuple[str, ...] = ()
    ref: str = ""
    buttons: Mapping[str, str] = field(default_factory=dict)  # yes or no option -> ref
    value: str = ""  # what the page shows now (text, option, file name)
    checked: bool | None = None  # a box's or a pressed button's state, when the page says

    def form_field(self) -> answers.FormField:
        kind: answers.FieldKind = "yes_no" if self.kind == "yes_no" else self.kind
        return answers.FormField(self.label, kind, self.options, self.required)


@dataclass(frozen=True)
class Skipped:
    label: str
    role: str
    reason: str  # unsupported_widget, autofill, no_label


@dataclass
class Discovery:
    fields: list[Field]
    skipped: list[Skipped]
    submit_refs: list[str]
    alerts: list[str]
    cut: bool = False
    root: Node | None = field(default=None, repr=False)

    def find(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


CONTROLS = frozenset(
    {"button", "textbox", "searchbox", "spinbutton", "combobox", "checkbox", "radio", "option",
     "listbox", "link", "switch", "slider", "menuitem", "tab"}
)  # fmt: skip
PLACEHOLDERS = frozenset({"select...", "select", "choose...", "selectionner...", "--"})


def _around(group: Node, control: Node) -> tuple[list[str], list[str]]:
    """The words of `group` before `control` and after it, the control's subtree left out."""
    before: list[str] = []
    after: list[str] = []
    side = before
    stack = [group]
    while stack:
        node = stack.pop()
        if node is control:
            side = after
            continue
        if node.role in CONTROLS:
            continue  # a control's name is no label of another, an option no value
        word = node.name if node.name and node.role not in ("generic", "text") else node.text
        if word.strip() and fold(word) not in PLACEHOLDERS:
            side.append(word)
        stack.extend(reversed(node.children))
    return before, after


def _group_label(control: Node) -> tuple[Node | None, str, bool, list[str]]:
    """The field group a control stands in (the nearest of three ancestors with words before
    the control), its label (those words, marks off) and the words after the control (a chosen
    file's name)."""
    inner, group = control, control.parent
    for _ in range(3):
        if group is None or group.role == "root":
            break
        before, after = _around(group, inner)
        if before:
            label, required = label_of(" ".join(w for w in before if w not in ("*", "✱")))
            return group, label, required or "*" in before or "✱" in before, after
        inner, group = group, group.parent
    return None, "", False, []


def _is_yes_no(node: Node) -> dict[str, str] | None:
    buttons = [c for c in node.children if c.role == "button"]
    if len(buttons) != 2 or len(node.children) != 2:
        return None
    names = {fold(b.name): b.ref for b in buttons}
    for pair in YES_NO:
        if set(names) == pair:
            yes, no = sorted(pair, key=lambda w: w in ("no", "non"))
            return {"Yes": names[yes], "No": names[no]}
    return None


def _options_outside_selects(root: Node, skip: Node | None = None) -> list[Node]:
    """`option` nodes not inside a native select, skipping `skip`'s subtree."""
    out: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node is skip:
            continue
        if node.role == "combobox" and any(c.role == "option" for c in node.children):
            continue
        if node.role == "option":
            out.append(node)
        stack.extend(reversed(node.children))
    return out


def discover_fields(root: Node, submit_names: frozenset[str]) -> Discovery:
    """The fields of a parsed snapshot, in page order."""
    fields: list[Field] = []
    skipped: list[Skipped] = []
    submit: list[str] = []
    alerts: list[str] = []
    seen: dict[str, int] = {}
    in_pair: set[int] = set()

    def add(label: str, kind: FieldKind, required: bool, **rest: Any) -> None:
        if AUTOFILL.search(fold(label)):
            skipped.append(Skipped(label, kind, "autofill"))
            return
        base = f"{kind}:{fold(label)}"
        seen[base] = seen.get(base, 0) + 1
        fields.append(Field(f"{base}#{seen[base]}", label, kind, required, **rest))

    for node in root.walk():
        if node.role == "alert":
            alerts.append(" ".join(node.words()).strip()[:NAME_MAX])
        if (pair := _is_yes_no(node)) is not None:
            in_pair.update(id(c) for c in node.children)
            _, label, required, _ = _group_label(node)
            if not label:
                skipped.append(Skipped("", "yes_no", "no_label"))
                continue
            pressed = [o for o, r in pair.items() if _by_ref(node, r).attrs.get("pressed") == ""]
            add(
                label,
                "yes_no",
                required,
                options=("Yes", "No"),
                buttons=pair,
                value=pressed[0] if len(pressed) == 1 else "",
                checked=True if pressed else None,
            )
            continue
        label, required = label_of(node.name)
        if node.role in TEXT_ROLES:
            if not label:
                _, label, required, _ = _group_label(node)
            if not label:
                skipped.append(Skipped("", node.role, "no_label"))
                continue
            add(label, "text", required, ref=node.ref, value=node.text)
        elif node.role == "combobox":
            if not label:
                _, label, required, _ = _group_label(node)
            if not label:
                skipped.append(Skipped("", node.role, "no_label"))
                continue
            native = [c for c in node.children if c.role == "option"]
            if native:
                options = tuple(o.name for o in native if fold(o.name) not in PLACEHOLDERS)
                chosen = [o.name for o in native if "selected" in o.attrs]
                value = chosen[0] if chosen and fold(chosen[0]) not in PLACEHOLDERS else ""
                add(label, "select", required, options=options, ref=node.ref, value=value)
            else:
                shown = _shown_choice(node, label)
                add(label, "combobox", required, ref=node.ref, value=shown or node.text)
        elif node.role == "checkbox":
            if not label:
                skipped.append(Skipped("", node.role, "no_label"))
                continue
            add(label, "checkbox", required, ref=node.ref, checked="checked" in node.attrs)
        elif node.role == "button" and id(node) not in in_pair:
            if fold(node.name) in submit_names:
                submit.append(node.ref)
                continue
            group, group_label, group_required, after = _group_label(node)
            if group is None or not group_label:
                continue  # a button outside a labelled group is no field and is never clicked
            if fold(label) == fold(group_label) or fold(node.name) in UPLOAD_ACTIONS:
                shown = after[0] if len(after) == 1 else ""
                add(group_label, "file", required or group_required, ref=node.ref, value=shown)
        elif node.role in UNSUPPORTED_ROLES and node.parent is not None:
            if node.role == "listbox" and node.parent.role in ("combobox", "generic"):
                continue  # the open menu of a typeahead, read as its options
            skipped.append(Skipped(label, node.role, "unsupported_widget"))
    return Discovery(fields, skipped, submit, [a for a in alerts if a])


def _by_ref(scope: Node, ref: str) -> Node:
    return next(n for n in scope.walk() if n.ref == ref)


def _shown_choice(control: Node, label: str) -> str:
    """A react-select's chosen value: the one word of its control's container other than the
    placeholder, when there is exactly one and the container is not the field's whole group
    (the group holds the label)."""
    box = control.parent
    if box is None:
        return ""
    before, after = _around(box, control)
    words = [w for w in before + after if fold(w) not in ("*", "✱")]
    if len(words) != 1 or fold(label_of(words[0])[0]) == fold(label):
        return ""
    return words[0]


# --- results -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filled:
    """What one field's fill came to. `code` says why it is not `filled`; values are never in a
    code."""

    status: Status
    code: str = ""


@dataclass(frozen=True)
class Outcome:
    field: Field
    answer: answers.Answer | answers.Left
    filled: Filled


@dataclass
class Report:
    """The fill of one form. `stopped` is set when the fill stopped before the end:
    `form_changed`, `posting_closed`, `snapshot_cut` or a tool failure's code."""

    outcomes: list[Outcome] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    stopped: str = ""
    needs_person: str = ""  # email_code, captcha (A10 hands them to the person)


def _ok(result: Mapping[str, Any]) -> str:
    """'' when a tool answered ok, else its refusal code."""
    return "" if result.get("ok") else str(result.get("error") or "failed")


# --- the adapter ---------------------------------------------------------------------------


class Adapter:
    """One ATS strategy. Subclasses set the class attributes and may override `form_url`,
    `open` and `pending_step`."""

    name: str = ""
    hosts: tuple[str, ...] = ()
    submit_names: frozenset[str] = frozenset()
    # Standard fields the form must show, each as the folded labels it may carry.
    anchors: tuple[frozenset[str], ...] = ()
    traps: tuple[str, ...] = ()

    def __init__(self, stub_base: str | None = None) -> None:
        """`stub_base` is the fake ATS forms server of the tests (apply A1); None in production,
        where apply A2's settings refuse a stub address."""
        self.stub_base = stub_base.rstrip("/") if stub_base else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    # -- addresses (pure; each adapter writes its own) --
    def matches(self, url: str, snapshot: str | None = None) -> bool:
        """This adapter's form: by the address first, then by the page (an embedded form)."""
        if self.form_url(url) is not None:
            return True
        return snapshot is not None and self.recognises(snapshot)

    def form_url(self, posting_url: str) -> str | None:
        raise NotImplementedError

    def recognises(self, snapshot: str) -> bool:
        """The page holds this adapter's form (its anchors and its submit), framed or not."""
        found = discover_fields(parse_snapshot(snapshot), self.submit_names)
        return self.anchor_problem(found) == ""

    def anchor_problem(self, found: Discovery) -> str:
        if len(found.submit_refs) != 1:
            return "submit_missing" if not found.submit_refs else "submit_ambiguous"
        labels = {fold(f.label) for f in found.fields}
        for anchor in self.anchors:
            if not labels & anchor:
                return "anchor_missing"
        return ""

    # -- pages (pure) --
    def confirmation(self, text: str, url: str = "") -> bool:
        return confirmed(text)

    def errors(self, snapshot: str) -> list[str]:
        """The form's error messages after a submit: the page's alerts."""
        return discover_fields(parse_snapshot(snapshot), self.submit_names).alerts

    def pending_step(self, snapshot: str) -> str:
        """A step only the person can take, shown on the page now: `captcha`, or an adapter's
        own (Greenhouse's `email_code`). '' when there is none."""
        return "captcha" if _captcha(parse_snapshot(snapshot)) else ""

    # -- the lease --
    async def open(self, tools: Tools, posting_url: str) -> str:
        """Opens the form; '' when a page is there, `posting_closed` when it says the posting
        is gone, else the tool's code."""
        return await self.visit(tools, self.form_url(posting_url) or posting_url)

    async def visit(self, tools: Tools, url: str) -> str:
        if code := _ok(await tools.navigate(url)):
            return code
        shot = await tools.snapshot()
        if code := _ok(shot):
            return code
        return "posting_closed" if closed(page_text(str(shot.get("snapshot") or ""))) else ""

    async def read(self, tools: Tools) -> tuple[Discovery | None, str]:
        """A fresh snapshot read into fields (refs change on every render)."""
        shot = await tools.snapshot()
        if code := _ok(shot):
            return None, code
        root = parse_snapshot(str(shot.get("snapshot") or ""))
        found = discover_fields(root, self.submit_names)
        found.cut, found.root = bool(shot.get("cut")), root
        return found, ""

    async def discover(
        self, tools: Tools, known: frozenset[str] = frozenset()
    ) -> tuple[Discovery | None, str]:
        """The form's fields, the options of each empty react-select read by opening it once
        (fields whose key is in `known` are not opened again). '' or the code that stops the
        run (`form_changed`, `snapshot_cut`, a tool's code)."""
        found, code = await self.read(tools)
        if found is None:
            return None, code
        if found.cut:
            return found, "snapshot_cut"
        if problem := self.anchor_problem(found):
            logger.info("%s: form_changed (%s)", self.name, problem)
            return found, "form_changed"
        opened: dict[str, tuple[str, ...]] = {}
        for item in found.fields:
            if item.kind != "combobox" or item.value or item.key in known:
                continue
            fresh, now = await self._now(tools, item.key)
            if now is None:
                return fresh, "form_changed"
            if code := _ok(await tools.click(now.ref, now.label)):
                return found, code
            menu, code = await self.read(tools)
            if menu is None or menu.root is None:
                return found, code
            opened[item.key] = tuple(o.name for o in _options_outside_selects(menu.root) if o.name)
            await tools.press_key("Escape")
        if opened:
            found, code = await self.read(tools)
            if found is None:
                return None, code
            found.fields = [
                replace(f, options=opened[f.key]) if f.key in opened else f for f in found.fields
            ]
        return found, ""

    async def _now(self, tools: Tools, key: str) -> tuple[Discovery | None, Field | None]:
        found, _ = await self.read(tools)
        return found, (found.find(key) if found is not None else None)

    async def fill(
        self, tools: Tools, item: Field, answer: answers.Answer | answers.Left
    ) -> Filled:
        """Sets one field to its answer, through a fresh snapshot (refs change on every
        render), and checks the page shows it. A Left is never typed."""
        if isinstance(answer, answers.Left):
            return Filled("left", answer.reason)
        found, code = await self.read(tools)
        if found is None:
            return Filled("failed", code)
        now = found.find(item.key)
        if now is None:
            return Filled("failed", "form_changed")
        try:
            return await _FILL[now.kind](self, tools, now, answer)
        except _StopError as stop:
            return Filled("failed", stop.code)

    async def fill_form(self, tools: Tools, context: answers.Context) -> Report:
        """Discovers the form, resolves every field from the profile (apply A3) and fills what
        is answered, in page order. Stops at the first `form_changed` or tool failure; a field
        the form added while filling (a question that appears after an answer) is read on a
        second pass."""
        report = Report()
        found, code = await self.discover(tools)
        if found is None or code:
            report.stopped = code
            if found is not None:
                report.skipped = list(found.skipped)
            return report
        report.skipped = list(found.skipped)
        done: set[str] = set()
        for _ in range(2):
            for item in [f for f in found.fields if f.key not in done]:
                done.add(item.key)
                answer = answers.resolve(item.form_field(), context)
                result = await self.fill(tools, item, answer)
                report.outcomes.append(Outcome(item, answer, result))
                if result.status == "failed" and result.code in STOPPING:
                    report.stopped = result.code
                    return report
            found, code = await self.discover(tools, frozenset(done))
            if found is None or code:
                report.stopped = code
                return report
            if all(f.key in done for f in found.fields):
                break
        shot = await tools.snapshot()
        if not _ok(shot):
            report.needs_person = self.pending_step(str(shot.get("snapshot") or ""))
        return report


# A failure that stops the whole fill: the page is not the one discovered, or the lease is gone.
STOPPING = frozenset({"form_changed", "host_not_allowed", "ended", "snapshot_cut", "unreadable"})


class _StopError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _fresh(adapter: Adapter, tools: Tools, key: str) -> tuple[Discovery, Field | None]:
    found, code = await adapter.read(tools)
    if found is None:
        raise _StopError(code)
    return found, found.find(key)


def _value(answer: answers.Answer) -> str:
    if answer.option is not None:
        return answer.option
    value = answer.value
    if isinstance(value, tuple):
        return value[0] if len(value) == 1 else ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value


def _same(shown: str, wanted: str) -> bool:
    return " ".join(fold(shown).split()) == " ".join(fold(wanted).split())


async def _fill_text(adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer) -> Filled:
    wanted = _value(answer)
    if not wanted:
        return Filled("left", "empty_answer")
    if code := _ok(await tools.type(item.ref, wanted, item.label)):
        return Filled("failed", code)
    _, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    return Filled("filled") if _same(now.value, wanted) else Filled("unverified", "value_differs")


async def _fill_select(
    adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer
) -> Filled:
    wanted = _value(answer)
    if wanted not in item.options:
        return Filled("left", "option_missing")
    if code := _ok(await tools.select_option(item.ref, [wanted], item.label)):
        return Filled("failed", code)
    _, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    return Filled("filled") if _same(now.value, wanted) else Filled("unverified", "value_differs")


async def _fill_combobox(
    adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer
) -> Filled:
    """React-select and typeaheads: type, wait for the option, click the option that reads
    exactly as the answer (the resolver's closed world), then read the chosen value back."""
    wanted = _value(answer)
    if not wanted:
        return Filled("left", "empty_answer")
    if code := _ok(await tools.type(item.ref, wanted, item.label)):
        return Filled("failed", code)
    pick: Node | None = None
    for attempt in range(OPTION_WAITS):
        found, now = await _fresh(adapter, tools, item.key)
        if now is None or found.root is None:
            return Filled("failed", "form_changed")
        options = [o for o in _options_outside_selects(found.root) if o.name]
        if options:
            # The option that reads exactly as the answer: the resolver's own choice, else its
            # closed world matcher over what the menu shows (a typeahead lists no options
            # before typing).
            target = answer.option or answers.match_option(wanted, tuple(o.name for o in options))
            pick = next((o for o in options if target and _same(o.name, target)), None)
            break
        if attempt < OPTION_WAITS - 1:
            await tools.wait_for(time=OPTION_WAIT_S)
    if pick is None:
        await tools.press_key("Escape")
        _, now = await _fresh(adapter, tools, item.key)
        if now is not None and now.value:
            await tools.type(now.ref, "", item.label)  # a typed text is no choice: cleared
            await tools.press_key("Escape")
        return Filled("left", "option_missing")
    target_name = pick.name
    if code := _ok(await tools.click(pick.ref, target_name)):
        return Filled("failed", code)
    _, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    return (
        Filled("filled") if _same(now.value, target_name) else Filled("unverified", "value_differs")
    )


async def _fill_yes_no(
    adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer
) -> Filled:
    """Clicks the Yes or No button, then asserts on the buttons: the one clicked pressed, the
    other not. No hidden input is read or set."""
    wanted = _value(answer)
    if wanted not in item.buttons:
        return Filled("left", "option_missing")
    if code := _ok(await tools.click(item.buttons[wanted], f"{item.label}: {wanted}")):
        return Filled("failed", code)
    _, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    if now.checked is None:
        return Filled("unverified", "no_pressed_state")
    return Filled("filled") if now.value == wanted else Filled("unverified", "value_differs")


async def _fill_checkbox(
    adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer
) -> Filled:
    if answer.value is not True:
        return Filled("left", "not_ticked")
    if code := _ok(await tools.check(item.ref, True, item.label)):
        return Filled("failed", code)
    _, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    return Filled("filled") if now.checked else Filled("unverified", "not_checked")


async def _fill_file(adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer) -> Filled:
    """Uploads a document given to the lease; only a document answer is uploaded."""
    if not answer.source.startswith("documents.") or not isinstance(answer.value, str):
        return Filled("left", "not_a_document")
    if code := _ok(await tools.upload(item.ref, [answer.value], item.label)):
        return Filled("failed", "upload_refused" if code == "file_not_given" else code)
    found, now = await _fresh(adapter, tools, item.key)
    if now is None:
        return Filled("failed", "form_changed")
    if found.alerts:
        return Filled("failed", "upload_rejected")
    return Filled("filled") if _same(now.value, answer.value) else Filled("unverified", "no_file")


_FILL = {
    "text": _fill_text,
    "select": _fill_select,
    "combobox": _fill_combobox,
    "yes_no": _fill_yes_no,
    "checkbox": _fill_checkbox,
    "file": _fill_file,
}


# --- page sentences: confirmation, closed, captcha --------------------------------------------

# A confirmation is a sentence that reads, whole, as one of these (closed world); a page holding
# any refusal sentence is never a confirmation, whatever else it says.
_CONFIRM = re.compile(
    r"(?:thank you|thanks)(?: so much)?,? for "
    r"(?:applying|your application|submitting your application)"
    r"(?: to [\w&' .-]{1,80})?"
    r"|(?:your )?application (?:has been |was |is )?(?:successfully )?(?:submitted|received|sent)"
    r"(?: successfully)?(?:,? thank you)?"
    r"|we(?: have|'ve) received your application"
    r"|votre candidature a (?:bien )?ete (?:envoyee|recue|transmise|enregistree)"
    r"|merci (?:pour|de) votre candidature"
    r"|candidature (?:envoyee|transmise|recue)"
)
_REFUSAL = re.compile(
    r"\b(?:error|errors|required|invalid|failed|fix|try again|could not|couldn't|cannot|"
    r"unable|unfortunately|not (?:been )?(?:submitted|received|sent)|no longer|closed|not found|"
    r"erreur|obligatoire|invalide|echec|impossible|n'a pas ete|veuillez corriger)\b"
)


def _sentences(text: str) -> list[str]:
    out = []
    for part in re.split(r"[.!\n]+", text):  # a question mark ends no sentence: "Not found?"
        plain = re.sub(r"[^\w&' -]+", " ", fold(part))
        plain = re.sub(r"\s+", " ", plain).strip(" -")
        if plain:
            out.append(plain)
    return out


def confirmed(text: str) -> bool:
    """The page confirms the application: one sentence reads as a confirmation and none as a
    refusal. When unsure, not confirmed (apply A6 then waits, and ends `submit_unknown`)."""
    sentences = _sentences(text)
    if any(_REFUSAL.search(s) for s in sentences):
        return False
    return any(_CONFIRM.fullmatch(s) for s in sentences)


_CLOSED = re.compile(
    r"(?:the )?(?:job|page|posting|position)(?: you (?:requested|are looking for))? "
    r"(?:was |is )?not found"
    r"|(?:the |this )?(?:job|posting|position|role)(?: you are looking for)? is no longer "
    r"(?:open|available|accepting applications)"
    r"|(?:this )?(?:job|posting|position|role) (?:has been|is) (?:closed|filled)"
    r"|(?:cette )?(?:offre|annonce) n'est plus (?:disponible|en ligne)"
)


def closed(text: str) -> bool:
    """The page says the posting is gone: one sentence reads, whole, as a closed or not found
    page."""
    return any(_CLOSED.fullmatch(s) for s in _sentences(text))


_CAPTCHA_NAMES = frozenset(
    {"i am not a robot", "i'm not a robot", "je ne suis pas un robot", "verify you are human"}
)


def _captcha(root: Node) -> bool:
    return any(
        (n.role in ("button", "checkbox", "iframe") and fold(n.name) in _CAPTCHA_NAMES)
        or (n.role == "iframe" and re.search(r"\b(?:recaptcha|hcaptcha)\b", fold(n.name)))
        for n in root.walk()
    )
