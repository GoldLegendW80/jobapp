import { describe, expect, it } from "vitest";

import { haraldLike, profileBody, type FakeProfile } from "@/test/profile";

import { IMPORT_MAX_BYTES, profileSchema, toProfile } from "./api";
import {
  checkImportFile,
  describeSection,
  exportFileName,
  formatBytes,
  OLDER_FILE_PREFERENCES,
} from "./import-file";

function profileOf(sections: FakeProfile["sections"], photo = false) {
  return toProfile(
    profileSchema.parse(
      profileBody({
        revision: 6,
        sections,
        photoEtag: photo ? '"etag"' : null,
        saves: [],
        uploads: [],
        deletes: 0,
        revisions: [],
        imports: [],
        exports: 0,
      }),
    ),
  );
}

const exported = (overrides: Record<string, unknown> = {}) => ({
  format: "jobapp-profile",
  version: 1,
  exported_at: "2026-10-09T08:00:00+00:00",
  sections: haraldLike(),
  ...overrides,
});

const check = (value: unknown, profile = profileOf(haraldLike())) => {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return checkImportFile(text, new TextEncoder().encode(text).length, profile);
};

const errorOf = (value: unknown) => {
  const result = check(value);
  if (result.ok) throw new Error("expected a refusal");
  return result.error;
};

describe("checkImportFile refuses", () => {
  it("an empty file", () => {
    expect(checkImportFile("", 0, profileOf({}))).toEqual({
      ok: false,
      error: "The file is empty.",
    });
  });

  it("a file over 2 MB, before reading it as JSON", () => {
    expect(
      checkImportFile("not json", IMPORT_MAX_BYTES + 1, profileOf({})),
    ).toEqual({
      ok: false,
      error: "The file is 2.0 MB; an import file is at most 2 MB.",
    });
    // Exactly the limit is fine as far as size goes.
    const atLimit = checkImportFile("x", IMPORT_MAX_BYTES, profileOf({}));
    expect(atLimit).toEqual({
      ok: false,
      error: "The file is not valid JSON.",
    });
  });

  it.each([
    ["{", "The file is not valid JSON."],
    ["not json at all", "The file is not valid JSON."],
    ["[]", "The file must hold one JSON object."],
    ["null", "The file must hold one JSON object."],
    ['"text"', "The file must hold one JSON object."],
  ])("bad JSON %s", (text, error) => {
    expect(errorOf(text)).toBe(error);
  });

  it.each([
    [{ format: "something-else" }],
    [{ format: undefined }],
    [{ format: "JOBAPP-PROFILE" }],
  ])("a wrong format %j", (overrides) => {
    expect(errorOf(exported(overrides))).toBe(
      'Unknown file format: expected "format": "jobapp-profile", a profile export.',
    );
  });

  it.each([[0], ["1"], [1.5], [null], [undefined]])(
    "a wrong version %j",
    (version) => {
      expect(errorOf(exported({ version }))).toBe(
        "Unsupported export version: this app reads version 1.",
      );
    },
  );

  it("a newer version, saying so", () => {
    expect(errorOf(exported({ version: 2 }))).toBe(
      "Unsupported export version 2: this app reads version 1. The file comes from a newer version of the app.",
    );
  });

  it("unknown top level fields", () => {
    expect(errorOf(exported({ owner: "x", extra: 1 }))).toBe(
      "Unknown field in the file: owner, extra.",
    );
  });

  it.each([[undefined], ["yesterday"], [42]])(
    "a missing or bad export date %j",
    (exportedAt) => {
      expect(errorOf(exported({ exported_at: exportedAt }))).toBe(
        "The file has no valid export date (exported_at).",
      );
    },
  );

  it("no sections, missing sections, unknown or non object sections", () => {
    expect(errorOf(exported({ sections: [] }))).toBe(
      "The file has no sections.",
    );
    const lacking: Partial<ReturnType<typeof haraldLike>> = haraldLike();
    delete lacking.facts;
    expect(errorOf(exported({ sections: lacking }))).toBe(
      "The file lacks the section Facts.",
    );
    expect(errorOf(exported({ sections: {} }))).toBe(
      "The file lacks the sections Identity, Experience, Facts, Skills, Search, Answers.",
    );
    expect(
      errorOf(exported({ sections: { ...haraldLike(), hobbies: {} } })),
    ).toBe("Unknown section in the file: hobbies.");
    expect(
      errorOf(exported({ sections: { ...haraldLike(), facts: null } })),
    ).toBe("Facts must be an object in the file.");
    expect(
      errorOf(
        exported({ sections: { ...haraldLike(), facts: [], skills: "x" } }),
      ),
    ).toBe("Facts, Skills must be objects in the file.");
  });

  it.each([
    [{ mime: "image/gif", base64: "AAAA" }, "must be a PNG or JPEG image"],
    [{ mime: "image/png", base64: "" }, "is empty"],
    [{ mime: "image/png", base64: "not base64!" }, "is not valid base64"],
    [{ mime: "image/png", base64: "AAA" }, "is not valid base64"],
    ["photo", "is not readable"],
  ])("a bad photo %#", (photo, error) => {
    expect(errorOf(exported({ photo }))).toContain(error);
  });

  it("a photo over 2 MB once decoded (the file size check comes first otherwise)", () => {
    const text = JSON.stringify(
      exported({ photo: { mime: "image/png", base64: "A".repeat(2_796_204) } }),
    );
    expect(checkImportFile(text, 1000, profileOf(haraldLike()))).toEqual({
      ok: false,
      error: "The photo in the file is larger than 2 MB.",
    });
  });
});

