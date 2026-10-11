"""Does this text look like it holds a password or an access key?

`scan(text)` returns the names of the rules that match, in the order below, each once. A match
sets `ingestion_documents.secret_warning`: the user confirms the document or deletes it, and no
run reads it before (SPEC-ingestion.md, Laptop-analysis prompt). Whoever stores a document's text
calls it: the upload of a text file or a paste here in G1, the parse job and the transcription
later.

The rules are a small port of the default rules of gitleaks (https://github.com/gitleaks/gitleaks,
MIT licence, Copyright (c) 2019 Zachary Rice): the provider tokens with a fixed shape, private key
blocks, and the generic "keyword, assignment, random looking value" rule with its entropy floor.
`url-credentials` (a password inside an address) is ours. They are a warning for the user, not a
guarantee: a secret with no recognisable shape passes.

Only rule names ever leave this module. A caller logs the names, never the text or the match.
Every pattern is linear: bounded repetitions and no nested quantifier, so a hostile text of
500,000 characters cannot make a scan slow.
"""

import math
import re
from collections import Counter

# Shannon entropy floor of the generic rule, as gitleaks' generic-api-key.
GENERIC_ENTROPY = 3.5
# A base64 value that holds a slash or a plus (the form of an AWS secret key) counts from this
# length and this entropy. Measured on made up values: 0.04 percent of random base64 values of
# 40 characters are under 4.3, 3 percent of those of 32; a file path stays under 4.2.
BASE64_MIN_CHARS = 30
BASE64_ENTROPY = 4.3

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[a-z]{2,10}\d{2}-[A-Za-z0-9_-]{20,200}")),
    (
        "openai-key",
        re.compile(
            r"\bsk-(?:(?:proj|svcacct|admin)-)?[A-Za-z0-9_-]{20,200}T3BlbkFJ[A-Za-z0-9_-]{20,200}"
        ),
    ),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{82})\b"),
    ),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,64}")),
    ("slack-token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,250}")),
    (
        "slack-webhook",
        re.compile(r"hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9+/]{43,56}"),
    ),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:test|live|prod)_[A-Za-z0-9]{10,99}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z]{34}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("pypi-token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,1000}")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    (
        "json-web-token",
        re.compile(
            r"\bey[A-Za-z0-9]{17,500}\.ey[A-Za-z0-9/_-]{17,2000}\.[A-Za-z0-9/_-]{10,1000}={0,2}"
        ),
    ),
    (
        "url-credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^\s/:@]{1,64}:[^\s/:@]{3,128}@[^\s/@]"),
    ),
)

