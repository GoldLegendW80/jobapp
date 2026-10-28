"""The importer on synthetic sources (runs everywhere, CI included): assemble.py parsing, facts.md
blocks and answers, skills and boards conversion, regex splitting, missing and broken files."""

import base64
import dataclasses
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile import matching
from app.profile.importer import (
    ConversionError,
    SourcePaths,
    convert,
    expand_alternative,
    parse_assemble,
    regex_terms,
    split_alternatives,
    split_blocks,
    wrap_skill_term,
)
from app.profile.photo import normalize
from app.profile.schemas import completeness
from tests.profile import images

NOW = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"

ASSEMBLE = '''\
"""A minimal assemble.py."""
import pathlib
STYLE = (pathlib.Path(__file__).parent / "style.css").read_text()  # not a literal: ignored
NAME = "ADA EXAMPLE"
CONTACT_LEFT = [
    ("https://www.linkedin.com/in/ada/", "linkedin.com/in/ada"),
    ("https://ada.example.org/", "ada.example.org"),
]
CONTACT_RIGHT = ["+33 6 12 34 56 78", "ada@example.org", "Lyon, France"]
EDUCATION = [("MSc", "Some School 2020 \u2013 2022. Option AI")]
LANGUAGES = [("French", "Mother tongue")]
EMPLOYERS = {
    "acme": ("Acme", "March 2023 \u2013 Present"),
}
'''

FACTS = """\
# FACT BASE \u2014 Ada

Intro line.

## IDENTITY

- **Ada Example** \u2014 Lyon
- Current title: **AI Engineer**

## EMPLOYMENT

| Employer | Role | Dates | Location |
|---|---|---|---|
| **Acme** | AI Engineer (apprenticeship) | March 2023 \u2013 Present | Lyon |

## EDUCATION

- **Some School**, Lyon \u2014 *MSc*,
  **September 2020 \u2013 June 2022**.

## PRACTICALITIES FOR APPLICATION FORMS

- **Citizenship and work authorization:** French citizen. No sponsorship needed in France.
- **Notice and start:** one month. Earliest start: January 2027. Travel is fine.
- **Salary expectations:** never invent a number.
- **Motivation, in her own words:** she likes agents.

### A sub heading

stays in the block
"""


def write_sources(root: Path, **files: str | None) -> SourcePaths:
    """The engine layout under root; a value of None leaves that file out."""
    defaults: dict[str, str | None] = {
        "profile/facts.md": FACTS,
        "engine/assemble.py": ASSEMBLE,
        "profile/skills.json": json.dumps(
            {
                "_doc": "ignored",
                "years_experience": 2,
                "have": {"agents": ["agent", "tool use"], "ml": ["fine[- ]?tun\\w*", "lora"]},
                "adjacent": {},
                "none": {"research": ["pytorch"]},
                "gates": {"phd": "phd (is )?required"},
            }
        ),
        "engine/boards.json": json.dumps(
            {
                "title_re": "ai engineer|llm|solutions? (architect|engineer)",
                "exclude_re": "\\b(staff|lead)\\b",
                "location_re": "paris|\\bfrance\\b",
                "boards": [
                    {"company": "Acme AI", "ats": "ashby", "org": "acme.ai"},
                    {
                        "company": "Beta",
                        "ats": "lever",
                        "org": "beta",
                        "exclude_re": "senior|\\bsr\\b",
                    },
                ],
            }
        ),
        "engine/photo.b64": "data:image/png;base64," + base64.b64encode(images.png()).decode(),
    }
    names = {
        "facts": "profile/facts.md",
        "assemble": "engine/assemble.py",
        "skills": "profile/skills.json",
        "boards": "engine/boards.json",
        "photo": "engine/photo.b64",
    }
    for key, value in files.items():
        defaults[names[key]] = value
    for relative, content in defaults.items():
        if content is not None:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return SourcePaths.under(root)


