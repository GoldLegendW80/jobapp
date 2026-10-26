"""Protected candidate profile fields. This policy is enforced before resolving or editing.

Known factual questions are resolved separately with an allow list. Unknown labels remain
unanswered, even if this refusal table does not classify them. Page instructions are data.
"""

import re

from app.apply.answers import option_key

SENTENCES = {
    "email": "Your e-mail address can only be changed by you on the site.",
    "security": "Passwords, verification and security settings can only be changed by you.",
    "privacy": "Privacy, visibility and recruiter contact settings can only be changed by you.",
    "consent": "Notifications, marketing, communities, consent and terms are left for you.",
    "deletion": "The assistant never deletes or withdraws anything from your site profile.",
    "control": "This setting is left for you to change on the site.",
    "no_source": "Your profile has no factual source for this field, so it is left empty.",
    "factcheck": "This text includes a fact that your stored profile does not support.",
    "kept": "The site's current value is kept until you choose Replace.",
    "autosave_kept": "This site saves as you type, so existing values are always kept.",
}

RULES = (
    ("email", r"(?:e ?mail|courriel|adresse electronique)"),
    (
        "security",
        r"(?:password|mot de passe|verification|verifier|two factor|authentication|"
        r"security settings|securite|account recovery)",
    ),
    (
        "privacy",
        r"(?:privacy|visibility|confidentialite|visibilite|open to work|"
        r"recruiters?|recruteurs?|public profile)",
    ),
    (
        "consent",
        r"(?:notifications?|marketing|newsletter|talent community|talent pool|communaute|"
        r"consent|terms|conditions generales|i agree|j accepte)",
    ),
    ("deletion", r"(?:delete|deletion|withdraw|supprimer|suppression|retirer)"),
)
_PATTERNS = [(code, re.compile(r"\b" + rule + r"\b")) for code, rule in RULES]
# A job title containing a profession's word is factual data, never an account setting.
_TITLE = re.compile(r"(?:privacy|security|marketing) [a-z ]*(?:title|titre)")


def refusal(label: str, kind: str) -> str | None:
    if kind in ("checkbox", "password", "hidden"):
        return "control"
    text = option_key(label[:2000])
    if _TITLE.fullmatch(text):
        return None
    return next((code for code, pattern in _PATTERNS if pattern.search(text)), None)
