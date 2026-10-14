"""Which boxes of an application form the agent may tick
(SPEC-apply, "What the agent may never do").

Allowed, and nothing else: the employer's privacy notice for processing this application, and
"I confirm this information is accurate" (true by construction, every value has a source).
Every other box is the person's: arbitration, marketing, newsletters, talent communities,
retention beyond the application, background check authorisations, a general "I agree to the
terms", any other legal commitment. A label that mixes an allowed box with one of those is the
person's whole, and so is a label with any word outside the two boxes' own vocabulary. The
classifier reads English and French labels and never ticks on doubt.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class BoxKind(StrEnum):
    privacy = "privacy"
    accuracy = "accuracy"
    person = "person"


@dataclass(frozen=True)
class Box:
    kind: BoxKind
    reason: str

    @property
    def allowed(self) -> bool:
        return self.kind is not BoxKind.person


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.lower().replace("\u2019", "'"))
    return re.sub(r"\s+", " ", re.sub("[̀-ͯ]", "", plain)).strip()


# Anything here makes the box the person's, whatever else the label says.
_PERSON = re.compile(
    r"arbitra|marketing|newsletter|promotion|advertis|publicit|talent (?:community|pool|network)"
    r"|communaute|vivier|futur|other (?:job|role|position|opening|opportunit|vacanc)s?"
    r"|\bopenings?\b|opportunit|autres? (?:postes?|offres?|recrutements?)|recrutements"
    r"|retain|retention|retenu|conserv|keep my (?:data|information)|\d"
    r"|background|reference|verif|antecedent|criminal|drug|credit check|third part|tiers"
    r"|partenaires|affiliate|sms|text message|whatsapp|stay in touch|keep in touch|updates?\b"
    r"|receive (?:e-?mail|communication)|recevoir|share|partag|sensitive|sensible|biometric"
    r"|non-?compete|non-?disclosure|confidential\b|contract|contrat|binding|waive|release|indemn"
    r"|signature|\bterms?\b|\bconditions?\b|\bcgu\b|\bcgv\b|at-?will|transfer|transfert"
    r"|agreement|\baccord\b|cookie|employ|authori[sz]|consider|contact|\bany\b|\ball (?:my )?data"
)
_PRIVACY = re.compile(
    r"privacy (?:notice|policy|statement)|data protection (?:notice|policy)"
    r"|politique de (?:confidentialite|protection des donnees)|notice de confidentialite"
    r"|avis de confidentialite|processing of my (?:personal )?data|traitement de mes donnees"
)
_ACCURACY = re.compile(
    r"(?:information|details|answers?|data|statements?).{0,60}(?:accurate|correct|true|complete)"
    r"|(?:accurate|correct|true|truthful).{0,40}(?:information|details|answers?|best of my)"
    r"|(?:renseignements?|informations?|reponses?).{0,40}(?:exact|vrai|veridique|correct|sincere)"
)
# The whole label of an allowed box is made of these words: a word outside them is a clause the
# agent does not understand, and an unknown clause may be a commitment ("and to be considered for
# other openings", "and agree to the Candidate Agreement"), so the box is the person's.
_ALLOWED_WORDS = frozenset(
    word
    for line in (
        "i i've me my mine have has had read reviewed and understood understand acknowledge",
        "accept agree consent confirm certify declare attest hereby that the a an this these of to",
        "for in on with by is are was were be been all provided given submitted supplied",
        "privacy notice policy statement data protection personal processing process candidate",
        "applicant application recruitment role position job vacancy form herein above below",
        "information details answers answer statements accurate true correct complete truthful",
        "best knowledge belief gdpr rgpd accordance as described set out here link click please",
        "je j ai lu et compris pris connaissance de la le les l d du des politique",
        "confidentialite protection donnees personnelles a caractere personnel traitement mes",
        "ma mon pour cette ce candidature recrutement poste offre dans cadre conformement au",
        "accepte consens certifie atteste confirme declare que sont est informations",
        "renseignements reponses fournies fournis communiquees exactes exacts veridiques",
        "completes correctes sinceres sincere avoir relative relatif notice avis",
        "en matiere ici sur purpose purposes fins",
    )
    for word in line.split()
)


def classify(label: str) -> Box:
    """The verdict on one box. Only the two allowed kinds are ever ticked by the agent: the label
    must name one of them and say nothing else."""
    text = _fold(label)
    if not text:
        return Box(BoxKind.person, "an unnamed box is the person's")
    if _PERSON.search(text):
        return Box(BoxKind.person, "a legal commitment or consent beyond this application")
    words = re.findall(r"[a-z]+", text.replace("'", " "))
    if any(word not in _ALLOWED_WORDS for word in words):
        return Box(BoxKind.person, "a clause beyond the two boxes the agent may tick")
    if _PRIVACY.search(text):
        return Box(BoxKind.privacy, "the employer's privacy notice for this application")
    if _ACCURACY.search(text):
        return Box(BoxKind.accuracy, "the information is accurate")
    return Box(BoxKind.person, "not one of the two boxes the agent may tick")
