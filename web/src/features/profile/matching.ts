/**
 * How a term list matches text, ported from api/app/profile/matching.py so the Skills "Try it" box
 * shows in the browser exactly what search and scoring will decide on the server, and the regex
 * checks of api/app/profile/schemas.py so a bad pattern is caught before a save.
 *
 * A term list matches a text when any of its terms does, case-insensitive:
 * - plain term: the words as written, as a whole word, with an optional plural "s" or "es";
 * - advanced term: a Python regular expression searched anywhere in the text.
 *
 * Python and JavaScript regular expressions differ, so advanced terms go through `pyToJs`, which
 * rewrites the Python meaning into JavaScript: `\w`, `\d` and `\b` are Unicode aware as in Python
 * (JavaScript's are ASCII only), `.` and `$` keep Python's newline rules, `\A`, `\Z`, `(?P<name>)`,
 * `{,n}` and Python's lenient escapes are translated. A construct JavaScript cannot express
 * (possessive quantifiers, atomic groups, inline flags, comments, conditionals, octal or named
 * escapes) gives null: the browser then neither previews nor judges the term, and the server
 * decides on save. Parity is tested on the strings of
 * api/tests/profile/fixtures/matching_parity.json, which the Python tests check too.
 */

export const TERM_MAX = 200;
export const REGEX_MAX = 500;

const WORD = "\\p{L}\\p{N}_";
const W = `[${WORD}]`;
const NOT_W = `[^${WORD}]`;
const BOUNDARY = `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`;
const NOT_BOUNDARY = `(?:(?<=${W})(?=${W})|(?<!${W})(?!${W}))`;
const START = "(?<![\\s\\S])";
const END = "(?![\\s\\S])";
const PLURAL = "(?:s|es)?";
// What a JavaScript Unicode pattern accepts after a backslash outside a class.
const JS_SYNTAX = new Set("^$\\.*+?()[]{}|/");
const QUANTIFIER = /^\{(\d*)(,?)(\d*)\}/;

/** `text` as a pattern that matches it literally, escaping what Python's `escape` escapes. */
export function escapeTerm(text: string): string {
  return text.replace(/[.^$*+?{}[\]\\|()]/g, "\\$&");
}

/** The pattern a plain term stands for: whole word, optional plural (JavaScript, flags "iu"). */
export function plainSource(terms: readonly string[]): string {
  return `(?<!${W})(?:${terms.map(escapeTerm).join("|")})${PLURAL}(?!${W})`;
}

const isHex = (text: string) => /^[0-9a-fA-F]+$/.test(text);

/**
 * The JavaScript source (for flags "iu") of a Python pattern, or null when it uses a construct
 * the browser cannot reproduce. The result may still be invalid: compiling it then fails as
 * Python would (an unclosed group, nothing to repeat).
 */
export function pyToJs(pattern: string): string | null {
  let out = "";
  let i = 0;
  const n = pattern.length;
  while (i < n) {
    const c = pattern.charAt(i);
    if (c === "\\") {
      const escaped = escapeOutside(pattern, i);
      if (escaped === null) return null;
      out += escaped.text;
      i += escaped.length;
      continue;
    }
    if (c === "[") {
      const set = characterClass(pattern, i);
      if (set === null) return null;
      out += set.text;
      i += set.length;
      continue;
    }
    if (c === "(") {
      const group = groupOpen(pattern, i);
      if (group === null) return null;
      out += group.text;
      i += group.length;
      continue;
    }
    if (c === "{") {
      const q = QUANTIFIER.exec(pattern.slice(i));
      if (q && (q[1] !== "" || q[2] === ",")) {
        const min = q[1] === "" ? "0" : q[1];
        out += q[2] === "," ? `{${min},${q[3] ?? ""}}` : `{${min}}`;
        i += q[0].length;
        if (pattern.charAt(i) === "+") return null; // possessive
        continue;
      }
      out += "\\{";
      i += 1;
      continue;
    }
    if (c === "*" || c === "+" || c === "?") {
      out += c;
      i += 1;
      if (pattern.charAt(i) === "+") return null; // possessive
      continue;
    }
    if (c === "}" || c === "]") {
      out += `\\${c}`;
    } else if (c === ".") {
      out += "[^\\n]";
    } else if (c === "$") {
      out += `(?=\\n?${END})`;
    } else if (c === "^") {
      out += START;
    } else {
      out += c;
    }
    i += 1;
  }
  return out;
}

