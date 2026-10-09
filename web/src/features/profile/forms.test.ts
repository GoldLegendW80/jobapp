import { describe, expect, it } from "vitest";

import { haraldLike } from "@/test/profile";

import { EMPTY } from "./api";
import {
  answersSchema,
  experienceSchema,
  fromAnswersForm,
  fromExperienceForm,
  fromIdentityForm,
  identitySchema,
  isMonth,
  NEW_EMPLOYER,
  NEW_SCHOOL,
  slugify,
  toAnswersForm,
  toExperienceForm,
  toIdentityForm,
  type ExperienceForm,
  type IdentityForm,
} from "./forms";

/** path -> message of every issue, for readable assertions */
function issues(result: {
  success: boolean;
  error?: { issues: { path: PropertyKey[]; message: string }[] };
}) {
  return Object.fromEntries(
    (result.error?.issues ?? []).map((i) => [i.path.join("."), i.message]),
  );
}

const identity = (patch: Partial<IdentityForm> = {}): IdentityForm => ({
  ...toIdentityForm(haraldLike().identity),
  ...patch,
});

describe("identity schema", () => {
  it("accepts the imported profile and an empty one", () => {
    expect(identitySchema.safeParse(identity()).success).toBe(true);
    expect(
      identitySchema.safeParse(toIdentityForm(EMPTY.identity)).success,
    ).toBe(true);
  });

  it.each([
    ["email", "not-an-email", "Enter a valid email address."],
    ["email", "a@b", "Enter a valid email address."],
    [
      "linkedin_url",
      "linkedin.com/in/x",
      "Enter a full address starting with http:// or https://.",
    ],
    [
      "website",
      "ftp://example.org",
      "Enter a full address starting with http:// or https://.",
    ],
    [
      "website",
      "https://localhost",
      "Enter a full address starting with http:// or https://.",
    ],
    [
      "phone",
      "call me",
      "Enter a phone number: digits, spaces, dots and brackets, optionally starting with +.",
    ],
    [
      "phone",
      "+33",
      "Enter a phone number: digits, spaces, dots and brackets, optionally starting with +.",
    ],
    ["full_name", "x".repeat(201), "Use at most 200 characters."],
    ["headline", "a\u0007b", "Remove the control characters."],
  ] as const)("refuses %s = %j", (name, value, message) => {
    const result = identitySchema.safeParse(identity({ [name]: value }));
    expect(issues(result)[name]).toBe(message);
  });

  it("counts length after trimming, and accepts unicode names", () => {
    const result = identitySchema.safeParse(
      identity({ full_name: `  ${"é".repeat(200)}  `, city: "Zürich 🏔" }),
    );
    expect(result.success).toBe(true);
  });

  it("refuses a duplicate language whatever its case, and an empty one", () => {
    const result = identitySchema.safeParse(
      identity({
        languages: [
          { name: "French", level: "" },
          { name: " french ", level: "C2" },
          { name: "  ", level: "" },
        ],
      }),
    );
    expect(issues(result)).toEqual({
      "languages.1.name": "This language is already listed.",
      "languages.2.name": "Enter the language.",
    });
  });

  it("refuses more than 20 languages and more than 10 links", () => {
    const result = identitySchema.safeParse(
      identity({
        languages: Array.from({ length: 21 }, (_, i) => ({
          name: `L${i}`,
          level: "",
        })),
        links: Array.from({ length: 11 }, (_, i) => ({
          label: `l${i}`,
          url: "https://example.org",
        })),
      }),
    );
    expect(issues(result)).toEqual({
      languages: "Keep at most 20 languages.",
      links: "Keep at most 10 links.",
    });
  });

  it("requires a link's label and a valid address", () => {
    const result = identitySchema.safeParse(
      identity({ links: [{ label: "", url: "nope" }] }),
    );
    expect(issues(result)).toEqual({
      "links.0.label": "Enter a label.",
      "links.0.url": "Enter a full address starting with http:// or https://.",
    });
  });

  it("round trips, trimming and storing empty fields as null", () => {
    const data = haraldLike().identity;
    expect(fromIdentityForm(toIdentityForm(data))).toEqual(data);
    const out = fromIdentityForm(
      identity({
        phone: "  ",
        headline: "  Engineer ",
        languages: [{ name: " Basque ", level: " " }],
      }),
    );
    expect(out.phone).toBeNull();
    expect(out.headline).toBe("Engineer");
    expect(out.languages).toEqual([{ name: "Basque", level: null }]);
  });
});

