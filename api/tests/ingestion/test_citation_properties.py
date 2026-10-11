"""Generated cases for the citation checker. Each family builds documents from a seed and knows,
from the way it built them, what they say: the oracle is the generator, never the checker.

1. Excerpts: a passage cut from a text on token boundaries is found at its own offsets, and a
   passage that is not in the text never is.
2. Numbers: a number is supported only by a number of the same value, whatever the thousands
   and decimal conventions; a number cut out of a longer one, or glued from two, is refused.
3. Dates: in every supported form, the month the document gives is accepted with its year, and
   each of the eleven other months, and every other year, is refused.
4. Addresses: an email or a URL is accepted as written between any two bounds, and every prefix,
   suffix and near miss of it is refused.
5. Phone numbers: all the digits of the number, in any writing, and no part of them.
6. Names: whole words only.
7. A cut excerpt hides nothing: what is refused with a whole line cited is refused with every
   part of that line cited.
8. Hostile text: nothing raises, the answer is the same twice, and what is accepted points at
   the document's own words.

No test library generates these: `random.Random(seed)` does, so a failure names its seed and is
replayed by running that test again. The identity is fictitious (example.org, .example, and the
French phone numbers kept for fiction).
"""

import random
from decimal import Decimal

import pytest

from app.ingestion import citations
from app.ingestion.citations import Accepted, Refusal, Rule, Span, check_item, find, prepare

SEEDS = range(25)
seeded = pytest.mark.parametrize("seed", SEEDS)
# The two families that cite every part of a line make hundreds of checks per seed.
few_seeds = pytest.mark.parametrize("seed", range(12))

WORDS = [
    "développeur",
    "ingénieur",
    "équipe",
    "projet",
    "chez",
    "depuis",
    "mission",
    "produit",
    "données",
    "plateforme",
    "Bordeaux",
    "Betclic",
    "Thales",
    "Elsan",
    "client",
    "service",
    "pilotage",
    "conception",
    "livraison",
    "production",
    "engineer",
    "software",
    "team",
    "project",
    "since",
    "product",
    "delivery",
    "design",
    "senior",
    "lead",
    "back-end",
    "l'équipe",
    "d'un",
    "full-stack",
    "très",
    "après",
    "été",
    "année",
]
OTHER_WORDS = [
    "zèbre",
    "girafe",
    "quokka",
    "okapi",
    "wombat",
    "narval",
    "axolotl",
    "tapir",
    "lémurien",
    "yack",
]


def generator(seed: int) -> random.Random:
    """The generator of one test: the same cases for the same seed, on every machine."""
    return random.Random(seed)  # noqa: S311 (test data, not a secret)


def sentence(rng: random.Random, low: int = 4, high: int = 9) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(low, high)))


def plain_words(rng: random.Random, low: int, high: int) -> str:
    """Words without an apostrophe: the d of "d'un" would be a one letter address of its own."""
    words = [word for word in WORDS if "'" not in word]
    return " ".join(rng.choice(words) for _ in range(rng.randint(low, high)))


def refused(result: Accepted | Refusal, rule: Rule) -> bool:
    return isinstance(result, Refusal) and result.rule is rule


# --- 1. excerpts ---------------------------------------------------------------------------------

JOINERS = (" ", " ", " ", ", ", " : ", ". ", "\n", "\n\n", " (", ") ", " \u2013 ", "\u00a0", "   ")
NUMBERS = ("2019", "2023", "10 000", "3,5", "1 234,56", "42", "06 39 98 12 34", "01/2023")


def document(rng: random.Random, pieces: int = 60) -> str:
    out = []
    for _ in range(pieces):
        word = rng.choice(NUMBERS) if rng.random() < 0.15 else rng.choice(WORDS)
        if rng.random() < 0.15:
            word = word.capitalize()
        out.append(word)
        out.append(rng.choice(JOINERS))
    return "".join(out).strip()


def boundaries(norm: str) -> list[int]:
    """The positions of a normalised text where a span may start or end: token boundaries that
    do not open or close on whitespace or on a hyphen."""
    return [
        at
        for at in range(len(norm) + 1)
        if citations.is_token_boundary(norm, at)
        and (at == len(norm) or norm[at] not in " \n-")
        and (at == 0 or norm[at - 1] not in " \n-")
    ]


@seeded
def test_an_excerpt_cut_on_token_boundaries_is_found_at_its_own_offsets(seed: int) -> None:
    rng = generator(seed)
    text = document(rng)
    prepared = prepare(text)
    norm = prepared.text
    cuts = boundaries(norm)
    checked = 0
    for _ in range(60):
        start, end = sorted(rng.sample(cuts, 2))
        size = end - start
        bones = citations.skeleton(norm[start:end]).text
        if not (citations.EXCERPT_MIN <= size <= citations.EXCERPT_MAX) or len(bones) < 12:
            continue
        span = Span(prepared, start, end)
        excerpt = text[span.source_start : span.source_end]
        found = find(prepared, excerpt)
        assert isinstance(found, Span), (seed, excerpt, found)
        # the document's own words, and the same whether cited whole or re-read
        assert found.text == norm[start:end]
        assert citations.normalise(found.source()).text == found.text
        if prepared.skeleton.count(bones) == 1:
            assert (found.start, found.end) == (start, end)
            assert (found.source_start, found.source_end) == (span.source_start, span.source_end)
        else:
            assert found.start <= start
        checked += 1
    assert checked >= 10


@seeded
def test_an_excerpt_typed_another_way_is_found_at_the_same_place(seed: int) -> None:
    """Case, line breaks in place of spaces, hyphenation, curly apostrophes, soft hyphens and
    non breaking spaces in the excerpt do not move the span."""
    rng = generator(seed)
    text = document(rng)
    prepared = prepare(text)
    norm = prepared.text
    cuts = boundaries(norm)
    checked = 0
    for _ in range(40):
        start, end = sorted(rng.sample(cuts, 2))
        bones = citations.skeleton(norm[start:end]).text
        if not (25 <= end - start <= 300) or len(bones) < 12 or prepared.skeleton.count(bones) > 1:
            continue
        typed = []
        for char in norm[start:end]:
            roll = rng.random()
            if char == " " and roll < 0.3:
                typed.append(rng.choice(("\n", "\u00a0", "  ", " \u200b")))
            elif char == "\n" and roll < 0.5:
                typed.append(" ")
            elif char == "'" and roll < 0.5:
                typed.append("\u2019")
            elif char.isalpha() and roll < 0.1:
                typed.append(char.upper())
            elif char.isalpha() and roll < 0.13:
                typed.append(char + "\u00ad")
            elif char.isalpha() and roll < 0.15:
                typed.append(char + "-\n")
            else:
                typed.append(char)
        excerpt = "".join(typed).strip()
        if not (20 <= len(citations.normalise(excerpt).text) <= 600):
            continue
        found = find(prepared, excerpt)
        assert isinstance(found, Span), (seed, excerpt, found)
        assert (found.start, found.end) == (start, end), (seed, excerpt)
        checked += 1
    assert checked >= 5


@seeded
def test_a_passage_that_is_not_in_the_text_is_never_found(seed: int) -> None:
    rng = generator(seed)
    text = document(rng)
    prepared = prepare(text)
    for _ in range(20):
        # words of another vocabulary
        absent = " ".join(rng.choice(OTHER_WORDS) for _ in range(rng.randint(4, 8)))
        assert refused(find(prepared, absent), Rule.EXCERPT_NOT_FOUND), (seed, absent)
    norm = prepared.text
    cuts = boundaries(norm)
    checked = 0
    for _ in range(40):
        start, end = sorted(rng.sample(cuts, 2))
        if not (30 <= end - start <= 300):
            continue
        # one character of a real passage replaced by a letter the text never uses
        excerpt = norm[start:end]
        at = rng.randrange(len(excerpt))
        if excerpt[at] in " \n-":
            continue
        mutated = excerpt[:at] + "ø" + excerpt[at + 1 :]
        assert refused(find(prepared, mutated), Rule.EXCERPT_NOT_FOUND), (seed, mutated)
        # and two real passages glued over a gap
        gap = norm.find(" ", start + 10, end - 10)
        if gap > 0:
            glued = excerpt[: gap - start] + " ø " + excerpt[gap - start + 1 :]
            assert refused(find(prepared, glued), Rule.EXCERPT_NOT_FOUND), (seed, glued)
        checked += 1
    assert checked >= 5