def sections(tmp_path: Path, **files: str | None) -> dict[str, Any]:
    document = convert(write_sources(tmp_path, **files), now=NOW)
    return json.loads(document.to_json())["sections"]


# --- the whole conversion -----------------------------------------------------------------------


def test_minimal_sources_convert_to_a_complete_profile(tmp_path: Path) -> None:
    document = convert(write_sources(tmp_path), now=NOW)
    body = json.loads(document.to_json())
    assert body["format"] == "jobapp-profile"
    assert body["version"] == 1
    assert body["exported_at"] == "2026-10-08T09:30:00Z"
    assert body["photo"]["mime"] == "image/png"
    out = body["sections"]
    assert out["identity"] == {
        "full_name": "Ada Example",
        "headline": "AI Engineer",
        "email": "ada@example.org",
        "phone": "+33 6 12 34 56 78",
        "city": "Lyon",
        "country": "France",
        "linkedin_url": "https://www.linkedin.com/in/ada/",
        "website": "https://ada.example.org/",
        "links": [],
        "languages": [{"name": "French", "level": "Mother tongue"}],
    }
    assert out["experience"]["employers"] == [
        {
            "start": "2023-03",
            "end": None,
            "key": "acme",
            "organisation": "Acme",
            "title": "AI Engineer (apprenticeship)",
            "location": "Lyon",
            "contract": "apprenticeship",
        }
    ]
    assert out["experience"]["education"] == [
        {
            "start": "2020-09",
            "end": "2022-06",
            "school": "Some School",
            "degree": "MSc",
            "location": "Lyon",
            "details": "2020 \u2013 2022. Option AI",
        }
    ]
    assert [b["title"] for b in out["facts"]["blocks"]] == [
        "FACT BASE \u2014 Ada",
        "IDENTITY",
        "EMPLOYMENT",
        "EDUCATION",
        "PRACTICALITIES FOR APPLICATION FORMS",
    ]
    assert out["facts"]["blocks"][-1]["body"].endswith("### A sub heading\n\nstays in the block")
    assert out["answers"]["work_authorization"] == [
        {
            "country": "France",
            "answer": "French citizen. No sponsorship needed in France.",
            "needs_sponsorship": False,
        }
    ]
    assert out["answers"]["notice_period"] == "One month."
    assert out["answers"]["earliest_start"] == "January 2027"
    assert out["answers"]["travel"] == "Travel is fine."
    assert out["answers"]["salary_policy"] == "Never invent a number."
    assert out["answers"]["motivation"] == "She likes agents."
    # Every section complete but Search: the engine files name boards and no job website, and
    # only a job website ticked in Find jobs completes it (owner, 2026-10-28).
    result = completeness(out)
    assert result.sections["search"].missing == ["interactive_sites"]
    assert all(not s.missing for name, s in result.sections.items() if name != "search")
    assert result.overall == 97


def test_skills_conversion(tmp_path: Path) -> None:
    skills = sections(tmp_path)["skills"]
    assert skills["years_experience"] == 2.0
    assert skills["have"] == [
        {"advanced_regex": False, "terms": ["agent", "tool use"], "name": "agents"},
        {
            "advanced_regex": True,
            "terms": [r"(?<!\w)(?:fine[- ]?tun\w*)(?:s|es)?(?!\w)", r"\blora(?:s|es)?\b"],
            "name": "ml",
        },
    ]
    assert skills["adjacent"] == []
    assert skills["gates"] == [
        {"advanced_regex": True, "terms": ["phd (is )?required"], "name": "phd", "penalty": 25}
    ]


