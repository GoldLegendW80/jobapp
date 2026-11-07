import { describe, expect, it } from "vitest";

import { aboutOf } from "@/test/about";

import {
  blockTitle,
  factBaseOf,
  factBytes,
  formatBytes,
  orderBlocks,
  sizeText,
  utf8Bytes,
} from "../helpers/fact-base";
import { splitParts } from "../api/about-api";

describe("the size of the fact base", () => {
  it("counts UTF-8 bytes of the cleaned titles and bodies, as the api", () => {
    expect(utf8Bytes("é")).toBe(2);
    expect(utf8Bytes("😀")).toBe(4);
    expect(factBytes([{ title: " a ", body: "\r\nbé\r\n" }])).toBe(1 + 3);
    expect(factBytes([])).toBe(0);
  });

  it("formats sizes, exact near and over the cap", () => {
    expect(formatBytes(0)).toBe("0 bytes");
    expect(formatBytes(999)).toBe("999 bytes");
    expect(formatBytes(12_345)).toBe("12.3 KB");
    expect(sizeText(12_345, 200_000)).toBe("12.3 KB of 200 KB");
    expect(sizeText(198_000, 200_000)).toBe("198,000 of 200,000 bytes");
    expect(sizeText(200_003, 200_000)).toBe("200,003 of 200,000 bytes");
  });

  it("measures the fact parts as typed and the stored blocks no part holds", () => {
    const text =
      "## Skills\n\n- a: b\n\n## Notes\n\nabc\n\n## Answers\n\nYes.\n";
    const about = { ...aboutOf(text), missing_blocks: ["Kept"] };
    const base = factBaseOf(splitParts(text).parts, about, [
      { title: "Kept", body: "é" },
      { title: "Notes", body: "old text" },
    ]);
    expect(base).toEqual([
      { title: "Notes", body: "abc" },
      { title: "Kept", body: "é" },
    ]);
  });
});

describe("the order of the fact base", () => {
  const blocks = [
    { title: "A", body: "1" },
    { title: "Elsewhere", body: "2" },
    { title: "B", body: "3" },
    { title: "C", body: "4" },
  ];

  it("puts the blocks the titles name in their order, the others in their places", () => {
    expect(
      orderBlocks(blocks, ["c", "A", "Draft", "B"])?.map((b) => b.title),
    ).toEqual(["C", "Elsewhere", "A", "B"]);
  });

  it("answers null when nothing moves", () => {
    expect(orderBlocks(blocks, ["A", "Draft", "B", "C"])).toBeNull();
    expect(orderBlocks([], ["A"])).toBeNull();
  });

  it("reads a part's block title as the api does", () => {
    expect(blockTitle("Projects")).toBe("Projects");
    expect(blockTitle("Notes: Skills")).toBe("Skills");
    expect(blockTitle("Notes: Projects")).toBe("Notes: Projects");
  });
});
