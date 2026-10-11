"""Break the citation checker one line at a time and see that a test fails (task G4, issue #45).

Not product code and not a test of CI: evidence that the tests of api/tests/ingestion would
notice a rule that stops holding. Each entry replaces one piece of app/ingestion/citations.py
by a wrong one (no context around a span, a chain read backward, floats, an address that ends
anywhere), runs the tests, and puts the file back. It matches the text of the module as it was
on 2026-10-11: after a refactoring an entry may no longer apply, and says so.

    cd api && uv run python ../tasks/evidence/ingestion-g4/mutations.py \
        > ../tasks/evidence/ingestion-g4/mutations.txt

Exit status 1 when a change is not noticed or does not apply.
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
        "an address ends anywhere",
        "    return rest < stop and not _address_char(text[rest], _ADDRESS_AFTER)",
        "    return True",
    ),
    (
        "an address starts anywhere",
        "    if at == 0 or not _address_char(text[at - 1], _ADDRESS_BEFORE):"
        + N
        + "        return True",
        "    if True:" + N + "        return True",
    ),
    (
        "the words of a name not kept",
        "                and _words_kept(text, bones, at, wanted)" + N,
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
    ("any fraction is a decimal", '(len(parts[1]) <= 2 or parts[0] == "0")', "True"),
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
        "                run.version or not readings(run.text) & self._readings",
        "                not readings(run.text) & self._readings",
    ),
    (
        "a version in the source is a number",
        "                    if not run.version:"
        + N
        + "                        found |= readings(run.text)",
        "                    found |= readings(run.text)",
    ),
    ("format characters kept", '        category == "Cf"' + N, "        False" + N),
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
        "                return head == 0 or not _address_char(text[head - 1], _ADDRESS_BEFORE)",
        "                return True",
    ),
    (
        "a date value of any shape",
        '_DATE_VALUE = re.compile(r"([0-9]{4})(?:-(0[1-9]|1[0-2]))?")',
        '_DATE_VALUE = re.compile(r"([0-9]{4})(?:-([0-9]{2}))?")',
    ),
    (
        "years of any size",
        'if token.kind == "d" and len(token.text) == 4}',
        'if token.kind == "d" and len(token.text) >= 4}',
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
]


def passes(paths: list[str]) -> bool:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *paths],
        cwd=API,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


def main() -> int:
    original = MODULE.read_text(encoding="utf-8")
    missed = []
    print(
        "# one line of app/ingestion/citations.py broken at a time: which tests notice"
    )
    try:
        if not passes(FAST + SLOW):
            print("the tests fail before any change")
            return 1
        for name, old, new in MUTATIONS:
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
    print(f"# {len(MUTATIONS)} changes, {len(MUTATIONS) - len(missed)} noticed")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
