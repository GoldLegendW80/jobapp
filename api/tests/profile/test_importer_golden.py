"""The importer against Harald's real files (../profile, ../engine, ../data/jobs.csv, read only).

Skipped, with the reason, where those files do not exist (CI, containers). Set JOBAPP_SOURCE_DIR
to the job folder to point at them explicitly. The golden output of those files holds personal
data, so it lives beside them, never in this public repository: <job folder>/jobapp-golden/
profile_export.json. Write it the first time, and regenerate it after an intended change of the
sources or of the converter, with:

    UPDATE_GOLDEN=1 uv run --native-tls pytest tests/profile/test_importer_golden.py

then review its diff by eye. tests/profile/fixtures/owner_export.json is a different file: the
importer output of the invented sources in tests/profile/fixtures/owner_sources (test_importer.py).
"""

import ast
import base64
import csv
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import ImageChops, ImageStat

from app.profile import matching
from app.profile.importer import SourcePaths, convert, regex_terms, wrap_skill_term
from app.profile.schemas import ExportDocument
from tests.profile import images

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def _find_job_root() -> Path | None:
    explicit = os.environ.get("JOBAPP_SOURCE_DIR")
    candidates = [Path(explicit)] if explicit else list(Path(__file__).resolve().parents)
    for parent in candidates:
        if (parent / "profile" / "facts.md").is_file() and (
            parent / "engine" / "assemble.py"
        ).is_file():
            return parent
    return None


ROOT = _find_job_root()
GOLDEN = ROOT / "jobapp-golden" / "profile_export.json" if ROOT else None
pytestmark = pytest.mark.skipif(
    ROOT is None,
    reason="Harald's engine files (profile/facts.md, engine/assemble.py) are not on this machine; "
    "set JOBAPP_SOURCE_DIR to the job folder to run the golden test",
)


@pytest.fixture(scope="module")
def root() -> Path:
    assert ROOT is not None
    return ROOT


@pytest.fixture(scope="module")
def document(root: Path) -> ExportDocument:
    return convert(SourcePaths.under(root), now=NOW)


@pytest.fixture(scope="module")
def out(document: ExportDocument) -> dict[str, Any]:
    return json.loads(document.to_json())


def test_golden(out: dict[str, Any]) -> None:
    assert GOLDEN is not None
    text = json.dumps(images.golden_view(out), ensure_ascii=False, indent=2) + "\n"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    if not GOLDEN.is_file():
        pytest.skip(f"no golden output yet: run with UPDATE_GOLDEN=1 to write {GOLDEN}")
    assert GOLDEN.read_text(encoding="utf-8") == text


# --- everything in the sources is in the output -------------------------------------------------