@seeded
def test_a_span_never_cuts_a_token_wherever_the_excerpt_is_cut(seed: int) -> None:
    """Any slice of the text, cut anywhere: when it is found, the span starts and ends on token
    boundaries, holds the slice, and is the text of the document."""
    rng = generator(seed)
    text = document(rng, pieces=40)
    prepared = prepare(text)
    norm = prepared.text
    found_some = 0
    for _ in range(80):
        start = rng.randrange(len(text) - 25)
        end = start + rng.randint(20, 120)
        excerpt = text[start:end]
        found = find(prepared, excerpt)
        if isinstance(found, Refusal):
            assert found.rule in (
                Rule.EXCERPT_TOO_SHORT,
                Rule.EXCERPT_TOO_THIN,
            ), (seed, excerpt, found)
            continue
        found_some += 1
        assert citations.is_token_boundary(norm, found.start)
        assert citations.is_token_boundary(norm, found.end)
        wanted = citations.skeleton(citations.normalise(excerpt).text).text
        assert wanted in citations.skeleton(found.text).text
        assert text[found.source_start : found.source_end] == found.source()
        assert citations.normalise(found.source()).text == found.text
        assert norm[found.start] not in " \n"
        assert norm[found.end - 1] not in " \n"
    assert found_some >= 40


# --- 2. numbers ----------------------------------------------------------------------------------

GROUP_SEPARATORS = (" ", "\u00a0", "\u202f", ",", ".", "'")


def written(rng: random.Random, whole: int, fraction: str) -> str:
    """One way a real document writes the number `whole.fraction`, by the conventions of the
    plan: groups of three with one separator, a decimal part after a full stop or a comma."""
    digits = str(whole)
    grouped = whole >= 1000 and rng.random() < 0.7
    if grouped:
        separator = rng.choice(GROUP_SEPARATORS)
        groups = []
        while digits:
            groups.insert(0, digits[-3:])
            digits = digits[:-3]
        digits = separator.join(groups)
    else:
        separator = ""
    if not fraction:
        return digits
    mark = rng.choice([m for m in ".," if m != separator])
    return f"{digits}{mark}{fraction}"


def some_number(rng: random.Random) -> tuple[int, str]:
    whole = rng.choice(
        (
            rng.randint(0, 9),
            rng.randint(10, 999),
            rng.randint(1000, 9999),
            rng.randint(10_000, 999_999),
            rng.randint(1_000_000, 99_999_999),
        )
    )
    if rng.random() < 0.5:
        return whole, ""
    # one to five decimals after any whole part: "3,5", "0,125", "12.3456", "1234.567"
    while True:
        size = rng.randint(1, 5)
        fraction = "".join(rng.choice("0123456789") for _ in range(size - 1))
        fraction += rng.choice("123456789")
        # but what the conventions read as two numbers: a month and its year, two years
        if len(meanings(plain(whole, fraction))) == 1:
            return whole, fraction


def plain(whole: int, fraction: str) -> str:
    return f"{whole}.{fraction}" if fraction else str(whole)


def near_misses(whole: int, fraction: str) -> set[str]:
    """Numbers that a careless or a dishonest reading makes out of `whole.fraction`."""
    digits = str(whole)
    out = {
        digits[1:],  # without its first digit
        digits[:-1],  # without its last digit
        digits + "0",
        digits + "00",
        "1" + digits,
        str(whole + 1),
        str(whole * 2 + 1),
        digits[-3:],  # the last group alone
        digits[:-3],  # the number of thousands
        digits[:1],
        digits[::-1],
    }
    if fraction:
        out |= {
            digits,  # the integer part alone
            fraction,  # the decimals alone
            digits + fraction,  # the separator forgotten
            f"{digits}.{fraction}1",
            f"{digits}.{fraction[:-1]}" if len(fraction) > 1 else f"{digits}.0{fraction}",
            f"{fraction}.{digits}",
        }
    else:
        out |= {f"{digits}.5", f"0.{digits}", f"{digits[:1]}.{digits[1:]}" if digits[1:] else "7.7"}
    return {miss for miss in out if miss and miss.strip(".") == miss}


def meanings(text: str) -> frozenset[Decimal]:
    """The numbers that a number written with at most one full stop means, by the conventions
    of the plan: three digits after one to three that do not start with 0 are a group of
    thousands; a year from 1900 to 2100 after a month in two digits or after another such year
    is a second number; anything else is one decimal, whatever the number of its decimals.
    Where it means two numbers, both must be in the source."""
    whole, _, fraction = text.partition(".")
    if not fraction:
        return frozenset({Decimal(whole)})
    if len(fraction) == 3 and len(whole) <= 3 and not whole.startswith("0"):
        return frozenset({Decimal(whole + fraction)})

    def year(digits: str) -> bool:
        return len(digits) == 4 and 1900 <= int(digits) <= 2100

    month = len(whole) == 2 and 1 <= int(whole) <= 12
    if year(fraction) and (year(whole) or month):
        return frozenset({Decimal(whole), Decimal(fraction)})
    return frozenset({Decimal(text)})


@seeded
def test_a_number_is_supported_only_by_a_number_of_the_same_value(seed: int) -> None:
    rng = generator(1000 + seed)
    for _ in range(12):
        numbers = [some_number(rng) for _ in range(rng.randint(1, 3))]
        present = frozenset().union(*(meanings(plain(*number)) for number in numbers))
        parts = [sentence(rng, 3, 5)]
        for number in numbers:
            parts.append(written(rng, *number))
            parts.append(sentence(rng, 2, 4))
        source = " ".join(parts)
        for whole, fraction in numbers:
            # the same value in the other conventions
            for honest in (plain(whole, fraction), written(rng, whole, fraction)):
                result = check_item("text", f"valeur {honest} mesurée", source, [source])
                assert isinstance(result, Accepted), (seed, source, honest, result)
            for miss in near_misses(whole, fraction):
                if meanings(miss) <= present:
                    continue
                result = check_item("text", f"valeur {miss} mesurée", source, [source])
                assert refused(result, Rule.NUMBER_NOT_IN_SOURCE), (seed, source, miss, result)


@seeded
def test_every_number_of_a_run_of_the_value_is_supported(seed: int) -> None:
    """A value may write several numbers in one run ("2019 2021", "14.2.3", a phone number in
    a text). Each of them must be a number of the source: one that is does not carry the
    others (the first review of pull request #97 found that one did)."""
    rng = generator(19_000 + seed)
    for _ in range(12):
        # two digit numbers over 12: joined, they read as pieces, never as a group or a month
        present = rng.sample(range(13, 100), 4)
        absent = rng.choice([number for number in range(13, 100) if number not in present])
        source = " ".join(f"{sentence(rng, 1, 3)} {number}" for number in present)
        source = f"{source} {sentence(rng, 2, 4)}"
        size = rng.randint(2, 4)
        # a full stop or a comma between two pieces would be a decimal: three pieces or more
        joiner = rng.choice((" ", "'") if size == 2 else (" ", "'", ".", ","))
        pieces = [str(number) for number in rng.sample(present, size)]
        honest = joiner.join(pieces)
        result = check_item("text", f"valeurs {honest} mesurées", source, [source])
        assert isinstance(result, Accepted), (seed, source, honest, result)
        for place in range(size + 1):
            invented = joiner.join([*pieces[:place], str(absent), *pieces[place:]])
            result = check_item("text", f"valeurs {invented} mesurées", source, [source])
            assert refused(result, Rule.NUMBER_NOT_IN_SOURCE), (seed, source, invented, result)
        for place in range(size):
            replaced = joiner.join([*pieces[:place], str(absent), *pieces[place + 1 :]])
            result = check_item("text", f"valeurs {replaced} mesurées", source, [source])
            assert refused(result, Rule.NUMBER_NOT_IN_SOURCE), (seed, source, replaced, result)


