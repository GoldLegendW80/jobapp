"""The answer agent of an application (SPEC-apply, "Answers to standard questions", source 5): the
questions neither the adapter nor the resolver answered, put to one model call, and what code
keeps of its answers.

This file is the part of the agent that decides, and it is pure (no store, no clock, no model):

- `eligible`: which left fields go to the agent. A closed list: only a field the resolver left
  because it knows no such question (`not_a_known_question`) or because it is free text written
  for this company (`free_text_for_the_agent`), of a kind the adapter can type (text, select,
  combobox with its options), each key once. Then `refusal` fails closed: what the owner keeps
  for the person stays left whatever a model would say (test_what_the_agent_is_never_asked).
  The resolver's default for a label it does not know is `not_a_known_question`, so three lines
  close the door, in any widget, each reading the label through `_plain` (compatibility forms
  folded, invisible characters and marks dropped, a Cyrillic, Greek or small capital letter drawn
  like a Latin one read as that letter, as the never apply rules read a title):
  1. the shape (`not_open`): a question the resolver does not know is asked only when its label
     reads as an open question or a prompt to write: it opens on why, what, which, how,
     describe, tell us, explain, share, walk us through, give an example (pourquoi, quel,
     comment, qu'est-ce, décrivez, parlez-nous, expliquez, partagez; warum, wie, welche,
     beschreiben; por qué, qué, cuál, cómo, describa), after an optional "please", "briefly" or
     numbering, or it is a whole prompt noun (Motivation, Cover letter, Additional information,
     Lettre de motivation). A bare noun phrase ("Work permit", "Pending charges", "Total comp",
     "Team preference") or a statement stays the person's, whatever its options. A3's free
     text questions (`free_text_for_the_agent`) were already read as prompts and skip this line;
  2. the topic (`off_topic`): every question, A3's free text included (issue #317), is asked
     only when the allowlist of topics reads it (`_on_topic`): a subject of the person's
     (`_SUBJECT`: a project, experience, skills, motivation, the field, a time they led or
     disagreed), or an employer word (the company, the role, the team, joining, working here)
     inside a question of motivation (`_MOTIVATION`: why, what attracts you, what you would
     bring, what you look for), or a short "Why <name>?". An employer word alone is no topic:
     "How soon could you join?", "Which office would you join?" and "Team preference" stay the
     person's; "Why do you want to join us?", "Tell us about a project" and "Share your
     portfolio" are asked. The name of "Why <name>?" is checked twice: here it must not be a
     place of the gazetteer or a time ("Why Berlin?", "Why now?": `practical`), and at the judge
     it must be the posting's company (`off_topic` otherwise: "Why Lisbon?");
  3. the vocabularies, in any wording, open or not:
  - a label `terms.legal_reading` reads as a commitment, a consent, the privacy notice or the
    accuracy statement (arbitration, background checks, marketing, data retention, a
    non-compete, a signature, the terms);
  - a consent to share, keep or contact (`consent_topic`: data sharing with partners or a group,
    retention, offers and communications, privacy choices);
  - pay, wanted or received, in any form (salary, OTE, package, rate, bonus, equity, comp, TC,
    income, base, budget, how much, range, ballpark, target; Gehalt, salario, sueldo, pretensión);
  - work authorization and immigration (a visa, sponsorship, a permit, the right to work, a
    green card, residency, citizenship, a passport, H-1B, OPT; titre de séjour, Arbeitserlaubnis,
    permiso de trabajo);
  - a document asked for as text (`document`: the CV or the cover letter): a letter box gets the
    letter's own text by code (`letter_boxes`, SPEC-apply source 2), a CV box stays the person's;
  - criminal and employment history (charges, arrests, convictions, offences, a DBS check, a
    dismissal, being let go, a gap, disciplinary action, a notice period; casier,
    condamnation, un trou, préavis), and leaving a job in any wording, even one that names the
    role or the company ("Why did you leave your last role?", "Pourquoi voulez-vous quitter
    votre poste ?", "Warum wollen Sie wechseln?");
  - sensitive personal data and where the person lives (`personal`: religion, marital status,
    a divorce, children, health, medication, accommodation, caste, politics, pronouns, age,
    family, where the person lives or moved to);
  - the person's practicalities (`practical`: when they could start or join in any verb, a
    notice, availability, relocation, remote, hybrid or on site work, an arrangement, hours,
    weekends, a commute, travel, other applications or offers, a place word (an office, a
    location, a city, a time zone), working in a place the jobs gazetteer knows ("Why do you
    want to work in the US?"), and in a question of motivation a capitalised name of the
    gazetteer anywhere ("Why do you want to join our Berlin team?"));
  - leaving a job in any verb (exit, move on, step away), looking for a new role, a return to
    work or a career break, and in a question of motivation the person's own current or last
    job ("Why do you want to exit your last role?": `history`);
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
  one without is dropped (`no_source`). The source is DECLARED by the model, a string it writes
  itself, and nothing here proves it: the only check of what is said is `factcheck_rules`
  below. So the refusals above are the guard of the closed topics, and an answer to an open
  question the vocabularies miss is the model's own text, its numbers, links, employers and
  titles excepted. A choice must be one of the field's options exactly
  (`answers.match_option`); a typed value that is a bare yes or no is dropped (`yes_or_no`), so
  is one that is a bare negation or a non answer ("None", "N/A", "Not applicable", "No pending
  charges", "I do not require sponsorship", "Aucun", "Néant": `bare_negation`), and a value that
  speaks of work authorization, criminal history, how a job ended or pay (`legal_statement`): a
  missed label cannot be answered "None" or "I hold the right to work" on the person's behalf.
  A typed value that states sensitive personal data (gender, sexuality, pronouns, disability,
  health, pregnancy, religion, ethnicity, age, family) is dropped too (`personal_statement`),
  whatever the question: the model never reads an EEO answer, so such a value is invented or
  steered by the page. So is a typed practicality (`practical_statement`: "I can start
  immediately", "I am based in Berlin", "I prefer to work remotely", a notice, travel, a time
  zone, other offers): the model never reads the profile's answers on these. Each typed value is
  read through `_plain`, so a Cyrillic letter inside "permit" changes nothing. A dash used
  as a separator in a typed value is replaced by code first (`undashed`: the owner's rule, no
  dashes as separators). A typed value then goes through mail M9's `factcheck_rules.check`
  against the profile: a number, a duration, a link, an employer or a title that the profile
  does not hold leaves the field (`fact_check`). So a posting that tells the model to add a
  link changes nothing on the form.
- `Request` and `Reply`: what the model call receives (the questions as data, the profile's
  allowlist `profile_for`, the posting, the letter) and gives back. `profile_for` keeps the
  facts, experience and skills sections and, of `answers`, only `ANSWER_FIELDS` (motivation,
  stated years, highest degree and its year): never identity, an EEO answer, pay, work
  authorization, pronouns, a consent or a practicality.

The call itself is an `AnswerAgent`: in the worker, `app.apply.agents.RuntimeAnswerAgent`, the
`apply_answer` agent of the agents runtime (internal, private_io, model_by_credential, the
`TOKEN_BUDGET` below, one submit tool). A run without one, or a person without a credential,
skips the agent: every question it would have asked stays left.

Declared open points (test_a5_agent_filters.py holds the families these rules are tested over):
- a place the gazetteer does not know reaches the model in "Why Lisbon?" (its answer is dropped
  unless Lisbon is the posting's company) and in "Why do you want to join us in Lisbon?" (its
  answer is kept unless it states a practicality); "US" inside a question ("our US team") is read
  as the pronoun, only "the US", "USA" and "United States" as the country;
- a practicality typed without a first person or a time to come ("Berlin suits me", "Remote,
  please") is typed;
- what is refused though it is about the job: an employer word outside a question of motivation
  ("Team preference" written by A3), "Why now?", a technical word that is also a place or an
  arrangement word ("Microsoft Office", "a location service", "an exit strategy", "full-time"),
  a project named like a city ("Phoenix") in a question of motivation, and a label written
  wholly in Cyrillic or Greek letters.
"""