def test_search_conversion(tmp_path: Path) -> None:
    search = sections(tmp_path)["search"]
    assert search["title_include"] == {
        "advanced_regex": True,
        "terms": ["ai engineer", "llm", "solutions? architect", "solutions? engineer"],
    }
    assert search["title_exclude"]["terms"] == [r"\bstaff\b", r"\blead\b"]
    assert search["location"]["terms"] == ["paris", r"\bfrance\b"]
    assert search["boards"][0]["extra_exclude"] == {"advanced_regex": False, "terms": []}
    assert search["boards"][1]["extra_exclude"]["terms"] == ["senior", r"\bsr\b"]
    assert search["contract_types"] == ["CDI", "CDD", "VIE", "Freelance"]
    assert search["remote_stance"] == "any"
    assert (search["min_fit"], search["days"]) == (70, 30)


def test_min_fit_and_days_from_boards_json_win(tmp_path: Path) -> None:
    boards = json.dumps({"min_fit": 80, "days": 14, "boards": []})
    search = sections(tmp_path, boards=boards)["search"]
    assert (search["min_fit"], search["days"]) == (80, 14)
    assert search["title_include"] == {"advanced_regex": False, "terms": []}


def test_missing_optional_files_give_empty_sections(tmp_path: Path) -> None:
    document = convert(write_sources(tmp_path, skills=None, boards=None, photo=None), now=NOW)
    assert document.photo is None
    out = json.loads(document.to_json())
    assert "photo" not in out
    skills = out["sections"]["skills"]
    assert skills == {
        "years_experience": None,
        "have": [],
        "adjacent": [],
        "none": [],
        "gates": [],
    }
    search = out["sections"]["search"]
    assert search["boards"] == []
    assert search["never_apply"] == [
        "No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"
    ]
    assert completeness(out["sections"]).sections["search"].missing == [
        "title_include",
        "location",
        "interactive_sites",
    ]


def test_facts_without_practicalities_or_identity(tmp_path: Path) -> None:
    facts = "## EMPLOYMENT\n\n| E | Role | D | L |\n|---|---|---|---|\n| Acme | Dev | x | |\n"
    out = sections(tmp_path, facts=facts)
    assert out["answers"]["work_authorization"] == []
    assert out["answers"]["motivation"] is None
    assert out["identity"]["full_name"] == "ADA EXAMPLE"  # no proper case to borrow
    assert out["identity"]["headline"] is None
    assert out["experience"]["employers"][0]["location"] is None
    assert out["experience"]["education"][0]["start"] is None


@pytest.mark.parametrize("missing", ["facts", "assemble"])
def test_missing_required_file(tmp_path: Path, missing: str) -> None:
    with pytest.raises(ConversionError, match="file not found"):
        convert(write_sources(tmp_path, **{missing: None}))


def test_employer_missing_from_the_facts_table(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="no row with a role for 'Acme'"):
        convert(write_sources(tmp_path, facts="## EMPLOYMENT\n\nnothing here\n"))


@pytest.mark.parametrize(
    ("dates", "message"),
    [
        ("Sometime", "cannot read the dates"),
        ("Present \u2013 Present", "the start cannot be Present"),
        ("2023 \u2013 2024", "cannot read the month"),
        ("Brumaire 2023 \u2013 Present", "cannot read the month"),
    ],
)
def test_bad_employer_dates(tmp_path: Path, dates: str, message: str) -> None:
    assemble = ASSEMBLE.replace("March 2023 \u2013 Present", dates)
    with pytest.raises(ConversionError, match=message):
        convert(write_sources(tmp_path, assemble=assemble))


@pytest.mark.parametrize(
    ("photo", "message"),
    [
        ("not base64 at all!", "not valid base64"),
        (base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode(), "not a PNG"),
        ("data:image/gif;base64," + base64.b64encode(b"GIF89a....").decode(), "not a PNG"),
    ],
)
def test_bad_photo_file(tmp_path: Path, photo: str, message: str) -> None:
    with pytest.raises(ConversionError, match=message):
        convert(write_sources(tmp_path, photo=photo))


