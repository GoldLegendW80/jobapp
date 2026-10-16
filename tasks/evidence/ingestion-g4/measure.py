"""The cost of the citation checker on a large document (task G4, issue #45). Not product code:
the figures of tasks/ingestion/todo.md, "Measurements", come from this script.

    cd api && uv run python ../tasks/evidence/ingestion-g4/measure.py \
        > ../tasks/evidence/ingestion-g4/measurements.txt

CPU time of one call, the best of five. The regression tests with their bounds are in
api/tests/ingestion/test_citation_bounds.py.
"""

import platform
import random
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "api"))

from app.ingestion import citations

WORDS = [
    "Lumetra",
    "Velmor",
    "développeur",
    "ingénieur",
    "full-stack",
    "Python",
    "Docker",
    "équipe",
    "projet",
    "depuis",
    "janvier",
    "2023",
    "mission",
    "chez",
    "Bordeaux",
    "l\u2019équipe",
    "GenAI",
    "à",
    "de",
    "la",
    "et",
    "en",
    "production",
    "10\u202f000",
    "3,5",
    "ans",
    "camille.durand@example.org",
    "06",
    "39",
    "98",
    "12",
    "34",
    "https://www.example.org/in/camille-durand",
    "01/2023",
]


def cv(size: int) -> str:
    """A text of `size` characters that reads like a long French dossier."""
    rng = random.Random(7)
    out = []
    length = 0
    while length < size:
        word = rng.choice(WORDS)
        out.append(word)
        out.append("\n" if rng.random() < 0.05 else " ")
        length += len(word) + 1
    return "".join(out)[:size]


def cpu(action: Callable[[], object], repeat: int = 5) -> float:
    best = float("inf")
    for _ in range(repeat):
        started = time.process_time()
        action()
        best = min(best, time.process_time() - started)
    return best * 1000


def main() -> None:
    print(
        f"# {platform.machine()}, {platform.system()}, Python {platform.python_version()}"
    )
    print("# milliseconds of CPU for one call, the best of five")
    print()
    print(
        "characters | prepare | find, present | find, absent | check_item, 3 excerpts"
    )
    times = {}
    for size in (125_000, 250_000, 500_000, 1_000_000):
        text = cv(size)
        document = citations.prepare(text)
        start = text.index(" ", size // 2) + 1
        excerpts = [
            text[at : at + 250] for at in (start, text.index(" ", size // 3) + 1, 0)
        ]
        value = {
            "organisation": "Lumetra",
            "title": "Développeur full-stack, 10 000 utilisateurs",
            "start": "2023-01",
        }
        types = {"organisation": "name", "title": "text", "start": "date"}
        times[size] = cpu(lambda text=text: citations.prepare(text))
        hit = cpu(lambda d=document, e=excerpts[0]: citations.find(d, e))
        miss = cpu(
            lambda d=document: citations.find(d, "zèbre girafe quokka okapi wombat")
        )
        check = cpu(
            lambda d=document, v=value, t=types, e=excerpts: citations.check_item(
                t, v, d, e
            )
        )
        print(
            f"{size:>10,} | {times[size]:7.1f} | {hit:13.3f} | {miss:12.3f} | {check:9.3f}"
        )
    print()
    ratio = times[500_000] / times[125_000]
    print(
        f"prepare, 500,000 against 125,000 characters: {ratio:.1f} times (4 when linear)"
    )
    print()
    print(
        "crafted documents of 500,000 characters | prepare | find (worst of 3 excerpts)"
    )
    units = {
        "one letter": "a",
        "two letters": "ab",
        "letters and spaces": "a b ",
        "numbers": "1 2 3 4 ",
        "phone numbers": "06 39 98 12 34 ",
        "combining accents": "e\u0301",
        "marks without end": "\u0301\u0316",
        "superscripts": "\u00b2",
        "ligature of eighteen": "ﷺ",
        "chinese": "阿里巴巴集团担任高级软件工程师",
        "controls": "\x00\x01a",
    }
    for label, unit in units.items():
        text = unit * (500_000 // len(unit))
        prepared = cpu(lambda text=text: citations.prepare(text), repeat=3)
        document = citations.prepare(text)
        middle = len(text) // 2
        worst = max(
            cpu(lambda d=document, e=excerpt: citations.find(d, e), repeat=3)
            for excerpt in (
                text[middle : middle + 300],
                text[middle + 1 : middle + 44],
                unit * 20,
            )
        )
        print(f"{label:>38} | {prepared:7.1f} | {worst:8.3f}")
    print()
    text = cv(500_000)
    tracemalloc.start()
    document = citations.prepare(text)
    kept, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"memory of prepare(500,000 characters): peak {peak / 1e6:.1f} MB, "
        f"kept {kept / 1e6:.1f} MB ({kept / len(text):.1f} bytes per character)"
    )
    print(f"N holds {len(document.text):,} characters, S {len(document.skeleton):,}")


if __name__ == "__main__":
    main()