@pytest.fixture(scope="module")
def constants(root: Path) -> dict[str, Any]:
    tree = ast.parse((root / "engine" / "assemble.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return found


def test_every_employer_is_there_with_its_dates(
    out: dict[str, Any], constants: dict[str, Any]
) -> None:
    employers = out["sections"]["experience"]["employers"]
    assert [e["key"] for e in employers] == list(constants["EMPLOYERS"])
    names = [
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
    ]  # fmt: skip
    for employer in employers:
        organisation, dates = constants["EMPLOYERS"][employer["key"]]
        assert employer["organisation"] == organisation
        start, end = re.split(r" – ", dates)
        month, year = start.split()
        assert employer["start"] == f"{year}-{names.index(month.lower()) + 1:02d}"
        if end == "Present":
            assert employer["end"] is None
        else:
            month, year = end.split()
            assert employer["end"] == f"{year}-{names.index(month.lower()) + 1:02d}"
        assert employer["title"]


def test_employer_titles_and_places_come_from_the_facts_table(
    out: dict[str, Any], root: Path
) -> None:
    facts = (root / "profile" / "facts.md").read_text(encoding="utf-8")
    for employer in out["sections"]["experience"]["employers"]:
        row = next(
            line
            for line in facts.splitlines()
            if line.startswith("|") and employer["organisation"] in line
        )
        cells = [c.strip().strip("*") for c in row.strip("|").split("|")]
        assert cells[1] == employer["title"]
        assert cells[3] == employer["location"]


def test_every_school_is_there_word_for_word(
    out: dict[str, Any], constants: dict[str, Any]
) -> None:
    schools = out["sections"]["experience"]["education"]
    assert len(schools) == len(constants["EDUCATION"])
    for school, (degree, description) in zip(schools, constants["EDUCATION"], strict=True):
        assert school["degree"] == degree
        details = school["details"]
        if details is None:
            rebuilt = school["school"]
        elif details[0].isdigit():
            rebuilt = f"{school['school']} {details}"
        else:
            rebuilt = f"{school['school']}. {details}"
        assert rebuilt == description


def test_identity_contact_and_languages(out: dict[str, Any], constants: dict[str, Any]) -> None:
    identity = out["sections"]["identity"]
    assert identity["full_name"].upper() == constants["NAME"]
    assert identity["headline"] == "GenAI Engineer"
    urls = [identity["linkedin_url"], identity["website"], *(x["url"] for x in identity["links"])]
    assert urls == [url for url, _ in constants["CONTACT_LEFT"]]
    phone, email, place = constants["CONTACT_RIGHT"]
    assert (identity["phone"], identity["email"]) == (phone, email)
    assert f"{identity['city']}, {identity['country']}" == place
    assert [(x["name"], x["level"]) for x in identity["languages"]] == constants["LANGUAGES"]


def test_every_skills_category_term_and_gate(out: dict[str, Any], root: Path) -> None:
    source = json.loads((root / "profile" / "skills.json").read_text(encoding="utf-8"))
    skills = out["sections"]["skills"]
    assert skills["years_experience"] == source["years_experience"]
    for level in ("have", "adjacent", "none"):
        assert [c["name"] for c in skills[level]] == list(source[level])
        for category in skills[level]:
            terms = source[level][category["name"]]
            raw = any("\\" in term for term in terms)
            assert category["advanced_regex"] is raw
            expected = [wrap_skill_term(t) for t in terms] if raw else terms
            assert category["terms"] == expected
    assert [g["name"] for g in skills["gates"]] == list(source["gates"])
    for gate in skills["gates"]:
        assert gate == {
            "name": gate["name"],
            "terms": [source["gates"][gate["name"]]],
            "advanced_regex": True,
            "penalty": 25,
        }


def test_every_board_and_search_setting(out: dict[str, Any], root: Path) -> None:
    source = json.loads((root / "engine" / "boards.json").read_text(encoding="utf-8"))
    search = out["sections"]["search"]
    assert [(b["company"], b["ats"], b["org"]) for b in search["boards"]] == [
        (b["company"], b["ats"], b["org"]) for b in source["boards"]
    ]
    assert all(b["enabled"] for b in search["boards"])
    for board, original in zip(search["boards"], source["boards"], strict=True):
        if "exclude_re" in original:
            assert board["extra_exclude"]["advanced_regex"] is True
            assert board["extra_exclude"]["terms"] == regex_terms(original["exclude_re"])
        else:
            assert board["extra_exclude"] == {"advanced_regex": False, "terms": []}
    assert len(source["exclude_re"]) > 500  # the reason it had to be split
    for name in ("title_include", "title_exclude", "location"):
        assert search[name]["advanced_regex"] is True
        assert all(len(term) <= 500 for term in search[name]["terms"])
    assert search["contract_types"] == ["CDI", "CDD", "VIE", "Freelance"]
    assert (search["remote_stance"], search["min_fit"], search["days"]) == ("any", 70, 30)
    assert search["never_apply"] == [
        "No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"
    ]


def test_every_facts_heading_and_all_text(out: dict[str, Any], root: Path) -> None:
    facts = (root / "profile" / "facts.md").read_text(encoding="utf-8")
    blocks = out["sections"]["facts"]["blocks"]
    headings = re.findall(r"^## (.+)$", facts, re.MULTILINE)
    title = re.search(r"^# (.+)$", facts, re.MULTILINE)
    assert title is not None
    assert [b["title"] for b in blocks] == [title.group(1), *headings]
    # Sub-headings stay inside their block.
    for sub in re.findall(r"^### .+$", facts, re.MULTILINE):
        assert sum(sub in b["body"] for b in blocks) == 1
    # Put back together, the blocks are the file, but for blank lines around the headings.
    rebuilt = "\n\n".join(
        [f"# {blocks[0]['title']}\n\n{blocks[0]['body']}"]
        + [f"## {b['title']}\n\n{b['body']}" for b in blocks[1:]]
    )
    assert re.sub(r"\n+", "\n", rebuilt).strip() == re.sub(r"\n+", "\n", facts).strip()


def test_practicalities_are_mapped_into_answers(out: dict[str, Any], root: Path) -> None:
    answers = out["sections"]["answers"]
    facts = " ".join((root / "profile" / "facts.md").read_text(encoding="utf-8").split())
    countries = {w["country"]: w["needs_sponsorship"] for w in answers["work_authorization"]}
    assert countries == {
        "France": False,
        "European Union": False,
        "Switzerland": True,
        "United Kingdom": True,
    }
    assert answers["earliest_start"] == "December 2026 (date fields: 2026-12-01)"
    assert answers["notice_period"] == "Standard French notice period, 1 to 3 months."
    for field in ("salary_policy", "relocation", "motivation", "heard_about", "travel"):
        text = answers[field]
        assert text[:1].isupper()
        # Word for word from facts.md but for the capital letter.
        assert text[1:] in facts, field
    assert answers["leave_blank"][0].startswith("Demographic and voluntary self-identification")


def test_photo_is_the_engine_photo(out: dict[str, Any], root: Path) -> None:
    """The same picture as engine/photo.b64, re-encoded: same type and size, pixels within JPEG
    re-compression noise, and none of the metadata the source carries (Canva document ids, the
    author's name, XMP, Photoshop blocks)."""
    source = (root / "engine" / "photo.b64").read_text().strip()
    mime, _, encoded = source.partition(";base64,")
    original = images.opened(base64.b64decode(encoded))
    stored = base64.b64decode(out["photo"]["base64"])
    converted = images.opened(stored)
    assert out["photo"]["mime"] == mime.removeprefix("data:")
    assert out["photo"]["base64"] != encoded
    scale = min(1.0, 1024 / max(original.size))
    assert converted.size == (round(original.width * scale), round(original.height * scale))
    if scale == 1.0:
        difference = ImageChops.difference(original.convert("RGB"), converted.convert("RGB"))
        mean = sum(ImageStat.Stat(difference).mean) / 3
        assert mean < 3, mean
    assert images.metadata(stored) == {}


# --- the converted regexes decide exactly as the originals --------------------------------------


@pytest.fixture(scope="module")
def postings(root: Path) -> list[dict[str, str]]:
    path = root / "data" / "jobs.csv"
    if not path.is_file():
        pytest.skip(f"{path} not found")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) > 100
    return rows


def samples(rows: list[dict[str, str]], column: str) -> Iterator[str]:
    """Every value of the column, plus variants that probe word edges and case."""
    for row in rows:
        value = row[column]
        yield value
        yield value.upper()
        yield f"x{value}x"
        yield value.replace(" ", "-")


def engine_rx(terms: list[str]) -> re.Pattern[str]:
    """engine/jobs.py `_rx`, copied (the engine is never imported)."""
    parts = [t if "\\" in t else re.escape(t) for t in terms]
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?:s|es)?(?!\w)", re.I)