interface Piece {
  text: string;
  length: number;
}

/** A backslash escape outside a class, at `pattern[i]`. */
function escapeOutside(pattern: string, i: number): Piece | null {
  const next = pattern.charAt(i + 1);
  if (next === "") return { text: "\\", length: 1 }; // fails to compile, as in Python
  switch (next) {
    case "w":
      return { text: W, length: 2 };
    case "W":
      return { text: NOT_W, length: 2 };
    case "d":
      return { text: "\\p{Nd}", length: 2 };
    case "D":
      return { text: "\\P{Nd}", length: 2 };
    case "b":
      return { text: BOUNDARY, length: 2 };
    case "B":
      return { text: NOT_BOUNDARY, length: 2 };
    case "A":
      return { text: START, length: 2 };
    case "Z":
      return { text: END, length: 2 };
    default:
      return commonEscape(pattern, i, false);
  }
}

/** Escapes with the same meaning inside and outside a class. */
function commonEscape(
  pattern: string,
  i: number,
  inClass: boolean,
): Piece | null {
  const next = pattern.charAt(i + 1);
  if (next === "a") return { text: "\\x07", length: 2 };
  if ("fnrtvsS".includes(next)) return { text: `\\${next}`, length: 2 };
  if (next === "x") {
    const hex = pattern.slice(i + 2, i + 4);
    return isHex(hex) && hex.length === 2
      ? { text: `\\x${hex}`, length: 4 }
      : { text: "\\x", length: 2 }; // invalid in both
  }
  if (next === "u") {
    const hex = pattern.slice(i + 2, i + 6);
    return isHex(hex) && hex.length === 4
      ? { text: `\\u${hex}`, length: 6 }
      : { text: "\\u", length: 2 };
  }
  if (next === "U") {
    const hex = pattern.slice(i + 2, i + 10);
    if (!isHex(hex) || hex.length !== 8) return { text: "\\U", length: 2 };
    const code = Number.parseInt(hex, 16);
    if (code > 0x10ffff) return { text: "\\U", length: 2 };
    return { text: `\\u{${code.toString(16)}}`, length: 10 };
  }
  if (next === "N" || next === "0") return null; // named and octal escapes
  if (/[1-9]/.test(next)) {
    if (inClass) return null; // octal inside a class
    const digits = /^\d{1,2}/.exec(pattern.slice(i + 1))?.[0] ?? next;
    return { text: `\\${digits}`, length: 1 + digits.length };
  }
  if (/[A-Za-z]/.test(next)) return { text: `\\${next}`, length: 2 }; // bad escape in both
  // Python lets any other character be escaped: keep it literal.
  if (inClass) {
    return {
      text: "\\]^-[".includes(next) ? `\\${next}` : next,
      length: 2,
    };
  }
  return { text: JS_SYNTAX.has(next) ? `\\${next}` : next, length: 2 };
}

/** A character class at `pattern[i] === "["`, up to its closing bracket. */
function characterClass(pattern: string, i: number): Piece | null {
  let out = "[";
  let j = i + 1;
  if (pattern.charAt(j) === "^") {
    out += "^";
    j += 1;
  }
  if (pattern.charAt(j) === "]") {
    out += "\\]";
    j += 1;
  }
  while (j < pattern.length && pattern.charAt(j) !== "]") {
    const c = pattern.charAt(j);
    if (c === "\\") {
      const next = pattern.charAt(j + 1);
      let piece: Piece | null;
      if (next === "w") piece = { text: WORD, length: 2 };
      else if (next === "d") piece = { text: "\\p{Nd}", length: 2 };
      else if (next === "D") piece = { text: "\\P{Nd}", length: 2 };
      else if (next === "b") piece = { text: "\\x08", length: 2 };
      else if (next === "W") return null;
      else if (next === "") piece = { text: "\\", length: 1 };
      else piece = commonEscape(pattern, j, true);
      if (piece === null) return null;
      out += piece.text;
      j += piece.length;
      continue;
    }
    out += c;
    j += 1;
  }
  if (j >= pattern.length) return { text: out, length: j - i }; // unterminated, fails in both
  return { text: `${out}]`, length: j - i + 1 };
}