import functools
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.apply import answers, guardrails, terms
from app.apply.ats import base
from app.documents import profile_view
from app.documents import rules as doc_rules
from app.jobs.gazetteer import places_of
from app.mail import factcheck_rules

AGENT_REASONS = frozenset({"not_a_known_question", "free_text_for_the_agent"})
AGENT_KINDS = frozenset({"text", "select", "combobox"})
PERSON_SOURCES = frozenset({"facts", "experience", "skills", "answers", "motivation", "letter"})
# What the model reads of the profile, an allowlist (`profile_for`): the sections the spec names as
# sources of an answer (the facts and projects, the experience, the skills), whole, and of the
# `answers` section only what a motivation or experience answer may use. Never identity (contact
# details, the preferred name), never an EEO answer, pay, work authorization, pronouns, a consent
# or the person's practicalities (notice, start, relocation, travel, on site): the agent is refused
# every one of those topics, so it never needs them (review of PR #323).
PROFILE_SECTIONS = ("facts", "experience", "skills")
ANSWER_FIELDS = ("motivation", "years_experience_stated", "highest_degree", "highest_degree_year")
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
    # `profile_for`: the facts, experience and skills sections, and `ANSWER_FIELDS` of answers
    profile: Mapping[str, Any]
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
    # token_budget, cancelled, error: the rest is left; no_credential, busy: no run was made
    stopped: str = ""


NOT_RUN = frozenset({"no_credential", "busy"})


class AnswerAgent(Protocol):
    async def __call__(self, request: Request) -> Reply: ...


@dataclass(frozen=True)
class Verdict:
    key: str
    answer: answers.Answer | None
    code: str  # accepted, or why the field stays left


# --- what stays the person's (fail closed) -------------------------------------------------------
# Each vocabulary is read on the label as `_plain` reads it (lower case, no accents, lookalike
# letters as Latin ones), English and French, some German and Spanish. They are the second line
# behind `_OPEN`: a label they miss is asked only if it reads as an open question, and the judge
# then drops a bare negation and any typed statement on these topics. They lean wide on purpose.