def probe_words(pattern: str) -> list[str]:
    """Strings built from the words of a regex, to hit every alternative at least once."""
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", pattern)
    return [*words, *(f"{w}s" for w in words), *(f"pre{w}" for w in words), " ".join(words)]


@pytest.mark.parametrize(
    ("key", "section", "column"),
    [
        ("title_re", "title_include", "title"),
        ("exclude_re", "title_exclude", "title"),
        ("location_re", "location", "location"),
    ],
)
def test_search_terms_decide_like_the_regex(
    root: Path,
    out: dict[str, Any],
    postings: list[dict[str, str]],
    key: str,
    section: str,
    column: str,
) -> None:
    source = json.loads((root / "engine" / "boards.json").read_text(encoding="utf-8"))
    old = re.compile(source[key], re.I)
    terms = out["sections"]["search"][section]
    texts = [*samples(postings, column), *probe_words(source[key])]
    hits = 0
    for text in texts:
        expected = old.search(text) is not None
        assert matching.matches(terms["terms"], terms["advanced_regex"], text) is expected, text
        hits += expected
    assert 0 < hits < len(texts)  # the samples exercise both outcomes


def test_board_extra_excludes_decide_like_the_regex(
    root: Path, out: dict[str, Any], postings: list[dict[str, str]]
) -> None:
    source = json.loads((root / "engine" / "boards.json").read_text(encoding="utf-8"))
    for board, original in zip(out["sections"]["search"]["boards"], source["boards"], strict=True):
        if "exclude_re" not in original:
            continue
        old = re.compile(original["exclude_re"], re.I)
        terms = board["extra_exclude"]
        for text in [*samples(postings, "title"), *probe_words(original["exclude_re"])]:
            assert matching.matches(terms["terms"], True, text) is (old.search(text) is not None)


