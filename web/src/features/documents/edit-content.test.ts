import { describe, expect, it } from "vitest";

import { fakeCv, fakeDocumentOf } from "@/test/documents";

import {
  characters,
  differences,
  localPath,
  moved,
  notesByField,
  pathKey,
  setAt,
  softLimit,
  span,
  toPayload,
  withDefaults,
} from "./edit-content";

const LIMITS = { headline: 90, summary: 330, main_bullet: 190, bullet: 100 };

describe("the draft of the Edit view", () => {
  it("fills the fields the form shows, a base CV's fixed block only for a base CV", () => {
    const base = fakeDocumentOf(fakeCv(), { draft: { headline: "Kept" } });
    expect(withDefaults(base)).toMatchObject({
      schema: 1,
      headline: "Kept",
      summary: "",
      experience: [],
      fixed: { employers: [], schools: [], languages: [] },
    });
    const posting = { ...base, scope: "posting" as const };
    expect(withDefaults(posting)).not.toHaveProperty("fixed");
    const letter = { ...posting, kind: "letter" as const, draft: {} };
    expect(withDefaults(letter)).toMatchObject({ paragraphs: [], closing: "" });
    expect(withDefaults(letter)).not.toHaveProperty("experience");
  });

  it("sends no entry that was not typed yet, and keeps every typed one as typed", () => {
    const draft = {
      headline: "",
      highlights: ["", " Led the move ", "  "],
      experience: [{ employer_key: "example-corp", bullets: [] }],
      skills: [
        { label: "", items: [""] },
        { label: "", items: ["Go"] },
      ],
      fixed: { employers: [{ key: "", organisation: "", start: null }] },
    };
    expect(toPayload(draft)).toEqual({
      headline: "",
      highlights: [" Led the move "],
      experience: [{ employer_key: "example-corp", bullets: [] }],
      skills: [{ label: "", items: ["Go"] }],
      fixed: { employers: [] },
    });
  });

  it("finds the field the api names in the sent draft, past the entries left out", () => {
    const draft = {
      highlights: ["", "One", "", "Two"],
      skills: [
        { label: "", items: [] },
        { label: "Tools", items: ["", "Go"] },
      ],
    };
    expect(pathKey(localPath(draft, "highlights.1"))).toBe("highlights.3");
    expect(pathKey(localPath(draft, "skills.0.items.0"))).toBe(
      "skills.1.items.1",
    );
    expect(pathKey(localPath(draft, "summary"))).toBe("summary");
    expect(pathKey(localPath(draft, ""))).toBe("");
  });

  it("puts each note under its field with the words it is about", () => {
    const saved = fakeDocumentOf(fakeCv(), {
      draft: {
        summary: "Led 40 engineers.",
        skills: [{ label: "Tools", items: ["Go", "Rust"] }],
      },
      notes: [
        {
          field: "summary",
          start: 4,
          end: 6,
          code: "number_not_in_sources",
          level: "claim",
        },
        {
          field: "skills.0.items.1",
          start: 0,
          end: 0,
          code: "banned_phrase",
          level: "claim",
        },
        { field: "", start: 0, end: 0, code: "a_new_code", level: "note" },
        { field: 3 },
      ],
    });
    const notes = notesByField(saved, saved.draft);
    expect(notes.get("summary")).toEqual([
      { sentence: "A number your profile does not state", excerpt: "40" },
    ]);
    // A group's terms are one field.
    expect(notes.get("skills.0.items")).toEqual([
      { sentence: "A phrase you banned", excerpt: "" },
    ]);
    expect(notes.get("")).toEqual([
      { sentence: "A note of the fact check", excerpt: "" },
    ]);
    expect(notes.size).toBe(3);
  });

  it("counts against the template's counter of each field, the first employer's longer", () => {
    expect(softLimit(LIMITS, ["summary"])).toBe(330);
    expect(softLimit(LIMITS, ["experience", 0, "bullets", 2])).toBe(190);
    expect(softLimit(LIMITS, ["experience", 1, "bullets", 0])).toBe(100);
    expect(softLimit(LIMITS, ["highlights", 0])).toBeNull();
    expect(softLimit(LIMITS, ["notes"])).toBeNull();
    expect(softLimit(undefined, ["summary"])).toBeNull();
    expect(characters("  two   words\t ")).toBe(9);
    expect(characters("Zoë 🙂")).toBe(5);
  });

  it("moves an entry, and leaves the list as it is for a place outside it", () => {
    expect(moved(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    expect(moved(["a", "b"], 0, 2)).toEqual(["a", "b"]);
    expect(moved(["a", "b"], -1, 0)).toEqual(["a", "b"]);
    expect(setAt({ a: [{ b: "x" }] }, ["a", 0, "b"], "y")).toEqual({
      a: [{ b: "y" }],
    });
  });

  it("lists what differs between two drafts, field by field", () => {
    const mine = { schema: 1, summary: "Mine.", highlights: ["Same", "Extra"] };
    const theirs = { schema: 1, summary: "Theirs.", highlights: ["Same", ""] };
    expect(differences(mine, theirs)).toEqual([
      { label: "Summary", mine: "Mine.", theirs: "Theirs." },
      { label: "Highlights, item 2", mine: "Extra", theirs: "" },
    ]);
    expect(differences(mine, { ...mine, summary: " Mine. " })).toEqual([]);
  });

  it("writes a period in words, without a dash", () => {
    expect(span("2021-03", null)).toBe("March 2021 to today");
    expect(span("2018", "2021-02")).toBe("2018 to February 2021");
    expect(span(null, "2021")).toBe("");
  });
});
