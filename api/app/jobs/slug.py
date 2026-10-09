"""Slugs and duplicate detection of postings, after engine/jobs.py `slug`, `norm` and `match`.

Slug. `slugify("Mistral AI", "Applied AI Engineer")` gives "mistral-ai-applied-ai-engineer":
lowercase, every run of characters outside a-z and 0-9 becomes one hyphen, no hyphen at either
end, and a slug over 80 characters is cut at its last hyphen within the first 80 (or at 80 when
there is none). On ASCII text this is the engine's rule character for character. One addition:
accents are folded first (NFKD, combining marks dropped), so "Développeur" gives "developpeur"
where the engine gave "d-veloppeur"; imported rows keep their engine id as their slug anyway.
Text with nothing left (only symbols, or a script with no Latin letters) gives "job".
`unique_slug` adds "-2", "-3", ... until the slug is free, as the engine's upsert did (without
its limit of 98).

Duplicates. Two postings are the same when they have the same URL, compared by `url_key`, or
when company, title and location are the same once normalised by `text_key`. Location is part of
the rule, as in the engine: ../data/jobs.csv holds ten pairs with the same company and title in
different cities (Anthropic, OpenAI, Databricks, ...), which are different postings.

What counts as the same URL (`url_key`):
- http and https are the same; the host is compared lowercase, without "www." and without a
  default port (80, 443);
- the fragment is ignored, and so is a trailing slash on the path ("/jobs/1/" is "/jobs/1");
- tracking parameters are ignored: any utm_* and the names in TRACKING_PARAMS (gclid, fbclid,
  ref, source, lever-source, gh_src, trk, ...). Every other parameter counts, in any order, so
  "?gh_jid=1" and "?gh_jid=2" stay different postings;
- the path keeps its case: "/Jobs/1" and "/jobs/1" are different URLs.
The database's unique (user_id, url) index compares the stored URL exactly; `url_key` is the
application's stricter check before an insert.
"""

import re
import unicodedata
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

SLUG_MAX = 80
FALLBACK_SLUG = "job"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "ref",
        "refid",
        "src",
        "source",
        "trk",
        "trackingid",
        "lever-source",
        "lever-origin",
        "gh_src",
    }
)
DEFAULT_PORTS = {"http": 80, "https": 443}


def fold(text: str) -> str:
    """`text` lowercased with its accents removed: "Équipe Données" gives "equipe donnees"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def slug(text: str) -> str:
    """The engine's slug of `text` (after folding accents), or "job" when nothing is left."""
    s = _NOT_SLUG.sub("-", fold(text)).strip("-")
    if len(s) > SLUG_MAX:
        s = s[:SLUG_MAX].rsplit("-", 1)[0]
    return s or FALLBACK_SLUG


def slugify(company: str, title: str) -> str:
    """The slug of a posting, `<company>-<role>`, as the engine builds its ids."""
    return slug(f"{company} {title}")


def unique_slug(base: str, taken: Container[str]) -> str:
    """`base`, or `base-2`, `base-3`, ... : the first one not in `taken`."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def text_key(text: str) -> str:
    """The engine's `norm` after folding accents: lowercase words of a-z and 0-9, one space."""
    return " ".join(_NOT_SLUG.sub(" ", fold(text)).split())


def url_key(url: str | None) -> str | None:
    """The URL as compared for duplicates (see the module docstring), None when there is none."""
    if url is None or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return url.strip()  # not an absolute URL: compared as written
    host = (parts.hostname or "").lower().removeprefix("www.")
    try:
        port = parts.port
    except ValueError:
        port = None
    scheme = parts.scheme.lower()
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    if scheme in DEFAULT_PORTS:
        scheme = "http"
    path = parts.path.rstrip("/")
    params = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (name.lower().startswith("utm_") or name.lower() in TRACKING_PARAMS)
    )
    query = f"?{urlencode(params)}" if params else ""
    return f"{scheme}://{host}{path}{query}"


@dataclass(frozen=True)
class PostingKey:
    """What duplicate detection compares: the URL key and the company, title, location key."""

    url: str | None
    identity: tuple[str, str, str]

    @classmethod
    def of(cls, company: str, title: str, location: str | None, url: str | None) -> "PostingKey":
        return cls(url_key(url), (text_key(company), text_key(title), text_key(location or "")))

    def same_as(self, other: "PostingKey") -> bool:
        if self.url is not None and self.url == other.url:
            return True
        return self.identity == other.identity


def find_duplicate[T](
    key: PostingKey, rows: Iterable[T], key_of: Callable[[T], PostingKey]
) -> T | None:
    """The first row that is the same posting as `key`: a URL match wins over the identity one."""
    by_identity = None
    for row in rows:
        other = key_of(row)
        if key.url is not None and key.url == other.url:
            return row
        if by_identity is None and key.identity == other.identity:
            by_identity = row
    return by_identity
