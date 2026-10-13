"""Break the citation checker one line at a time and see that a test fails (task G4, issue #45).

Not product code and not a test of CI: evidence that the tests of api/tests/ingestion would
notice a rule that stops holding. Each entry replaces one piece of app/ingestion/citations.py
by a wrong one (no context around a span, a chain read backward, floats, an address that ends
anywhere), runs the tests, and puts the file back. It matches the text of the module as it was
on 2026-10-11 after the two reviews of pull request #97, whose findings each have their
entries at the end: after a refactoring an entry may no longer apply, and says so.

    cd api && uv run python ../tasks/evidence/ingestion-g4/mutations.py \
        > ../tasks/evidence/ingestion-g4/mutations.txt

Exit status 1 when a change is not noticed or does not apply. With names of entries as
arguments, those entries alone are run.
"""

import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "api"
MODULE = API / "app" / "ingestion" / "citations.py"
FAST = [
    "tests/ingestion/test_citations.py",
    "tests/ingestion/test_citation_examples.py",
    "tests/ingestion/test_citation_documents.py",
]
SLOW = [
    "tests/ingestion/test_citation_properties.py",
    "tests/ingestion/test_citation_bounds.py",
]
N = "\n"

# (what is broken, the text of the module, what replaces it)
MUTATIONS = [
    ("no context before a date", "_DATE_BEFORE = 2", "_DATE_BEFORE = 0"),
    ("no context after a date", "_DATE_AFTER = 24", "_DATE_AFTER = 0"),
    ("no context around a phone", "_PHONE_CONTEXT = 48", "_PHONE_CONTEXT = 0"),
    ("one occurrence looked at", "OCCURRENCES_MAX = 64", "OCCURRENCES_MAX = 1"),
    (
        "a separator before a digit is no token",
        "        return before in _NUMBER_SEPARATORS and at >= 2 and _digit(norm[at - 2])",
        "        return False",
    ),
    ("a chain of months may go backward", "if following < previous:", "if False:"),
    (
        "school years are months",
        "if _school_year(first) and _school_year(second):",
        "if False:",
    ),
    ("a range in any order", "or not _later(first, second):", "or False:"),
    (
        "YYYY-MM always firm",
        'kind = "firm" if second <= year % 100 else "ambiguous"',
        'kind = "firm"',
    ),
    (
        "control characters removed",
        '            kept.append("\\n")' + N + "        elif not _invisible",
        "            pass" + N + "        elif not _invisible",
    ),
    (
        "no fence around a superscript",
        "    if out and not cluster[0].isdecimal():",
        "    if False:",
    ),
    (
        "no version",
        "yield _Run(run.group(), _VERSION_BEFORE.search(before) is not None, form)",
        "yield _Run(run.group(), False, form)",
    ),
    (
        "a URL ends anywhere",
        "    return rest < stop and not _address_char(text[rest], _URL_AFTER)",
        "    return True",
    ),
    (
        "an email ends anywhere",
        '    return rest < stop and text[rest] != "@" and not _address_char(text[rest], "-_")',
        "    return True",
    ),
    (
        "a URL starts anywhere",
        "    if not _address_char(text[at - 1], _URL_JOINS):"
        + N
        + "        return True",
        "    if True:" + N + "        return True",
    ),
    (
        "an email starts anywhere",
        '    return not _address_char(before, "")',
        "    return True",
    ),
    (
        "the words of a name not kept",
        "                and _words_kept(read, at, wanted, norm)" + N,
        "",
    ),
    (
        "a name inside tokens",
        "                is_token_boundary(text, start)"
        + N
        + "                and is_token_boundary(text, end)"
        + N
        + "                and (size",
        "                True" + N + "                and (size",
    ),
    ("short names in any case", "and (size >= SHORT_NAME or ", "and (True or "),
    (
        "three digits after four are a second number",
        "        if not _two_numbers(whole, fraction, separators[0]):",
        "        if len(fraction) != 3 and not _two_numbers(whole, fraction, separators[0]):",
    ),
    ("groups of any size", "all(len(part) == 3 for part in parts[1:]):", "True:"),
    (
        "a phone number cut by the span",
        "        if run.start() < clear or run.end() > end:",
        "        if False:",
    ),
    ("no french rule", "        for pattern in _FRENCH:", "        for pattern in ():"),
    ("sept is always a month", "not (bare_sept and counted)", "not False"),
    ("a year outside the span pairs", "if year_at < self.limit and not", "if not"),
    (
        "a chain of numbers cut by the span is read",
        "if at >= self.first and end <= self.limit and not after_word:",
        "if not after_word:",
    ),
    (
        "a chain of numbers glued to a word is read",
        "if at >= self.first and end <= self.limit and not after_word:",
        "if at >= self.first and end <= self.limit:",
    ),
    (
        "any month",
        "    if (year, month) in facts.pairs or (year % 100, month) in facts.short:",
        "    if True:",
    ),
    ("any year", "    if year not in facts.years:", "    if False:"),
    (
        "dotted dates month first",
        'elif 12 < second <= 31 and 1 <= first <= 12 and separator != ".":',
        "elif 12 < second <= 31 and 1 <= first <= 12:",
    ),
    ("an ambiguous date is day first", '(separator == "." or first == second)', "True"),
    ("no minimum length", "        if len(wanted) < EXCERPT_MIN:", "        if False:"),
    (
        "no minimum skeleton",
        "        if len(needle) < SKELETON_MIN:",
        "        if False:",
    ),
    ("no maximum length", "        if len(wanted) > EXCERPT_MAX:", "        if False:"),
    ("settings pass", "    if setting is not None:", "    if False:"),
    ("unknown fields pass", "    if unknown is not None:", "    if False:"),
    (
        "no dotted abbreviation",
        'dotted = self._is(at + 1, ".") and tokens[at + 1].glued',
        "dotted = False",
    ),
    (
        "accents kept in month words",
        'kind, piece = "w", _unaccent(piece)',
        'kind, piece = "w", piece',
    ),
    (
        "no case fold",
        "    if casefold:" + N + "        text = text.casefold()",
        "    if False:" + N + "        pass",
    ),
    (
        "no widening",
        "            chosen = _widen(norm, *first)",
        "            chosen = first",
    ),
    (
        "no limit on the source under a span",
        "    if span.source_end - span.source_start > SPAN_SOURCE_MAX:",
        "    if False:",
    ),
    (
        "never flagged by a span",
        "    flagged = any(looks_like_instruction(span.text) for span in spans)",
        "    flagged = False",
    ),
    (
        "any phone number",
        "    return None if digits in phones else Rule.PHONE_NOT_IN_SOURCE",
        "    return None",
    ),
    (
        "a version in the value is a number",
        "                run.version or not readings(run.text) <= self._readings",
        "                not readings(run.text) <= self._readings",
    ),
    (
        "a version in the source is a number",
        "                    if not run.version:"
        + N
        + "                        found |= readings(run.text)",
        "                    found |= readings(run.text)",
    ),
    (
        "format characters kept",
        '        (category == "Cf" and char not in _FORMAT_SIGNS)' + N,
        "        False" + N,
    ),
    (
        "no fold before NFKC",
        "    for char in cluster.translate(_FOLDS):",
        "    for char in cluster:",
    ),
    (
        "the present outside the span",
        "if present and atom.end + step + present <= self.limit:",
        "if present:",
    ),
    (
        "a shared year outside the span",
        "        if 0 <= shared < self.limit:",
        "        if shared >= 0:",
    ),
    (
        "letters of a script without spaces are address",
        "(char.isalnum() and not _unspaced(char))",
        "char.isalnum()",
    ),
    (
        "a script without spaces has runs",
        "        return not (_unspaced(before) or _unspaced(after))",
        "        return True",
    ),
    (
        "no limit on a span",
        "    if chosen is None or chosen[1] - chosen[0] > SPAN_MAX:",
        "    if chosen is None:",
    ),
    (
        "no limit on the excerpts",
        "    if len(excerpts) > EXCERPTS_MAX:",
        "    if False:",
    ),
    (
        "digits of other scripts kept",
        "            out.append(str(unicodedata.decimal(char)))",
        "            out.append(char)",
    ),
    (
        "numbers as floats",
        'return f"{whole}.{fraction}" if fraction else whole',
        'return str(float(f"{whole}.{fraction or 0}"))',
    ),
    (
        "the scheme of a URL anywhere",
        "            return head == 0 or not _address_char(text[head - 1], _URL_JOINS)",
        "            return True",
    ),
    (
        "a date value of any shape",
        '_DATE_VALUE = re.compile(r"([0-9]{4})(?:-(0[1-9]|1[0-2]))?")',
        '_DATE_VALUE = re.compile(r"([0-9]{4})(?:-([0-9]{2}))?")',
    ),
    (
        "years of any size",
        'tokens[at].kind == "d" and len(tokens[at].text) == size',
        'tokens[at].kind == "d" and len(tokens[at].text) >= size',
    ),
    (
        "a day of any size",
        "        if not 1 <= int(tokens[at].text) <= 31:"
        + N
        + "            return 0"
        + N,
        "",
    ),
    (
        "two commas between a month and its year",
        '        if self._is(at, ","):' + N + "            at = skip(at + 1)" + N,
        '        while self._is(at, ","):' + N + "            at = skip(at + 1)" + N,
    ),
    (
        "a month in one digit anywhere",
        'return _Atom(start, end, numbers[1], numbers[0], "one_digit")',
        'return _Atom(start, end, numbers[1], numbers[0], "firm")',
    ),
    (
        "two digit years read",
        '            return _Atom(start, end, numbers[1], numbers[0], "short")',
        '            return _Atom(start, end, 2000 + numbers[1], numbers[0], "firm")',
    ),
    (
        "a number with a superscript gives its numbers",
        "                        forms.add(run.form)  # a number with a superscript is not that number"
        + N
        + "                        continue",
        "                        forms.add(run.form)",
    ),
    (
        "any written form in a value",
        "                if run.form not in self._forms:",
        "                if False:",
    ),
    (
        "no limit on a text that unfolds",
        "        if limit is not None and len(origin) > limit:",
        "        if False:",
    ),
    (
        "no limit on the characters of a value",
        "    if too_large or characters > VALUE_CHARS_MAX:",
        "    if too_large:",
    ),
    (
        "a tie between two readings of a chain is read",
        "            if alone[0] == alone[1]:" + N + "                return []" + N,
        "",
    ),
    (
        "a chain always split at the hyphen",
        "            groups = both[0] if alone[0] < alone[1] else both[1]",
        "            groups = both[0]",
    ),
    (
        "an unreadable document is read",
        "    if not prepared.readable:",
        "    if False:",
    ),
    (
        "no limit on the clusters remembered",
        "            if len(cache) < CLUSTERS_REMEMBERED:",
        "            if True:",
    ),
    (
        "superscripts glued to a number never found",
        "    glued = source[low:first] + source[last + 1 : high]",
        '    glued = ""',
    ),
    # --- the first review of pull request #97
    (
        "B1: one number of a run of the value carries the others",
        "not readings(run.text) <= self._readings",
        "not readings(run.text) & self._readings",
    ),
    (
        "S4: a decimal with four decimals or more is cut into pieces",
        "        if not _two_numbers(whole, fraction, separators[0]):",
        "        if len(fraction) <= 2 and not _two_numbers(whole, fraction, separators[0]):",
    ),
    (
        "B2: a sign of a local part before an address is never looked past",
        "    while low > floor and text[low - 1] in _LOCAL_SIGNS and text[low - 1] not in _LOCAL_JOINS:",
        "    while False:",
    ),
    (
        "B2: a sign that joins is looked past as a quote is",
        " and text[low - 1] not in _LOCAL_JOINS:",
        ":",
    ),
    (
        "B2: an address is compared under the case fold",
        "        return written.lower() == proposed.lower()",
        "        return written.casefold() == proposed.casefold()",
    ),
    (
        "B2: the path of a URL in any case",
        "    return written[:cut].lower() == proposed[:cut].lower() and written[cut:] == proposed[cut:]",
        "    return written.lower() == proposed.lower()",
    ),
    (
        "B2: the host of a URL as written",
        "    return written[:cut].lower() == proposed[:cut].lower() and written[cut:] == proposed[cut:]",
        "    return written == proposed",
    ),
    (
        "B2: an underscore after a domain ends it",
        'not _address_char(text[rest], "-_")',
        'not _address_char(text[rest], "-")',
    ),
    (
        "B2: the colon of a scheme or of a port bounds a URL",
        '        return not (text.startswith("//", at) or _port(text, at) or _user_info(text, at - 1))',
        "        return True",
    ),
    (
        "N4: the user and the password of a URL are an email",
        "        return not _user_info(text, low - 1)",
        "        return True",
    ),
    (
        "N7: the scheme of a URL is left out of an email too",
        "    head = _URL_HEAD.match(wanted) if url else None",
        "    head = _URL_HEAD.match(wanted)",
    ),
    (
        "S5: an address cut by a line at a hyphen is an address",
        "            if whole and ends(text, end) and not _cut_by_a_line(text, at, end):",
        "            if whole and ends(text, end):",
    ),
    (
        "S1: four digits glued to a decimal are a year",
        "            if end > at + 1 and not self._dotted_date(at, end):",
        "            if False:",
    ),
    (
        "S1: four digits of a phone number are a year",
        "                        if max(beside) >= 3:",
        "                        if False:",
    ),
    (
        "S1: a month word takes a year cut out of a number",
        "        return self._digits(at, 4) and at not in self.cut",
        "        return self._digits(at, 4)",
    ),
    (
        "S2: a name goes across a blank line or two columns",
        "            if where in read.hard or (glued and where in read.soft and not cut_word):",
        "            if glued and where in read.soft and not cut_word:",
    ),
    (
        "S2: one word of a value goes across a line break",
        "            if where in read.hard or (glued and where in read.soft and not cut_word):",
        "            if where in read.hard:",
    ),
    (
        "S2: a spaced dash asks the value for nothing",
        """            if glued or ("-" in gap and "-" not in typed):""",
        "            if glued:",
    ),
    (
        "S3: a format character that has a glyph is no break",
        '        if category == "Cc" or char in _FORMAT_SIGNS:',
        '        if category == "Cc":',
    ),
    (
        "N3: punctuation alone is a name",
        "    if not any(char.isalnum() for char in wanted.text):",
        "    if not size:",
    ),
    (
        "N1: every name makes the skeleton of every span again",
        "        if self._reads is None:",
        "        if True:",
    ),
    (
        "decision: a range of MM/YY up to 12 is read",
        """    return atom.kind != "short" or atom.year > 12""",
        "    return True",
    ),
    (
        "decision: a range that is not read is refused as a month",
        "    if (year % 100, month) in facts.unread:" + N,
        "    if False:" + N,
    ),
    (
        "N6: no hyphen between a month word and its year",
        """        if self._is(at, "-") and tokens[at].glued and self._year(at + 1) and tokens[at + 1].glued:""",
        "        if False:",
    ),
    (
        "N6: a spaced slash is read inside a longer date",
        '        if not parts or goes_on or self._is(end, "/") or not self._year(parts[-1]):',
        '        if not parts or self._is(end, "/") or not self._year(parts[-1]):',
    ),
    (
        "N6: a day and its month take the year of a date with another separator",
        "        if whole is None or whole[4] != separator:",
        "        if whole is None:",
    ),
    (
        "N6: a range that goes backward shares its year",
        " and (its_month, its_day) < (month, day):",
        ":",
    ),
    (
        "N2: a key that is not text is printed",
        """                below = (f"{path}.{key}" if path else key) if isinstance(key, str) else None""",
        """                below = f"{path}.{key}" if path else key""",
    ),
    (
        "N2: a path of the table of types that is not text is read",
        "        if not isinstance(path, str):" + N,
        "        if False:" + N,
    ),
    (
        "N2: a document that is not text is prepared",
        "    return document if isinstance(document, Document) else None",
        "    return document",
    ),
    # --- the second review of pull request #97
    (
        "R1: four decimals after a month are its year, whatever they are",
        "    if not _plain_year(fraction):" + N + "        return False" + N,
        "    if len(fraction) != 4:" + N + "        return False" + N,
    ),
    (
        "R1: any two numbers of four digits are two years",
        "    return _plain_year(whole) or (",
        "    return len(whole) == 4 or (",
    ),
    (
        "R1: a month in one digit before a full stop and its year",
        """len(whole) == 2 and "01" <= whole <= "12")""",
        """len(whole) <= 2 and 1 <= int(whole) <= 12)""",
    ),
    (
        "R2: four digits of a number glued by hyphens are a year",
        "                    if size == 3 or size >= 5:",
        "                    if False:",
    ),
    (
        "R2: four digits next to five glued by a hyphen are a year",
        "                    if size == 3 or size >= 5:",
        "                    if size == 3:",
    ),
    (
        "R2: a ratio written with one digit and a full stop gives a year",
        " or sizes == [1, 4]:",
        ":",
    ),
    (
        "R2: a month in one digit with a full stop is read in a range",
        ' and separator != ".":  # "9/2021 to present"',
        ':  # "9/2021 to present"',
    ),
    (
        "the cells of a row lose their years",
        """ or (len(sizes) >= 3 and signs == {","})""",
        "",
    ),
    (
        "two numbers with a comma are the cells of a row",
        """(len(sizes) >= 3 and signs == {","})""",
        """(len(sizes) >= 2 and signs == {","})""",
    ),
    (
        "a date with spaced slashes ends on any number",
        '        if not parts or goes_on or self._is(end, "/") or not self._year(parts[-1]):',
        '        if not parts or goes_on or self._is(end, "/"):',
    ),
    (
        "the password of a URL starts a URL",
        " or _user_info(text, at - 1))",
        ")",
    ),
    (
        "six digits after a colon start a URL",
        "    if end == at:"
        + N
        + "        return False"
        + N
        + "    rest = text[end : end + 2]",
        "    if end == at or end - at > 5:"
        + N
        + "        return False"
        + N
        + "    rest = text[end : end + 2]",
    ),
    (
        "an email never starts right after another",
        "    if _after_an_address(text, at):" + N + "        return True" + N,
        "",
    ),
    (
        "an email starts after any word and a sign that no domain holds",
        '    return low < domain < at and low > 0 and text[low - 1] == "@"',
        "    return low < domain < at",
    ),
    (
        "an email starts right after the full stop of another",
        "{|}~" + '"' + N + "_DOMAIN_MAX = 255",
        "{|}~." + '"' + N + "_DOMAIN_MAX = 255",
    ),
    (
        "a hyphen before an address is a dash",
        "    if document.source[document.origin[at - 1]] not in _DASHES:"
        + N
        + "        return False"
        + N,
        "",
    ),
    (
        "a dash glued to a word bounds an address",
        "    return at == 1 or not _address_char(text[at - 2], joins)",
        "    return True",
    ),
]


def passes(paths: list[str]) -> bool:
    done = subprocess.run(
        [
            *(sys.executable, "-m", "pytest", "-x", "-q", "-n", "2"),
            *("-p", "no:cacheprovider", *paths),
        ],
        cwd=API,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


def main() -> int:
    original = MODULE.read_text(encoding="utf-8")
    only = sys.argv[1:]
    chosen = [entry for entry in MUTATIONS if not only or entry[0] in only]
    missed = []
    print(
        "# one line of app/ingestion/citations.py broken at a time: which tests notice"
    )
    try:
        if not passes(FAST + SLOW):
            print("the tests fail before any change")
            return 1
        for name, old, new in chosen:
            if original.count(old) != 1:
                print(f"does not apply            | {name}")
                missed.append(name)
                continue
            MODULE.write_text(original.replace(old, new), encoding="utf-8")
            if not passes(FAST):
                print(f"noticed by the unit tests | {name}")
            elif not passes(SLOW):
                print(f"noticed by the properties | {name}")
            else:
                print(f"NOT NOTICED               | {name}")
                missed.append(name)
    finally:
        MODULE.write_text(original, encoding="utf-8")
    print(f"# {len(chosen)} changes, {len(chosen) - len(missed)} noticed")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