const experience = (patch: Partial<ExperienceForm> = {}): ExperienceForm => ({
  ...toExperienceForm(haraldLike().experience),
  ...patch,
});
const employer = (patch: Partial<ExperienceForm["employers"][number]>) => ({
  ...NEW_EMPLOYER,
  key: "acme",
  organisation: "Acme",
  title: "Engineer",
  start: "2024-01",
  present: true,
  ...patch,
});

describe("experience schema", () => {
  it("accepts the imported profile", () => {
    expect(experienceSchema.safeParse(experience()).success).toBe(true);
  });

  it.each([
    ["2024-13", false],
    ["2024-00", false],
    ["24-01", false],
    ["2024-1", false],
    ["1899-12", false],
    ["2101-01", false],
    ["1900-01", true],
    ["2100-12", true],
    ["2024-01 ", false],
  ])("month %j valid: %s", (value, valid) => {
    expect(isMonth(value)).toBe(valid);
  });

  it("names the month format", () => {
    const result = experienceSchema.safeParse(
      experience({ employers: [employer({ start: "2024/05" })] }),
    );
    expect(issues(result)["employers.0.start"]).toBe(
      "Write the month as YYYY-MM, between 1900 and 2100.",
    );
  });

  it("requires organisation, title, key and start", () => {
    const result = experienceSchema.safeParse(
      experience({ employers: [{ ...NEW_EMPLOYER, present: true }] }),
    );
    expect(issues(result)).toEqual({
      "employers.0.key": "Enter a key.",
      "employers.0.organisation": "Enter the organisation.",
      "employers.0.title": "Enter the job title.",
      "employers.0.start": "Enter the start month.",
    });
  });

  it("asks for an end month unless present is ticked", () => {
    const result = experienceSchema.safeParse(
      experience({ employers: [employer({ present: false, end: "" })] }),
    );
    expect(issues(result)["employers.0.end"]).toBe(
      "Enter the end month, or tick Present.",
    );
  });

  it("refuses an end before the start, for employers and schools", () => {
    const result = experienceSchema.safeParse(
      experience({
        employers: [
          employer({ present: false, start: "2024-05", end: "2024-04" }),
        ],
        education: [
          {
            ...NEW_SCHOOL,
            school: "S",
            degree: "D",
            start: "2020-09",
            end: "2019-06",
          },
        ],
      }),
    );
    expect(issues(result)).toEqual({
      "employers.0.end": "The end month is before the start month.",
      "education.0.end": "The end month is before the start month.",
    });
  });

  it("accepts the same month as start and end, and ignores end when present", () => {
    const same = employer({ present: false, start: "2024-05", end: "2024-05" });
    const ignored = employer({
      key: "b",
      present: true,
      start: "2024-05",
      end: "2000-01",
    });
    expect(
      experienceSchema.safeParse(experience({ employers: [same, ignored] }))
        .success,
    ).toBe(true);
  });

  it("refuses duplicate employer keys on the later one", () => {
    const result = experienceSchema.safeParse(
      experience({
        employers: [employer({}), employer({ organisation: "Acme 2" })],
      }),
    );
    expect(issues(result)).toEqual({
      "employers.1.key": "Another employer already uses this key.",
    });
  });

  it.each(["Acme", "acme_co", "-acme", "acme-", "acme--co", "a".repeat(41)])(
    "refuses the key %j",
    (key) => {
      const result = experienceSchema.safeParse(
        experience({ employers: [employer({ key })] }),
      );
      expect(issues(result)["employers.0.key"]).toBeDefined();
    },
  );

  it("caps the lists at 30 employers and 20 schools", () => {
    const result = experienceSchema.safeParse(
      experience({
        employers: Array.from({ length: 31 }, (_, i) =>
          employer({ key: `e${i}` }),
        ),
        education: Array.from({ length: 21 }, () => ({
          ...NEW_SCHOOL,
          school: "S",
          degree: "D",
        })),
      }),
    );
    expect(issues(result)).toMatchObject({
      employers: "Keep at most 30 employers.",
      education: "Keep at most 20 schools.",
    });
  });

  it("round trips; present means end null; empty contract is null", () => {
    const data = haraldLike().experience;
    expect(fromExperienceForm(toExperienceForm(data))).toEqual(data);
    const form = toExperienceForm(data);
    expect(form.employers[0]).toMatchObject({
      present: true,
      end: "",
      autoKey: false,
    });
    expect(form.employers[2]).toMatchObject({ present: false, end: "2025-08" });
    const out = fromExperienceForm(
      experience({
        employers: [employer({ present: true, end: "2025-01", contract: "" })],
      }),
    );
    expect(out.employers[0]).toMatchObject({ end: null, contract: null });
  });
});