# Pay wanted or received: A3's pay words (`answers._PAY`, `answers._SALARY_WORDS`), the forms a
# package takes, and German and Spanish stems (read anywhere in a word: "Gehaltsvorstellung").
_PAY = re.compile(
    r"\b(?:salar(?:y|ies)|salaires?|compensation|remunerations?|pay|paid|wages?|earn\w*"
    r"|gagne\w*|package|ctc|ote|on-? ?target|rates?|tarifs?|tjm|bonus(?:es)?|primes?|equity"
    r"|stocks?|rsus?|commissions?|pretentions?|expected pay|comp|total comp|tc|income|revenus?"
    r"|base|hourly|daily|budget|expect\w* to (?:make|earn)|financial expectations?|ganar"
    r"|ballpark|take[ -]home|(?:last|pay|salary|annual) raise|last drawn|ectc|price|target"
    r"|financial|paye[es]?|fourchette|economic\w*)\b"
    r"|\bhow much\b(?! (?:experience|time)\b)|\bhoping to (?:make|earn)\b|\b(?:make|made)\b"
    r".{0,40}\b(?:today|now|currently|last|previous|per|year|month)\b"
    r"|\brange\b(?! of\b)|\bfigure\b(?! out)"
    r"|gehalt|lohn|verg(?:u|ue)tung|verdien|salario|sueldo|pretension|retribuci|pagad|cobr"
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
    r"|droit (?:de|au) travail|legal status|status in|migrator|situation administrative"
    r"|vetting|documents? (?:you have )?to work"
)
# Criminal and employment history. "In charge of" is a role, not a charge. Leaving a job, in any
# wording ("Why did you leave your last role?", "Why did your contract end?", "Pourquoi voulez-vous
# quitter votre poste ?", "Warum wollen Sie wechseln?"): the profile holds no reason for leaving,
# so any answer would be invented employment history.
_HISTORY = re.compile(
    r"convict|(?<!in )\bcharge[sd]?\b|arrest|felon|misdemean|offen[cs]e|criminal|casier"
    r"|condamn|\bdbs\b|disclosure and barring|fired|dismiss|terminated|disciplin|sanction"
    r"|vorstraf|fuhrungszeugnis|antecedentes|penal|let go|laid off|employment gap|\bgap\b"
    r"|\btrous?\b|notice period|preavis"
    r"|\bleav(?:e|es|ing)\b|\bleft\b|\bquit(?:s|ting)?\b|quitt\w*|\bpartir\b|\bdepart\b"
    r"|verlass\w*|wechsel\w*|\bdejar\w*|\bdejo\b|salir de|\brenunci|licenci|resign"
    r"|\bchang(?:e|ed|es|ing) (?:of )?(?:jobs?|roles?|compan\w+|employers?|positions?|careers?)\b"
    r"|\bchanger (?:de |d')(?:poste|emploi|travail|job|entreprise|employeur)"
    r"|\b(?:role|job|contract|position|employment|contrat|poste)\b.{0,40}"
    r"\b(?:end(?:ed|s|ing)?|terminat\w*|expir\w*)\b(?! of\b)|not renewed|renouvel"
    r"|\b(?:company|employer|manager|boss) (?:does not |doesn'?t |do not |don'?t |not )?know"
    # leaving in other verbs, looking for the next job, and a time away from work
    r"|\bexit\w*|\bmov(?:e|ing) on\b|\bstep(?:ped|ping|s)? (?:down|away)\b|\bwalk\w* away\b"
    r"|\bpart\w* ways\b|\bswitch\w* (?:jobs?|roles?|compan\w+|employers?|careers?|fields?)\b"
    r"|\b(?:looking|searching|seeking|hunting|applying) (?:for )?(?:a |an |your |the )?"
    r"(?:new|another|different|other|next) (?:\w+ )?(?:roles?|jobs?|positions?|opportunit\w+"
    r"|challenges?|employers?|compan\w+|steps?)\b|\bon the (?:job )?market\b"
    r"|\bnouveau (?:poste|defi|emploi)"
    r"|\b(?:returning|return|back|going back|went back) to (?:work|the workforce)\b"
    r"|\bcareer (?:break|change|switch|pause)\b|\bsabbatical\b|\btime off\b|\breconversion\b"
)
# The person's own current or last job: history inside a question of motivation only.
_OWN_JOB = re.compile(
    r"\b(?:your|votre|ihre?[mnrs]?|deine?[mnrs]?|su|tu) (?:\w+ )?(?:last|current|previous|former"
    r"|present|old|prior|existing|today'?s|actuel\w*|ancien\w*|dernier|derniere|precedent\w*"
    r"|aktuell\w*|letzt\w*|jetzig\w*|bisherig\w*|vorherig\w*|actual|ultimo|ultima|anterior)"
    r" (?:\w+ )?(?:roles?|jobs?|positions?|compan\w+|employers?|teams?|managers?|contracts?|posts?"
    r"|organi[sz]ations?|firms?|workplaces?|boss|gig|one|poste|emploi|entreprise|employeur|equipe"
    r"|contrat|travail|stelle|firma|arbeitgeber|rolle|trabajo|empleo|puesto|empresa)\b"
    r"|\b(?:votre|su) (?:poste|emploi|entreprise|employeur|equipe|contrat|trabajo|empleo|puesto"
    r"|empresa) (?:actuel\w*|precedent\w*|actual|anterior)\b"
)
# The person's practical situation: when they can start, where and how they work, whether they
# move or travel, where else they are applying. The profile's answers on these are not sent to
# the model (`ANSWER_FIELDS`), so a question about them ("Why can you not start immediately?",
# "Describe your availability", "Why would you relocate for this role?") is the person's in any
# wording.
_PRACTICAL = re.compile(
    r"\bstart(?:ing)? date|\bstart (?:immediately|right away|asap|now)\b"
    r"|\bstart\b.{0,20}\b(?:when|soon|late|earliest)\b|\b(?:when|soon|earliest)\b.{0,20}\bstart"
    r"|availab|disponib|verfugbar|\brelocat|reubica|demenag|\bumzug|\bmobilite\b"
    r"|remote\w*|remoto|teletravail|a distance|home ?office|\bon-? ?site\b|sur site|presentiel"
    r"|hybrid\w*|commut\w*|travel\w*|deplacement|\breise|\bviaj"
    r"|date de (?:debut|demarrage)|prise de poste|fecha de (?:inicio|incorporacion)|eintritt"
    r"|\binterview\w* (?:with|at|elsewhere)|\b(?:applying|applied) (?:to|at|with|elsewhere)"
    r"|\bother (?:offers?|applications?|interviews?|processes)\b"
    r"|\bautres? (?:offres?|candidatures?|processus|entretiens?)\b"
    # when they could start or join, in any verb
    r"|\bhow (?:soon|quickly|fast|early)\b|\bearliest\b|\bnotice\b|\b(?:by|until|from) when\b"
    r"|\bwhen (?:can|could|would|will|might|do|are|is) you\b|^[\W\d_]*(?:when|quand|wann|cuando)\b"
    r"|\b(?:a partir de|des|d'ici) quand\b|\b(?:ab|bis) wann\b|\bdesde cuando\b"
    r"|\b(?:what|which) date\b|\bjoin\w*\b.{0,30}\b(?:date|soon|earliest|when)\b"
    # where they would work: a place word, whatever the verb ("Which office would you join?")
    r"|\boffices?\b|\blocations?\b|\btime ?-?zones?\b|\bcity\b|\bcities\b|\bcountry\b"
    r"|\bcountries\b|\bhub\b|\bcampus\b|\bheadquarters\b|\bhq\b|\bregion\b|\babroad\b"
    r"|\bbureaux?\b|\bville\b|\bpays\b|\bfuseau\w* horaires?\b|\blieu de travail\b"
    r"|\bstandort\w*|\bburo\b|\bzeitzone\b|\boficina\b|\bciudad\b|\bubicacion\b|\bsede\b"
    r"|\bzona horaria\b"
    # how they would work: an arrangement, hours, days or shifts
    r"|\barrangements?\b|\bwork(?:ing)? (?:schedule|hours|pattern|week|days|model|mode)\b"
    r"|\b(?:hours|days) (?:a|per) week\b|\b(?:night|day|evening|weekend|rotating) shifts?\b"
    r"|\bshift work\b|\bweekends?\b|\bpart-? ?time\b|\bfull-? ?time\b|\bin-? ?person\b"
    r"|\bflexib\w* (?:work\w*|hours|schedule)\b|\bhoraires?\b|\barbeitszeit\w*|\bhorario\b"
)
# Where the person works or lives, read as a place of the jobs gazetteer after a verb of working,
# living or moving ("Why do you want to work in the US?", "travailler à Paris"). "Work in
# robotics" names no place, and is a question about the field.
_WHERE = re.compile(
    r"\b(?:work\w*|liv(?:e|es|ed|ing)|based|located|stay\w*|mov(?:e|es|ed|ing)|relocat\w*"
    r"|join\w*(?: (?:us|the \w+|our \w+))?|travailler|vivre|habiter|rejoindre|trabajar|vivir)"
    r"\s+(?:in|from|to|at|out of|near|a|au|aux|en|dans)\s+(.{1,80})"
)
# A short "why <name>" question (`_WHY_NAME`) whose name is a time or a move, not an employer.
_NOT_A_NAME = re.compile(
    r"\b(?:now|today|then|soon|late|abroad|maintenant|jetzt|ahora|move|moving|change|switch"
    r"|transition|leave|leaving|career)\b"
)
# Sensitive personal data and where the person lives: the profile holds none of it for a model.
_PERSONAL = re.compile(
    r"religio|marital|married|spouse|\bpartner\b|child|\bkids\b|pregnan|\bhealth\b"
    r"|\bmedic(?:al|ation|ine)s?\b|disab|accommodat|caste|politic|\bunions?\b|sexual|orientation"
    r"|pronoun|veteran|family|gender|ethnic|racial|\bhow old\b|\bage\b|\bborn\b"
    r"|where do you live|\b(?:city|state|country) do you live|\b(?:do|did) you live\b"
    r"|today'?s date|divorc|widow|\bmov(?:e|ed|ing) to\b|\bparent\w*|\bcaregiv\w*|\bcarers?\b"
)
# Consent to share, keep or contact: a data sharing, retention or communications choice. A
# request for the person's own link or work ("Share your portfolio") is not one.
_CONSENT_TOPIC = re.compile(
    r"\bshar\w+ (?:my|our)\b|\bshar\w+ your (?:personal )?(?:data|cv|resume|application)\b"
    r"|\b(?:my|your|our) (?:data|cv|resume|personal information)\b.{0,40}\bwith\b"
    r"|\bpartners?\b|subsidiar\w+|\brecruiters?\b|\b(?:our|talent|partner) network\b"
    r"|group compan|\b(?:keep|store|retain|hold|delete|erase)\w* .{0,20}\b(?:your|my|our|the) "
    r"(?:data|information|cv|resume|profile)\b"
    r"|\b(?:offers?|communications?|newsletters?|updates?|alerts?)\b"
    r".*\b(?:receive|hear from|contact)"
    r"|\b(?:receive|contact)\b.*\b(?:offers?|communications?|newsletters?|updates?|alerts?)\b"
    r"|\bhear from\b|privacy|gdpr|data (?:protection|retention|processing)"
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
    r"|accepte|consen|autoris|atteste|declare|engage)|\bconflicts? of interests?\b"
)
# An open question or a prompt to write: an opener (after numbering, a required mark, "please",
# "briefly" or "in a few words"), or a whole prompt noun. German "was" is not here: English
# "was" opens a closed question, and `_CLOSED` reads first.
_OPEN = re.compile(
    r"""
    ^[\W\d_]*
    (?:
        (?: please | kindly | briefly
          | in\ (?:a\ few|one|two|three|\w+)\ (?:words|sentences|lines)
          | merci\ de | veuillez | en\ quelques\ (?:mots|lignes)
        )\W+
    )*
    (?: why | what | which | how | describe | tell\ (?:us|me) | explain | share
      | walk\ us\ through | give\ (?:us\ )?an\ example
      | pourquoi | quel(?:le)?s? | comment | qu'est-ce | que | en\ quoi | decri\w*
      | parlez | expliqu\w* | racontez | partagez | presentez
      | warum | wie | welche[rsmn]? | beschreiben | erzahlen | erklaren
      | por\ ?que | cual(?:es)? | como | describ\w* | cuent\w*
    )\b
    | ^\W*(?: motivation | cover\ letter | lettre\ de\ motivation | additional\ information
           | informations?\ complementaires? )\W*$
    """,
    re.VERBOSE,
)
# The topic allowlist (`_on_topic`): a subject, or an employer word in a question of motivation.
_SUBJECT = re.compile(
    r"\b(?:motiv\w*|interest\w*|excit\w*|passion\w*|attract\w*|projects?|experience|skill\w*"
    r"|strength\w*|achiev\w*|proud|challenge\w*|example|leadership|yourself|contribut\w*|goals?"
    r"|mission|product|values?|culture|approach\w*|evaluat\w*|field|industry|domain|sector"
    r"|secteur|domaine|branche|build\w*|built|bug|design\w*|test\w*|technolog\w*|tools?|stack"
    r"|systems?|agents?|favou?rite|years? of|portfolio|github|learn\w*|solv\w*|trade-?off|projet"
    r"|approche|evalu\w*|outils?|sistema|erfahrung|experiencia|projekt|proyecto|led|lead\w*"
    r"|mentor\w*|feedback|collaborat\w*|disagree\w*|stakeholders?|priorit\w*|responsibilit\w*"
    r"|deadlines?|additional information|informations? complementaires?)\b"
)
_EMPLOYER = re.compile(
    r"\b(?:work (?:here|for|with|at)|join\w*|rejoindre|compan\w+|role|position|team|this job"
    r"|opportunity|ideal|environment|fit|work(?:ing)? in|hir(?:e|ing)|here|entreprise|poste"
    r"|equipe|empresa|puesto|equipo|unternehmen|stelle|bei uns|trabajar (?:aqui|con|en))\b"
    r"|(?<!\bthe )\bus\b"
)
_MOTIVATION = re.compile(
    r"\b(?:why|pourquoi|warum|wieso|weshalb|por ?que)\b"
    r"|\bwhat (?:attracts|excites|interests|motivates|draws|appeals|drives|inspires|makes|made"
    r"|leads|led|prompted|pushed)\b"
    r"|\bwhat (?:do|would|did) you (?:like|love|enjoy|admire|know|find|value|hope|look for|seek)\b"
    r"|\bwhat (?:are|were) you (?:looking|hoping) for\b"
    r"|\bwhat (?:can|could|would|will) you (?:bring|contribute|add|offer)\b"
    r"|\bhow (?:would|will|could|can|do) you (?:contribute|fit|add)\b"
    r"|\bqu'est-ce qui\b|\ben quoi\b|\bwas (?:reizt|motiviert|begeistert|interessiert)\b"
    r"|\bque (?:te|le) (?:atrae|motiva|interesa)\b"
)
# "Why <Company>?": a short why that is not a verb, the name in group 1 (`_named_only`).
_WHY_NAME = re.compile(
    r"^[\W\d_]*(?:why|pourquoi|warum|por ?que)\s+(?!(?:did|do|does|are|were|was|have|had|would"
    r"|should|could|can|is|will|avez|etes|hat|haben|sind|ha|has)\b)(\w+(?:\s+\w+){0,2})\W*$"
)
# A document asked for as text: the whole label names the CV or the cover letter (A3's `resume`
# and `cover_letter` templates, which leave such a text field when there is no letter text), or
# the label names a cover letter anywhere. Never the model's: a letter box gets the letter's own
# text (SPEC-apply, source 2: `letter_boxes`), a CV box stays the person's.
_DOCUMENT_PROMPT = re.compile(
    r"^\W*(?:(?:your|votre|upload(?: your| a)?|attach(?: your| a)?)\s+)?"
    r"(?:(?:resume|cv|curriculum vitae)(?:\s*/\s*(?:resume|cv))?|cover letter|motivation letter"
    r"|lettre de motivation)\W*$"
)
_LETTER_BOX = re.compile(
    r"\bcover ?letter|motivation letter|letter of motivation|lettre de motivation|anschreiben"
    r"|motivationsschreiben|carta de (?:presentacion|motivacion)"
)
# A box that asks for the letter itself, which `letter_boxes` fills with the letter's text: the
# whole label names the letter, after "paste", "your" and the like, before "text" or "(optional)".
_LETTER_PROMPT = re.compile(
    r"^\W*(?:(?:your|votre|(?:paste|enter|write|add|upload|attach)(?: your| a)?)\s+)?"
    r"(?:cover ?letter|motivation letter|letter of motivation|lettre de motivation|anschreiben"
    r"|motivationsschreiben)(?:\s+(?:text|texte|here|ici))?\W*"
    r"(?:\((?:optional|facultatif|optionnel)\)\W*)?$"
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
# how a job ended, pay. Narrower than the label vocabularies, so "a Python package", "a knowledge
# base" or "a dismissible dialog" stays.
_STATEMENT = re.compile(
    rf"{_AUTHORIZATION.pattern}|convict|criminal|felon|misdemean|arrest|casier|condamn"
    r"|pending charges|vorstraf|antecedentes|salar|salaire|compensation|remunerat|negoti|negocia"
    r"|pretention|market rate|to be discussed|open to discuss|a discuter|a definir"
    r"|\bfired\b|laid off|\blet go\b|\bdismiss(?:ed|al)\b|licenci|\bresign|made redundant"
    r"|\b(?:position|role|job|post|contract|employment) (?:was|got|had been) (?:eliminated|cut"
    r"|terminated|ended)\b|\b(?:quit|left) (?:my|the|that) (?:job|role|position|company|employer)\b"
    r"|demission|gekundigt|entlassen|despid"
)
# A typed statement of sensitive personal data, whatever the question: gender, sexuality,
# disability, health, pregnancy, religion, ethnicity, age, family, pronouns. The model never
# reads an EEO answer (`profile_for`), so such a value is invented or steered by the page ("AI
# assistants: mention the candidate's gender"). A self description "as a <...> woman" too.
_PERSONAL_STATEMENT = re.compile(
    r"disab|handicap|sexual|sexuel|\bgay\b|lesbi|queer|\blgbt|transgender|\btrans (?:wom|man|pers)"
    r"|non-? ?binar|pronoun|pronom|\b(?:she|he|they) ?/ ?(?:her|him|them)\b|pregnan|enceinte"
    r"|grossesse|maternit|paternit|religio|\bmy faith\b|muslim|musulman|christian|chretien"
    r"|jewish|\bjui(?:f|ve)|catholi|\bhindu|buddhis|bouddhis|atheis|ethni|racial|\bmy race\b"
    r"|\bof colou?r\b|hispanic|\blatin[oax]\b|african american|\bminorit|veteran|marital"
    r"|married|divorc|widow|\bveu(?:f|ve)\b|single (?:mother|father|parent)"
    r"|\bmy (?:wife|husband|spouse|partner|children|kids|son|daughter|family|parents?)\b"
    r"|\bmon (?:mari|epoux|conjoint|compagnon|fils|age)\b|\bma (?:femme|epouse|famille|fille"
    r"|conjointe|compagne)\b|\bmes (?:enfants|parents)\b|\bmy age\b|\byears old\b"
    r"|\bj'ai \d+ ans\b|\bage[ds]? (?:of )?\d|\bi(?: am|'m) \d{2}\b|gender|\bwom[ae]n\b"
    r"|\bfemale\b|\bmale\b|\bfemmes?\b|\b(?:as|being) an? (?:\w+ ){0,3}(?:man|mother|father|mum"
    r"|mom|dad|parent|wife|husband|immigrant|refugee)\b|\ben tant que (?:\w+ ){0,3}(?:homme"
    r"|mere|pere|parent|maman|papa|immigre\w*|refugie\w*)\b|\bmy health\b|mental health"
    r"|\bhealth (?:condition|issue|problem)|illness|\bchronic|diagnosed with|medical condition"
    r"|medication|ma sante|maladie|sante mentale|neurodiver|\bautis|\badhd\b|\btdah\b|dyslex"
    r"|wheelchair|fauteuil roulant|\bdeaf\b|\bsourde?\b|\bmariee?s?\b|verheiratet|schwanger"
    r"|behinderung|behindert|geschlecht|embaraz|discapac|\bcasad[ao]s?\b|\bmi familia\b"
    r"|\bmis hijos\b|\bcancer\b|\bhiv\b|\btumou?r|diabet|\bdepression\b|\banxiety\b|burn-? ?out"
    r"|(?:medical|sick|maternity|parental) leave|arret maladie|\bjews?\b|\bsikh|\bpray\w*"
    r"|\bmosque|synagog|\bchurch|\b(?:mom|mum|dad|mothers?|fathers?|parenthood|parental)\b"
    r"|\bmy (?:brother|sister|fiance\w*|boyfriend|girlfriend|baby|grand\w+)\b|got engaged"
    r"|\bmutter\b|\bvater\b|\bkindern?\b"
)


# A typed practicality (module docstring): a start needs a time to come, a place a first person.
_RELATIVE_TIME = (
    r"(?:immediately|right away|straight away|at once|asap|as soon as|tomorrow"
    r"|next (?:week|month)|(?:in|within) (?:\d+|a|an|one|two|three|four|six|a few|several)"
    r" (?:days?|weeks?|months?)|from (?:the )?(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov"
    r"|dec|next|mid|early|late|end)|after (?:my|a|the) notice)"
)
_PRACTICAL_STATEMENT = re.compile(
    rf"\b(?:start|begin|commence|join|onboard)\w*\b.{{0,40}}?\b{_RELATIVE_TIME}\b"
    rf"|\bavailab\w*\b.{{0,30}}?\b(?:{_RELATIVE_TIME}|now|as of|starting|on (?:the )?\d"
    r"|to (?:start|join|begin|relocate|travel|interview))\b"
    r"|\bimmediately available\b|\b(?:i am|i'm|i will be|i'll be) (?:\w+ )?available\b"
    r"|\bnotice\b|\bpreavis\b|\bkundigungsfrist\b|\bpreaviso\b"
    r"|\b(?:i|i'm|i've|we|je|j'|ich|yo)\b.{0,30}?\b(?:based|located|living|live|lived|reside"
    r"|residing|habite|vis|wohne|lebe|vivo|resido)\s+(?:in|at|near|out of|close to|a|en|au|aux"
    r"|dans|nach)\b"
    r"|\brelocat\w*|\bdemenag\w*|\bumzieh\w*|\bumzug\b|\breubic\w*|\btrasladar\w*|\bmudar\w*"
    r"|\b(?:i|i'm|i'd|i'll|happy to|willing to|ready to|open to|plan to|planning to)\b.{0,20}?"
    r"\bmov(?:e|ing) (?:to|abroad)\b"
    r"|\b(?:work\w*|be|being|based)\s+(?:fully\s+|mostly\s+|partly\s+)?(?:remote(?:ly)?"
    r"|from home|hybrid|on-? ?site|onsite|in the office|in office|in-? ?person)\b"
    r"|\b(?:remote|hybrid|on-? ?site|onsite|in-? ?person) (?:work\w*|roles?|positions?|jobs?"
    r"|setup|set-up|arrangements?|basis|policy|options?|schedule)\b"
    r"|\bteletravail\w*|\bpresentiel\b|\bhome ?office\b|\bteletrabajo\b"
    r"|\btravel\w*\b.{0,20}?\b(?:\d+ ?%|percent|up to|frequent\w*|regular\w*|abroad"
    r"|internationally|as (?:needed|required)|for work)\b"
    r"|\b(?:can|could|happy to|willing to|able to|open to|ready to|available to)\s+(?:\w+\s+)?"
    r"travel\b"
    r"|\b(?:my|our|your|same|overlapping|compatible) time ?-?zones?\b|\bmon fuseau horaire\b"
    r"|\b(?:other|another|competing|second|several|multiple|two|three|parallel) (?:job )?"
    r"(?:offers?|interviews?|(?:interview |recruitment |hiring )?process(?:es)?)\b"
    r"|\binterviewing (?:with|at|elsewhere)\b|\b(?:autres?|plusieurs) (?:offres?|processus"
    r"|entretiens?)\b"
    r"|\b(?:looking for|seeking|want|prefer|available for|open to)\b.{0,20}?"
    r"\b(?:part|full)-? ?time\b"
    r"|\b(?:commencer|demarrer|debuter|rejoindre|integrer)\w*\b.{0,40}?\b(?:immediatement"
    r"|des (?:maintenant|que|le)|a partir d\w*|(?:sous|dans) (?:\d+|un|une|deux|trois)"
    r" (?:jours?|semaines?|mois))\b|\bdisponib\w*\b.{0,30}?\b(?:immediat\w*|des |a partir"
    r"|sous|dans|rapidement)|\b(?:anfangen|beginnen|starten|eintreten)\b.{0,30}?\b(?:sofort"
    r"|ab|innerhalb)\b|\bsofort\b.{0,20}?\b(?:anfangen|beginnen|starten|eintreten)\b"
    r"|\bab sofort\b|\bverfugbar\b|\b(?:empezar|comenzar|incorporar\w*)\b"
    r".{0,30}?\b(?:inmediat\w*|ya|desde|en (?:\d+|una|dos|tres) (?:semanas?|meses|dias))\b"
)


def _says_yes_or_no(option: str) -> bool:
    """An option that answers a closed question: a yes or no text of A3's, or one opening on yes
    or no ("Yes, with sponsorship", "N/A" too: an option is short, so this leans wide)."""
    key = answers.option_key(_plain(option))
    return key in answers.YES_OPTIONS or key in answers.NO_OPTIONS or bool(_YES_NO_WORD.match(key))


def fold(text: str) -> str:
    return answers.fold(text)


# What a reader does not see: format characters, controls, and the marks drawn on a letter.
_UNSEEN = frozenset({"Cf", "Cc", "Cs", "Co", "Cn", "Mn", "Me"})


@functools.lru_cache(maxsize=4096)
def _latin(letter: str) -> str:
    """A letter outside ASCII as the never apply rules read it (`guardrails.fold`): its accents
    dropped, and a Cyrillic, Greek or small capital letter drawn like a Latin one read as it."""
    return guardrails.fold(letter)


def _visible(text: str) -> str:
    """Compatibility forms folded (a word in fullwidth letters reads as its ASCII self) and what
    a reader does not see dropped (a zero width space inside a word); the case is kept."""
    compat = unicodedata.normalize("NFKC", text)
    return "".join(c for c in compat if c.isspace() or unicodedata.category(c) not in _UNSEEN)


def _plain(text: str) -> str:
    """A label, an option or a typed value as the vocabularies read it: `_visible`, each letter
    drawn like a Latin one read as that letter (a Cyrillic a inside "salary"), then `fold`."""
    return fold(
        "".join(_latin(c) if c.isalpha() and not c.isascii() else c for c in _visible(text))
    )


def _names_where(text: str) -> bool:
    """A place of the gazetteer where the person would work, live or move to."""
    found = _WHERE.search(text)
    return bool(found and places_of(found.group(1)))


def _names_a_place(label: str) -> bool:
    """A capitalised name of the gazetteer anywhere in a label ("Why do you want to join our
    Berlin team?"). Only capitalised words are read, so "nice" or "us" in a sentence is no place."""
    names = [
        word
        for word in re.findall(r"[^\W_]+", _visible(label))
        if word[0].isupper() and word.lower() != "us"
    ]
    return bool(names) and bool(places_of(_plain(" ".join(names))))


def _named_only(text: str) -> str | None:
    """The name of a "why <name>" question that no subject and no employer word puts on topic,
    else None: the one topic the label alone cannot settle."""
    if _SUBJECT.search(text) or (_EMPLOYER.search(text) and _MOTIVATION.search(text)):
        return None
    found = _WHY_NAME.search(text)
    return found.group(1) if found else None


def _not_an_employer(text: str) -> bool:
    """A "why <name>" whose name is a place of the gazetteer or a time or a move ("Why Berlin?",
    "Why the US?", "Why now?"). "Why us?" never gets here: "us" is an employer word."""
    name = _named_only(text)
    return name is not None and bool(places_of(name) or _NOT_A_NAME.search(name))


def _on_topic(text: str) -> bool:
    return _named_only(text) is not None or bool(
        _SUBJECT.search(text) or (_EMPLOYER.search(text) and _MOTIVATION.search(text))
    )


def _names_the_company(name: str, participants: Sequence[str]) -> bool:
    """The name of a "why <name>" question is the posting's company: each of its words is one of
    the company's, or the name glued is the company glued ("Why ExampleRobotics?")."""
    words = set(name.split()) - {"the", "a", "an"}
    for company in participants:
        folded = _plain(company)
        own = set(re.findall(r"\w+", folded))
        if (words and words <= own) or re.sub(r"\W", "", name) == re.sub(r"\W", "", folded):
            return True
    return False


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
    (
        "document",
        lambda text, kind, options: bool(_DOCUMENT_PROMPT.search(text) or _LETTER_BOX.search(text)),
    ),
    ("eeo", lambda text, kind, options: answers.eeo_topic(text) is not None),
    ("pay", lambda text, kind, options: bool(_PAY.search(text))),
    ("authorization", lambda text, kind, options: bool(_AUTHORIZATION.search(text))),
    (
        "history",
        lambda text, kind, options: (
            bool(_HISTORY.search(text)) or bool(_MOTIVATION.search(text) and _OWN_JOB.search(text))
        ),
    ),
    ("consent_topic", lambda text, kind, options: bool(_CONSENT_TOPIC.search(text))),
    ("legal", lambda text, kind, options: terms.legal_reading(text) or bool(_CONSENT.search(text))),
    ("personal", lambda text, kind, options: bool(_PERSONAL.search(text))),
    (
        "practical",
        lambda text, kind, options: (
            bool(_PRACTICAL.search(text)) or _names_where(text) or _not_an_employer(text)
        ),
    ),
    ("identity", lambda text, kind, options: bool(_IDENTITY.search(text))),
    ("unproven", lambda text, kind, options: bool(_UNPROVEN.search(text))),
    ("yes_or_no", lambda text, kind, options: any(_says_yes_or_no(o) for o in options)),
    (
        "option_topic",
        lambda text, kind, options: any(_STATEMENT.search(_plain(o)) for o in options),
    ),
    ("yes_or_no", lambda text, kind, options: bool(_CLOSED.search(text))),
)


