"""The gazetteer port (app/jobs/gazetteer.py): folding, alias boundaries, cities before
countries, several places in one location, empty and unknown locations, the aggregation, and
parity with the engine's own JavaScript (`fold`, `matcher`, `placesOf` of engine/dashboard.html,
run with node) on every real location of ../data/jobs.csv and on the edge cases below.

The parity tests are skipped, with the reason, where the engine, the data or node are absent
(CI, containers). Set JOBAPP_SOURCE_DIR to the job folder to point at them explicitly.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.jobs import gazetteer
from app.jobs.gazetteer import Gazetteer, MapJob, fold, places_of, summarize
from app.jobs.models import JobStatus

ENGINE_FILES = ("engine/dashboard.html", "engine/places.json", "data/jobs.csv")


def names(location: str | None) -> list[str]:
    return [place.name for place in places_of(location)]


# --- folding and matching -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Zürich", "zurich"),
        ("Montréal, Québec", "montreal, quebec"),
        ("SÃO PAULO", "sao paulo"),
        ("Malmö", "malmo"),
        ("", ""),
        (None, ""),
        ("İstanbul", "istanbul"),  # the dotted capital I lowers to i plus a combining dot
        ("ΣΟΦΙΑΣ", "σοφιας"),  # final sigma, as JavaScript's toLowerCase
        ("Łódź", "łodz"),  # the stroke is a letter, not a combining mark: kept as in the engine
    ],
)
def test_fold_matches_the_engine(text: str | None, folded: str) -> None:
    assert fold(text) == folded


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Paris, France", ["Paris"]),
        ("paris", ["Paris"]),
        ("PARIS (hybrid)", ["Paris"]),
        ("La Défense", ["Paris"]),  # an alias of Paris, accent folded
        ("Courbevoie, Île-de-France", ["Paris"]),
        ("Zürich, Switzerland", ["Zurich"]),
        ("Montréal, Canada, VIE 24 months", ["Montreal"]),
        # Several cities: every one gets a pin, in gazetteer order, not text order.
        ("Paris, London", ["Paris", "London"]),
        ("London or Paris", ["Paris", "London"]),
        ("New York; San Francisco; Seattle", ["San Francisco Bay Area", "Seattle", "New York"]),
        # A city hides the countries: no France pin for "Paris, France".
        ("Remote, Paris, France, Germany", ["Paris"]),
        # No city: every country named.
        ("Remote, United States", ["United States"]),
        ("France", ["France"]),
        ("Remote, France or Spain", ["France", "Spain"]),
        ("Remote, US", ["United States"]),
        ("U.S.", ["United States"]),
        # Word boundaries: "us" is not inside "Austin" or "business", "nice" not in "Venice".
        ("Austin, Texas", []),
        ("business travel", []),
        ("Venice", []),
        ("Nicely remote", []),
        # Unplaced: nothing named, or nothing known.
        ("Remote", []),
        ("", []),
        (None, []),
        ("   ", []),
        ("EMEA", []),
        ("Atlantis", []),
        ("東京", []),
        ("🌍 anywhere", []),
        ("x" * 10_000, []),
    ],
)
def test_places_of(location: str | None, expected: list[str]) -> None:
    assert names(location) == expected


def test_kinds_and_coordinates() -> None:
    (paris,) = places_of("Paris")
    assert (paris.kind, paris.lat, paris.lon) == ("city", 48.86, 2.35)
    (usa,) = places_of("USA")
    assert usa.kind == "country"
    assert -90 <= usa.lat <= 90
    assert -180 <= usa.lon <= 180


def test_bundled_gazetteer_is_well_formed() -> None:
    data = json.loads(gazetteer.GAZETTEER_PATH.read_text(encoding="utf-8"))
    assert set(data) == {"cities", "countries"}
    seen: set[str] = set()
    for kind in ("cities", "countries"):
        assert data[kind], kind
        for entry in data[kind]:
            assert entry["name"] not in seen, entry["name"]
            seen.add(entry["name"])
            assert -90 <= entry["lat"] <= 90
            assert -180 <= entry["lon"] <= 180
            assert entry["aliases"]
            # Aliases are matched against folded text, so they must be folded themselves.
            assert all(alias == fold(alias) for alias in entry["aliases"]), entry["name"]


def test_alias_with_regex_characters_is_literal() -> None:
    custom = Gazetteer.from_json(
        {
            "cities": [{"name": "Dot", "lat": 0, "lon": 0, "aliases": ["a.b"]}],
            "countries": [],
        }
    )
    assert [p.name for p in custom.places_of("a.b")] == ["Dot"]
    assert custom.places_of("axb") == ()


def test_a_place_without_alias_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one alias"):
        Gazetteer.from_json(
            {"cities": [{"name": "X", "lat": 0, "lon": 0, "aliases": []}], "countries": []}
        )


# --- aggregation --------------------------------------------------------------------------------


def job(location: str, status: JobStatus = JobStatus.found, fit: int | None = None) -> MapJob:
    return MapJob(id=uuid.uuid4(), location=location, status=status, fit=fit, title=location)


def test_summarize_groups_counts_and_unplaced() -> None:
    jobs = [
        job("Paris, France", JobStatus.applied, 80),
        job("Paris", JobStatus.found, 90),
        job("Paris, London", JobStatus.found, None),
        job("London, UK", JobStatus.skipped, 10),
        job("Remote"),
        job(" Remote "),
        job(""),
        job("Atlantis"),
    ]
    summary = summarize(jobs)
    assert summary.total == 8
    assert summary.placed == 4  # "Paris, London" placed once
    assert [(g.place.name, g.count) for g in summary.groups] == [("Paris", 3), ("London", 2)]
    paris = summary.groups[0]
    assert paris.counts_by_status() == {JobStatus.found: 2, JobStatus.applied: 1}
    # Panel order: pipeline order, then best fit, unscored last.
    assert paris.job_ids() == [jobs[1].id, jobs[2].id, jobs[0].id]
    assert summary.unplaced == [("Remote", 2), ("", 1), ("Atlantis", 1)]


def test_summarize_caps_job_ids() -> None:
    jobs = [job("Paris", fit=i % 100) for i in range(gazetteer.JOB_IDS_MAX + 5)]
    (group,) = summarize(jobs).groups
    assert group.count == gazetteer.JOB_IDS_MAX + 5
    assert len(group.job_ids()) == gazetteer.JOB_IDS_MAX


def test_summarize_ties_are_by_name() -> None:
    summary = summarize([job("London"), job("Bordeaux")])
    assert [g.place.name for g in summary.groups] == ["Bordeaux", "London"]


def test_summarize_nothing() -> None:
    summary = summarize([])
    assert (summary.groups, summary.unplaced, summary.total, summary.placed) == ([], [], 0, 0)


# --- parity with the engine ---------------------------------------------------------------------


def _find_job_root() -> Path | None:
    explicit = os.environ.get("JOBAPP_SOURCE_DIR")
    candidates = [Path(explicit)] if explicit else list(Path(__file__).resolve().parents)
    for parent in candidates:
        if all((parent / name).is_file() for name in ENGINE_FILES):
            return parent
    return None


ROOT = _find_job_root()
NODE = shutil.which("node")
engine_only = pytest.mark.skipif(
    ROOT is None or NODE is None,
    reason="the engine's dashboard.html, places.json and data/jobs.csv (or node) are not on this "
    "machine; set JOBAPP_SOURCE_DIR to the job folder to run the parity test",
)

# Everything from `const fold` to the end of `function placesOf` in engine/dashboard.html.
ENGINE_SNIPPET = re.compile(
    r"^const fold = .*?^function placesOf\(loc\) \{.*?^\}$", re.MULTILINE | re.DOTALL
)


def engine_places(locations: list[str | None]) -> list[list[str]]:
    """The engine's own placesOf on each location, names in order."""
    assert ROOT is not None
    assert NODE is not None
    html = (ROOT / "engine/dashboard.html").read_text(encoding="utf-8")
    snippet = ENGINE_SNIPPET.search(html)
    assert snippet, "placesOf not found in engine/dashboard.html"
    script = (
        f"const PLACES = {(ROOT / 'engine/places.json').read_text(encoding='utf-8')};\n"
        f"{snippet.group(0)}\n"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "process.stdout.write(JSON.stringify(input.map((l) => placesOf(l).map((p) => p.name))));\n"
    )
    out = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(locations),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(out.stdout)