@seeded
def test_two_numbers_are_never_glued_and_one_is_never_split(seed: int) -> None:
    rng = generator(2000 + seed)
    for _ in range(15):
        first, second = rng.randint(1, 999), rng.randint(100, 999)
        between = rng.choice(
            ("\n", "   ", " et ", " puis ", " / ", "-", "\u2013", "\t", "\x0b", " x ")
        )
        source = f"{sentence(rng, 3, 4)} {first}{between}{second} {sentence(rng, 3, 4)}"
        for honest in (first, second):
            result = check_item("text", f"valeur {honest}", source, [source])
            assert isinstance(result, Accepted), (seed, source, honest, result)
        glued = f"{first}{second}"
        result = check_item("text", f"valeur {glued}", source, [source])
        assert refused(result, Rule.NUMBER_NOT_IN_SOURCE), (seed, source, glued, result)
        # one number in groups of three is not its groups
        whole = rng.randint(1_000, 999_999)
        grouped = f"{whole:,}".replace(",", rng.choice(GROUP_SEPARATORS))
        source = f"{sentence(rng, 3, 4)} {grouped} {sentence(rng, 3, 4)}"
        for group in {str(whole // 1000), str(whole % 1000), f"{whole % 1000:03d}"}:
            if int(group) == whole:
                continue
            result = check_item("text", f"valeur {group}", source, [source])
            assert refused(result, Rule.NUMBER_NOT_IN_SOURCE), (seed, source, group, result)


# --- 3. dates ------------------------------------------------------------------------------------

EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
EN_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FR_SHORT = [
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juill.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
]
ORDINAL = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}


def month_forms(year: int, month: int, day: int) -> list[str]:
    """Every supported way to write that month next to that year."""
    en, fr = EN[month - 1], FR[month - 1]
    suffix = ORDINAL.get(day, "th")
    forms = [
        f"{en} {year}",
        f"{en.upper()} {year}",
        f"{fr} {year}",
        f"{fr.capitalize()} {year}",
        f"{EN_SHORT[month - 1]} {year}",
        # May is a full name, and a full name takes no full stop
        f"{EN_SHORT[month - 1]}{'' if month == 5 else '.'} {year}",
        f"{FR_SHORT[month - 1]} {year}",
        f"{en}, {year}",
        f"{en} of {year}",
        f"{day} {fr} {year}",
        f"le {day} {fr} {year}",
        f"1er {fr} {year}",
        f"{en} {day}, {year}",
        f"{en} {day} {year}",
        f"{en} {day}{suffix}, {year}",
        f"{en} the {day}{suffix}, {year}",
        f"the {day}{suffix} of {en} {year}",
        f"{day}{suffix} {en} {year}",
        f"{month:02d}/{year}",
        f"{month:02d}.{year}",
        f"{month:02d}-{year}",
        f"{month:02d} / {year}",
        f"{EN_SHORT[month - 1]}-{year}",
        f"{fr}-{year}",
        f"du 02/{month:02d} au 28/{month:02d}/{year}",
        f"{year}-{month:02d}-{day:02d}",
        f"{year}/{month:02d}/{day:02d}",
        f"{day:02d}.{month:02d}.{year}",
        f"{month:02d}/{year} to present",
        f"{month}/{year} to present",
        f"{month}/{year} à aujourd'hui",
        f"{year}-{month:02d} to present",
        f"{year}-{month:02d} à ce jour",
    ]
    if month <= year % 100:
        forms += [f"{year}-{month:02d}", f"{year}/{month:02d}"]
    if day > 12:
        forms += [
            f"{day}/{month:02d}/{year}",
            f"{day}-{month:02d}-{year}",
            f"{month:02d}/{day}/{year}",
            f"{month:02d}-{day}-{year}",
            f"{day}/{month}/{year}",
            f"{day} / {month:02d} / {year}",
        ]
    return forms


def year_only_forms(year: int, month: int, day: int) -> list[str]:
    """Ways to write that year that give no month, or not that one for certain."""
    en, fr = EN[month - 1], FR[month - 1]
    other = 1 + (month % 12)
    low = min(month, other) if min(month, other) <= 12 else 1
    forms = [
        f"{year}",
        f"en {year}",
        f"{year} {en}",
        f"{year}, {fr}",
        f"{fr}\n{year}",
        f"{en}   {year}",
        f"{fr}. {year}",
        f"{fr} ({year})",
        f"{fr} : {year}",
        f"x{fr} {year}",
        f"{fr}x {year}",
        # a month in one digit, outside a range
        f"{month % 9 + 1}/{year}",
        f"{low:02d}/{max(month, other):02d}/{year}" if low != max(month, other) else f"{year}",
        f"{month:02d}/{other:02d}/{year}",
        f"{other:02d}-{month:02d}-{year}",
        f"{year}.{month:02d}",
        f"{year} - {month:02d}",
        f"{month:02d} /{year}",
        f"{month:02d}/ {year}",
        f"{en} - {year}",
        f"{en}- {year}",
        f"T{month:02d}/{year}",
        f"{en} {day}, , {year}",
        f"{en} and {year}",
    ]
    if month > year % 100:
        forms += [f"{year}-{month:02d}", f"{year}/{month:02d}"]
    return forms


def some_date(rng: random.Random) -> tuple[int, int, int]:
    return rng.randint(1990, 2026), rng.randint(1, 12), rng.randint(1, 28)


def wrapped(rng: random.Random, form: str) -> str:
    """The form inside a sentence without any other digit or month word."""
    before = rng.choice(("Poste occupé", "Mission chez Betclic", "Engineer at Thales", "Stage"))
    link = rng.choice((", ", " : ", " ", " depuis ", " (", "\n"))
    after = rng.choice(
        (", à Bordeaux en équipe", " chez Elsan à Paris", "\nDéveloppeur web senior")
    )
    return f"{before}{link}{form}{after}"


@seeded
def test_a_date_gives_its_month_and_no_other(seed: int) -> None:
    rng = generator(3000 + seed)
    year, month, day = some_date(rng)
    for form in month_forms(year, month, day):
        source = wrapped(rng, form)
        cited = prepare(source)
        assert isinstance(check_item("date", f"{year}-{month:02d}", cited, [source]), Accepted), (
            seed,
            source,
        )
        assert isinstance(check_item("date", f"{year}", cited, [source]), Accepted), (
            seed,
            source,
        )
        for other in range(1, 13):
            if other == month:
                continue
            result = check_item("date", f"{year}-{other:02d}", cited, [source])
            assert refused(result, Rule.MONTH_NOT_IN_SOURCE), (seed, source, other, result)
        for wrong in (year - 1, year + 1, year + 10, year - 100):
            for value in (f"{wrong}", f"{wrong}-{month:02d}"):
                result = check_item("date", value, cited, [source])
                assert isinstance(result, Refusal), (seed, source, value)
                assert result.rule in (Rule.YEAR_NOT_IN_SOURCE, Rule.TWO_DIGIT_YEAR)


@seeded
def test_a_form_that_gives_no_month_supports_the_year_alone(seed: int) -> None:
    rng = generator(4000 + seed)
    for _ in range(2):
        year, month, day = some_date(rng)
        for form in year_only_forms(year, month, day):
            source = wrapped(rng, form)
            cited = prepare(source)
            assert isinstance(check_item("date", f"{year}", cited, [source]), Accepted), (
                seed,
                source,
            )
            for claimed in range(1, 13):
                result = check_item("date", f"{year}-{claimed:02d}", cited, [source])
                assert isinstance(result, Refusal), (seed, source, claimed)
                assert result.rule in (Rule.MONTH_NOT_IN_SOURCE, Rule.MONTH_APART_FROM_YEAR)


@seeded
def test_a_year_is_never_cut_out_of_a_phone_number_or_a_decimal(seed: int) -> None:
    """Four digits that end a phone number, a postcode or a decimal are not a year, however
    the number is written (the second review of pull request #97 found the hyphens and the
    slashes), and the honest forms that look like them keep their year."""
    rng = generator(4500 + seed)
    for _ in range(8):
        year = rng.randint(1990, 2026)
        area, exchange = rng.randint(200, 989), rng.randint(200, 989)
        other = rng.randint(1900, 2100)
        cut_out = (
            f"{area}-{exchange}-{year}",
            f"({area}) {exchange}-{year}",
            f"0{area}/{exchange}/{year}",
            f"{area}.{exchange}.{year}",
            f"+1 {area} {exchange} {year}",
            f"{rng.randint(10_000, 99_999)}-{year}",
            f"{year}-{rng.randint(10_000, 99_999)}",
            f"{year},{rng.randint(10, 99)}",
            f"{year}.{rng.randint(13, 99)}",
            f"0.{year}",
            f"{rng.randint(1, 9)}.{year}",
            f"{rng.randint(13, 99)}.{year}",
            f"{exchange} {year} {area}",
        )
        for form in cut_out:
            source = f"Référence {form}, dossier suivi à Bordeaux"
            result = check_item("date", f"{year}", source, [source])
            assert refused(result, Rule.YEAR_NOT_IN_SOURCE), (seed, source, result)
        month, day = rng.randint(1, 12), rng.randint(13, 28)
        whole = (
            f"{year}-{other}",
            f"{year}/{other}",
            f"{month:02d}-{year}",
            f"{day}-{month:02d}-{year}",
            f"{year}-{month:02d}-{day}",
            f"{month:02d}.{year}",
            f"{day}.{month:02d}.{year}",
            f"{year},{other}",
            f"{year},{other},{exchange}",
            f"{year} {other}",
            f"{year} {other} {rng.randint(1900, 2100)}",
            f"{day} {month:02d} {year}",
        )
        for form in whole:
            source = f"Référence {form}, dossier suivi à Bordeaux"
            result = check_item("date", f"{year}", source, [source])
            assert isinstance(result, Accepted), (seed, source, result)


CONNECTORS = (" à ", " au ", " to ", " until ", " till ", " through ", " - ", "-", " \u2013 ")
LISTS = (" et ", " and ", ", ", "/")


@seeded
def test_a_range_shares_its_year_with_the_months_before_it_in_the_calendar(seed: int) -> None:
    rng = generator(5000 + seed)
    for _ in range(12):
        year = rng.randint(1990, 2026)
        first, last = sorted(rng.sample(range(1, 13), 2))
        names = rng.choice((EN, FR, EN_SHORT))
        connector = rng.choice(CONNECTORS + LISTS)
        day = rng.choice(("", "", "1er ", "15 "))
        forward = f"{day}{names[first - 1]}{connector}{names[last - 1]} {year}"
        source = wrapped(rng, forward)
        for month in range(1, 13):
            result = check_item("date", f"{year}-{month:02d}", source, [source])
            if month in (first, last):
                assert isinstance(result, Accepted), (seed, source, month, result)
            else:
                assert refused(result, Rule.MONTH_NOT_IN_SOURCE), (seed, source, month, result)
        # the other way round the first month belongs to the year before: it gets none
        backward = f"{names[last - 1]}{connector}{names[first - 1]} {year}"
        source = wrapped(rng, backward)
        assert isinstance(check_item("date", f"{year}-{first:02d}", source, [source]), Accepted)
        result = check_item("date", f"{year}-{last:02d}", source, [source])
        assert refused(result, Rule.MONTH_NOT_IN_SOURCE), (seed, source, result)
        for wrong in (year - 1, year + 1):
            result = check_item("date", f"{wrong}-{last:02d}", source, [source])
            assert refused(result, Rule.YEAR_NOT_IN_SOURCE), (seed, source, result)


@seeded
def test_a_two_digit_year_is_never_read_on_its_own(seed: int) -> None:
    rng = generator(6000 + seed)
    for _ in range(12):
        start_year = rng.randint(2001, 2020)
        end_year = start_year + rng.randint(1, 5)
        start_month, end_month = rng.randint(1, 12), rng.randint(1, 12)
        connector = rng.choice((" à ", " to ", " - ", "-", " until "))
        short = (
            f"Mission de {start_month:02d}/{start_year % 100:02d}{connector}"
            f"{end_month:02d}/{end_year % 100:02d} chez Elsan"
        )
        full = f"Elsan, de {start_year} à {end_year}, développeur."
        source = f"{full}\n\n{short}, Bordeaux"
        for year, month in ((start_year, start_month), (end_year, end_month)):
            alone = check_item("date", f"{year}-{month:02d}", source, [short])
            assert refused(alone, Rule.TWO_DIGIT_YEAR), (seed, source, alone)
            assert refused(check_item("date", f"{year}", source, [short]), Rule.TWO_DIGIT_YEAR)
            both = check_item("date", f"{year}-{month:02d}", source, [full, short])
            if start_year % 100 > 12:
                assert isinstance(both, Accepted), (seed, source, both)
            else:  # up to 12 the two numbers may be a day and its month: never read
                assert refused(both, Rule.TWO_DIGIT_YEAR), (seed, source, both)
            for other in range(1, 13):
                if other in (start_month, end_month):
                    continue
                wrong = check_item("date", f"{year}-{other:02d}", source, [full, short])
                assert refused(wrong, Rule.MONTH_NOT_IN_SOURCE), (seed, source, other, wrong)
        apostrophe = f"Betclic, {EN_SHORT[start_month - 1]} '{start_year % 100:02d} to present"
        result = check_item("date", f"{start_year}-{start_month:02d}", apostrophe, [apostrophe])
        assert refused(result, Rule.TWO_DIGIT_YEAR), (seed, apostrophe, result)


# --- 4. addresses --------------------------------------------------------------------------------
# Generated from the grammars of an address, never from the lists of the module: the first
# review of pull request #97 found that a generator which draws from the module's own characters
# cannot see the ones it forgot ("o'brien", "r&d", a URL in the query of another).

LETTERS = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"
# RFC 5322, atext: what a local part holds besides letters and digits, and full stops between.
ATEXT_SIGNS = "!#$%&'*+/=?^_`{|}~-"
# RFC 3986: unreserved marks, the percent of an escape, and the delimiters that join the parts
# of a URL (path, query, fragment, user).
URL_JOINS = "-._~%/?#=&@+"
# RFC 3986 again: the delimiters that a text also writes around an address.
URL_AROUND = "(),;:!'*[]$"
HOSTS = ("example.org", "mail.example", "my-site.example", "sub.example.org", "a-b.c.example")
# What no address holds: a text writes these around one.
OPENINGS = ("", " ", "(", "<", "[", '"', ": ", ", ", "; ", "\n", "mailto:", "\u00ab ", ",", ";")
CLOSINGS = ("", " ", ")", ">", "]", '"', ", ", "; ", ". ", ".", "\n", " \u00bb", ",", ";", "? ")
PADDING = "Pour toute question sur ce dossier"


def some_email(rng: random.Random) -> str:
    """Atoms of letters and digits joined by full stops, some with a sign of the grammar inside,
    an @ and a domain of labels."""
    atoms = []
    for _ in range(rng.randint(1, 3)):
        size = rng.randint(1, 6)
        atom = [rng.choice(LETTERS + DIGITS) for _ in range(size)]
        if size >= 3 and rng.random() < 0.6:
            atom[rng.randrange(1, size - 1)] = rng.choice(ATEXT_SIGNS)
        atoms.append("".join(atom))
    return f"{'.'.join(atoms)}@{rng.choice(HOSTS)}"


def some_url(rng: random.Random) -> str:
    """A host, sometimes a port, a path of segments that hold the marks of the grammar, sometimes
    a query and a fragment. No scheme: the caller writes one or not."""
    segments = ("in", "camille-durand", "projets", "Brain", "cv_2023", "a.b", "~cd", "a%20b", "x+y")
    port = rng.choice(("", "", "", ":8080"))
    path = "".join("/" + rng.choice(segments) for _ in range(rng.randint(0, 3)))
    query = rng.choice(("", "", "?id=42", "?id=42&lang=fr", "?q=Brain"))
    fragment = rng.choice(("", "", "#top"))
    return f"{rng.choice(HOSTS)}{port}{path}{query if path else ''}{fragment if path else ''}"


def framed(rng: random.Random, address: str) -> str:
    """The address between two bounds that are not part of an address, in a longer line."""
    opening, closing = rng.choice(OPENINGS), rng.choice(CLOSINGS)
    return f"{PADDING} {opening}{address}{closing} {plain_words(rng, 3, 5)}"


def parts_of(address: str) -> set[str]:
    """Every strict prefix and suffix, and the near misses that change one character."""
    out = {address[:cut] for cut in range(1, len(address))}
    out |= {address[cut:] for cut in range(1, len(address))}
    for at, char in enumerate(address):
        if not char.isalnum():
            out.add(address[:at] + address[at + 1 :])  # a sign forgotten
            out.add(address[:at] + " " + address[at + 1 :])
        if char.isalpha():
            out.add(address[:at] + ("z" if char != "z" else "y") + address[at + 1 :])
    out.add("x" + address)
    out.add(address + "x")
    out.add(address + ".fr")
    out.discard(address)
    return out


@seeded
def test_an_email_is_accepted_as_written_and_no_part_of_it(seed: int) -> None:
    rng = generator(7000 + seed)
    for _ in range(6):
        email = some_email(rng)
        source = framed(rng, email)
        for honest in (email, email.upper(), f" {email} "):
            result = check_item("email", honest, source, [source])
            assert isinstance(result, Accepted), (seed, source, honest, result)
        for part in parts_of(email):
            result = check_item("email", part, source, [source])
            assert isinstance(result, Refusal), (seed, source, part)
            assert result.rule in (Rule.ADDRESS_NOT_IN_SOURCE, Rule.VALUE_EMPTY)


@seeded
def test_an_email_inside_a_longer_address_is_refused(seed: int) -> None:
    """Whatever the grammar lets stand before the local part or after the domain makes a longer
    address: the shorter one is not written."""
    rng = generator(8000 + seed)
    for _ in range(10):
        email = some_email(rng)
        letter = rng.choice(LETTERS + DIGITS + "é")
        before = rng.choice((letter, letter + rng.choice(ATEXT_SIGNS + "."), letter + "''", "@"))
        after = rng.choice(
            (letter, "-" + letter, "_" + letter, "." + letter, ".." + letter, "@" + letter)
        )
        for longer in (before + email, email + after, before + email + after):
            source = f"{PADDING} {longer} {sentence(rng, 3, 5)}"
            result = check_item("email", email, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, email, result)
        # a sign that joins the atoms of a local part never opens an address in a text
        for sign in "._%+-/":
            source = f"{PADDING} {sign}{email} {sentence(rng, 3, 5)}"
            result = check_item("email", email, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, email, result)


@seeded
def test_what_a_text_writes_around_an_email_is_not_part_of_it(seed: int) -> None:
    """A quote, the asterisks or the backticks of Markdown before an address, with nothing of an
    address before them, and whatever is not of a domain after it: the address is whole."""
    rng = generator(8500 + seed)
    for _ in range(8):
        email = some_email(rng)
        opening, closing = rng.choice(
            (("'", "'"), ("`", "`"), ("**", "**"), ("*", "*"), ("|", "|"), ("{", "}"), ("", ""))
        )
        # a domain holds none of these: whatever follows, the address ended before it
        after = rng.choice(("", "?subject=Bonjour", "/x", "!", "%20", "+x", "=", "~", "&", "/"))
        source = f"{PADDING} {opening}{email}{closing}{after} {plain_words(rng, 3, 5)}"
        result = check_item("email", email, source, [source])
        assert isinstance(result, Accepted), (seed, source, email, result)


@seeded
def test_an_email_is_compared_in_lower_case_never_case_folded(seed: int) -> None:
    rng = generator(8700 + seed)
    for _ in range(6):
        name = "".join(rng.choice(LETTERS) for _ in range(rng.randint(2, 5)))
        sharp, double = f"{name}ß{name}", f"{name}ss{name}"
        host = rng.choice(HOSTS)
        for written, proposed in ((sharp, double), (double, sharp)):
            source = framed(rng, f"{written}@{host}")
            wrong = check_item("email", f"{proposed}@{host}", source, [source])
            assert refused(wrong, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, proposed, wrong)
            for honest in (f"{written}@{host}", f"{written.capitalize()}@{host.upper()}"):
                right = check_item("email", honest, source, [source])
                assert isinstance(right, Accepted), (seed, source, honest, right)
        source = framed(rng, f"https://www.{sharp}.example/{name}")
        wrong = check_item("url", f"https://www.{double}.example/{name}", source, [source])
        assert refused(wrong, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, wrong)


@seeded
def test_a_url_is_accepted_as_written_and_no_part_of_it(seed: int) -> None:
    rng = generator(9000 + seed)
    for _ in range(6):
        url = some_url(rng)
        scheme = rng.choice(("", "https://", "http://", "www.", "https://www.", "HTTPS://"))
        slash = rng.choice(("", "/")) if "?" not in url and "#" not in url else ""
        source = framed(rng, f"{scheme}{url}{slash}")
        host, sign, rest = url.partition("/")
        shouted = f"{host.upper()}{sign}{rest}"
        for honest in (url, f"https://{url}", f"http://www.{url}/", f"www.{url}", shouted):
            if honest.endswith("/") and ("?" in url or "#" in url):
                continue  # a slash after a query is another query
            result = check_item("url", honest, source, [source])
            assert isinstance(result, Accepted), (seed, source, honest, result)
        if any(char.isalpha() for char in rest):
            # the path and the query are compared as written
            for other in (f"{host}{sign}{rest.swapcase()}", url.upper()):
                result = check_item("url", other, source, [source])
                assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, other, result)
        for part in parts_of(url):
            if part.rstrip("/") == url:
                continue
            result = check_item("url", part, source, [source])
            assert isinstance(result, Refusal), (seed, source, part)
            assert result.rule in (Rule.ADDRESS_NOT_IN_SOURCE, Rule.VALUE_EMPTY)


