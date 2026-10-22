"""The answer agent's boundaries (SPEC-apply, A5).

`eligible` asks only unresolved, open job questions in `AGENT_REASONS`, once per key. It leaves
legal choices, EEO, identity, pay, authorization, employment history, practicalities and closed
questions with the person. Standard fields and document boxes belong to the resolver;
`letter_boxes` copies a prepared document, never a model's substitute. `profile_for` sends an
allowlist of profile sections and answer fields, excluding private practical answers.

Question topics are positive anchors: motivation, the job/company/team, experience and technical
projects. A destination after working/living/joining is unknown unless it names a recognised
professional field; a qualified team is likewise unknown unless its qualifier names a field.
This does not depend on a place gazetteer. Technical noun phrases such as Microsoft Office,
location service and exit strategy retain their ordinary reading. Team preference and Why now
can ask for job motivation, while their proposed answers still need a supported job claim.

`judge` repeats the question guards, checks the declared source against PERSON_SOURCES, matches
choices to options and applies the legal, personal, practical and fact-check guards. A model's
source label alone establishes nothing. Each typed clause must either match permitted source
text or have a positive job-claim structure whose content words trace to those sources or the
job-topic vocabulary. This allows supported technical paraphrases without inventing arbitrary
new nouns, places or arrangements. Clauses joined by conjunctions are checked separately, so a
job sentence cannot carry an unsupported second claim. Ambiguous arrangement vocabulary needs
a complete sourced technical past action; consent and other closed statements stay refused.
The prepared letter enters these sources through the immutable run context, rather than an
edited answer. `undashed` applies the owner's separator rule before matching and checking text.
Fact checking remains independent; Full auto's model check still applies to the resulting text.

The runtime call is an internal, private_io AnswerAgent with TOKEN_BUDGET. Posting and page text
remain data. No credential or agent means these questions stay left.

Declared open points:
- Unrecognised professional fields or team qualifiers can leave a valid question with the person.
- New technical terms and paraphrases outside the positive vocabulary need supporting profile
  or prepared-letter words. Non-Latin wording without such support is conservative as well.
- Token provenance and topic structure do not prove the semantic relationship of every
  paraphrase. The fact check, approval and Full auto model check remain separate checks.
- Short company questions still need the posting's company at the judge. Existing lexical
  guards can refuse technical words whose ordinary reading is not recognised here.
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
# These positive structures describe work, rather than the person's arrangements. Ambiguous
# labels can reach the judge, which still requires each proposed claim to trace to sources.
_FACT_QUESTION = re.compile(
    r"\b(?:projects?|software|services?|algorithms?|strateg(?:y|ies)|integrations?|systems?"
    r"|tools?|technolog\w*|microsoft office|projets?|logiciels?)\b"
)
_FIELD_TOPIC = re.compile(
    r"^(?:robotics|ai|artificial intelligence|software|engineering|research|this field"
    r"|this industry|this sector|this domain|robotique|informatique)\b"
)
_PURPOSE_ACTION = r"(?:build|design|develop|create|improve|reach)"
_PURPOSE = re.compile(rf"^{_PURPOSE_ACTION}\b")
_WORK_PURPOSE = re.compile(rf"\bwork to {_PURPOSE_ACTION}\b")
_TEAM_DETAIL = re.compile(r"\b(?:our|your|notre|votre) (.+?) (?:team|equipe)\b")
_SHORT_TOPICS = frozenset({"team preference", "why now", "pourquoi maintenant"})
_TECHNICAL_TEXT = re.compile(
    r"\bmicrosoft office\b|\blocation services?\b|\bexit strateg(?:y|ies)\b"
    r"|\bfull-? ?time (?:(?:\w+ )?projects?|algorithms?|software)\b"
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
    """A working/living destination is unknown unless it names an allowed professional field.
    This depends on the sentence structure, rather than whether a gazetteer knows the place."""
    found = _WHERE.search(text)
    if found and text[: found.start(1)].endswith(" to ") and _PURPOSE.search(found.group(1)):
        return False
    return bool(found and not _FIELD_TOPIC.search(found.group(1)))


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
    if (
        text.strip(" ?.!:") in _SHORT_TOPICS
        or _SUBJECT.search(text)
        or (_EMPLOYER.search(text) and _MOTIVATION.search(text))
    ):
        return None
    found = _WHY_NAME.search(text)
    return found.group(1) if found else None


def _not_an_employer(text: str) -> bool:
    """A "why <name>" whose name is a place of the gazetteer or a time or a move ("Why Berlin?",
    "Why the US?", "Why now?"). "Why us?" never gets here: "us" is an employer word."""
    name = _named_only(text)
    return name is not None and bool(places_of(name) or _NOT_A_NAME.search(name))


def _on_topic(text: str) -> bool:
    return (
        text.strip(" ?.!:") in _SHORT_TOPICS
        or _named_only(text) is not None
        or bool(_WORK_PURPOSE.search(text))
        or bool(_SUBJECT.search(text) or (_EMPLOYER.search(text) and _MOTIVATION.search(text)))
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
    fact_question = bool(_FACT_QUESTION.search(text))
    for code, refuses in REFUSALS:
        reading = _TECHNICAL_TEXT.sub("project", text) if code in ("practical", "history") else text
        if refuses(reading, kind, options):
            return code
    if _MOTIVATION.search(text) and not fact_question:
        team = _TEAM_DETAIL.search(text)
        if _names_a_place(label) or (team and not _FIELD_TOPIC.fullmatch(team.group(1))):
            return "practical"
    if reason != "free_text_for_the_agent" and not _OPEN.search(text) and text not in _SHORT_TOPICS:
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
    sections: Mapping[str, Any], posting_title: str, company: str, letter_text: str = ""
) -> factcheck_rules.Sources:
    """What an answer may claim: the person's own profile (facts, experience, answers), its
    employers, schools and titles. The posting legitimises its own title and company only."""
    view = profile_view.build(profile_for(sections), 0)
    own = [body for _, body in view.facts_blocks]
    own += [f"{e.title} {e.organisation} {e.start or ''} {e.end or ''}" for e in view.employers]
    own += [f"{s.degree} {s.school} {s.start or ''} {s.end or ''}" for s in view.schools]
    if view.motivation:
        own.append(view.motivation)
    if letter_text:
        own.append(letter_text)
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


_CLAUSES = re.compile(r"[.!?;\n]+|\b(?:and|but|because|et|mais|car|parce que)\b", re.I)
_CLAIM_FRAME = re.compile(
    r"^(?:(?:nonetheless|notably) )?(?:i|we|my work|our work|your mission|your culture)\b"
    r"|^(?:robotics|robots?|agents?|software|ai|robotique|je|nous)\b"
)
_CLAIM_TOPIC = re.compile(
    r"\b(?:robot\w*|agents?|software|ai|evaluat\w*|build\w*|built|project\w*|projet\w*"
    r"|engineer\w*|develop\w*|design\w*|integrat\w*|algorith\w*|system\w*|tools?|work|problem\w*)\b"
)
# Grammar and neutral motivation words add no personal fact. Content words must belong to a
# supported job topic or the permitted sources; the model's source label is not proof.
_CLAIM_GLUE = frozenset(
    [
        "i",
        "we",
        "my",
        "our",
        "your",
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "me",
        "us",
        "you",
        "to",
        "of",
        "for",
        "with",
        "on",
        "in",
        "at",
        "from",
        "by",
        "as",
        "over",
        "into",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "have",
        "has",
        "had",
        "do",
        "did",
        "can",
        "could",
        "would",
        "will",
        "should",
        "want",
        "like",
        "love",
        "enjoy",
        "hope",
        "need",
        "bring",
        "make",
        "made",
        "help",
        "helps",
        "helped",
        "learn",
        "learned",
        "know",
        "use",
        "used",
        "using",
        "create",
        "created",
        "develop",
        "developed",
        "developing",
        "design",
        "designed",
        "fix",
        "fixed",
        "improve",
        "improved",
        "ship",
        "ships",
        "shipping",
        "work",
        "working",
        "worked",
        "join",
        "joined",
        "build",
        "built",
        "building",
        "lead",
        "led",
        "evaluate",
        "evaluated",
        "evaluation",
        "because",
        "and",
        "but",
        "so",
        "what",
        "which",
        "how",
        "where",
        "more",
        "most",
        "real",
        "hard",
        "strong",
        "safe",
        "safety",
        "people",
        "human",
        "humans",
        "robots",
        "robotics",
        "team",
        "teams",
        "agent",
        "agents",
        "ai",
        "software",
        "operations",
        "warehouse",
        "field",
        "physical",
        "world",
        "interested",
        "interest",
        "excited",
        "excites",
        "nothing",
        "nonetheless",
        "notably",
        "nobody",
        "before",
        "about",
        "through",
        "reach",
        "culture",
        "mission",
        "je",
        "nous",
        "mon",
        "ma",
        "mes",
        "notre",
        "votre",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "aux",
        "pour",
        "avec",
        "dans",
        "sur",
        "aime",
        "aimer",
        "construire",
        "developper",
        "concevoir",
        "robots",
        "robotique",
        "travail",
        "projet",
        "projets",
    ]
)


def _supported_claims(value: str, sources: factcheck_rules.Sources, *, exact: bool = False) -> bool:
    texts = (
        *sources.profile,
        *sources.answers,
        *sources.known_terms,
        *sources.participants,
        *sources.titles,
        *sources.employers,
        *sources.schools,
    )
    clauses = [answers.option_key(c) for c in _CLAUSES.split(value) if answers.option_key(c)]
    stored = {answers.option_key(c) for text in texts for c in _CLAUSES.split(undashed(text))}
    words = {w for text in texts for w in answers.option_key(text).split()}
    for clause in clauses:
        if clause in stored:
            continue
        if exact:
            return False
        if not _CLAIM_FRAME.search(clause) or not _CLAIM_TOPIC.search(clause):
            return False
        if any(
            w not in words and w not in _CLAIM_GLUE and not _SUBJECT.fullmatch(w)
            for w in clause.split()
        ):
            return False
    return bool(clauses)


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
    picked = None
    if question.kind in ("select", "combobox"):
        picked = answers.match_option(value, question.options)
        if picked is None:
            return Verdict(key, None, "no_matching_option")
        value = picked
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
    if _CONSENT.search(plain):
        return Verdict(key, None, "legal_statement")
    supported = _supported_claims(value, sources)
    if _PRACTICAL_STATEMENT.search(plain):
        return Verdict(key, None, "practical_statement")
    # Ambiguous arrangement words need an actual technical fact, rather than a new preference
    # assembled from individually known words. Recognised technical noun phrases retain their
    # ordinary reading; other overlaps are allowed only as a complete sourced past action.
    reading = _TECHNICAL_TEXT.sub("project", plain)
    team = _TEAM_DETAIL.search(plain)
    practical = (
        _PRACTICAL.search(reading)
        or _names_where(reading)
        or (team and not _FIELD_TOPIC.fullmatch(team.group(1)))
    )
    if practical and not (
        re.match(r"^(?:i|we) (?:built|developed|designed|created|implemented|led)\b", plain)
        and _FACT_QUESTION.search(plain)
        and _supported_claims(value, sources, exact=True)
    ):
        return Verdict(key, None, "practical_statement")
    if _issues(value, sources):
        return Verdict(key, None, "fact_check")
    if not supported:
        return Verdict(key, None, "unsupported_claim")
    return Verdict(key, answers.Answer(value, source, picked), "accepted")


def judge(
    questions: Sequence[Question], reply: Reply, sources: factcheck_rules.Sources
) -> list[Verdict]:
    """One verdict per question asked, in their order: a question the reply does not answer, or
    answers twice, stays left; a proposal for a question not asked is ignored. Topic structure,
    provenance and fact checking apply regardless of the model's declared `source`."""
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
