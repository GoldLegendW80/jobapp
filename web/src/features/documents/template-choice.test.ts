import { describe, expect, it } from "vitest";

import { fakeCv, fakeDocumentOf } from "@/test/documents";
import { fakeTemplate, TEMPLATES } from "@/test/templates";

import type { Template } from "./documents-api";
import {
  canShorten,
  documentPath,
  galleryLanguage,
  galleryPath,
  inheritanceSentence,
  isRemoved,
  matches,
  nameOf,
  NO_FILTERS,
  ordered,
  REMOVED_SENTENCE,
  type Filters,
} from "./template-choice";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const base = fakeDocumentOf(fakeCv({ id: "b", name: "Main CV" }));
const postingCv = {
  ...base,
  id: "p",
  scope: "posting" as const,
  posting_id: "job-1",
  base_document_id: "b",
};
const postingLetter = { ...postingCv, id: "l", kind: "letter" as const };

const slugs = (templates: Template[]) => templates.map((one) => one.slug);
const shown = (filters: Partial<Filters>) =>
  slugs(TEMPLATES.filter((one) => matches(one, { ...NO_FILTERS, ...filters })));

describe("the order of the templates", () => {
  it("puts the person's own first, in the api's order, then the bundled ones", () => {
    const second = fakeTemplate("upload-ffff0000", { user: true });
    expect(slugs(ordered([...TEMPLATES, second]))).toEqual([
      "upload-abcd1234",
      "upload-ffff0000",
      "banner",
      "classic",
      "compact",
      "sidebar",
      "serif",
    ]);
    expect(ordered([])).toEqual([]);
  });
});

describe("the language of the names", () => {
  it.each([
    [undefined, "fr-FR", "fr"],
    [undefined, "FR", "fr"],
    [undefined, "en-GB", "en"],
    [undefined, "de-DE", "en"],
    [undefined, "", "en"],
    [undefined, "fr_CA", "fr"],
    [undefined, "frr-DE", "en"],
    [undefined, "fur", "en"],
    ["en", "fr-FR", "en"],
    ["fr", "en-US", "fr"],
  ] as const)("document %s, browser %s: %s", (document, browser, expected) => {
    expect(galleryLanguage(document, browser)).toBe(expected);
  });

  it("names a template in the language, else in English, else from its slug", () => {
    const classic = fakeTemplate("classic");
    expect(nameOf(classic, "fr")).toBe("Classic FR");
    expect(nameOf({ ...classic, name: { en: "Classic" } }, "fr")).toBe(
      "Classic",
    );
    expect(nameOf({ ...classic, slug: "split-right", name: {} }, "en")).toBe(
      "Split right",
    );
  });
});

describe("the filters", () => {
  it("shows everything with no filter", () => {
    expect(shown({})).toEqual(slugs(TEMPLATES));
  });

  it.each([
    [{ photo: "with" }, ["banner", "classic", "sidebar"]],
    [{ photo: "without" }, ["compact", "serif", "upload-abcd1234"]],
    [{ columns: "one" }, ["banner", "classic", "compact", "serif"]],
    [{ columns: "two" }, ["sidebar"]],
    [{ ats: true }, ["classic", "compact", "serif"]],
    [{ photo: "without", ats: true }, ["compact", "serif"]],
    [{ photo: "with", columns: "two" }, ["sidebar"]],
    [{ photo: "without", columns: "two" }, []],
    [{ photo: "with", columns: "one", ats: true }, ["classic"]],
  ] as const)("%o shows %o", (filters, expected) => {
    expect(shown(filters)).toEqual(expected);
  });

  it.each([
    // A template matches only what it declares: tags first, then its manifest's photo.
    [{ tags: ["photo"], photo: "none" }, "with", true],
    [{ tags: ["no photo"], photo: "optional" }, "without", true],
    [{ tags: [], photo: "required" }, "with", true],
    [{ tags: [], photo: "optional" }, "with", true],
    [{ tags: [], photo: "none" }, "without", true],
    [{ tags: [], photo: "none" }, "with", false],
    [{ tags: [], photo: "something else" }, "with", false],
    [{ tags: [], photo: "something else" }, "without", true],
    [{ tags: ["Photo"], photo: "none" }, "with", false],
    [{ tags: ["photos"], photo: "none" }, "with", false],
  ] as const)("%o under photo %s: %s", (shape, photo, expected) => {
    const template = fakeTemplate("x", {
      tags: [...shape.tags],
      photo: shape.photo,
    });
    expect(matches(template, { ...NO_FILTERS, photo })).toBe(expected);
  });

  it.each([
    [["one column"], "one", true],
    [["two columns"], "two", true],
    [["two columns"], "one", false],
    [["one column"], "two", false],
    [[], "one", false],
    [[], "two", false],
    [["one columns"], "one", false],
    [["two column"], "two", false],
    [["One column"], "one", false],
    [["ats friendly"], "one", false],
  ] as const)("tags %o under columns %s: %s", (tags, columns, expected) => {
    expect(
      matches(fakeTemplate("x", { tags: [...tags] }), {
        ...NO_FILTERS,
        columns,
      }),
    ).toBe(expected);
  });

  it.each([
    [["ats friendly"], true],
    [["ats"], false],
    [["ats-friendly"], false],
    [["ATS friendly"], false],
    [["not ats friendly"], false],
    [[], false],
  ] as const)("tags %o under ats friendly only: %s", (tags, expected) => {
    expect(
      matches(fakeTemplate("x", { tags: [...tags] }), {
        ...NO_FILTERS,
        ats: true,
      }),
    ).toBe(expected);
  });
});

describe("what a switch says", () => {
  it("names what changes for a base CV, a posting's CV and its letter, without dashes", () => {
    expect(inheritanceSentence(base)).toBe(
      "The CVs you write from this one afterwards take its template. The CVs already written keep theirs.",
    );
    expect(inheritanceSentence(postingCv)).toBe(
      "Only this CV changes. The base CV it was written from keeps its template.",
    );
    expect(inheritanceSentence(postingLetter)).toBe(
      "Only this letter changes. The CV of the posting keeps its template.",
    );
    for (const text of [
      inheritanceSentence(base),
      inheritanceSentence(postingCv),
      inheritanceSentence(postingLetter),
      REMOVED_SENTENCE,
    ])
      expect(text).not.toMatch(SEPARATOR);
  });

  it("offers Make it fit on a posting's CV only", () => {
    expect([
      canShorten(base),
      canShorten(postingCv),
      canShorten(postingLetter),
    ]).toEqual([false, true, false]);
  });

  it("knows a template that is no longer served, once the list is read", () => {
    expect(isRemoved("classic", TEMPLATES)).toBe(false);
    expect(isRemoved("upload-abcd1234", TEMPLATES)).toBe(false);
    expect(isRemoved("upload-gone0000", TEMPLATES)).toBe(true);
    expect(isRemoved("retired", TEMPLATES)).toBe(true);
    expect(isRemoved("retired", undefined)).toBe(false);
    expect(isRemoved("classic", [])).toBe(true);
  });

  it("goes back to the page of the document, and opens the gallery for it", () => {
    expect(documentPath(base)).toBe("/profile/cv/b");
    expect(documentPath(postingCv)).toBe("/jobs/job-1/documents/cv");
    expect(documentPath(postingLetter)).toBe("/jobs/job-1/documents/letter");
    expect(galleryPath("a b/c")).toBe(
      "/documents/templates?document=a%20b%2Fc",
    );
  });
});
