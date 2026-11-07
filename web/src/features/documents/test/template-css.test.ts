import { describe, expect, it } from "vitest";

import {
  pageMargins,
  pageProperties,
  scaled,
  templateCss,
} from "../helpers/template-css";
import { TEMPLATE_MIRROR } from "../helpers/template-mirror";

describe("a template's style, drawn in the browser", () => {
  it("scales the printed page's lengths to the paper and leaves the others", () => {
    expect(scaled("12pt")).toBe("calc(12 * var(--tpl-pt))");
    expect(scaled("0 -2mm 0.75px")).toBe(
      "0 calc(-2 * var(--tpl-mm)) calc(0.75 * var(--tpl-px))",
    );
    expect(scaled("2.5px solid #1d232b")).toBe(
      "calc(2.5 * var(--tpl-px)) solid #1d232b",
    );
    expect(scaled("1.05em 50% 1fr var(--item-gap)")).toBe(
      "1.05em 50% 1fr var(--item-gap)",
    );
  });

  it("pads the paper with the page's margins: the step's, the template's, the first page's top", () => {
    expect(pageMargins("@page { size: A4; }")).toEqual({
      top: "var(--margin)",
      right: "var(--margin)",
      bottom: "var(--margin)",
      left: "var(--margin)",
    });
    expect(pageMargins("@page { size: A4; margin: 0; }").left).toBe("0");
    expect(
      pageMargins(
        "@page { size: A4; margin-left: 0; margin-right: 0; }\n@page :first { margin-top: 0; }",
      ),
    ).toEqual({
      top: "0",
      right: "0",
      bottom: "var(--margin)",
      left: "0",
    });
  });

  it("scopes the rules to the page: html, body, its kind and :root are its root", () => {
    const css = templateCss(
      "sample",
      [
        "@page { size: A4; }",
        "html { font-size: var(--body-size); }",
        ":root { --rail: 27mm; }",
        "body.cv .x, .letter .side { padding: 0; }",
        ".letter-body p { margin-bottom: 4pt; }",
        ".frame { position: fixed; top: 6mm; }",
        '.languages li:not(:last-child)::after { content: ";"; color: #111111; }',
      ].join("\n"),
    );
    expect(css).toContain(
      '@scope ([data-template-sheet="sample"]) to ([data-sheet-ui])',
    );
    expect(css).toContain(":scope { font-size: var(--body-size) }");
    expect(css).toContain(":scope { --rail: calc(27 * var(--tpl-mm)) }");
    expect(css).toContain(":scope.cv .x, :scope.letter .side { padding: 0 }");
    expect(css).toContain(
      ".letter-body p { margin-bottom: calc(4 * var(--tpl-pt)) }",
    );
    expect(css).toContain(
      ".frame { position: absolute; top: calc(6 * var(--tpl-mm)) }",
    );
    expect(css).toContain('content: ";"; color: #111111');
    expect(css).not.toContain("@page");
  });

  it.each(Object.keys(TEMPLATE_MIRROR))(
    "%s: every printed length scales with the paper",
    (slug) => {
      const css = templateCss(slug, TEMPLATE_MIRROR[slug]?.css ?? "");
      const unscaled = css
        .replace(/calc\([^)]*\)/g, "")
        .replace(/"[^"]*"/g, "")
        .match(/\d(pt|mm|cm|in)\b/g);
      expect(unscaled).toBeNull();
      expect(css.match(/\{/g)?.length).toBe(css.match(/\}/g)?.length);
    },
  );

  it("sets the step's density, at least a readable size on a narrow screen", () => {
    const style = pageProperties(
      {
        body_size: 10,
        line_height: 1.42,
        section_gap: 10,
        item_gap: 5,
        margin: 15,
      },
      "#1f4e79",
    );
    expect(style["--body-size"]).toBe("max(calc(10 * var(--tpl-pt)), 9px)");
    expect(style["--margin"]).toBe("calc(15 * var(--tpl-mm))");
    expect(style["--accent"]).toBe("#1f4e79");
  });
});
