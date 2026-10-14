"""The answer agent of an application (SPEC-apply, "Answers to standard questions", source 5): the
questions neither the adapter nor the resolver answered, put to one model call, and what code
keeps of its answers.

This file is the part of the agent that decides, and it is pure (no store, no clock, no model):

- `eligible`: which left fields go to the agent. A closed list: only a field the resolver left
  because it knows no such question (`not_a_known_question`) or because it is free text written
  for this company (`free_text_for_the_agent`), of a kind the adapter can type (text, select,
  combobox with its options), each key once. Then `refusal` fails closed: what the owner keeps
  for the person stays left whatever a model would say (test_what_the_agent_is_never_asked).
  The resolver's default for a label it does not know is `not_a_known_question`, so two lines
  close the door, in any widget:
  1. the shape (`not_open`): a question the resolver does not know is asked only when its label
     reads as an open question or a prompt to write: it opens on why, what, which, how,
     describe, tell us, explain, share, walk us through, give an example (pourquoi, quel,
     comment, qu'est-ce, décrivez, parlez-nous, expliquez, partagez; warum, wie, welche,
     beschreiben; por qué, qué, cuál, cómo, describa), after an optional "please", "briefly" or
     numbering, or it is a whole prompt noun (Motivation, Cover letter, Additional information,
     Lettre de motivation). A bare noun phrase ("Work permit", "Pending charges", "Total comp",
     "Team preference") or a statement stays the person's, whatever its options. A3's free
     text questions (`free_text_for_the_agent`) were already read as prompts and skip this line;
  2. the vocabularies, in any wording, open or not:
  - a label `terms.legal_reading` reads as a commitment, a consent, the privacy notice or the
    accuracy statement (arbitration, background checks, marketing, data retention, a
    non-compete, a signature, the terms);
  - pay, wanted or received, in any form (salary, OTE, package, rate, bonus, equity, comp, TC,
    income, base, budget; Gehalt, salario, sueldo, pretensión);
  - work authorization and immigration (a visa, sponsorship, a permit, the right to work, a
    green card, residency, citizenship, a passport, H-1B, OPT; titre de séjour, Arbeitserlaubnis,
    permiso de trabajo);
  - criminal and employment history (charges, arrests, convictions, offences, a DBS check, a
    dismissal, disciplinary action; casier, condamnation);
  - the person's identity or a credential (a password, a social security, tax or ID number, an
    address, a phone, an e-mail, a name, a signature, a birth date): the agent never receives
    the identity section, so any value would be invented;
  - a qualification code cannot prove from the profile (a PhD, a clearance, export control
    status, fine-tuning, a certification or a licence);
  - a select whose options speak of work authorization, criminal history or pay ("Citizen",
    "Permanent resident", "Needs a visa"), whatever its label (`option_topic`);
  - a yes or no: a `yes_no` field, a select whose options say yes or no, or a label worded as a
    closed question. Code cannot prove a yes from the profile, and a no can be a false legal
    statement (sponsorship in a wording the resolver does not know).
  Never a box (terms are the person's), a file, an EEO topic, never a field a policy or a
  missing profile value left (age, citizenship, current pay, a years question the profile does
  not settle).
- `judge`: what is kept of the agent's reply. The same `refusal` first, so a question that
  reaches it by another path is still left. Each proposal must name a question it was asked and
  a source of the person's (`facts`, `experience`, `skills`, `answers`, `motivation`, `letter`):
  one without is dropped (`no_source`). A choice must be one of the field's options exactly
  (`answers.match_option`); a typed value that is a bare yes or no is dropped (`yes_or_no`), so
  is one that is a bare negation or a non answer ("None", "N/A", "Not applicable", "No pending
  charges", "I do not require sponsorship", "Aucun", "Néant": `bare_negation`), and a value that
  speaks of work authorization, criminal history or pay (`legal_statement`): a missed label
  cannot be answered "None" or "I hold the right to work" on the person's behalf. A typed value
  goes through mail M9's `factcheck_rules.check` against the profile: a number, a
  duration, a link, an employer or a title that the profile does not hold leaves the field
  (`fact_check`). So a posting that tells the model to add a link changes nothing on the form.
- `Request` and `Reply`: what the model call receives (the questions as data, the profile's
  facts and answers, the posting, the letter: never the identity section) and gives back.

The call itself is an `AnswerAgent`. Declared open point: the agent of the agents runtime
(`apply_answer`, internal, private_io, model_by_credential, token_budget, its fake streams) is the
second slice of A5; until it is registered the run has no `AnswerAgent`, and skips the agent the
way a run without a credential does (every question it would have asked stays left).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.apply import answers, terms
from app.apply.ats import base
from app.documents import profile_view
from app.mail import factcheck_rules

AGENT_REASONS = frozenset({"not_a_known_question", "free_text_for_the_agent"})
AGENT_KINDS = frozenset({"text", "select", "combobox"})
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
    # Why the resolver left it: anything but A3's free text is held to the opener rule.
    reason: str = "not_a_known_question"


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


# --- what stays the person's (fail closed) -------------------------------------------------------
# Each vocabulary is read on the folded label (`answers.fold`: lower case, no accents), English and
# French, some German and Spanish. They are the second line behind `_OPEN`: a label they miss is
# asked only if it reads as an open question, and the judge then drops a bare negation and any
# typed statement on these topics. They lean wide on purpose.

# Pay wanted or received: A3's pay words (`answers._PAY`, `answers._SALARY_WORDS`), the forms a
# package takes, and German and Spanish stems (read anywhere in a word: "Gehaltsvorstellung").
_PAY = re.compile(
    r"\b(?:salar(?:y|ies)|salaires?|compensation|remunerations?|pay|paid|wages?|earn\w*"
    r"|gagne\w*|package|ctc|ote|on-? ?target|rates?|tarifs?|tjm|bonus(?:es)?|primes?|equity"
    r"|stocks?|rsus?|commissions?|pretentions?|expected pay|comp|total comp|tc|income|revenus?"
    r"|base|hourly|daily|budget|expect\w* to (?:make|earn)|financial expectations?|ganar)\b"
    r"|gehalt|lohn|verg(?:u|ue)tung|verdien|salario|sueldo|pretension|retribuci"
)
# Work authorization and immigration: a status the profile never proves to code, and a no that
# is a false legal statement (sponsorship in a wording A3 does not know).
_AUTHORIZATION = re.compile(
    r"\bvisas?\b|sponsor|permit|right[ -]to[ -]work|(?:eligib\w*|authori[sz]\w*|allowed"
    r"|entitled) to work|\beligib|work (?:authori[sz]ation|eligibility|status|pass)"
    r"|employment pass|green ?card|\bead\b|\bresiden|citizen|nationalit|passport|passeport"
    r"|immigra|\bh-? ?1 ?-?b\b|\bopt\b|\bcpt\b|titre de sejour|permis de (?:travail|sejour)"
    r"|autorisation de travail|parrainage|arbeitserlaubnis|arbeitsgenehmigung|aufenthalt"
    r"|staatsangeh|permiso de trabajo|patrocin|nacionalid|ciudadan"
)
# Criminal and employment history. "In charge of" is a role, not a charge.
_HISTORY = re.compile(
    r"convict|(?<!in )\bcharge[sd]?\b|arrest|felon|misdemean|offen[cs]e|criminal|casier"
    r"|condamn|\bdbs\b|disclosure and barring|fired|dismiss|terminated|disciplin|sanction"
    r"|vorstraf|fuhrungszeugnis|antecedentes|penal"
)
# The person's identity and credentials: never sent to the model, so never answerable by it.
_IDENTITY = re.compile(
    r"pass ?word|passcode|mot de passe|\bpin\b|user ?name|\blog ?in\b|identifiant|\baccount\b"
    r"|social security|securite sociale|\bssn\b|national insurance|\bnino\b|\bnir\b|\btax\b"
    r"|fiscal|\bid\b|identit|passport|passeport|driver'?s? licen|permis de conduire"
    r"|address|adresse|\bstreet\b|\brue\b|post ?code|\bzip\b|code postal"
    r"|phone|telephone|\bmobile (?:number|no)\b|\bportable\b|\bcell\b|e-?mail|courriel"
    r"|(?:full|first|last|legal|given|family|middle|maiden|preferred|your|user) ?name\b|^name\b"
    r"|\bnoms?\b|prenoms?|signat|\bsign\b|initials|paraphe|birth|naissance|\bdob\b"
    r"|\biban\b|\bbic\b|\bswift\b|\brib\b|bank"
)
# A qualification the profile does not prove to code: a degree level, a clearance, an export
# control status, a model training claim, a certification or a licence.
_UNPROVEN = re.compile(
    r"\bph\.? ?d\b|doctora|clearance|habilitation|secret defense|export control|\bitar\b|\bear\b"
    r"|u\.? ?s\.? person|deemed export|fine-? ?tun|certif|licen[cs]|accredit|chartered"
)
# A closed question: answered yes or no whatever the widget. English and German openers, the
# French inversion ("Avez-vous", "Pouvez-vous", "Est-ce que") and Spanish ones; "whether" and
# "confirm" anywhere.
_CLOSED = re.compile(
    r"^\W*(?:do|does|did|are|is|am|have|has|had|can|could|will|would|were|was|may|might|shall"
    r"|should|haben|habt|hast|sind|bist|ist|konnen|kannst|wurden|sprechen|tiene|tienes|tienen"
    r"|eres|es usted|esta|puede|puedes|cuenta|posee)\b"
    r"|^\W*\w+(?:-t)?-(?:vous|tu|ils?|elles?)\b|^\W*est-ce\b|\bwhether\b|\bconfirm"
)
# A commitment verb: a label that asks the person to agree, consent, authorise or certify is a
# legal statement whatever it is about, beside what `terms` reads.
_CONSENT = re.compile(
    r"\b(?:agree|consent|accept|acknowledge|authori[sz]|certify|certifie\b|attest|declare|undertake|waive"
    r"|accepte|consen|autoris|atteste|declare|engage)"
)
# An open question or a prompt to write: an opener (after numbering, a required mark, "please",
# "briefly" or "in a few words"), or a whole prompt noun. German "was" is not here: English
# "was" opens a closed question, and `_CLOSED` reads first.
_OPEN = re.compile(
    r"^[\W\d_]*(?:(?:please|kindly|briefly|in (?:a few|one|two|three|\w+) (?:words|sentences|lines)"
    r"|merci de|veuillez|en quelques (?:mots|lignes))\W+)*"
    r"(?:why|what|which|how|describe|tell (?:us|me)|explain|share|walk us through"
    r"|give (?:us )?an example|pourquoi|quel(?:le)?s?|comment|qu'est-ce|que|en quoi|decri\w*"
    r"|parlez|expliqu\w*|racontez|partagez|presentez|warum|wie|welche[rsmn]?|beschreiben"
    r"|erzahlen|erklaren|por ?que|cual(?:es)?|como|describ\w*|cuent\w*)\b"
    r"|^\W*(?:motivation|cover letter|lettre de motivation|additional information"
    r"|informations? complementaires?)\W*$"
)
_YES_NO_WORD = re.compile(r"^(?:yes|no|oui|non|ja|nein|si|y|n)\b")
_BARE = frozenset({"yes", "no", "oui", "non", "ja", "nein", "si", "y", "n"})
# A typed value read by `answers.option_key`: a first clause that is a non answer, or that opens
# on a negation ("No pending charges", "I do not require sponsorship"). "Nothing" and "rien" only
# alone: "Nothing excites me more than agents" is an answer.
_NON_ANSWER = re.compile(
    r"^(?:nothing(?: to (?:add|declare|report|say|mention))?|n a|na|nil|not applicable"
    r"|sans objet|neant|non applicable|rien(?: a (?:signaler|declarer|ajouter))?|nada)$"
)
_NEGATION = re.compile(
    r"^(?:no|not|none|nope|never|nein|kein\w*|aucun\w*|jamais|nunca|ningun\w*"
    r"|(?:i|we) (?:do not|don t|dont|will not|won t|wont|shall not) (?:require|need)"
    r"|je n (?:ai|aurai) pas besoin)\b"
)
# A typed statement on a topic that stays the person's: work authorization, criminal history,
# pay. Narrower than the label vocabularies, so "a Python package" or "a knowledge base" stays.
_STATEMENT = re.compile(
    rf"{_AUTHORIZATION.pattern}|convict|criminal|felon|misdemean|arrest|casier|condamn"
    r"|pending charges|vorstraf|antecedentes|salar|salaire|compensation|remunerat|negoti|negocia"
    r"|pretention|market rate|to be discussed|open to discuss|a discuter|a definir"
)


def _says_yes_or_no(option: str) -> bool:
    """An option that answers a closed question: a yes or no text of A3's, or one opening on yes
    or no ("Yes, with sponsorship", "N/A" too: an option is short, so this leans wide)."""
    key = answers.option_key(option)
    return key in answers.YES_OPTIONS or key in answers.NO_OPTIONS or bool(_YES_NO_WORD.match(key))


def fold(text: str) -> str:
    return answers.fold(text)


def _bare_negation(value: str) -> bool:
    key = answers.option_key(re.split(r"[,.;:!?]", value, maxsplit=1)[0])
    return bool(_NON_ANSWER.match(key) or _NEGATION.match(key))


def _bare_yes_or_no(value: str) -> bool:
    """A typed value whose first clause is a yes or a no ("Yes, I am."), and nothing like "No two
    days are alike"."""
    key = answers.option_key(re.split(r"[,.;:!]", value, maxsplit=1)[0])
    return key in answers.YES_OPTIONS or key in answers.NO_OPTIONS or key in _BARE


