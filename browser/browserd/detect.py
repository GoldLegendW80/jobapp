"""The submit detector and the danger detector of the tool layer (browser B5, SPEC-browser "What
the tool layer enforces in code").

Before a click, a check, a key that activates (Enter, Space) or a typing, the tool reads the facts
of the element in the page (FACTS_JS, run on the element the ref names) and `verdict` decides here,
in plain Python, from those facts alone:

- **submit**: the element (or the button, link or label control it sits in) is a submit control
  (`type` submit or image, which a `<button>` without a type inside a form is), matches one of the
  selectors the lease was given (the apply adapters' own submit elements), or is a button like
  control whose accessible names say submit in English or French (SUBMIT_STEMS, and a confirm verb
  with an application noun: "Valider ma candidature"). A lease of purpose `profile_edit` adds
  Save, Update, Enregistrer and Mettre à jour (PROFILE_STEMS). A submit element is clicked only by
  a call that carries the lease's gate capability (browserd/tools).
- **danger**: delete, remove, withdraw, pay, buy and their French forms, and every field of a
  payment card (autocomplete `cc-*`, or a name, id or label that says card number, CVC, CVV,
  expiry) or a control in a form that has one. Refused whatever the call carries.

Names are every text a person or a screen reader could take for the element's name: aria-label,
aria-labelledby, title, alt, value of a button input, the visible text, the labels, and the same
attributes of the element's descendants (a "Submit" hidden in an image's alt or an icon's
aria-label). They are folded (NFKC, format characters dropped, accents stripped, case folded) and
cut into words; a word matches a stem when it starts with it, so glued forms (`SubmitApplication`,
`ApplyNow`) match too. Fail closed: an invalid selector of the lease refuses the click
(`bad_selector`), and so does a page that cannot be read (`unreadable`).

A click is judged on the element the ref names and on the element at the point the click lands on
(a wrapper `<div>`, a `<fieldset>` or a `<form>` whose centre is the submit button), and an element
holding a submit control is itself marked. Walks up the tree cross shadow roots. A typing goes into
a text entry only (`not_editable` otherwise).

Declared open points: a plain `<div>` whose script submits the form on click, with no submit word
in any name and no selector of the lease, is not marked (the adapters pass their selectors for
that); nor is `<a href="javascript:...">`; Enter in a single text input outside any form is let
through; a name written in another language than English or French is not read; a closed shadow
root is not entered (its host is judged).
"""

import re
import unicodedata
from typing import Any

# A word starting with one of these marks a button like control as submit.
SUBMIT_STEMS = (
    "submit",
    "soumet",
    "soumission",
    "send",
    "envoy",
    "envoi",
    "apply",
    "postul",
)
# A confirm verb and an application noun in the same name: "Valider ma candidature".
CONFIRM_VERBS = (
    "valid",
    "confirm",
    "finalis",
    "finaliz",
    "finish",
    "termin",
    "complet",
)
APPLICATION_NOUNS = ("candidature", "application")
# Purpose `profile_edit` only (SPEC-browser): the profile gate clicks these, nobody else.
PROFILE_STEMS = ("save", "update", "enregistr", "sauvegard")
PROFILE_PHRASES = (("mettre", "a", "jour"), ("mise", "a", "jour"))

# A word starting with one of these marks any element as dangerous.
DANGER_STEMS = (
    "delete", "remov", "supprim", "suppress", "effac", "withdraw", "purchas", "achet", "achat",
    "checkout", "unsubscrib", "desinscri", "desabonn", "deactivat", "desactiv",
)  # fmt: skip
# Whole words only: a stem "pay" would refuse the French "Pays" (country) of every form.
DANGER_WORDS = frozenset(
    {"pay", "payer", "payez", "paiement", "paiements", "payment", "payments", "paypal", "buy",
     "retirer", "retirez", "commander"}
)  # fmt: skip
# "Annuler ma candidature", "Cancel application": cancel only with the application noun.
CANCEL_VERBS = ("annul", "cancel")

