"""Slugs and duplicate detection (app/jobs/slug.py): unicode, punctuation, long titles,
collisions, and what counts as the same URL."""

import re

import pytest

from app.jobs.models import SLUG_PATTERN
from app.jobs.slug import (
    FALLBACK_SLUG,
    SLUG_MAX,
    PostingKey,
    find_duplicate,
    slug,
    slugify,
    text_key,
    unique_slug,
    url_key,
)


@pytest.mark.parametrize(
    ("company", "title", "expected"),
    [
        ("Mistral AI", "Applied AI Engineer", "mistral-ai-applied-ai-engineer"),
        (
            "Anthropic",
            "Forward Deployed Engineer (FDE), Paris",
            "anthropic-forward-deployed-engineer-fde-paris",
        ),
        (
            "OpenAI",
            "Applied AI Engineer - Codex: Core Agent",
            "openai-applied-ai-engineer-codex-core-agent",
        ),
        ("  --Acme--  ", "  ** C# / .NET Dev **  ", "acme-c-net-dev"),
        (
            "Société Générale",
            "Développeur IA générative",
            "societe-generale-developpeur-ia-generative",
        ),
        ("Crédit Agricole", "Ingénieur(e) ML – CDI", "credit-agricole-ingenieur-e-ml-cdi"),
        ("ﬁnance Co", "Ⅻ Engineer", "finance-co-xii-engineer"),  # compatibility forms folded
        ("Straße AG", "Engineer", "stra-e-ag-engineer"),  # ß has no decomposition
        ("株式会社", "エンジニア", FALLBACK_SLUG),
        ("", "", FALLBACK_SLUG),
        ("🤖", "!!!", FALLBACK_SLUG),
        ("Acme", "123", "acme-123"),
    ],
)
def test_slugify(company: str, title: str, expected: str) -> None:
    result = slugify(company, title)
    assert result == expected
    assert re.fullmatch(SLUG_PATTERN, result)


def test_long_titles_are_cut_at_a_word() -> None:
    title = "Senior Staff Principal Applied Artificial Intelligence Engineer, Agents Platform, EMEA"
    result = slugify("Some Very Long Company Name International", title)
    assert len(result) <= SLUG_MAX
    assert not result.endswith("-")
    assert f"{result}-".startswith(result)
    assert slugify("Acme", title).startswith("acme-senior-staff")
    # One unbroken word longer than the limit is cut at the limit.
    assert slug("a" * 200) == "a" * SLUG_MAX
    # Exactly 80 is kept whole.
    assert slug("b" * 80) == "b" * 80
    assert slug("ab " * 40).count("-") < 40
    assert re.fullmatch(SLUG_PATTERN, slug("é-" * 100))


def test_collisions_get_a_suffix() -> None:
    assert unique_slug("acme-ai", set()) == "acme-ai"
    assert unique_slug("acme-ai", {"acme-ai"}) == "acme-ai-2"
    assert unique_slug("acme-ai", {"acme-ai", "acme-ai-2", "acme-ai-3"}) == "acme-ai-4"
    assert unique_slug("acme-ai", {"acme-ai", "acme-ai-3"}) == "acme-ai-2"
    # The engine stopped at -98; the port does not.
    taken = {"acme-ai"} | {f"acme-ai-{n}" for n in range(2, 150)}
    assert unique_slug("acme-ai", taken) == "acme-ai-150"
    # A suffixed slug stays a valid slug even on a full-length base.
    base = slug("x" * 200)
    assert re.fullmatch(SLUG_PATTERN, unique_slug(base, {base}))