describe("slugify", () => {
  it.each([
    ["Betclic Group", "betclic-group"],
    ["  Zoë Ångström & Co. ", "zoe-angstrom-co"],
    ["ÉLSAN (Bordeaux)", "elsan-bordeaux"],
    ["東京", ""],
    ["", ""],
    ["a".repeat(39) + " bcd", "a".repeat(39)],
  ])("%j -> %j", (input, slug) => {
    expect(slugify(input)).toBe(slug);
  });
});

describe("answers schema", () => {
  const answers = () => toAnswersForm(haraldLike().answers);

  it("accepts the imported profile and an empty one", () => {
    expect(answersSchema.safeParse(answers()).success).toBe(true);
    expect(answersSchema.safeParse(toAnswersForm(EMPTY.answers)).success).toBe(
      true,
    );
  });

  it("refuses a duplicate country, an empty answer and an over long motivation", () => {
    const result = answersSchema.safeParse({
      ...answers(),
      work_authorization: [
        { country: "France", answer: "Yes.", needs_sponsorship: "no" },
        { country: "FRANCE", answer: "  ", needs_sponsorship: "" },
      ],
      motivation: "m".repeat(10_001),
      earliest_start: "x".repeat(201),
      leave_blank: [{ value: "" }],
    });
    expect(issues(result)).toEqual({
      "work_authorization.1.country": "This country is already listed.",
      "work_authorization.1.answer": "Enter the answer.",
      motivation: "Use at most 10,000 characters.",
      earliest_start: "Use at most 200 characters.",
      "leave_blank.0.value": "Enter a question.",
    });
  });

  it("keeps newlines and tabs in multi line text but refuses other control characters", () => {
    expect(
      answersSchema.safeParse({ ...answers(), motivation: "a\n\tb" }).success,
    ).toBe(true);
    const result = answersSchema.safeParse({
      ...answers(),
      motivation: "a\u0000b",
    });
    expect(issues(result).motivation).toBe("Remove the control characters.");
  });

  it("round trips; sponsorship yes, no and not specified", () => {
    const data = haraldLike().answers;
    expect(fromAnswersForm(toAnswersForm(data))).toEqual(data);
    const out = fromAnswersForm({
      ...answers(),
      work_authorization: [
        { country: " Spain ", answer: "a\r\nb", needs_sponsorship: "" },
      ],
      motivation: "  ",
    });
    expect(out.work_authorization).toEqual([
      { country: "Spain", answer: "a\nb", needs_sponsorship: null },
    ]);
    expect(out.motivation).toBeNull();
  });
});