def test_bare_base64_jpeg_with_line_breaks(tmp_path: Path) -> None:
    jpeg = images.jpeg_with_gps(orientation=1)
    wrapped = base64.encodebytes(jpeg).decode()  # 76-character lines
    document = convert(write_sources(tmp_path, photo=wrapped))
    assert document.photo is not None
    assert document.photo.mime == "image/jpeg"
    # Re-encoded as the app stores it: same picture, no metadata.
    assert document.photo.image() == normalize(jpeg).data
    assert images.opened(document.photo.image()).size == (40, 30)
    assert images.metadata(document.photo.image()) == {}


def test_photo_is_re_encoded_and_stripped(tmp_path: Path) -> None:
    """The engine photo goes through the upload pipeline: EXIF orientation applied, GPS, author,
    XMP and ICC gone, longest side at most 1024 px, so the file stays far below the limit."""
    big = images.save(
        images.halves(2400, 1200), "JPEG", quality=100, exif=images.gps_exif(6).tobytes()
    )
    document = convert(write_sources(tmp_path, photo=base64.b64encode(big).decode()))
    assert document.photo is not None
    stored = document.photo.image()
    assert images.opened(stored).size == (512, 1024)  # turned upright, then downscaled
    assert images.metadata(stored) == {}
    assert len(stored) < len(big)


def test_photo_that_does_not_decode(tmp_path: Path) -> None:
    truncated = images.png()[:-30]
    with pytest.raises(ConversionError, match=r"photo\.b64: The image could not be read"):
        convert(write_sources(tmp_path, photo=base64.b64encode(truncated).decode()))


def test_invalid_json_source(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match=r"skills\.json: not valid JSON"):
        convert(write_sources(tmp_path / "a", skills="{nope"))
    with pytest.raises(ConversionError, match=r"skills\.json: expected an object"):
        convert(write_sources(tmp_path / "b", skills="[]"))
    with pytest.raises(ConversionError, match=r"boards\.json: expected an object"):
        convert(write_sources(tmp_path / "c", boards="3"))


def test_non_utf8_source(tmp_path: Path) -> None:
    paths = write_sources(tmp_path)
    paths.facts.write_bytes(b"## X\n\n\xff\xfe broken")
    with pytest.raises(ConversionError, match="cannot be read as UTF-8"):
        convert(paths)


def test_a_rule_broken_by_the_sources_is_a_validation_error(tmp_path: Path) -> None:
    long_regex = "|".join(f"(?:word{i}x|other{i}y)" for i in range(10))  # groups not expandable
    boards = json.dumps({"title_re": f"({long_regex}){'z' * 400}", "boards": []})
    with pytest.raises(ValidationError, match=r"sections\.search\.title_include\.terms\.0"):
        convert(write_sources(tmp_path, boards=boards))


def test_crlf_sources(tmp_path: Path) -> None:
    out = sections(tmp_path, facts=FACTS.replace("\n", "\r\n"))
    assert out == sections(tmp_path, facts=FACTS)


# --- assemble.py parsing ------------------------------------------------------------------------


def test_assemble_parsing_is_robust_to_comments_order_and_other_code() -> None:
    source = """
# a comment before anything
import sys
raise SystemExit("this file must never run")
EMPLOYERS: dict = {  # annotated, first
    "x": ("X", "May 2020 \u2013 June 2021"),  # trailing comment
}
def build():
    NAME = "NOT THIS ONE"  # inside a function: not a module constant
LANGUAGES = [("English", "C1")]
CONTACT_RIGHT = ["a@b.org"]
NAME = "FIRST"
EDUCATION = []
CONTACT_LEFT = []
NAME = "LAST"  # the last assignment wins, as at run time
if True:
    NAME = "conditional, ignored"
"""
    constants = parse_assemble(source)
    assert constants == {
        "EMPLOYERS": {"x": ("X", "May 2020 \u2013 June 2021")},
        "LANGUAGES": [("English", "C1")],
        "CONTACT_RIGHT": ["a@b.org"],
        "NAME": "LAST",
        "EDUCATION": [],
        "CONTACT_LEFT": [],
    }