def test_text_key() -> None:
    assert text_key("  Mistral AI ") == "mistral ai"
    assert text_key("Mistral-AI") == "mistral ai"
    assert text_key("Ingénieur IA (H/F)") == "ingenieur ia h f"
    assert text_key("") == ""


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://jobs.ashbyhq.com/mistral/123", "http://jobs.ashbyhq.com/mistral/123"),
        ("https://jobs.ashbyhq.com/mistral/123", "https://jobs.ashbyhq.com/mistral/123/"),
        ("https://jobs.ashbyhq.com/mistral/123", "HTTPS://Jobs.AshbyHQ.com/mistral/123"),
        ("https://jobs.ashbyhq.com/mistral/123", "https://www.jobs.ashbyhq.com/mistral/123"),
        ("https://jobs.ashbyhq.com/mistral/123", "https://jobs.ashbyhq.com:443/mistral/123"),
        ("http://jobs.example/1", "http://jobs.example:80/1"),
        ("https://jobs.ashbyhq.com/mistral/123", "https://jobs.ashbyhq.com/mistral/123#apply"),
        (
            "https://jobs.lever.co/acme/abc",
            "https://jobs.lever.co/acme/abc?utm_source=linkedin&utm_medium=social&lever-source=x",
        ),
        (
            "https://boards.greenhouse.io/acme/jobs/1?gh_jid=1",
            "https://boards.greenhouse.io/acme/jobs/1?gh_src=abc&gh_jid=1&ref=hn",
        ),
        ("https://x.example/a?b=2&a=1", "https://x.example/a?a=1&b=2"),  # order ignored
        ("  https://x.example/a ", "https://x.example/a"),
        ("https://x.example", "https://x.example/"),
    ],
)
def test_same_url(a: str, b: str) -> None:
    assert url_key(a) == url_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "https://boards.greenhouse.io/acme/jobs?gh_jid=1",
            "https://boards.greenhouse.io/acme/jobs?gh_jid=2",
        ),
        ("https://x.example/Jobs/1", "https://x.example/jobs/1"),  # paths are case-sensitive
        ("https://x.example/jobs/1", "https://x.example/jobs/12"),
        ("https://x.example:8443/1", "https://x.example/1"),
        ("https://x.example/1", "https://y.example/1"),
        ("https://x.example/1", "ftp://x.example/1"),
    ],
)
def test_different_urls(a: str, b: str) -> None:
    assert url_key(a) != url_key(b)


def test_url_key_edge_cases() -> None:
    assert url_key(None) is None
    assert url_key("") is None
    assert url_key("   ") is None
    assert url_key("not a url") == "not a url"  # compared as written
    assert url_key("https://x.example:notaport/1") == "http://x.example/1"


def key(company: str, title: str, location: str = "", url: str | None = None) -> PostingKey:
    return PostingKey.of(company, title, location, url)


def test_same_posting_rules() -> None:
    base = key("Mistral AI", "AI Engineer", "Paris", "https://jobs.example/1")
    # Same URL, whatever the text says.
    assert base.same_as(key("Other", "Other", "Other", "http://jobs.example/1/"))
    # Same company, title and location, once normalised, whatever the URL.
    assert base.same_as(key("mistral-ai", "AI  engineer", "PARIS", "https://jobs.example/2"))
    assert base.same_as(key("Mistral AI", "AI Engineer", "Paris"))
    # Another location is another posting (the CSV has such pairs).
    assert not base.same_as(key("Mistral AI", "AI Engineer", "London"))
    # Two postings without a URL never match by URL.
    assert not key("A", "B").same_as(key("C", "D"))
    assert key("A", "B", "", None).same_as(key("a", "b", None, ""))  # type: ignore[arg-type]
    # Accents are folded on both sides.
    assert key("Société Générale", "Ingénieur").same_as(key("Societe Generale", "Ingenieur"))


def test_find_duplicate_prefers_the_url_match() -> None:
    rows = [
        key("Mistral AI", "AI Engineer", "Paris"),
        key("Other", "Other", "", "https://jobs.example/1"),
    ]
    probe = key("Mistral AI", "AI Engineer", "Paris", "https://jobs.example/1?utm_source=x")
    assert find_duplicate(probe, rows, lambda k: k) is rows[1]
    assert find_duplicate(key("Mistral AI", "AI Engineer", "Paris"), rows, lambda k: k) is rows[0]
    assert find_duplicate(key("New", "Role"), rows, lambda k: k) is None
    assert find_duplicate(key("New", "Role"), [], lambda k: k) is None