@seeded
def test_a_url_that_goes_on_or_started_earlier_is_refused(seed: int) -> None:
    """Whatever joins the parts of a URL, before the value or after it, makes a longer URL."""
    rng = generator(10_000 + seed)
    for _ in range(10):
        url = some_url(rng)
        letter = rng.choice(LETTERS + DIGITS)
        after = rng.choice(
            (
                *("/x", "/Brain", ".html", "?id=4", "#top", "&a=1", "=1", "-x", "_x", "x", "2"),
                *("%20", "+x", "@x", "/~x", "/?x", "//x", ":8080" if ":" not in url else "/y"),
            )
        )
        before = rng.choice(
            (
                *(letter, f"{letter}.", "blog.", f"{letter}-", f"{letter}/", f"{letter}@"),
                *("awww.", "xhttps://", "ftp://"),
                *(f"https://{letter}.example/?u=", f"https://{letter}.example/~"),
                *URL_JOINS,
            )
        )
        for longer in (url + after, before + url):
            source = f"{PADDING} {longer} {sentence(rng, 3, 5)}"
            result = check_item("url", url, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, url, result)
        # the same URL after its own scheme, in the query of another URL
        source = f"{PADDING} https://redirect.example/?u=https://{url} {sentence(rng, 3, 5)}"
        result = check_item("url", f"https://{url}", source, [source])
        assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, url, result)
        # a colon bounds a URL, except the colon of its scheme and the colon of its port
        source = f"{PADDING} Site:https://{url} {sentence(rng, 3, 5)}"
        pieces = [f"//{url}"] + ([url.partition(":")[2]] if ":" in url else [])
        for piece in pieces:
            result = check_item("url", piece, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, piece, result)
        result = check_item("url", url, f"{PADDING} Site:{url} ici", [f"{PADDING} Site:{url} ici"])
        assert isinstance(result, Accepted), (seed, url, result)