# In order: the first that matches names the refusal.
REFUSALS: tuple[tuple[str, Callable[[str, str, tuple[str, ...]], bool]], ...] = (
    ("yes_or_no", lambda text, kind, options: kind == "yes_no"),
    ("kind", lambda text, kind, options: kind not in AGENT_KINDS),
    ("no_options", lambda text, kind, options: kind == "combobox" and not options),
    ("eeo", lambda text, kind, options: answers.eeo_topic(text) is not None),
    ("pay", lambda text, kind, options: bool(_PAY.search(text))),
    ("authorization", lambda text, kind, options: bool(_AUTHORIZATION.search(text))),
    ("history", lambda text, kind, options: bool(_HISTORY.search(text))),
    ("legal", lambda text, kind, options: terms.legal_reading(text) or bool(_CONSENT.search(text))),
    ("identity", lambda text, kind, options: bool(_IDENTITY.search(text))),
    ("unproven", lambda text, kind, options: bool(_UNPROVEN.search(text))),
    ("yes_or_no", lambda text, kind, options: any(_says_yes_or_no(o) for o in options)),
    ("option_topic", lambda text, kind, options: any(_STATEMENT.search(fold(o)) for o in options)),
    ("yes_or_no", lambda text, kind, options: bool(_CLOSED.search(text))),
)