def test_assemble_parsing_never_executes_code(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    source = ASSEMBLE + f"\nopen({str(marker)!r}, 'w').write('x')\nNAME = 'X'\n"
    assert parse_assemble(source)["NAME"] == "X"
    assert not marker.exists()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("NAME = 'x'\n", "missing CONTACT_LEFT, CONTACT_RIGHT, EDUCATION, LANGUAGES, EMPLOYERS"),
        (ASSEMBLE + "NAME = compute()\n", "NAME is not a literal value"),
        (ASSEMBLE + "EMPLOYERS = {**OTHER}\n", "EMPLOYERS is not a literal value"),
        ("def broken(:\n", "not valid Python"),
    ],
)
def test_assemble_parsing_errors(source: str, message: str) -> None:
    with pytest.raises(ConversionError, match=re.escape(message)):
        parse_assemble(source)


# --- facts.md blocks ----------------------------------------------------------------------------


def test_split_blocks_edge_cases() -> None:
    markdown = (
        "No title, just an intro.\n"
        "## One\n"
        "body one\n"
        "```\n"
        "## not a heading inside a fence\n"
        "```\n"
        "### kept\n"
        "## one\n"
        "duplicate title\n"
        "## Empty\n"
        "\n"
        "## Trailing\n"
        "  indented body stays indented\n"
    )
    blocks = split_blocks(markdown)
    assert [(b.title, b.body) for b in blocks] == [
        ("Introduction", "No title, just an intro."),
        ("One", "body one\n```\n## not a heading inside a fence\n```\n### kept"),
        ("one (2)", "duplicate title"),
        ("Empty", "(empty)"),
        ("Trailing", "  indented body stays indented"),
    ]


def test_split_blocks_title_only_or_nothing() -> None:
    assert split_blocks("") == []
    assert split_blocks("# Title only\n\n") == []
    assert [b.title for b in split_blocks("# T\n\nintro\n## A\nx")] == ["T", "A"]


# --- regex splitting ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "parts"),
    [
        ("a|b", ["a", "b"]),
        ("a", ["a"]),
        ("", [""]),
        ("a(b|c)|d", ["a(b|c)", "d"]),
        (r"a\|b|c", [r"a\|b", "c"]),
        ("[|]|x", ["[|]", "x"]),
        ("[]|]|x", ["[]|]", "x"]),
        ("[^]|]|x", ["[^]|]", "x"]),
        (r"[\]|]|x", [r"[\]|]", "x"]),
        ("((a|b)|c)|d", ["((a|b)|c)", "d"]),
    ],
)
def test_split_alternatives(pattern: str, parts: list[str]) -> None:
    assert split_alternatives(pattern) == parts


@pytest.mark.parametrize(
    ("alternative", "expanded"),
    [
        (r"\b(a|b)\b", [r"\ba\b", r"\bb\b"]),
        ("(a|b)[ -]x", ["a[ -]x", "b[ -]x"]),
        ("x(?:a|b)", ["xa", "xb"]),
        ("(a|b)+", ["(a|b)+"]),  # quantified group
        ("(a|b)?c", ["(a|b)?c"]),
        ("(a|b){2}", ["(a|b){2}"]),
        ("(?=a|b)c", ["(?=a|b)c"]),  # lookahead
        ("(?i:a|b)", ["(?i:a|b)"]),  # flags
        ("(?P<n>a|b)", ["(?P<n>a|b)"]),  # named group
        (r"(a|b)\1", [r"(a|b)\1"]),  # back reference
        ("(a)(b|c)", ["(a)(b|c)"]),  # two groups
        ("((a|b)|c)", ["((a|b)|c)"]),  # nested
        ("(abc)", ["(abc)"]),  # nothing to expand
        ("plain", ["plain"]),
    ],
)
def test_expand_alternative(alternative: str, expanded: list[str]) -> None:
    assert expand_alternative(alternative) == expanded


