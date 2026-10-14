"""The answer agent of an application (SPEC-apply, "Answers to standard questions", source 5): the
questions neither the adapter nor the resolver answered, put to one model call, and what code
keeps of its answers.

This file is the part of the agent that decides, and it is pure (no store, no clock, no model):

- `eligible`: which left fields go to the agent. A closed list: only a field the resolver left
  because it knows no such question (`not_a_known_question`) or because it is free text written
  for this company (`free_text_for_the_agent`), of a kind the adapter can type (text, select,
  combobox with its options, yes or no). Never a box (terms are the person's), a file, an EEO
  topic or a salary, never a field a policy or a missing profile value left (age, citizenship,
  current pay, a years question the profile does not settle): those stay left whatever a model
  would say (test_only_unknown_questions_go_to_the_agent).
- `judge`: what is kept of the agent's reply. Each proposal must name a question it was asked and
  a source of the person's (`facts`, `experience`, `skills`, `answers`, `motivation`, `letter`):
  one without is dropped (`no_source`). A choice must be one of the field's options exactly
  (`answers.match_option`), a yes or no exactly yes or no. A typed value, and the question itself
  when the answer is yes, go through mail M9's `factcheck_rules.check` against the profile: a
  number, a duration, a link, an employer or a title that the profile does not hold leaves the
  field (`fact_check`). So a posting that tells the model to add a link, or a "yes" to "8+ years
  of Go" from a profile that says 3, changes nothing on the form.
- `Request` and `Reply`: what the model call receives (the questions as data, the profile's
  facts and answers, the posting, the letter: never the identity section) and gives back.

The call itself is an `AnswerAgent`. Declared open point: the agent of the agents runtime
(`apply_answer`, internal, private_io, model_by_credential, token_budget, its fake streams) is the
second slice of A5; until it is registered the run has no `AnswerAgent`, and skips the agent the
way a run without a credential does (every question it would have asked stays left).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.apply import answers
from app.apply.ats import base
from app.documents import profile_view
from app.mail import factcheck_rules

AGENT_REASONS = frozenset({"not_a_known_question", "free_text_for_the_agent"})
AGENT_KINDS = frozenset({"text", "select", "combobox", "yes_no"})
PERSON_SOURCES = frozenset({"facts", "experience", "skills", "answers", "motivation", "letter"})
SECTIONS = ("facts", "experience", "skills", "answers")  # never identity: contact details
VALUE_MAX = 3000
# One call: about 15,000 tokens in and 2,000 out (SPEC-apply, "Cost per application"), and the
# ceiling of twice the estimate.
ESTIMATE_TOKENS = 17_000
TOKEN_BUDGET = 2 * ESTIMATE_TOKENS


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    kind: str
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class Request:
    user_id: str
    questions: tuple[Question, ...]
    profile: Mapping[str, Any]  # the person's facts, experience, skills and answers sections
    posting_title: str
    posting_company: str
    posting_text: str  # data, never instructions
    letter_text: str
    token_budget: int = TOKEN_BUDGET


@dataclass(frozen=True)
class Proposal:
    key: str
    value: str | None  # None: "leave"
    source: str = ""


@dataclass(frozen=True)
class Reply:
    proposals: tuple[Proposal, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: str = ""  # token_budget, turns, error: the rest is left


class AnswerAgent(Protocol):
    async def __call__(self, request: Request) -> Reply: ...


@dataclass(frozen=True)
class Verdict:
    key: str
    answer: answers.Answer | None
    code: str  # accepted, or why the field stays left


def _salary(label: str) -> bool:
    return any(word in label for word in answers._SALARY_WORDS.split("|"))


def eligible(outcomes: Sequence[base.Outcome]) -> list[Question]:
    """The fields the agent is asked about, in page order."""
    found: list[Question] = []
    for outcome in outcomes:
        item, left = outcome.field, outcome.answer
        if not isinstance(left, answers.Left) or left.reason not in AGENT_REASONS:
            continue
        if item.kind not in AGENT_KINDS or (item.kind == "combobox" and not item.options):
            continue
        text = answers.fold(item.label)
        if answers.eeo_topic(text) or _salary(text):
            continue
        found.append(Question(item.key, item.label, item.kind, item.options, item.required))
    return found


def profile_for(sections: Mapping[str, Any]) -> dict[str, Any]:
    return {name: sections.get(name) for name in SECTIONS if sections.get(name)}


def sources_of(
    sections: Mapping[str, Any], posting_title: str, company: str
) -> factcheck_rules.Sources:
    """What an answer may claim: the person's own profile (facts, experience, answers), its
    employers, schools and titles. The posting legitimises its own title and company only."""
    view = profile_view.build(sections, 0)
    own = [body for _, body in view.facts_blocks]
    own += [f"{e.title} {e.organisation} {e.start or ''} {e.end or ''}" for e in view.employers]
    own += [f"{s.degree} {s.school} {s.start or ''} {s.end or ''}" for s in view.schools]
    if view.motivation:
        own.append(view.motivation)
    return factcheck_rules.Sources(
        profile=tuple(text for text in own if text.strip()),
        answers=tuple(view.answers),
        employers=tuple(e.organisation for e in view.employers),
        schools=tuple(s.school for s in view.schools),
        titles=(*(e.title for e in view.employers), posting_title),
        participants=(company,) if company else (),
        known_terms=tuple(view.skills_terms),
    )


def _issues(text: str, sources: factcheck_rules.Sources) -> list[str]:
    return sorted({issue.code for issue in factcheck_rules.check(text, sources)})


def _judge_one(question: Question, proposal: Proposal, sources: factcheck_rules.Sources) -> Verdict:
    key, value = question.key, proposal.value
    if value is None or not value.strip():
        return Verdict(key, None, "agent_left")
    if proposal.source not in PERSON_SOURCES:
        return Verdict(key, None, "no_source")
    value = value.strip()
    source = f"agent.{proposal.source}"
    if question.kind == "yes_no":
        said = answers.option_key(value)
        if said in answers.YES_OPTIONS:
            # A yes affirms the question: "8+ years of Go?" is checked as a claim.
            if _issues(question.label, sources):
                return Verdict(key, None, "fact_check")
            return Verdict(key, answers.Answer(True, source), "accepted")
        if said in answers.NO_OPTIONS:
            return Verdict(key, answers.Answer(False, source), "accepted")
        return Verdict(key, None, "not_a_yes_or_no")
    if question.kind in ("select", "combobox"):
        picked = answers.match_option(value, question.options)
        if picked is None:
            return Verdict(key, None, "no_matching_option")
        if _issues(picked, sources):
            return Verdict(key, None, "fact_check")
        return Verdict(key, answers.Answer(picked, source, picked), "accepted")
    if len(value) > VALUE_MAX:
        return Verdict(key, None, "too_long")
    if _issues(value, sources):
        return Verdict(key, None, "fact_check")
    return Verdict(key, answers.Answer(value, source), "accepted")


def judge(
    questions: Sequence[Question], reply: Reply, sources: factcheck_rules.Sources
) -> list[Verdict]:
    """One verdict per question asked, in their order: a question the reply does not answer, or
    answers twice, stays left; a proposal for a question not asked is ignored."""
    by_key: dict[str, list[Proposal]] = {}
    for proposal in reply.proposals:
        by_key.setdefault(proposal.key, []).append(proposal)
    verdicts = []
    for question in questions:
        given = by_key.get(question.key, [])
        if len(given) != 1:
            code = "not_answered" if not given else "answered_twice"
            verdicts.append(Verdict(question.key, None, code))
            continue
        verdicts.append(_judge_one(question, given[0], sources))
    return verdicts