def real_locations() -> list[str]:
    assert ROOT is not None
    with (ROOT / "data/jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
        return [row["location"] for row in csv.DictReader(handle)]


@engine_only
def test_bundled_gazetteer_is_the_engines() -> None:
    assert ROOT is not None
    assert gazetteer.GAZETTEER_PATH.read_bytes() == (ROOT / "engine/places.json").read_bytes()


@engine_only
def test_parity_with_the_engine_on_every_real_location() -> None:
    locations = real_locations()
    assert len(locations) >= 433
    expected = engine_places(locations)
    got = [names(location) for location in locations]
    mismatches = [(loc, e, g) for loc, e, g in zip(locations, expected, got, strict=True) if e != g]
    assert mismatches == []
    placed = sum(1 for names_ in got if names_)
    # The numbers of the report: same placed and unplaced decisions as the engine.
    print(
        f"parity: {len(locations)} rows, {len(set(locations))} distinct locations, "
        f"placed {placed}, unplaced {len(locations) - placed}, mismatches 0"
    )


@engine_only
def test_parity_with_the_engine_on_edge_cases() -> None:
    cases: list[str | None] = [
        "Paris, London",
        "Remote",
        "",
        "Zürich",
        "La Défense",
        "İstanbul",
        "ΣΟΦΙΑΣ",
        "東京",
        "Austin, Texas",
        "Remote, US",
        "U.S.",
        "Montréal",
        "Nicely",
        "Sète",
        "Villeneuve-d'Ascq",
        "New York; San Francisco",
        "x" * 5000,
    ]
    assert engine_places(cases) == [names(case) for case in cases]