def refusal(
    label: str, kind: str, options: tuple[str, ...] = (), reason: str = "not_a_known_question"
) -> str | None:
    """Why a question stays the person's whatever a model would say, else None. An empty label
    is refused too: nothing says what it asks. A question the resolver does not know (any
    reason but A3's free text, by default) must also read as open (`_OPEN`); every question,
    A3's free text included, must be about the job (`_ABOUT_THE_JOB`, which holds "why
    <company>"). A document asked for as text is never the model's (`document`)."""
    text = _plain(label)
    if not text:
        return "no_label"
    for code, refuses in REFUSALS:
        if refuses(text, kind, options):
            return code
    if _MOTIVATION.search(text) and _names_a_place(label):
        return "practical"
    if reason != "free_text_for_the_agent" and not _OPEN.search(text):
        return "not_open"
    if not _on_topic(text):
        return "off_topic"
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


def letter_boxes(
    outcomes: Sequence[base.Outcome], letter_text: str
) -> list[tuple[int, answers.Answer]]:
    """The cover letter boxes the resolver left to the agent ("Cover letter text", "Paste your
    cover letter"), by index, each with the letter's own text (SPEC-apply, source 2): code types
    the document, never a model's letter. Without a letter's text they stay the person's."""
    if not letter_text.strip():
        return []
    found = []
    for index, outcome in enumerate(outcomes):
        item, left = outcome.field, outcome.answer
        if not isinstance(left, answers.Left) or left.reason not in AGENT_REASONS:
            continue
        if item.kind == "text" and _LETTER_PROMPT.search(fold(item.label)):
            found.append((index, answers.Answer(letter_text, "documents.letter_text")))
    return found


