"""Remember this answer for next forms (owner, 2026-11-05, answer 6; SPEC-apply, "Answers to
standard questions"). Where the review asks the person for an answer the app could not fill, a
box saves it to Saved answers, so that the next form with the same question is filled with it.

Editing an application still saves nothing by itself (owner, 2026-10-23): remembering is its own
act and its own route, and what is saved is the answer stored on the application, the one the
person saw saved.

What may be remembered is what the resolver would read back from the bank: `answers.resolve` on
the same field, with a bank that holds the answer, must give that answer, from the bank
(`offered` probes it before the person types, `check` with the answer they saved). So nothing the
resolver keeps away from the bank is ever stored: the EEO topics, salary, someone else's details,
age, citizenship, a criminal record, pay received, free text written for one company, the
person's own "leave blank" rules, legal boxes and files. `SENSITIVE` also refuses the special
categories the resolver does not name (health, religion, politics, union membership, pregnancy,
family status...): a label that touches one is never remembered, whatever else it says.

The box is ticked by default unless the question names the company (`names_company`): the
company's name, a distinctive word of it, or the words a form uses for its own company ("us",
"our", "this company"). When unsure (no company name), it is unticked.
"""

import re
from dataclasses import replace
from typing import Any, Literal

from app.apply import answers, bank

Default = Literal["on", "off"]

# The special categories of personal data the resolver's policies do not already keep from the
# bank (its EEO topics hold gender, sexual orientation, ethnicity, race, disability, veteran
# status and age). Matched on the folded label, anywhere.
SENSITIVE = re.compile(
    r"\bhealth|medic|\bmental\b|illness|disease|\bsick|allerg|\bvaccin|\bcovid|\bdrug|alcohol"
    r"|\bpregnan|maternit|paternit|\bsante\b|maladie|\benceinte|grossesse|traitement"
    r"|accommodat|amenagement|\bphysical (?:condition|limitation|restriction)"
    r"|religio|\bfaith\b|\bcroyance|\bpolitic|\bpolitique|trade union|union member|syndica"
    r"|marital|\bmarried\b|situation familiale|etat civil|\bchild|\benfant|\bkids?\b"
    r"|\bdependents?\b|genetic|genetique|biometric|biometrique|\bblood\b|\bsang\b"
)

_WORDS = re.compile(r"[a-z0-9]+")
# Words of a company name that name no company in a question.
_LEGAL = frozenset(
    _WORDS.findall(
        "inc incorporated ltd limited llc llp plc corp corporation co company group holding "
        "holdings sa sas sasu sarl srl spa gmbh ag kg bv nv ab as asa oy oyj pty kk se the and "
        "et de des du la le les of"
    )
)
_GENERIC = frozenset(
    _WORDS.findall(
        "ai app apps analytics bank capital cloud consulting data digital energy europe france "
        "global health international io lab labs media network networks partners robotics "
        "services software solutions studio studios systems tech technologies technology"
    )
)
# The words a form uses for the company it belongs to.
_SELF = re.compile(
    r"\b(?:us|our|ours|we|this (?:company|organi[sz]ation|employer|firm|team)|the company"
    r"|nous|notre|nos|chez nous|cette (?:entreprise|societe|organisation)|l'entreprise"
    r"|la societe|votre entreprise)\b"
)


def _words(text: str) -> list[str]:
    return _WORDS.findall(answers.fold(text))


def names_company(label: str, company: str | None) -> bool:
    """Whether the question names the posting's company: its whole name (legal words such as
    "SAS" or "Inc" left out), a distinctive word of it (three letters or more, not a word as
    common as "Robotics" or "Labs"), the name written glued, or the words a form uses for
    itself. True when the company is unknown: the box is then unticked."""
    said = _words(label)
    if not said or _SELF.search(" ".join(said)):
        return True
    named = [one for one in _words(company or "") if one not in _LEGAL]
    if not named:
        return True
    glued = "".join(named)
    if f" {' '.join(named)} " in f" {' '.join(said)} " or (
        len(glued) >= 5 and glued in "".join(said)
    ):
        return True
    distinctive = {one for one in named if len(one) >= 3 and one not in _GENERIC}
    return bool(distinctive.intersection(said))


def _probe(field: answers.FormField) -> str:
    """An answer the field takes, to ask the resolver whether it would read the bank at all."""
    if field.kind == "yes_no":
        return "Yes"
    if field.kind == "date":
        return "2000-01-01"
    if field.options:
        return field.options[0]
    return "answer"


def _from_bank(field: answers.FormField, context: answers.Context, text: str) -> Any:
    """What the resolver fills this field with when the bank holds `text` for its question:
    the value (or the option chosen), or None when it does not take it from the bank."""
    key = bank.question_key(field.label)
    found = answers.resolve(
        field, replace(context, bank=bank.AnswerBank(context.user_id, {key: text}))
    )
    if not isinstance(found, answers.Answer) or found.source != "bank":
        return None
    return found.option if found.option is not None else found.value


def _askable(field: answers.FormField) -> bool:
    label = field.label.strip()
    return (
        field.kind not in ("file", "checkbox", "multi")
        and 0 < len(label) <= bank.QUESTION_MAX
        and bool(bank.question_key(label))
        and not SENSITIVE.search(answers.fold(label))
    )


def offered(
    field: answers.FormField, context: answers.Context, company: str | None
) -> Default | None:
    """Whether the review offers the box for this field, and ticked or not: None when the
    answer could never be read back from the bank (a sensitive question, a policy, a legal
    box), "off" when the question names the company, else "on"."""
    if not _askable(field) or _from_bank(field, context, _probe(field)) is None:
        return None
    return "off" if names_company(field.label, company) else "on"


def saved_text(value: Any) -> str | None:
    """The person's answer as Saved answers keeps it: Yes or No for a yes or no question, the
    text otherwise. None for a value that is not an answer."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if not isinstance(value, str) or len(value) > bank.VALUE_MAX:
        return None
    try:
        return bank.saved_text(value)
    except ValueError:
        return None


def check(field: answers.FormField, context: answers.Context, value: Any) -> str | None:
    """The text to save for the person's answer to this field, or None when it may not be
    remembered: the next form with the same question must be filled with exactly this answer,
    from the bank."""
    text = saved_text(value)
    if text is None or not _askable(field):
        return None
    filled = _from_bank(field, context, text)
    if isinstance(value, bool):
        # A yes or no question that lists its options is filled with the option that says it.
        said = answers.YES_OPTIONS if value else answers.NO_OPTIONS
        same = filled is value or (isinstance(filled, str) and answers.option_key(filled) in said)
    else:
        same = filled == text
    return text if same else None
