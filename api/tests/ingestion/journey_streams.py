"""The recorded streams of the G10, G12, G13 and G14 journeys (web/e2e/ingestion-g10.spec.ts,
ingestion-g12.spec.ts, ingestion-g13.spec.ts and ingestion-g14.spec.ts), written from here:
`python -m tests.ingestion.journey_streams` from api/ rewrites
app/agents/fake_streams/ingest_extract_<first 12 hex digits of the fixture's sha256>_p0.json for
each fixture below (and ingest_transcribe_<the same digits>_p0.json for the two fixtures of G14
that are images), and tests/ingestion/test_journey_streams.py fails when a file and this module
differ, when a stream named after a hash has no fixture, when an extraction cites words its
transcription did not store, or when the large stream leaves the range that lets a run pause
between two steps and finish after one Continue.

The fixtures are tests/ingestion/fixtures/g10_*.txt, made up texts about a fictitious person.
Their bytes are what the journey uploads, so the hash in a stream's name is the hash of the
stored document. g10_plain.txt has no stream on purpose: its step replays
`ingest_extract_default`, which reads and proposes nothing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agents import fake_backend
from tests.ingestion.transcribe_helpers import store_call, view_call
from tests.ingestion.transcribe_helpers import stream as transcription

FIXTURES = Path(__file__).parent / "fixtures"
PLAIN = "g10_plain.txt"
LARGE_FIXTURE = "g10_large.txt"
LARGE_TOKENS = 380_000

SMALL: dict[str, Any] = {
    "input_tokens": 700,
    "output_tokens": 40,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 100,
}


def init() -> dict[str, Any]:
    return {
        "message": {
            "type": "system",
            "subtype": "init",
            "session_id": "{session_id}",
            "uuid": "00000000-0000-4000-8000-000000000001",
            "cwd": "/tmp/run",  # noqa: S108, what the CLI prints, not a path this code opens
            "model": "{model}",
            "permissionMode": "dontAsk",
            "tools": ["mcp__app__read_document", "mcp__app__propose_profile_changes"],
            "mcp_servers": [{"name": "app", "status": "connected"}],
            "apiKeySource": "ANTHROPIC_API_KEY",
            "claude_code_version": "2.1.286",
        }
    }


def assistant(number: int, content: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any]:
    calls = any(block["type"] == "tool_use" for block in content)
    return {
        "message": {
            "type": "assistant",
            "session_id": "{session_id}",
            "uuid": f"00000000-0000-4000-8000-0000000001{number:02d}",
            "parent_tool_use_id": None,
            "message": {
                "id": f"msg_g10_{number:03d}",
                "type": "message",
                "role": "assistant",
                "model": "{model}",
                "content": content,
                "stop_reason": "tool_use" if calls else "end_turn",
                "usage": usage,
            },
        }
    }


def result(text: str, turns: int, usages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "message": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1800,
            "duration_api_ms": 1500,
            "num_turns": turns,
            "session_id": "{session_id}",
            "uuid": "00000000-0000-4000-8000-0000000009ff",
            "result": text,
            "stop_reason": "end_turn",
            "total_cost_usd": 0.02,
            "usage": {key: sum(one[key] for one in usages) for key in SMALL},
            "permission_denials": [],
            "terminal_reason": "completed",
        }
    }


def read(number: int) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_g10_{number:03d}",
        "name": "mcp__app__read_document",
        "input": {"offset": 0},
    }


def propose(number: int, section: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_g10_{number:03d}",
        "name": "mcp__app__propose_profile_changes",
        "input": {"section": section, "items": items},
    }


def item(key: str, value: object, excerpt: str) -> dict[str, Any]:
    return {"key": key, "action": "add", "value": value, "citations": [{"excerpt": excerpt}]}


def stream(
    description: str, messages: list[dict[str, Any]], text: str, sleep: float = 0.0
) -> dict[str, Any]:
    steps = [init()]
    usages = []
    for one in messages:
        if sleep:
            steps.append({"sleep": sleep})
        steps.append(one)
        usages.append(one["message"]["message"]["usage"])
    turns = sum(1 for one in messages if one["message"]["message"]["stop_reason"] == "tool_use")
    steps.append(result(text, turns + 1, usages))
    return {"description": description, "steps": steps}


def said(number: int, text: str, usage: dict[str, Any] = SMALL) -> dict[str, Any]:
    return assistant(number, [{"type": "text", "text": text}], usage)


FIRST = stream(
    "G10 journey, tests/ingestion/fixtures/g10_first.txt: reads the part, proposes the name, "
    "the email and one employer, every item passes.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "identity",
                    [
                        item(
                            "full_name",
                            "Alex Martin",
                            "Alex Martin\nEmail: alex.martin@example.org",
                        ),
                        item(
                            "email",
                            "alex.martin@example.org",
                            "Email: alex.martin@example.org",
                        ),
                    ],
                )
            ],
            SMALL,
        ),
        assistant(
            3,
            [
                propose(
                    3,
                    "experience",
                    [
                        item(
                            "employers",
                            {
                                "organisation": "Example Freight",
                                "title": "Backend Engineer",
                                "start": "2020-03",
                                "end": "2023-06",
                                "location": "Nantes",
                            },
                            "Example Freight, Backend Engineer, March 2020 to June 2023, Nantes",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        said(4, "Proposed 3 items."),
    ],
    "Proposed 3 items.",
)

SECOND = stream(
    "G10 journey, tests/ingestion/fixtures/g10_second.txt: a slow step (two seconds before each "
    "message, so a journey can cancel it while it runs) that reads the part and proposes one "
    "fact.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "facts",
                    [
                        item(
                            "blocks",
                            {
                                "title": "Booking tool",
                                "body": "Wrote the made up booking tool of a community garden.",
                            },
                            "Wrote the made up booking tool of a community garden.",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        said(3, "Proposed 1 item."),
    ],
    "Proposed 1 item.",
    sleep=2.0,
)

# The whole step reports LARGE_TOKENS, all but 1,880 of them in its last message, which asks
# for nothing more: the step ends done whatever its budget, and the run pauses before the next.
LAST = {**SMALL, "input_tokens": LARGE_TOKENS - 2_120}
LARGE = stream(
    "G10 journey, tests/ingestion/fixtures/g10_large.txt: reads the part, proposes one fact, and "
    "its last message reports a usage that takes the step to 380,000 tokens: between twice the "
    "high estimate of a run over g10_large.txt and g10_plain.txt and three times that estimate "
    "minus the low estimate of the second step, so the run pauses on its ceiling between its "
    "two steps and finishes after one Continue (tests/ingestion/test_journey_streams.py).",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "facts",
                    [
                        item(
                            "blocks",
                            {
                                "title": "Yearly report",
                                "body": "Wrote the made up yearly report on regional timetables.",
                            },
                            "Wrote the made up yearly report on regional timetables.",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        said(3, "Proposed 1 item.", LAST),
    ],
    "Proposed 1 item.",
)


def _person(number: int) -> dict[str, Any]:
    return propose(
        number,
        "identity",
        [item("full_name", "Sam Rivera", "Sam Rivera\nEmail: sam.rivera@example.org")],
    )


def _employer(organisation: str, title: str, start: str, end: str, city: str, excerpt: str) -> Any:
    value = {
        "organisation": organisation,
        "title": title,
        "start": start,
        "end": end,
        "location": city,
    }
    return item("employers", value, excerpt)


# The G12 journey (web/e2e/ingestion-g12.spec.ts): two documents that agree on a name (one
# proposal, two citations), disagree on a start date (one conflict, both sources), and one of
# which names an employer of its own (the derived years are counted from it).
G12_FIRST = stream(
    "G12 journey, tests/ingestion/fixtures/g12_first.txt: reads the part, proposes a name and "
    "one employer starting in March 2019.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(2, [_person(2)], SMALL),
        assistant(
            3,
            [
                propose(
                    3,
                    "experience",
                    [
                        _employer(
                            "Example Rail",
                            "Data Engineer",
                            "2019-03",
                            "2022-06",
                            "Lyon",
                            "Example Rail, Data Engineer, March 2019 to June 2022, Lyon",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        said(4, "Proposed 2 items."),
    ],
    "Proposed 2 items.",
)

G12_SECOND = stream(
    "G12 journey, tests/ingestion/fixtures/g12_second.txt: reads the part, proposes the same "
    "name, the same employer starting in May 2019, and an earlier employer.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(2, [_person(2)], SMALL),
        assistant(
            3,
            [
                propose(
                    3,
                    "experience",
                    [
                        _employer(
                            "Example Rail",
                            "Data Engineer",
                            "2019-05",
                            "2022-06",
                            "Lyon",
                            "Example Rail, Data Engineer, May 2019 to June 2022, Lyon",
                        ),
                        _employer(
                            "Example Post",
                            "Analyst",
                            "2016-01",
                            "2018-12",
                            "Paris",
                            "Example Post, Analyst, January 2016 to December 2018, Paris",
                        ),
                    ],
                )
            ],
            SMALL,
        ),
        said(4, "Proposed 3 items."),
    ],
    "Proposed 3 items.",
)

# The G13 journey (web/e2e/ingestion-g13.spec.ts) reads the two documents of G12 and this one,
# which gives the review what a bulk accept may take: a headline and four answers, none of them
# a locked fact.
G13_NOTES = stream(
    "G13 journey, tests/ingestion/fixtures/g13_notes.txt: reads the part, proposes a headline "
    "and four answers.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "identity",
                    [
                        item(
                            "headline",
                            "Data engineer for public transport timetables",
                            "Data engineer for public transport timetables",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        assistant(
            3,
            [
                propose(
                    3,
                    "answers",
                    [
                        item("notice_period", "Two months", "Notice period: two months"),
                        item(
                            "earliest_start",
                            "The first of March",
                            "Earliest start: the first of March",
                        ),
                        item(
                            "relocation",
                            "Open to moving within France",
                            "Relocation: open to moving within France",
                        ),
                        item("travel", "Up to two days a month", "Travel: up to two days a month"),
                    ],
                )
            ],
            SMALL,
        ),
        said(4, "Proposed 5 items."),
    ],
    "Proposed 5 items.",
)


# The G14 journey (web/e2e/ingestion-g14.spec.ts) uploads a screenshot and a two page scan, whose
# bytes are the fixtures g14_screenshot.png (a made up CV fragment) and g14_scan.pdf
# (`scan_fixtures.scan(2)`), and a short text. Under the fake backend nothing reads a picture:
# each transcription stream stores the made up text below, and the extraction stream of the same
# document cites words of that text, so its items carry the `transcribed` flag. The text file
# gives the Answers section two items a bulk accept may take.
G14_SHOT = "g14_screenshot.png"
G14_SCAN = "g14_scan.pdf"
G14_NOTES_FIXTURE = "g14_notes.txt"
G14_SHOT_PAGE = (
    "Sam Rivera\n"
    "Timetable analyst for city tram networks\n"
    "\n"
    "Example Tram, Lyon. Since April 2021.\n"
    "Relocation: ready to move to Lyon or Grenoble"
)
G14_SCAN_PAGES = (
    "Curriculum vitae of Sam Rivera, page 1 of 2\nTimetable analyst, Example Tram, Lyon",
    "Page 2 of 2\nTravel: up to four days a month\nLanguages: French and English",
)
G14_PAUSE = 1.5  # seconds before each message of the scan's transcription: the page shows the step


def _paced(body: dict[str, Any], seconds: float) -> dict[str, Any]:
    """The same stream with a wait before every message after the first."""
    first, *rest = body["steps"]
    steps = [first]
    for one in rest:
        steps += [{"sleep": seconds}, one]
    return {**body, "steps": steps}


G14_SHOT_READ = transcription(
    "G14 journey, tests/ingestion/fixtures/g14_screenshot.png: views the image and stores a "
    "made up CV fragment.",
    (view_call(1, 0),),
    (store_call(2, 0, G14_SHOT_PAGE),),
    "Transcribed 1 image.",
)
G14_SCAN_READ = _paced(
    transcription(
        "G14 journey, tests/ingestion/fixtures/g14_scan.pdf: views and stores its two pages, "
        "slowly enough for the page to show the step.",
        (view_call(1, 0),),
        (store_call(2, 0, G14_SCAN_PAGES[0]),),
        (view_call(3, 1),),
        (store_call(4, 1, G14_SCAN_PAGES[1]),),
        "Transcribed 2 images.",
    ),
    G14_PAUSE,
)
G14_SHOT_ITEMS = stream(
    "G14 journey, the text transcribed from g14_screenshot.png: reads it, proposes a headline "
    "and one answer.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "identity",
                    [
                        item(
                            "headline",
                            "Timetable analyst for city tram networks",
                            "Timetable analyst for city tram networks",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        assistant(
            3,
            [
                propose(
                    3,
                    "answers",
                    [
                        item(
                            "relocation",
                            "Ready to move to Lyon or Grenoble",
                            "Relocation: ready to move to Lyon or Grenoble",
                        )
                    ],
                )
            ],
            SMALL,
        ),
        said(4, "Proposed 2 items."),
    ],
    "Proposed 2 items.",
)
G14_SCAN_ITEMS = stream(
    "G14 journey, the text transcribed from g14_scan.pdf: reads it, proposes one answer cited "
    "from its second page.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "answers",
                    [item("travel", "Up to four days a month", "Travel: up to four days a month")],
                )
            ],
            SMALL,
        ),
        said(3, "Proposed 1 item."),
    ],
    "Proposed 1 item.",
)
G14_NOTES = stream(
    "G14 journey, tests/ingestion/fixtures/g14_notes.txt: reads the part, proposes two answers.",
    [
        assistant(1, [read(1)], SMALL),
        assistant(
            2,
            [
                propose(
                    2,
                    "answers",
                    [
                        item("notice_period", "Six weeks", "Notice period: six weeks"),
                        item(
                            "earliest_start",
                            "The first of June",
                            "Earliest start: the first of June",
                        ),
                    ],
                )
            ],
            SMALL,
        ),
        said(3, "Proposed 2 items."),
    ],
    "Proposed 2 items.",
)


STREAMS: dict[str, dict[str, Any]] = {
    "g10_first.txt": FIRST,
    "g10_second.txt": SECOND,
    LARGE_FIXTURE: LARGE,
    "g12_first.txt": G12_FIRST,
    "g12_second.txt": G12_SECOND,
    "g13_notes.txt": G13_NOTES,
    G14_SHOT: G14_SHOT_ITEMS,
    G14_SCAN: G14_SCAN_ITEMS,
    G14_NOTES_FIXTURE: G14_NOTES,
}
# The fixtures that are images: what transcribes each, before its stream of `STREAMS` reads it.
TRANSCRIPTIONS: dict[str, dict[str, Any]] = {G14_SHOT: G14_SHOT_READ, G14_SCAN: G14_SCAN_READ}


def digest_of(fixture: str) -> str:
    """The first 12 hexadecimal digits of the fixture's sha256, as the engine names a stream."""
    return hashlib.sha256((FIXTURES / fixture).read_bytes()).hexdigest()[:12]


