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

Declared open points: a plain `<div>` whose script submits the form on click, with no submit word
in any name and no selector of the lease, is not marked (the adapters pass their selectors for
that); Enter in a single text input outside any form is let through; a name written in another
language than English or French is not read.
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
# Over the folded words of a field's autocomplete, name, id, aria-label, placeholder and labels.
# Narrow on purpose: "expiry" alone is also a work permit's date, "carte" a residence permit and
# "security code" the e-mail code of an ATS; a card form is caught by its number or CVC field.
CARD = re.compile(
    r"\bcc (number|csc|exp)|\bcard ?num|\bcardnumber|\bcredit card|\bcvc\b|\bcvv\b|\bcvc2\b"
    r"|\bcarte (bancaire|de credit)|\bnumero de (la )?carte|\bcryptogramme"
)
NAMES_MAX = 60
NAME_MAX = 500

# What the page tells about the element a ref names. `sels` are the lease's submit selectors.
# The control is the element itself, or the button or link it sits in, or a label's control.
FACTS_JS = r"""
(el, sels) => {
  const clickable = "button, input, select, textarea, a[href], label, summary, [role=button]," +
    " [role=link], [role=menuitem], [role=checkbox], [role=radio], [role=switch], [role=tab]," +
    " [role=option], [role=combobox]";
  const target = el.closest(clickable) || el;
  const control = (target.tagName === "LABEL" && target.control) ? target.control : target;
  const names = [];
  const add = v => { if (typeof v === "string" && v.trim()) names.push(v.slice(0, 500)); };
  const own = n => {
    if (!n || !n.getAttribute) return;
    for (const a of ["aria-label", "title", "alt", "placeholder", "name", "id"]) add(n.getAttribute(a));
    const ids = (n.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    for (const id of ids.slice(0, 5)) { const l = document.getElementById(id); if (l) add(l.textContent); }
  };
  for (const n of new Set([el, target, control])) {
    own(n);
    if (n.tagName === "INPUT" && ["submit", "button", "image", "reset"].includes(n.type)) add(n.value);
    if (n.tagName !== "SELECT" && n.tagName !== "TEXTAREA") add(n.innerText || n.textContent || "");
    for (const d of Array.from(n.querySelectorAll("[aria-label], [alt], [title]")).slice(0, 30)) own(d);
    for (const l of Array.from(n.labels || []).slice(0, 5)) add(l.innerText || l.textContent || "");
  }
  const form = control.form || control.closest("form");
  let selectorHit = false, selectorError = false;
  for (const s of sels) {
    try { if (el.closest(s) || target.closest(s) || control.closest(s)) selectorHit = true; }
    catch (e) { selectorError = true; }
  }
  const fieldText = n => [n.getAttribute("autocomplete"), n.getAttribute("name"), n.id,
    n.getAttribute("aria-label"), n.getAttribute("placeholder"),
    ...Array.from(n.labels || []).map(l => l.textContent)].filter(Boolean).join(" ").toLowerCase();
  const fields = form ? Array.from(form.elements).slice(0, 300) : [];
  return {
    tag: control.tagName.toLowerCase(),
    type: (control.type || "").toLowerCase(),
    role: (control.getAttribute("role") || "").toLowerCase(),
    in_form: !!form,
    names: names.slice(0, 60),
    selector_hit: selectorHit,
    selector_error: selectorError,
    field: fieldText(control),
    form_fields: fields.map(fieldText),
  };
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


def verdict(
    facts: Any, *, action: str = "click", profile_edit: bool = False
) -> str | None:
    """None when the action may go ahead, else the refusal's code: `danger`, `card_field`,
    `submit`, `bad_selector`, `unreadable`. `action` is click, check, type or key (Enter or
    Space on the focused element)."""
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
    if facts.get("selector_hit"):
        return "submit"
    if action == "type":
        return None
    tag, kind = facts.get("tag"), facts.get("type")
    if tag in ("button", "input") and kind in SUBMIT_TYPES:
        return "submit"
    if action == "key" and tag == "input" and not button and facts.get("in_form"):
        return "submit"  # Enter in a field of a form submits it (implicit submission)
    if action in ("click", "key") and button and says_submit(names, profile_edit):
        return "submit"
    return None