# Controls a name can turn into a submit: buttons and links. A text field, a select, a checkbox
# or a radio never submits by a click, whatever its label says ("I confirm what I submit...").
BUTTON_TAGS = frozenset({"button", "a", "summary", "div", "span", "li", "img", "svg"})
BUTTON_ROLES = frozenset({"button", "link", "menuitem", "tab", ""})
BUTTON_INPUTS = frozenset({"submit", "button", "image", "reset"})
SUBMIT_TYPES = frozenset({"submit", "image"})
# What browser_type types into: a text entry, never a button, a box, a file picker or a link.
TEXT_INPUTS = frozenset(
    {"", "text", "email", "tel", "url", "search", "password", "number", "date",
     "datetime-local", "month", "week", "time"}
)  # fmt: skip
TEXT_ROLES = frozenset({"textbox", "searchbox", "combobox"})
NOT_TEXT_TAGS = frozenset(
    {"button", "a", "select", "option", "summary", "label", "img", "svg", "iframe", "frame",
     "form", "fieldset"}
)  # fmt: skip
# The actions that click a point of the page: the element there is judged too.
POINTED = ("click", "check", "type")
# Over the folded words of a field's autocomplete, name, id, aria-label, placeholder and labels.
# Narrow on purpose: "expiry" alone is also a work permit's date, "carte" a residence permit and
# "security code" the e-mail code of an ATS; a card form is caught by its number or CVC field.
CARD = re.compile(
    r"\bcc (number|csc|exp)|\bcard ?num|\bcardnumber|\bcredit card|\bcvc\b|\bcvv\b|\bcvc2\b"
    r"|\bcarte (bancaire|de credit)|\bnumero de (la )?carte|\bcryptogramme"
)
NAMES_MAX = 60
NAME_MAX = 500

