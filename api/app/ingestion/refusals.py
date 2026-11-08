"""Why the extraction refused items, kept on the step (owner, 2026-11-08; SPEC-ingestion.md,
"Extraction agent").

A refused item is never stored (`tools.py`), and its rule used to live only in a log line of the
worker. Each extraction step keeps `ingestion_run_steps.refusals`: by section, then by rule, how
many proposals it refused and did not store afterwards, `{"skills": {"unknown_field": 14}}`. The
agent may correct a refused item and propose it again: once an item of the same section and key
is stored, the refusals of that key leave the counts, and a refusal of a key the step already
stored is not counted. So the counts say what was not kept and why; `refused_count` and
`two_digit_year_count` still count every refusal, for the retry budget.

Only constants are kept: section names of `items.SLOTS` and the codes of the rules of the tools,
of the item model and of the checker, never a value, an excerpt or a sentence of the section
model. `sentence` gives the constant sentence the agent was given for a rule; the page writes its
own for the person.

The api reads this module (the run's page, the review), so it imports no Claude Agent SDK.
"""

import uuid
from collections import Counter
from collections.abc import Collection, Mapping

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ingestion import citations, items
from app.ingestion.runs_models import IngestionRunStep

INVALID_ITEM = "invalid_item"
REMOVAL_OF_A_LIST = "removal_of_a_list"
# The sentences of the tools' own rules (`tools.py` answers them).
REASONS: Mapping[str, str] = {
    INVALID_ITEM: "an item is an object with key, action, value and citations",
    REMOVAL_OF_A_LIST: (
        "only a field that holds one value can be removed, never an entry or a list: "
        "propose an entry as the document states it now"
    ),
    items.INVALID_VALUE: "the value does not fit this field",
}

Counts = dict[str, dict[str, int]]


class RunRefusalOut(BaseModel):
    """Proposals of one section refused for one rule and never stored afterwards."""

    section: str
    rule: str
    count: int
    sentence: str = Field(description="The constant sentence the agent was given for the rule.")


def sentence(rule: str) -> str:
    """The sentence the agent was given for a refusal of `rule`: the tools' own, else the item
    model's (it judges an item before the checker does), else the checker's."""
    found = REASONS.get(rule) or items.REASONS.get(rule)
    if found is not None:
        return found
    try:
        return citations.REASONS[citations.Rule(rule)]
    except ValueError:
        return REASONS[items.INVALID_VALUE]


def counts_of(held: object) -> Counts:
    """The counts a step holds, read defensively: a section or a rule that is not a name with a
    whole number above zero is left out."""
    if not isinstance(held, Mapping):
        return {}
    found: Counts = {}
    for section, rules in held.items():
        if not isinstance(section, str) or not isinstance(rules, Mapping):
            continue
        kept = {
            rule: count
            for rule, count in rules.items()
            if isinstance(rule, str) and type(count) is int and count > 0
        }
        if kept:
            found[section] = kept
    return found


def moved(held: object, section: str, change: Mapping[str, int]) -> Counts:
    """`held` with the counts of `section` moved by `change`. A count never goes under zero, and a
    rule or a section left at zero is dropped."""
    counts = counts_of(held)
    rules = counts.pop(section, {})
    for rule, by in change.items():
        left = rules.get(rule, 0) + by
        if left > 0:
            rules[rule] = left
        else:
            rules.pop(rule, None)
    if rules:
        counts[section] = rules
    return counts


def of_steps(db: Session, step_ids: Collection[uuid.UUID]) -> list[RunRefusalOut]:
    """The counts of these steps summed, by section in the order of the profile, then the rule
    that refused most first. A reader of a run gives the steps whose items are the run's
    (`reuse.source_steps`): its own, and for a reused step the earlier step it stands for."""
    if not step_ids:
        return []
    total: Counter[tuple[str, str]] = Counter()
    held = db.scalars(
        sa.select(IngestionRunStep.refusals).where(IngestionRunStep.id.in_(list(step_ids)))
    )
    for one in held:
        for section, rules in counts_of(one).items():
            for rule, count in rules.items():
                total[(section, rule)] += count
    order = {name: place for place, name in enumerate(items.SLOTS)}
    ranked = sorted(
        total.items(),
        key=lambda pair: (order.get(pair[0][0], len(order)), pair[0][0], -pair[1], pair[0][1]),
    )
    return [
        RunRefusalOut(section=section, rule=rule, count=count, sentence=sentence(rule))
        for (section, rule), count in ranked
    ]
