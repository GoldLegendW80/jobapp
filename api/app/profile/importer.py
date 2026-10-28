"""Harald's importer: the engine's files converted into one profile export document.

    convert(SourcePaths.under(job_root), now) -> ExportDocument

A pure function: it reads the files it is given and returns the document, writes nothing,
touches no database and never imports or executes engine code (assemble.py is parsed with `ast`
and only literal constants are read). The CLI (`python -m app.profile.cli convert`) writes the
result to a file and `python -m app.profile.cli import` loads it for one user.

Sources and where they go:
- engine/assemble.py (required): NAME, CONTACT_LEFT, CONTACT_RIGHT, LANGUAGES -> identity;
  EMPLOYERS and EDUCATION -> experience.
- profile/facts.md (required): every `##` heading is one fact block, its body verbatim (only
  line endings normalised, `###` sub-headings stay inside); the text before the first `##` is a
  block named after the `#` title. IDENTITY gives the headline ("Current title") and the proper
  case of the name; the EMPLOYMENT table gives each employer's title and location; EDUCATION gives
  months and the school's city; PRACTICALITIES FOR APPLICATION FORMS is also mapped into the
  answers section (and stays a fact block, so nothing is lost): each answer is the text of the
  matching bullet, cut into sentences where one bullet answers several fields (notice, start
  and travel) or countries, with its first letter capitalised.
- profile/skills.json (optional): the scoring model -> skills.
- engine/boards.json (optional): title, exclude and location regexes -> search. Its company
  boards are not imported: the boards were deleted from the app (owner, 2026-10-28).
- engine/photo.b64 (optional): the photo, a data URL or bare base64 of a PNG or JPEG, re-encoded
  exactly as the app stores an upload (photo.normalize: metadata stripped, at most 1024 px).
A missing optional file gives the empty section (or no photo).

Matching semantics are kept exactly (app/profile/matching.py defines them):
- A boards.json regex is searched as is, so it becomes an advanced term list: it is split on its
  top-level `|` (parentheses, character classes and escapes respected), and an alternative of the
  form `prefix(a|b|c)suffix` with one plain group is expanded into `prefix a suffix`, ... . A
  search matches when any alternative does, so the list decides exactly as the regex did, and each
  word becomes one editable term (the 529 character exclude regex is over the 500 limit anyway).
- A skills category whose terms are all plain words stays plain: the engine and the app both
  match plain terms as whole words with an optional plural. A category with a raw regex term (one
  containing a backslash) becomes advanced, and each term is wrapped the way the engine wraps it:
  plain `w` becomes `\\bw(?:s|es)?\\b` (or the lookaround form when `w` does not start and end
  with a word character), raw `r` becomes `(?<!\\w)(?:r)(?:s|es)?(?!\\w)`.
- A gate is one regex searched as is: one advanced term, penalty 25 (the engine's fixed value).
"""

import ast
import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.profile.matching import PLURAL, escape
from app.profile.photo import PhotoError, normalize
from app.profile.schemas import ExportDocument, sniff_photo


class ConversionError(Exception):
    """A source file is missing, unreadable or not in the shape the importer knows."""


@dataclass(frozen=True)
class SourcePaths:
    facts: Path
    assemble: Path
    skills: Path | None = None
    boards: Path | None = None
    photo: Path | None = None

    @classmethod
    def under(cls, root: Path) -> "SourcePaths":
        """The engine's layout below the job folder: profile/ and engine/."""
        return cls(
            facts=root / "profile" / "facts.md",
            assemble=root / "engine" / "assemble.py",
            skills=root / "profile" / "skills.json",
            boards=root / "engine" / "boards.json",
            photo=root / "engine" / "photo.b64",
        )


# --- defaults of the search section that boards.json does not hold ------------------------------

CONTRACT_TYPES = ["CDI", "CDD", "VIE", "Freelance"]
REMOTE_STANCE = "any"
MIN_FIT = 70
DAYS = 30
NEVER_APPLY = ["No cybersecurity roles: cyber, security, pentest, threat (Harald, 2026-10-06)"]
GATE_PENALTY = 25

ASSEMBLE_CONSTANTS = (
    "NAME",
    "CONTACT_LEFT",
    "CONTACT_RIGHT",
    "EDUCATION",
    "LANGUAGES",
    "EMPLOYERS",
)

MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_RANGE_SEP = re.compile(r"\s+[\u2013\u2014-]\s+")


# --- reading ------------------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConversionError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConversionError(f"{path}: cannot be read as UTF-8 text ({exc})") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _optional(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return _read_text(path)