def refusal(
    label: str, kind: str, options: tuple[str, ...] = (), reason: str = "not_a_known_question"
) -> str | None:
    """Why a question stays the person's whatever a model would say, else None. An empty label
    is refused too: nothing says what it asks. A question the resolver does not know (any
    reason but A3's free text, by default) must also read as open (`_OPEN`)."""
    text = answers.fold(label)
    if not text:
        return "no_label"
    for code, refuses in REFUSALS:
        if refuses(text, kind, options):
            return code
    if reason != "free_text_for_the_agent" and not _OPEN.search(text):
        return "not_open"
    return None


def eligible(outcomes: Sequence[base.Outcome]) -> list[Question]:
    """The fields the agent is asked about, in page order, each key once (A4's second pass may
    report a field again)."""
    found: list[Question] = []
    seen: set[str] = set()
    for outcome in outcomes:
        item, left = outcome.field, outcome.answer
        if not isinstance(left, answers.Left) or left.reason not in AGENT_REASONS:
            continue
        if item.key in seen or refusal(item.label, item.kind, item.options, left.reason):
            continue
        seen.add(item.key)
        found.append(
            Question(item.key, item.label, item.kind, item.options, item.required, left.reason)
        )
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
    if refused := refusal(question.label, question.kind, question.options, question.reason):
        return Verdict(key, None, refused)
    if value is None or not value.strip():
        return Verdict(key, None, "agent_left")
    if proposal.source not in PERSON_SOURCES:
        return Verdict(key, None, "no_source")
    value = value.strip()
    source = f"agent.{proposal.source}"
    if question.kind in ("select", "combobox"):
        picked = answers.match_option(value, question.options)
        if picked is None:
            return Verdict(key, None, "no_matching_option")
        if _issues(picked, sources):
            return Verdict(key, None, "fact_check")
        return Verdict(key, answers.Answer(picked, source, picked), "accepted")
    if len(value) > VALUE_MAX:
        return Verdict(key, None, "too_long")
    if _bare_yes_or_no(value):
        return Verdict(key, None, "yes_or_no")
    if _bare_negation(value):
        return Verdict(key, None, "bare_negation")
    if _STATEMENT.search(fold(value)):
        return Verdict(key, None, "legal_statement")
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
