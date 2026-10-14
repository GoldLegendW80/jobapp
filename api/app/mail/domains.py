"""The registrable domain of a host name (mail M3): `mail.hr.example.co.uk` is `example.co.uk`.

It is what the binding rule compares: the visible `From` of a mail against the domain Gmail
authenticated, and against the host of a posting. The answer comes from the public suffix list
(https://publicsuffix.org, MPL-2.0), **the snapshot bundled in the `publicsuffixlist` package
pinned in pyproject.toml**: read from its file when this module is imported, never downloaded,
never refreshed at run time. `SNAPSHOT_DATE` is the date of that snapshot; a test holds it to
the installed package, so the list changes only when someone changes the pin.

Private suffixes count (`github.io`, `notion.site`): under them each customer has its own
registrable domain, so `alice.github.io` and `mallory.github.io` are two organisations. A name
under a top level domain the list does not know is read with the list's own default rule (the
last label is the suffix), which is what makes `evil.example` a domain in the tests.

Anything that is not a plain host name has no registrable domain, and the callers read `None`
as "not trusted": an IP address, a name with an empty label, an underscore, a space, a control
character, a label over 63 characters, a name over 253, a name that is itself a suffix.

A name written in another script is read by Python's `idna` codec, which follows IDNA 2003: it
writes `straße.example` as `strasse.example`, a final sigma as a sigma, and drops a zero width
joiner. Under IDNA 2008, which registries, browsers and mail follow, each of those is another
registrable domain, or none. So a name the codec changed by more than case and composition has
no registrable domain here either: it is never read as the name of someone else.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from publicsuffixlist import PublicSuffixList

SNAPSHOT_DATE = date(2026, 10, 10)

MAX_NAME = 253
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PSL = PublicSuffixList()  # the bundled file; `accept_unknown` is the list's default rule


def normalise(host: object) -> str | None:
    """`host` as lower case ASCII labels (IDNA for a name written in another script), without
    its final dot, or None when it is not a host name, or is one the codec reads as another."""
    if not isinstance(host, str) or not host or len(host) > MAX_NAME * 4:
        return None
    name = host[:-1] if host.endswith(".") else host
    if not name.isascii():
        try:
            encoded = name.encode("idna")
            if encoded.decode("idna").lower() != unicodedata.normalize("NFC", name).lower():
                return None
        except UnicodeError:
            return None
        name = encoded.decode("ascii")
    name = name.lower()
    if len(name) > MAX_NAME:
        return None
    labels = name.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    if labels[-1].isdigit():  # an IPv4 address, or anything that would be read as one
        return None
    return name


def registrable_domain(host: object) -> str | None:
    """The registrable domain `host` belongs to, or None when it has none."""
    name = normalise(host)
    if name is None:
        return None
    found = _PSL.privatesuffix(name)
    return found if isinstance(found, str) and found else None


def same_organisation(one: object, other: object) -> bool:
    """Whether two host names have the same registrable domain. Never true for a name that has
    none."""
    domain = registrable_domain(one)
    return domain is not None and domain == registrable_domain(other)
