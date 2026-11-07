/**
 * A template's style.css as the renderer prints it (templates/__init__.py, build_html), scoped to
 * the page drawn here: `html`, `body` (and its class) and `:root` are its root, `@page` its
 * paper's padding, print lengths scale with the paper's width, `data-sheet-ui` stays out of it.
 */
import type { MirrorStep } from "./template-mirror";

/** One CSS pixel of the A4 page, 793.7 px wide, as a share of the paper's width. */
const PAGE_PX = "calc(100cqw / 793.7)";
/** The smallest body text on a narrow screen, where the page is narrower than a printed one. */
const MIN_BODY = "9px";
/** What the renderer appends after every template (template_css.ENFORCED_CSS). */
const ENFORCED =
  "*,*::before,*::after{overflow-wrap:anywhere!important;word-break:normal!important}";

const UNITS: Record<string, string> = {
  pt: "--tpl-pt",
  mm: "--tpl-mm",
  px: "--tpl-px",
  cm: "--tpl-cm",
  in: "--tpl-in",
};
const LENGTH = /(?<![\w.#-])(-?(?:\d+\.?\d*|\.\d+))(pt|mm|px|cm|in)(?![\w-])/g;
const ROOT = /(^|[\s>+~(])(?:html|body|:root)(?=$|[\s>+~).:#[])/g;
/** `body`'s class (the kind) opening a selector: `.letter .side`. */
const KIND = /^(\.(?:cv|letter))(?=$|[\s>+~.:#[])/;
const RULE = /([^{}]+)\{([^{}]*)\}/g;

/** A length of the printed page, scaled to the paper: `12pt` -> `calc(12 * var(--tpl-pt))`. */
export function scaled(value: string): string {
  return value.replace(
    LENGTH,
    (_, number: string, unit: string) =>
      `calc(${number} * var(${UNITS[unit] ?? "--tpl-px"}))`,
  );
}

/** A block's declarations, split at the semicolons outside a string (`content: ";"`). */
function declarations(block: string): [string, string][] {
  const parts: string[] = [];
  let quote = "";
  let start = 0;
  for (let at = 0; at < block.length; at += 1) {
    const char = block[at];
    if (quote !== "") {
      if (char === "\\") at += 1;
      else if (char === quote) quote = "";
    } else if (char === '"' || char === "'") quote = char;
    else if (char === ";") {
      parts.push(block.slice(start, at));
      start = at + 1;
    }
  }
  parts.push(block.slice(start));
  return parts
    .map((one) => one.trim())
    .filter((one) => one.includes(":"))
    .map((one) => {
      const at = one.indexOf(":");
      return [one.slice(0, at).trim().toLowerCase(), one.slice(at + 1).trim()];
    });
}

export interface PageMargins {
  top: string;
  right: string;
  bottom: string;
  left: string;
}

const SIDES = ["top", "right", "bottom", "left"] as const;

/** `margin: a b c d` and its longhands, onto `margins`. */
function applyMargins(
  margins: PageMargins,
  block: string,
  only?: "top",
): PageMargins {
  const out = { ...margins };
  for (const [property, value] of declarations(block)) {
    if (property === "margin") {
      const parts = value.split(/\s+/);
      const [a, b = a, c = a, d = b] = parts;
      const all = [a, b, c, d];
      SIDES.forEach((side, index) => {
        const one = all[index];
        if (one !== undefined && (only === undefined || side === only))
          out[side] = scaled(one);
      });
    } else if (property.startsWith("margin-")) {
      const side = property.slice("margin-".length);
      if (
        (SIDES as readonly string[]).includes(side) &&
        (only === undefined || side === only)
      )
        out[side as (typeof SIDES)[number]] = scaled(value);
    }
  }
  return out;
}

function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * The paper's padding: the step's margin (`var(--margin)`), then the template's `@page`; the top
 * of page 1 from `@page :first` (a band that runs to the top edge).
 */
export function pageMargins(css: string): PageMargins {
  let margins: PageMargins = {
    top: "var(--margin)",
    right: "var(--margin)",
    bottom: "var(--margin)",
    left: "var(--margin)",
  };
  for (const match of withoutComments(css).matchAll(RULE)) {
    const selector = (match[1] ?? "").trim();
    if (selector === "@page") margins = applyMargins(margins, match[2] ?? "");
    else if (/^@page\s*:first$/.test(selector))
      margins = applyMargins(margins, match[2] ?? "", "top");
  }
  return margins;
}

function rule(selectors: string, block: string): string {
  const scope = selectors
    .split(",")
    .map((one) =>
      one.trim().replace(ROOT, "$1:scope").replace(KIND, ":scope$1"),
    )
    .join(", ");
  const body = declarations(block)
    .map(([property, value]) =>
      property === "position" && value === "fixed"
        ? "position: absolute"
        : property === "content"
          ? `${property}: ${value}`
          : `${property}: ${scaled(value)}`,
    )
    .join("; ");
  return `${scope} { ${body} }`;
}

/** The style of one template's page, for a `<style>` beside it; `slug` names the page's root. */
export function templateCss(slug: string, css: string): string {
  const rules: string[] = [];
  for (const match of withoutComments(css).matchAll(RULE)) {
    const selectors = (match[1] ?? "").trim();
    if (selectors.startsWith("@")) continue;
    rules.push(rule(selectors, match[2] ?? ""));
  }
  const root = `[data-template-sheet="${slug.replace(/[^a-z0-9-]/g, "")}"]`;
  return [
    "@property --tpl-px { syntax: '<length>'; inherits: true; initial-value: 1px; }",
    `@scope (${root}) to ([data-sheet-ui]) {`,
    // The renderer's page has no app style: every element starts from the browser's own.
    ":scope, :where(*), :where(*)::before, :where(*)::after { all: revert; }",
    ":scope { color: #000000; background: transparent; }",
    ...rules,
    ENFORCED,
    ".sheet-empty { color: #8a929c; font-style: italic; font-weight: 400; text-transform: none; letter-spacing: normal; }",
    "}",
  ].join("\n");
}

/** The custom properties of the paper: the scale, the step's density, the accent. */
export function pageProperties(
  step: MirrorStep,
  accent: string,
): Record<string, string> {
  return {
    "--tpl-px": PAGE_PX,
    "--tpl-pt": "calc(var(--tpl-px) * 4 / 3)",
    "--tpl-mm": "calc(var(--tpl-px) * 96 / 25.4)",
    "--tpl-cm": "calc(var(--tpl-px) * 960 / 25.4)",
    "--tpl-in": "calc(var(--tpl-px) * 96)",
    "--body-size": `max(calc(${String(step.body_size)} * var(--tpl-pt)), ${MIN_BODY})`,
    "--line-height": String(step.line_height),
    "--section-gap": `calc(${String(step.section_gap)} * var(--tpl-pt))`,
    "--item-gap": `calc(${String(step.item_gap)} * var(--tpl-pt))`,
    "--margin": `calc(${String(step.margin)} * var(--tpl-mm))`,
    "--accent": accent,
  };
}

const styles = new Map<string, { css: string; margins: PageMargins }>();

/** A template's scoped style and paper margins, worked out once per template. */
export function templateStyle(
  slug: string,
  css: string,
): { css: string; margins: PageMargins } {
  const known = styles.get(slug);
  if (known !== undefined) return known;
  const made = { css: templateCss(slug, css), margins: pageMargins(css) };
  styles.set(slug, made);
  return made;
}
