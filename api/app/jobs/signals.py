"""What a posting's own text says before the person clicks (owner, 2026-11-07, "build all" of the
career-ops analysis; SPEC-jobs, "Before the click"): pure functions of the text, no profile, no
database, linear in the text's length.

- `read(text)`: the eligibility signals, each with the posting's own sentence quoted:
  `no_sponsorship` (it does not sponsor a visa or a work permit), `sponsorship` (it does),
  `work_permit` (it requires the right to work in a place, never one it sponsors or arranges;
  `value` the place, a name of `app.apply.answers.COUNTRIES`, one signal a place), `degree` (it
  requires a degree), `contractor` (independent contractor, 1099, auto-entrepreneur, portage
  salarial), `fixed_term`, `unpaid`, `candidate_fee` (the candidate pays), and `closing` (the
  closing date it states, `value` its ISO date).
- `closing(text)`: that closing date and its sentence, or None.
- `fingerprint(text)`: a 64-bit SimHash of the text's distinct word pairs (a signed bigint), or
  None for a text too short to tell; `same_text(a, b)` for two fingerprints at most SAME_BITS
  apart (career-ops's cross-listing check: 92 % similar, at most 5 of 64 bits differ).
- `stored(text)`: the three columns a store or a rescore writes (`signals`, `text_fingerprint`,
  `closes_on`, migration 0038).

Rules are allow lists of English and French sentences, matched one sentence at a time on its
words (folded as `never_apply.fold` folds, then every sign but letters, digits and "+" read as a
space). A sentence a rule does not recognise gives nothing: silence is the absence of a signal,
never a claim. A rule that offers or requires something skips a sentence that negates it, and a
closing date is read only in an unambiguous form with its year.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.apply.answers import COUNTRIES
from app.jobs.never_apply import fold

TEXT_MAX = 200_000  # characters read; a longer posting is read up to here
SENTENCE_MAX = 1_000  # words a single sentence is matched on
QUOTE_MAX = 240
SHINGLE_WORDS = 2
SHINGLES_MIN = 40  # fewer distinct shingles: no fingerprint, never compared
SAME_BITS = 5
_MASK = (1 << 64) - 1


@dataclass(frozen=True)
class Signal:
    code: str
    quote: str
    value: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "quote": self.quote, "value": self.value}


# --- sentences ----------------------------------------------------------------------------------

_US = re.compile(r"\bU\.S\.(?:A\.)?|\bU\.S\b|\bUSA\b|\bUS\b")
_LINES = re.compile("[\r\n\u2028\u2029\u2022\u00b7\u25aa\u25cf\u25e6]+")
_STOPS = re.compile(r"(?<=[.!?;])\s+(?=[\"“«(A-ZÀ-Ý])")
_BULLET = re.compile("^[\\s*>#\\-\u2010-\u2015]+")
_SPACES = re.compile(r"\s+")
_NOT_WORD = re.compile(r"[^a-z0-9+]+")


def _sentences(text: str | None) -> list[str]:
    """The text's sentences as written: one per line or bullet, split again after . ! ? or ;
    when a capital follows (so "Ph.D. required" stays one)."""
    found = []
    for line in _LINES.split((text or "")[:TEXT_MAX]):
        for part in _STOPS.split(line):
            sentence = _SPACES.sub(" ", _BULLET.sub("", part)).strip()
            if sentence:
                found.append(sentence)
    return found


def words(sentence: str) -> str:
    """A sentence as the rules read it: "Must hold the U.S. right-to-work" reads
    "must hold the usa right to work"; "n'offrons" reads "n offrons"."""
    folded = fold(_US.sub(" USA ", sentence))
    return " ".join(_NOT_WORD.sub(" ", folded).split()[:SENTENCE_MAX])


def _quote(sentence: str) -> str:
    return sentence if len(sentence) <= QUOTE_MAX else sentence[: QUOTE_MAX - 1].rstrip() + "…"


# --- places -------------------------------------------------------------------------------------

_PLACE_OF = {phrase: name for name, phrases in COUNTRIES.items() for phrase in phrases}
_PLACES = "|".join(re.escape(p) for p in sorted(_PLACE_OF, key=len, reverse=True))
_PLACE = re.compile(rf"\b(?:{_PLACES})\b")


def place_of(text: str | None) -> str | None:
    """The country or region a text names, as a name of COUNTRIES ("UK", "Royaume-Uni" and "the
    United Kingdom" read "United Kingdom"), or None when it names none or several. Phrases are
    read longest first, so "Latin America" is never the "America" inside it."""
    said = re.sub(r"^(?:the|l|le|la|les) ", "", words(text or ""))
    if said in _PLACE_OF:
        return _PLACE_OF[said]
    named = {_PLACE_OF[phrase] for phrase in _PLACE.findall(said)}
    return named.pop() if len(named) == 1 else None


# --- the rules ----------------------------------------------------------------------------------

# "do not", "cannot", "won't" ... before a verb of sponsoring.
_NEG = (
    r"(?:cannot|can not|can t|do not|don t|does not|doesn t|will not|won t|would not|are not"
    r"|is not|unable to|not able to|not in a position to|no longer)"
)
_SPONSOR_OBJECT = (
    r"(?:visa|visas|sponsorship|work permit|work permits|work authorization|work authorisation"
    r"|immigration|h1b|h 1b)"
)
_FR_SPONSOR_OBJECT = (
    r"(?:visa|visas|parrainage|sponsoring|sponsorisation|permis de travail|titre de sejour)"
)
_FR_SPONSOR_VERB = (
    r"(?:parrainer|sponsoriser|parrainons|sponsorisons|prendre en charge|prenons en charge"
    r"|accompagner|accompagnons|proposer|proposons|offrir|offrons|fournir|fournissons|assurer"
    r"|assurons)"
)
# The words a negation may stand apart from its verb by, and the verb from what it sponsors: allow
# lists, so "can't wait to sponsor" or "don't just sponsor" refuse nothing.
_NEG_GAP = (
    r"(?: (?:currently|presently|now|yet|unfortunately|sadly|legally|generally|typically|usually"
    r"|be|able|to|in|a|position|at|this|the|moment|time|present|stage|for|role|ever|any|longer"
    r"|offer|provide|support))"
)
_OBJECT_GAP = (
    r"(?: (?:a|an|the|any|your|our|their|new|international|foreign|overseas|non|eu|uk|usa"
    r"|employment|employee|employees|applicants|applicant|candidates|candidate|hires|anyone"
    r"|people|staff|workers|worker|for|or|nor|and|kind|kinds|type|types|form|forms|of|sort|this"
    r"|these|role|roles|position|positions|work|transfer|transfers|renew|renewal|renewals"
    r"|relocation|assistance|with|skilled|temporary|student|tier|2|at|time|now|currently))"
)
_FR_AUX_GAP = (
    r"(?: (?:sommes|serons|sera|seront|est|etait|sont|pouvons|pourrons|pourrions|pourra"
    r"|pourront|pourrait|peut|peuvent|allons|va|vont|souhaitons|prevoyons|malheureusement"
    r"|actuellement|en|y|l|le|la|les|vous|leur|lui))"
)
_FR_OBJECT_GAP = (
    r"(?: (?:de|d|du|des|un|une|le|la|les|l|votre|vos|leur|leurs|aucun|aucune|demande|demandes"
    r"|demarche|demarches|procedure|procedures|frais|dossier|pour|ce|cette|poste|en|matiere"
    r"|nouveaux|nouvelles|candidats|candidates|salaries|collaborateurs))"
)
NO_SPONSORSHIP = (
    rf"\b{_NEG}{_NEG_GAP}{{0,4}}? (?:sponsor|sponsoring|support|supporting|provide|providing"
    rf"|offer|offering){_OBJECT_GAP}{{0,4}}? {_SPONSOR_OBJECT}\b",
    rf"\b{_NEG}{_NEG_GAP}{{0,3}}? (?:sponsor|sponsoring)\b",
    r"\b(?:visa |immigration |work permit |employment visa |h1b )?sponsorship (?:is |will |would "
    r"|can )?(?:not|n t|no longer) (?:be )?(?:available|offered|provided|possible|supported"
    r"|considered)\b",
    r"\bsponsorship (?:is )?unavailable\b",
    r"\bno (?:visa |immigration |work permit )?sponsorship\b(?! (?:is |will be )?(?:needed"
    r"|required|necessary))",
    r"\bwithout (?:the need for |the need of |needing |requiring |requiring any |need for |any "
    r"|any need for )?(?:current or future |future |present or future )?(?:visa |immigration "
    r"|employer |company |work permit |employment visa |h1b )?sponsorship\b",
    r"\b(?:will|do|does|must|should) not (?:now or in the future |currently |now |presently )?"
    r"(?:require|need)(?: [a-z]+){0,3}? sponsorship\b",
    rf"\b(?:ne|n){_FR_AUX_GAP}{{0,3}} (?:pas|plus) (?:en mesure de |la possibilite de "
    rf"|possible de )?(?:de )?{_FR_SPONSOR_VERB}{_FR_OBJECT_GAP}{{0,4}}? {_FR_SPONSOR_OBJECT}\b",
    rf"\b(?:ne|n) {_FR_SPONSOR_VERB} (?:pas|aucun|aucune|plus){_FR_OBJECT_GAP}{{0,4}}? "
    rf"{_FR_SPONSOR_OBJECT}\b",
    r"\b(?:pas de|aucun|aucune|sans) (?:possibilite de )?(?:parrainage|sponsoring|sponsorisation)"
    r"(?: de| d un| pour| pour le| pour un)?(?: visa| visas| permis de travail)?\b"
    r"(?! (?:necessaire|requis|requise|besoin))",
    r"\b(?:parrainage|sponsoring|sponsorisation)(?: de| d un)?(?: visa| visas| permis de travail)?"
    r" (?:n est |ne sera |ne peut etre )?(?:pas|non) (?:possible|propose|proposee|disponible"
    r"|assure|assuree|pris en charge|prise en charge|envisageable)\b",
)
SPONSORSHIP = (
    r"\b(?:visa |immigration |work permit |employment visa )?sponsorship (?:is |will be |can be "
    r"|may be |are )?(?:available|offered|provided|possible|supported)\b",
    r"\b(?:we|we re|we are|company|employer) (?:can |will |do |are able to |are happy to |also "
    r"|gladly |happily )?(?:sponsor|sponsors|support)(?: [a-z]+){0,2}? (?:visa|visas"
    r"|work permit|work permits|visa applications)\b",
    r"\b(?:offer|offers|offering|provide|provides|providing|include|includes|including)"
    r"(?: [a-z]+){0,3}? (?:visa sponsorship|visa support|work permit support|immigration support"
    r"|sponsorship for (?:a |your )?(?:visa|work permit))\b",
    r"^(?:visa|immigration|work permit) (?:sponsorship|support)(?: (?:and|plus|\+) relocation"
    r"(?: support| package| assistance)?)?$",
    r"^relocation (?:and|plus|\+) (?:visa|immigration) (?:sponsorship|support)$",
    r"\b(?:parrainage|sponsoring|sponsorisation|accompagnement|prise en charge)(?: de| du| des"
    r"| pour| pour le| pour la| a la| au| dans les| dans vos)?(?: (?:demande|demandes|demarches"
    r"|procedure|procedures))?(?: de| d)?(?: visa| visas| permis de travail| titre de sejour"
    r"| immigration)(?: [a-z]+){0,2}? (?:possible|propose|proposee|disponible|assure|assuree"
    r"|inclus|incluse|offert|offerte)\b",
    r"\bnous (?:pouvons |proposons de |sommes en mesure de )?(?:parrainer|sponsoriser|accompagner"
    r"|prendre en charge|parrainons|sponsorisons|accompagnons|prenons en charge)(?: [a-z]+){0,4}?"
    r" (?:visa|visas|permis de travail|titre de sejour)\b",
)
# The right as the person's state ("eligible to work"): naming it is asking for it.
_RIGHT_STATE = (
    r"(?:authorized to work|authorised to work|eligible to work|entitled to work|permitted to work"
    r"|legally able to work|legally work|autorise a travailler|autorisee a travailler"
    r"|autorises a travailler|autorisees a travailler)"
)
# The right as a thing held or given ("a work permit"): asked only by requirement wording.
_RIGHT_THING = (
    r"(?:right to work|rights to work|right to live and work|authorization to work"
    r"|authorisation to work|work authorization|work authorisation|eligibility to work"
    r"|entitlement to work|permission to work|work permit|work permits|work visa|work visas"
    r"|autorisation de travail|autorisation de travailler|droit de travailler|droit au travail"
    r"|permis de travail|titre de sejour)"
)
_IN = r"(?:in|within|for|en|au|aux|dans|pour|sur le territoire)(?: (?:the|l|le|la|les|de|du|des))?"
WORK_PERMIT = (
    rf"\b(?:(?P<state>{_RIGHT_STATE})|{_RIGHT_THING})(?: [a-z0-9]+){{0,4}}? {_IN}"
    rf" (?P<place>{_PLACES})\b",
)
# Each further place of a list: "in the UK, Ireland or the EU".
_MORE_PLACE = re.compile(
    rf" (?:(?:or|and|ou|et|and or|as well as)(?: {_IN})? )?(?:(?:the|l|le|la|les) )?"
    rf"(?P<place>{_PLACES})\b"
)
# The wording that requires the right, an allow list.
_REQUIRED = re.compile(
    r"\b(?:must|required|require|requires|requirement|requirements|mandatory|essential"
    r"|have to have|has to have|have to hold|has to hold|need to have|needs to have|need to hold"
    r"|needs to hold|need you to have|need you to hold|expect you to have|expect you to hold"
    r"|valid|only (?:candidates|applicants|people|persons|individuals|those"
    r"|residents|citizens|nationals) (?:with|who|holding)|devez|devrez|devra|doit|doivent|requis"
    r"|requise|requises|exige|exigee|exiges|exigees|obligatoire|obligatoires|indispensable"
    r"|indispensables|necessaire|necessaires|valide|valides|titulaire)\b"
)
_HOLDS = re.compile(
    r"(?<!\bif )(?<!\bwhether )\b(?:you|candidates|applicants|vous)(?: will| ll| would| already)?"
    r" (?:need|have|hold|possess|avez|disposez|possedez|detenez)(?: (?:a|an|the|full|valid"
    rf"|current|existing|proof|of|your|own|d|un|une|le|la|l|des|du|de)){{0,4}} {_RIGHT_THING}\b"
)
# "Right to work in the UK" alone: a list item that can only ask.
_BARE = re.compile(
    r"^(?:(?:a|the|full|valid|current|proven|legal|le|un) ){0,3}(?=right to work|rights to work"
    r"|right to live and work|droit de travailler|droit au travail)"
)
# A requirement the sentence negates: "No work permit for Germany is required."
_NOT_ASKED = re.compile(
    r"\b(?:(?:not|t|never|no longer) (?:be )?(?:required|needed|necessary|mandatory|essential"
    r"|a requirement)|no need|need not|needn t|(?:don t|do not|doesn t|does not|won t|will not"
    r"|not) (?:need|have to|require)(?! (?:any |visa |immigration |employer |company |future "
    r"|current ){0,3}(?:sponsorship|sponsoring|parrainage|sponsorisation)\b)|pas besoin"
    r"|pas necessaire|pas requis|pas requise|pas obligatoire|pas exige|pas exigee"
    r"|pas indispensable|non requis|non requise|non necessaire|non obligatoire|non exige"
    r"|non exigee)\b"
)
_NO_RIGHT = re.compile(
    r"\b(?:no|aucun|aucune|pas de|pas d)(?: (?:visa|visas|or|and|special|separate|additional"
    r"|other|specific|prior|existing|local|valid|current|ou|et|de|d|un|une|le|la|l)){0,3}"
    rf" {_RIGHT_THING}\b"
)
# The employer gives the permit: a right it sponsors, handles or arranges is not asked of anyone.
_COMPANY = (
    r"(?:we|we ll|we d|we re|nous|the company|our company|the employer|our employer"
    r"|the business|our client|the client|our team|the team|our hr team|our people team)"
)
_COMPANY_GAP = (
    r"(?: (?:will|can|could|would|may|shall|do|does|also|gladly|happily|fully|then|always|are"
    r"|is|be|able|to|happy|glad|pleased|ready|willing|offer|help|pouvons|allons|savons|proposons"
    r"|de|sommes|en|mesure|pourrons|nous|vous|aussi|egalement))"
)
_GIVE = (
    r"(?:sponsor|sponsors|sponsoring|provide|provides|providing|arrange|arranges|arranging"
    r"|handle|handles|handling|take care of|takes care of|taking care of|cover|covers|covering"
    r"|organise|organises|organize|organizes|organising|organizing|offer|offers|offering|secure"
    r"|secures|securing|apply for|applies for|file|files|manage|manages|managing|pay for"
    r"|pays for|process|processes|processing|ensure|make sure|get you|sort out|sorts out"
    r"|prendre en charge|prenons en charge|prend en charge|prennent en charge|parrainer"
    r"|parrainons|parraine|sponsoriser|sponsorisons|sponsorise|fournir|fournissons|fournit"
    r"|offrir|offrons|offre|proposer|proposons|propose|gerer|gerons|gere|organiser|organisons"
    r"|organise|occupons|occupe|occuper|assurer|assurons|assure|obtenir|obtenons|faire|faisons"
    r"|fait|obtain|obtains|obtaining)"
)
_GIVEN_GAP = (
    r"(?: (?:a|an|the|your|our|their|any|all|full|every|necessary|for|you|with|both|and|uk|us|eu"
    r"|local|new|successful|candidate|candidates|hire|hires|de|d|du|des|votre|vos|un|une|le|la"
    r"|les|l|ton|ta|tes))"
)
_PERMIT = (
    rf"(?:{_RIGHT_THING}|visa|visas|sponsorship|immigration|permit|permits|paperwork"
    r"|parrainage)"
)
_GIVES = re.compile(
    rf"\b{_COMPANY}{_COMPANY_GAP}{{0,4}} (?:{_GIVE}{_GIVEN_GAP}{{0,4}} {_PERMIT}|sponsor|sponsors"
    r"|sponsoring|parrainer|parrainons|parraine|sponsoriser|sponsorisons|sponsorise)\b"
    # The employer by its name: "Example Robotics will handle your work permit".
    r"|(?<!\byou )(?<!\bcandidates )(?<!\bcandidate )(?<!\bapplicants )(?<!\bapplicant )"
    r"(?<!\bthey )(?<!\bhires )(?<!\bemployees )\b(?:will|can|ll|shall|is able to|are able to"
    rf"|is happy to|are happy to)(?: (?:gladly|happily|also|fully))? {_GIVE}{_GIVEN_GAP}{{0,4}}"
    rf" {_PERMIT}\b"
    rf"|\b(?:visa|visas|work permit|work permits|work visa|work visas|immigration)"
    r" (?:sponsorship|sponsoring)\b"
    rf"|\bsponsorship (?:of|for) (?:(?:your|a|an|the) )?(?:{_RIGHT_THING}|visa|visas)\b"
    rf"|\b(?:sponsored|company sponsored|employer sponsored) (?:{_RIGHT_THING}|visa|visas)\b"
    r"|\b(?:parrainage|sponsoring|sponsorisation|prise en charge) (?:(?:de|du|d|des|votre|vos"
    rf"|un|une) ){{0,3}}(?:{_RIGHT_THING}|visa|visas)\b"
)
# Elsewhere in the sentence, the employer gives the right it names: "we arrange it", "which we
# can provide", "sponsored by the company" (a person as the agent is a requirement).
_GIVEN_ELSEWHERE = re.compile(
    rf"\b{_COMPANY}{_COMPANY_GAP}{{0,4}} {_GIVE}(?: (?:it|them|this|that|one))?(?: for you)?$"
    rf"|\b{_COMPANY}{_COMPANY_GAP}{{0,4}} {_GIVE} (?:it|them|this|that|one)\b"
    rf"|\b(?:nous|l entreprise|la societe|l employeur){_COMPANY_GAP}{{0,4}}"
    r" (?:le|la|les|l|en|nous en|en)(?: (?:nous|vous))? (?:chargeons|charge|occupons|occupe"
    r"|prenons en charge|prend en charge|fournissons|fournit|parrainons|parraine|gerons|gere"
    r"|organisons|organise)\b"
    rf"|\b(?:which|that|qui|que|qu)(?: {_COMPANY}| our [a-z]+ team| our [a-z]+){_COMPANY_GAP}"
    rf"{{0,4}} {_GIVE}\b"
    r"|\b(?:sponsored|provided|arranged|handled|covered|organised|organized|obtained|secured"
    r"|processed|managed|offered|paid for|taken care of|pris en charge|prise en charge|fourni"
    r"|fournie|fournis|fournies|parraine|parrainee|parraines|parrainees|organise|organisee"
    r"|gere|geree|assure|assuree) (?:by|par)"
    r" (?!(?:you|yourself|the candidate|candidate|the candidates|candidates|the applicant"
    r"|applicant|the applicants|applicants|the employee|employees|the hire|vous|le candidat"
    r"|la candidate|les candidats|vos soins|toi)\b)"
)
# Right after the place: "Work permit for Germany provided."
_GIVEN = re.compile(
    r" (?:(?:is|are|will be|can be|may be|would be|could be|gets|get) )?(?:provided|sponsored"
    r"|arranged|handled|covered|offered|organised|organized|included|paid for|taken care of"
    r"|sorted|fourni|fournie|fournis|fournies|pris en charge|prise en charge|parraine"
    r"|parrainee|offert|offerte|assure|assuree|organise|organisee|inclus|incluse)\b"
)
DEGREE = (
    r"\b(?:bachelor s degree|bachelors degree|bachelor degree|bachelor of|bachelor s in"
    r"|bachelors in|bachelor s or|bachelors or|master s degree|masters degree|master degree"
    r"|master of|master s in|masters in|master s or|masters or|msc|bsc|mba|phd|ph d|doctorate"
    r"|doctoral degree|university degree|college degree|academic degree|graduate degree"
    r"|undergraduate degree|engineering degree|degree in|degree from|degree level"
    r"|degree or equivalent|degree required|degree is required|(?<!to )a degree(?! of)"
    r"|relevant degree)\b",
    r"\b(?:bac ?\+ ?[2-8]|diplome d ingenieur|diplome d ecole d ingenieur|ecole d ingenieur"
    r"|diplome de niveau|diplome universitaire|diplome superieur|master 1|master 2|master en"
    r"|master de|master ou|doctorat|licence en|licence professionnelle|licence ou|niveau master"
    r"|niveau licence|grande ecole|ecole de commerce|formation superieure|dut ou bts|bts ou dut"
    r"|dut bts|bts dut|(?:niveau|type|formation) (?:dut|bts)|(?:dut|bts) (?:exige|exigee|requis"
    r"|requise|obligatoire|indispensable|minimum))\b",
)
# DUT and BTS are also "device under test" and "base transceiver station": a degree only in a
# sentence with a French word of diplomas.
_DUT_BTS = re.compile(r"\b(?:dut|bts)\b")
_FR_DIPLOMA = re.compile(
    r"\b(?:titulaire|diplome|diplomee|diplomes|diplomees|cursus|etudes|licence|ou equivalent)\b"
)
CONTRACTOR = (
    r"\b(?:independent contractor|independent contractors|1099 (?:contractor|contract|position"
    r"|role|basis|employee|only)|on a 1099|w2 or 1099|w 2 or 1099|corp to corp|c2c"
    r"|contractor basis|contract basis|as a contractor|on a freelance basis|freelance basis"
    r"|auto entrepreneur|auto entrepreneurs|micro entrepreneur|statut d independant"
    r"|statut independant|statut de freelance|statut freelance|en tant qu independant"
    r"|en tant que freelance|portage salarial)\b",
    # "En freelance" of the role, never "avec des développeurs en freelance".
    r"(?:^|\b(?:mission|missions|poste|contrat|role|prestation|intervention|collaboration"
    r"|travail|travailler|travaillerez|exercer|exercerez|intervenir|interviendrez|recrutement"
    r"|recrutons|propose|proposee|ouvert|ouverte|uniquement|exclusivement) )en (?:freelance"
    r"|portage)\b",
)
_MONTHS_SPAN = r"(?:[1-9]|1[0-9]|2[0-4]|three|six|nine|twelve|eighteen)"
FIXED_TERM = (
    rf"\b(?:fixed term|temporary contract|temporary position|temporary role|temporary assignment"
    rf"|temp to perm|maternity cover|maternity leave cover|parental leave cover"
    rf"|{_MONTHS_SPAN} months? (?:contract|fixed term|assignment|mission|engagement)"
    rf"|contract (?:of|for) {_MONTHS_SPAN} months)\b",
    r"\b(?:cdd|contrat a duree determinee|duree determinee|contrat de (?:[1-9]|1[0-9]|2[0-4])"
    r" mois|mission de (?:[1-9]|1[0-9]|2[0-4]) mois|mission d interim|(?<!the )interim"
    r"|remplacement (?:d un |de )?conge (?:maternite|parental)|(?:[1-9]|1[0-9]|2[0-4]) mois"
    r" renouvelable)\b",
)
UNPAID = (
    r"\b(?:unpaid (?:internship|internships|position|role|placement|opportunity|work|trial"
    r"|volunteer)|(?:role|position|internship|placement|opportunity|this) is unpaid"
    r"|volunteer (?:position|role|opportunity|basis)|on a volunteer basis|on a voluntary basis"
    r"|commission only|commission based only)\b",
)
UNPAID_FR = (
    r"\b(?:stage|poste|mission|emploi|travail|contrat)(?: [a-z]+){0,2}? (?:non|pas) remuner"
    r"(?:e|ee|es|ees)\b",
    r"\b(?:poste|mission|role) (?:de )?benevole\b",
)
CANDIDATE_FEE = (
    r"\b(?:you|candidates|candidate|applicants|applicant|trainees|participants) (?:will |must "
    r"|need to |have to |will need to |will have to |are required to |would need to "
    r"|may need to )?pay (?:(?:a |an |the "
    r"|your )?(?:[a-z]+ )?(?:fee|fees|deposit)|for (?:the |your )?(?:own )?(?:training|course"
    r"|certification|registration))\b",
    r"\b(?:training|registration|application|enrolment|enrollment|onboarding|processing"
    r"|certification|starter kit|administration|admin|background check) fees? (?:of|is|are"
    r"|applies|apply|required|payable|must be paid|to be paid)\b",
    r"\b(?:refundable|upfront|up front) (?:training |registration |application |enrolment "
    r"|enrollment |onboarding |starter kit |admin |administration )?(?:fee|fees)\b",
    r"\bfrais (?:de formation|d inscription|de dossier|de candidature|d adhesion"
    r"|de certification)(?: [a-z0-9]+){0,4}? (?:a la charge du (?:candidat|stagiaire"
    r"|participant)|payant|payants|s elevent|sont de|est de|obligatoire|obligatoires)\b",
    r"\b(?:formation|certification) payante\b",
    r"\ba la charge du candidat\b",
    r"\ble candidat (?:devra|doit) (?:payer|regler|financer)\b",
)

# A sentence that negates what a rule offers or requires gives no signal.
_NEGATED = re.compile(
    r"\b(?:not|no|never|cannot|without|unable|ne|n|pas|aucun|aucune|sans|non|jamais|t)\b"
)
# Help to get a permit, or a degree that is only a plus, is not a requirement.
# The employer helps: "obtain" alone is what a requirement asks ("candidates must obtain it").
_HELPED = re.compile(
    r"\b(?:help|helps|helping|assist|assists|assisting|assistance|support|supports|supporting"
    r"|guidance|aider|aidons|aidera|accompagner|accompagnons|accompagnement|demarches)\b"
)
_PREFERRED = re.compile(
    r"\b(?:a plus|is a plus|preferred|preferable|nice to have|bonus|ideally|souhaite|souhaitee"
    r"|apprecie|appreciee|un plus|idealement|serait un plus)\b"
)
_REASSURED = re.compile(r"\b(?:don t worry|do not worry|no worries|no problem|not a problem)\b")
_FEE_COVERED = re.compile(
    r"\b(?:free|waived|covered|reimbursed|refunded|we pay|we cover|company pays|paid by"
    r"|employer pays|gratuit|gratuite|offert|offerte|pris en charge|prise en charge|rembourse"
    r"|remboursee|rembourses)\b"
)


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


_NO_SPONSORSHIP = _compile(NO_SPONSORSHIP)
_SPONSORSHIP = _compile(SPONSORSHIP)
_WORK_PERMIT = _compile(WORK_PERMIT)
_DEGREE = _compile(DEGREE)
_CONTRACTOR = _compile(CONTRACTOR)
_FIXED_TERM = _compile(FIXED_TERM)
_UNPAID = _compile(UNPAID)
_UNPAID_FR = _compile(UNPAID_FR)
_CANDIDATE_FEE = _compile(CANDIDATE_FEE)


def _any(patterns: tuple[re.Pattern[str], ...], said: str) -> bool:
    return any(p.search(said) for p in patterns)


def _places(said: str, match: re.Match[str]) -> tuple[list[str], int]:
    """The places of a work permit match, every one of a list, and where the list ends."""
    named = [_PLACE_OF[match.group("place")]]
    end = match.end()
    while (more := _MORE_PLACE.match(said, end)) is not None:
        named.append(_PLACE_OF[more.group("place")])
        end = more.end()
    return named, end


def _rights(said: str, refused: bool) -> list[str]:
    """The places a sentence requires the right to work in: a state of the person ("eligible to
    work"), or a right with requirement wording ("must", "requis") or alone as a list item. None
    when the requirement is negated, when the employer helps, sponsors, gives or arranges it, or
    when the sentence reads as sponsorship (unless it refuses sponsorship)."""
    if _NOT_ASKED.search(said) or _NO_RIGHT.search(said) or _HELPED.search(said):
        return []
    if not refused and (
        _GIVES.search(said) or _GIVEN_ELSEWHERE.search(said) or _any(_SPONSORSHIP, said)
    ):
        return []
    required = bool(_REQUIRED.search(said) or _HOLDS.search(said))
    bare = _BARE.match(said)
    found: list[str] = []
    for match in _WORK_PERMIT[0].finditer(said):
        named, end = _places(said, match)
        if _GIVEN.match(said, end):
            return []
        alone = bare is not None and bare.end() == match.start() and end == len(said)
        if required or alone or match.group("state"):
            found.extend(named)
    return found


def _sentence_signals(said: str) -> list[tuple[str, str | None]]:
    """The codes one sentence's words give, with a value for work_permit."""
    found: list[tuple[str, str | None]] = []
    negated = bool(_NEGATED.search(said))
    refused = _any(_NO_SPONSORSHIP, said) and not _REASSURED.search(said)
    if refused:
        found.append(("no_sponsorship", None))
    elif not negated and _any(_SPONSORSHIP, said):
        found.append(("sponsorship", None))
    found.extend(("work_permit", place) for place in _rights(said, refused))
    degree = _any(_DEGREE, said) or bool(_DUT_BTS.search(said) and _FR_DIPLOMA.search(said))
    if not negated and not _PREFERRED.search(said) and degree:
        found.append(("degree", None))
    if not negated and _any(_CONTRACTOR, said):
        found.append(("contractor", None))
    if not negated and _any(_FIXED_TERM, said):
        found.append(("fixed_term", None))
    if (not negated and _any(_UNPAID, said)) or _any(_UNPAID_FR, said):
        found.append(("unpaid", None))
    if (
        not _FEE_COVERED.search(said)
        and not _NEGATED.search(said.replace("non refundable", "refundable"))
        and _any(_CANDIDATE_FEE, said)
    ):
        found.append(("candidate_fee", None))
    return found


def read(text: str | None) -> list[Signal]:
    """The signals of a posting's text, each code with its first sentence (a work permit once
    per place), then its closing date when it states one."""
    signals: list[Signal] = []
    seen: set[tuple[str, str | None]] = set()
    for sentence in _sentences(text):
        if sentence.endswith("?"):
            continue  # a question asks, it states nothing
        for code, value in _sentence_signals(words(sentence)):
            if (code, value) not in seen:
                seen.add((code, value))
                signals.append(Signal(code, _quote(sentence), value))
    closes = closing(text)
    if closes is not None:
        signals.append(Signal("closing", closes[1], closes[0].isoformat()))
    return signals


# --- the closing date ---------------------------------------------------------------------------

_MONTH_NUMBER = {
    **dict.fromkeys(("january", "jan", "janvier", "janv"), 1),
    **dict.fromkeys(("february", "feb", "fevrier", "fevr"), 2),
    **dict.fromkeys(("march", "mar", "mars"), 3),
    **dict.fromkeys(("april", "apr", "avril", "avr"), 4),
    **dict.fromkeys(("may", "mai"), 5),
    **dict.fromkeys(("june", "jun", "juin"), 6),
    **dict.fromkeys(("july", "jul", "juillet", "juil"), 7),
    **dict.fromkeys(("august", "aug", "aout"), 8),
    **dict.fromkeys(("september", "sep", "sept", "septembre"), 9),
    **dict.fromkeys(("october", "oct", "octobre"), 10),
    **dict.fromkeys(("november", "nov", "novembre"), 11),
    **dict.fromkeys(("december", "dec", "decembre"), 12),
}
_MONTH = "(?:" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + ")"
_DAYS = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun"
    r"|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
)
# A bare "closes", "deadline" or "open until" is the posting's only as a label at the start of
# its sentence: "The office closes on 24 December" is no closing date.
_POSTING = (
    r"(?:applications?|(?:this |the )?(?:job|posting|role|position|vacancy|advert|advertisement"
    r"|listing|opportunity|opening|offer|application window|application period|recruitment))"
)
_EN_MARKER = (
    rf"(?:closing date|application deadline|applications deadline|deadline for applications"
    rf"|deadline to apply|{_POSTING} (?:closes|close|closing|will close|is closing|is open until"
    rf"|are open until|open until|remains open until|are accepted until|accepted until)"
    rf"|accepting applications until|apply by|apply before|submit your application by"
    rf"|^(?:closes|closing|deadline|open until|submit by))"
)
_FR_MARKER = (
    r"(?:date limite de depot des candidatures|date limite de candidature|date limite des"
    r" candidatures|date limite de depot|date limite pour postuler|date de cloture des"
    r" candidatures|cloture des candidatures|candidatures ouvertes jusqu|candidatures acceptees"
    r" jusqu|candidatures jusqu|postulez avant|candidatez avant|postuler avant|envoyez votre"
    r" candidature avant|^(?:date limite|date de cloture|cloture))"
)
_LEAD = rf"(?: (?:is|on|by|the|of|for|le|au|a|est|fixee|fixe|until|jusqu|{_DAYS}))*"
_DATE = (
    r" (?:(?P<iy>20\d\d) (?P<im>\d{1,2}) (?P<id>\d{1,2})"
    r"|(?P<na>\d{1,2}) (?P<nb>\d{1,2}) (?P<ny>20\d\d)"
    rf"|(?P<dd>\d{{1,2}})(?: ?(?:st|nd|rd|th|er))?(?: of)? (?P<dm>{_MONTH}) (?P<dy>20\d\d)"
    rf"|(?P<mm>{_MONTH}) (?P<md>\d{{1,2}})(?: ?(?:st|nd|rd|th))? (?P<my>20\d\d))\b"
)
_CLOSING = re.compile(rf"\b(?:(?P<en>{_EN_MARKER})|(?P<fr>{_FR_MARKER})){_LEAD}{_DATE}")


def _day(year: str, month: int, day: str) -> date | None:
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _date_of(match: re.Match[str]) -> date | None:
    """The match's date; a numeric day and month in French order after a French marker, and
    after an English one only when the order cannot be mistaken (a part above 12, or both
    equal)."""
    if match.group("iy"):
        return _day(match.group("iy"), int(match.group("im")), match.group("id"))
    if match.group("dm"):
        return _day(match.group("dy"), _MONTH_NUMBER[match.group("dm")], match.group("dd"))
    if match.group("mm"):
        return _day(match.group("my"), _MONTH_NUMBER[match.group("mm")], match.group("md"))
    a, b = int(match.group("na")), int(match.group("nb"))
    if match.group("fr") or a == b or (a > 12 and b <= 12):
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    else:
        return None
    return _day(match.group("ny"), month, str(day))


def closing(text: str | None) -> tuple[date, str] | None:
    """The closing date the text states and its sentence. None when it states none, when a
    date after a marker cannot be read without guessing, or when two markers give two dates."""
    found: tuple[date, str] | None = None
    for sentence in _sentences(text):
        for match in _CLOSING.finditer(words(sentence)):
            day = _date_of(match)
            if day is None:
                return None
            if found is not None and found[0] != day:
                return None
            if found is None:
                found = (day, _quote(sentence))
    return found


# --- the fingerprint ----------------------------------------------------------------------------


def fingerprint(text: str | None) -> int | None:
    """The 64-bit SimHash of the text's distinct word pairs, as a signed bigint, or None when
    the text has fewer than SHINGLES_MIN of them."""
    tokens = words(" ".join(_sentences(text))).split()
    shingles = {
        " ".join(tokens[i : i + SHINGLE_WORDS]) for i in range(len(tokens) - SHINGLE_WORDS + 1)
    }
    if len(shingles) < SHINGLES_MIN:
        return None
    hashes = (hashlib.blake2b(s.encode(), digest_size=8).digest().hex() for s in shingles)
    # Each bit's column of the hashes, most significant first: set when most shingles set it.
    columns = zip(*(format(int(h, 16), "064b") for h in hashes), strict=True)
    value = int("".join("1" if 2 * c.count("1") > len(shingles) else "0" for c in columns), 2)
    return value - (1 << 64) if value >= 1 << 63 else value


def same_text(a: int | None, b: int | None) -> bool:
    """Whether two fingerprints are at most SAME_BITS apart (never for a missing one)."""
    return a is not None and b is not None and ((a ^ b) & _MASK).bit_count() <= SAME_BITS


# --- what a store writes ------------------------------------------------------------------------


def stored(text: str | None) -> dict[str, Any]:
    """The columns a posting's text gives (migration 0038): its signals, its fingerprint and
    its closing date."""
    signals = read(text)
    closes = next((s.value for s in signals if s.code == "closing"), None)
    return {
        "signals": [s.as_json() for s in signals],
        "text_fingerprint": fingerprint(text),
        "closes_on": date.fromisoformat(closes) if closes else None,
    }