def test_the_whole_title_filter_decides_like_find(
    root: Path, out: dict[str, Any], postings: list[dict[str, str]]
) -> None:
    """engine/jobs.py find keeps a posting when the title matches title_re and not exclude_re,
    and the location matches location_re: same decision for every posting in the CSV."""
    source = json.loads((root / "engine" / "boards.json").read_text(encoding="utf-8"))
    want, skip, where = (
        re.compile(source[k], re.I) for k in ("title_re", "exclude_re", "location_re")
    )
    search = out["sections"]["search"]
    kept = 0
    for row in postings:
        title, location = row["title"], row["location"]
        old = bool(want.search(title) and not skip.search(title) and where.search(location))
        new = (
            matching.matches(search["title_include"]["terms"], True, title)
            and not matching.matches(search["title_exclude"]["terms"], True, title)
            and matching.matches(search["location"]["terms"], True, location)
        )
        assert new is old, (title, location)
        kept += old
    assert kept > 0


def _texts(root: Path, rows: list[dict[str, str]]) -> Iterator[str]:
    for row in rows:
        yield row["title"].lower()
        posting = root / "data" / "jobs" / row["id"] / "posting.txt"
        if posting.is_file():
            yield posting.read_text(encoding="utf-8", errors="replace").lower()


def test_skills_categories_count_like_the_engine(
    root: Path, out: dict[str, Any], postings: list[dict[str, str]]
) -> None:
    """Every category finds the same number of occurrences as engine/jobs.py score, on every
    title and posting text, for plain categories and for the ones converted to regexes."""
    source = json.loads((root / "profile" / "skills.json").read_text(encoding="utf-8"))
    texts = list(_texts(root, postings))
    extra = [w for terms in source["have"].values() for t in terms for w in (t, f"{t}s", f"{t}es")]
    texts.append(" ; ".join(extra).lower())
    seen = 0
    for level in ("have", "adjacent", "none"):
        for category in out["sections"]["skills"][level]:
            old = engine_rx(source[level][category["name"]])
            new = matching.compile_terms(category["terms"], category["advanced_regex"])
            assert new is not None
            for text in texts:
                count = len(old.findall(text))
                assert len(new.findall(text)) == count, (category["name"], text[:80])
                seen += count
    assert seen > 1000


def test_gates_fire_like_the_engine(
    root: Path, out: dict[str, Any], postings: list[dict[str, str]]
) -> None:
    source = json.loads((root / "profile" / "skills.json").read_text(encoding="utf-8"))
    texts = list(_texts(root, postings))
    for gate in out["sections"]["skills"]["gates"]:
        rx = source["gates"][gate["name"]]
        probes = [text.lower() for text in probe_words(rx)] + [
            "phd is required",
            "requires a phd",
            "fluent in german",
            "spanish-speaking",
            "us citizen",
            "security clearance",
        ]
        for text in [*texts, *probes]:
            assert matching.matches(gate["terms"], True, text) is (re.search(rx, text) is not None)