# What the page tells about the element a ref names, `opts.sels` being the lease's submit selectors.
# The control is the element itself, or the button or link it sits in, or a label's control; every
# walk up crosses shadow roots (a shadow root's host is the next ancestor), since closest() stops
# at them. `holds_submit`: a submit control or a selector of the lease among the descendants (a
# wrapper whose centre is the button). `custom_submit`: the element or a composed ancestor is a
# custom element of type submit (<sl-button type="submit">, form associated). With `opts.point`,
# `point` holds the same facts of the element at the centre of the visible box, the point a click
# lands on (elementFromPoint, then down through open shadow roots), with `inside` true when that
# element is the one named or within it; null when no element is there.
FACTS_JS = r"""
(el, opts) => {
  const sels = (opts && opts.sels) || [];
  const clickable = "button, input, select, textarea, a[href], label, summary, [role=button]," +
    " [role=link], [role=menuitem], [role=checkbox], [role=radio], [role=switch], [role=tab]," +
    " [role=option], [role=combobox]";
  const host = n => { const r = n.getRootNode ? n.getRootNode() : null; return r && r.host ? r.host : null; };
  const up = n => n.parentElement || host(n);
  const closest = (n, sel) => {
    for (let at = n, i = 0; at && i < 50; at = host(at), i++) { const hit = at.closest(sel); if (hit) return hit; }
    return null;
  };
  const within = (outer, n) => {
    for (let at = n, i = 0; at && i < 1000; at = up(at), i++) if (at === outer) return true;
    return false;
  };
  const kindOf = n => String(n.type || (n.getAttribute && n.getAttribute("type")) || "").toLowerCase();
  const describe = el => {
    const target = closest(el, clickable) || el;
    const control = (target.tagName === "LABEL" && target.control) ? target.control : target;
    const names = [];
    const add = v => { if (typeof v === "string" && v.trim()) names.push(v.slice(0, 500)); };
    const own = n => {
      if (!n || !n.getAttribute) return;
      for (const a of ["aria-label", "title", "alt", "placeholder", "name", "id"]) add(n.getAttribute(a));
      const ids = (n.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
      const root = n.getRootNode ? n.getRootNode() : document;
      for (const id of ids.slice(0, 5)) {
        const l = (root.getElementById ? root : document).getElementById(id); if (l) add(l.textContent);
      }
    };
    for (const n of new Set([el, target, control])) {
      own(n);
      if (n.tagName === "INPUT" && ["submit", "button", "image", "reset"].includes(n.type)) add(n.value);
      if (n.tagName !== "SELECT" && n.tagName !== "TEXTAREA") add(n.innerText || n.textContent || "");
      for (const d of Array.from(n.querySelectorAll("[aria-label], [alt], [title]")).slice(0, 30)) own(d);
      for (const l of Array.from(n.labels || []).slice(0, 5)) add(l.innerText || l.textContent || "");
    }
    const form = control.form || closest(control, "form");
    let selectorHit = false, selectorError = false, holdsSubmit = false;
    for (const s of sels) {
      try {
        if (closest(el, s) || closest(target, s) || closest(control, s)) selectorHit = true;
        if (el.querySelector(s)) holdsSubmit = true;
      } catch (e) { selectorError = true; }
    }
    const inner = Array.from(el.querySelectorAll("button, input, [type]")).slice(0, 300);
    if (inner.some(d => ["submit", "image"].includes(kindOf(d)))) holdsSubmit = true;
    let customSubmit = false;
    for (let at = el, i = 0; at && i < 1000; at = up(at), i++) {
      if (at.tagName && at.tagName.includes("-") && ["submit", "image"].includes(kindOf(at))) customSubmit = true;
    }
    const fieldText = n => [n.getAttribute("autocomplete"), n.getAttribute("name"), n.id,
      n.getAttribute("aria-label"), n.getAttribute("placeholder"),
      ...Array.from(n.labels || []).map(l => l.textContent)].filter(Boolean).join(" ").toLowerCase();
    const fields = form && form.elements ? Array.from(form.elements).slice(0, 300) : [];
    return {
      tag: control.tagName.toLowerCase(),
      type: kindOf(control),
      role: (control.getAttribute("role") || "").toLowerCase(),
      in_form: !!form,
      names: names.slice(0, 60),
      selector_hit: selectorHit,
      selector_error: selectorError,
      holds_submit: holdsSubmit,
      custom_submit: customSubmit,
      editable: !!(control.isContentEditable || el.isContentEditable),
      field: fieldText(control),
      form_fields: fields.map(fieldText),
    };
  };
  const facts = describe(el);
  if (opts && opts.point) {
    const doc = el.ownerDocument, win = doc.defaultView, r = el.getBoundingClientRect();
    const x0 = Math.max(r.left, 0), x1 = Math.min(r.right, win.innerWidth);
    const y0 = Math.max(r.top, 0), y1 = Math.min(r.bottom, win.innerHeight);
    let hit = null;
    if (x1 > x0 && y1 > y0) {
      const x = (x0 + x1) / 2, y = (y0 + y1) / 2;
      hit = doc.elementFromPoint(x, y);
      for (let i = 0; hit && hit.shadowRoot && i < 50; i++) {
        const deeper = hit.shadowRoot.elementFromPoint(x, y);
        if (!deeper || deeper === hit) break;
        hit = deeper;
      }
    }
    facts.point = hit ? Object.assign(describe(hit), {inside: within(el, hit)}) : null;
  }
  return facts;
}
"""