@seeded
def test_what_a_text_writes_around_a_url_is_not_part_of_it(seed: int) -> None:
    """The delimiters of RFC 3986 that a text also uses as punctuation bound a URL: the cells
    of a CSV line, a label and its colon, parentheses, brackets."""
    rng = generator(10_500 + seed)
    for _ in range(8):
        url = some_url(rng)
        opening = rng.choice((*URL_AROUND, "[PERSONAL:", "Durand,", "(voir ", "Site : "))
        closing = rng.choice((")", ",", ";", "]", "!", "'", "*", ", Bordeaux", ""))
        source = f"{PADDING} {opening}https://{url}{closing} {plain_words(rng, 3, 5)}"
        result = check_item("url", f"https://{url}", source, [source])
        assert isinstance(result, Accepted), (seed, source, url, result)


@seeded
def test_an_address_cut_by_a_line_at_a_hyphen_is_not_repaired_and_no_piece_is_it(seed: int) -> None:
    rng = generator(10_700 + seed)
    for _ in range(6):
        host = rng.choice(HOSTS)
        first = "".join(rng.choice(LETTERS) for _ in range(rng.randint(2, 6)))
        second = "z" + "".join(rng.choice(LETTERS) for _ in range(rng.randint(1, 5))) + "q"
        source = f"{PADDING} {first}-\n{second}@{host} {plain_words(rng, 3, 5)}"
        for value in (f"{second}@{host}", f"{first}-{second}@{host}", f"{first}{second}@{host}"):
            result = check_item("email", value, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, value, result)
        source = f"{PADDING} https://{host}/{first}-\n{second} {plain_words(rng, 3, 5)}"
        for value in (
            f"https://{host}/{first}-",
            f"https://{host}/{first}",
            second,
            f"https://{host}/{first}-{second}",
            f"https://{host}/{first}{second}",
        ):
            result = check_item("url", value, source, [source])
            assert refused(result, Rule.ADDRESS_NOT_IN_SOURCE), (seed, source, value, result)
        # a dash that stands alone at the end of a line cuts nothing
        email = some_email(rng)
        source = f"{PADDING} -\n{email} {plain_words(rng, 3, 5)}"
        result = check_item("email", email, source, [source])
        assert isinstance(result, Accepted), (seed, source, email, result)


