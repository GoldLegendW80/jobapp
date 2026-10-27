"""Case spellings of a word, for the tables of browserd's shared detector (browser B5, B17): a page
or a server may write `Submit` as `SubMit`, `sUbmit` or `ApPly`, and a case insensitive server
serves `/AppLy` as `/apply`, so whatever the detector refuses in lower case it must refuse in
every one of them (detect.match_words)."""

import random


def case_spellings(text: str, sample: int = 20) -> list[str]:
    """Spellings of `text` that differ from it only in case: lower, upper, title and swapped case,
    the two alternating cases, each letter flipped on its own, and `sample` seeded random ones."""
    letters = [i for i, ch in enumerate(text) if ch.lower() != ch.upper()]

    def cased(upper: set[int]) -> str:
        return "".join(
            (ch.upper() if i in upper else ch.lower()) if i in letters else ch
            for i, ch in enumerate(text)
        )

    spellings = {text.lower(), text.upper(), text.title(), text.swapcase()}
    spellings |= {cased(set(letters[parity::2])) for parity in (0, 1)}
    spellings |= {text[:i] + text[i].swapcase() + text[i + 1 :] for i in letters}
    rng = random.Random(text)  # noqa: S311  seeded test spellings, not a secret
    spellings |= {cased({i for i in letters if rng.random() < 0.5}) for _ in range(sample)}
    return sorted(spellings)