def words(text: str) -> list[str]:
    """The text folded as SPEC-browser's detector reads it: NFKC (full width letters), format
    characters dropped (a zero width space inside "Sub\u200bmit"), accents stripped, case folded,
    camel case split off as a word of its own too, then cut on anything not a letter or a digit."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return [w for w in re.split(r"[^0-9a-z]+", text.casefold()) if w]


def _starts(tokens: list[str], stems: tuple[str, ...]) -> bool:
    return any(token.startswith(stems) for token in tokens)


def _phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(
        tuple(tokens[i : i + size]) == phrase for i in range(len(tokens) - size + 1)
    )


def _glued(names: list[str]) -> list[str]:
    """Every name as words, plus the words of the name with no split (`submitapplication`)."""
    tokens: list[str] = []
    for name in names[:NAMES_MAX]:
        name = name[:NAME_MAX]
        tokens.extend(words(name))
        tokens.extend(words(name.replace(" ", "").replace("_", "").replace("-", "")))
    return tokens


def says_submit(names: list[str], profile_edit: bool = False) -> bool:
    tokens = _glued(names)
    if _starts(tokens, SUBMIT_STEMS):
        return True
    if _starts(tokens, CONFIRM_VERBS) and _starts(tokens, APPLICATION_NOUNS):
        return True
    return profile_edit and (
        _starts(tokens, PROFILE_STEMS)
        or any(_phrase(tokens, p) for p in PROFILE_PHRASES)
    )


def says_danger(names: list[str]) -> bool:
    tokens = _glued(names)
    if _starts(tokens, DANGER_STEMS) or any(token in DANGER_WORDS for token in tokens):
        return True
    return _starts(tokens, CANCEL_VERBS) and _starts(tokens, APPLICATION_NOUNS)


def is_card(field: str) -> bool:
    return bool(CARD.search(" ".join(words(field[: NAME_MAX * 4]))))


def button_like(facts: dict[str, Any]) -> bool:
    tag, kind, role = facts.get("tag", ""), facts.get("type", ""), facts.get("role", "")
    if tag == "input":
        return kind in BUTTON_INPUTS
    if tag in ("select", "textarea", "option", "label"):
        return False
    if role and role not in BUTTON_ROLES:
        return False
    return tag in BUTTON_TAGS or role in ("button", "link", "menuitem", "tab")


def text_entry(facts: dict[str, Any]) -> bool:
    """An input of a text like type, a textarea, a contenteditable, or a text box role."""
    tag, kind, role = facts.get("tag", ""), facts.get("type", ""), facts.get("role", "")
    if tag == "input":
        return kind in TEXT_INPUTS
    if tag == "textarea":
        return True
    if tag in NOT_TEXT_TAGS:
        return False
    return facts.get("editable") is True or role in TEXT_ROLES


def multiline(facts: dict[str, Any]) -> bool:
    """A field where a newline is text, not Enter: a textarea or a contenteditable."""
    tag = facts.get("tag")
    return tag == "textarea" or (facts.get("editable") is True and tag != "input")


def verdict(
    facts: Any, *, action: str = "click", profile_edit: bool = False
) -> str | None:
    """None when the action may go ahead, else the refusal's code: `danger`, `card_field`,
    `submit`, `not_editable`, `bad_selector`, `unreadable`. `action` is click, check, type
    (a click on the field, then keys) or key (Enter or Space on the focused element).

    Facts read with the click point (`point`, FACTS_JS) are judged twice: the element named and
    the element a click at that point lands on, which a wrapper's centre makes a submit button.
    A point that could not be read refuses, and a typing whose click lands inside the field
    itself needs nothing more."""
    code = _own(facts, action, profile_edit)
    if code or action not in POINTED or "point" not in facts:
        return code
    point = facts["point"]
    if not isinstance(point, dict):
        return "unreadable"
    if action == "type" and point.get("inside") is True:
        return None
    return _own(point, "click" if action == "type" else action, profile_edit)


def _own(facts: Any, action: str, profile_edit: bool) -> str | None:
    if not isinstance(facts, dict) or not isinstance(facts.get("names"), list):
        return "unreadable"
    names = [n for n in facts["names"] if isinstance(n, str)]
    fields = [f for f in facts.get("form_fields") or [] if isinstance(f, str)]
    if is_card(str(facts.get("field") or "")):
        return "card_field"
    if action != "type" and any(is_card(f) for f in fields):
        return "card_field"
    # A name is read on buttons and links only: a consent box saying "you may ask for the
    # deletion of your data" or "I confirm what I submit is accurate" is ticked, not refused.
    button = button_like(facts)
    if button and action != "type" and says_danger(names):
        return "danger"
    if facts.get("selector_error"):
        return "bad_selector"
    if facts.get("selector_hit") or facts.get("custom_submit"):
        return "submit"
    if action == "type":
        return None if text_entry(facts) else "not_editable"
    tag, kind = facts.get("tag"), facts.get("type")
    if tag in ("button", "input") and kind in SUBMIT_TYPES:
        return "submit"
    if facts.get("holds_submit"):
        return "submit"  # a wrapper, a fieldset or a form: its centre may be the submit button
    if action == "key" and tag == "input" and not button and facts.get("in_form"):
        return "submit"  # Enter in a field of a form submits it (implicit submission)
    if action in ("click", "key") and button and says_submit(names, profile_edit):
        return "submit"
    return None