# --- 5. phone numbers ----------------------------------------------------------------------------


def some_phone(rng: random.Random) -> tuple[str, str]:
    """A number of the range kept for fiction, as its digits and as a document writes it."""
    pairs = ["39", "98", f"{rng.randint(0, 99):02d}", f"{rng.randint(0, 99):02d}"]
    style = rng.randrange(8)
    if style == 0:
        return "06" + "".join(pairs), "06 " + " ".join(pairs)
    if style == 1:
        return "06" + "".join(pairs), "06." + ".".join(pairs)
    if style == 2:
        return "06" + "".join(pairs), "06-" + "-".join(pairs)
    if style == 3:
        return "06" + "".join(pairs), "06" + "".join(pairs)
    if style == 4:
        return "336" + "".join(pairs), "+33 6 " + " ".join(pairs)
    if style == 5:
        return "3306" + "".join(pairs), "+33 (0)6 " + " ".join(pairs)
    if style == 6:
        return "06" + "".join(pairs), "(06) " + " ".join(pairs)
    return "336" + "".join(pairs), "+33.6." + ".".join(pairs)


@seeded
def test_a_phone_number_is_all_its_digits_and_no_part_of_them(seed: int) -> None:
    rng = generator(11_000 + seed)
    for _ in range(10):
        digits, shown = some_phone(rng)
        before = rng.choice(("Tél : ", "Tél. ", "Mobile ", "", "Bordeaux, ", "né en 1990, tél "))
        after = rng.choice(("", ", Bordeaux", "\n33000 Bordeaux", " (mobile)", " ou par mail", "."))
        source = f"{sentence(rng, 3, 4)}\n{before}{shown}{after}\n{sentence(rng, 3, 4)}"
        for honest in (shown, digits, " ".join(digits), f"+{digits}"):
            result = check_item("phone", honest, source, [source])
            assert isinstance(result, Accepted), (seed, source, honest, result)
        for cut in range(1, len(digits)):
            for part in (digits[cut:], digits[:cut], digits[cut:] + "0", "1" + digits[:cut]):
                result = check_item("phone", part, source, [source])
                assert isinstance(result, Refusal), (seed, source, part)
                assert result.rule in (Rule.PHONE_NOT_IN_SOURCE, Rule.PHONE_TOO_SHORT)
        for wrong in (digits + "1", "0" + digits, digits[:-1] + str((int(digits[-1]) + 1) % 10)):
            result = check_item("phone", wrong, source, [source])
            assert refused(result, Rule.PHONE_NOT_IN_SOURCE), (seed, source, wrong, result)


# --- 6. names ------------------------------------------------------------------------------------

NAME_WORDS = ("Thalesgroup", "Services", "Betclic", "DevTech", "Solutions", "Université", "Elsan")


@seeded
def test_a_name_is_whole_words_and_never_a_part_of_one(seed: int) -> None:
    rng = generator(12_000 + seed)
    for _ in range(10):
        words = rng.sample(NAME_WORDS, rng.randint(1, 3))
        name = " ".join(words)
        before = rng.choice(("Consultant chez ", "Stage à ", "(", "", "Mission : ", "l'"))
        after = rng.choice((" à Paris", ", Bordeaux", ")", ".", "\nDéveloppeur", " ; puis"))
        source = f"{sentence(rng, 3, 4)}. {before}{name}{after} {sentence(rng, 3, 4)}"
        for honest in (name, name.upper(), name.lower(), name.replace(" ", "\n")):
            result = check_item("name", honest, source, [source])
            assert isinstance(result, Accepted), (seed, source, honest, result)
        misses = set()
        for cut in range(1, len(name)):
            if name[cut] != " " and name[cut - 1] != " ":
                misses.add(name[:cut])  # stops inside a word
                misses.add(name[cut:])  # starts inside a word
        misses.add(name.replace(" ", "")) if " " in name else None
        misses.add(name + "x")
        misses.add("x" + name)
        misses.add(name[:2] + " " + name[2:])
        for miss in misses:
            if citations.skeleton(citations.normalise(miss).text).text in {
                citations.skeleton(citations.normalise(word).text).text for word in WORDS
            }:
                continue
            result = check_item("name", miss, source, [source])
            assert refused(result, Rule.NAME_NOT_IN_SOURCE), (seed, source, miss, result)


# --- 7. a cut excerpt hides nothing --------------------------------------------------------------


def every_part(rng: random.Random, source: str, sample: int = 50) -> list[str]:
    """Slices of the line cut anywhere, long enough to be an excerpt."""
    if len(source) < 22:
        return []
    out = []
    for _ in range(sample):
        start = rng.randrange(len(source) - 20)
        end = rng.randint(start + 20, len(source))
        out.append(source[start:end])
    return out


