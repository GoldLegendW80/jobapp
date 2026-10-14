"""Which boxes of an application form the agent may tick
(SPEC-apply, "What the agent may never do").

Allowed, and nothing else: the employer's privacy notice for processing this application, and
"I confirm this information is accurate" (true by construction, every value has a source).
Every other box is the person's: arbitration, marketing, newsletters, talent communities,
retention beyond the application, background check authorisations, a general "I agree to the
terms", any other legal commitment. A label that mixes an allowed box with one of those is the
person's whole. The classifier reads English and French labels and never ticks on doubt.
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
    r"|communaute|vivier|future (?:job|role|opportunit|position|vacanc)"
    r"|other (?:job|role|position)s?"
    r"|opportunites (?:futures|similaires)|retain|retention|retenu|conserv"
    r"|keep my (?:data|information)"
    r"|(?:\d+|one|two|three|five|ten) (?:years?|months?|ans|mois)|background|reference check"
    r"|verification|antecedent|criminal|drug|credit check|third part|tiers|partenaires|affiliate"
    r"|sms|text message|whatsapp|stay in touch|keep in touch|updates?\b"
    r"|receive (?:e-?mail|communication)"
    r"|recevoir|share my|partager|sensitive|biometric|non-?compete|non-?disclosure|confidential\b"
    r"|contract|binding|waive|release|indemn|electronic signature|signature electronique"
    r"|terms (?:of|and)|conditions? (?:of|generales|d')|cgu|cgv|policy of use"
)
_PRIVACY = re.compile(
    r"privacy (?:notice|policy|statement)|data protection|politique de confidentialite"
    r"|protection des donnees|notice de confidentialite|avis de confidentialite|rgpd|gdpr"
    r"|processing of my (?:personal )?data|traitement de mes donnees"
)
_ACCURACY = re.compile(
    r"(?:information|details|answers?|data|statements?).{0,60}(?:accurate|correct|true|complete)"
    r"|(?:accurate|correct|true|truthful).{0,40}(?:information|details|answers?|best of my)"
    r"|renseignements?.{0,40}(?:exact|vrai|veridique|correct)|(?:exact|veridique)s?.{0,30}informations?"
    r"|certify|certifie|atteste"
)


def classify(label: str) -> Box:
    """The verdict on one box. Only the two allowed kinds are ever ticked by the agent."""
    text = _fold(label)
    if not text:
        return Box(BoxKind.person, "an unnamed box is the person's")
    if _PERSON.search(text):
        return Box(BoxKind.person, "a legal commitment or consent beyond this application")
    if _PRIVACY.search(text):
        return Box(BoxKind.privacy, "the employer's privacy notice for this application")
    if _ACCURACY.search(text):
        return Box(BoxKind.accuracy, "the information is accurate")
    return Box(BoxKind.person, "not one of the two boxes the agent may tick")
