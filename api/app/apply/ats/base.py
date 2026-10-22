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
  box that is no combobox's open menu, a button that is no yes or no pair, no file field and no
  submit) is reported `unsupported_widget` and left to the agent or the person; an unrecognised
  button is never clicked.
  * `textbox`, `searchbox`, `spinbutton`: a text field named by its accessible name;
  * `combobox` with `option` children: a native select; without: a react-select or a typeahead,
    whose options are read by opening it once (a typeahead shows none until something is typed).
    Its value is the choice the page shows (a react-select's single value; a typeahead's own
    text once its menu is closed), never the text typed into it; its options are those of its
    own group, or of the one list box that appeared after the type or click (a menu the page
    renders elsewhere), never another question's;
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
- A step only the person can take (`pending_step`: a captcha, a sign in wall, Greenhouse's e-mail
  code) is looked for on every read, before any anchor: when one shows, the fill stops with
  `needs_person` and nothing more is clicked or typed.
- An adapter chosen by the page (`adapter_for` with an address that is not the adapter's own) is
  `framed`: the submit and every anchor must stand inside one `iframe`, and everything the fill
  reads (fields, submit, alerts, options) is that iframe's subtree, so a company page's own boxes
  around the frame are never typed into. A top level page that only looks like the form gets no
  adapter (the generic strategy).
- A label is the accessible name with the required marks taken off (a leading or trailing `*`,
  "(required)", "(obligatoire)"); a name holding only marks is no label.

Nothing here clicks a submit element: the adapter has no such call (the gate's `gate_click`,
apply A6, is the only one), and a click the tool layer refuses as `submit` or `danger` stops the
fill. No answer value, typed text or page text is logged: codes and counts.

`Adapter.fill` on its own does not check the anchors: a caller that refills a field (apply A6)
goes through `discover` first.

Declared open points: a `textbox` cannot be told from a multi line one in the snapshot, so every
text field is resolved as `text` (a newline typed into a one line field is refused by the tool
layer and reported `failed`); a yes or no group whose buttons do not expose a pressed state is
`unverified`, never `filled` (apply A6 reads values back before the click); a box the page ticked
by itself and the resolver leaves stays as the page set it, reported with its state. A framed
form's host cannot be verified: the AI snapshot's `iframe` node carries no address and the tool
layer checks only the main frame's host, so a framed match says only that the page frames a form
of this shape (apply A5 and A13 decide whether Full auto may use it). A typeahead with no visible
label, or one that closes its menu without taking the click and keeps the typed text, reads as
its text: the first is `unverified`, the second would read `filled` (no recorded page does it).
A sign in wall is seen only by a password box; a "Sign in to apply" page without one reads as
`form_changed` (the generic strategy then sees it).
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
TEXT_MAX = 10_000  # characters of a value kept: what the tool layer types at most, read back whole
VALUE_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "combobox"})  # text cut at TEXT_MAX


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
        cap = TEXT_MAX if found["role"] in VALUE_ROLES else NAME_MAX
        node = Node(
            role=found["role"],
            name=_scalar(f'"{found["name"]}"')[:NAME_MAX] if found["name"] is not None else "",
            text=_scalar(found["text"] or "")[:cap],
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
_LEADING_MARKS = re.compile(r"^(?:\s*[*✱])+\s*")


def label_of(name: str) -> tuple[str, bool]:
    """The label without its leading `*` and its trailing required or optional marks, and
    whether it was marked required."""
    label, required = name.strip(), False
    if (lead := _LEADING_MARKS.match(label)) is not None:
        label, required = label[lead.end() :], True
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


def summary_answer(value: object, kind: str) -> object:
    """A discovered yes/no's canonical option remains a typed boolean for summary comparison."""
    return (value == "Yes") if kind == "yes_no" and value in ("Yes", "No") else value


def summary_matches(value: object, shown: str) -> bool:
    """Compare typed answers with a summary, including the yes/no vocabulary we discover."""
    if isinstance(value, bool):
        return fold(shown) in ({"yes", "oui"} if value else {"no", "non"})
    return isinstance(value, str) and fold(value) == fold(shown)


def summary_text(node: Node) -> str:
    """The value of a named summary row: its inline text and child text, without its label."""
    return " ".join(
        [node.text, *(word for child in node.children for word in child.words())]
    ).strip()


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
    value: str = ""  # what the page shows now (text, chosen option, file name)
    checked: bool | None = None  # a box's or a pressed button's state, when the page says
    typed: str = ""  # a combobox's input text, kept apart from its choice

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
    root: Node | None = field(
        default=None, repr=False
    )  # what was read: the form's frame, if framed
    pending: str = ""  # `pending_step` of the whole page

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
    the control), its label (those words, marks off, cut at `NAME_MAX` as it makes the field's
    key) and the words after the control (a chosen file's name)."""
    inner, group = control, control.parent
    for _ in range(3):
        if group is None or group.role == "root":
            break
        before, after = _around(group, inner)
        if before:
            label, required = label_of(" ".join(w for w in before if w not in ("*", "✱")))
            return group, label[:NAME_MAX], required or "*" in before or "✱" in before, after
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


GROUP_LEVELS = 4  # ancestors climbed to find a combobox's own group


def _foreign(node: Node, own: Node) -> bool:
    """`node` is a control of another field than the combobox `own`: anything in `CONTROLS` but
    `own`, an option, or an unnamed list box (a menu)."""
    if node is own or node.role == "option" or node.role not in CONTROLS:
        return False
    return not (node.role == "listbox" and not node.name)


def own_group(control: Node) -> Node:
    """The largest ancestor of a combobox (up to `GROUP_LEVELS`) that holds no other field: its
    label, its shown choice and its menu, never another question."""
    group, parent = control, control.parent
    for _ in range(GROUP_LEVELS):
        if parent is None or parent.role in ("root", "iframe"):
            break
        if any(_foreign(n, control) for n in parent.walk()):
            break
        group, parent = parent, parent.parent
    return group


def _menu_key(listbox: Node) -> tuple[str, tuple[str, ...]]:
    return fold(listbox.name), tuple(fold(o.name) for o in listbox.walk() if o.role == "option")


def field_options(root: Node, ref: str, before: Node | None = None) -> list[Node]:
    """The options a combobox offers now: those in its own group, else those of the one list box
    of the page that was not in `before` (the snapshot read before the type or click) and holds
    only options. Never another question's options."""
    control = next((n for n in root.walk() if n.ref == ref), None)
    if control is None:
        return []
    group = own_group(control)
    own = [o for o in _options_outside_selects(group) if o.name]
    if own or before is None:
        return own
    seen = {_menu_key(n) for n in before.walk() if n.role == "listbox"}
    inside = {id(n) for n in group.walk()}
    menus = [
        n
        for n in root.walk()
        if n.role == "listbox"
        and id(n) not in inside
        and _menu_key(n) not in seen
        and all(c.role in ("option", "group", "generic", "text") for c in n.walk() if c is not n)
    ]
    if len(menus) != 1:
        return []
    return [o for o in menus[0].walk() if o.role == "option" and o.name]


def menu_open(root: Node, ref: str, before: Node | None = None) -> bool:
    """A combobox's menu is shown: it says it is expanded, or it offers options."""
    control = next((n for n in root.walk() if n.ref == ref), None)
    if control is None:
        return False
    return "expanded" in control.attrs or bool(field_options(root, ref, before))


def _is_menu(listbox: Node) -> bool:
    """A list box is a combobox's open menu when the field group it stands in holds a combobox
    that is no native select."""
    group = _group_label(listbox)[0] or listbox.parent
    return group is not None and any(
        n.role == "combobox" and not any(c.role == "option" for c in n.children)
        for n in group.walk()
    )


def discover_fields(root: Node, submit_names: frozenset[str]) -> Discovery:
    """The fields of a parsed snapshot, in page order."""
    fields: list[Field] = []
    skipped: list[Skipped] = []
    submit: list[str] = []
    alerts: list[str] = []
    seen: dict[str, int] = {}
    in_pair: set[int] = set()
    # Buttons that are no field, by group (reported once per group, after the walk, unless the
    # group holds a file field: "Remove", "Dropbox" or "Enter manually" beside an upload).
    loose: dict[int, tuple[str, str]] = {}
    file_groups: set[int] = set()

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
                shown, own_text = _shown_choice(node, label)
                if not shown and own_text and not _menu_open_here(node):
                    shown = node.text  # a typeahead's choice is its own text, menu closed
                add(label, "combobox", required, ref=node.ref, value=shown, typed=node.text)
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
                file_groups.add(id(group))
            elif id(group) not in loose:
                autofill = AUTOFILL.search(fold(f"{group_label} {node.name}"))
                loose[id(group)] = (group_label, "autofill" if autofill else "unsupported_widget")
        elif node.role in UNSUPPORTED_ROLES and node.parent is not None:
            if node.role == "listbox" and _is_menu(node):
                continue  # the open menu of a combobox, read as its options
            if not label and node.role == "listbox":
                label = _group_label(node)[1]
            skipped.append(Skipped(label, node.role, "unsupported_widget"))
    skipped += [
        Skipped(lab, "button", why) for g, (lab, why) in loose.items() if g not in file_groups
    ]
    return Discovery(fields, skipped, submit, [a for a in alerts if a])


def _by_ref(scope: Node, ref: str) -> Node:
    return next(n for n in scope.walk() if n.ref == ref)


def _shown_choice(control: Node, label: str) -> tuple[str, bool]:
    """A react-select's chosen value: the one word of its control's container other than the
    placeholder, when there is exactly one and the container is not the field's whole group.
    The flag says the combobox stands alone in its labelled group (the container's only words
    are the label): a typeahead, whose choice is its own text."""
    box = control.parent
    if box is None:
        return "", False
    before, after = _around(box, control)
    words = [w for w in before + after if fold(w) not in ("*", "✱")]
    if words and all(fold(label_of(w)[0]) == fold(label) for w in words):
        return "", True
    return (words[0] if len(words) == 1 else ""), False


def _menu_open_here(control: Node) -> bool:
    return "expanded" in control.attrs or any(
        o.name for o in _options_outside_selects(own_group(control))
    )


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
    `needs_person` (the step in `needs_person`), `form_changed`, `posting_closed`,
    `snapshot_cut`, a click the tool layer refused (`submit`, `danger`) or a tool failure's
    code. `needs_person` alone, with `stopped` empty, is a step seen after a complete fill."""

    outcomes: list[Outcome] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    stopped: str = ""
    needs_person: str = ""  # captcha, signin, email_code (A10 hands them to the person)


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

    def __init__(self, stub_base: str | None = None, *, framed: bool = False) -> None:
        """`stub_base` is the fake ATS forms server of the tests (apply A1); None in production,
        where apply A2's settings refuse a stub address. `framed`: the adapter was chosen by the
        page, and reads only the iframe that holds the form (`adapter_for` sets it)."""
        self.stub_base = stub_base.rstrip("/") if stub_base else None
        self.framed = framed

    def __repr__(self) -> str:
        return f"{type(self).__name__}({'framed=True' if self.framed else ''})"

    # -- addresses (pure; each adapter writes its own) --
    def matches(self, url: str, snapshot: str | None = None) -> bool:
        """This adapter's form: by the address first, then by the page (a framed form)."""
        if self.form_url(url) is not None:
            return True
        return snapshot is not None and self.recognises(snapshot)

    def form_url(self, posting_url: str) -> str | None:
        raise NotImplementedError

    def recognises(self, snapshot: str) -> bool:
        """The page frames this adapter's form: one iframe holds its submit and every anchor. A
        top level form is never recognised by the page (anyone can draw one)."""
        frame = form_frame(parse_snapshot(snapshot), self.submit_names)
        if frame is None:
            return False
        return self.anchor_problem(discover_fields(frame, self.submit_names)) == ""

    def scope(self, root: Node) -> Node | None:
        """What the fill reads: the whole page, or the form's iframe when framed (None when no
        iframe holds the form now)."""
        return form_frame(root, self.submit_names) if self.framed else root

    def anchor_problem(self, found: Discovery) -> str:
        if len(found.submit_refs) != 1:
            return "submit_missing" if not found.submit_refs else "submit_ambiguous"
        labels = {anchor_key(f.label) for f in found.fields}
        for anchor in self.anchors:
            if not labels & anchor:
                return "anchor_missing"
        return ""

    # -- pages (pure) --
    def confirmation(self, text: str, url: str = "") -> bool:
        return confirmed(text)

    def errors(self, snapshot: str) -> list[str]:
        """The form's error messages after a submit: the alerts of what the fill reads."""
        scope = self.scope(parse_snapshot(snapshot))
        return [] if scope is None else discover_fields(scope, self.submit_names).alerts

    def pending_step(self, snapshot: str) -> str:
        """A step only the person can take, shown on the page now: `captcha`, `signin`, or an
        adapter's own (Greenhouse's `email_code`). '' when there is none. The whole page is
        read, framed or not (the safe direction)."""
        return self.pending_at(parse_snapshot(snapshot))

    def pending_at(self, root: Node) -> str:
        if _captcha(root):
            return "captcha"
        return "signin" if _sign_in(root) else ""

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
        """A fresh snapshot read into fields (refs change on every render): the form's iframe
        when framed (`root` None and no field when no iframe holds it now), and the whole
        page's pending step."""
        shot = await tools.snapshot()
        if code := _ok(shot):
            return None, code
        page = parse_snapshot(str(shot.get("snapshot") or ""))
        scope = self.scope(page)
        found = discover_fields(scope or Node("root"), self.submit_names)
        found.cut, found.root, found.pending = bool(shot.get("cut")), scope, self.pending_at(page)
        return found, ""

    async def discover(
        self, tools: Tools, known: frozenset[str] = frozenset()
    ) -> tuple[Discovery | None, str]:
        """The form's fields, the options of each empty react-select read by opening it once
        (fields whose key is in `known` are not opened again). '' or the code that stops the
        run: `needs_person` (the step is `Discovery.pending`, looked for before anything
        else), `form_changed`, `snapshot_cut`, a tool's code."""
        found, code = await self.read(tools)
        if found is None:
            return None, code
        if found.pending:
            return found, "needs_person"
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
            if fresh is not None and fresh.pending:
                return fresh, "needs_person"
            if now is None or fresh is None:
                return fresh, "form_changed"
            if code := _ok(await tools.click(now.ref, now.label)):
                return found, code
            menu, code = await self.read(tools)
            if menu is not None and menu.pending:
                return menu, "needs_person"
            shown = menu.find(item.key) if menu is not None else None
            if menu is None or menu.root is None or shown is None:
                return found, code or "form_changed"
            options = field_options(menu.root, shown.ref, fresh.root)
            opened[item.key] = tuple(o.name for o in options)
            await tools.press_key("Escape")
        if opened:
            found, code = await self.read(tools)
            if found is None:
                return None, code
            if found.pending:
                return found, "needs_person"
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
        render), and checks the page shows it. A Left is never typed. A step for the person
        seen on any read stops it, `failed` with the step as code. The anchors are not
        checked here: a refill goes through `discover` first."""
        if isinstance(answer, answers.Left):
            return Filled("left", answer.reason)
        found, code = await self.read(tools)
        if found is None:
            return Filled("failed", code)
        if found.pending:
            return Filled("failed", found.pending)
        now = found.find(item.key)
        if now is None:
            return Filled("failed", "form_changed")
        try:
            if now.kind == "combobox":
                return await _fill_combobox(self, tools, now, answer, found.root)
            return await _FILL[now.kind](self, tools, now, answer)
        except _StopError as stop:
            return Filled("failed", stop.code)

    async def fill_form(self, tools: Tools, context: answers.Context) -> Report:
        """Discovers the form, resolves every field from the profile (apply A3) and fills what
        is answered, in page order. Stops at the first `form_changed`, refused click or tool
        failure, and at a step for the person (`stopped` is then `needs_person`, the step in
        `needs_person`); a field the form added while filling (a question that appears after
        an answer) is read on a second pass."""
        report = Report()
        found, code = await self.discover(tools)
        if found is not None:
            report.skipped = list(found.skipped)
        if found is None or code:
            return _stopped(report, code, found.pending if found is not None else "")
        done: set[str] = set()
        for _ in range(2):
            for item in [f for f in found.fields if f.key not in done]:
                done.add(item.key)
                answer = answers.resolve(item.form_field(), context)
                result = await self.fill(tools, item, answer)
                report.outcomes.append(Outcome(item, answer, result))
                if result.status == "failed" and result.code in PENDING_STEPS:
                    return _stopped(report, "needs_person", result.code)
                if result.status == "failed" and result.code in STOPPING:
                    return _stopped(report, result.code)
            found, code = await self.discover(tools, frozenset(done))
            if found is None or code:
                return _stopped(report, code, found.pending if found is not None else "")
            if all(f.key in done for f in found.fields):
                break
        shot = await tools.snapshot()
        if not _ok(shot):
            report.needs_person = self.pending_step(str(shot.get("snapshot") or ""))
        return report


# A failure that stops the whole fill: the page is not the one discovered (a click the tool layer
# refused as a submit or a dangerous element included), or the lease is gone.
STOPPING = frozenset(
    {"form_changed", "host_not_allowed", "ended", "snapshot_cut", "unreadable", "submit", "danger"}
)
# The steps only the person can take (`pending_step`); the fill stops on them with `needs_person`.
PENDING_STEPS = frozenset({"captcha", "signin", "email_code"})


def _stopped(report: Report, code: str, step: str = "") -> Report:
    report.stopped = code
    if code == "needs_person":
        report.needs_person = step
    return report


class _StopError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _fresh(adapter: Adapter, tools: Tools, key: str) -> tuple[Discovery, Field | None]:
    found, code = await adapter.read(tools)
    if found is None:
        raise _StopError(code)
    if found.pending:
        raise _StopError(found.pending)  # nothing more is clicked or typed
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
    if answer.source == "documents.letter_text":
        from app.apply.answer_limits import letter_issue

        if issue := letter_issue(wanted):
            return Filled("left", issue)
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
    adapter: Adapter, tools: Tools, item: Field, answer: answers.Answer, before: Node | None
) -> Filled:
    """React-select and typeaheads: type, wait for the field's own options (`field_options`,
    against `before`, the snapshot read before the type), click the option that reads exactly
    as the answer (the resolver's closed world), then read the choice back: `filled` only when
    the page shows the option clicked as the choice and the menu is closed."""
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
        options = field_options(found.root, now.ref, before)
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
        if now is not None and now.typed:
            # A typed text is no choice: cleared, and the clear read back.
            if (code := _ok(await tools.type(now.ref, "", item.label))) in STOPPING:
                return Filled("failed", code)
            await tools.press_key("Escape")
            _, now = await _fresh(adapter, tools, item.key)
            if now is None:
                return Filled("failed", "form_changed")
            if now.typed:
                return Filled("unverified", "not_cleared")
        return Filled("left", "option_missing")
    target_name = pick.name
    if code := _ok(await tools.click(pick.ref, target_name)):
        return Filled("failed", code)
    found, now = await _fresh(adapter, tools, item.key)
    if now is None or found.root is None:
        return Filled("failed", "form_changed")
    if not _same(now.value, target_name):
        return Filled("unverified", "value_differs")
    if menu_open(found.root, now.ref, before):
        return Filled("unverified", "menu_open")
    return Filled("filled")


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


# A captcha the person must solve: a checkbox or button named as reCAPTCHA's, hCaptcha's or
# Cloudflare Turnstile's (closed world, whole name), or a frame named as a challenge of one of
# them. The invisible checks' badge frame ("reCAPTCHA") is no step: it shows on every Ashby and
# Greenhouse form, and passes with pacing (apply A6).
_CAPTCHA_NAMES = frozenset(
    {
        "i am not a robot", "i'm not a robot", "im not a robot", "je ne suis pas un robot",
        "i am human", "i'm human", "je suis humain", "verify you are human",
        "verify you're human", "verifiez que vous etes humain", "verifiez que vous etes un humain",
        "confirm you are human",
    }
)  # fmt: skip
_CAPTCHA_FRAME = re.compile(
    r"\b(?:recaptcha|hcaptcha|cloudflare|turnstile)\b.*\bchallenge\b"
    r"|\bchallenge\b.*\b(?:recaptcha|hcaptcha|cloudflare|turnstile)\b"
)


def _captcha(root: Node) -> bool:
    for n in root.walk():
        name = fold(n.name).rstrip(" .!")
        if n.role in ("button", "checkbox") and name in _CAPTCHA_NAMES:
            return True
        if n.role == "iframe" and _CAPTCHA_FRAME.search(name):
            return True
    return False


# A sign in wall: a password box (the app never types one; the person signs in, SPEC-browser's
# hand off). Its label, whole, in English or French.
_PASSWORD = re.compile(
    r"(?:(?:enter |confirm |re-?enter |current )?(?:your )?password"
    r"|(?:saisissez |confirmez |confirmer )?(?:votre |le )?mot de passe)"
)


def _sign_in(root: Node) -> bool:
    return any(
        n.role == "textbox" and _PASSWORD.fullmatch(fold(label_of(n.name)[0])) for n in root.walk()
    )


# --- the form's frame and its anchors ----------------------------------------------------------


def _frame_of(node: Node) -> Node | None:
    up = node.parent
    while up is not None and up.role != "iframe":
        up = up.parent
    return up


def form_frame(root: Node, submit_names: frozenset[str]) -> Node | None:
    """The iframe that holds the form: the nearest iframe around every button named as a
    submit that stands in one. None when no submit stands in an iframe, or the submits stand
    in different ones. A submit outside any iframe (the company page's own) is not the form's."""
    frames = {
        id(frame): frame
        for n in root.walk()
        if n.role == "button"
        and fold(n.name) in submit_names
        and (frame := _frame_of(n)) is not None
    }
    return next(iter(frames.values())) if len(frames) == 1 else None


def anchor_key(label: str) -> str:
    """A label as anchors compare it: folded, its marks off, "e-mail" read as "email", no
    spaces around a slash ("CV / Résumé" is "cv/resume")."""
    key = fold(label_of(label)[0])
    key = re.sub(r"\be[ -]?mail\b", "email", key)
    return re.sub(r"\s*/\s*", "/", key)


# Anchor vocabularies, as `anchor_key` gives them (closed world: anything else is no anchor).
FIRST_NAME = frozenset({"first name", "firstname", "given name", "prenom"})
LAST_NAME = frozenset({"last name", "lastname", "family name", "surname", "nom", "nom de famille"})
FULL_NAME = frozenset(
    {"name", "full name", "your name", "nom", "nom complet", "nom et prenom", "prenom et nom"}
)
EMAIL = frozenset(
    {
        "email", "email address", "your email", "your email address", "adresse email",
        "adresse electronique", "courriel", "adresse courriel", "adresse de courriel",
    }
)  # fmt: skip
RESUME = frozenset(
    {"resume", "cv", "resume/cv", "cv/resume", "curriculum vitae", "resume or cv", "cv or resume"}
)
SUBMIT_NAMES = frozenset(
    {
        "submit application", "envoyer la candidature", "envoyer ma candidature",
        "soumettre la candidature", "soumettre ma candidature", "postuler",
    }
)  # fmt: skip
