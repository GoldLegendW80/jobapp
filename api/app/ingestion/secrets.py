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

# keyword, at most 20 more word characters, an assignment, then the value (10 to 150 characters).
_GENERIC = re.compile(
    r"(?i)(?:access|auth|api|credential|creds|key|passw(?:or)?d|secret|token)[\w.-]{0,20}"
    r"[ \t\"']{0,3}(?:=|:|:=|=>|\|\|:|<=|,)[ \t\"'`]{0,5}"
    r"([\w.=+/-]{10,150})"
)
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


def scan(text: str) -> list[str]:
    """The names of the rules `text` matches (empty: nothing looks like a secret)."""
    found = [name for name, pattern in _RULES if pattern.search(text)]
    if any(_looks_random(match.group(1)) for match in _GENERIC.finditer(text)):
        found.append(GENERIC_RULE)
    return found