def assert_refused_from_every_part(
    rng: random.Random, field: str, value: str, source: str, context: object
) -> None:
    prepared = prepare(source)
    whole = check_item(field, value, prepared, [source])
    assert isinstance(whole, Refusal), (context, source, value)
    for excerpt in every_part(rng, source):
        result = check_item(field, value, prepared, [excerpt])
        assert isinstance(result, Refusal), (context, source, value, excerpt, result)


@few_seeds
def test_no_part_of_a_line_supports_what_the_line_refuses(seed: int) -> None:
    rng = generator(13_000 + seed)
    # a number cut out of a longer one
    whole, fraction = some_number(rng)
    present = meanings(plain(whole, fraction))
    source = f"{sentence(rng, 3, 5)} {written(rng, whole, fraction)} {sentence(rng, 3, 5)}"
    for miss in sorted(near_misses(whole, fraction)):
        if not meanings(miss) <= present:
            assert_refused_from_every_part(rng, "text", f"valeur {miss}", source, seed)
    # a part of an email, a part of a URL
    email = some_email(rng)
    source = framed(rng, email)
    for part in sorted(parts_of(email))[:: max(1, len(email) // 6)]:
        assert_refused_from_every_part(rng, "email", part, source, seed)
    url = some_url(rng)
    source = framed(rng, f"https://www.{url}")
    for part in sorted(parts_of(url))[:: max(1, len(url) // 6)]:
        if part.rstrip("/") != url:
            assert_refused_from_every_part(rng, "url", part, source, seed)
    # a part of a phone number
    digits, shown = some_phone(rng)
    source = f"{sentence(rng, 3, 4)}, tél {shown}, {sentence(rng, 3, 4)}"
    for cut in range(1, len(digits) - 5):
        assert_refused_from_every_part(rng, "phone", digits[cut:], source, seed)
        assert_refused_from_every_part(rng, "phone", digits[:-cut], source, seed)
    # a month the line does not give
    year, month, day = some_date(rng)
    for form in rng.sample(year_only_forms(year, month, day), 4):
        source = wrapped(rng, form)
        for claimed in rng.sample(range(1, 13), 3):
            assert_refused_from_every_part(rng, "date", f"{year}-{claimed:02d}", source, seed)
    for form in rng.sample(month_forms(year, month, day), 4):
        source = wrapped(rng, form)
        other = 1 + (month + rng.randint(0, 10)) % 12
        if other != month:
            assert_refused_from_every_part(rng, "date", f"{year}-{other:02d}", source, seed)
    # a part of a name
    name = " ".join(rng.sample(NAME_WORDS, 2))
    source = f"{sentence(rng, 3, 4)}. Consultant chez {name} à Paris {sentence(rng, 3, 4)}"
    for miss in (name[1:], name[:-1], name[2:], name[:-2], name[:3], name.replace(" ", "")):
        assert_refused_from_every_part(rng, "name", miss, source, seed)


@seeded
def test_text_around_the_cited_lines_changes_nothing(seed: int) -> None:
    """The same line cited inside a longer document gives the same answer, whatever stands on
    the lines before and after it: years, months, numbers, addresses of other lines."""
    rng = generator(14_000 + seed)
    noise = [
        "2019 janvier 2020 06 39 98 00 00 camille@example.org 12 345 version 1.1",
        "Mission de 09/21 à 03/23, 10 000 utilisateurs, https://example.org/x",
        "mars avril mai juin 2021 2022 2023 2024 3,5 0,125 www.site.example",
    ]
    year, month, day = some_date(rng)
    email, url = some_email(rng), some_url(rng)
    digits, shown = some_phone(rng)
    whole, fraction = some_number(rng)
    cases = [
        ("date", f"{year}-{month:02d}", wrapped(rng, rng.choice(month_forms(year, month, day)))),
        ("date", f"{year}-{1 + month % 12:02d}", wrapped(rng, f"{EN[month - 1]} {year}")),
        (
            "date",
            f"{year}-{month:02d}",
            wrapped(rng, rng.choice(year_only_forms(year, month, day))),
        ),
        ("email", email, framed(rng, email)),
        ("email", email[1:], framed(rng, email)),
        ("url", url, framed(rng, f"https://{url}")),
        ("url", url[:-1], framed(rng, f"https://{url}")),
        ("phone", digits, f"{sentence(rng, 3, 4)}, tél {shown}, {sentence(rng, 3, 4)}"),
        ("phone", digits[1:], f"{sentence(rng, 3, 4)}, tél {shown}, {sentence(rng, 3, 4)}"),
        (
            "text",
            f"valeur {plain(whole, fraction)}",
            f"{PADDING} {written(rng, whole, fraction)} ici",
        ),
        ("text", f"valeur {whole + 1}", f"{PADDING} {written(rng, whole, fraction)} ici"),
    ]
    for field, value, line in cases:
        line = line.replace("\n", " ")
        alone = check_item(field, value, line, [line])
        around = "\n".join([*rng.sample(noise, 2), line, *rng.sample(noise, 2)])
        inside = check_item(field, value, around, [line])
        assert type(alone) is type(inside), (seed, field, value, line, alone, inside)
        if isinstance(alone, Refusal) and isinstance(inside, Refusal):
            assert alone.rule is inside.rule
        if isinstance(inside, Accepted):
            (citation,) = inside.citations
            assert around[citation.start : citation.end] == citation.excerpt
            assert citation.excerpt.strip() == line.strip()


SOUP = (
    list(EN[:6])
    + list(FR[5:])
    + ["janv.", "sept", "sept.", "Mar", "mar.", "may.", "déc"]
    + ["2019", "2020", "2021", "2023", "1999", "21", "23", "09", "03", "12", "13", "3", "9", "15"]
    + ["1er", "15th", "3rd", "06", "39", "98", "0639981234", "+33", "(0)6", "10 000", "3,5", "1.10"]
    + ["/", "/", "-", "-", ".", ",", "'", ":", "(", ")", "\u2013", "\u2192"]
    + [" ", " ", " ", " ", "\n", "   ", " à ", " to ", " et ", " and ", " jusqu'à ", " au "]
    + ["the ", " of ", "present", "aujourd'hui", "en cours", "collaborateurs", "S", "x", "v", "T"]
    + ["version ", "camille@example.org", "www.example.org/x", "https://", "Betclic", "Thales"]
)


def soup(rng: random.Random, low: int = 10, high: int = 40) -> str:
    """Pieces of dates, numbers, phone numbers and addresses glued at random: no sense, every
    shape."""
    return "".join(rng.choice(SOUP) for _ in range(rng.randint(low, high))).strip()


@seeded
def test_a_part_of_a_text_says_no_more_than_the_text(seed: int) -> None:
    """What a span reads, in its place, is read by the whole text too: years, months next to
    their year, two digit years, phone numbers, numbers. So cutting an excerpt never adds a
    fact."""
    rng = generator(17_000 + seed)
    for _ in range(25):
        norm = citations.normalise(soup(rng)).text
        whole_dates = citations.dates_of(norm)
        whole_phones = citations.phones_of(norm)
        whole_numbers = citations.numbers_of(norm)
        cuts = boundaries(norm)
        if len(cuts) < 2:
            continue
        for _ in range(25):
            start, end = sorted(rng.sample(cuts, 2))
            context = (seed, norm, start, end)
            part = citations.dates_of(norm, start, end)
            assert part.years <= whole_dates.years, context
            assert part.pairs <= whole_dates.pairs, context
            assert part.short <= whole_dates.short, context
            assert part.implied <= whole_dates.implied, context
            assert citations.phones_of(norm, start, end) <= whole_phones, context
            assert citations.numbers_of(norm, start, end) <= whole_numbers, context


@few_seeds
def test_what_a_part_of_a_line_supports_the_line_supports(seed: int) -> None:
    """Whatever the value, an excerpt that is a part of a line never supports what the whole
    line does not."""
    rng = generator(18_000 + seed)
    for _ in range(6):
        line = soup(rng, 15, 45).replace("\n", " ").replace("   ", " ")
        prepared = prepare(line)
        if not (25 <= len(prepared.text) <= 500):
            continue
        whole = find(prepared, line)
        if isinstance(whole, Refusal):
            continue
        pieces = [line[a : a + rng.randint(1, 30)] for a in rng.sample(range(len(line)), 6)]
        values = {
            "date": [f"{year}-{month:02d}" for year in (2019, 2021, 2023) for month in (1, 3, 9)],
            "phone": [*pieces, "0639981234", "39981234", "33639981234"],
            "email": [*pieces, "camille@example.org", "amille@example.org", "camille@example"],
            "url": [*pieces, "example.org/x", "example.org", "www.example.org", "org/x"],
            "name": [*pieces, "Betclic", "Bet", "Thales", "janv"],
            "text": [*pieces, "10 000", "10", "3,5", "5", "1.1", "version 1.1", "2019"],
        }
        for excerpt in every_part(rng, line, sample=8):
            for field, candidates in values.items():
                for value in candidates:
                    by_part = check_item(field, value, prepared, [excerpt])
                    if isinstance(by_part, Accepted):
                        by_line = check_item(field, value, prepared, [line])
                        assert isinstance(by_line, Accepted), (seed, line, excerpt, field, value)


# --- 8. hostile text -----------------------------------------------------------------------------

HOSTILE = [
    *list("abcdefXYZéàüßıİ0123456789 \n\t\r\x0c\x0b.,;:'\"-/@()+%_&=?#~!*[]{}|\\<>"),
    "\x00",
    "\x01",
    "\x1b",
    "\x1c",
    "\x7f",
    "\x85",
    "\xa0",
    "\xad",
    "\u034f",
    "\u200b",
    "\u200d",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202e",
    "\u2066",
    "\u2069",
    "\ufeff",
    "\u0301",
    "\u0316",
    "\u0308",
    "\u20e3",
    "\ufe0f",
    "\u3099",
    "\u2018",
    "\u2019",
    "\u201c",
    "\u2013",
    "\u2014",
    "\u2212",
    "\u00b4",
    "\u2033",
    "\u2192",
    "ﬁ",
    "\u00b2",
    "\u00bd",
    "\u2460",
    "\uff11",
    "\u0663",
    "\u2028",
    "\u2029",
    "\u06dd",
    "\u0600",
    "\u180e",
    "\u3000",
    "阿",
    "里",
    "メ",
    "ル",
    "ก",
    "\u0e35",
    "한",
    "ᄒ",
    "ᅡ",
    "م",
    "ه",
    "ক",
    "\u09c7",
    "\U0001f600",
    "\U000e0041",
    "\ufffd",
    "ﷺ",
    "ẞ",
    chr(55296),
    chr(57343),
    chr(56320),
    "janvier",
    "2023",
    "06 39 98 12 34",
    "camille@example.org",
    "www.",
    "https://",
    "version ",
]
FIELDS = ("email", "url", "name", "date", "phone", "text", "term", "enum", "setting", "colour")


def hostile(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choice(HOSTILE) for _ in range(rng.randint(low, high)))


def assert_sound(text: str, result: Accepted | Refusal, excerpts: list[str]) -> None:
    if isinstance(result, Refusal):
        assert result.reason == citations.REASONS[result.rule]
        assert isinstance(result.index, str)
        return
    assert isinstance(result, Accepted)
    assert len(result.citations) == len(excerpts)
    assert set(result.flags) <= {citations.LOOKS_LIKE_INSTRUCTION}
    for citation in result.citations:
        assert 0 <= citation.start < citation.end <= len(text)
        assert text[citation.start : citation.end] == citation.excerpt
        assert len(citation.excerpt) <= citations.SPAN_SOURCE_MAX


@pytest.mark.parametrize("seed", range(120))
def test_nothing_raises_on_hostile_text_and_the_answer_is_the_same_twice(seed: int) -> None:
    rng = generator(15_000 + seed)
    text = hostile(rng, 0, 400)
    prepared = prepare(text)
    norm = prepared.text
    # the invariants of the normalisation
    assert len(prepared.origin) == len(norm)
    assert list(prepared.origin) == sorted(prepared.origin)
    assert all(index < len(text) for index in prepared.origin)
    assert norm == norm.strip(" \n")
    assert "  " not in norm
    assert " \n" not in norm
    assert "\n " not in norm
    assert "\n\n" not in norm
    assert all(not char.isspace() or char in " \n" for char in norm)
    assert all(not char.isdecimal() or char in "0123456789" for char in norm)
    assert [norm[index] for index in prepared.skeleton_index] == list(prepared.skeleton)
    assert citations.normalise(text).text == norm
    for _ in range(12):
        excerpts = []
        for _ in range(rng.randint(0, 3)):
            if text and rng.random() < 0.7:
                start = rng.randrange(len(text))
                excerpts.append(text[start : start + rng.randint(0, 80)])
            else:
                excerpts.append(hostile(rng, 0, 60))
        field = rng.choice(FIELDS)
        value: object = rng.choice(
            (
                hostile(rng, 0, 40),
                text[: rng.randint(0, 40)],
                rng.choice(excerpts) if excerpts else "",
                None,
                rng.randint(-5, 3000),
                1.5,
                True,
                [hostile(rng, 0, 10), None, 3],
                {"a": hostile(rng, 0, 10)},
                {hostile(rng, 0, 5): [hostile(rng, 0, 5)]},
                {10**5000: hostile(rng, 0, 5), 3: None, None: 3},
                {"a": {10**5000: "x"}},
                b"bytes",
            )
        )
        types: object = rng.choice(
            (field, {"a": field, "b[]": "text"}, {"": field}, {1: field}, {10**5000: field}, None)
        )
        once = check_item(types, value, prepared, excerpts)  # type: ignore[arg-type]
        twice = check_item(types, value, text, excerpts)  # type: ignore[arg-type]
        assert once == twice, (seed, field, value, excerpts)
        assert_sound(text, once, excerpts)
        for unread in (None, 3, b"bytes"):
            gone = check_item(types, value, unread, excerpts)  # type: ignore[arg-type]
            assert isinstance(gone, Refusal)
        for excerpt in excerpts:
            found = find(prepared, excerpt)
            if isinstance(found, Span):
                assert citations.is_token_boundary(norm, found.start)
                assert citations.is_token_boundary(norm, found.end)
                assert 0 <= found.start < found.end <= len(norm)
                citations.check_value(field, value, [found])
    # the readers, on their own
    sample = hostile(rng, 0, 200)
    normalised = citations.normalise(sample).text
    assert citations.normalise(sample, casefold=False).origin is not None
    citations.dates_of(normalised)
    citations.phones_of(normalised)
    citations.numbers_of(normalised)
    citations.looks_like_instruction(normalised)
    citations.readings(sample)
    for at in range(-1, len(normalised) + 2):
        citations.is_token_boundary(normalised, at)


@seeded
def test_a_passage_of_hostile_text_cited_as_it_is_written_is_found(seed: int) -> None:
    """Whatever the text holds, a passage of it that is long enough is found, or is refused for
    its size alone: `excerpt not found` is never the answer for the text's own words."""
    rng = generator(16_000 + seed)
    text = hostile(rng, 200, 600)
    prepared = prepare(text)
    for _ in range(40):
        start = rng.randrange(len(text) - 60)
        # a cluster is not cut: start after whitespace, and stop before whitespace
        excerpt = text[start : start + rng.randint(30, 200)]
        if not excerpt[:1].isalnum() or not excerpt[-1:].isalnum():
            continue
        if start and not text[start - 1].isspace():
            continue
        found = find(prepared, excerpt)
        if isinstance(found, Refusal):
            assert found.rule is not Rule.EXCERPT_NOT_FOUND, (seed, excerpt)