def _read_json(path: Path | None) -> Any:
    text = _optional(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ConversionError(f"{path}: not valid JSON ({exc})") from None


def parse_assemble(source: str, origin: str = "assemble.py") -> dict[str, Any]:
    """The literal constants of assemble.py, read from its syntax tree: the file is never run.
    Assignments may come in any order, among comments and other code; the last one wins, as it
    would at run time. Raises ConversionError when one is missing or not a literal."""
    try:
        tree = ast.parse(source, filename=origin)
    except SyntaxError as exc:
        raise ConversionError(
            f"{origin}: not valid Python ({exc.msg}, line {exc.lineno})"
        ) from None
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in ASSEMBLE_CONSTANTS:
                try:
                    found[target.id] = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    raise ConversionError(
                        f"{origin}: {target.id} is not a literal value (line {node.lineno})"
                    ) from None
    missing = [name for name in ASSEMBLE_CONSTANTS if name not in found]
    if missing:
        raise ConversionError(f"{origin}: missing {', '.join(missing)}")
    return found


# --- facts.md -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    title: str
    body: str


_HEADING_1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
_HEADING_2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
INTRO_TITLE = "Introduction"
EMPTY_BODY = "(empty)"


def split_blocks(markdown: str) -> list[Block]:
    """The fact base split on `##` headings (fenced code ignored). The text before the first one
    is a block titled with the `#` heading when there is one. Bodies are verbatim but for the
    surrounding blank lines; a duplicate title gets " (2)", " (3)" so every block can be cited."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING_2.match(line)
        if heading:
            blocks.append((heading.group(1), []))
        else:
            blocks[-1][1].append(line)
    title, intro = blocks[0]
    result: list[Block] = []
    for index, line in enumerate(intro):
        match = _HEADING_1.match(line)
        if match and not any(previous.strip() for previous in intro[:index]):
            title = match.group(1)
            intro = intro[index + 1 :]
            break
    body = "\n".join(intro).strip("\n")
    if body.strip():
        result.append(Block(title or INTRO_TITLE, body))
    for title, body_lines in blocks[1:]:
        body = "\n".join(body_lines).strip("\n")
        result.append(Block(title or INTRO_TITLE, body if body.strip() else EMPTY_BODY))
    seen: dict[str, int] = {}
    unique = []
    for block in result:
        key = block.title.casefold()
        seen[key] = seen.get(key, 0) + 1
        title = block.title if seen[key] == 1 else f"{block.title} ({seen[key]})"
        unique.append(Block(title, block.body))
    return unique


def _block(blocks: list[Block], prefix: str) -> str | None:
    for block in blocks:
        if block.title.upper().startswith(prefix):
            return block.body
    return None


def _unbold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*", lambda m: m.group(1) or m.group(2), text).strip()


def _table_rows(body: str) -> list[list[str]]:
    rows = []
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [_unbold(cell) for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows[1:]  # without the header


def _bullets(body: str) -> dict[str, str]:
    """`- **Label:** text` bullets (text continued on the following indented lines), by label."""
    found: dict[str, str] = {}
    label: str | None = None
    for line in body.split("\n"):
        start = re.match(r"^-\s+\*\*(.+?):?\*\*:?\s*(.*)$", line)
        if start:
            label = start.group(1).rstrip(":").strip()
            found[label] = start.group(2).strip()
        elif label is not None and line.startswith((" ", "\t")) and line.strip():
            found[label] = f"{found[label]} {line.strip()}".strip()
        else:
            label = None
    return found


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


def _bullet(bullets: dict[str, str], prefix: str) -> str | None:
    for label, text in bullets.items():
        if label.casefold().startswith(prefix.casefold()) and text:
            return text
    return None


COUNTRY_NAMES = {"UK": "United Kingdom", "US": "United States", "EU": "European Union"}


def _work_authorization(text: str) -> list[dict[str, Any]]:
    """The citizenship bullet: what it says before "For ..." holds for the home country (and the
    EU when it names it); each later clause tagged "(Country)" is that country's answer."""
    head, marker, tail = text.partition("For ")
    head = head.strip()
    entries: list[dict[str, Any]] = []
    if head:
        home_free = bool(re.search(r"\bno (visa )?sponsorship\b", head, re.IGNORECASE))
        needs = False if home_free else None
        country = "France" if "France" in head else "Home country"
        entries.append({"country": country, "answer": head, "needs_sponsorship": needs})
        if re.search(r"\b(EU|European Union)\b", head):
            entries.append(
                {"country": "European Union", "answer": head, "needs_sponsorship": needs}
            )
    if marker:
        clauses = tail.partition(":")[2] or tail
        for match in re.finditer(r"([^()]+?)\s*\(([^()]+)\)", clauses):
            answer = match.group(1).strip(" ,;.")
            country = COUNTRY_NAMES.get(match.group(2).strip(), match.group(2).strip())
            needs = bool(re.search(r"sponsorship would be needed|permit needed", answer, re.I))
            entries.append(
                {
                    "country": country,
                    "answer": _capitalised(f"{answer}."),
                    "needs_sponsorship": needs or None,
                }
            )
    return entries


def _capitalised(text: str) -> str:
    """A clause cut out of a sentence, as a sentence of its own."""
    return text[:1].upper() + text[1:]


def _answers(practicalities: str | None) -> dict[str, Any]:
    if practicalities is None:
        return {}
    bullets = _bullets(practicalities)
    answers: dict[str, Any] = {}
    authorization = _bullet(bullets, "Citizenship")
    if authorization:
        answers["work_authorization"] = _work_authorization(authorization)
    notice = _bullet(bullets, "Notice")
    if notice:
        sentences = _sentences(notice)
        answers["notice_period"] = _capitalised(sentences[0])
        for sentence in sentences[1:]:
            if sentence.casefold().startswith("earliest start"):
                answers["earliest_start"] = sentence.partition(": ")[2].rstrip(".") or sentence
        travel = [s for s in sentences if "travel" in s.casefold()]
        if travel:
            answers["travel"] = " ".join(travel)
    for field, prefix in (
        ("relocation", "Location stance"),
        ("salary_policy", "Salary"),
        ("motivation", "Motivation"),
        ("heard_about", "How did you hear"),
    ):
        text = _bullet(bullets, prefix)
        if text:
            answers[field] = _capitalised(text)
    for label, text in bullets.items():
        if label.casefold().startswith(("demographic", "eeo")):
            answers["leave_blank"] = [f"{label}: {text}"]
    return answers


# --- assemble.py --------------------------------------------------------------------------------


def _month(text: str, origin: str) -> str | None:
    """ "September 2025" -> "2025-09", "Present" -> None."""
    words = text.strip().split()
    if len(words) == 1 and words[0].casefold() == "present":
        return None
    if len(words) == 2 and words[0].casefold() in MONTHS and re.fullmatch(r"\d{4}", words[1]):
        return f"{words[1]}-{MONTHS[words[0].casefold()]:02d}"
    raise ConversionError(f"{origin}: cannot read the month {text!r}")


def _month_range(text: str, origin: str) -> tuple[str | None, str | None]:
    parts = _RANGE_SEP.split(text.strip())
    if len(parts) != 2:
        raise ConversionError(f"{origin}: cannot read the dates {text!r}")
    return _month(parts[0], origin), _month(parts[1], origin)


def _contract(title: str) -> str | None:
    low = title.casefold()
    if "apprentice" in low or "alternance" in low:
        return "apprenticeship"
    if "self-employed" in low or "auto-entrepreneur" in low or "freelance" in low:
        return "freelance"
    if "intern" in low or "stage" in low:
        return "internship"
    return None


def _employers(constants: dict[str, Any], employment: str | None) -> list[dict[str, Any]]:
    table: dict[str, list[str]] = {}
    for row in _table_rows(employment or ""):
        if row and row[0]:
            table.setdefault(row[0].casefold(), row)
    employers = []
    for key, value in constants["EMPLOYERS"].items():
        organisation, dates = value
        start, end = _month_range(dates, f"assemble.py EMPLOYERS[{key!r}]")
        if start is None:
            raise ConversionError(f"assemble.py EMPLOYERS[{key!r}]: the start cannot be Present")
        row = table.get(organisation.casefold())
        if row is None or len(row) < 2 or not row[1]:
            raise ConversionError(
                f"facts.md EMPLOYMENT: no row with a role for {organisation!r} (employer {key!r})"
            )
        employers.append(
            {
                "key": key,
                "organisation": organisation,
                "title": row[1],
                "start": start,
                "end": end,
                "location": row[3] if len(row) > 3 and row[3] else None,
                "contract": _contract(row[1]),
            }
        )
    return employers


_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _education(constants: dict[str, Any], education: str | None) -> list[dict[str, Any]]:
    """Each (degree, description) of EDUCATION. The description is "School <years>. Details" or
    "School. Details": the school is what precedes the years or the first ". ", the rest is kept
    word for word in `details`, so `school + " " or ". " + details` gives the original back.
    Months and the city come from the matching line of facts.md EDUCATION when it has them."""
    lines: list[str] = []  # one per bullet, continuation lines joined
    for line in (education or "").split("\n"):
        if line.startswith("- "):
            lines.append(line)
        elif lines and line.startswith((" ", "\t")) and line.strip():
            lines[-1] = f"{lines[-1]} {line.strip()}"
    schools = []
    for degree, description in constants["EDUCATION"]:
        year = _YEAR.search(description)
        stop = description.find(". ")
        if year and (stop == -1 or year.start() < stop):
            school, details = description[: year.start()].strip(), description[year.start() :]
        elif stop != -1:
            school, details = description[:stop].strip(), description[stop + 2 :]
        else:
            school, details = description.strip(), ""
        start = end = location = None
        for line in lines:
            if school.casefold() not in _unbold(line).casefold():
                continue
            months = re.search(
                r"\*\*([A-Z][a-z]+ \d{4} [\u2013\u2014-] [A-Z][a-z]+ \d{4})\*\*", line
            )
            if months:
                start, end = _month_range(months.group(1), "facts.md EDUCATION")
            city = re.match(r"^-\s+\*\*[^*]+\*\*,\s*([^,\u2014\u2013*]+?)\s+[\u2014\u2013]", line)
            if city:
                location = city.group(1).strip()
            break
        schools.append(
            {
                "school": school,
                "degree": degree,
                "start": start,
                "end": end,
                "location": location,
                "details": details.strip() or None,
            }
        )
    return schools


def _identity(constants: dict[str, Any], identity: str | None) -> dict[str, Any]:
    name = constants["NAME"]
    headline = None
    if identity:
        bold = re.search(r"^-\s+\*\*(.+?)\*\*", identity, re.MULTILINE)
        if bold and bold.group(1).casefold() == name.casefold():
            name = bold.group(1)  # the proper case; assemble.py writes it in capitals
        title = re.search(r"Current title:\s*(.+)$", identity, re.MULTILINE)
        if title:
            headline = _unbold(title.group(1))
    result: dict[str, Any] = {"full_name": name, "headline": headline, "links": []}
    for url, label in constants["CONTACT_LEFT"]:
        if "linkedin.com" in url and "linkedin_url" not in result:
            result["linkedin_url"] = url
        elif "website" not in result and "github.com" not in url:
            result["website"] = url
        else:
            result["links"].append({"label": label, "url": url})
    for item in constants["CONTACT_RIGHT"]:
        if "@" in item:
            result["email"] = item
        elif re.fullmatch(r"\+?[0-9][0-9 ().-]{4,38}", item):
            result["phone"] = item
        elif "," in item:
            city, _, country = item.rpartition(",")
            result["city"], result["country"] = city.strip(), country.strip()
        else:
            result["city"] = item
    result["languages"] = [{"name": n, "level": level} for n, level in constants["LANGUAGES"]]
    return result


# --- skills.json --------------------------------------------------------------------------------


def _word_edges(term: str) -> bool:
    return bool(re.match(r"\w", term) and re.search(r"\w$", term))


def wrap_skill_term(term: str) -> str:
    """A skills term as the advanced pattern that matches exactly what the engine matched."""
    if "\\" in term:
        return rf"(?<!\w)(?:{term}){PLURAL}(?!\w)"
    if _word_edges(term):
        return rf"\b{escape(term)}{PLURAL}\b"
    return rf"(?<!\w){escape(term)}{PLURAL}(?!\w)"


def _category(name: str, terms: list[str]) -> dict[str, Any]:
    if any("\\" in term for term in terms):
        return {"name": name, "terms": [wrap_skill_term(t) for t in terms], "advanced_regex": True}
    return {"name": name, "terms": list(terms), "advanced_regex": False}


def _skills(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConversionError("skills.json: expected an object")
    skills: dict[str, Any] = {}
    if "years_experience" in data:
        skills["years_experience"] = float(data["years_experience"])
    for level in ("have", "adjacent", "none"):
        skills[level] = [_category(name, terms) for name, terms in data.get(level, {}).items()]
    skills["gates"] = [
        {"name": name, "terms": [regex], "advanced_regex": True, "penalty": GATE_PENALTY}
        for name, regex in data.get("gates", {}).items()
    ]
    return skills


# --- boards.json --------------------------------------------------------------------------------


def split_alternatives(pattern: str) -> list[str]:
    """The top-level alternatives of a regex: `|` outside groups and character classes, escapes
    respected. "a|b(c|d)|[|]" gives ["a", "b(c|d)", "[|]"]."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":
                i += 1
            while i < len(pattern) and pattern[i] != "]":
                i += 2 if pattern[i] == "\\" else 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(pattern[start:i])
            start = i + 1
        i += 1
    parts.append(pattern[start:])
    return parts


def _groups(pattern: str) -> list[tuple[int, int]]:
    """(start, end) of every parenthesised group, end exclusive, ignoring escapes and classes."""
    spans, stack, i = [], [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":
                i += 1
            while i < len(pattern) and pattern[i] != "]":
                i += 2 if pattern[i] == "\\" else 1
        elif char == "(":
            stack.append(i)
        elif char == ")" and stack:
            spans.append((stack.pop(), i + 1))
        i += 1
    return spans


def expand_alternative(alternative: str) -> list[str]:
    """`prefix(a|b)suffix` as ["prefix" "a" "suffix", "prefix" "b" "suffix"] when the group is the
    only one, plain or `(?:`, not quantified, and nothing refers back to it; else unchanged.
    A regex search finds `prefix(a|b)suffix` exactly when it finds one of the expansions."""
    spans = _groups(alternative)
    if len(spans) != 1 or re.search(r"\\[1-9]|\(\?P=", alternative):
        return [alternative]
    start, end = spans[0]
    inner = alternative[start + 1 : end - 1]
    if inner.startswith("?:"):
        inner = inner[2:]
    elif inner.startswith("?"):
        return [alternative]  # lookaround, flags or a named group
    if re.match(r"[?*+{]", alternative[end:]):
        return [alternative]
    choices = split_alternatives(inner)
    if len(choices) < 2:
        return [alternative]
    return [alternative[:start] + choice + alternative[end:] for choice in choices]


def regex_terms(pattern: str) -> list[str]:
    """One advanced term per alternative of `pattern`, expanded where possible."""
    return [term for part in split_alternatives(pattern) for term in expand_alternative(part)]


def _term_list(pattern: str | None) -> dict[str, Any]:
    if not pattern:
        return {"terms": [], "advanced_regex": False}
    return {"terms": regex_terms(pattern), "advanced_regex": True}


def _search(data: Any) -> dict[str, Any]:
    search: dict[str, Any] = {
        "contract_types": list(CONTRACT_TYPES),
        "remote_stance": REMOTE_STANCE,
        "min_fit": MIN_FIT,
        "days": DAYS,
        "never_apply": list(NEVER_APPLY),
    }
    if data is None:
        return search
    if not isinstance(data, dict):
        raise ConversionError("boards.json: expected an object")
    search["title_include"] = _term_list(data.get("title_re"))
    search["title_exclude"] = _term_list(data.get("exclude_re"))
    search["location"] = _term_list(data.get("location_re"))
    for name in ("min_fit", "days"):
        if isinstance(data.get(name), int):
            search[name] = data[name]
    return search


# --- photo --------------------------------------------------------------------------------------


def _photo(text: str | None, origin: str) -> dict[str, str] | None:
    if text is None:
        return None
    encoded = text.strip()
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    try:
        data = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        raise ConversionError(f"{origin}: not valid base64") from None
    if sniff_photo(data) is None:
        raise ConversionError(f"{origin}: the photo is not a PNG or JPEG image")
    # Re-encoded as the app stores it (metadata stripped, at most 1024 px), so the export file
    # carries no EXIF or XMP and stays well under the import size limit.
    try:
        photo = normalize(data)
    except PhotoError as exc:
        raise ConversionError(f"{origin}: {exc.message}") from None
    return {"mime": photo.mime, "base64": base64.b64encode(photo.data).decode("ascii")}


# --- the conversion -----------------------------------------------------------------------------


def convert(paths: SourcePaths, now: datetime | None = None) -> ExportDocument:
    """The export document of the engine's files. Raises ConversionError on a missing required
    file or an unknown shape, and pydantic.ValidationError when the result breaks a section rule
    (a regex over 500 characters, a bad email): fix the source, never the check."""
    constants = parse_assemble(_read_text(paths.assemble), str(paths.assemble))
    blocks = split_blocks(_read_text(paths.facts))
    sections = {
        "identity": _identity(constants, _block(blocks, "IDENTITY")),
        "experience": {
            "employers": _employers(constants, _block(blocks, "EMPLOYMENT")),
            "education": _education(constants, _block(blocks, "EDUCATION")),
        },
        "facts": {"blocks": [{"title": b.title, "body": b.body} for b in blocks]},
        "skills": _skills(_read_json(paths.skills)),
        "search": _search(_read_json(paths.boards)),
        "answers": _answers(_block(blocks, "PRACTICALITIES")),
    }
    photo = _photo(_optional(paths.photo), str(paths.photo))
    # Validated as a whole document, so an error names its full path (sections.search...).
    return ExportDocument.model_validate(
        {"exported_at": now or datetime.now(UTC), "sections": sections, "photo": photo}
    )
