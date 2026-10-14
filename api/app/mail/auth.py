"""Whether Gmail authenticated the visible sender of a mail: A of the binding rule (mail M3).

The rule is stated once, in tasks/mail/todo.md ("The binding rule", A). This module is that
paragraph as code, and nothing more lenient:

**The one header read** is the topmost `Authentication-Results` of the message, in the order
Gmail returns the headers. It counts only if three things hold:

1. its server name is exactly `mx.google.com`;
2. the nearest `Received` header above it was written `by mx.google.com`: Gmail took this mail
   by SMTP itself;
3. the message has no `X-Gmail-Fetch-Info` header, the mark of a mail Gmail fetched from
   another mailbox (there the sender wrote everything, a forged pair of headers included).

Every other `Authentication-Results`, and every `ARC-Authentication-Results`, is the mail's own
claim and is never read.

**A pass**, in that header, is `dmarc=pass` with a `header.from` that has the registrable domain
of the visible `From`, or a `dkim=pass` whose signing domain (`header.i` or `header.d`) has that
same registrable domain. `spf=pass` alone is never a pass: it speaks of the envelope.

The message must carry one `From` header with one address and must not be suspicious
(mail/parse.py decides both and passes them in). **A header shape this module does not
recognise is not a pass**: a line break that is not a fold, a quoted string, a comment that
does not close, a result or a property that is not `name=value`, two `dmarc` results. Whatever
the rule does not cover is answered "not trusted".

Assumption A4 (plan): Gmail writes its header above anything the sender wrote and directly
under its own `Received` line, and the API returns headers in the message's order. Checkpoint
stage B checks it on real messages before any automatic change is trusted.

Pure functions, the standard library and mail/domains.py. Nothing is logged, and no value of a
header ever reaches an exception.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.mail import domains

GMAIL_SERVER = "mx.google.com"
FETCH_MARK = "x-gmail-fetch-info"
MAX_VALUE = 16_384  # characters of one header value this module reads; a longer one is no pass

_FOLD = re.compile(r"\r?\n(?=[ \t])")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_RESULT = re.compile(r"([a-z0-9-]+)=([a-z]+)")
_PROPERTY = re.compile(r"([a-z0-9-]+\.[a-z0-9_-]+|reason)=(\S*)")


@dataclass(frozen=True)
class Verdict:
    """`trusted`: Gmail authenticated the visible sender. `from_domain`: the registrable domain
    of the visible `From`, None when there is not exactly one address or it has no such domain.
    Without `trusted` that domain is only what the sender wrote."""

    trusted: bool
    from_domain: str | None


@dataclass(frozen=True)
class Result:
    method: str  # dkim, spf, dmarc, arc, ...
    result: str  # pass, fail, none, neutral, ...
    properties: dict[str, str]  # "header.from" -> "example.org"


def unfold(value: object) -> str | None:
    """A header value on one line, or None when it is not one header: a fold (a line break
    followed by a space or a tab) is removed as RFC 5322 says, and any other CR, LF or control
    character means the value was written to be read as something else."""
    if not isinstance(value, str) or len(value) > MAX_VALUE:
        return None
    line = _FOLD.sub("", value)
    return None if _CONTROL.search(line) else line


def without_comments(line: str) -> str | None:
    """`line` with every parenthesised comment replaced by a space, or None when a parenthesis
    is left open or closed twice. What a comment says is never read: it is free text, and in
    Gmail's own header it quotes the sender."""
    kept: list[str] = []
    depth = 0
    escaped = False
    for char in line:
        if escaped:
            escaped = False
        elif depth and char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
            if depth == 1:
                kept.append(" ")
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        elif depth == 0:
            kept.append(char)
    return None if depth or escaped else "".join(kept)


def read_results(value: object) -> tuple[str, tuple[Result, ...]] | None:
    """(server name, results) of one `Authentication-Results` value, or None when its shape is
    not the one of RFC 8601 as Gmail writes it."""
    line = unfold(value)
    if line is None or '"' in line:
        return None
    line = without_comments(line)
    if line is None:
        return None
    first, *rest = line.split(";")
    server = first.split()
    # The server name, alone or followed by the version of the header ("1", the only one).
    if not server or len(server) > 2 or server[1:] not in ([], ["1"]):
        return None
    results = []
    for part in rest:
        words = part.lower().split()
        if not words or words == ["none"]:
            continue
        head = _RESULT.fullmatch(words[0])
        if head is None:
            return None
        properties: dict[str, str] = {}
        for word in words[1:]:
            found = _PROPERTY.fullmatch(word)
            if found is None or found[1] in properties:
                return None
            properties[found[1]] = found[2]
        results.append(Result(head[1], head[2], properties))
    return server[0], tuple(results)


def received_by_gmail(value: object) -> bool:
    """Whether a `Received` value says the hop was taken `by mx.google.com`: the `by` clause
    itself, never those words inside a comment, a `from` clause or an id."""
    line = unfold(value)
    if line is None:
        return False
    line = without_comments(line)
    if line is None:
        return False
    words = line.split(";")[0].split()
    if len(words) >= 2 and words[0].lower() == "by":
        return words[1] == GMAIL_SERVER
    return (
        len(words) >= 4
        and words[0].lower() == "from"
        and words[2].lower() == "by"
        and words[3] == GMAIL_SERVER
    )


def _signs_for(result: Result, from_domain: str) -> bool:
    """A `dkim` result whose signing domain is of the sender's organisation. `header.i` is an
    identity ("@example.org", "bot@mail.example.org") and `header.d` a domain; each one that is
    there must agree."""
    signers = [
        result.properties[name] for name in ("header.i", "header.d") if name in result.properties
    ]
    return bool(signers) and all(
        domains.registrable_domain(signer.rsplit("@", 1)[-1]) == from_domain for signer in signers
    )


def passes(results: Sequence[Result], from_domain: str) -> bool:
    """A pass, as the rule defines it, for a sender of `from_domain`."""
    dmarc = [result for result in results if result.method == "dmarc"]
    if (
        len(dmarc) == 1
        and dmarc[0].result == "pass"
        and domains.registrable_domain(dmarc[0].properties.get("header.from")) == from_domain
    ):
        return True
    return any(
        result.method == "dkim" and result.result == "pass" and _signs_for(result, from_domain)
        for result in results
    )


def authenticate(
    headers: Sequence[tuple[str, str]], from_addr: str | None, *, suspicious: bool
) -> Verdict:
    """The verdict for one message. `headers` are its (name, value) pairs in Gmail's order,
    `from_addr` the one address of its one `From` (None otherwise), `suspicious` what
    mail/parse.py found."""
    from_domain = None
    if isinstance(from_addr, str) and "@" in from_addr:
        from_domain = domains.registrable_domain(from_addr.rsplit("@", 1)[-1])
    untrusted = Verdict(False, from_domain)
    if suspicious or from_domain is None:
        return untrusted
    names = [name.strip().lower() if isinstance(name, str) else "" for name, _ in headers]
    if FETCH_MARK in names or "authentication-results" not in names:
        return untrusted
    at = names.index("authentication-results")
    above = next((i for i in range(at - 1, -1, -1) if names[i] == "received"), None)
    if above is None or not received_by_gmail(headers[above][1]):
        return untrusted
    read = read_results(headers[at][1])
    if read is None or read[0] != GMAIL_SERVER:
        return untrusted
    return Verdict(passes(read[1], from_domain), from_domain)
