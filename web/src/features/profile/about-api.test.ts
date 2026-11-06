import { describe, expect, it } from "vitest";

import { demote, joinParts, keyOf, splitParts } from "./about-api";

// The twin of api/app/profile/about_text.py split and join (tests/profile/test_about_text.py).
describe("the parts of the About you document", () => {
  it("starts a part at each level two heading outside a code fence", () => {
    const { preamble, parts } = splitParts(
      "Intro\n## Skills\n```\n## not a part\n```\n## Travel notes\nOften\n",
    );
    expect(preamble).toBe("Intro");
    expect(parts).toEqual([
      { heading: "Skills", body: "```\n## not a part\n```" },
      { heading: "Travel notes", body: "Often" },
    ]);
  });

  it("joins the parts back as the api writes them", () => {
    const text =
      "## Experience\n\n### Data engineer, Example Robotics\n\n## Answers\n";
    const { preamble, parts } = splitParts(text);
    expect(joinParts(preamble, parts)).toBe(text);
    expect(joinParts("", [{ heading: "Projects", body: "" }])).toBe(
      "## Projects\n",
    );
  });

  it("writes a level one or two heading of a body one level down, fences aside", () => {
    expect(demote("# Big\n## Tools\n### Kept\n```\n## code\n```")).toBe(
      "### Big\n### Tools\n### Kept\n```\n## code\n```",
    );
  });

  it("reads a heading in any case and spacing as one key", () => {
    expect(keyOf("  Work   Experience ")).toBe("work experience");
  });
});