describe("checkImportFile accepts an export", () => {
  it("and drops a base_revision the file carries", () => {
    const result = check(exported({ base_revision: 3 }));
    if (!result.ok) throw new Error(result.error);
    expect(result.document.base_revision).toBeUndefined();
    expect(result.document.format).toBe("jobapp-profile");
  });

  it("summarises sections, unchanged when identical to the profile", () => {
    const result = check(exported());
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.exportedAt).toBe("2026-10-09T08:00:00+00:00");
    expect(result.summary.sections.map((s) => s.changed)).toEqual([
      false,
      false,
      false,
      false,
      false,
      false,
      false,
    ]);
    expect(result.summary.sections.map((s) => s.detail)).toEqual([
      "Harald Example, 3 languages",
      "3 employers, 2 schools",
      "2 blocks",
      "4 categories, 1 gate",
      "0 job websites, 0 ticked",
      "2 countries",
      "Documents: language auto, tone plain",
    ]);
    expect(result.summary.photo).toBe(false);
    // Search needs a job website ticked in Find jobs, which the file does not hold.
    expect(result.summary.completeness.overall).toBe(97);
  });

  it("reads an export with a ticked job website and no instructions as unchanged", () => {
    // The api stores and exports no instructions as null; the page reads them as "". The file
    // is read the same way, so the export imported back changes nothing.
    const sections = haraldLike();
    sections.search = {
      ...sections.search,
      interactive_sites: [
        {
          platform: "custom",
          url: "https://jobs.example.org/",
          enabled: true,
          mode: "llm_browser",
        },
      ],
      interactive_instructions: null,
    };
    const result = check(exported({ sections }), profileOf(sections));
    if (!result.ok) throw new Error(result.error);
    const search = result.summary.sections.find((s) => s.name === "search");
    expect(search).toMatchObject({
      detail: "1 job website, 1 ticked",
      changed: false,
    });
    expect(result.summary.completeness.overall).toBe(100);
  });

  it("says which sections an import replaces on an empty profile", () => {
    const result = check(exported(), profileOf({}));
    if (!result.ok) throw new Error(result.error);
    // Preferences at their defaults are what an empty profile reads: no change there.
    expect(
      result.summary.sections
        .filter((s) => s.name !== "preferences")
        .every((s) => s.changed),
    ).toBe(true);
  });

  it("reads a file without preferences (written before O4) as their defaults", () => {
    const sections: Record<string, unknown> = { ...haraldLike() };
    delete sections.preferences;
    const result = check(exported({ sections }));
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.sections.at(-1)).toMatchObject({
      name: "preferences",
      changed: false,
      detail: OLDER_FILE_PREFERENCES,
    });
  });

  it("says an older file resets a tone the person had set", () => {
    const current = haraldLike();
    const profile = profileOf({
      ...current,
      preferences: { ...current.preferences, documents_tone: "warm" },
    });
    const sections: Record<string, unknown> = { ...current };
    delete sections.preferences;
    const result = check(exported({ sections }), profile);
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.sections.at(-1)).toMatchObject({
      name: "preferences",
      changed: true,
      detail: OLDER_FILE_PREFERENCES,
    });
  });

  it("never counts the onboarding dates as a change: an import keeps them", () => {
    const sections = haraldLike();
    const profile = profileOf({
      ...sections,
      preferences: {
        ...sections.preferences,
        onboarding: {
          completed_at: "2026-10-20T08:00:00+00:00",
          dismissed_at: null,
          steps_passed: ["looking"],
        },
      },
    });
    const result = check(exported(), profile);
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.sections.at(-1)?.changed).toBe(false);
  });

  it("compares sections over their empty form (omitted fields are defaults)", () => {
    const sections = haraldLike();
    const identity: Partial<typeof sections.identity> = {
      ...sections.identity,
    };
    delete identity.links;
    const profile = profileOf({
      ...sections,
      identity: { ...identity, links: [] },
    });
    const result = check(
      exported({ sections: { ...sections, identity } }),
      profile,
    );
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.sections[0]?.changed).toBe(false);
  });

  it("reports a photo and the completeness after", () => {
    const empty = {
      identity: {},
      experience: {},
      facts: {},
      skills: {},
      search: {},
      answers: {},
    };
    const result = check(
      exported({
        sections: empty,
        photo: { mime: "image/png", base64: "iVBORw0KGgo=" },
      }),
    );
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.photo).toBe(true);
    expect(result.summary.completeness.overall).toBe(0);
    expect(result.summary.sections.map((s) => s.detail)).toEqual([
      "No name, 0 languages",
      "0 employers, 0 schools",
      "0 blocks",
      "0 categories, 0 gates",
      "0 job websites, 0 ticked",
      "0 countries",
      OLDER_FILE_PREFERENCES, // no preferences in this file
    ]);
  });

  it("accepts a null photo and unicode everywhere", () => {
    const sections = haraldLike();
    sections.identity.full_name = "Zoë Ñúñez 🙂";
    const result = check(exported({ sections, photo: null }));
    if (!result.ok) throw new Error(result.error);
    expect(result.summary.sections[0]?.detail).toBe(
      "Zoë Ñúñez 🙂, 3 languages",
    );
    expect(result.summary.photo).toBe(false);
  });
});

describe("helpers", () => {
  it("describes one of everything in the singular", () => {
    expect(
      describeSection("experience", { employers: [{}], education: [{}] }),
    ).toBe("1 employer, 1 school");
    expect(describeSection("answers", { work_authorization: [{}] })).toBe(
      "1 country",
    );
    expect(describeSection("skills", { have: [{}] })).toBe(
      "1 category, 0 gates",
    );
  });

  it("formats sizes", () => {
    expect(formatBytes(12)).toBe("12 bytes");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("names the download after the export date, in UTC", () => {
    expect(exportFileName("2026-10-09T23:30:00-02:00")).toBe(
      "profile-2026-10-10.json",
    );
    expect(exportFileName(undefined, new Date("2026-01-02T00:00:00Z"))).toBe(
      "profile-2026-01-02.json",
    );
    expect(exportFileName("garbage", new Date("2026-01-02T00:00:00Z"))).toBe(
      "profile-2026-01-02.json",
    );
  });
});