def path_of(fixture: str) -> Path:
    return fake_backend.STREAMS / f"ingest_extract_{digest_of(fixture)}_p0.json"


def transcription_path_of(fixture: str) -> Path:
    return fake_backend.STREAMS / f"ingest_transcribe_{digest_of(fixture)}_p0.json"


def written(fixture: str) -> str:
    return json.dumps(STREAMS[fixture], indent=2) + "\n"


def transcription_written(fixture: str) -> str:
    return json.dumps(TRANSCRIPTIONS[fixture], indent=2) + "\n"


def transcribed(fixture: str) -> list[str]:
    """The text a fixture's transcription stream stores, page by page."""
    stored = [
        (block["input"]["position"], block["input"]["text"])
        for step in TRANSCRIPTIONS[fixture]["steps"]
        if step.get("message", {}).get("type") == "assistant"
        for block in step["message"]["message"]["content"]
        if block["type"] == "tool_use" and "text" in block["input"]
    ]
    return [text for _, text in sorted(stored)]


if __name__ == "__main__":
    for name in STREAMS:
        path_of(name).write_text(written(name), encoding="utf-8")
        print(path_of(name).name)
    for name in TRANSCRIPTIONS:
        transcription_path_of(name).write_text(transcription_written(name), encoding="utf-8")
        print(transcription_path_of(name).name)