# The generic rule: a keyword, at most 20 more word characters, an assignment, then a value of
# 10 to 150 characters that looks random. Written so that an ordinary line of a technical CV
# does not match ("Authentication: OAuth2/OIDC/JWT-RS256", "REST API, FastAPI/Flask/Django4.2",
# "Author: Camille_Durand-2023"):
# - the keyword is a whole word (a plural too) or a whole part of a name: `password`,
#   `db_password`, `dbPassword`, `DB_PASSWORD`, `credentials`, never the start of a longer word
#   (Authentication, Author, Keycloak, tokenizer) nor its end (OAuth, FastAPI, monkey);
# - a comma is not an assignment;
# - the value takes no slash and no plus, as gitleaks' own: a list of tools is not one value.
#
# Two things depend on how sure the label is (`_sure`). A label is sure when its keyword can
# only name a secret (password, pwd, mdp, "mot de passe", secret, credentials, an api key in one
# word) or when the keyword is part of a longer name, a variable and not the heading of a line
# (api_key, access_token, X-Api-Key, AUTH_TOKEN). A bare "API:", "Auth:", "Key:", "Token:" or
# "Access:" is also how a line of a CV starts. So:
# - after a label that is not sure, a value that reads as words is left alone: one part of it,
#   between hyphens, dots or underscores, is five or more lower case letters (the heading
#   "Token" before JWT-RS256-rotation, the heading "Key" before Kubernetes1.29-certified; the
#   lines themselves are in the tests, and a scanner of this repository takes them for keys);
#   and so is a value that goes on with a slash or a plus, the first item of a list
#   ("Key: PostgreSQL16/Elasticsearch8");
# - a base64 value with a slash or a plus counts only after a sure label, and only when it is
#   long and random enough (BASE64_MIN_CHARS, BASE64_ENTROPY): "AWS_SECRET_ACCESS_KEY=" and 40
#   such characters is caught, "Key: PostgreSQL16/Elasticsearch8/MongoDB7" is not. A list of
#   tools reaches the entropy of base64 (4.6 measured), so the label has to tell them apart.
# The price: after a bare "Key:" or "Token:", a key that holds a slash or a plus is missed.
_LOWER = (
    "access|authorization|auth|apikey|api|credential|creds|key|mdp|passw(?:or)?d|pwd|secret|token"
)
_TITLE = (
    "Access|Authorization|Auth|Apikey|Api|Credential|Creds|Key|Mdp|Passw(?:or)?d|Pwd|Secret|Token"
)
_UPPER = (
    "ACCESS|AUTHORIZATION|AUTH|APIKEY|API|CREDENTIAL|CREDS|KEY|MDP|PASSW(?:OR)?D|PWD|SECRET|TOKEN"
)
_KEYWORD = (
    "(?P<keyword>"
    rf"(?<![A-Za-z])(?:{_LOWER})s?(?![a-z])"  # password, api_key, apiKey, tokens
    rf"|(?<![A-Z])(?:{_TITLE})s?(?![a-z])"  # Password, dbPassword, never OAuth or Author
    rf"|(?<![A-Za-z])(?:{_UPPER})S?(?![A-Za-z])"  # DB_PASSWORD, API_KEY, never FastAPI
    r"|(?<![A-Za-z])(?i:api[_-]?key)s?(?![a-z])"  # APIKey, ApiKey, in any mix of cases
    r"|(?<![A-Za-z])(?i:mot[ _-]de[ _-]passe)(?![a-z])"  # the label a French speaker writes
    ")"
)
# The keyword, what is left of its name, then the assignment. French puts a space before the
# colon, often a no-break one: it is taken like a space.
_LABEL = (
    _KEYWORD + r"(?P<more>[\w.-]{0,20})"
    r"[ \t\u00a0\u202f\"']{0,3}(?:=>|:=|\?=|=|:)[ \t\"'`]{0,5}"
)
_GENERIC = re.compile(_LABEL + r"(?P<value>[\w.=-]{10,150})")
_GENERIC_BASE64 = re.compile(_LABEL + rf"(?P<value>[A-Za-z0-9+/]{{{BASE64_MIN_CHARS},150}})")
_SURE_KEYWORD = re.compile(r"(?i)passw|pwd|mdp|passe|secret|cred|api[_-]?key")
_WORDS = re.compile(r"(?:^|[-_.])[a-z]{5,}(?:$|[-_.])")
GENERIC_RULE = "generic-password-or-key"

RULE_NAMES: tuple[str, ...] = (*(name for name, _ in _RULES), GENERIC_RULE)


def entropy(value: str) -> float:
    """Shannon entropy of the characters of `value`, in bits per character."""
    if not value:
        return 0.0
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in Counter(value).values())


def _looks_random(value: str) -> bool:
    """A value with digits and enough entropy: a word, a path or a file name is not one."""
    return any(c.isdigit() for c in value) and entropy(value) >= GENERIC_ENTROPY


def _sure(match: re.Match[str], text: str) -> bool:
    """The label can only name a secret, or its keyword is part of a longer name (see above)."""
    if match["more"] or _SURE_KEYWORD.search(match["keyword"]):
        return True
    start = match.start("keyword")
    return start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_.-")


def _generic(text: str) -> bool:
    """True when a value assigned to a secret's name in `text` looks like a secret."""
    for match in _GENERIC.finditer(text):
        value = match["value"]
        if not _looks_random(value):
            continue
        if _sure(match, text):
            return True
        # After what may be a heading: not the first item of a list, not a run of words.
        in_a_list = text[match.end() : match.end() + 1] in ("/", "+")
        if not in_a_list and not _WORDS.search(value):
            return True
    for match in _GENERIC_BASE64.finditer(text):
        value = match["value"]
        if (
            ("/" in value or "+" in value)
            and any(c.isdigit() for c in value)
            and entropy(value) >= BASE64_ENTROPY
            and _sure(match, text)
        ):
            return True
    return False


def scan(text: str) -> list[str]:
    """The names of the rules `text` matches (empty: nothing looks like a secret)."""
    found = [name for name, pattern in _RULES if pattern.search(text)]
    if _generic(text):
        found.append(GENERIC_RULE)
    return found