/** A group opening at `pattern[i] === "("`. */
function groupOpen(pattern: string, i: number): Piece | null {
  if (pattern.charAt(i + 1) !== "?") return { text: "(", length: 1 };
  const rest = pattern.slice(i + 2);
  if (rest.startsWith(":")) return { text: "(?:", length: 3 };
  for (const look of ["=", "!", "<=", "<!"]) {
    if (rest.startsWith(look))
      return { text: `(?${look}`, length: 2 + look.length };
  }
  const named = /^P<([^>]*)>/.exec(rest);
  if (named)
    return { text: `(?<${named[1] ?? ""}>`, length: 2 + named[0].length };
  const reference = /^P=([^)]*)\)/.exec(rest);
  if (reference) {
    return {
      text: `\\k<${reference[1] ?? ""}>`,
      length: 2 + reference[0].length,
    };
  }
  // The whole list is already case-insensitive, so a scoped (?i:...) is a plain group.
  if (rest.startsWith("i:")) return { text: "(?:", length: 4 };
  return null; // inline flags, comments, atomic groups, conditionals, (?<name>)
}

/** The case-insensitive pattern of a term list, as matching.py `compile_terms` builds it. */
export type Compiled =
  | { kind: "empty" }
  | { kind: "ok"; regex: RegExp }
  | { kind: "unsupported" } // a Python-only construct: the server decides
  | { kind: "invalid"; message: string };

export function compileTerms(
  terms: readonly string[],
  advancedRegex: boolean,
): Compiled {
  if (terms.length === 0) return { kind: "empty" };
  let source: string;
  if (advancedRegex) {
    const parts: string[] = [];
    for (const term of terms) {
      const translated = pyToJs(term);
      if (translated === null) return { kind: "unsupported" };
      parts.push(`(?:${translated})`);
    }
    source = parts.join("|");
  } else {
    source = plainSource(terms);
  }
  try {
    return { kind: "ok", regex: new RegExp(source, "giu") };
  } catch (error) {
    return { kind: "invalid", message: jsRegexMessage(error) };
  }
}

/** True when the list matches the text; undefined when the browser cannot tell. */
export function matches(
  terms: readonly string[],
  advancedRegex: boolean,
  text: string,
): boolean | undefined {
  const compiled = compileTerms(terms, advancedRegex);
  if (compiled.kind === "empty") return false;
  if (compiled.kind !== "ok") return undefined;
  compiled.regex.lastIndex = 0;
  return compiled.regex.test(text);
}

/** Every match in the text, in order (what Python's `findall` counts for a category). */
export function findMatches(regex: RegExp, text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(regex)) {
    found.push(match[0]);
    if (found.length >= 1000) break;
  }
  return found;
}

function jsRegexMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  // "Invalid regular expression: /(a/giu: Unterminated group" -> "unterminated group"
  const reason = message.split(": ").pop() ?? message;
  return reason.charAt(0).toLowerCase() + reason.slice(1);
}

// --- checks before a save, mirroring schemas.py ------------------------------------------------

const REPEAT = /[+*]|\{\d*,?\d*\}/y;

function repeatAt(pattern: string, index: number): string | undefined {
  REPEAT.lastIndex = index;
  return REPEAT.exec(pattern)?.[0];
}

/**
 * True when a repeated group contains a repetition itself, at any depth: (a+)+, (\w+\s?)*,
 * (?:x{2,})+. Escapes and character classes are skipped. A line by line port of
 * schemas.py `has_nested_quantifier`.
 */
