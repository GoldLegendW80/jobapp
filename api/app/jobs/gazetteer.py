"""Where a posting is: the port of the engine's map gazetteer (engine/places.json and `placesOf`
in engine/dashboard.html) and the aggregation behind GET /api/jobs/places.

`places.json` next to this module is a byte-for-byte copy of engine/places.json: a list of cities
and a list of countries, each with a display name, coordinates and the aliases matched against
the location text. It is Harald's own data (no third-party licence). The land outlines the map
draws (Natural Earth 110m, public domain) are a frontend asset, web/src/features/jobs/map/.

Matching rules, identical to the engine's (a parity test runs the engine's own JavaScript on every
real location):
- the location is folded: lower case, then NFD, then the combining marks U+0300 to U+036F removed
  ("Zürich" reads "zurich", "Montréal" reads "montreal");
- an alias matches when it is preceded by the start of the text or a character other than a to z,
  and not followed by a to z ("us" matches "Remote, US" but not "Austin" or "business");
- every city named gets a pin, in gazetteer order; only when no city is named, every country
  named gets one; nothing named, the location is unplaced ("Remote", "", "EMEA").
"""

import functools
import json
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.jobs.models import JobStatus

GAZETTEER_PATH = Path(__file__).with_name("places.json")
JOB_IDS_MAX = 200  # job ids listed per place; `count` is always the full number

PlaceKind = Literal["city", "country"]

_MARKS = re.compile("[̀-ͯ]")


@dataclass(frozen=True)
class Place:
    name: str
    kind: PlaceKind
    lat: float
    lon: float
    aliases: tuple[str, ...]
    pattern: re.Pattern[str] = field(compare=False, repr=False)


def fold(text: str | None) -> str:
    """The engine's `fold`: lower case, NFD, combining marks U+0300 to U+036F dropped."""
    return _MARKS.sub("", unicodedata.normalize("NFD", (text or "").lower()))


def _compile(entry: dict[str, object], kind: PlaceKind) -> Place:
    aliases = tuple(str(alias) for alias in entry["aliases"])  # type: ignore[attr-defined]
    if not aliases:
        raise ValueError(f"{entry['name']}: a place needs at least one alias")
    # The engine's RegExp, character for character: (^|[^a-z])(?:a|b|c)(?![a-z]).
    pattern = re.compile("(^|[^a-z])(?:" + "|".join(map(re.escape, aliases)) + ")(?![a-z])")
    return Place(
        name=str(entry["name"]),
        kind=kind,
        lat=float(entry["lat"]),  # type: ignore[arg-type]
        lon=float(entry["lon"]),  # type: ignore[arg-type]
        aliases=aliases,
        pattern=pattern,
    )


@dataclass(frozen=True)
class Gazetteer:
    cities: tuple[Place, ...]
    countries: tuple[Place, ...]

    @classmethod
    def from_json(cls, data: dict[str, list[dict[str, object]]]) -> "Gazetteer":
        return cls(
            cities=tuple(_compile(entry, "city") for entry in data["cities"]),
            countries=tuple(_compile(entry, "country") for entry in data["countries"]),
        )

    def places_of(self, location: str | None) -> tuple[Place, ...]:
        """Every city the text names, else every country it names, else nothing."""
        text = fold(location)
        hits = tuple(place for place in self.cities if place.pattern.search(text))
        if not hits:
            hits = tuple(place for place in self.countries if place.pattern.search(text))
        return hits


@functools.cache
def gazetteer() -> Gazetteer:
    """The bundled gazetteer, read once per process."""
    return Gazetteer.from_json(json.loads(GAZETTEER_PATH.read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=4096)
def places_of(location: str | None) -> tuple[Place, ...]:
    """The bundled gazetteer's places of a location text (cached: locations repeat a lot)."""
    return gazetteer().places_of(location)


# --- aggregation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class MapJob:
    """The columns of a job the map needs."""

    id: uuid.UUID
    location: str
    status: JobStatus
    fit: int | None
    title: str


_RANK = {status: index for index, status in enumerate(JobStatus)}


def _panel_order(job: MapJob) -> tuple[int, int, str, str]:
    """The engine's side panel order: pipeline order, then best fit (unscored last)."""
    return (_RANK[job.status], -(job.fit if job.fit is not None else -1), job.title, str(job.id))


@dataclass
class PlaceGroup:
    place: Place
    jobs: list[MapJob] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.jobs)

    def counts_by_status(self) -> dict[JobStatus, int]:
        counts = Counter(job.status for job in self.jobs)
        return {status: counts[status] for status in JobStatus if counts[status]}

    def job_ids(self, limit: int = JOB_IDS_MAX) -> list[uuid.UUID]:
        return [job.id for job in sorted(self.jobs, key=_panel_order)[:limit]]


@dataclass(frozen=True)
class MapSummary:
    groups: list[PlaceGroup]  # most postings first, then by name
    unplaced: list[tuple[str, int]]  # location text (stripped, "" when absent), postings
    total: int
    placed: int


def summarize(
    jobs: Iterable[MapJob], lookup: Callable[[str | None], Sequence[Place]] = places_of
) -> MapSummary:
    """Group jobs by the places their location names. A job naming two cities counts in both
    pins (as in the engine); `placed` counts each job once."""
    groups: dict[str, PlaceGroup] = {}
    unplaced: Counter[str] = Counter()
    total = placed = 0
    for job in jobs:
        total += 1
        hits = lookup(job.location)
        if not hits:
            unplaced[(job.location or "").strip()] += 1
            continue
        placed += 1
        for place in hits:
            groups.setdefault(place.name, PlaceGroup(place)).jobs.append(job)
    ordered = sorted(groups.values(), key=lambda g: (-g.count, g.place.name))
    return MapSummary(
        groups=ordered,
        unplaced=sorted(unplaced.items(), key=lambda item: (-item[1], item[0])),
        total=total,
        placed=placed,
    )