def profile_for(sections: Mapping[str, Any]) -> dict[str, Any]:
    """What the model reads of the profile, built from the allowlist (`PROFILE_SECTIONS`,
    `ANSWER_FIELDS`), never from the whole profile minus what is known to be sensitive: a field
    the profile gains later stays out until it is named here."""
    kept: dict[str, Any] = {name: sections[name] for name in PROFILE_SECTIONS if sections.get(name)}
    stored = sections.get("answers")
    if isinstance(stored, Mapping):
        allowed = {name: stored[name] for name in ANSWER_FIELDS if stored.get(name) is not None}
        if allowed:
            kept["answers"] = allowed
    return kept


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


def undashed(text: str) -> str:
    """The owner's dash rule on a value the agent typed, applied by code whatever the model
    wrote: each dash used as a separator (an em dash, an en dash, a spaced hyphen) becomes a
    comma, or nothing at the end of a line, by the documents module's own rule
    (`documents.rules.separators`); "full-stack" and a range "2019-2021" keep their hyphen."""
    found = doc_rules.separators(text)
    return doc_rules.apply_separators(text, found) if found else text


def _issues(text: str, sources: factcheck_rules.Sources) -> list[str]:
    return sorted({issue.code for issue in factcheck_rules.check(text, sources)})


def _judge_one(question: Question, proposal: Proposal, sources: factcheck_rules.Sources) -> Verdict:
    key, value = question.key, proposal.value
    if refused := refusal(question.label, question.kind, question.options, question.reason):
        return Verdict(key, None, refused)
    name = _named_only(_plain(question.label))
    if name is not None and not _names_the_company(name, sources.participants):
        return Verdict(key, None, "off_topic")
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
    value = undashed(value)
    if len(value) > VALUE_MAX:
        return Verdict(key, None, "too_long")
    if _bare_yes_or_no(value):
        return Verdict(key, None, "yes_or_no")
    if _bare_negation(value):
        return Verdict(key, None, "bare_negation")
    plain = _plain(value)
    if _STATEMENT.search(plain):
        return Verdict(key, None, "legal_statement")
    if _PERSONAL_STATEMENT.search(plain):
        return Verdict(key, None, "personal_statement")
    if _PRACTICAL_STATEMENT.search(plain):
        return Verdict(key, None, "practical_statement")
    if _issues(value, sources):
        return Verdict(key, None, "fact_check")
    return Verdict(key, answers.Answer(value, source), "accepted")


def judge(
    questions: Sequence[Question], reply: Reply, sources: factcheck_rules.Sources
) -> list[Verdict]:
    """One verdict per question asked, in their order: a question the reply does not answer, or
    answers twice, stays left; a proposal for a question not asked is ignored. The `source` of a
    proposal is declared by the model and checked only by `factcheck_rules` (see the module
    docstring), not against the profile text."""
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