export function hasNestedQuantifier(pattern: string): boolean {
  const stack: boolean[] = [];
  let i = 0;
  while (i < pattern.length) {
    const char = pattern.charAt(i);
    if (char === "\\") {
      i += 2;
      continue;
    }
    if (char === "[") {
      i += 1;
      if (pattern.charAt(i) === "^") i += 1;
      if (pattern.charAt(i) === "]") i += 1;
      while (i < pattern.length && pattern.charAt(i) !== "]") {
        i += pattern.charAt(i) === "\\" ? 2 : 1;
      }
      i += 1;
      continue;
    }
    if (char === "(") {
      stack.push(false);
      i += 1;
      continue;
    }
    if (char === ")" && stack.length > 0) {
      const inner = stack.pop() ?? false;
      const quantifier = repeatAt(pattern, i + 1);
      if (quantifier !== undefined && inner) return true;
      if (stack.length > 0) {
        stack[stack.length - 1] =
          (stack[stack.length - 1] ?? false) ||
          inner ||
          quantifier !== undefined;
      }
      i += 1 + (quantifier?.length ?? 0);
      continue;
    }
    if (stack.length > 0 && repeatAt(pattern, i) !== undefined) {
      stack[stack.length - 1] = true;
    }
    i += 1;
  }
  return false;
}

const GLOBAL_FLAGS = /\(\?[aiLmsux]+\)/y;

/**
 * The first global inline flag group such as (?i), (?m) or (?sx), outside escapes and character
 * classes, with its position, or undefined. Scoped groups such as (?i:...) are not global. A port
 * of schemas.py `global_flags`: the server wraps each advanced term in (?:...) and joins the list,
 * and Python accepts a global flag only at the start of the whole pattern.
 */
export function globalFlags(
  pattern: string,
): { index: number; flags: string } | undefined {
  let i = 0;
  while (i < pattern.length) {
    const char = pattern.charAt(i);
    if (char === "\\") {
      i += 2;
      continue;
    }
    if (char === "[") {
      i += 1;
      if (pattern.charAt(i) === "^") i += 1;
      if (pattern.charAt(i) === "]") i += 1;
      while (i < pattern.length && pattern.charAt(i) !== "]") {
        i += pattern.charAt(i) === "\\" ? 2 : 1;
      }
      i += 1;
      continue;
    }
    if (char === "(") {
      GLOBAL_FLAGS.lastIndex = i;
      const found = GLOBAL_FLAGS.exec(pattern);
      if (found) return { index: i, flags: found[0] };
    }
    i += 1;
  }
  return undefined;
}

/**
 * Why an advanced term would be refused on save, or undefined when the browser sees no problem
 * (the server stays the judge, and reports the exact position). Same order as `check_regex`:
 * length, global inline flags, compiling, nested quantifiers.
 */
export function regexProblem(pattern: string): string | undefined {
  if (pattern.length > REGEX_MAX) {
    return `Regular expression longer than ${REGEX_MAX} characters.`;
  }
  const flags = globalFlags(pattern);
  if (flags !== undefined) {
    return (
      `Inline flags such as ${flags.flags} are not allowed (position ${flags.index}). ` +
      "Matching is already case-insensitive; to apply a flag to part of the term, " +
      "scope it as a group such as (?s:...)."
    );
  }
  const translated = pyToJs(pattern);
  if (translated !== null) {
    try {
      new RegExp(translated, "u");
    } catch (error) {
      return `Invalid regular expression: ${jsRegexMessage(error)}.`;
    }
  }
  if (hasNestedQuantifier(pattern)) {
    return "Nested quantifiers such as (a+)+ are not allowed: they can make matching hang.";
  }
  return undefined;
}

// C0 and C1 control characters and DEL.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

/** Why a term would be refused, plain or advanced, or undefined. */
export function termProblem(
  term: string,
  advancedRegex: boolean,
): string | undefined {
  const value = term.trim();
  if (value === "") return "Enter a term.";
  if (CONTROL.test(value)) return "Remove the control characters.";
  if (advancedRegex) return regexProblem(value);
  if (value.length > TERM_MAX) {
    return `A plain term is at most ${TERM_MAX} characters.`;
  }
  return undefined;
}

/** Terms trimmed, NFC, empty ones dropped, duplicates (ignoring case) dropped, first kept. */
export function cleanTerms(terms: readonly string[]): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const raw of terms) {
    const term = raw.normalize("NFC").trim();
    const key = term.toLocaleLowerCase("en");
    if (term === "" || seen.has(key)) continue;
    seen.add(key);
    kept.push(term);
  }
  return kept;
}
