"""Keep a chat correction as a writing rule (SPEC-documents.md, "The chat, and keeping a rule").

Adapted from `modes/retro.md` of career-ops (MIT, NOTICE): a rule is proposed from the person's
own correction, quoted, nothing is written until the person accepts it, and a fact is never a
rule. Code, no model:

- `offer(text)`: the rule the chat offers to keep for a message of the person, else None. It is
  offered only for a message that corrects the wording (`_CORRECTION`, English and French) and
  states nothing about the person (`is_fact`). Whatever the lists do not recognise gets no
  offer: the person can always write a rule in the profile's facts by hand. The rule offered is
  the message itself, its white space collapsed; the person reads it, may edit it, and keeps it
  or not.
- `is_fact(text)`: a digit, a first person statement of what the person did, has or is, or a
  request to add or mention something about them. A rule says how to write; the writers read the
  facts section as the source of claims, so a fact kept there as a rule would make it pass.
- `keep(db, user_id, text, now)`: the person's click on Keep, which is the acceptance. The rule
  is checked again, then appended as one line of the block "Writing rules" of the profile's
  facts section (made when absent), through a profile item proposal of the agents module
  accepted by that click (`proposals.create`, then `proposals.accept`): one profile revision,
  recorded in the proposals, undoable as every accepted item is.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents import proposals
from app.documents import rules
from app.ingestion import items
from app.profile import service as profile_service

RULE_MAX = 300
BLOCK_TITLE = "Writing rules"

_WRITE_EN = (
    r"(?:say|says|saying|write|writes|writing|use|uses|using|call|calling|put|putting|start|"
    r"starting|open|opening|begin|beginning|mention|mentioning)"
)
_NEGATED_EN = r"(?:never|don't|do not|dont|stop|no longer|always|only)"
_WRITE_FR = (
    r"(?:ecris|ecrivez|ecrire|dis|dites|dire|mets|mettez|mettre|utilise|utilisez|utiliser|"
    r"emploie|employez|employer|commence|commencez|commencer|parle|parlez)"
)
_STYLE_FR = (
    r"(?:court|courte|courts|courtes|concis|concise|formel|formelle|simple|simples|direct|"
    r"directe|sobre|long|longue|vague|generique|pompeux|pompeuse|familier|familiere)"
)
# The words of a correction of the wording, on the folded text (`rules.fold`).
_CORRECTION = re.compile(
    "|".join(
        (
            rf"\b{_NEGATED_EN}\s+(?:\w+\s+){{0,2}}?{_WRITE_EN}\b",
            r"\b(?:avoid|avoids|avoiding|instead of|rather than|no more|replace|swap)\b",
            r"\b(?:shorter|longer|simpler|plainer|clearer|crisper|tighter|more concise|less formal|"
            r"more formal|less wordy|more direct|less jargon|fewer adjectives|fewer buzzwords)\b",
            r"\btoo\s+(?:long|short|formal|informal|wordy|vague|generic|salesy|flowery|stiff|"
            r"casual|dense)\b",
            r"\b(?:cliche|cliches|buzzword|buzzwords|jargon|wording|phrasing|tone)\b",
            rf"\bn'\s?(?:\w+\s+)?{_WRITE_FR}\s+(?:jamais|plus|pas)\b",
            rf"\bne\s+(?:\w+\s+)?{_WRITE_FR}\s+(?:jamais|plus|pas)\b",
            rf"\b(?:jamais|toujours)\s+{_WRITE_FR}\b",
            r"\b(?:evite|evitez|eviter|arrete|arretez|arreter|au lieu de|plutot que|remplace|"
            r"remplacez|remplacer|plus jamais)\b",
            rf"\b(?:plus|moins|trop)\s+{_STYLE_FR}\b",
            r"\b(?:formulation|formulations|tournure|tournures|ton)\b",
        )
    )
)
_DID_EN = (
    r"(?:am|was|were|have|had|led|lead|managed|manage|built|build|worked|work|developed|created|"
    r"launched|ran|run|spent|hold|held|earned|studied|speak|spoke|know|knew|own|owned|founded|"
    r"joined|left|got|received|won|graduated|delivered|designed|shipped|migrated|wrote|taught|"
    r"trained|hired|grew|increased|reduced|saved|also|recently|used to)"
)
_MINE_EN = (
    r"(?:team|teams|role|roles|job|jobs|title|employer|employers|company|degree|degrees|diploma|"
    r"salary|manager|experience|years|project|projects|client|clients|customer|customers|"
    r"achievement|achievements|result|results|school|university|phd|master|masters|bachelor|"
    r"certification|certifications|certificate|skills)"
)
_MINE_FR = (
    r"(?:equipe|equipes|poste|postes|role|diplome|diplomes|employeur|entreprise|salaire|manager|"
    r"experience|experiences|annees|projet|projets|client|clients|resultats|ecole|universite|"
    r"certification|certifications|competences)"
)
# What says something about the person, on the folded text: such a message is never a rule.
_FACT = re.compile(
    "|".join(
        (
            r"\d",
            rf"\bi\s+{_DID_EN}\b",
            r"\bi'(?:m|ve|d)\b",
            rf"\bmy\s+{_MINE_EN}\b",
            r"\b(?:add|include|mention|say|write|state|note)\s+(?:that|my|the fact)\b",
            r"\bj'(?:ai|avais|etais)\b",
            r"\bje\s+(?:suis|parle|connais|maitrise|travaille|travaillais|dirige|dirigeais|gere|"
            r"gerais|possede|detiens|sais)\b",
            rf"\b(?:mon|ma|mes)\s+{_MINE_FR}\b",
            r"\b(?:ajoute|ajoutez|mentionne|mentionnez|precise|precisez|indique|indiquez|dis|"
            r"dites|ecris|ecrivez)\s+(?:que|qu')",
        )
    )
)


def clean(text: str) -> str:
    return " ".join(text.split())


def is_fact(text: str) -> bool:
    return _FACT.search(rules.fold(text).text) is not None


def offer(text: str) -> str | None:
    """The rule offered for a message of the person, None when it is no wording correction."""
    rule = clean(text)
    if not rule or len(rule) > RULE_MAX or is_fact(rule):
        return None
    return rule if _CORRECTION.search(rules.fold(rule).text) else None


class RuleRefusedError(ValueError):
    """`reason`: `empty`, `long` or `fact`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FactsFullError(Exception):
    """The facts section cannot take the rule as it is now (`message`, the section model's)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class Kept:
    rule: str
    proposal_id: uuid.UUID | None  # None when the block held the rule already


def _block(facts: dict[str, Any] | None) -> dict[str, Any] | None:
    for block in (facts or {}).get("blocks") or ():
        if isinstance(block, dict) and str(block.get("title", "")).casefold() == "writing rules":
            return block
    return None


def keep(db: Session, user_id: uuid.UUID, text: str, now: datetime) -> Kept:
    """Append the rule to the person's writing rules (module docstring). The caller commits."""
    rule = clean(text)
    if not rule:
        raise RuleRefusedError("empty")
    if len(rule) > RULE_MAX:
        raise RuleRefusedError("long")
    if is_fact(rule):
        raise RuleRefusedError("fact")
    profile = profile_service.get_profile(db, user_id, now, lock=True)
    block = _block((profile.sections or {}).get("facts"))
    title = str(block["title"]) if block is not None else BLOCK_TITLE
    body = str(block.get("body") or "") if block is not None else ""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if rules.fold(rule).text in {rules.fold(line).text for line in lines}:
        return Kept(rule, None)
    value = {"title": title, "body": "\n".join([*lines, rule])}
    key = items.entry_key(items.SLOTS["facts"]["blocks"], value)
    payload = {"section": "facts", "key": key, "action": "add", "value": value, "citations": []}
    try:
        proposal = proposals.create(
            db,
            user_id,
            target=proposals.TARGET_PROFILE,
            target_id=None,
            kind=proposals.KIND_PROFILE_ITEM,
            payload=payload,
            run_id=None,
            now=now,
        )
        proposals.accept(db, user_id, proposal.id, now, profile_revision=profile.revision)
    except proposals.NotStoredError:
        return Kept(rule, None)
    except proposals.InvalidValueError as refused:
        raise FactsFullError(str(refused)) from None
    except proposals.ProfileConflictError as conflict:
        raise FactsFullError(conflict.message) from None
    return Kept(rule, proposal.id)