@pytest.mark.parametrize(
    "pattern",
    [
        r"\b(cyber|security|vp)\b|(arabic|dutch)[ -]speaking",
        r"forward.deployed|solutions? (architect|engineer)|ai (platform|product) engineer",
        r"paris|\buk\b|zürich|(?:a|b)c",
        r"x(a|b)+y|(?=lead)lead|c\+\+|[|(]",
        r"senior|\bsr\b|enterprise accounts",
    ],
)
def test_regex_terms_match_like_the_regex(pattern: str) -> None:
    """A property check on many strings: the split list decides exactly as the original."""
    old = re.compile(pattern, re.I)
    words = [*re.findall(r"[a-zü+|(]+", pattern), "", "-", " ", "x"]
    texts = {f"{a} {b}" for a in words for b in words}
    texts |= {f"{a}{b}" for a in words for b in words}
    texts |= {f"{a}-{b}".upper() for a in words for b in words}
    terms = regex_terms(pattern)
    for text in texts:
        assert matching.matches(terms, True, text) is (old.search(text) is not None), text


@pytest.mark.parametrize(
    "term",
    ["agent", "tool use", "c#", ".net", "node.js", "ci/cd", "a/b test", "fine[- ]?tun\\w*",
     "c\\+\\+", "\\bia\\b", "ia générative", "trust & safety", "0 to 1"],
)  # fmt: skip
def test_wrapped_skill_terms_match_like_the_engine(term: str) -> None:
    engine = re.compile(
        r"(?<!\w)(?:" + (term if "\\" in term else re.escape(term)) + r")(?:s|es)?(?!\w)", re.I
    )
    wrapped = re.compile(wrap_skill_term(term), re.I)
    base = re.sub(r"\\[bw+]|\[[^]]*\]|[?*]", "", term).replace("\\", "") or "x"
    texts = [
        base,
        f"{base}s",
        f"{base}es",
        f"{base}x",
        f"x{base}",
        f"({base})",
        f"{base}.",
        f"a {base}, b",
        base.upper(),
        f"_{base}_",
        f"{base}-y",
    ]
    for text in texts:
        assert [m.group() for m in wrapped.finditer(text)] == [
            m.group() for m in engine.finditer(text)
        ], text


def test_plain_terms_semantics() -> None:
    assert matching.matches(["agent"], False, "Agents and more")
    assert not matching.matches(["agent"], False, "agentic")
    assert not matching.matches(["agent"], False, "reagent")
    assert matching.matches(["c#"], False, "C# developer")
    assert matching.matches(["node.js"], False, "node.js")
    assert not matching.matches(["node.js"], False, "nodexjs")  # the dot is literal
    assert not matching.matches([], False, "anything")
    assert matching.compile_terms([], True) is None
    assert matching.plain_pattern("a.b") == r"(?<!\w)a\.b(?:s|es)?(?!\w)"


def test_the_owner_fixture_is_the_importer_output_of_its_invented_sources(tmp_path: Path) -> None:
    """fixtures/owner_export.json, read by the export, sweep and web tests, is what the importer
    makes of fixtures/owner_sources (an invented person). After an intended change of those
    sources or of the converter, regenerate it with UPDATE_GOLDEN=1 and review the diff."""
    photo = tmp_path / "photo.b64"
    photo.write_text(base64.b64encode(images.jpeg(420, 420)).decode(), encoding="utf-8")
    paths = dataclasses.replace(SourcePaths.under(FIXTURES / "owner_sources"), photo=photo)
    document = convert(paths, now=datetime(2026, 10, 8, tzinfo=UTC))
    view = images.golden_view(json.loads(document.to_json()))
    text = json.dumps(view, ensure_ascii=False, indent=2) + "\n"
    golden = FIXTURES / "owner_export.json"
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(text, encoding="utf-8")
    assert golden.read_text(encoding="utf-8") == text
